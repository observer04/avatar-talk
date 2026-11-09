"""Synthetic caller: a scripted "user" that talks to the deployed agent over LiveKit.

Used for (1) latency benchmarks of model stacks and avatar backends and (2) the
production synthetic monitor. It creates a room, dispatches the agent with an
inline persona, joins as a user, speaks Cartesia-synthesised questions in real
time, and measures:

  * reply latency: end of user speech -> first audible agent/avatar audio
  * barge-in: user starts talking over the agent -> agent audio goes quiet
  * transcripts (LiveKit lk.transcription text streams)
  * avatar video fps (when a video avatar publishes)

Requires an agent worker registered under AGENT_NAME (e.g. `uv run python -m agent.main dev`).

  uv run python services/agent/scripts/synthetic_call.py --llm openai/gpt-4.1-mini --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import aiohttp
import numpy as np
from avatar_core import AGENT_NAME, AvatarBackend, AvatarConfig, LLMConfig, Persona, SessionMetadata
from livekit import api, rtc

SR = 48000
FRAME = SR // 100  # 10 ms
CARTESIA_VOICE = "a167e0f3-df7e-4d52-a9c3-f949145efdab"  # a male voice for the "user"; any valid id works
LOUD_RMS = 500


CACHE_DIR = Path.home() / ".cache" / "avatar_talk" / "synth"
DEEPGRAM_VOICE = "aura-2-andromeda-en"


async def synth(text: str) -> np.ndarray:
    """TTS -> int16 mono @ 48 kHz (cached on disk; questions rarely change).

    Cartesia is the preferred "user" voice, but the account can run out of credits; the
    monitor container has no cache, so fall back to Deepgram Aura-2 (same key as STT).
    """
    cache = CACHE_DIR / (hashlib.sha1(f"{CARTESIA_VOICE}:{text}".encode()).hexdigest() + ".pcm")
    if cache.exists():
        return np.frombuffer(cache.read_bytes(), dtype=np.int16)
    try:
        pcm = await _synth_api(text)
    except Exception as e:
        print(f"  (Cartesia TTS unavailable: {e}; using Deepgram for the question)")
        pcm = await _synth_deepgram(text)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pcm.tobytes())
    return pcm


async def _synth_deepgram(text: str) -> np.ndarray:
    params = {"model": DEEPGRAM_VOICE, "encoding": "linear16", "sample_rate": SR}
    headers = {"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as http:
        async with http.post(
            "https://api.deepgram.com/v1/speak", params=params, json={"text": text}, headers=headers
        ) as r:
            r.raise_for_status()
            return np.frombuffer(await r.read(), dtype=np.int16)


async def _synth_api(text: str) -> np.ndarray:
    body = {
        "model_id": "sonic-3",
        "transcript": text,
        "voice": {"mode": "id", "id": CARTESIA_VOICE},
        "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": SR},
        "language": "en",
    }
    headers = {"X-API-Key": os.environ["CARTESIA_API_KEY"], "Cartesia-Version": "2025-04-16"}
    async with aiohttp.ClientSession() as http:
        async with http.post("https://api.cartesia.ai/tts/bytes", json=body, headers=headers) as r:
            if r.status != 200:
                # fall back to the plugin's default voice if the "user" voice id is unavailable
                body["voice"]["id"] = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
                async with http.post("https://api.cartesia.ai/tts/bytes", json=body, headers=headers) as r2:
                    r2.raise_for_status()
                    return np.frombuffer(await r2.read(), dtype=np.int16)
            return np.frombuffer(await r.read(), dtype=np.int16)


class Mic:
    """Publishes a real-time audio track; silence unless an utterance is queued."""

    def __init__(self) -> None:
        # Small queue so 'end of speech' timestamps match when audio actually leaves.
        self.source = rtc.AudioSource(SR, 1, queue_size_ms=30)
        self._pending = np.zeros(0, dtype=np.int16)
        self.idle = asyncio.Event()
        self.idle.set()
        self.started_at = 0.0
        self.ended_at = 0.0

    def say(self, pcm: np.ndarray) -> None:
        self._pending = np.concatenate([self._pending, pcm])
        self.idle.clear()
        self.started_at = time.monotonic()

    async def run(self) -> None:
        silence = np.zeros(FRAME, dtype=np.int16)
        while True:
            if len(self._pending):
                chunk, self._pending = self._pending[:FRAME], self._pending[FRAME:]
                chunk = np.pad(chunk, (0, FRAME - len(chunk)))
                if not len(self._pending):
                    self.ended_at = time.monotonic()
                    self.idle.set()
            else:
                chunk = silence
            frame = rtc.AudioFrame(chunk.tobytes(), SR, 1, FRAME)
            await self.source.capture_frame(frame)  # paces itself in real time


class AgentEar:
    """Tracks when remote (agent/avatar) audio is audible."""

    def __init__(self) -> None:
        self.last_loud = 0.0
        self.loud_events: list[float] = []

    async def listen(self, track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
            pcm = np.frombuffer(ev.frame.data, dtype=np.int16)
            if pcm.size and np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) > LOUD_RMS:
                now = time.monotonic()
                if now - self.last_loud > 0.3:
                    self.loud_events.append(now)
                self.last_loud = now

    def first_loud_after(self, t: float) -> float | None:
        return next((x for x in self.loud_events if x > t), None)

    def speaking(self, window: float = 0.35) -> bool:
        return time.monotonic() - self.last_loud < window


class VideoMeter:
    def __init__(self) -> None:
        self.frames = 0
        self.first_frame_at: float | None = None
        self.size = (0, 0)

    async def watch(self, track: rtc.Track) -> None:
        async for ev in rtc.VideoStream(track):
            self.frames += 1
            self.size = (ev.frame.width, ev.frame.height)
            if self.first_frame_at is None:
                self.first_frame_at = time.monotonic()


async def wait_until(pred, timeout: float, step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


async def wait_agent_done(ear: AgentEar, after: float = 0.0, quiet: float = 3.0, timeout: float = 45) -> None:
    """Agent spoke after `after` and then stayed quiet for `quiet` s (a filler is followed by the answer)."""
    await wait_until(lambda: ear.first_loud_after(after), timeout)
    await wait_until(lambda: time.monotonic() - ear.last_loud > quiet, timeout)


async def main(args) -> dict:
    url = os.environ["LIVEKIT_URL"]
    key, secret = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
    room_name = f"synth-{uuid.uuid4().hex[:8]}"

    provider, _, model = args.llm.partition("/")
    persona = Persona(
        name="Ava",
        llm=LLMConfig(provider=provider, model=model, fallbacks=[]),
        avatar=AvatarConfig(backend=AvatarBackend(args.avatar), simli_face_id=args.simli_face_id,
                            photo_key=args.photo_key),
        tools=["basics"],
    )
    meta = SessionMetadata(persona=persona, tags={"synthetic": "true"})

    print(f"synth: preparing questions ({args.llm}, avatar={args.avatar}, room={room_name})")
    # Sequential: the Cartesia plan limits concurrent requests.
    q_weather = await synth("What's the weather like in London right now?")
    q_story = await synth("Tell me a long story about a dragon who learns to cook.")
    q_barge = await synth("Stop, stop. Actually, what time is it in Tokyo?")

    lk = api.LiveKitAPI(url, key, secret)
    if args.persona_id:
        # Production path: the control-plane API creates the room, dispatches the agent, mints the token.
        headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{args.api_url}/v1/sessions", json={"persona_id": args.persona_id},
                                 headers=headers) as r:
                if r.status != 201:
                    raise SystemExit(f"session create failed: {r.status} {await r.text()}")
                start = await r.json()
        room_name, token, url = start["room"], start["token"], start["livekit_url"]
        pl = start["persona"]["llm"]
        results_extra = {"session_id": start["session_id"], "persona_id": args.persona_id,
                         "llm": f"{pl['provider']}/{pl['model']}", "avatar": start["persona"]["avatar"]["backend"]}
    else:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room_name, metadata=meta.model_dump_json())
        )
        token = (
            api.AccessToken(key, secret).with_identity(f"synthetic-user-{uuid.uuid4().hex[:4]}")
            .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt()
        )
        results_extra = {}

    room = rtc.Room()
    ear, video = AgentEar(), VideoMeter()
    transcripts: list[tuple[float, str, str]] = []
    tasks: list[asyncio.Task] = []

    @room.on("track_subscribed")
    def on_track(track: rtc.Track, pub, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(ear.listen(track)))
        elif track.kind == rtc.TrackKind.KIND_VIDEO:
            tasks.append(asyncio.create_task(video.watch(track)))

    async def on_transcript(reader: rtc.TextStreamReader, identity: str):
        text = await reader.read_all()
        attrs = reader.info.attributes or {}
        if attrs.get("lk.transcription_final", "true") == "true" and text.strip():
            who = "USER" if identity.startswith(("synthetic-user", "user_")) else "AGENT"
            transcripts.append((time.monotonic(), who, text.strip()))
            print(f"  {who}: {text.strip()}")

    room.register_text_stream_handler(
        "lk.transcription", lambda r, ident: tasks.append(asyncio.create_task(on_transcript(r, ident)))
    )

    t_join = time.monotonic()
    await room.connect(url, token)
    mic = Mic()
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", mic.source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )
    tasks.append(asyncio.create_task(mic.run()))
    results: dict = {"llm": args.llm, "avatar": args.avatar, "room": room_name, **results_extra}

    try:
        print("1) greeting")
        await wait_until(lambda: ear.loud_events, 90)
        results["join_to_greeting_s"] = round(ear.loud_events[0] - t_join, 2) if ear.loud_events else None
        await wait_agent_done(ear)

        lat = []
        for label, q in (("weather", q_weather),):
            print(f"2) {label} question (tool call)")
            mic.say(q)
            await mic.idle.wait()
            t_end = mic.ended_at
            await wait_until(lambda: ear.first_loud_after(t_end), 30)
            first = ear.first_loud_after(t_end)
            lat.append(round(first - t_end, 2) if first else None)
            await wait_agent_done(ear, after=t_end)
        results["tool_turn_first_audio_s"] = lat[0]

        print("3) story question, then barge-in after 3 s of agent speech")
        mic.say(q_story)
        await mic.idle.wait()
        t_end = mic.ended_at
        await wait_until(lambda: ear.first_loud_after(t_end), 30)
        first = ear.first_loud_after(t_end)
        results["plain_turn_first_audio_s"] = round(first - t_end, 2) if first else None
        await asyncio.sleep(3.0)
        t_barge = time.monotonic()
        if ear.speaking():
            mic.say(q_barge)
            await wait_until(lambda: not ear.speaking(), 10)
            results["barge_in_stop_s"] = round(time.monotonic() - t_barge, 2)
        else:
            results["barge_in_stop_s"] = "agent already finished (not measured)"
            mic.say(q_barge)
        await mic.idle.wait()
        await wait_agent_done(ear, after=mic.ended_at)
    finally:
        if video.frames and video.first_frame_at:
            dur = time.monotonic() - video.first_frame_at
            results["video"] = {"frames": video.frames, "fps": round(video.frames / dur, 1), "size": video.size,
                                "first_frame_after_join_s": round(video.first_frame_at - t_join, 2)}
        results["transcript"] = [(who, text) for _, who, text in transcripts]
        for t in tasks:
            t.cancel()
        await room.disconnect()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception:
            pass
        await lk.aclose()
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="openai/gpt-4.1-mini")
    ap.add_argument("--avatar", default="none", choices=[b.value for b in AvatarBackend])
    ap.add_argument("--simli-face-id", default=None)
    ap.add_argument("--photo-key", default=None)
    ap.add_argument("--json", default=None, help="write results to this file")
    ap.add_argument("--persona-id", default=None, help="go through the API (POST /v1/sessions) with this persona")
    ap.add_argument("--api-url", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("AVATAR_API_KEY"))
    args = ap.parse_args()
    res = asyncio.run(main(args))
    print("\nRESULTS")
    print(json.dumps({k: v for k, v in res.items() if k != "transcript"}, indent=2))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)

"""GPU smoke test for the deployed FlashHead worker.

Creates a LiveKit room, joins as the agent and as a user, launches the Modal
worker through its /launch endpoint, sends TTS-shaped audio over the exact
``DataStreamAudioOutput`` wire format the agent uses, and measures the avatar's
published video/audio for the duration of the clip.

Example (the shell already exports LIVEKIT_*; .env supplies INTERNAL_API_TOKEN):

    uv run python services/avatar_flashhead/scripts/smoke_test.py \
        --launch-url https://<workspace>--avatar-talk-flashhead-web.modal.run \
        --wav /path/to/speech_16k_or_48k.wav --seconds 90 \
        --json docs/flashhead_spike/smoke_test.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
import wave
from pathlib import Path

import aiohttp
import numpy as np
from livekit import api, rtc
from livekit.agents.voice.avatar import DataStreamAudioOutput

SR = 16000
FRAME = SR // 50  # 20 ms
AVATAR_IDENTITY = "flashhead-avatar"
AGENT_IDENTITY = "smoke-agent"
USER_IDENTITY = "smoke-user"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_audio(path: str | None, seconds: float) -> np.ndarray:
    if path:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            channels = w.getnchannels()
            if w.getsampwidth() != 2:
                raise SystemExit("only 16-bit PCM WAV files are supported")
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if sr != SR:
            n = int(len(pcm) * SR / sr)
            pcm = np.interp(
                np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm
            ).astype(np.int16)
    else:
        t = np.arange(int(SR * seconds)) / SR
        envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 1.5 * t)
        signal = 0.2 * np.sin(2 * np.pi * 120 * t) + 0.1 * np.sin(2 * np.pi * 240 * t)
        pcm = (signal * envelope * 32767).astype(np.int16)

    target = int(SR * seconds)
    if len(pcm) < target:
        pcm = np.tile(pcm, int(np.ceil(target / len(pcm))))[:target]
    return pcm[:target]


async def wait_for(predicate, limit: float, step: float = 0.05) -> bool:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(step)
    return False


class VideoMeter:
    def __init__(self) -> None:
        self.frames = 0
        self.first_at: float | None = None
        self.last_at: float | None = None
        self.size = (0, 0)

    async def watch(self, track: rtc.Track) -> None:
        async for event in rtc.VideoStream(track):
            self.frames += 1
            self.size = (event.frame.width, event.frame.height)
            now = time.monotonic()
            if self.first_at is None:
                self.first_at = now
            self.last_at = now

    @property
    def fps(self) -> float | None:
        if self.first_at is None or self.last_at is None or self.last_at <= self.first_at:
            return None
        return (self.frames - 1) / (self.last_at - self.first_at)


class AudioMeter:
    def __init__(self) -> None:
        self.samples = 0
        self.first_at: float | None = None
        self.last_at: float | None = None

    async def listen(self, track: rtc.Track) -> None:
        async for event in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
            self.samples += event.frame.samples_per_channel
            now = time.monotonic()
            if self.first_at is None:
                self.first_at = now
            self.last_at = now

    @property
    def seconds(self) -> float:
        return self.samples / SR


async def send_audio(output: DataStreamAudioOutput, pcm: np.ndarray) -> None:
    for i in range(0, len(pcm), FRAME):
        chunk = pcm[i : i + FRAME]
        if len(chunk) < FRAME:
            chunk = np.pad(chunk, (0, FRAME - len(chunk)))
        frame = rtc.AudioFrame(chunk.tobytes(), SR, 1, FRAME)
        await output.capture_frame(frame)
        await asyncio.sleep(FRAME / SR)
    output.flush()


def mint_tokens(url: str, key: str, secret: str, room: str) -> tuple[str, str, str]:
    def token(identity: str, *, on_behalf: str | None = None) -> str:
        builder = (
            api.AccessToken(key, secret)
            .with_identity(identity)
            .with_name(identity)
            .with_kind("agent")
            .with_grants(api.VideoGrants(room_join=True, room=room))
        )
        if on_behalf:
            builder = builder.with_attributes({"lk.publish_on_behalf": on_behalf})
        return builder.to_jwt()

    return (
        token(AGENT_IDENTITY),
        token(AVATAR_IDENTITY, on_behalf=AGENT_IDENTITY),
        token(USER_IDENTITY),
    )


async def main(args) -> dict:
    load_dotenv(Path(args.env_file))
    url = os.environ["LIVEKIT_URL"]
    key = os.environ["LIVEKIT_API_KEY"]
    secret = os.environ["LIVEKIT_API_SECRET"]
    internal_token = os.environ.get("INTERNAL_API_TOKEN")
    launch_url = args.launch_url or os.environ.get("FLASHHEAD_LAUNCH_URL")
    if not launch_url:
        raise SystemExit("pass --launch-url or set FLASHHEAD_LAUNCH_URL")

    room_name = f"flashhead-smoke-{uuid.uuid4().hex[:8]}"
    agent_token, avatar_token, user_token = mint_tokens(url, key, secret, room_name)

    lk = api.LiveKitAPI(url, key, secret)
    await lk.room.create_room(api.CreateRoomRequest(name=room_name))
    print(f"room: {room_name}")

    agent_room = rtc.Room()
    user_room = rtc.Room()
    video, audio = VideoMeter(), AudioMeter()
    tasks: list[asyncio.Task] = []

    @user_room.on("track_subscribed")
    def on_track(track: rtc.Track, publication, participant: rtc.RemoteParticipant) -> None:
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            tasks.append(asyncio.create_task(video.watch(track)))
        elif track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(audio.listen(track)))

    t_launch = time.monotonic()
    results: dict = {"room": room_name, "launch_url": launch_url}
    try:
        await agent_room.connect(url, agent_token)
        await user_room.connect(url, user_token)
        print(f"agent joined as {agent_room.local_participant.identity}")

        payload = {
            "room": room_name,
            "livekit_url": url,
            "token": avatar_token,
            "agent_identity": AGENT_IDENTITY,
        }
        headers = {"Authorization": f"Bearer {internal_token}"} if internal_token else {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
            async with http.post(f"{launch_url.rstrip('/')}/launch", json=payload, headers=headers) as r:
                body = await r.text()
                print(f"launch: HTTP {r.status} {body[:300]}")
                if r.status >= 300:
                    raise SystemExit(f"launch failed: {r.status} {body}")

        joined = await wait_for(
            lambda: any(p.identity == AVATAR_IDENTITY for p in agent_room.remote_participants.values()),
            args.join_timeout,
        )
        if not joined:
            raise SystemExit("avatar did not join in time")
        t_join = time.monotonic()
        results["join_seconds"] = round(t_join - t_launch, 2)
        print(f"avatar joined after {results['join_seconds']}s")

        published = await wait_for(
            lambda: any(
                p.identity == AVATAR_IDENTITY
                and any(pub.kind == rtc.TrackKind.KIND_VIDEO for pub in p.track_publications.values())
                for p in agent_room.remote_participants.values()
            ),
            args.join_timeout,
        )
        results["video_track_published"] = published
        print(f"video track published: {published}")

        output = DataStreamAudioOutput(
            agent_room,
            destination_identity=AVATAR_IDENTITY,
            sample_rate=SR,
            wait_playback_start=True,
        )
        started = asyncio.Event()
        finished = asyncio.Event()
        finished_ev: dict = {}

        @output.on("playback_started")
        def on_started(event) -> None:
            results["playback_started_after_launch_s"] = round(time.monotonic() - t_launch, 2)
            started.set()

        @output.on("playback_finished")
        def on_finished(event) -> None:
            finished_ev.update(
                playback_position=event.playback_position, interrupted=event.interrupted
            )
            finished.set()

        pcm = np.zeros(0, dtype=np.int16) if args.no_audio else load_audio(args.wav, args.seconds)
        results["audio_sent_seconds"] = round(len(pcm) / SR, 2)
        if len(pcm):
            print(f"sending {results['audio_sent_seconds']}s of audio at 16 kHz")
            send_task = asyncio.create_task(send_audio(output, pcm))
            if not await wait_for(started.is_set, 30):
                raise SystemExit("playback_started was not received")
            print(f"playback started after {results['playback_started_after_launch_s']}s")
            await send_task
            if not await wait_for(finished.is_set, 30):
                raise SystemExit("playback_finished was not received")
            results.update(finished_ev)
            await asyncio.sleep(1.0)  # let the last frames reach the subscriber
        else:
            print(f"no audio; measuring idle video for {args.seconds}s")
            await wait_for(lambda: video.first_at is not None, 30)
            await asyncio.sleep(args.seconds)

        if video.first_at is not None:
            results["video"] = {
                "frames": video.frames,
                "fps": round(video.fps or 0.0, 2),
                "size": list(video.size),
                "first_frame_after_launch_s": round(video.first_at - t_launch, 2),
                "measured_seconds": round((video.last_at or video.first_at) - video.first_at, 2),
            }
        if audio.first_at is not None:
            results["audio_received"] = {
                "seconds": round(audio.seconds, 2),
                "measured_seconds": round((audio.last_at or audio.first_at) - audio.first_at, 2),
                "realtime_ratio": round(
                    audio.seconds / max(1e-6, (audio.last_at or audio.first_at) - audio.first_at), 3
                ),
            }
        results["ok"] = bool(
            results.get("video_track_published")
            and results.get("video", {}).get("fps", 0) >= 24.0
            and (args.no_audio or not results.get("interrupted", True))
        )
        return results
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await agent_room.disconnect()
        await user_room.disconnect()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception:
            pass
        await lk.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch-url", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--wav", default=None, help="16-bit PCM WAV; synthetic speech if omitted")
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--no-audio", action="store_true", help="measure idle video only")
    parser.add_argument("--join-timeout", type=float, default=90.0)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    result = asyncio.run(main(args))
    print("\nRESULTS")
    print(json.dumps(result, indent=2))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n")

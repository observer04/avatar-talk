"""Headless end-to-end call: talks to the running server over WebRTC like a browser.

Speaks Kokoro-synthesised questions into the bot, records the bot's audio, prints
RTVI events (transcripts, avatar cues), and measures response latency and
barge-in (interruption) time.

Usage (server must be running):  uv run scripts/e2e_call.py [--url http://localhost:7860]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json
import time
import wave
from fractions import Fraction

import aiohttp
import numpy as np
import soxr
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame
from kokoro_onnx import Kokoro

SR = 48000
FRAME = 960  # 20 ms


class SpeechTrack(MediaStreamTrack):
    """Fake microphone: silence, plus whatever utterances are queued with say()."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._pending = np.zeros(0, dtype=np.int16)
        self._pts = 0
        self._t0 = None
        self.done_speaking = asyncio.Event()
        self.done_speaking.set()

    def say(self, samples: np.ndarray):
        self._pending = np.concatenate([self._pending, samples])
        self.done_speaking.clear()

    async def recv(self):
        if self._t0 is None:
            self._t0 = time.monotonic()
        wait = self._t0 + self._pts / SR - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        if len(self._pending):
            chunk, self._pending = self._pending[:FRAME], self._pending[FRAME:]
            chunk = np.pad(chunk, (0, FRAME - len(chunk)))
            if not len(self._pending):
                self.done_speaking.set()
        else:
            chunk = np.zeros(FRAME, dtype=np.int16)
        frame = AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = SR
        frame.pts = self._pts
        frame.time_base = Fraction(1, SR)
        self._pts += FRAME
        return frame


class Recorder:
    def __init__(self):
        self.chunks: list[np.ndarray] = []
        self.sr = SR
        self.last_loud = 0.0
        self.first_loud_after: float | None = None
        self.watch_from: float | None = None

    async def consume(self, track):
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            pcm = frame.to_ndarray().astype(np.int16)
            if pcm.ndim == 2 and pcm.shape[0] > 1:  # planar stereo -> mono
                pcm = pcm.mean(axis=0).astype(np.int16)
            pcm = pcm.reshape(-1)
            if frame.layout.name == "stereo" and pcm.size == 2 * frame.samples:
                pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
            self.sr = frame.sample_rate
            self.chunks.append(pcm)
            now = time.monotonic()
            if np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) > 300:
                self.last_loud = now
                if self.watch_from and self.first_loud_after is None and now > self.watch_from:
                    self.first_loud_after = now

    def save(self, path):
        audio = np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.int16)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes(audio.tobytes())


def synth(kokoro: Kokoro, text: str) -> np.ndarray:
    samples, sr = kokoro.create(text, voice="am_michael", speed=1.0, lang="en-us")
    samples = soxr.resample(samples, sr, SR)
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16)


async def main(url: str, out: str):
    from config import settings
    from services import _kokoro_files, prefetch_models

    cfg = settings.model_copy(update={"tts_provider": "kokoro"})
    prefetch_models(cfg)
    model, voices = _kokoro_files(cfg)
    kokoro = Kokoro(str(model), str(voices))
    q_weather = synth(kokoro, "What's the weather like in London right now?")
    q_story = synth(kokoro, "Tell me a long story about a dragon who learns to cook.")
    q_barge = synth(kokoro, "Stop, stop. Actually, what time is it in Tokyo?")

    events: list[tuple[float, str, dict]] = []
    state = {"bot_speaking": False}
    bot_stopped = asyncio.Event()
    bot_started = asyncio.Event()

    pc = RTCPeerConnection()
    mic = SpeechTrack()
    pc.addTrack(mic)
    pc.addTransceiver("video", direction="recvonly")
    rec = Recorder()

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            asyncio.ensure_future(rec.consume(track))

    dc = pc.createDataChannel("chat")

    @dc.on("open")
    def on_open():
        dc.send(json.dumps({
            "label": "rtvi-ai", "type": "client-ready", "id": "ready-1",
            "data": {"version": "2.3.0", "about": {"library": "e2e_call.py"}},
        }))

    @dc.on("message")
    def on_message(msg):
        try:
            m = json.loads(msg)
        except Exception:
            return
        t, data = m.get("type", ""), m.get("data") or {}
        events.append((time.monotonic(), t, data))
        if t == "bot-started-speaking":
            state["bot_speaking"] = True
            bot_started.set()
            bot_stopped.clear()
        elif t == "bot-stopped-speaking":
            state["bot_speaking"] = False
            bot_stopped.set()
            bot_started.clear()
        if t == "user-transcription" and data.get("final"):
            print(f"  USER: {data.get('text')}")
        elif t == "bot-output" and data.get("text") and data.get("aggregated_by", "sentence") != "word":
            print(f"  BOT : {data.get('text')}")
        elif t == "server-message":
            print(f"  CUE : {data}")
        elif t in ("error", "error-response"):
            print(f"  ERROR: {data}")

    async def keepalive():
        while True:
            await asyncio.sleep(1)
            if dc.readyState == "open":
                dc.send(f"ping: {time.time()}")

    await pc.setLocalDescription(await pc.createOffer())
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{url}/api/offer", json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}) as r:
            answer = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
    ka = asyncio.create_task(keepalive())

    async def wait_turn_done(label: str, timeout: float = 45, quiet_secs: float = 3.0):
        """Wait until the bot has spoken and then stayed silent for quiet_secs
        (a filler like "Let me check." is followed by the real answer)."""
        await asyncio.wait_for(bot_started.wait(), timeout)
        while True:
            await asyncio.wait_for(bot_stopped.wait(), timeout)
            try:
                await asyncio.wait_for(bot_started.wait(), quiet_secs)
            except asyncio.TimeoutError:
                break
        print(f"  [{label}: bot finished speaking]")

    results = {}
    try:
        print("1) Greeting (client-ready)")
        await wait_turn_done("greeting")
        await asyncio.sleep(1.0)

        print("2) Tool question (weather)")
        mic.say(q_weather)
        await mic.done_speaking.wait()
        t_end = time.monotonic()
        rec.watch_from = t_end
        await asyncio.wait_for(bot_started.wait(), 30)
        results["response_latency_s (end of user speech -> bot-started-speaking)"] = round(time.monotonic() - t_end, 2)
        await wait_turn_done("weather")
        if rec.first_loud_after:
            results["response_latency_s (end of user speech -> first loud bot audio)"] = round(rec.first_loud_after - t_end, 2)
        await asyncio.sleep(1.0)

        print("3) Barge-in: long story, interrupted after 3 s of speech")
        mic.say(q_story)
        await asyncio.wait_for(bot_started.wait(), 30)
        await asyncio.sleep(3.0)
        t_interrupt = time.monotonic()
        mic.say(q_barge)
        await asyncio.wait_for(bot_stopped.wait(), 10)
        results["barge_in_s (user starts talking -> bot-stopped-speaking)"] = round(time.monotonic() - t_interrupt, 2)
        await wait_turn_done("after barge-in")
        await asyncio.sleep(1.0)
    finally:
        ka.cancel()
        await pc.close()
        rec.save(out)

    print("\nRESULTS")
    for k, v in results.items():
        print(f"  {k}: {v}")
    cues = [d for _, t, d in events if t == "server-message"]
    print(f"  avatar cues received: {cues}")
    print(f"  bot audio saved to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:7860")
    ap.add_argument("--out", default="e2e_bot_audio.wav")
    args = ap.parse_args()
    asyncio.run(main(args.url, args.out))

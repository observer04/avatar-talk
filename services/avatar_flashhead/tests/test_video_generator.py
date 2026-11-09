"""VideoGenerator contract tests with a fake model (no torch, CPU only)."""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest
from avatar_flashhead.video_generator import FlashHeadVideoGenerator, ModelParams
from livekit import rtc
from livekit.agents.voice.avatar import AudioSegmentEnd


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


class FakeModel:
    def __init__(self, params: ModelParams | None = None) -> None:
        self.params = params or ModelParams()
        self.calls: list[tuple[np.ndarray, int, int]] = []
        self.resets = 0
        self._value = 0

    def reset(self) -> None:
        self.resets += 1

    def generate(self, window: np.ndarray, audio_start_idx: int, audio_end_idx: int) -> np.ndarray:
        self.calls.append((np.array(window, copy=True), audio_start_idx, audio_end_idx))
        n = self.params.frame_num
        frames = np.zeros((n, self.params.height, self.params.width, 3), dtype=np.uint8)
        frames[:, :, :, 0] = ((self._value + np.arange(n)) % 256)[:, None, None]
        self._value += 1
        return frames


def make_generator(model: FakeModel | None = None) -> tuple[FlashHeadVideoGenerator, FakeModel, FakeClock]:
    model = model or FakeModel()
    clock = FakeClock()
    gen = FlashHeadVideoGenerator(model, clock=clock, sleep=clock.sleep)
    return gen, model, clock


def audio_frame(samples: np.ndarray, sample_rate: int = 16000) -> rtc.AudioFrame:
    pcm = np.ascontiguousarray(samples, dtype=np.int16)
    return rtc.AudioFrame(pcm.tobytes(), sample_rate, 1, int(pcm.size))


async def wait_until(predicate, limit: float = 2.0) -> None:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met in time")


async def take(gen: FlashHeadVideoGenerator, n: int) -> list:
    return [await gen.__anext__() for _ in range(n)]


async def test_chunk_math_and_audio_interleave():
    gen, model, clock = make_generator()
    p = model.params
    samples = np.arange(p.slice_samples * 2, dtype=np.int32) % 1000
    samples = samples.astype(np.int16) - 500
    try:
        await gen.push_audio(audio_frame(samples))

        per_frame = 1 + gen._audio_frames_per_video_frame
        items = await take(gen, p.slice_len * per_frame)
        videos = [x for x in items if isinstance(x, rtc.VideoFrame)]
        audios = [x for x in items if isinstance(x, rtc.AudioFrame)]
        assert len(videos) == p.slice_len
        assert len(audios) == p.slice_len * gen._audio_frames_per_video_frame
        assert all(v.width == p.width and v.height == p.height for v in videos)

        # The first chunk covers exactly the first slice of audio.
        played = np.concatenate(
            [np.frombuffer(a.data, dtype=np.int16) for a in audios]
        )
        np.testing.assert_array_equal(played, samples[: p.slice_samples])

        # Cadence: one 40 ms sleep per video frame after the first.
        assert len(clock.sleeps) == p.slice_len - 1
        assert all(abs(d - 1 / p.fps) < 1e-6 for d in clock.sleeps)

        # Ring buffer: the first call sees 8 s of silence with the new slice at the end.
        window, start, end = model.calls[0]
        assert start == p.audio_start_idx and end == p.audio_end_idx
        assert window.shape == (p.window_samples,)
        np.testing.assert_array_equal(window[-p.slice_samples :], samples[: p.slice_samples] / 32768.0)
        assert np.all(window[: -p.slice_samples] == 0.0)

        # The second chunk's window rolls in the second slice.
        await take(gen, p.slice_len * per_frame)
        window2 = model.calls[1][0]
        np.testing.assert_array_equal(
            window2[-2 * p.slice_samples : -p.slice_samples], samples[: p.slice_samples] / 32768.0
        )
        np.testing.assert_array_equal(
            window2[-p.slice_samples :], samples[p.slice_samples : 2 * p.slice_samples] / 32768.0
        )
    finally:
        await gen.aclose()


async def test_silence_generates_idle_frames():
    gen, model, _ = make_generator()
    p = model.params
    try:
        items = await take(gen, p.slice_len)
        assert all(isinstance(x, rtc.VideoFrame) for x in items)
        assert model.calls, "idle motion should call the model"
        assert np.all(model.calls[0][0] == 0.0)
        assert gen.chunks_generated >= 1
    finally:
        await gen.aclose()


async def test_clear_buffer_drops_pending_audio_and_resets():
    gen, model, _ = make_generator()
    p = model.params
    samples = np.full(p.slice_samples * 4, 1234, dtype=np.int16)
    try:
        await gen.push_audio(audio_frame(samples))
        await take(gen, 3)  # start playing the first chunk
        calls_before = len(model.calls)
        gen.clear_buffer()
        assert gen._pending_samples == 0

        await wait_until(lambda: model.resets >= 1)
        # Everything after the interrupt is idle video, no stale audio.
        items = await take(gen, p.slice_len)
        assert all(isinstance(x, rtc.VideoFrame) for x in items)
        # A stale in-flight chunk may still land, but a zero-window idle chunk
        # must have been generated after the reset.
        assert any(np.all(call[0] == 0.0) for call in model.calls[calls_before:])
    finally:
        await gen.aclose()


async def test_interrupt_stops_promptly():
    gen, model, _ = make_generator()
    p = model.params
    samples = np.full(p.slice_samples * 20, 4321, dtype=np.int16)
    try:
        await gen.push_audio(audio_frame(samples))
        await take(gen, 5)
        calls_before = len(model.calls)
        started = time.monotonic()
        gen.clear_buffer()
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert time.monotonic() - started < 1.0
        assert isinstance(frame, rtc.VideoFrame)
        assert any(np.all(call[0] == 0.0) for call in model.calls[calls_before:])
    finally:
        await gen.aclose()


async def test_segment_end_after_padded_last_chunk():
    gen, model, _ = make_generator()
    p = model.params
    # 1.5 slices, then the agent closes the segment.
    samples = np.full(p.slice_samples + p.slice_samples // 2, 777, dtype=np.int16)
    try:
        await gen.push_audio(audio_frame(samples))
        await gen.push_audio(AudioSegmentEnd())

        per_frame = 1 + gen._audio_frames_per_video_frame
        first = await take(gen, p.slice_len * per_frame)
        assert (
            len([x for x in first if isinstance(x, rtc.AudioFrame)])
            == p.slice_len * gen._audio_frames_per_video_frame
        )

        second = await take(gen, p.slice_len * per_frame)
        audios = [x for x in second if isinstance(x, rtc.AudioFrame)]
        assert len(audios) == p.slice_len * gen._audio_frames_per_video_frame
        played = np.concatenate([np.frombuffer(a.data, dtype=np.int16) for a in audios])
        np.testing.assert_array_equal(played[: p.slice_samples // 2], samples[-p.slice_samples // 2 :])
        assert np.all(played[p.slice_samples // 2 :] == 0)

        marker = await gen.__anext__()
        assert isinstance(marker, AudioSegmentEnd)
    finally:
        await gen.aclose()


async def test_ring_buffer_rolls_with_small_params():
    params = ModelParams(
        sample_rate=16000,
        fps=25,
        frame_num=9,
        motion_frames_num=1,
        cached_audio_duration=2,
        height=8,
        width=8,
    )
    model = FakeModel(params)
    gen, model, _ = make_generator(model)
    p = model.params
    assert p.slice_len == 8 and p.slice_samples == 5120 and p.window_samples == 32000
    try:
        first = np.full(p.slice_samples, 16384, dtype=np.int16)  # 0.5
        await gen.push_audio(audio_frame(first))
        await wait_until(lambda: len(model.calls) >= 1)
        window1 = model.calls[0][0]
        np.testing.assert_array_equal(window1[-p.slice_samples :], first / 32768.0)
        assert np.all(window1[: -p.slice_samples] == 0.0)

        second = np.full(p.slice_samples, 8192, dtype=np.int16)  # 0.25
        await gen.push_audio(audio_frame(second))
        await wait_until(lambda: len(model.calls) >= 2)
        window2 = model.calls[1][0]
        np.testing.assert_array_equal(
            window2[-2 * p.slice_samples : -p.slice_samples], first / 32768.0
        )
        np.testing.assert_array_equal(window2[-p.slice_samples :], second / 32768.0)
        assert np.all(window2[: -2 * p.slice_samples] == 0.0)
    finally:
        await gen.aclose()


async def test_rejects_wrong_sample_rate():
    gen, _, _ = make_generator()
    try:
        with pytest.raises(ValueError, match="16000"):
            await gen.push_audio(audio_frame(np.zeros(480, dtype=np.int16), sample_rate=48000))
    finally:
        await gen.aclose()

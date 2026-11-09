"""Streaming video generator for SoulX-FlashHead.

This module is deliberately torch-free: the heavy model is injected.  The
production adapter lives in :mod:`avatar_flashhead.model` (imported only inside
the Modal GPU image); tests inject a small fake.

The streaming algorithm mirrors ``spike/fh_worker.py``:

* an ``cached_audio_duration`` (8 s) rolling 16 kHz window,
* wav2vec2 embedding over the window, take the last ``frame_num`` frames,
* 4-step distilled DiT + VAE decode,
* drop the ``motion_frames_num`` overlap frames, keep ``slice_len`` new frames
  (24 at 25 fps = 0.96 s of audio for the Lite model).

The generator consumes audio in ``slice_samples`` chunks and emits the matching
video frames paced at the model's native fps.  Audio frames are interleaved with
the video frames so the published audio track stays in sync.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from livekit import rtc
from livekit.agents.voice.avatar import AudioSegmentEnd

log = logging.getLogger(__name__)

# Audio is published in 40 ms frames (one per video frame at 25 fps).  Larger
# frames mean fewer AudioSource FFI round-trips on the event loop, which is
# what keeps the 25 fps pacer from stalling under encoder load.
AUDIO_FRAME_MS = 40
# If the pacer falls further behind than this, re-anchor instead of bursting.
MAX_PACING_LAG_S = 0.3


@dataclass(frozen=True)
class ModelParams:
    """Inference parameters exposed by the model adapter."""

    sample_rate: int = 16000
    fps: int = 25
    frame_num: int = 33
    motion_frames_num: int = 9
    cached_audio_duration: int = 8
    height: int = 512
    width: int = 512

    @property
    def slice_len(self) -> int:
        """New frames produced per chunk (overlap frames are dropped)."""
        return self.frame_num - self.motion_frames_num

    @property
    def slice_samples(self) -> int:
        """Audio samples consumed per chunk."""
        return self.slice_len * self.sample_rate // self.fps

    @property
    def window_samples(self) -> int:
        return self.cached_audio_duration * self.sample_rate

    @property
    def audio_start_idx(self) -> int:
        return self.cached_audio_duration * self.fps - self.frame_num

    @property
    def audio_end_idx(self) -> int:
        return self.cached_audio_duration * self.fps


@runtime_checkable
class FlashHeadModel(Protocol):
    """The part of the model the generator needs (see model.py for the real one)."""

    params: ModelParams

    def reset(self) -> None:
        """Restart motion latents from the reference image (after an interrupt)."""

    def generate(
        self, window: np.ndarray, audio_start_idx: int, audio_end_idx: int
    ) -> np.ndarray:
        """Return ``frame_num`` uint8 RGB frames ``[N, H, W, 3]`` for the window."""


@dataclass
class _Chunk:
    frames: np.ndarray | None  # [slice_len, H, W, 3] uint8
    audio: np.ndarray | None  # int16, slice_samples; None for idle chunks
    segment_end: bool
    epoch: int


class _Clear:
    pass


_CLEAR = _Clear()


@dataclass
class _Failure:
    exc: BaseException


class FlashHeadVideoGenerator:
    """``VideoGenerator`` implementation: audio in, paced video/audio frames out.

    ``push_audio`` accepts 16 kHz mono ``rtc.AudioFrame`` objects and
    ``AudioSegmentEnd`` markers.  ``__anext__`` yields ``rtc.VideoFrame`` and
    ``rtc.AudioFrame`` objects at 25 fps, plus ``AudioSegmentEnd`` after the last
    frame of a TTS segment.  When no audio is pending it generates idle motion
    from silence, so the avatar keeps breathing between turns.
    """

    def __init__(
        self,
        model: FlashHeadModel,
        *,
        output_size: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        audio_frame_ms: int = AUDIO_FRAME_MS,
    ) -> None:
        self._model = model
        self.params = model.params
        self.output_size = output_size or self.params.width
        if self.params.width % self.output_size:
            raise ValueError("output_size must divide the model width")
        self._output_step = self.params.width // self.output_size
        self._clock = clock
        self._sleep = sleep
        self._audio_frame_samples = max(1, self.params.sample_rate * audio_frame_ms // 1000)
        self._audio_per_frame = max(1, self.params.sample_rate // self.params.fps)
        self._audio_frames_per_video_frame = max(
            1, -(-self._audio_per_frame // self._audio_frame_samples)  # ceil
        )

        # 8 s rolling window, zero-initialised like fh_worker.new_stream().
        # A numpy ring (not a deque of Python floats) keeps the per-chunk shift
        # in C and off the event loop's critical path.
        self._ring = np.zeros(self.params.window_samples, dtype=np.float32)

        # Audio not yet consumed by the model worker.
        self._pending: deque[np.ndarray] = deque()
        self._pending_samples = 0
        self._segment_open = False
        self._segment_end_samples: int | None = None
        self._audio_event = asyncio.Event()

        # Output queue: bounded so idle generation cannot run far ahead of playback.
        self._out: asyncio.Queue[_Chunk | _Clear | _Failure | None] = asyncio.Queue(maxsize=2)
        self._task: asyncio.Task[None] | None = None

        self._epoch = 0
        self._reset_requested = False
        self._closed = False
        self._failure: BaseException | None = None

        self._chunk: _Chunk | None = None
        self._frame_idx = 0
        self._audio_sub = 0
        self._video_sent = False
        self._next_deadline: float | None = None

        self.frames_emitted = 0
        self.audio_frames_emitted = 0
        self.chunks_generated = 0
        self.last_chunk_seconds: float | None = None

    # ------------------------------------------------------------------ input

    async def push_audio(self, frame: rtc.AudioFrame | AudioSegmentEnd) -> None:
        self._ensure_started()
        if isinstance(frame, AudioSegmentEnd):
            if self._segment_open:
                self._segment_end_samples = self._pending_samples
                self._segment_open = False
            elif self._segment_end_samples is None:
                # Empty segment (e.g. after clear_buffer): acknowledge it.
                self._segment_end_samples = 0
            self._audio_event.set()
            return

        if frame.sample_rate != self.params.sample_rate:
            raise ValueError(
                f"FlashHead expects {self.params.sample_rate} Hz audio, got {frame.sample_rate} Hz"
            )
        pcm = np.frombuffer(frame.data, dtype=np.int16)
        if frame.num_channels != 1:
            pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1).astype(np.int16)
        pcm = np.ascontiguousarray(pcm, dtype=np.int16)
        if pcm.size == 0:
            return
        self._pending.append(pcm)
        self._pending_samples += int(pcm.size)
        self._segment_open = True
        self._audio_event.set()

    def clear_buffer(self) -> None:
        """Drop buffered audio and queued frames; restart motion from the photo."""
        self._epoch += 1
        self._pending.clear()
        self._pending_samples = 0
        self._segment_open = False
        self._segment_end_samples = None
        self._reset_requested = True
        self._ring = np.zeros(self.params.window_samples, dtype=np.float32)
        self._chunk = None
        self._frame_idx = 0
        self._audio_sub = 0
        self._video_sent = False
        self._next_deadline = None
        self._drain_output()
        with contextlib.suppress(asyncio.QueueFull):
            self._out.put_nowait(_CLEAR)
        self._audio_event.set()

    # ----------------------------------------------------------------- output

    def __aiter__(self) -> AsyncIterator[rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd]:
        return self

    async def __anext__(self) -> rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd:
        self._ensure_started()
        while True:
            if self._failure is not None:
                raise self._failure
            if self._chunk is None:
                if self._closed and self._out.empty():
                    raise StopAsyncIteration
                item = await self._out.get()
                if item is None:
                    raise StopAsyncIteration
                if item is _CLEAR:
                    continue
                if isinstance(item, _Failure):
                    raise item.exc
                if item.epoch != self._epoch:
                    continue
                self._chunk = item
                self._frame_idx = 0
                self._audio_sub = 0
                self._video_sent = False
                if item.frames is None or len(item.frames) == 0:
                    self._chunk = None
                    if item.segment_end:
                        return AudioSegmentEnd()
                    continue

            chunk = self._chunk
            if chunk.epoch != self._epoch:
                self._chunk = None
                continue

            if not self._video_sent:
                await self._pace()
                if chunk.epoch != self._epoch:
                    self._chunk = None
                    continue
                frame = chunk.frames[self._frame_idx]
                if self._output_step > 1:
                    frame = frame[:: self._output_step, :: self._output_step]
                self._video_sent = True
                self.frames_emitted += 1
                return rtc.VideoFrame(
                    frame.shape[1],
                    frame.shape[0],
                    rtc.VideoBufferType.RGB24,
                    frame.tobytes(),
                )

            if chunk.audio is not None and self._audio_sub < self._audio_frames_per_video_frame:
                frame_audio_start = self._frame_idx * self._audio_per_frame
                frame_audio_end = min(frame_audio_start + self._audio_per_frame, len(chunk.audio))
                start = frame_audio_start + self._audio_sub * self._audio_frame_samples
                end = min(start + self._audio_frame_samples, frame_audio_end)
                self._audio_sub += 1
                if start >= end:
                    continue
                pcm = chunk.audio[start:end]
                self.audio_frames_emitted += 1
                return rtc.AudioFrame(
                    pcm.tobytes(), self.params.sample_rate, 1, int(pcm.size)
                )

            self._frame_idx += 1
            self._audio_sub = 0
            self._video_sent = False
            if self._frame_idx >= len(chunk.frames):
                self._chunk = None
                if chunk.segment_end:
                    return AudioSegmentEnd()

    async def aclose(self) -> None:
        self._closed = True
        self._audio_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._drain_output()
        with contextlib.suppress(asyncio.QueueFull):
            self._out.put_nowait(None)

    # ---------------------------------------------------------------- worker

    def _ensure_started(self) -> None:
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(self._run(), name="flashhead-generator")

    async def _run(self) -> None:
        try:
            while not self._closed:
                chunk = await self._next_chunk()
                if chunk is None:
                    return
                await self._out.put(chunk)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - surfaced to the consumer
            log.exception("FlashHead generator failed")
            self._failure = exc
            with contextlib.suppress(asyncio.QueueFull):
                self._out.put_nowait(_Failure(exc))

    async def _next_chunk(self) -> _Chunk | None:
        p = self.params
        while not self._closed:
            # Capture the epoch before taking audio: if clear_buffer() runs
            # while this chunk is being generated, the chunk is stamped stale
            # and dropped instead of playing the interrupted speech.
            epoch = self._epoch
            if self._reset_requested:
                await asyncio.to_thread(self._model.reset)
                self._reset_requested = False

            if self._segment_end_samples is not None:
                if self._segment_end_samples == 0:
                    self._segment_end_samples = None
                    return _Chunk(frames=None, audio=None, segment_end=True, epoch=epoch)
                if self._segment_end_samples <= p.slice_samples:
                    n = self._segment_end_samples
                    pcm = self._take(n)
                    self._segment_end_samples = None
                    return await self._generate(pcm, segment_end=True, epoch=epoch)
                pcm = self._take(p.slice_samples)
                self._segment_end_samples -= p.slice_samples
                return await self._generate(pcm, segment_end=False, epoch=epoch)

            if self._pending_samples >= p.slice_samples:
                pcm = self._take(p.slice_samples)
                return await self._generate(pcm, segment_end=False, epoch=epoch)

            if self._segment_open:
                # Mid-utterance: wait for the next TTS frames instead of padding
                # silence into the sentence.
                self._audio_event.clear()
                await self._audio_event.wait()
                continue

            if self._pending_samples > 0:
                # Defensive: audio without a segment marker -> treat it as a
                # segment ending at the current buffer boundary.
                self._segment_end_samples = self._pending_samples
                continue

            # Idle: keep the avatar alive with motion driven by silence.
            pcm = np.zeros(p.slice_samples, dtype=np.int16)
            return await self._generate(pcm, segment_end=False, idle=True, epoch=epoch)
        return None

    def _generate_sync(
        self, ring: np.ndarray, pcm: np.ndarray, *, segment_end: bool, idle: bool, epoch: int
    ) -> _Chunk:
        """Runs in a worker thread: ring shift + model call + frame slicing.

        ``ring`` is captured on the event loop before the thread starts, so a
        concurrent ``clear_buffer()`` (which swaps ``self._ring`` for a fresh
        zero buffer) cannot be corrupted by an in-flight stale chunk.
        """
        audio_f32 = pcm.astype(np.float32) / 32768.0
        n = len(audio_f32)
        if n >= len(ring):
            ring[:] = audio_f32[-len(ring) :]
        else:
            ring[:-n] = ring[n:].copy()
            ring[-n:] = audio_f32

        frames = self._model.generate(
            ring, self.params.audio_start_idx, self.params.audio_end_idx
        )
        frames = np.asarray(frames)
        if frames.ndim != 4 or frames.shape[0] != self.params.frame_num:
            raise RuntimeError(
                f"model returned {frames.shape}, expected [{self.params.frame_num}, H, W, 3]"
            )
        frames = np.ascontiguousarray(frames[self.params.motion_frames_num :], dtype=np.uint8)
        return _Chunk(
            frames=frames,
            audio=None if idle else pcm,
            segment_end=segment_end,
            epoch=epoch,
        )

    async def _generate(
        self, pcm: np.ndarray, *, segment_end: bool, epoch: int, idle: bool = False
    ) -> _Chunk:
        started = time.perf_counter()
        ring = self._ring  # capture before the thread; clear_buffer may swap it
        chunk = await asyncio.to_thread(
            self._generate_sync, ring, pcm, segment_end=segment_end, idle=idle, epoch=epoch
        )
        self.last_chunk_seconds = time.perf_counter() - started
        self.chunks_generated += 1
        if self.chunks_generated == 1:
            log.info("FlashHead first chunk generated in %.3fs", self.last_chunk_seconds)
        elif self.chunks_generated % 25 == 0:
            log.info(
                "FlashHead stats: chunks=%d frames=%d last_chunk=%.3fs",
                self.chunks_generated,
                self.frames_emitted,
                self.last_chunk_seconds,
            )
        return chunk

    def _take(self, n: int) -> np.ndarray:
        """Take ``n`` real samples from the pending buffer, pad to one slice."""
        out = np.zeros(self.params.slice_samples, dtype=np.int16)
        filled = 0
        while filled < n and self._pending:
            head = self._pending[0]
            take = min(len(head), n - filled)
            out[filled : filled + take] = head[:take]
            if take == len(head):
                self._pending.popleft()
            else:
                self._pending[0] = head[take:]
            filled += take
        self._pending_samples -= filled
        return out

    async def _pace(self) -> None:
        now = self._clock()
        if self._next_deadline is None:
            self._next_deadline = now
        delay = self._next_deadline - now
        if delay > 0:
            await self._sleep(delay)
        elif delay < -MAX_PACING_LAG_S:
            log.warning("FlashHead video pacing fell behind by %.0f ms; re-anchoring", -delay * 1000)
            self._next_deadline = self._clock()
        self._next_deadline += 1.0 / self.params.fps

    def _drain_output(self) -> None:
        while True:
            try:
                self._out.get_nowait()
            except asyncio.QueueEmpty:
                return

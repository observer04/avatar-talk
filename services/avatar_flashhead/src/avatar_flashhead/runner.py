"""LiveKit room runner for the FlashHead avatar worker.

Joins the room with the token minted by the agent, receives the agent's TTS
audio over the ``lk.audio_stream`` DataStream, feeds it to
:class:`FlashHeadVideoGenerator`, and publishes synchronized audio + video
tracks.  Mirrors the reference ``livekit.agents.voice.avatar.AvatarRunner``
behaviour, without waiting for a remote subscription before publishing video.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from livekit import rtc
from livekit.agents.voice.avatar import AudioSegmentEnd, DataStreamAudioReceiver

from .video_generator import FlashHeadVideoGenerator

log = logging.getLogger(__name__)


class _AsyncAVSynchronizer(rtc.AVSynchronizer):
    """AVSynchronizer whose video capture does not block the event loop.

    ``VideoSource.capture_frame`` is a synchronous FFI call and can block for
    seconds when the WebRTC encoder is backpressured.  The stock synchronizer
    calls it directly from an asyncio task, which stalls every other coroutine
    (including the playback RPCs).  Running it in a worker thread keeps the
    event loop responsive; the FPS controller still paces at 25 fps.
    """

    async def _capture_video(self) -> None:
        while not self._stopped:
            frame, timestamp = await self._video_queue.get()
            if self._stopped:
                break
            async with self._fps_controller:
                await asyncio.to_thread(self._video_source.capture_frame, frame)
                if timestamp is not None:
                    self._last_video_time = timestamp
            self._video_queue.task_done()


class AvatarRunner:
    def __init__(
        self,
        *,
        livekit_url: str,
        token: str,
        agent_identity: str,
        video_generator: FlashHeadVideoGenerator,
        width: int = 512,
        height: int = 512,
        fps: int = 25,
        sample_rate: int = 16000,
        max_session_seconds: float | None = None,
    ) -> None:
        self._livekit_url = livekit_url
        self._token = token
        self._agent_identity = agent_identity
        self._video_gen = video_generator
        self._width = width
        self._height = height
        self._fps = fps
        self._sample_rate = sample_rate
        self._max_session_seconds = max_session_seconds

        self._room = rtc.Room()
        self._recv = DataStreamAudioReceiver(self._room, sender_identity=agent_identity)
        self._audio_source = rtc.AudioSource(sample_rate, 1, queue_size_ms=100)
        self._video_source = rtc.VideoSource(width, height)
        self._av_sync = _AsyncAVSynchronizer(
            audio_source=self._audio_source,
            video_source=self._video_source,
            video_fps=fps,
            video_queue_size_ms=500,
        )

        self._audio_pub: rtc.LocalTrackPublication | None = None
        self._video_pub: rtc.LocalTrackPublication | None = None
        self._publish_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

        self._playback_position = 0.0
        self._audio_playing = False
        self._done = asyncio.Event()
        self._reason = "unknown"

    @property
    def reason(self) -> str:
        return self._reason

    async def run(self) -> str:
        self._room.on("participant_disconnected", self._on_participant_disconnected)
        self._room.on("connection_state_changed", self._on_connection_state)
        self._room.on("disconnected", self._on_disconnected)
        self._recv.on("clear_buffer", self._on_clear_buffer)

        try:
            await self._room.connect(self._livekit_url, self._token)
            log.info(
                "joined room %s as %s (agent=%s)",
                self._room.name,
                self._room.local_participant.identity,
                self._agent_identity,
            )
            if not await self._start_receiver():
                return self._reason

            read_task = asyncio.create_task(self._read_audio(), name="flashhead-read-audio")
            forward_task = asyncio.create_task(self._forward_video(), name="flashhead-forward-video")
            stats_task = asyncio.create_task(self._stats(), name="flashhead-stats")
            done_task = asyncio.create_task(self._done.wait(), name="flashhead-done")
            wait_set: set[asyncio.Task] = {done_task, read_task, forward_task, stats_task}
            if self._max_session_seconds:
                wait_set.add(asyncio.create_task(asyncio.sleep(self._max_session_seconds)))
            finished, pending = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            if done_task not in finished:
                self._reason = "stream ended"
                for task in finished:
                    if task is done_task:
                        continue
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None:
                        self._reason = f"task failed: {exc!r}"
                        log.error("FlashHead runner task failed", exc_info=exc)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            return self._reason
        finally:
            await self._aclose()

    async def _start_receiver(self) -> bool:
        start_task = asyncio.create_task(self._recv.start(), name="flashhead-recv-start")
        done_task = asyncio.create_task(self._done.wait(), name="flashhead-recv-done")
        finished, pending = await asyncio.wait(
            {start_task, done_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if done_task in finished:
            self._reason = "agent left before the audio stream started"
            return False
        await start_task  # propagate connection / participant errors
        return True

    async def _stats(self) -> None:
        while True:
            await asyncio.sleep(10)
            log.info(
                "FlashHead video stats: gen_frames=%d chunks=%d last_chunk=%.3fs av_fps=%.2f",
                self._video_gen.frames_emitted,
                self._video_gen.chunks_generated,
                self._video_gen.last_chunk_seconds or 0.0,
                self._av_sync.actual_fps,
            )

    async def _read_audio(self) -> None:
        async for frame in self._recv:
            if isinstance(frame, rtc.AudioFrame) and not self._audio_playing:
                self._audio_playing = True
            await self._video_gen.push_audio(frame)

    async def _forward_video(self) -> None:
        async for frame in self._video_gen:
            if isinstance(frame, AudioSegmentEnd):
                if self._audio_playing:
                    self._recv.notify_playback_finished(
                        playback_position=self._playback_position, interrupted=False
                    )
                    self._audio_playing = False
                    self._playback_position = 0.0
                continue

            if self._audio_pub is None:
                await self._publish_tracks()

            await self._av_sync.push(frame)
            if isinstance(frame, rtc.AudioFrame):
                if self._playback_position == 0.0 and frame.duration > 0.0:
                    self._recv.notify_playback_started()
                self._playback_position += frame.duration

    async def _publish_tracks(self) -> None:
        async with self._publish_lock:
            if self._audio_pub is not None:
                return
            audio_track = rtc.LocalAudioTrack.create_audio_track(
                "avatar_audio", self._audio_source
            )
            self._audio_pub = await self._room.local_participant.publish_track(
                audio_track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
            )
            video_track = rtc.LocalVideoTrack.create_video_track(
                "avatar_video", self._video_source
            )
            # LiveKit's default 512x512 bitrate is ~500 kbps; at that rate the
            # encoder's rate control drops frames below 25 fps.  Give it room
            # and tell it to keep the frame rate.
            self._video_pub = await self._room.local_participant.publish_track(
                video_track,
                rtc.TrackPublishOptions(
                    source=rtc.TrackSource.SOURCE_CAMERA,
                    video_encoding=rtc.VideoEncoding(
                        max_bitrate=1_500_000, max_framerate=float(self._fps)
                    ),
                    video_codec=rtc.VideoCodec.Value("H264"),
                    video_encoder=rtc.VideoEncoderBackend.Value("ENCODER_BACKEND_NVENC"),
                    degradation_preference=rtc.DegradationPreference.MAINTAIN_FRAMERATE,
                ),
            )
            log.info("published avatar tracks (audio=%s video=%s)", self._audio_pub.sid, self._video_pub.sid)

    def _on_clear_buffer(self) -> None:
        self._video_gen.clear_buffer()
        self._spawn(self._av_sync.clear_queue())
        if self._audio_playing:
            self._recv.notify_playback_finished(
                playback_position=self._playback_position, interrupted=True
            )
            self._playback_position = 0.0
            self._audio_playing = False

    def _on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        if participant.identity == self._agent_identity:
            self._reason = "agent left the room"
            self._done.set()

    def _on_connection_state(self, state: rtc.ConnectionState) -> None:
        if state == rtc.ConnectionState.CONN_DISCONNECTED:
            self._reason = "room disconnected"
            self._done.set()

    def _on_disconnected(self, reason: rtc.DisconnectReason) -> None:
        self._reason = f"disconnected: {reason}"
        self._done.set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _aclose(self) -> None:
        self._done.set()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        with contextlib.suppress(Exception):
            await self._recv.aclose()
        with contextlib.suppress(Exception):
            await self._video_gen.aclose()
        with contextlib.suppress(Exception):
            await self._av_sync.aclose()
        with contextlib.suppress(Exception):
            await self._audio_source.aclose()
        with contextlib.suppress(Exception):
            await self._video_source.aclose()
        with contextlib.suppress(Exception):
            await self._room.disconnect()
        log.info("FlashHead runner closed (%s)", self._reason)

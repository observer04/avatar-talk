"""Self-hosted photoreal avatar: SoulX-FlashHead worker on a Modal GPU.

Contract with services/avatar_flashhead:
  POST {FLASHHEAD_LAUNCH_URL}/launch  {"room": ..., "livekit_url": ..., "token": ..., "persona_id": ...,
                                       "photo_key": ..., "agent_identity": ...}
The worker joins the room as AVATAR_IDENTITY, receives the agent's TTS audio
over a LiveKit DataStream and publishes synchronized audio + video.

A warm GPU worker joins in ~5-6 s, a cold one in 25-80 s. The agent waits
``flashhead_ready_timeout`` for it; if it is not there yet the session starts
voice-only and the agent's audio is handed to the avatar when it joins (between
replies, via ``replace_audio_tail``), so a cold start never leaves the user
waiting or stuck with a silent face.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from avatar_core import Persona
from livekit import api, rtc
from livekit.agents import AgentSession, utils
from livekit.agents.voice.avatar import DataStreamAudioOutput

from agent.settings import settings

log = logging.getLogger(__name__)

AVATAR_IDENTITY = "flashhead-avatar"
SAMPLE_RATE = 16000  # FlashHead's wav2vec audio encoder runs at 16 kHz


class FlashHeadBackend:
    name = "flashhead"
    wants_cues = False

    def __init__(self) -> None:
        self._late_task: asyncio.Task[None] | None = None

    async def attach(self, session: AgentSession, room: rtc.Room, persona: Persona) -> bool:
        if not settings.flashhead_launch_url:
            log.error("flashhead avatar requested but FLASHHEAD_LAUNCH_URL is not set")
            return False
        if not persona.avatar.photo_key:
            log.error("flashhead avatar requested but persona %s has no photo", persona.id)
            return False

        token = (
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(AVATAR_IDENTITY)
            .with_name(f"{persona.name} (avatar)")
            .with_kind("agent")
            .with_attributes({"lk.publish_on_behalf": room.local_participant.identity})
            .with_grants(api.VideoGrants(room_join=True, room=room.name))
            .to_jwt()
        )
        payload = {
            "room": room.name,
            "livekit_url": settings.livekit_url,
            "token": token,
            "persona_id": persona.id,
            "photo_key": persona.avatar.photo_key,
            "agent_identity": room.local_participant.identity,
        }
        headers = {"Authorization": f"Bearer {settings.internal_api_token}"} if settings.internal_api_token else {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
            async with http.post(f"{settings.flashhead_launch_url.rstrip('/')}/launch", json=payload,
                                 headers=headers) as r:
                if r.status >= 300:
                    log.error("flashhead launch failed: %s %s", r.status, await r.text())
                    return False

        try:
            async with asyncio.timeout(settings.flashhead_ready_timeout):
                await utils.wait_for_participant(room=room, identity=AVATAR_IDENTITY)
        except TimeoutError:
            log.warning(
                "flashhead avatar not ready after %.0fs (cold GPU); starting voice-only, will switch when it joins",
                settings.flashhead_ready_timeout,
            )
            self._late_task = asyncio.create_task(self._switch_when_ready(session, room), name="flashhead-late-join")
            return False

        session.output.audio = _avatar_output(room)
        return True

    async def _switch_when_ready(self, session: AgentSession, room: rtc.Room) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(settings.flashhead_late_join_timeout):
                await utils.wait_for_participant(room=room, identity=AVATAR_IDENTITY)
        except TimeoutError:
            log.error("flashhead avatar never joined (%.0fs); staying voice-only", settings.flashhead_late_join_timeout)
            return
        # Swap at a pause: before the session has started there is no chain to swap into,
        # and swapping mid-reply would cut the sentence off.
        while session.agent_state in ("initializing", "speaking"):  # noqa: ASYNC110 (a 0.2 s poll is simpler than tracking state events)
            await asyncio.sleep(0.2)
        session.output.replace_audio_tail(_avatar_output(room))
        log.info("flashhead avatar joined late; audio switched to the avatar",
                 extra={"fields": {"late_join_s": round(loop.time() - started, 1)}})

    async def aclose(self) -> None:
        if self._late_task and not self._late_task.done():
            self._late_task.cancel()


def _avatar_output(room: rtc.Room) -> DataStreamAudioOutput:
    return DataStreamAudioOutput(
        room, destination_identity=AVATAR_IDENTITY, sample_rate=SAMPLE_RATE, wait_playback_start=True
    )


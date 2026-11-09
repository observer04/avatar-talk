"""Self-hosted photoreal avatar: SoulX-FlashHead worker on a Modal GPU.

Contract with services/avatar_flashhead:
  POST {FLASHHEAD_LAUNCH_URL}/launch  {"room": ..., "livekit_url": ..., "token": ..., "persona_id": ...,
                                       "photo_key": ..., "agent_identity": ...}
The worker joins the room as AVATAR_IDENTITY, receives the agent's TTS audio
over a LiveKit DataStream and publishes synchronized audio + video.
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

        session.output.audio = DataStreamAudioOutput(
            room, destination_identity=AVATAR_IDENTITY, sample_rate=SAMPLE_RATE, wait_playback_start=True
        )
        async with asyncio.timeout(settings.avatar_join_timeout):
            await utils.wait_for_participant(room=room, identity=AVATAR_IDENTITY)
        return True

    async def aclose(self) -> None:
        pass


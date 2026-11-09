"""Managed photoreal avatar via Simli.

The persona's photo is turned into a Simli face (face_id) by the API service;
here we only start the Simli avatar participant for this room.
"""

from __future__ import annotations

import logging

from avatar_core import Persona
from livekit import rtc
from livekit.agents import AgentSession

from agent.settings import settings

log = logging.getLogger(__name__)


class SimliBackend:
    name = "simli"
    wants_cues = False

    def __init__(self) -> None:
        self._avatar = None

    async def attach(self, session: AgentSession, room: rtc.Room, persona: Persona) -> bool:
        from livekit.plugins import simli

        face_id = persona.avatar.simli_face_id
        if not settings.simli_api_key or not face_id:
            log.error("simli avatar requested but %s missing", "SIMLI_API_KEY" if face_id else "face_id")
            return False

        self._avatar = simli.AvatarSession(
            simli_config=simli.SimliConfig(
                api_key=settings.simli_api_key,
                face_id=face_id,
                max_session_length=persona.max_session_minutes * 60,
                max_idle_time=120,
            ),
        )
        await self._avatar.start(session, room=room)
        # The plugin logs and returns on API errors, so confirm the avatar really joined.
        await self._avatar.wait_for_join(timeout=settings.avatar_join_timeout)
        return True

    async def aclose(self) -> None:
        if self._avatar is not None:
            await self._avatar.aclose()

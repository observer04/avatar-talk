"""Avatar backends: how a persona gets a face.

Every backend implements ``AvatarBackendImpl``. ``attach()`` runs *before*
``AgentSession.start`` so a video avatar can take over the agent's audio output
(the avatar worker publishes synchronized audio + video). If a video avatar
fails to join in time, the session falls back to voice-only so the user is
never left in silence.
"""

from __future__ import annotations

import logging
from typing import Protocol

from avatar_core import AvatarBackend, Persona
from livekit import rtc
from livekit.agents import AgentSession

log = logging.getLogger(__name__)


class AvatarBackendImpl(Protocol):
    name: str
    #: Whether the LLM should write [mood:..]/[gesture:..] cues for this backend.
    wants_cues: bool

    async def attach(self, session: AgentSession, room: rtc.Room, persona: Persona) -> bool:
        """Start the avatar. Return True if a video avatar is now carrying the audio."""
        ...

    async def aclose(self) -> None: ...


def get_backend(persona: Persona) -> AvatarBackendImpl:
    backend = persona.avatar.backend
    if backend == AvatarBackend.SIMLI:
        from agent.avatars.simli import SimliBackend

        return SimliBackend()
    if backend == AvatarBackend.FLASHHEAD:
        from agent.avatars.flashhead import FlashHeadBackend

        return FlashHeadBackend()
    if backend == AvatarBackend.BROWSER3D:
        from agent.avatars.browser3d import Browser3DBackend

        return Browser3DBackend()
    from agent.avatars.browser3d import NoAvatarBackend

    return NoAvatarBackend()


async def attach_with_fallback(
    backend: AvatarBackendImpl, session: AgentSession, room: rtc.Room, persona: Persona
) -> bool:
    """Attach the avatar; on any failure restore normal room audio (voice-only)."""
    try:
        active = await backend.attach(session, room, persona)
    except Exception:
        log.exception("avatar backend %s failed; continuing voice-only", backend.name)
        active = False
    if not active and backend.name in ("simli", "flashhead"):
        session.output.audio = None  # let RoomIO publish the agent's own audio track
    return active

"""Browser-rendered 3D avatar (TalkingHead + HeadAudio in the web app).

No server video: the browser draws the face from the agent's audio track and
applies body-language cues the agent publishes on AVATAR_CUE_TOPIC.
"""

from __future__ import annotations

from avatar_core import Persona
from livekit import rtc
from livekit.agents import AgentSession


class Browser3DBackend:
    name = "browser3d"
    wants_cues = True

    async def attach(self, session: AgentSession, room: rtc.Room, persona: Persona) -> bool:
        return False  # audio stays on the agent's own track

    async def aclose(self) -> None:
        pass


class NoAvatarBackend(Browser3DBackend):
    name = "none"
    wants_cues = False

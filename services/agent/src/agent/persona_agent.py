from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterable

from avatar_core import AVATAR_CUE_TOPIC, Persona
from livekit.agents import Agent, ModelSettings

from agent.cues import CUE_PROMPT, Cue, strip_cues
from agent.settings import settings

log = logging.getLogger(__name__)


def build_instructions(persona: Persona, *, with_cues: bool) -> str:
    base = persona.system_prompt.strip() or settings.default_prompt_file.read_text(encoding="utf-8")
    base = base.replace("{name}", persona.name)
    return f"{base}\n\n{CUE_PROMPT}" if with_cues else base


class PersonaAgent(Agent):
    """The digital human. Speech and transcripts never contain body-language cues;
    when the avatar renders in the browser, cues are published to it instead."""

    def __init__(self, persona: Persona, *, tools: list, publish_cues: bool) -> None:
        super().__init__(instructions=build_instructions(persona, with_cues=publish_cues), tools=tools)
        self._persona = persona
        self._publish_cues = publish_cues
        self._pending: set[asyncio.Task] = set()

    async def on_enter(self) -> None:
        if self._persona.greeting:
            self.session.generate_reply(instructions=self._persona.greeting)

    def _send_cue(self, cue: Cue) -> None:
        if not self._publish_cues:
            return
        kind, value = cue
        room = self.session.room_io.room if self.session.room_io else None
        if room is None:
            return
        task = asyncio.create_task(
            room.local_participant.send_text(json.dumps({kind: value}), topic=AVATAR_CUE_TOPIC)
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        return Agent.default.tts_node(self, strip_cues(text, self._send_cue), model_settings)

    async def transcription_node(self, text: AsyncIterable, model_settings: ModelSettings):
        return Agent.default.transcription_node(self, strip_cues(text), model_settings)

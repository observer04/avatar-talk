"""Per-session observability: per-turn latency breakdown, usage and cost.

LiveKit attaches a latency breakdown to every ChatMessage (``item.metrics``):
user turns carry transcription/end-of-turn delays, agent turns carry LLM TTFT,
time-to-first-sentence, TTS TTFB, playback latency and the end-to-end latency
(user stopped speaking -> agent started). Usage arrives via
``session_usage_updated``. We log every turn with correlation IDs, record OTel
histograms, and at session end compute an approximate cost and report it to
the API.
"""

from __future__ import annotations

import logging
import time

import aiohttp
from avatar_core import Persona, SessionMetadata
from avatar_core.costs import SessionUsage
from avatar_core.telemetry import meter
from livekit.agents import AgentSession, ConversationItemAddedEvent, SessionUsageUpdatedEvent, llm, metrics

from agent.providers import model_key
from agent.settings import settings

log = logging.getLogger(__name__)

_m = meter("avatar_agent")
HISTOGRAMS = {
    name: _m.create_histogram(f"agent.{name}", unit="s")
    for name in (
        "e2e_latency",
        "end_of_turn_delay",
        "transcription_delay",
        "llm_node_ttft",
        "llm_node_ttfs",
        "tts_node_ttfb",
        "playback_latency",
    )
}
SESSIONS = _m.create_counter("agent.sessions", description="Sessions started")
SESSION_COST = _m.create_histogram("agent.session_cost", unit="USD", description="Approximate cost per session")

USER_KEYS = ("transcription_delay", "end_of_turn_delay", "on_user_turn_completed_delay")
AGENT_KEYS = ("llm_node_ttft", "llm_node_ttfs", "tts_node_ttfb", "playback_latency", "e2e_latency")


class SessionObserver:
    def __init__(self, session: AgentSession, persona: Persona, meta: SessionMetadata, avatar_backend: str):
        self.persona = persona
        self.meta = meta
        self.avatar_backend = avatar_backend
        self.usage = SessionUsage()
        self.started = time.monotonic()
        self.turns: list[dict] = []
        self._pending_user: dict = {}
        self._attrs = {"persona_id": persona.id, "avatar": avatar_backend, "llm": persona.llm.model}
        SESSIONS.add(1, self._attrs)
        session.on("conversation_item_added", self._on_item)
        session.on("session_usage_updated", self._on_usage)

    def _on_item(self, ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, llm.ChatMessage) or not item.metrics:
            return
        m = item.metrics
        if item.role == "user":
            self._pending_user = {k: round(m[k], 3) for k in USER_KEYS if k in m}
            self._pending_user["user_text"] = (item.text_content or "")[:120]
            return
        if item.role != "assistant":
            return
        turn = {**self._pending_user, **{k: round(m[k], 3) for k in AGENT_KEYS if k in m}}
        turn["interrupted"] = bool(getattr(item, "interrupted", False))
        self._pending_user = {}
        self.turns.append(turn)
        for k, v in turn.items():
            if k in HISTOGRAMS and isinstance(v, float):
                HISTOGRAMS[k].record(v, self._attrs)
        log.info("turn", extra={"fields": {"turn": len(self.turns), **turn}})

    def _on_usage(self, ev: SessionUsageUpdatedEvent) -> None:
        u = SessionUsage()
        for mu in ev.usage.model_usage:
            if isinstance(mu, metrics.LLMModelUsage):
                u.llm_input_tokens += mu.input_tokens
                u.llm_output_tokens += mu.output_tokens
            elif isinstance(mu, metrics.TTSModelUsage):
                u.tts_characters += mu.characters_count
            elif isinstance(mu, metrics.STTModelUsage):
                u.stt_audio_seconds += mu.audio_duration
        self.usage = u

    def summary(self) -> dict:
        self.usage.session_seconds = time.monotonic() - self.started
        p = self.persona
        cost = self.usage.cost(
            stt=model_key(p.stt.provider, p.stt.model),
            tts=model_key(p.voice.provider, p.voice.model),
            llm=model_key(p.llm.provider, p.llm.model),
            avatar=self.avatar_backend,
        )
        SESSION_COST.record(cost, self._attrs)
        e2e = [t["e2e_latency"] for t in self.turns if "e2e_latency" in t]
        return {
            "session_id": self.meta.session_id,
            "persona_id": p.id,
            "avatar_backend": self.avatar_backend,
            "duration_s": round(self.usage.session_seconds, 1),
            "turn_count": len(self.turns),
            "e2e_latency_avg_s": round(sum(e2e) / len(e2e), 3) if e2e else None,
            "turns": self.turns,
            "usage": {
                "stt_audio_s": round(self.usage.stt_audio_seconds, 1),
                "tts_chars": self.usage.tts_characters,
                "llm_in_tokens": self.usage.llm_input_tokens,
                "llm_out_tokens": self.usage.llm_output_tokens,
            },
            "cost_usd": cost,
            "cost_breakdown": {k: round(v, 5) for k, v in self.usage.breakdown.items()},
        }

    async def report(self) -> dict:
        data = self.summary()
        log.info("session summary", extra={"fields": data})
        if settings.api_url:
            headers = {"Authorization": f"Bearer {settings.internal_api_token}"} if settings.internal_api_token else {}
            url = f"{settings.api_url.rstrip('/')}/internal/sessions/{self.meta.session_id}/report"
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:
                    async with http.post(url, json=data, headers=headers) as r:
                        if r.status >= 300:
                            log.warning("session report rejected: HTTP %s", r.status)
            except aiohttp.ClientError as e:
                log.warning("could not report session summary: %s", e)
        return data

"""Approximate per-unit list prices (USD) for cost accounting per session.

These are estimates for dashboards and spend caps, not billing. Override any
entry via the COST_OVERRIDES env var in a later iteration; keep them updated
when a provider changes pricing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# STT priced per audio minute.
STT_PER_MIN = {
    "deepgram/flux-general-en": 0.0077,
    "deepgram/nova-3": 0.0077,
}
# TTS priced per 1k characters.
TTS_PER_1K_CHARS = {
    "cartesia/sonic-3": 0.04,
    "cartesia/sonic-turbo": 0.04,
}
# LLM priced per 1M tokens: (input, output).
LLM_PER_1M = {
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "openai/gpt-5.4-mini": (0.25, 2.00),
    "groq/openai/gpt-oss-120b": (0.15, 0.60),
    "groq/openai/gpt-oss-20b": (0.075, 0.30),
}
# Avatar priced per minute of session.
AVATAR_PER_MIN = {
    "simli": 0.05,
    "flashhead": 0.033,  # L40S at ~$1.95/h, one stream per GPU; updated after the GPU spike
    "browser3d": 0.0,
    "none": 0.0,
}


@dataclass
class SessionUsage:
    stt_audio_seconds: float = 0.0
    tts_characters: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    session_seconds: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)

    def cost(self, *, stt: str, tts: str, llm: str, avatar: str) -> float:
        llm_in, llm_out = LLM_PER_1M.get(llm, (0.0, 0.0))
        self.breakdown = {
            "stt": STT_PER_MIN.get(stt, 0.0) * self.stt_audio_seconds / 60,
            "tts": TTS_PER_1K_CHARS.get(tts, 0.0) * self.tts_characters / 1000,
            "llm": (llm_in * self.llm_input_tokens + llm_out * self.llm_output_tokens) / 1e6,
            "avatar": AVATAR_PER_MIN.get(avatar, 0.0) * self.session_seconds / 60,
        }
        return round(sum(self.breakdown.values()), 5)

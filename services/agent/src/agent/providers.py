"""Turn a Persona's model choices into LiveKit plugin instances.

Every provider decision lives here, so adding a provider means adding one
branch, and personas switch providers through data, not code.
"""

from __future__ import annotations

import logging

from avatar_core import LLMConfig, STTConfig, VoiceConfig
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts

from agent.settings import settings

log = logging.getLogger(__name__)

# Models that think before answering; keep that to a minimum for voice latency.
_REASONING_PREFIXES = ("gpt-5", "o3", "o4")
# Spoken replies are short; a cap also bounds cost and runaway generations.
MAX_COMPLETION_TOKENS = 600


def make_stt(cfg: STTConfig) -> lk_stt.STT:
    if cfg.provider == "deepgram":
        from livekit.plugins import deepgram

        if cfg.model.startswith("flux"):
            # Flux: conversational STT with built-in semantic end-of-turn detection.
            return deepgram.STTv2(model=cfg.model, eager_eot_threshold=0.4, api_key=settings.deepgram_api_key)
        return deepgram.STT(model=cfg.model, language=cfg.language, api_key=settings.deepgram_api_key)
    if cfg.provider == "openai":
        from livekit.plugins import openai

        return openai.STT(model=cfg.model, language=cfg.language)
    raise ValueError(f"Unknown STT provider {cfg.provider!r}")


def stt_handles_turns(cfg: STTConfig) -> bool:
    return cfg.provider == "deepgram" and cfg.model.startswith("flux")


def _one_llm(provider: str, model: str, temperature: float) -> lk_llm.LLM:
    if provider == "openai":
        from livekit.plugins import openai

        if model.startswith(_REASONING_PREFIXES):
            return openai.LLM(model=model, reasoning_effort="minimal", max_completion_tokens=MAX_COMPLETION_TOKENS)
        return openai.LLM(model=model, temperature=temperature, max_completion_tokens=MAX_COMPLETION_TOKENS)
    if provider == "groq":
        from livekit.plugins import groq

        return groq.LLM(model=model, temperature=temperature, max_completion_tokens=MAX_COMPLETION_TOKENS)
    if provider == "openrouter":
        from livekit.plugins import openai

        # OpenAI-compatible endpoint (with_openrouter() doesn't expose max_completion_tokens).
        return openai.LLM(
            model=model,
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
            temperature=temperature,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )
    raise ValueError(f"Unknown LLM provider {provider!r}")


def parse_model_ref(ref: str) -> tuple[str, str]:
    """'groq/openai/gpt-oss-120b' -> ('groq', 'openai/gpt-oss-120b')."""
    provider, _, model = ref.partition("/")
    if not model:
        raise ValueError(f"Model ref {ref!r} must look like provider/model")
    return provider, model


def make_llm(cfg: LLMConfig) -> lk_llm.LLM:
    primary = _one_llm(cfg.provider, cfg.model, cfg.temperature)
    fallbacks: list[lk_llm.LLM] = []
    for ref in cfg.fallbacks:
        try:
            fallbacks.append(_one_llm(*parse_model_ref(ref), cfg.temperature))
        except Exception as e:  # a misconfigured fallback must not take down the session
            log.warning("skipping LLM fallback %s: %s", ref, e)
    if not fallbacks:
        return primary
    return lk_llm.FallbackAdapter([primary, *fallbacks])


def _one_tts(
    provider: str,
    model: str,
    *,
    voice_id: str | None = None,
    speed: float | None = None,
    language: str = "en",
) -> lk_tts.TTS:
    if provider == "cartesia":
        from livekit.plugins import cartesia

        kwargs = {"model": model, "language": language, "api_key": settings.cartesia_api_key}
        if voice_id:
            kwargs["voice"] = voice_id
        if speed is not None:
            kwargs["speed"] = speed
        return cartesia.TTS(**kwargs)
    if provider == "deepgram":
        from livekit.plugins import deepgram

        # Aura-2: the voice is the model name (aura-2-andromeda-en, aura-2-thalia-en, ...).
        return deepgram.TTS(model=model, api_key=settings.deepgram_api_key)
    if provider == "openai":
        from livekit.plugins import openai

        return openai.TTS(model=model, voice=voice_id or "alloy")
    raise ValueError(f"Unknown TTS provider {provider!r}")


def tts_fallback_refs(value: str) -> list[str]:
    """'deepgram/a, cartesia/b' -> ['deepgram/a', 'cartesia/b']; empty disables the chain."""
    return [ref.strip() for ref in value.split(",") if ref.strip()]


def make_tts(cfg: VoiceConfig) -> lk_tts.TTS:
    primary = _one_tts(
        cfg.provider, cfg.model, voice_id=cfg.voice_id, speed=cfg.speed, language=cfg.language
    )
    fallbacks: list[lk_tts.TTS] = []
    # The global chain is a safety net (e.g. Cartesia out of credits): it applies to every
    # persona, while the persona's own voice stays primary.
    for ref in tts_fallback_refs(settings.tts_fallbacks):
        try:
            fallbacks.append(_one_tts(*parse_model_ref(ref)))
        except Exception as e:  # a misconfigured fallback must not take down the session
            log.warning("skipping TTS fallback %s: %s", ref, e)
    if not fallbacks:
        return primary
    return lk_tts.FallbackAdapter([primary, *fallbacks])


def model_key(provider: str, model: str) -> str:
    """Key used for cost lookup in avatar_core.costs."""
    return f"{provider}/{model}"

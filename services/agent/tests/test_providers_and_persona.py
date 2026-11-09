import pytest
from agent.avatars import get_backend
from agent.persona_agent import build_instructions
from agent.persona_source import parse_metadata, resolve_persona
from agent.providers import make_tts, parse_model_ref, stt_handles_turns, tts_fallback_refs
from agent.settings import AgentSettings, settings
from avatar_core import AvatarBackend, AvatarConfig, Persona, SessionMetadata, VoiceConfig
from livekit.agents.tts import TTS, FallbackAdapter
from livekit.plugins import deepgram


def test_parse_model_ref():
    assert parse_model_ref("groq/openai/gpt-oss-120b") == ("groq", "openai/gpt-oss-120b")
    with pytest.raises(ValueError):
        parse_model_ref("gpt-4.1-mini")


def test_flux_handles_turns():
    assert stt_handles_turns(Persona().stt)


async def test_inline_persona_wins_and_bad_metadata_is_safe():
    p = Persona(name="Zed")
    meta = parse_metadata(SessionMetadata(persona=p).model_dump_json())
    assert (await resolve_persona(meta)).name == "Zed"
    assert parse_metadata("not json").persona is None


def test_backend_selection_and_cue_prompt():
    browser = Persona(avatar=AvatarConfig(backend=AvatarBackend.BROWSER3D))
    simli = Persona(avatar=AvatarConfig(backend=AvatarBackend.SIMLI))
    assert get_backend(browser).wants_cues and not get_backend(simli).wants_cues
    assert "[mood:happy]" in build_instructions(browser, with_cues=True)
    assert "[mood:" not in build_instructions(simli, with_cues=False)
    assert "Ava" in build_instructions(Persona(), with_cues=False)


def test_tts_fallback_refs_parsing():
    assert tts_fallback_refs("deepgram/aura-2-andromeda-en") == ["deepgram/aura-2-andromeda-en"]
    assert tts_fallback_refs(" deepgram/a , cartesia/b ,, ") == ["deepgram/a", "cartesia/b"]
    assert tts_fallback_refs("") == []


def test_tts_fallbacks_default_is_deepgram_aura2():
    assert AgentSettings.model_fields["tts_fallbacks"].default == "deepgram/aura-2-andromeda-en"


def _keys(monkeypatch):
    # Plugin constructors only need a non-empty key; no network happens at construction.
    monkeypatch.setattr(settings, "cartesia_api_key", "test-key")
    monkeypatch.setattr(settings, "deepgram_api_key", "test-key")


def test_make_tts_deepgram_branch(monkeypatch):
    _keys(monkeypatch)
    monkeypatch.setattr(settings, "tts_fallbacks", "")
    tts = make_tts(VoiceConfig(provider="deepgram", model="aura-2-andromeda-en"))
    assert isinstance(tts, deepgram.TTS)
    assert not isinstance(tts, FallbackAdapter)
    assert tts.model == "aura-2-andromeda-en"


def test_make_tts_wraps_primary_and_fallbacks(monkeypatch):
    _keys(monkeypatch)
    monkeypatch.setattr(settings, "tts_fallbacks", "deepgram/aura-2-andromeda-en")
    tts = make_tts(VoiceConfig(provider="cartesia", model="sonic-3", voice_id="voice-1"))
    assert isinstance(tts, FallbackAdapter)
    assert len(tts._tts_instances) == 2
    assert isinstance(tts._tts_instances[0], TTS) and not isinstance(tts._tts_instances[0], FallbackAdapter)
    assert isinstance(tts._tts_instances[1], deepgram.TTS)


def test_make_tts_without_fallbacks_is_bare_primary(monkeypatch):
    _keys(monkeypatch)
    monkeypatch.setattr(settings, "tts_fallbacks", "")
    tts = make_tts(VoiceConfig(provider="cartesia", model="sonic-3"))
    assert not isinstance(tts, FallbackAdapter)


def test_make_tts_skips_invalid_fallbacks(monkeypatch):
    _keys(monkeypatch)
    monkeypatch.setattr(settings, "tts_fallbacks", "bogus/model,deepgram/aura-2-andromeda-en")
    tts = make_tts(VoiceConfig(provider="cartesia", model="sonic-3"))
    assert isinstance(tts, FallbackAdapter)
    assert len(tts._tts_instances) == 2  # invalid ref skipped, valid one kept

    monkeypatch.setattr(settings, "tts_fallbacks", "bogus/model")
    bare = make_tts(VoiceConfig(provider="cartesia", model="sonic-3"))
    assert not isinstance(bare, FallbackAdapter)

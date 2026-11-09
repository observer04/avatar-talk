import pytest
from agent.avatars import get_backend
from agent.persona_agent import build_instructions
from agent.persona_source import parse_metadata, resolve_persona
from agent.providers import parse_model_ref, stt_handles_turns
from avatar_core import AvatarBackend, AvatarConfig, Persona, SessionMetadata


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

import logging

from avatar_core import Persona, SessionMetadata
from avatar_core.costs import SessionUsage
from avatar_core.logging import bind, setup_logging
from avatar_core.settings import ServiceSettings
from avatar_core.telemetry import langfuse_otlp


def test_metadata_roundtrip():
    meta = SessionMetadata(persona=Persona(name="Ava"))
    assert SessionMetadata.model_validate_json(meta.model_dump_json()).persona.name == "Ava"


def test_cost_breakdown():
    u = SessionUsage(stt_audio_seconds=60, tts_characters=1000, llm_input_tokens=1_000_000, session_seconds=60)
    c = u.cost(stt="deepgram/flux-general-en", tts="cartesia/sonic-3", llm="groq/openai/gpt-oss-120b",
               avatar="simli")
    assert c == round(0.0077 + 0.04 + 0.15 + 0.05, 5)


def test_langfuse_endpoint_only_with_keys():
    s = ServiceSettings(_env_file=None, langfuse_host="https://lf.example", langfuse_public_key="pk",
                        langfuse_secret_key="sk")
    endpoint, headers = langfuse_otlp(s)
    assert endpoint == "https://lf.example/api/public/otel/v1/traces" and headers["Authorization"].startswith("Basic ")
    assert langfuse_otlp(ServiceSettings(_env_file=None, langfuse_host=None)) is None


def test_log_context_survives_foreign_extras(caplog):
    setup_logging("test-svc", install_handler=False)
    bind(session_id="ses_1", room="r1")
    with caplog.at_level(logging.INFO):
        # frameworks (LiveKit) pass their own extras, e.g. "room": must not collide
        logging.getLogger("x").info("hello", extra={"room": "other"})
    rec = caplog.records[-1]
    assert rec.ctx["session_id"] == "ses_1" and rec.room == "other" and rec.service == "test-svc"

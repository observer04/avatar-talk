from __future__ import annotations

from pathlib import Path

from avatar_core.settings import ServiceSettings

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class AgentSettings(ServiceSettings):
    service_name: str = "avatar-agent"

    # Provider keys (read from env / Modal secrets).
    deepgram_api_key: str | None = None
    cartesia_api_key: str | None = None
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None
    simli_api_key: str | None = None
    tavily_api_key: str | None = None

    # TTS fallbacks tried in order when the persona's voice fails ("provider/model", comma-
    # separated). Default: Deepgram Aura-2, since the Cartesia account can run out of credits.
    tts_fallbacks: str = "deepgram/aura-2-andromeda-en"

    # Where personas come from when a dispatch only carries persona_id.
    persona_from_api: bool = True
    default_prompt_file: Path = PROMPTS_DIR / "default_persona.md"

    # FlashHead avatar worker launch endpoint (Modal web endpoint).
    flashhead_launch_url: str | None = None

    # Seconds to wait for a video avatar to join before falling back to voice-only.
    avatar_join_timeout: float = 45.0  # Simli: wait for the avatar before starting the session
    # FlashHead: wait this long for a warm worker before starting voice-only, then keep
    # waiting up to flashhead_late_join_timeout for a cold one and switch to it when it joins.
    flashhead_ready_timeout: float = 8.0
    flashhead_late_join_timeout: float = 180.0
    # Barge-in: "adaptive" (ML, ignores backchannels like "mm-hmm") or "vad" (faster, cruder).
    # Benchmarked: vad stopped the agent ~0.7 s sooner than adaptive (docs/benchmarks.md).
    interruption_mode: str = "vad"
    interruption_min_duration: float = 0.4

    # Prometheus metrics port of the worker (None = off).
    prometheus_port: int | None = None


settings = AgentSettings()

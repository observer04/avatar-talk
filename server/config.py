"""Runtime configuration, read from environment variables / .env.

Swapping a provider (e.g. Groq -> Gemini) should only ever need an .env change.
"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT.parent / ".env", ROOT / ".env"),
        extra="ignore",
    )

    # --- Providers -------------------------------------------------------
    llm_provider: Literal["groq", "google"] = "groq"
    # moonshine = local open-source ASR on CPU (default; ~0.6-0.8 s per utterance here, no rate limits)
    # groq      = Whisper large-v3-turbo on Groq's free tier (~1.2 s incl. upload, ~20 req/min cap)
    stt_provider: Literal["moonshine", "groq"] = "moonshine"
    # kokoro = local open-source TTS on CPU (default, free).
    # groq   = Orpheus (open weights) hosted on Groq's free tier; accept the model terms
    #          once at https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
    tts_provider: Literal["kokoro", "groq"] = "kokoro"

    # How the avatar is rendered. "client3d" = browser TalkingHead (no server video).
    # "simli" / "selfhosted" are reserved for the later photoreal renderers.
    avatar_mode: Literal["client3d", "simli", "selfhosted"] = "client3d"

    # --- Credentials -----------------------------------------------------
    groq_api_key: str | None = None
    google_api_key: str | None = None
    simli_api_key: str | None = None
    simli_face_id: str | None = None

    # --- Model choices ---------------------------------------------------
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_reasoning_effort: Literal["low", "medium", "high"] | None = "low"
    google_llm_model: str = "gemini-flash-lite-latest"
    groq_stt_model: str = "whisper-large-v3-turbo"
    # small-streaming is ~2x faster but dropped words in testing; medium was accurate.
    moonshine_model: Literal["tiny-streaming", "small-streaming", "medium-streaming"] = "medium-streaming"
    kokoro_voice: str = "af_heart"
    kokoro_speed: float = 1.0
    # int8 is ~2.5x faster on CPU (RTF ~0.4 vs ~1.0 on a Ryzen 5 7530U) with near-identical quality.
    kokoro_precision: Literal["int8", "fp32"] = "int8"
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "autumn"

    # --- Agent -----------------------------------------------------------
    persona_file: Path = ROOT / "persona" / "system_prompt.md"
    # Tool modules under server/tools/ to load (comma-separated).
    enabled_tools: str = "basics"

    # --- Server ----------------------------------------------------------
    host: str = "localhost"
    port: int = 7860
    # Empty = host candidates only (fine for localhost dev, and avoids a ~5 s STUN
    # gathering stall). Set e.g. stun:stun.l.google.com:19302 when deployed remotely.
    stun_url: str = ""
    # Optional TURN relay for strict NATs (e.g. free Open Relay: https://www.metered.ca/tools/openrelay/).
    turn_url: str = ""
    turn_username: str = ""
    turn_credential: str = ""
    # Browser gathers all ICE candidates before sending its offer (no trickle PATCHes).
    # Needed when the session runs in a different container than the signalling endpoint (Modal).
    wait_for_ice_gathering: bool = False
    # If set, clients must open the page with ?key=<token>. Use it for any public deployment.
    access_token: str = ""

    @property
    def enabled_tool_modules(self) -> list[str]:
        return [m.strip() for m in self.enabled_tools.split(",") if m.strip()]

    def ice_servers(self) -> list[dict]:
        servers: list[dict] = []
        if self.stun_url:
            servers.append({"urls": self.stun_url})
        if self.turn_url:
            servers.append(
                {"urls": self.turn_url, "username": self.turn_username, "credential": self.turn_credential}
            )
        return servers

    def client_rtc_config(self) -> dict:
        """What the browser needs to connect (served at GET /api/rtc-config)."""
        return {
            "iceServers": self.ice_servers() or [{"urls": "stun:stun.l.google.com:19302"}],
            "waitForIceGathering": self.wait_for_ice_gathering,
        }

    def check_access(self, offer: dict) -> bool:
        if not self.access_token:
            return True
        data = offer.get("request_data") or offer.get("requestData") or {}
        return isinstance(data, dict) and data.get("access_token") == self.access_token


settings = Settings()

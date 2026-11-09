"""Settings shared by all services. Each service subclasses ServiceSettings.

Values come from environment variables (Modal secrets in production) or a .env
file at the repo root in development.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    """The workspace root in a source checkout; the working directory when deployed
    (containers ship the packages without the repo layout)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "packages").is_dir():
            return parent
    return Path.cwd()


REPO_ROOT = _find_repo_root()


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env",), extra="ignore")

    service_name: str = "avatar-talk"
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"

    # --- Observability ---------------------------------------------------
    # Langfuse receives traces over OTLP when both keys are set.
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    # Any extra OTLP/HTTP backend (Grafana Tempo, Jaeger, Honeycomb...).
    otel_exporter_otlp_endpoint: str | None = None
    otel_exporter_otlp_headers: str | None = None  # "k1=v1,k2=v2"
    # Capture prompts/transcripts in traces (PII). Keep False in prod unless needed.
    trace_content: bool = True
    sentry_dsn: str | None = None

    # --- LiveKit ---------------------------------------------------------
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None

    # --- Internal service-to-service auth --------------------------------
    api_url: str = "http://localhost:8000"
    internal_api_token: str | None = None

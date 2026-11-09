from __future__ import annotations

from pathlib import Path
from typing import Literal

from avatar_core.settings import REPO_ROOT, ServiceSettings


class ApiSettings(ServiceSettings):
    service_name: str = "avatar-api"

    # Postgres in production (postgresql+asyncpg://...), SQLite file in dev.
    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / '.data' / 'api.db'}"

    # Persona photos.
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_dir: Path = REPO_ROOT / ".data" / "storage"
    s3_bucket: str | None = None
    s3_region: str | None = None

    # Comma-separated client API keys for /v1. Empty = open (dev only).
    api_keys: str = ""
    cors_origins: str = "http://localhost:5173"

    simli_api_key: str | None = None
    simli_api_url: str = "https://api.simli.ai"

    # Guardrails.
    daily_spend_cap_usd: float = 20.0
    session_token_ttl_minutes: int = 15
    rate_limit_per_minute: int = 60
    max_photo_bytes: int = 10 * 1024 * 1024

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = ApiSettings()

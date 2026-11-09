"""Auth and rate limiting.

- Client routes (/v1): Bearer API key from API_KEYS (open when unset — dev only).
- Internal routes (/internal): Bearer INTERNAL_API_TOKEN (agent -> API).
- A small in-memory token bucket per key/IP; replace with Redis when running
  several API replicas.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Depends, HTTPException, Request, status

from api.settings import settings


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


def key_hash(key: str | None) -> str | None:
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key else None


class _Bucket:
    def __init__(self) -> None:
        self.hits: dict[str, list[float]] = {}

    def allow(self, who: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        recent = [t for t in self.hits.get(who, []) if now - t < window]
        if len(recent) >= limit:
            self.hits[who] = recent
            return False
        recent.append(now)
        self.hits[who] = recent
        return True


_bucket = _Bucket()


async def client_auth(request: Request) -> str | None:
    """Returns the caller's API key (None in open dev mode)."""
    key = _bearer(request)
    keys = settings.api_key_set
    if keys and not any(hmac.compare_digest(key or "", k) for k in keys):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")
    who = key_hash(key) or (request.client.host if request.client else "anon")
    if not _bucket.allow(who, settings.rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
    return key


async def internal_auth(request: Request) -> None:
    expected = settings.internal_api_token
    if not expected:
        if settings.environment == "prod":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "internal API not configured")
        return
    if not hmac.compare_digest(_bearer(request) or "", expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid internal token")


ClientKey = Depends(client_auth)
Internal = Depends(internal_auth)

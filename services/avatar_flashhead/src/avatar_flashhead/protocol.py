"""Launch contract between the agent and the FlashHead worker.

The agent (``services/agent/src/agent/avatars/flashhead.py``) POSTs::

    {"room": ..., "livekit_url": ..., "token": ..., "persona_id": ...,
     "photo_key": ..., "agent_identity": ...}

Optionally with ``Authorization: Bearer <INTERNAL_API_TOKEN>``.  This module is
stdlib-only so the Modal web endpoint can validate a request without importing
torch / livekit.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

DEFAULT_PHOTO_PATH = "/opt/flashhead/examples/girl.png"
MAX_PHOTO_BYTES = 10 * 1024 * 1024


class LaunchError(ValueError):
    """The launch payload is malformed."""


@dataclass(frozen=True)
class LaunchPayload:
    room: str
    livekit_url: str
    token: str
    agent_identity: str
    persona_id: str | None = None
    photo_key: str | None = None
    photo_url: str | None = None

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> LaunchPayload:
        if not isinstance(data, Mapping):
            raise LaunchError("launch payload must be a JSON object")

        def required(name: str) -> str:
            value = data.get(name)
            if not isinstance(value, str) or not value.strip():
                raise LaunchError(f"missing or empty {name!r}")
            return value.strip()

        def optional(name: str) -> str | None:
            value = data.get(name)
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise LaunchError(f"{name!r} must be a non-empty string when set")
            return value.strip()

        room = required("room")
        livekit_url = required("livekit_url")
        token = required("token")
        agent_identity = required("agent_identity")
        persona_id = optional("persona_id")
        photo_key = optional("photo_key")
        photo_url = optional("photo_url")

        if urlparse(livekit_url).scheme not in ("ws", "wss", "http", "https"):
            raise LaunchError("livekit_url must be a ws(s):// or http(s):// URL")
        if photo_url is not None and urlparse(photo_url).scheme not in ("http", "https"):
            raise LaunchError("photo_url must be an http(s):// URL")
        if photo_key is not None and persona_id is None:
            raise LaunchError("photo_key requires persona_id")

        return cls(
            room=room,
            livekit_url=livekit_url,
            token=token,
            agent_identity=agent_identity,
            persona_id=persona_id,
            photo_key=photo_key,
            photo_url=photo_url,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "room": self.room,
            "livekit_url": self.livekit_url,
            "token": self.token,
            "agent_identity": self.agent_identity,
            "persona_id": self.persona_id,
            "photo_key": self.photo_key,
            "photo_url": self.photo_url,
        }


def bearer_token(header: str | None) -> str | None:
    if not header or not header.lower().startswith("bearer "):
        return None
    return header[7:].strip()


def authorize(header: str | None, expected: str | None) -> bool:
    """Validate the launch bearer token.

    When ``expected`` is unset the endpoint is open (local dev only).  Production
    deployments set ``INTERNAL_API_TOKEN`` on both the agent and this worker.
    """
    if not expected:
        return True
    supplied = bearer_token(header)
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)

"""LiveKit: create a room, dispatch the agent with session metadata, mint the user's token."""

from __future__ import annotations

import uuid
from datetime import timedelta

from avatar_core import AGENT_NAME, SessionMetadata
from livekit import api

from api.settings import settings


def _require_livekit() -> tuple[str, str, str]:
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        raise RuntimeError("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET are not configured")
    return settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret


async def start_room(meta: SessionMetadata, user_identity: str) -> tuple[str, str]:
    """Returns (room_name, participant_token)."""
    url, key, secret = _require_livekit()
    room = f"room_{uuid.uuid4().hex[:12]}"
    async with api.LiveKitAPI(url, key, secret) as lk:
        await lk.room.create_room(
            api.CreateRoomRequest(name=room, empty_timeout=120, departure_timeout=20, max_participants=4)
        )
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room, metadata=meta.model_dump_json())
        )
    token = (
        api.AccessToken(key, secret)
        .with_identity(user_identity)
        .with_name("Guest")
        .with_ttl(timedelta(minutes=settings.session_token_ttl_minutes))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )
    return room, token

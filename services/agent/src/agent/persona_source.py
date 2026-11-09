"""Resolve which persona a session runs.

Order: persona inlined in the dispatch metadata (tests, previews) → persona_id
fetched from the API service → built-in default persona.
"""

from __future__ import annotations

import logging

import aiohttp
from avatar_core import Persona, SessionMetadata

from agent.settings import settings

log = logging.getLogger(__name__)


def parse_metadata(raw: str | None) -> SessionMetadata:
    if not raw:
        return SessionMetadata()
    try:
        return SessionMetadata.model_validate_json(raw)
    except ValueError:
        log.warning("invalid dispatch metadata; using defaults")
        return SessionMetadata()


async def resolve_persona(meta: SessionMetadata) -> Persona:
    if meta.persona is not None:
        return meta.persona
    if meta.persona_id and settings.persona_from_api:
        headers = {"Authorization": f"Bearer {settings.internal_api_token}"} if settings.internal_api_token else {}
        url = f"{settings.api_url.rstrip('/')}/internal/personas/{meta.persona_id}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:
                async with http.get(url, headers=headers) as r:
                    if r.status == 200:
                        return Persona.model_validate(await r.json())
                    log.error("persona %s lookup failed: HTTP %s", meta.persona_id, r.status)
        except aiohttp.ClientError as e:
            log.error("persona %s lookup failed: %s", meta.persona_id, e)
    return Persona()

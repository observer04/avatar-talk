"""Turning a single photo into an avatar.

1. normalise_photo(): validate, fix orientation, square-crop around the upper
   centre (where faces usually are), resize to 512x512 JPEG.
2. provision(): per backend. Simli builds a "Trinity" face asynchronously
   (queued, minutes); FlashHead uses the photo directly at session time.
3. refresh_status(): lazily polls Simli while a persona is provisioning, so no
   background worker is needed (works with scale-to-zero hosting).
"""

from __future__ import annotations

import io
import logging
import time

import httpx
from avatar_core import AvatarBackend, AvatarStatus, Persona
from PIL import Image, ImageOps

from api.settings import settings
from api.storage import storage

log = logging.getLogger(__name__)

PHOTO_SIZE = 512
_last_poll: dict[str, float] = {}
POLL_INTERVAL_S = 10.0


class PhotoError(ValueError):
    pass


def normalise_photo(data: bytes) -> bytes:
    if len(data) > settings.max_photo_bytes:
        raise PhotoError(f"photo larger than {settings.max_photo_bytes // (1024 * 1024)} MB")
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as e:
        raise PhotoError("not a readable image (use JPEG, PNG or WebP)") from e
    w, h = img.size
    if min(w, h) < 256:
        raise PhotoError("photo is too small; use at least 256x256 pixels")
    side = min(w, h)
    left = (w - side) // 2
    # Portraits: bias the crop upwards so the face, not the chest, is centred.
    top = 0 if h > w else (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((PHOTO_SIZE, PHOTO_SIZE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def photo_key(persona_id: str) -> str:
    return f"personas/{persona_id}/photo.jpg"


async def provision(persona: Persona, photo: bytes) -> Persona:
    """Store the normalised photo and kick off avatar creation for the persona's backend."""
    key = photo_key(persona.id)
    await storage.put(key, photo, "image/jpeg")
    persona.avatar.photo_key = key
    persona.avatar.error = None
    backend = persona.avatar.backend

    if backend == AvatarBackend.SIMLI:
        persona.avatar.simli_face_id = None
        if not settings.simli_api_key:
            persona.avatar.status = AvatarStatus.FAILED
            persona.avatar.error = "SIMLI_API_KEY is not configured on the API"
            return persona
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.post(
                    f"{settings.simli_api_url}/faces/trinity",
                    headers={"x-simli-api-key": settings.simli_api_key},
                    params={"face_name": f"{persona.name}-{persona.id}"[:60]},
                    files={"image": ("photo.jpg", photo, "image/jpeg")},
                )
            r.raise_for_status()
            body = r.json()
            persona.avatar.simli_face_id = body["face_id"]
            persona.avatar.status = AvatarStatus.PROVISIONING
            log.info("simli face queued", extra={"fields": {"persona_id": persona.id, "msg": body.get("message")}})
        except Exception as e:
            persona.avatar.status = AvatarStatus.FAILED
            persona.avatar.error = f"Simli face creation failed: {_err(e)}"
        return persona

    if backend == AvatarBackend.FLASHHEAD:
        persona.avatar.status = AvatarStatus.READY  # the photo is the avatar
        return persona

    persona.avatar.status = AvatarStatus.NOT_REQUIRED
    return persona


async def refresh_status(persona: Persona) -> bool:
    """Poll Simli for a provisioning face (throttled). Returns True if the persona changed."""
    a = persona.avatar
    if a.backend != AvatarBackend.SIMLI or a.status != AvatarStatus.PROVISIONING or not a.simli_face_id:
        return False
    now = time.monotonic()
    if now - _last_poll.get(persona.id, 0.0) < POLL_INTERVAL_S:
        return False
    _last_poll[persona.id] = now
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(
                f"{settings.simli_api_url}/faces/trinity/generation_status",
                headers={"x-simli-api-key": settings.simli_api_key or ""},
                params={"face_id": a.simli_face_id},
            )
        r.raise_for_status()
        status = r.json().get("status")
    except Exception as e:
        log.warning("simli status check failed: %s", _err(e))
        return False
    if status == "completed":
        a.status = AvatarStatus.READY
        return True
    if status in ("failed", "not_found"):
        a.status = AvatarStatus.FAILED
        a.error = f"Simli face generation {status}"
        return True
    return False


def required_status(persona: Persona) -> AvatarStatus:
    if persona.avatar.backend in (AvatarBackend.SIMLI, AvatarBackend.FLASHHEAD):
        return AvatarStatus.READY
    return AvatarStatus.NOT_REQUIRED


def _err(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    return str(e)[:200]

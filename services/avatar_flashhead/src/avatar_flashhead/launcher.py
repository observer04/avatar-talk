"""Glue: launch payload -> photo -> model -> LiveKit room.

The Modal web endpoint only validates the payload and spawns a GPU container;
this module runs inside that container.  It fetches the persona photo (or uses
the baked-in example), loads the FlashHead pipeline, warms it up, then hands the
room to :class:`avatar_flashhead.runner.AvatarRunner`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .protocol import (
    DEFAULT_PHOTO_PATH,
    MAX_PHOTO_BYTES,
    LaunchError,
    LaunchPayload,
)
from .runner import AvatarRunner
from .video_generator import FlashHeadVideoGenerator

log = logging.getLogger(__name__)


class PhotoError(RuntimeError):
    pass


def _photo_token() -> str | None:
    # The API's /v1/personas/{id}/photo route is a *client* route (API_KEYS).
    # AVATAR_API_KEY is preferred; INTERNAL_API_TOKEN works in dev/open mode.
    return os.environ.get("AVATAR_API_KEY") or os.environ.get("INTERNAL_API_TOKEN")


def _same_origin(url: str, api_url: str | None) -> bool:
    if not api_url:
        return False
    a, b = urlparse(url), urlparse(api_url)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


async def fetch_photo(
    payload: LaunchPayload, *, api_url: str | None = None, token: str | None = None
) -> bytes:
    if payload.photo_url:
        url = payload.photo_url
        headers = {}
        if token and _same_origin(url, api_url):
            headers["Authorization"] = f"Bearer {token}"
    elif payload.persona_id:
        if not api_url:
            raise PhotoError("API_URL is not set; cannot fetch the persona photo")
        url = f"{api_url.rstrip('/')}/v1/personas/{payload.persona_id}/photo"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
    else:
        raise PhotoError("payload has neither photo_url nor persona_id")

    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(url, headers=headers) as response:
                if response.status != 200:
                    body = (await response.text())[:300]
                    raise PhotoError(f"photo fetch failed: HTTP {response.status} {body}")
                data = await response.read()
    except aiohttp.ClientError as exc:
        raise PhotoError(f"photo fetch failed: {exc!r}") from exc

    if not data:
        raise PhotoError("photo fetch returned an empty body")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoError(f"photo is larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB")
    return data


def _write_temp_photo(data: bytes) -> str:
    fd, path = tempfile.mkstemp(prefix="flashhead-photo-", suffix=".jpg")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


async def resolve_photo_path(
    payload: LaunchPayload, *, api_url: str | None = None, token: str | None = None
) -> str:
    if payload.photo_url or payload.persona_id:
        if api_url is None:
            api_url = os.environ.get("API_URL")
        if token is None:
            token = _photo_token()
        data = await fetch_photo(payload, api_url=api_url, token=token)
        path = _write_temp_photo(data)
        log.info("using photo for persona %s (%d bytes)", payload.persona_id, len(data))
        return path

    default = os.environ.get("FLASHHEAD_DEFAULT_PHOTO", DEFAULT_PHOTO_PATH)
    if not await asyncio.to_thread(os.path.exists, default):
        raise PhotoError(
            f"no photo source in payload and default image {default!r} does not exist"
        )
    log.info("no photo in payload; using the baked-in example %s", default)
    return default


def load_model():
    """Load the pipeline (weights).  Called once per container / session."""
    from .model import FlashHeadModel

    return FlashHeadModel(
        model_type=os.environ.get("FLASHHEAD_MODEL", "lite"),
        compile=os.environ.get("FLASHHEAD_COMPILE", "0") == "1",
        use_face_crop=os.environ.get("FLASHHEAD_FACE_CROP", "0") == "1",
        sage=os.environ.get("FLASHHEAD_SAGE", "0") == "1",
    )


async def run_session(payload: LaunchPayload, *, model=None) -> str:
    photo_path = await resolve_photo_path(payload)

    if model is None:
        log.info("loading FlashHead model (this can take a few seconds)")
        model = await asyncio.to_thread(load_model)
    await asyncio.to_thread(model.prepare, photo_path)

    warmup_chunks = int(os.environ.get("FLASHHEAD_WARMUP", "3"))
    if warmup_chunks > 0:
        await asyncio.to_thread(model.warmup, warmup_chunks)

    output_size = int(os.environ.get("FLASHHEAD_OUTPUT_SIZE", "0") or 0) or model.params.width
    generator = FlashHeadVideoGenerator(model, output_size=output_size)
    max_session = float(os.environ.get("FLASHHEAD_MAX_SESSION_SECONDS", "0") or 0) or None
    runner = AvatarRunner(
        livekit_url=payload.livekit_url,
        token=payload.token,
        agent_identity=payload.agent_identity,
        video_generator=generator,
        width=output_size,
        height=output_size,
        fps=model.params.fps,
        sample_rate=model.params.sample_rate,
        max_session_seconds=max_session,
        agent_wait_seconds=float(os.environ.get("FLASHHEAD_AGENT_WAIT_SECONDS", "30")),
    )
    log.info(
        "starting avatar for room=%s persona=%s (%dx%d @ %d fps, %s)",
        payload.room,
        payload.persona_id,
        output_size,
        output_size,
        model.params.fps,
        model.model_type,
    )
    return await runner.run()


def run_session_sync(payload: Mapping[str, Any], *, model=None) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parsed = LaunchPayload.parse(payload)
    reason = asyncio.run(run_session(parsed, model=model))
    log.info("FlashHead session finished: %s", reason)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the FlashHead avatar worker")
    parser.add_argument("--payload", help="launch payload as a JSON string")
    parser.add_argument("--payload-file", help="path to a JSON launch payload")
    args = parser.parse_args(argv)

    raw = args.payload
    if raw is None and args.payload_file:
        with open(args.payload_file) as f:
            raw = f.read()
    if raw is None:
        raw = os.environ.get("FLASHHEAD_PAYLOAD")
    if not raw:
        parser.error("provide --payload, --payload-file, or FLASHHEAD_PAYLOAD")

    try:
        data = json.loads(raw)
        LaunchPayload.parse(data)
    except (json.JSONDecodeError, LaunchError) as exc:
        parser.error(f"invalid launch payload: {exc}")
    run_session_sync(data)

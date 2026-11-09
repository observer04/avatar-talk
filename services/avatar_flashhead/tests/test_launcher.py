"""Launch payload parsing, bearer auth, and photo resolution tests."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import pytest
from aiohttp import web
from avatar_flashhead.launcher import PhotoError, fetch_photo, resolve_photo_path
from avatar_flashhead.protocol import LaunchError, LaunchPayload, authorize, bearer_token


def valid_payload(**overrides):
    data = {
        "room": "room-1",
        "livekit_url": "wss://example.livekit.cloud",
        "token": "jwt-token",
        "persona_id": "persona-1",
        "photo_key": "personas/persona-1/photo.jpg",
        "agent_identity": "agent-1",
    }
    data.update(overrides)
    return data


def test_parse_valid_payload():
    payload = LaunchPayload.parse(valid_payload())
    assert payload.room == "room-1"
    assert payload.agent_identity == "agent-1"
    assert payload.persona_id == "persona-1"
    assert payload.photo_key == "personas/persona-1/photo.jpg"
    assert payload.photo_url is None


def test_parse_optional_photo_url():
    payload = LaunchPayload.parse(valid_payload(photo_url="https://cdn.example.com/me.jpg"))
    assert payload.photo_url == "https://cdn.example.com/me.jpg"


@pytest.mark.parametrize("missing", ["room", "livekit_url", "token", "agent_identity"])
def test_parse_requires_fields(missing):
    data = valid_payload()
    del data[missing]
    with pytest.raises(LaunchError, match=missing):
        LaunchPayload.parse(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("livekit_url", "ftp://example.com"),
        ("photo_url", "file:///etc/passwd"),
        ("room", ""),
        ("token", 123),
    ],
)
def test_parse_rejects_bad_values(field, value):
    with pytest.raises(LaunchError):
        LaunchPayload.parse(valid_payload(**{field: value}))


def test_parse_photo_key_requires_persona():
    with pytest.raises(LaunchError, match="persona_id"):
        LaunchPayload.parse(valid_payload(persona_id=None))


def test_bearer_token():
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer abc") == "abc"
    assert bearer_token("Basic abc") is None
    assert bearer_token(None) is None


def test_authorize():
    assert authorize(None, None) is True  # dev/open mode
    assert authorize("Bearer secret", "secret") is True
    assert authorize("bearer secret", "secret") is True
    assert authorize("Bearer wrong", "secret") is False
    assert authorize(None, "secret") is False
    assert authorize("Bearer ", "secret") is False


@contextlib.asynccontextmanager
async def serve(handler):
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


async def test_fetch_photo_from_persona_api():
    async def handler(request):
        assert request.headers.get("Authorization") == "Bearer secret"
        return web.Response(body=b"jpeg-bytes", content_type="image/jpeg")

    async with serve(handler) as base:
        payload = LaunchPayload.parse(valid_payload())
        data = await fetch_photo(payload, api_url=base, token="secret")
        assert data == b"jpeg-bytes"


async def test_fetch_photo_direct_url_does_not_leak_token():
    async def handler(request):
        assert "Authorization" not in request.headers
        return web.Response(body=b"png-bytes", content_type="image/png")

    async with serve(handler) as base:
        payload = LaunchPayload.parse(
            valid_payload(photo_url=f"{base}/me.png", persona_id=None, photo_key=None)
        )
        data = await fetch_photo(payload, api_url="https://api.example.com", token="secret")
        assert data == b"png-bytes"


async def test_fetch_photo_direct_url_same_origin_sends_token():
    async def handler(request):
        assert request.headers.get("Authorization") == "Bearer secret"
        return web.Response(body=b"png-bytes", content_type="image/png")

    async with serve(handler) as base:
        payload = LaunchPayload.parse(
            valid_payload(photo_url=f"{base}/me.png", persona_id=None, photo_key=None)
        )
        data = await fetch_photo(payload, api_url=base, token="secret")
        assert data == b"png-bytes"


async def test_fetch_photo_reports_http_errors():
    async def handler(request):
        return web.Response(status=404, text="no photo")

    async with serve(handler) as base:
        payload = LaunchPayload.parse(valid_payload())
        with pytest.raises(PhotoError, match="404"):
            await fetch_photo(payload, api_url=base, token="secret")


async def test_resolve_photo_path_default(monkeypatch, tmp_path):
    image = tmp_path / "girl.png"
    image.write_bytes(b"default-image")
    monkeypatch.setenv("FLASHHEAD_DEFAULT_PHOTO", str(image))
    payload = LaunchPayload.parse(
        {"room": "r", "livekit_url": "wss://x", "token": "t", "agent_identity": "a"}
    )
    assert await resolve_photo_path(payload) == str(image)


async def test_resolve_photo_path_fetches_and_writes(tmp_path):
    async def handler(request):
        return web.Response(body=b"fetched-jpeg", content_type="image/jpeg")

    async with serve(handler) as base:
        payload = LaunchPayload.parse(valid_payload())
        path = await resolve_photo_path(payload, api_url=base, token="secret")
        try:
            assert await asyncio.to_thread(Path(path).read_bytes) == b"fetched-jpeg"
        finally:
            os.unlink(path)

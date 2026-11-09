import io

import httpx
import pytest
from PIL import Image

from api import avatars
from api.db import init_db
from api.main import app
from api.routes import sessions as sessions_routes

AUTH = {"Authorization": "Bearer test-key"}


def jpeg(w=800, h=1000) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (180, 140, 120)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
async def client(monkeypatch):
    async def fake_start_room(meta, user_identity):
        return "room_test", "jwt-token"

    monkeypatch.setattr(sessions_routes, "start_room", fake_start_room)
    await init_db()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_auth_required(client):
    assert (await client.get("/v1/personas")).status_code == 401
    assert (await client.get("/v1/personas", headers=AUTH)).status_code == 200


async def test_flashhead_persona_photo_to_session(client):
    r = await client.post("/v1/personas", json={"name": "Nia", "avatar_backend": "flashhead"}, headers=AUTH)
    assert r.status_code == 201
    p = r.json()
    assert p["avatar"]["status"] == "pending"
    # no session before the avatar is ready
    r = await client.post("/v1/sessions", json={"persona_id": p["id"]}, headers=AUTH)
    assert r.status_code == 409
    r = await client.put(f"/v1/personas/{p['id']}/photo", files={"photo": ("me.jpg", jpeg(), "image/jpeg")},
                         headers=AUTH)
    assert r.status_code == 200 and r.json()["avatar"]["status"] == "ready"
    photo = await client.get(f"/v1/personas/{p['id']}/photo", headers=AUTH)
    assert Image.open(io.BytesIO(photo.content)).size == (512, 512)
    r = await client.post("/v1/sessions", json={"persona_id": p["id"]}, headers=AUTH)
    assert r.status_code == 201 and r.json()["room"] == "room_test"


async def test_simli_without_key_fails_clearly(client):
    p = (await client.post("/v1/personas", json={"avatar_backend": "simli"}, headers=AUTH)).json()
    r = await client.put(f"/v1/personas/{p['id']}/photo", files={"photo": ("a.jpg", jpeg(), "image/jpeg")},
                         headers=AUTH)
    assert r.json()["avatar"]["status"] == "failed" and "SIMLI_API_KEY" in r.json()["avatar"]["error"]


async def test_bad_photo_rejected(client):
    p = (await client.post("/v1/personas", json={"avatar_backend": "flashhead"}, headers=AUTH)).json()
    r = await client.put(f"/v1/personas/{p['id']}/photo", files={"photo": ("x.jpg", b"nope", "image/jpeg")},
                         headers=AUTH)
    assert r.status_code == 422
    r = await client.put(f"/v1/personas/{p['id']}/photo", files={"photo": ("s.jpg", jpeg(100, 100), "image/jpeg")},
                         headers=AUTH)
    assert r.status_code == 422


async def test_internal_report_and_spend_cap(client):
    p = (await client.post("/v1/personas", json={"avatar_backend": "browser3d"}, headers=AUTH)).json()
    assert p["avatar"]["status"] == "not_required"
    s = (await client.post("/v1/sessions", json={"persona_id": p["id"]}, headers=AUTH)).json()
    assert (await client.post(f"/internal/sessions/{s['session_id']}/report", json={"cost_usd": 5})).status_code == 401
    r = await client.post(f"/internal/sessions/{s['session_id']}/report", json={"cost_usd": 5.0, "turns": []},
                          headers={"Authorization": "Bearer internal-secret"})
    assert r.status_code == 200
    got = (await client.get(f"/v1/sessions/{s['session_id']}", headers=AUTH)).json()
    assert got["status"] == "ended" and got["cost_usd"] == 5.0
    # cap is $1 in tests -> next session refused
    assert (await client.post("/v1/sessions", json={"persona_id": p["id"]}, headers=AUTH)).status_code == 429


async def test_backend_switch_keeps_photo(client):
    p = (await client.post("/v1/personas", json={"avatar_backend": "flashhead"}, headers=AUTH)).json()
    await client.put(f"/v1/personas/{p['id']}/photo", files={"photo": ("m.jpg", jpeg(), "image/jpeg")}, headers=AUTH)
    r = await client.patch(f"/v1/personas/{p['id']}", json={"avatar_backend": "simli"}, headers=AUTH)
    assert r.json()["avatar"]["status"] == "pending" and r.json()["avatar"]["photo_key"]
    r = await client.patch(f"/v1/personas/{p['id']}", json={"avatar_backend": "flashhead"}, headers=AUTH)
    assert r.json()["avatar"]["status"] == "ready"


def test_normalise_crops_portrait_square():
    out = avatars.normalise_photo(jpeg(600, 900))
    assert Image.open(io.BytesIO(out)).size == (512, 512)

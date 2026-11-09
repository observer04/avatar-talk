from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from avatar_core import Persona, SessionMetadata
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api import avatars
from api.auth import client_auth, internal_auth, key_hash
from api.db import SessionRow, get_db, spend_today
from api.livekit_rooms import start_room
from api.routes.personas import load
from api.settings import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["sessions"])


class SessionCreate(BaseModel):
    persona_id: str


class SessionStart(BaseModel):
    session_id: str
    room: str
    livekit_url: str
    token: str
    persona: Persona


@router.post("/v1/sessions", response_model=SessionStart, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate, db: AsyncSession = Depends(get_db), api_key: str | None = Depends(client_auth)
) -> SessionStart:
    _, persona = await load(db, body.persona_id)
    if persona.avatar.status != avatars.required_status(persona):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"avatar not ready (status={persona.avatar.status.value}"
            + (f": {persona.avatar.error}" if persona.avatar.error else "") + ")",
        )
    if (spent := await spend_today(db)) >= settings.daily_spend_cap_usd:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"daily spend cap reached (${spent:.2f})")

    meta = SessionMetadata(persona_id=persona.id)
    try:
        room, token = await start_room(meta, user_identity=f"user_{uuid.uuid4().hex[:8]}")
    except RuntimeError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    db.add(SessionRow(id=meta.session_id, persona_id=persona.id, room=room, api_key_hash=key_hash(api_key)))
    await db.commit()
    log.info("session created", extra={"fields": {"session_id": meta.session_id, "persona_id": persona.id,
                                                  "room": room}})
    return SessionStart(
        session_id=meta.session_id, room=room, livekit_url=settings.livekit_url or "", token=token, persona=persona
    )


@router.get("/v1/sessions/{session_id}", dependencies=[Depends(client_auth)])
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return {
        "session_id": row.id, "persona_id": row.persona_id, "room": row.room, "status": row.status,
        "created_at": row.created_at, "ended_at": row.ended_at, "cost_usd": row.cost_usd, "report": row.report,
    }


# --- internal (agent -> API) ---------------------------------------------------------------

@router.get("/internal/personas/{persona_id}", response_model=Persona, dependencies=[Depends(internal_auth)])
async def internal_persona(persona_id: str, db: AsyncSession = Depends(get_db)) -> Persona:
    return (await load(db, persona_id))[1]


@router.post("/internal/sessions/{session_id}/report", dependencies=[Depends(internal_auth)])
async def session_report(session_id: str, report: dict, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(SessionRow, session_id)
    if row is None:  # synthetic/inline sessions aren't created through the API
        row = SessionRow(id=session_id, persona_id=str(report.get("persona_id", "")), room="")
        db.add(row)
    row.status = "ended"
    row.ended_at = datetime.now(UTC)
    row.cost_usd = float(report.get("cost_usd") or 0.0)
    row.report = report
    await db.commit()
    return {"ok": True}

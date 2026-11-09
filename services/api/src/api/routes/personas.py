from __future__ import annotations

from avatar_core import AvatarBackend, AvatarConfig, AvatarStatus, LLMConfig, Persona, VoiceConfig
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import avatars
from api.auth import ClientKey
from api.db import PersonaRow, get_db
from api.storage import storage

router = APIRouter(prefix="/v1/personas", tags=["personas"], dependencies=[ClientKey])


class PersonaCreate(BaseModel):
    name: str = "Ava"
    system_prompt: str = ""
    greeting: str | None = "Greet the person warmly and briefly introduce yourself."
    voice: VoiceConfig | None = None
    llm: LLMConfig | None = None
    avatar_backend: AvatarBackend = AvatarBackend.SIMLI
    tools: list[str] | None = None
    max_session_minutes: int | None = None


class PersonaPatch(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    greeting: str | None = None
    voice: VoiceConfig | None = None
    llm: LLMConfig | None = None
    avatar_backend: AvatarBackend | None = None
    tools: list[str] | None = None
    max_session_minutes: int | None = None


async def load(db: AsyncSession, persona_id: str) -> tuple[PersonaRow, Persona]:
    row = await db.get(PersonaRow, persona_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")
    persona = Persona.model_validate(row.data)
    if await avatars.refresh_status(persona):
        row.data = persona.model_dump(mode="json")
        await db.commit()
    return row, persona


PHOTO_BACKENDS = (AvatarBackend.SIMLI, AvatarBackend.FLASHHEAD)


def initial_avatar(backend: AvatarBackend, photo_key: str | None = None) -> AvatarConfig:
    """Avatar state right after choosing a backend (before/without a new photo upload)."""
    if backend not in PHOTO_BACKENDS:
        status_ = AvatarStatus.NOT_REQUIRED
    elif backend == AvatarBackend.FLASHHEAD and photo_key:
        status_ = AvatarStatus.READY  # FlashHead uses the stored photo directly
    else:
        status_ = AvatarStatus.PENDING  # needs a photo upload (Simli builds its face from it)
    return AvatarConfig(backend=backend, photo_key=photo_key, status=status_)


def _apply(persona: Persona, body: PersonaCreate | PersonaPatch) -> Persona:
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    backend = data.pop("avatar_backend", None)
    updated = persona.model_copy(update=data)
    if backend is not None and backend != persona.avatar.backend:
        updated.avatar = initial_avatar(backend, persona.avatar.photo_key)
    return updated


@router.get("", response_model=list[Persona])
async def list_personas(db: AsyncSession = Depends(get_db)) -> list[Persona]:
    rows = (await db.scalars(select(PersonaRow).order_by(PersonaRow.created_at.desc()))).all()
    out = []
    for row in rows:
        persona = Persona.model_validate(row.data)
        if await avatars.refresh_status(persona):
            row.data = persona.model_dump(mode="json")
        out.append(persona)
    await db.commit()
    return out


@router.post("", response_model=Persona, status_code=status.HTTP_201_CREATED)
async def create_persona(body: PersonaCreate, db: AsyncSession = Depends(get_db)) -> Persona:
    persona = _apply(Persona(), body)
    persona.avatar = initial_avatar(body.avatar_backend)
    db.add(PersonaRow(id=persona.id, data=persona.model_dump(mode="json")))
    await db.commit()
    return persona


@router.get("/{persona_id}", response_model=Persona)
async def get_persona(persona_id: str, db: AsyncSession = Depends(get_db)) -> Persona:
    return (await load(db, persona_id))[1]


@router.patch("/{persona_id}", response_model=Persona)
async def update_persona(persona_id: str, body: PersonaPatch, db: AsyncSession = Depends(get_db)) -> Persona:
    row, persona = await load(db, persona_id)
    persona = _apply(persona, body)
    row.data = persona.model_dump(mode="json")
    await db.commit()
    return persona


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona(persona_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    row, persona = await load(db, persona_id)
    if persona.avatar.photo_key:
        await storage.delete(persona.avatar.photo_key)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{persona_id}/photo", response_model=Persona)
async def upload_photo(
    persona_id: str, photo: UploadFile = File(...), db: AsyncSession = Depends(get_db)
) -> Persona:
    row, persona = await load(db, persona_id)
    raw = await photo.read()
    try:
        normalised = avatars.normalise_photo(raw)
    except avatars.PhotoError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    persona = await avatars.provision(persona, normalised)
    row.data = persona.model_dump(mode="json")
    await db.commit()
    return persona


@router.get("/{persona_id}/photo")
async def get_photo(persona_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    _, persona = await load(db, persona_id)
    data = await storage.get(persona.avatar.photo_key) if persona.avatar.photo_key else None
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no photo")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})

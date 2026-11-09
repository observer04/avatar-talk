"""Persistence. Personas are stored as JSON documents (the pydantic Persona model
is the schema, so adding persona fields needs no migration); sessions are rows
with the report the agent posts at the end."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from api.settings import settings


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class PersonaRow(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(32), index=True)
    room: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="started")  # started | ended
    api_key_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def _engine_url(url: str) -> str:
    # Neon/Supabase hand out postgres:// URLs; we need the asyncpg driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


engine = create_async_engine(_engine_url(settings.database_url), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as db:
        yield db


async def init_db() -> None:
    """Dev convenience: create tables directly. Production uses Alembic migrations."""
    if settings.database_url.startswith("sqlite"):
        from pathlib import Path

        Path(settings.database_url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def spend_today(db: AsyncSession) -> float:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(select(func.coalesce(func.sum(SessionRow.cost_usd), 0.0)).where(
        SessionRow.created_at >= start
    ))
    return float(total or 0.0)

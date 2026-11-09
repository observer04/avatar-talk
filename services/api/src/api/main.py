"""Control-plane API: personas, avatar provisioning, LiveKit sessions.

Run locally:  uv run uvicorn api.main:app --reload --port 8000
Production:   infra/modal/api_app.py
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from avatar_core.logging import bind, setup_logging
from avatar_core.telemetry import setup_telemetry, shutdown_telemetry
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.db import SessionLocal, init_db
from api.routes import personas, sessions
from api.settings import settings
from api.storage import storage

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("api ready", extra={"fields": {"db": settings.database_url.split(":")[0],
                                            "storage": settings.storage_backend}})
    yield
    shutdown_telemetry()


def create_app() -> FastAPI:
    setup_logging(settings.service_name, settings.log_level, settings.log_format)
    setup_telemetry(settings)

    app = FastAPI(title="Avatar Talk API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        bind(request_id=rid)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        if request.url.path != "/health":
            log.info("request", extra={"fields": {"method": request.method, "path": request.url.path,
                                                  "status": response.status_code,
                                                  "ms": round((time.perf_counter() - start) * 1000, 1)}})
        return response

    app.include_router(personas.router)
    app.include_router(sessions.router)

    @app.get("/health")
    async def health() -> dict:
        try:
            async with SessionLocal() as db:
                await db.execute(text("select 1"))
            db_ok = True
        except Exception:
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "db": db_ok, "storage": await storage.healthy()}

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
    return app


app = create_app()

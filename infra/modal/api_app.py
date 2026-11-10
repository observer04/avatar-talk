"""Modal deployment of the control-plane API, which also serves the built web app.

Until a managed Postgres URL is configured (DATABASE_URL in the secret), the
API uses SQLite on a Modal Volume with a single container and commits the
volume after every write. Set DATABASE_URL (Neon/Supabase) and STORAGE_BACKEND=s3
for production, then raise max_containers.

Deploy:  (cd web && npm run build) && modal deploy infra/modal/api_app.py
Secrets: modal secret "avatar-talk-api" (see infra/modal/README.md)
"""

from __future__ import annotations

from pathlib import Path

import modal

# The app file is re-imported inside the container, where the repo layout does not exist.
ROOT = Path(__file__).resolve().parents[2] if modal.is_local() else Path("/root")
WEB_DIST = ROOT / "web" / "dist"

volume = modal.Volume.from_name("avatar-talk-data", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_pip_install(requirements=[str(ROOT / "infra/modal/requirements-api.txt")])
    .env({
        "PYTHONPATH": "/root",
        "ENVIRONMENT": "prod",
        "LOG_FORMAT": "json",
        # Defaults for the volume-backed mode; overridden by the secret when Postgres/S3 are set.
        "DATABASE_URL": "sqlite+aiosqlite:////data/api.db",
        "LOCAL_STORAGE_DIR": "/data/storage",
    })
    # local files last: added at container start, no rebuild on code changes
    .add_local_dir(ROOT / "packages/core/src/avatar_core", "/root/avatar_core", ignore=["**/__pycache__"])
    .add_local_dir(ROOT / "services/api/src/api", "/root/api", ignore=["**/__pycache__"])
    .add_local_dir(ROOT / "services/api/migrations", "/root/migrations", ignore=["**/__pycache__"])
    .add_local_file(ROOT / "services/api/alembic.ini", "/root/alembic.ini")
)
if WEB_DIST.exists():
    image = image.add_local_dir(WEB_DIST, "/root/web")

app = modal.App("avatar-talk-api")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("avatar-talk-api")],
    volumes={"/data": volume},
    cpu=1.0,
    memory=1024,
    max_containers=1,  # single SQLite writer; raise once DATABASE_URL points to Postgres
    scaledown_window=10 * 60,
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    import os

    from api.main import app as api
    from fastapi import Request
    from fastapi.staticfiles import StaticFiles

    if os.environ.get("DATABASE_URL", "").startswith("sqlite"):
        @api.middleware("http")
        async def commit_volume(request: Request, call_next):
            response = await call_next(request)
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status_code < 400:
                await volume.commit.aio()
            return response

    if Path("/root/web").exists():
        api.mount("/", StaticFiles(directory="/root/web", html=True), name="web")
    return api

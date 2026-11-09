"""HTTP entrypoint for local runs: WebRTC signalling (/api/offer) + health check.

Run:  uv run app.py   (then start the web app: cd ../web && npm run dev)
For the hosted version see modal_app.py.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from loguru import logger
from pipecat.transports.smallwebrtc.connection import IceServer
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from bot import run_bot
from config import settings
from services import prefetch_models

handler = SmallWebRTCRequestHandler(ice_servers=[IceServer(**s) for s in settings.ice_servers()])
_sessions: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(prefetch_models, settings)
    yield
    for task in _sessions:
        task.cancel()
    await handler.close()


app = FastAPI(lifespan=lifespan)


@app.get("/api/rtc-config")
async def rtc_config():
    return settings.client_rtc_config()


@app.post("/api/offer")
async def offer(request: dict):
    if not settings.check_access(request):
        raise HTTPException(status_code=401, detail="Missing or wrong access key")

    async def on_connection(connection):
        # Run the session independently of this HTTP request.
        task = asyncio.create_task(run_bot(connection))
        _sessions.add(task)
        task.add_done_callback(_sessions.discard)

    return await handler.handle_web_request(
        request=SmallWebRTCRequest.from_dict(request),
        webrtc_connection_callback=on_connection,
    )


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest):
    await handler.handle_patch_request(request)
    return {"status": "success"}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "llm": settings.llm_provider,
        "stt": settings.stt_provider,
        "tts": settings.tts_provider,
        "avatar_mode": settings.avatar_mode,
        "active_sessions": len(_sessions),
    }


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    uvicorn.run(app, host=settings.host, port=settings.port)

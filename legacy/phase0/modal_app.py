"""Hosted demo on Modal: one public URL serving the web app + signalling; each
conversation runs in its own CPU container that scales to zero afterwards.

  browser ──HTTPS──► web (FastAPI: static UI, /api/rtc-config, /api/offer)
     │                  │ spawns Session.run() and passes the SDP offer/answer via a modal.Dict
     └───WebRTC (UDP, STUN/TURN)───► Session container (Pipecat bot, CPU only)

Setup (once):
  cd web && npm run build                      # the UI is served from web/dist
  modal secret create avatar-talk GROQ_API_KEY=... ACCESS_TOKEN=<pick-a-passphrase> \
      [TURN_URL=turn:standard.relay.metered.ca:443 TURN_USERNAME=... TURN_CREDENTIAL=...]
Deploy:
  cd server && modal deploy modal_app.py       # then open <url>/?key=<passphrase>
"""

import asyncio
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
WEB_DIST = HERE.parent / "web" / "dist"

app = modal.App("avatar-talk")
secret = modal.Secret.from_name("avatar-talk")


def _prefetch_models():
    from config import settings
    from services import prefetch_models

    prefetch_models(settings)  # bake Kokoro + Moonshine weights into the image


image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_pip_install(
        "pipecat-ai[google,groq,kokoro,local-smart-turn-v3,moonshine,silero,webrtc]==1.15.0",
        "fastapi",
        "pydantic-settings",
        "python-dotenv",
    )
    .env({"PYTHONPATH": "/root/server", "STUN_URL": "stun:stun.l.google.com:19302", "WAIT_FOR_ICE_GATHERING": "true"})
    .add_local_dir(
        HERE,
        "/root/server",
        copy=True,
        ignore=[".venv", "**/__pycache__", "tests", "scripts", "*.wav", ".pytest_cache"],
    )
    .run_function(_prefetch_models)
    .add_local_dir(WEB_DIST, "/root/web")
)

ANSWER_TIMEOUT_SECS = 60


@app.cls(image=image, secrets=[secret], cpu=4.0, memory=4096, timeout=30 * 60)
class Session:
    """One conversation per container (Kokoro + Moonshine want the CPU to themselves)."""

    @modal.method()
    async def run(self, d: modal.Dict):
        from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection

        from bot import run_bot
        from config import settings

        offer = await d.get.aio("offer")
        connection = SmallWebRTCConnection(ice_servers=[IceServer(**s) for s in settings.ice_servers()])
        await connection.initialize(sdp=offer["sdp"], type=offer["type"])
        await d.put.aio("answer", connection.get_answer())
        await run_bot(connection)  # returns when the user disconnects (or Pipecat's idle timeout fires)


@app.function(image=image, secrets=[secret], cpu=0.5, memory=1024)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles

    from config import settings

    api = FastAPI()

    @api.get("/api/rtc-config")
    async def rtc_config():
        return settings.client_rtc_config()

    @api.post("/api/offer")
    async def offer(request: dict):
        if not settings.check_access(request):
            raise HTTPException(status_code=401, detail="Missing or wrong access key")
        if not request.get("sdp") or not request.get("type"):
            raise HTTPException(status_code=400, detail="missing sdp or type")

        async with modal.Dict.ephemeral() as d:
            await d.put.aio("offer", {"sdp": request["sdp"], "type": request["type"]})
            call = await Session().run.spawn.aio(d)
            deadline = time.monotonic() + ANSWER_TIMEOUT_SECS
            while time.monotonic() < deadline:
                answer = await d.get.aio("answer")
                if answer is not None:
                    return answer
                await asyncio.sleep(0.1)
            await call.cancel.aio()
            raise HTTPException(status_code=504, detail="Timed out waiting for the bot to start")

    @api.get("/api/health")
    async def health():
        return {"status": "ok", "llm": settings.llm_provider, "stt": settings.stt_provider, "tts": settings.tts_provider}

    api.mount("/", StaticFiles(directory="/root/web", html=True), name="web")
    return api

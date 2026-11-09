"""Modal deployment of the self-hosted SoulX-FlashHead avatar worker.

The agent POSTs the launch payload to ``{FLASHHEAD_LAUNCH_URL}/launch``; this app
validates the bearer token, spawns a GPU session container, and that container
joins the LiveKit room as the avatar participant.

Deploy:
    env -u MODAL_TOKEN_ID -u MODAL_TOKEN_SECRET modal deploy infra/modal/flashhead_app.py

Environment (read at deploy time):
    FLASHHEAD_GPU          L40S (default), H100, A10, ...
    FLASHHEAD_MODEL        lite (default) or pro
    FLASHHEAD_COMPILE      1 to enable torch.compile (default 0 / eager)
    FLASHHEAD_KEEP_WARM    1 to keep one GPU container warm (default 0)
    FLASHHEAD_MAX_CONTAINERS  concurrent GPU sessions (default 10)

The app uses the existing ``avatar-talk-agent`` secret for API_URL /
INTERNAL_API_TOKEN / AVATAR_API_KEY and never downloads weights: they live in
the ``flashhead-weights`` volume.
"""

import os
from pathlib import Path

import modal

# The app file is re-imported inside the container, where the repo layout does not exist.
ROOT = Path(__file__).resolve().parents[2] if modal.is_local() else Path("/root")

FLASHHEAD_COMMIT = "9bc03de06bb0de82cd6bc477804512ae06144bf2"
FA_WHEEL = (
    "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/"
    "flash_attn-2.8.0.post2+cu12torch2.7cxx11abiTRUE-cp310-cp310-linux_x86_64.whl"
)

GPU = os.environ.get("FLASHHEAD_GPU", "L40S")
MODEL = os.environ.get("FLASHHEAD_MODEL", "lite")
COMPILE = os.environ.get("FLASHHEAD_COMPILE", "0") == "1"
KEEP_WARM = os.environ.get("FLASHHEAD_KEEP_WARM", "0") == "1"
MAX_CONTAINERS = int(os.environ.get("FLASHHEAD_MAX_CONTAINERS", "10"))
SESSION_TIMEOUT = int(os.environ.get("FLASHHEAD_SESSION_TIMEOUT", str(60 * 60)))
SCALEDOWN_WINDOW = int(os.environ.get("FLASHHEAD_SCALEDOWN_WINDOW", "60"))

app = modal.App("avatar-talk-flashhead")
weights = modal.Volume.from_name("flashhead-weights", create_if_missing=True)
icache = modal.Volume.from_name("flashhead-inductor-cache", create_if_missing=True)

# The heavy inference image is exactly the spike recipe plus livekit-agents.
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg", "build-essential", "libgl1", "libglib2.0-0")
    .pip_install("torch==2.7.1", "torchvision==0.22.1", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install(FA_WHEEL)
    .pip_install(
        "torch==2.7.1",  # keep pinned while resolving the rest
        "diffusers>=0.34.0",
        "transformers==4.57.3",
        "tokenizers>=0.20.3",
        "accelerate>=1.8.1",
        "xfuser>=0.4.3",
        "einops",
        "pyyaml",
        "loguru",
        "easydict",
        "ftfy",
        "imageio",
        "imageio-ffmpeg",
        "scikit-image",
        "pyloudnorm",
        "librosa",
        "soundfile",
        # mediapipe is intentionally absent: 0.10.9 pins protobuf<4 while
        # livekit 1.2 requires protobuf>=5.  Face crop is not used; the image
        # build patches the unused import out (patches/lazy_mediapipe.py).
        "opencv-python-headless>=4.12.0.88",
        "sageattention==1.0.6",  # triton-only SageAttention v1; off by default
        "livekit-agents==1.8.11",
    )
    .run_commands(
        f"git clone https://github.com/Soul-AILab/SoulX-FlashHead /opt/flashhead && "
        f"cd /opt/flashhead && git checkout {FLASHHEAD_COMMIT}",
    )
    .add_local_file(
        ROOT / "services/avatar_flashhead/patches/lazy_mediapipe.py",
        "/opt/flashhead_patch.py",
        copy=True,
    )
    .run_commands(
        "python /opt/flashhead_patch.py",
        "python -c \"import torch, flash_attn; print(torch.__version__, torch.version.cuda, flash_attn.__version__)\"",
    )
    .env(
        {
            "TORCHINDUCTOR_CACHE_DIR": "/icache" if COMPILE else "/tmp/inductor",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": "/opt/avatar_flashhead/src",
        }
    )
    .add_local_dir(
        ROOT / "services/avatar_flashhead/src",
        "/opt/avatar_flashhead/src",
        copy=True,
        ignore=["**/__pycache__"],
    )
)

# The web endpoint only needs FastAPI and the stdlib-only launch protocol.
web_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("fastapi>=0.115")
    .env({"PYTHONPATH": "/opt/avatar_flashhead/src"})
    .add_local_dir(
        ROOT / "services/avatar_flashhead/src",
        "/opt/avatar_flashhead/src",
        copy=True,
        ignore=["**/__pycache__"],
    )
)

volumes: dict[str, modal.Volume] = {"/weights": weights}
if COMPILE:
    volumes["/icache"] = icache

session_env = {
    "FLASHHEAD_MODEL": MODEL,
    "FLASHHEAD_COMPILE": "1" if COMPILE else "0",
    "FLASHHEAD_WARMUP": os.environ.get("FLASHHEAD_WARMUP", "3"),
    "FLASHHEAD_SAGE": os.environ.get("FLASHHEAD_SAGE", "0"),
    "FLASHHEAD_FACE_CROP": os.environ.get("FLASHHEAD_FACE_CROP", "0"),
    "FLASHHEAD_OUTPUT_SIZE": os.environ.get("FLASHHEAD_OUTPUT_SIZE", "0"),
    "FLASHHEAD_MAX_SESSION_SECONDS": os.environ.get("FLASHHEAD_MAX_SESSION_SECONDS", "0"),
    "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
}


@app.cls(
    image=image,
    gpu=GPU,
    volumes=volumes,
    secrets=[modal.Secret.from_name("avatar-talk-agent")],
    env=session_env,
    cpu=8,
    memory=49152,
    timeout=SESSION_TIMEOUT,
    region="us-east",
    min_containers=1 if KEEP_WARM else 0,
    max_containers=MAX_CONTAINERS,
    scaledown_window=SCALEDOWN_WINDOW,
)
class FlashHeadSession:
    @modal.enter()
    def load(self) -> None:
        # Load the pipeline once per container; each session only swaps the photo.
        from avatar_flashhead.launcher import load_model

        self.model = load_model()

    @modal.method()
    def run(self, payload: dict) -> None:
        from avatar_flashhead.launcher import run_session_sync

        run_session_sync(payload, model=self.model)


@app.function(
    image=web_image,
    secrets=[modal.Secret.from_name("avatar-talk-agent")],
    cpu=0.25,
    memory=512,
    timeout=60,
    max_containers=2,
)
@modal.asgi_app()
def web():
    from avatar_flashhead.protocol import LaunchError, LaunchPayload, authorize
    from fastapi import FastAPI, HTTPException, Request

    api = FastAPI(title="avatar-talk-flashhead", docs_url=None, redoc_url=None)

    @api.get("/health")
    async def health() -> dict:
        return {"ok": True, "app": "avatar-talk-flashhead", "gpu": GPU, "model": MODEL}

    @api.post("/launch")
    async def launch(request: Request) -> dict:
        expected = os.environ.get("INTERNAL_API_TOKEN")
        if not authorize(request.headers.get("authorization"), expected):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")
        try:
            body = await request.json()
            payload = LaunchPayload.parse(body)
        except (ValueError, LaunchError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        call = await FlashHeadSession().run.spawn.aio(payload.as_dict())
        return {
            "status": "accepted",
            "call_id": getattr(call, "object_id", None),
            "room": payload.room,
        }

    return api

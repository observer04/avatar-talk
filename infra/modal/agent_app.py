"""Modal deployment of the LiveKit agent worker (CPU).

A LiveKit worker is a long-running process that dials out to LiveKit Cloud and
waits for job dispatches; each call runs in its own subprocess. We keep
`min_containers` warm containers, each running one worker started in
@modal.enter and drained in @modal.exit, so in-flight calls finish when Modal
recycles a container.

Deploy:  modal deploy infra/modal/agent_app.py
Secrets: modal secret "avatar-talk-agent" (see infra/modal/README.md)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import modal

# The app file is re-imported inside the container, where the repo layout does not exist.
ROOT = Path(__file__).resolve().parents[2] if modal.is_local() else Path("/root")
MIN_WORKERS = 1

image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_pip_install(requirements=[str(ROOT / "infra/modal/requirements-agent.txt")])
    .add_local_dir(ROOT / "packages/core/src/avatar_core", "/root/avatar_core", copy=True)
    .add_local_dir(ROOT / "services/agent/src/agent", "/root/agent", copy=True,
                   ignore=["**/__pycache__"])
    .env({"PYTHONPATH": "/root", "ENVIRONMENT": "prod", "LOG_LEVEL": "INFO"})
    # Bake VAD / turn-detector weights into the image (no download at call time).
    .run_commands("cd /root && python -m agent.main download-files")
)

app = modal.App("avatar-talk-agent")


@app.cls(
    image=image,
    secrets=[modal.Secret.from_name("avatar-talk-agent")],
    cpu=2.0,
    memory=4096,
    min_containers=MIN_WORKERS,
    max_containers=MIN_WORKERS,  # LiveKit balances jobs across registered workers
    timeout=24 * 3600,
    scaledown_window=20 * 60,
    # Next to the AI providers (Deepgram, Groq, Cartesia are US-hosted); LiveKit's backbone
    # carries audio from the user's nearest edge.
    region="us-east",
)
class AgentWorker:
    @modal.enter()
    def start(self) -> None:
        # `start` = production mode (no dev helpers).
        self.proc = subprocess.Popen(
            ["python", "-m", "agent.main", "start"], cwd="/root"
        )

    @modal.method()
    def status(self) -> dict:
        return {"worker_running": self.proc.poll() is None, "exit_code": self.proc.poll()}

    @modal.exit()
    def stop(self) -> None:
        # SIGTERM lets LiveKit drain: stop taking jobs, wait for active calls to end.
        self.proc.terminate()
        try:
            self.proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            self.proc.kill()

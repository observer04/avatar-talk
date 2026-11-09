"""Synthetic monitoring: a scripted caller talks to the deployed agent on a schedule.

Each run joins a fresh LiveKit room as a "user", asks a tool question and a plain
question, interrupts the agent, and checks transcripts and latency. A run that
fails or breaches the thresholds raises, so Modal marks it failed (enable failure
alerts for the app in the Modal dashboard). Results are logged as one JSON line.

Cost: ~1 minute of STT/TTS/LLM per run (about $0.02), so the default is every 3 hours.

Deploy:   modal deploy infra/modal/monitor_app.py
Run now:  modal run infra/modal/monitor_app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[2] if modal.is_local() else Path("/root")

THRESHOLDS = {
    "join_to_greeting_s": 6.0,
    "plain_turn_first_audio_s": 3.5,
    "tool_turn_first_audio_s": 3.5,
}

image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_pip_install(requirements=[str(ROOT / "infra/modal/requirements-agent.txt")])
    .env({"PYTHONPATH": "/root"})
    # local files last: added at container start, no rebuild on code changes
    .add_local_dir(ROOT / "packages/core/src/avatar_core", "/root/avatar_core", ignore=["**/__pycache__"])
    .add_local_file(ROOT / "services/agent/scripts/synthetic_call.py", "/root/synthetic_call.py")
)

app = modal.App("avatar-talk-monitor")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("avatar-talk-agent")],
    schedule=modal.Cron("17 */3 * * *"),
    timeout=6 * 60,
    retries=0,
)
async def synthetic_check(llm: str = "groq/openai/gpt-oss-120b") -> dict:
    import argparse

    import synthetic_call

    args = argparse.Namespace(
        llm=llm, avatar="none", simli_face_id=None, photo_key=None, json=None,
        persona_id=None, api_url=None, api_key=None,
    )
    result = await synthetic_call.main(args)
    result.pop("transcript", None)

    problems = []
    for key, limit in THRESHOLDS.items():
        value = result.get(key)
        if not isinstance(value, (int, float)):
            problems.append(f"{key} missing ({value!r})")
        elif value > limit:
            problems.append(f"{key}={value}s > {limit}s")
    result["problems"] = problems
    print(json.dumps({"event": "synthetic_check", **result}))
    if problems:
        raise RuntimeError("synthetic check failed: " + "; ".join(problems))
    return result


@app.local_entrypoint()
def main() -> None:
    print(json.dumps(synthetic_check.remote(), indent=2))

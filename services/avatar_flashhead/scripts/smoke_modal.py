"""Run the GPU smoke test's publisher/subscriber from a clean Modal CPU container.

The local machine is heavily loaded; decoding 512x512@25fps there drops frames
and stalls the audio sender.  This app runs the same ``smoke_test.py`` logic in
us-east, next to the worker, so the measured fps reflects the worker rather than
the laptop.

    env -u MODAL_TOKEN_ID -u MODAL_TOKEN_SECRET modal run \
        services/avatar_flashhead/scripts/smoke_modal.py \
        --launch-url https://<workspace>--avatar-talk-flashhead-web.us-east.modal.run \
        --seconds 60
"""

import json
from pathlib import Path

import modal

app = modal.App("avatar-talk-flashhead-smoke")
HERE = Path(__file__).resolve().parent

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("livekit-agents==1.8.11", "numpy", "aiohttp")
    .add_local_file(str(HERE / "smoke_test.py"), "/root/smoke_test.py", copy=True)
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("avatar-talk-agent")],
    cpu=2,
    memory=4096,
    timeout=600,
    region="us-east",
)
def run(launch_url: str, seconds: float = 60.0, no_audio: bool = False) -> dict:
    import argparse
    import asyncio
    import sys

    sys.path.insert(0, "/root")
    import smoke_test

    args = argparse.Namespace(
        launch_url=launch_url,
        env_file="/root/.env",  # absent; LIVEKIT_* / INTERNAL_API_TOKEN come from the secret
        wav=None,  # synthetic speech-like signal
        seconds=seconds,
        no_audio=no_audio,
        join_timeout=120.0,
        json=None,
    )
    return asyncio.run(smoke_test.main(args))


@app.local_entrypoint()
def main(launch_url: str, seconds: float = 60.0, no_audio: bool = False):
    result = run.remote(launch_url, seconds, no_audio)
    print(json.dumps(result, indent=2))

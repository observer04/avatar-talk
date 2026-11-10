"""On/off switch for the cloud demo.

    infra/modal/demo.sh status            what is running now and what it costs per hour
    infra/modal/demo.sh on                get everything ready for a live demo
    infra/modal/demo.sh on --no-gpu       ...without a warm photoreal (FlashHead) GPU
    infra/modal/demo.sh on --check        ...then run one scripted test call (~$0.02)
    infra/modal/demo.sh off               stop everything that costs money while idle
    infra/modal/demo.sh off --dry-run     show what "off" would stop, without stopping it

What costs money while nobody is talking:
  - avatar-talk-agent keeps one CPU worker registered with LiveKit (~$0.13/h).
  - avatar-talk-flashhead costs nothing unless a GPU is kept warm (~$2.71/h).
  - avatar-talk-monitor has no schedule; it only runs on demand (`modal run`).
  - avatar-talk-api scales to zero on its own; it costs nothing while idle.
LiveKit, Deepgram, Groq, Cartesia and Simli bill only for calls.

"on" deploys any app that is missing (building the web app first), keeps one agent
worker and, unless --no-gpu, one FlashHead GPU warm, waits until they are up and
checks the API. "off" sets the agent and GPU to zero containers and stops the ones
still running. Volumes, secrets and the API URL are kept,
so "on" brings the same demo back. A later `modal deploy` restores the code defaults
(agent on, GPU cold).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODAL_DIR = ROOT / "infra/modal"

API, AGENT, GPU, MONITOR = "avatar-talk-api", "avatar-talk-agent", "avatar-talk-flashhead", "avatar-talk-monitor"
APP_FILES = {API: "api_app.py", AGENT: "agent_app.py", GPU: "flashhead_app.py", MONITOR: "monitor_app.py"}
CLASSES = {AGENT: "AgentWorker", GPU: "FlashHeadSession"}
COST_PER_HOUR = {AGENT: 0.13, GPU: 2.71, API: 0.06, MONITOR: 0.05}  # per running container, approximate


def _fix_modal_env() -> None:
    # A malformed MODAL_TOKEN_* in the shell overrides ~/.modal.toml; real token ids start with "ak-".
    tid = os.environ.get("MODAL_TOKEN_ID")
    if tid is not None and not tid.startswith("ak-"):
        os.environ.pop("MODAL_TOKEN_ID", None)
        os.environ.pop("MODAL_TOKEN_SECRET", None)


def _modal(*args: str, capture: bool = True) -> str:
    cmd = [sys.executable, "-m", "modal", *args]
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"`modal {' '.join(args)}` failed:\n{(result.stderr or '')[-2000:]}")
    return result.stdout or ""


def deployed_apps() -> set[str]:
    apps = json.loads(_modal("app", "list", "--json"))
    return {a["description"] for a in apps if a.get("state") == "deployed" and a["description"] in APP_FILES}


def containers() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in json.loads(_modal("container", "list", "--json")):
        if c.get("app_name") in APP_FILES:
            out.setdefault(c["app_name"], []).append(c["container_id"])
    return out


def set_min_containers(app: str, n: int) -> None:
    import modal

    modal.Cls.from_name(app, CLASSES[app])().update_autoscaler(min_containers=n)


def gpu_runners() -> int:
    import modal

    return modal.Cls.from_name(GPU, CLASSES[GPU])().run.get_current_stats().num_total_runners


def api_url() -> str:
    import modal

    return modal.Function.from_name(API, "web").get_web_url()


def deploy(app: str, env: dict[str, str] | None = None) -> None:
    if app == API:
        web = ROOT / "web"
        if not (web / "node_modules").exists():
            print("  installing web dependencies (npm ci)...")
            subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=web, check=True)
        print("  building the web app...")
        subprocess.run(["npm", "run", "build"], cwd=web, check=True, capture_output=True)
    print(f"  deploying {app}...")
    old = {k: os.environ.get(k) for k in (env or {})}
    os.environ.update(env or {})
    try:
        _modal("deploy", str(MODAL_DIR / APP_FILES[app]))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def wait_for(what: str, ready, timeout: float) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if ready():
            print(f"  {what}: ready after {time.monotonic() - start:.0f}s")
            return True
        time.sleep(5)
    print(f"  {what}: NOT ready after {timeout:.0f}s")
    return False


def status() -> None:
    deployed = deployed_apps()
    running = containers()
    total = 0.0
    gpus = gpu_runners() if GPU in deployed else 0
    print(f"{'app':<24}{'deployed':<10}{'running containers':<20}{'≈ $/hour now'}")
    for app in APP_FILES:
        n = len(running.get(app, []))
        if app == GPU:
            # The app also has a small CPU /launch endpoint; only GPU containers cost much.
            cost = gpus * COST_PER_HOUR[GPU] + (n - gpus) * COST_PER_HOUR[API]
            label = f"{n} ({gpus} GPU)"
        else:
            cost = n * COST_PER_HOUR[app]
            label = str(n)
        total += cost
        print(f"{app:<24}{'yes' if app in deployed else 'no':<10}{label:<20}{cost:.2f}")
    print(f"{'total':<54}{total:.2f}")
    if MONITOR in deployed:
        print(f"note: {MONITOR} is deployed; `demo.sh off` stops it")
    if gpus:
        print(f"note: {gpus} FlashHead GPU(s) running: a call in progress, or kept warm by `demo.sh on`")
    if API in deployed:
        print(f"demo URL: {api_url()}")


def on(gpu: bool, check: bool) -> None:
    deployed = deployed_apps()
    print("1/4 deployments")
    for app in (API, AGENT, GPU):
        if app in deployed:
            print(f"  {app}: deployed")
        else:
            deploy(app)

    print("2/4 warm containers")
    set_min_containers(AGENT, 1)
    print("  agent: 1 worker kept warm")
    if gpu:
        set_min_containers(GPU, 1)
        print("  FlashHead: 1 GPU kept warm")
    else:
        print("  FlashHead: cold (first photoreal call takes ~25 s to show the face)")

    print("3/4 waiting for containers")
    ok = wait_for("agent worker", lambda: bool(containers().get(AGENT)), 180)
    if gpu:
        ok &= wait_for("FlashHead GPU (model loaded)", lambda: gpu_runners() >= 1, 420)

    print("4/4 API health")
    url = api_url()
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=60) as r:
            print(f"  {url}/health -> {r.status}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  {url}/health failed: {exc}")

    if check:
        print("test call (voice only, via the synthetic caller)...")
        print(_modal("run", str(MODAL_DIR / "monitor_app.py"))[-1500:])

    hourly = COST_PER_HOUR[AGENT] + (COST_PER_HOUR[GPU] if gpu else 0)
    print()
    print(("READY" if ok else "NOT FULLY READY (see above)") + f" · {url}")
    print(f"While on this costs about ${hourly:.2f}/hour plus usage. Run `infra/modal/demo.sh off` afterwards.")
    # Give the agent a moment to register with LiveKit before the first call.
    time.sleep(5)


def off(dry_run: bool = False) -> None:
    deployed = deployed_apps()
    if dry_run:
        print("DRY RUN: would set min containers to 0 for", [a for a in (AGENT, GPU) if a in deployed])
        for app, ids in containers().items():
            if app != MONITOR:
                print(f"DRY RUN: would stop {app} containers {ids}")
        if MONITOR in deployed:
            print(f"DRY RUN: would stop {MONITOR}")
        if GPU in deployed and gpu_runners():
            print("DRY RUN: warning, a FlashHead GPU is running: stopping it ends any call in progress")
        return
    print("1/3 scale to zero")
    for app in (AGENT, GPU):
        if app in deployed:
            set_min_containers(app, 0)
            print(f"  {app}: min containers 0")
    print("2/3 stop running containers")
    for app, ids in containers().items():
        if app == MONITOR:
            continue
        for cid in ids:
            _modal("container", "stop", "-y", cid)
            print(f"  stopped {app} container {cid}")
    if MONITOR in deployed:
        _modal("app", "stop", "-y", MONITOR)
        print(f"  {MONITOR}: stopped")
    print("3/3 checking")
    wait_for("all containers gone", lambda: not containers(), 120)
    print("OFF · nothing is running; the API wakes up only if someone opens the URL (free while idle).")


def main() -> None:
    _fix_modal_env()
    ap = argparse.ArgumentParser(description="Turn the cloud demo on or off.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_on = sub.add_parser("on", help="get everything ready for a demo")
    p_on.add_argument("--no-gpu", action="store_true", help="don't keep a FlashHead GPU warm")
    p_on.add_argument("--check", action="store_true", help="run one scripted test call afterwards")
    p_off = sub.add_parser("off", help="stop everything that costs money while idle")
    p_off.add_argument("--dry-run", action="store_true", help="only show what would be stopped")
    sub.add_parser("status", help="show what is running")
    args = ap.parse_args()
    if args.cmd == "on":
        on(gpu=not args.no_gpu, check=args.check)
    elif args.cmd == "off":
        off(dry_run=args.dry_run)
    else:
        status()


if __name__ == "__main__":
    main()

# Deploying on Modal

Each service is its own Modal app. Media flows through LiveKit Cloud; Modal runs the compute.

| App | File | What it runs | Scaling |
| --- | --- | --- | --- |
| `avatar-talk-agent` | `agent_app.py` | LiveKit Agents worker (CPU, 2 vCPU / 4 GB) | `min_containers` warm workers; LiveKit balances calls across them |
| `avatar-talk-api` | `api_app.py` | Control-plane API plus the built web app | 1 container while on SQLite; raise once on Postgres |
| `avatar-talk-monitor` | `monitor_app.py` | Synthetic caller every 3 h; fails loudly on regressions | cron |
| `avatar-talk-flashhead` | `services/avatar_flashhead` | GPU avatar worker (after the spike) | per call, scales to zero |

## Modal CLI note for this machine

The shell exports `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` values that override `~/.modal.toml` and are rejected as malformed. Prefix Modal commands with `env -u MODAL_TOKEN_ID -u MODAL_TOKEN_SECRET`, or fix the exported values.

## Secrets

Secrets are created from JSON so values never appear on a command line:

```bash
modal secret create avatar-talk-agent --from-json agent.json --force
modal secret create avatar-talk-api   --from-json api.json   --force
```

- **`avatar-talk-agent`:**
  - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
  - `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`
  - `SIMLI_API_KEY`
  - `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
  - `API_URL`, `INTERNAL_API_TOKEN`
  - `TTS_FALLBACKS`, optional (default `deepgram/aura-2-andromeda-en`)
  - `FLASHHEAD_LAUNCH_URL`, optional
- **`avatar-talk-api`:**
  - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
  - `INTERNAL_API_TOKEN` (the same value as the agent's), `API_KEYS` (client keys), `CORS_ORIGINS`
  - `SIMLI_API_KEY`
  - `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
  - For production: `DATABASE_URL` (Postgres), and `STORAGE_BACKEND=s3` with `S3_BUCKET`, `S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.

The generated `INTERNAL_API_TOKEN` and the client `AVATAR_API_KEY` are kept in the repo-root `.env`, which is gitignored.

## Demo on/off switch

`infra/modal/demo.sh` turns the demo on and off without redeploying. It changes each app's minimum container count at runtime (`update_autoscaler`) and stops containers that are still running.

| Command | What it does |
| --- | --- |
| `demo.sh on` | Deploys any missing app (building the web app first), keeps 1 agent worker and 1 FlashHead GPU warm, waits until both are up, and checks `/health` |
| `demo.sh on --no-gpu` | Same, but the GPU stays cold (~$0.13/h instead of ~$2.84/h) |
| `demo.sh on --check` | Also places one scripted voice call through `monitor_app.py` |
| `demo.sh on --monitor` | Also re-enables the 3-hourly monitor schedule |
| `demo.sh status` | Deployed apps, running containers (GPU vs CPU) and the approximate hourly cost |
| `demo.sh off` | Agent and GPU to 0 containers, stops running containers, stops the monitor schedule. Volumes, secrets and the URL stay. `--dry-run` previews it |

`off` ends any call in progress. A later `modal deploy` restores the code defaults (agent worker on, GPU cold).

## Deploy

```bash
modal deploy infra/modal/agent_app.py
(cd web && npm run build) && modal deploy infra/modal/api_app.py
modal deploy infra/modal/monitor_app.py
modal run infra/modal/monitor_app.py      # one synthetic call now, prints latency
```

When `requirements-*.txt` changes, re-export them after changing dependencies:

```bash
uv export --package avatar-agent --no-dev --no-emit-workspace --frozen --no-hashes -o infra/modal/requirements-agent.txt
uv export --package avatar-api   --no-dev --no-emit-workspace --frozen --no-hashes -o infra/modal/requirements-api.txt
```

## Observability

- **Traces:** Langfuse groups one session per call, keyed by the `session_id` from `SessionMetadata`. Spans include `agent_turn`, `llm_request`, `user_speaking`/`agent_speaking`, tool calls and `event_loop_blocked`. For programmatic queries use the v2 API: `GET /api/public/v2/observations` (the v1 endpoints are removed on 2026-11-16).
- **Logs:** `modal app logs avatar-talk-agent`. These are JSON with `service` and `ctx` (`session_id`, `room`, `persona_id`). Each finished call logs a `session summary` line with per-turn latency, usage and approximate cost.
- **Session reports:** the agent posts each summary to `POST /internal/sessions/{id}/report`, and you can read it with `GET /v1/sessions/{id}`.
- **Synthetic monitor:** `avatar-talk-monitor` runs are marked failed when latency thresholds are breached. Turn on failure notifications for the app in the Modal dashboard.

## Region

The agent is pinned to `region="us-east"`, next to Deepgram, Groq and Cartesia. LiveKit reports it as "US East B". Measured from the cloud monitor:

| Agent region | Plain turn → first audio | Tool turn → filler | Barge-in |
| --- | --- | --- | --- |
| Europe ("Germany 2", unpinned) | 2.21 s | 0.96 s | 1.43 s |
| `us-east` (2 runs) | 1.40–1.82 s | 1.21–1.95 s | 1.23–1.33 s |

The monitor runs on Modal in the US too. A user in India adds the LiveKit backbone hop from the India edge to the US, roughly 0.25 s round trip.

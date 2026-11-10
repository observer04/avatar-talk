# Avatar Talk

A general-purpose **digital human** you talk to face to face in the browser. You pick a persona (or create one from a single photo), press call, and talk. It listens, thinks, uses tools (time, weather, web search), and answers with a voice and a lip-synced face, in real time, and you can interrupt it mid-sentence like a person.

It is a cloud-native system of small services: a serverless control-plane API, a fleet of real-time voice agents, and a GPU avatar renderer that scales to zero. They run on **Modal**, with **LiveKit Cloud** as the WebRTC media plane and managed AI APIs for speech and language.

- **Live demo:** `https://observer04--avatar-talk-api-web.us-east.modal.run` (needs a client API key, entered once in the page)
- **Project report:** [`docs/REPORT.md`](docs/REPORT.md)
- **Deploy runbook:** [`infra/modal/README.md`](infra/modal/README.md) · **API reference:** [`docs/api.md`](docs/api.md) · **Measurements:** [`docs/benchmarks.md`](docs/benchmarks.md)

## Architecture

### System overview

Three planes, kept separate so each can scale and fail on its own:

- **Control plane:** the API owns personas, photos, sessions, auth and spend limits. It never touches audio.
- **Media plane:** LiveKit Cloud carries all audio, video and data between the browser, the agent and the avatar, over WebRTC with a global edge and TURN.
- **Compute plane:** stateless agent workers (CPU) run the conversation; GPU workers render the face only while a call needs one.

```mermaid
flowchart LR
    U["Browser<br/>React + LiveKit SDK"]

    subgraph LK["LiveKit Cloud"]
        ROOM(("Room<br/>per call"))
    end

    SIMLI["Simli<br/>managed avatar"]

    subgraph Modal["Modal · serverless compute"]
        API["avatar-talk-api<br/>FastAPI + web app"]
        VOL[("Volume<br/>SQLite + photos")]
        AGENT["avatar-talk-agent<br/>voice agent<br/>CPU · always warm"]
        GPU["avatar-talk-flashhead<br/>photoreal avatar<br/>GPU · scales to zero"]
        MON["avatar-talk-monitor<br/>cron test caller"]
    end

    subgraph AI["Managed AI APIs"]
        STT["Deepgram Flux · STT"]
        LLM["Groq LLM + fallbacks"]
        TTS["Cartesia TTS + fallback"]
        TOOLS["Tavily · Open-Meteo"]
    end

    OBS["Langfuse<br/>traces"]

    U -- "① start session<br/>HTTPS" --> API
    API -- "② room + dispatch" --> ROOM
    U <-- "③ voice + video<br/>WebRTC" --> ROOM
    ROOM <--> AGENT
    ROOM <--> GPU
    ROOM <--> SIMLI
    API --- VOL
    AGENT -- "persona<br/>report" --> API
    AGENT -- "launch" --> GPU
    AGENT --> AI
    AGENT -. "spans" .-> OBS
    MON -. "test call" .-> ROOM
```

### What happens when you press "Call"

The API creates a LiveKit room and **explicitly dispatches** an agent into it with the session metadata, then hands the browser a short-lived token. The agent loads the persona, attaches the avatar backend and greets you.

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant API as Control-plane API
    participant LK as LiveKit Cloud
    participant A as Agent worker
    participant G as FlashHead GPU worker

    U->>API: POST /v1/sessions {persona_id}
    API->>API: auth, rate limit, daily spend cap, avatar ready?
    API->>LK: create room + dispatch "avatar-agent" (session_id, persona_id)
    API-->>U: livekit_url, token (15 min), session_id
    U->>LK: join room (WebRTC)
    LK->>A: job request
    A->>API: GET /internal/personas/{id}
    A->>G: POST /launch {room, token, persona photo}
    alt GPU warm (joins in about 5 s)
        G->>LK: join as avatar
        A->>A: route TTS audio to the avatar
    else GPU cold
        A->>A: start voice-only after 8 s
        G-->>LK: joins later (about 25 s)
        A->>A: switch audio to the avatar between replies
    end
    A->>LK: greeting (voice, then lip-synced video)
    LK-->>U: audio + video + transcript
    Note over U,A: conversation turns
    U->>LK: hang up
    A->>API: POST /internal/sessions/{id}/report (latency, usage, cost)
    G->>G: agent left, worker exits, GPU scales to zero
```

### One conversational turn

Latency is the product. Each stage streams into the next, nothing waits for a whole sentence, and every provider has a fallback.

```mermaid
flowchart LR
    MIC["user speech"] --> VAD["Silero VAD<br/>(barge-in detection)"]
    MIC --> FLUX["Deepgram Flux<br/>streaming STT +<br/>semantic end of turn"]
    FLUX -- "eager end of turn" --> PRE["preemptive<br/>generation"]
    PRE --> LLM["LLM (streaming)<br/>Groq, then OpenRouter, then OpenAI"]
    LLM -- "tool call" --> T["tools<br/>time, weather, web search"]
    T -- "filler: Let me check." --> TTS
    T --> LLM
    LLM -- "sentences" --> CUES["cue stripper<br/>mood and gesture tags"]
    CUES --> TTS["TTS (streaming)<br/>Cartesia, then Deepgram Aura-2"]
    CUES -. "cues (data topic)" .-> B3D["browser 3D avatar"]
    TTS --> AV{"avatar<br/>backend"}
    AV -- "flashhead / simli" --> FACE["lip-synced video<br/>+ audio"]
    AV -- "browser3d / none" --> VOICE["agent audio track"]
    VAD -- "user talks over agent" --> CLR["interrupt: stop TTS,<br/>clear avatar buffer"]
```

Measured on the deployed stack (agent in Modal `us-east`, voice only): the agent starts answering **1.4–1.9 s** after you stop talking, a tool turn plays its filler in **1.2–2.0 s**, and barge-in silences it in **1.2–1.4 s** as heard by the caller. Details in [`docs/benchmarks.md`](docs/benchmarks.md).

### Swappable avatars behind one interface

A persona chooses its face. Every backend implements `attach(session, room, persona)`, so the agent code does not change when a renderer is added.

```mermaid
classDiagram
    class AvatarBackendImpl {
        <<protocol>>
        name
        wants_cues
        attach(session, room, persona) bool
        aclose()
    }
    class SimliBackend {
        managed photoreal face
        simli.AvatarSession
    }
    class FlashHeadBackend {
        self-hosted photoreal face
        launches a Modal GPU worker
        late-join audio handoff
    }
    class Browser3DBackend {
        Three.js TalkingHead in the browser
        LLM body-language cues
    }
    class NoAvatarBackend {
        voice only
    }
    AvatarBackendImpl <|.. SimliBackend
    AvatarBackendImpl <|.. FlashHeadBackend
    AvatarBackendImpl <|.. Browser3DBackend
    AvatarBackendImpl <|.. NoAvatarBackend
```

| Backend | Where it runs | From one photo | Video | Added per-turn latency | Cost |
| --- | --- | --- | --- | --- | --- |
| `flashhead` | our Modal GPU (SoulX-FlashHead Lite, L40S) | yes | 25 fps, 512×512 | ~3–4 s | ~$2.71/h while in a call, $0 idle |
| `simli` | Simli (managed) | yes (paid plan) or preset faces | ~23–25 fps, 768×768 | ~0.7–1.0 s | ~$0.05/min |
| `browser3d` | the user's browser (Three.js) | no (3D character) | rendered locally | none | $0 |
| `none` | – | – | – | none | $0 |

The FlashHead GPU worker never leaves the user waiting: if the GPU is cold, the call starts voice-only and the agent moves its audio to the face when it joins. A worker that starts after the call has ended leaves within 30 s instead of holding a GPU.

```mermaid
stateDiagram-v2
    [*] --> Launching: agent POSTs /launch
    Launching --> AvatarLive: avatar joins within 8 s (warm GPU)
    Launching --> VoiceOnly: not joined after 8 s (cold GPU)
    VoiceOnly --> Switching: avatar joins (up to 180 s)
    Switching --> AvatarLive: agent not speaking, replace_audio_tail()
    VoiceOnly --> [*]: never joined, stays voice-only
    AvatarLive --> [*]: call ends, GPU worker exits
```

### Deployment on Modal

All infrastructure is Python code in [`infra/modal/`](infra/modal/): images, hardware, scaling, regions, secrets, volumes and schedules are declared next to the code that uses them.

```mermaid
flowchart TB
    subgraph Apps["Modal apps (modal deploy infra/modal/*.py)"]
        direction LR
        A1["avatar-talk-api<br/>asgi_app, 1 container<br/>Volume /data"]
        A2["avatar-talk-agent<br/>cls, 1 always-warm worker<br/>region us-east, 24 h timeout"]
        A3["avatar-talk-flashhead<br/>GPU cls: L40S or H100, region us<br/>min 0, max 10, scaledown 60 s<br/>+ small CPU /launch endpoint"]
        A4["avatar-talk-monitor<br/>function, Cron 17 */3 * * *"]
    end
    S1[["Secret avatar-talk-api"]] --> A1
    S2[["Secret avatar-talk-agent"]] --> A2 & A3 & A4
    V1[("Volume avatar-talk-data<br/>SQLite + photos")] --- A1
    V2[("Volume flashhead-weights<br/>model weights")] --- A3
    GH["GitHub Actions CI<br/>ruff, pytest, tsc, vite build"] -. "checks every push;<br/>deploy is modal deploy" .-> Apps
```

| Cloud feature | How it is used |
| --- | --- |
| Serverless containers | API, agent and monitor are Modal functions and classes built from code-defined images |
| Autoscaling and scale to zero | GPU avatar workers start per call and stop 60 s after it; up to 10 concurrent sessions |
| GPU acceleration with fallback | `gpu=["L40S", "H100"]` in any US region, so a call isn't stuck waiting for one GPU type |
| Region placement | the agent is pinned to `us-east` next to Deepgram, Groq and Cartesia (plain turn 2.21 s from the EU vs 1.40–1.82 s from us-east) |
| Persistent storage | Modal Volumes for the SQLite database and photos, and for the 1.3B-parameter model weights |
| Secrets management | Modal Secrets per service, created from JSON files so values never appear on a command line |
| Scheduled jobs | a cron synthetic caller checks latency every 3 hours and fails loudly on regressions |
| Managed media (PaaS) | LiveKit Cloud SFU, TURN and explicit agent dispatch |
| Observability (SaaS) | OpenTelemetry traces to Langfuse, JSON logs with correlation IDs, per-session cost reports |

### Observability

```mermaid
flowchart LR
    A["agent"] -- "OTel spans: agent_turn,<br/>llm_request, tts, tool calls" --> LF["Langfuse<br/>(one session per call)"]
    API["api"] -- "FastAPI spans" --> LF
    A -- "JSON logs with<br/>session_id, room, persona_id" --> ML["Modal logs"]
    A -- "session summary: per-turn<br/>latency, usage, cost" --> API
    API --> DB[("sessions table")]
    MON["monitor cron"] -- "latency thresholds" --> ALERT["failed run raises<br/>a Modal alert"]
    A -. "optional" .-> OTLP["any OTLP backend<br/>(Jaeger, Tempo) or Sentry"]
```

## Repository layout

```
packages/core/             avatar_core: shared settings, Persona/Session schemas, telemetry, logging, cost model
services/agent/            LiveKit Agents worker: pipeline, persona agent, tools, avatar backends, observability
  scripts/synthetic_call.py  headless caller for e2e tests, benchmarks and the monitor
services/api/              FastAPI control plane: personas, photos, sessions, auth, spend cap (SQLAlchemy + Alembic)
services/avatar_flashhead/ GPU avatar worker: FlashHead model wrapper, video generator, LiveKit runner, launcher
web/                       React + LiveKit components: persona gallery, create persona, call view, transcript
infra/modal/               Modal apps (api, agent, flashhead, monitor) and the deploy runbook
infra/docker-compose.dev.yml  local Postgres + Jaeger
docs/                      report, API reference, benchmarks, FlashHead GPU spike
legacy/phase0/             first prototype (Pipecat, local CPU models, 3D avatar)
```

## Run it

### Prerequisites

- [uv](https://docs.astral.sh/uv/) with Python 3.13, and Node 20+
- Keys: LiveKit Cloud, Deepgram, Cartesia and Groq are required. OpenRouter or OpenAI (LLM fallbacks), Tavily (web search), Langfuse (traces), Simli (managed avatar) and Modal (cloud deploy) are optional.

### Locally

```bash
uv sync --all-packages                  # Python workspace (core, agent, api)
cp .env.example .env                    # fill in the keys
uv run uvicorn api.main:app --port 8000 # control-plane API (creates the SQLite DB on start)
uv run python -m agent.main dev         # agent worker, registers with LiveKit Cloud
cd web && npm install && npm run dev    # http://localhost:5173 (proxies /v1 to :8000)
```

Leave `API_KEYS` empty for local use (no auth), or set it and paste the key when the page asks. `npm run mock` in `web/` serves a stand-in API for UI work without any keys.

### On the cloud (Modal)

```bash
modal secret create avatar-talk-agent --from-json agent.json   # keys listed in infra/modal/README.md
modal secret create avatar-talk-api   --from-json api.json
modal deploy infra/modal/agent_app.py
modal deploy infra/modal/flashhead_app.py                       # optional: self-hosted photoreal avatar
(cd web && npm run build) && modal deploy infra/modal/api_app.py
modal deploy infra/modal/monitor_app.py
```

The full runbook (secrets per service, regions, logs, keep-warm, costs) is in [`infra/modal/README.md`](infra/modal/README.md).

### Demo on/off switch

Turn the deployed demo on before showing it and off afterwards so idle workers don't use credit:

```bash
infra/modal/demo.sh on            # deploy anything missing, keep 1 agent worker + 1 FlashHead GPU warm, check health
infra/modal/demo.sh on --no-gpu   # same without the GPU (photoreal face takes ~25 s to appear on the first call)
infra/modal/demo.sh on --check    # also place one scripted test call (~$0.02)
infra/modal/demo.sh status        # what is running and roughly what it costs per hour
infra/modal/demo.sh off           # scale everything to zero and stop the monitor schedule (--dry-run to preview)
```

While on it costs about $2.84/hour with the warm GPU, or $0.13/hour without it, plus per-call usage. When off, nothing runs; the API wakes only if someone opens the URL.

### Tests and a synthetic call

```bash
uv run pytest -q            # 55 tests: core, agent, api, FlashHead worker
uv run ruff check .
cd web && npx tsc --noEmit -p tsconfig.app.json && npm run build

# A scripted caller joins a room, asks a tool question and a plain one, interrupts, and prints latency:
uv run python services/agent/scripts/synthetic_call.py --persona-id <per_...> \
    --api-url https://observer04--avatar-talk-api-web.us-east.modal.run --api-key "$AVATAR_API_KEY"
```

## Configuration

All settings are environment variables (Modal Secrets in the cloud, `.env` locally); see [`.env.example`](.env.example). Each persona also carries its own STT, LLM, voice, avatar backend and tools, so one deployment can host very different digital humans.

## Status and limits

- Voice-only turns take 1.4–1.9 s end to end; the original target was 1.2 s.
- FlashHead adds ~3–4 s per turn (0.96 s audio chunks plus frame pacing); Simli adds ~0.7–1.0 s.
- A cold FlashHead GPU shows its face ~25 s into a call (voice-only until then).
- Custom Simli faces need a paid Simli plan; Simli's preset faces work on the free tier.
- The API uses SQLite on a Modal Volume (single writer). The code also supports Postgres and S3 (`DATABASE_URL`, `STORAGE_BACKEND=s3`) for scaling out.
- The FlashHead weights licence must be reviewed before any commercial use.

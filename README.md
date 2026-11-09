# Avatar Talk

A general-purpose digital human you talk to face to face in the browser. It listens, thinks, speaks and moves a 3D face and hands in real time. It is built from open-source parts and free tiers so the MVP costs about $0 to run.

```
Browser (React + TalkingHead + HeadAudio)            Python bot (Pipecat, CPU only)
  mic ───────────── WebRTC (peer-to-peer) ─────────►  Silero VAD + Smart Turn v3   (local)
  3D avatar ◄── bot audio + {mood, gesture} cues ───  Moonshine STT                (local)
  lip-sync computed in the browser from the audio     Groq LLM (gpt-oss-120b)      (free tier)
                                                      Kokoro TTS int8              (local)
```

- **Lip-sync without a server GPU.** HeadAudio (an AudioWorklet) turns the bot's audio into visemes in the browser, and TalkingHead (Three.js) renders the avatar, idle motion, moods and gestures.
- **Body language from the LLM.** The model writes inline cues like `[mood:happy]` or `[gesture:thumbup]`. `server/avatar_tags.py` strips them before TTS and sends them to the browser.
- **Swappable providers.** STT, LLM, TTS and the avatar renderer are each chosen in `.env` (see `server/services.py`). A photoreal renderer (Simli, or self-hosted MuseTalk/FlashHead) can replace the 3D one later via `AVATAR_MODE`.
- **Plug-in tools.** Drop a module with a `TOOLS` list into `server/tools/` and enable it in `ENABLED_TOOLS`. Mark slow tools with `@slow` and the avatar says "Let me check." while they run.

## Quick start (local)

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, a free [Groq API key](https://console.groq.com/keys). No GPU needed.

```bash
cp .env.example .env            # add GROQ_API_KEY (or export it in your shell)
cd server && uv run app.py      # first start downloads Kokoro + Moonshine (~0.5 GB, once)
cd web && npm install && npm run dev
```

Open http://localhost:5173, click **Start conversation** and allow the microphone. Headphones help; the browser's echo cancellation handles speakers too.

## Configuration

Everything lives in `.env` (see `.env.example`). The main switches:

| Variable | Default | Options |
| --- | --- | --- |
| `LLM_PROVIDER` | `groq` | `groq`, `google` (Gemini free tier) |
| `STT_PROVIDER` | `moonshine` | `moonshine` (local), `groq` (Whisper, free tier) |
| `TTS_PROVIDER` | `kokoro` | `kokoro` (local), `groq` (Orpheus; accept its terms in the Groq console first) |
| `AVATAR_MODE` | `client3d` | `client3d`; `simli` / `selfhosted` reserved for the photoreal phase |
| `ENABLED_TOOLS` | `basics` | comma-separated modules in `server/tools/` |
| `ACCESS_TOKEN` | empty | set for any public deployment; open the page with `?key=<token>` |

The persona and body-language rules are in `server/persona/system_prompt.md`.

## Measured performance (laptop: Ryzen 5 7530U, no GPU)

From `server/scripts/e2e_call.py`, a headless WebRTC client that speaks to the bot:

| Stage | Time |
| --- | --- |
| End of user speech → bot starts speaking | ~1.9–2.1 s |
| Moonshine STT (medium) | 0.5–1.0 s |
| Groq LLM first token | 0.3–0.6 s |
| Kokoro int8 first audio | 0.4–0.6 s |
| Barge-in (user talks over the bot → bot stops) | ~0.5 s |

Next latency wins, in order: streaming STT (transcribe while the user is still talking), a faster LLM (`openai/gpt-oss-20b`), and a faster CPU host.

## Tests

```bash
cd server && uv run pytest                 # unit tests (tag parsing, TTS chunking, config)
uv run app.py & uv run scripts/e2e_call.py # full voice round-trip incl. tool call and barge-in
cd web && npm run build                    # type-check + production build
```

## Hosted demo on Modal

`server/modal_app.py` serves the UI and signalling from one URL and runs each conversation in its own CPU container that scales to zero.

```bash
cd web && npm run build
modal secret create avatar-talk GROQ_API_KEY=... ACCESS_TOKEN=<passphrase>
cd ../server && modal deploy modal_app.py   # open <url>/?key=<passphrase>
```

If a network blocks the peer-to-peer connection, add a TURN relay to the secret (`TURN_URL`, `TURN_USERNAME`, `TURN_CREDENTIAL`; e.g. the free [Open Relay](https://www.metered.ca/tools/openrelay/) tier).

## Roadmap

See `docs/` for the research behind these choices. Next phases: photoreal renderer (Simli free minutes, then self-hosted MuseTalk or SoulX-FlashHead on a Modal GPU), memory/RAG, camera and screen understanding, Indic voices (Azure Speech free tier, Sarvam, IndicF5), and joining Zoom/Meet calls.

## Licences

Code: your choice. Components: Pipecat (BSD-2), TalkingHead and HeadAudio (MIT), Kokoro (Apache-2.0), Moonshine (MIT), Silero VAD (MIT). The sample avatar `web/public/avatars/brunette.glb` is CC BY-NC 4.0, so it's fine for demos but not commercial use; swap in your own avatar before shipping.

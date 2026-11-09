# Benchmarks

## Voice stack on LiveKit (Spike B, 2026-11-09)

Setup:
- **Tool:** `services/agent/scripts/synthetic_call.py`. A scripted caller joins a LiveKit Cloud room (India South) and speaks Cartesia-synthesised questions in real time.
- **Agent:** worker running on a laptop in India.
- **Stack:** Deepgram Flux STT with Flux end-of-turn detection, Cartesia `sonic-3`, the LLM varies.
- **Caveat:** the caller and the agent share the same home connection, so every audio packet makes two round trips through LiveKit Cloud. That adds roughly 0.5–1 s to the "heard" numbers compared with a real user talking to a cloud-hosted agent.

What the columns mean:
- **Server e2e:** LiveKit's `ChatMessage.metrics.e2e_latency`, from the user finishing speaking to the agent starting its reply.
- **Heard:** what the synthetic user hears, from the end of its speech to the first audible agent audio.

| LLM | Turn | End-of-turn | LLM TTFT | TTS TTFB | Server e2e | Heard |
| --- | --- | --- | --- | --- | --- | --- |
| Groq `gpt-oss-120b` | plain question | 1.10–1.27 s | 0.46–0.70 s | 0.15–0.24 s | 1.47–2.03 s | 2.5–3.0 s |
| Groq `gpt-oss-120b` | weather tool (filler spoken) | 0.61–0.67 s | 0.51–0.84 s | 0.20–0.30 s | 4.2–4.3 s | 2.07–2.23 s (filler) |
| Groq `gpt-oss-20b` | plain question | 1.64 s | 0.95 s | 0.23 s | 2.34 s | 4.03 s |
| OpenRouter `gpt-4.1-mini` | plain question | 1.10 s | 0.89 s (2.06 s cold) | 0.48 s | 2.48 s | 4.21 s |
| OpenAI `gpt-4.1-mini` / `gpt-5.4-mini` | — | — | — | — | — | not measured: the OpenAI account has no credits (HTTP 429) |

Barge-in, measured from the user starting to talk over the agent to the agent going quiet at the listener:

| Interruption mode | Heard |
| --- | --- |
| `adaptive` (ML; ignores backchannels like "mm-hmm") | 2.13 s |
| `vad`, min duration 0.3 s | 1.47 s |

Decisions:
- **LLM:** Groq `gpt-oss-120b` is the default, with fallbacks `openrouter/openai/gpt-4.1-mini` then `openai/gpt-4.1-mini`.
- **Interruption:** the `vad` mode (min duration 0.4 s) is the default; `adaptive` is available via `INTERRUPTION_MODE`.
- **Tool fillers:** slow tools speak a filler ("Let me check.") deterministically through `session.say()`. Prompting `gpt-oss` to do it was ignored.
- **Preemptive generation:** the LLM and TTS start on Flux's eager end-of-turn signal.

Known issues:
- **Early end-of-turn:** Flux sometimes ends the turn early when a sentence is grammatically complete before the speaker is done. It heard "…in London right" and cut "now?" in 2 of 5 runs. Tune `eot_threshold` with real users.
- **Cartesia concurrency:** the plan rejects parallel requests (HTTP 429 on 3 concurrent syntheses). That caps how many concurrent sessions are possible; check the plan limits before load testing.

## Simli avatar (deployed)

Setup:
- **Stack:** control-plane API and web on Modal (`https://observer04--avatar-talk-api-web.us-east.modal.run`), agent worker on Modal `us-east`, Simli Trinity face built from the persona photo through `PUT /v1/personas/{id}/photo`.
- **Caller:** `services/agent/scripts/synthetic_call.py --persona-id …` (API → LiveKit dispatch → agent).

Result: **blocked by the Simli account quota — no avatar metrics could be measured.**

| Step | Result |
| --- | --- |
| `POST /v1/personas` (`avatar_backend=simli`) | 201, status `pending` |
| `PUT /v1/personas/{id}/photo` (512×512 normalised JPEG from the test portrait) | 200 after 20.0 s, status `failed` |
| `GET /v1/personas/{id}` | 200, same `avatar.error` |
| `GET https://api.simli.ai/faces` | 200, 0 faces on the account |
| Face build time | not reached (request rejected) |
| Avatar join time / video fps / avatar e2e latency | not measured |

Exact API error (`avatar.error`):
`Simli face creation failed: HTTP 403: {"detail":"You have no Trinity-2 generations left. Your plan's monthly generations renew with your subscription, and Pro and Scale plans can buy more on https://app.simli.com/billing/generations"}`

The key authenticates (the 403 is a quota response, not 401), and the account has no pre-existing face to reuse, so no avatar call was attempted. The same deployed loop was verified voice-only with a `browser3d` persona: join→greeting 2.57 s, tool turn 1.42 s, plain turn 2.42 s, barge-in 1.17 s (Groq `gpt-oss-120b`). Re-run the Simli leg after the plan's generations renew or a face is added to the account.

### Preset faces: the free tier works (2026-11-10)

Simli's free plan separates **streaming minutes** (≈200 on the account) from **Trinity-2 generations** (face builds from your own photo — zero on free). The [default face library](https://docs.simli.com/api-reference/preset-faces) needs **no generation**: a preset face ID is passed straight to the avatar session. A generation is only needed to clone a custom photo, so the avatar path is fully testable on free minutes.

Two synthetic calls (direct dispatch, preset face `bf13113a-…` "Anna", Groq `gpt-oss-120b`) both ran the avatar end to end:

| Metric | Voice only | Simli avatar |
| --- | --- | --- |
| join → greeting | 2.57 s | 4.80–4.96 s |
| tool turn → first audio | 1.42 s | 1.90–1.98 s |
| avatar video | — | 24.9 fps, 768×768, first frame 4.4–4.6 s after join |

The avatar published lip-synced audio+video for the whole ~113 s session; the agent's cost model put avatar time at ≈$0.09 (≈$0.05/min, so the free minutes ≈ $10 of credit). The avatar adds ≈2.3 s to session start and ≈0.5 s per turn.

**Turns 3–4 failed in both runs — Cartesia, not Simli.** TTS synthesis returned no audio; the underlying error is HTTP 402 `Model credits limit reached` — the Cartesia account is out of credits (confirmed with a direct API probe). The LLM answers existed; only synthesis failed. Until Cartesia is topped up or a TTS fallback is wired in, sessions lose audio after ~2 turns.

### TTS fallback (Deepgram Aura-2)

Wired on 2026-11-10: `make_tts` still builds the persona's voice first (Cartesia `sonic-3`), then appends the global `TTS_FALLBACKS` chain (default `deepgram/aura-2-andromeda-en`) and wraps both in `livekit.agents.tts.FallbackAdapter`. A misconfigured ref is logged and skipped, like the LLM chain. For Aura-2 the voice is the model name, so `deepgram/aura-2-thalia-en` etc. work without a separate voice id. The fallback voice differs from the persona voice; Cartesia stays primary pending a top-up.

The synthetic caller's own questions also fall back to Deepgram now (48 kHz `linear16`): the monitor container has no `~/.cache/avatar_talk/synth`, so its Cartesia-synthesised questions were failing with the same 402 before the agent was ever reached.

End-to-end, direct dispatch, Groq `gpt-oss-120b`:

| Metric | Voice only | Simli avatar (run 1) | Simli avatar (run 2) |
| --- | --- | --- | --- |
| join → greeting | 3.53 s | 6.39 s | 7.64 s |
| tool turn → first audio | 1.72 s | 3.46 s | 1.34 s |
| plain turn → first audio | 2.39 s | 4.86 s | 2.54 s |
| barge-in (heard) | 1.68 s | not measured* | not measured* |
| turns with audio | 4/4 | 4/4 | 4/4 |
| agent e2e avg | 1.61 s | 1.83 s | 1.07 s |
| TTS TTFB | 0.13–0.21 s | 0.05–0.14 s | 0.14 s |
| avatar video | — | 6.7 fps, 768×768 | 15.6 fps, 768×768 |
| avatar first frame | — | 8.74 s | 7.87 s |

\* The story reply ended (or paused below the caller's loudness threshold) before the scripted 3 s barge window, so the caller did not record a barge-in number in either avatar run. Voice-only and the monitor both measured it.

Every session starts with a Cartesia 402 (`Invalid response status`, `status_code=402`, `retryable=False`); the log then shows `cartesia.tts.TTS error, switching to next TTS` and Deepgram serves the audio. The adapter's background recovery probes keep hitting the same 402 until the account is topped up, which is harmless but noisy.

The avatar fps is below the 24.9 fps measured in the earlier preset-face run. Run 1 logged a caller-side DNS failure (`Temporary failure in name resolution`) and a native video queue overflow; the host was also under load from parallel work. The avatar published 768×768 video for the whole session in both runs and the agent-side turn metrics were normal, so this looks like caller-side degradation rather than an avatar or TTS regression. Simli time spent: ≈2.7 min across the two calls (≈$0.14 of avatar cost, from the session summaries).

Synthetic monitor validation (`modal run infra/modal/monitor_app.py`, 2026-11-10): join → greeting 3.36 s, tool turn 3.03 s, plain turn 1.60 s, barge-in 1.22 s, `problems: []`. The monitor's three scripted questions all hit Cartesia 402 and were synthesised with Deepgram instead.

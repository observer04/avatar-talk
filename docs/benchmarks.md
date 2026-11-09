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

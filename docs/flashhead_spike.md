# SoulX-FlashHead spike: self-hosted photoreal avatar

Date: 2026-11-09 · Branch: `phase-1` · Model: [SoulX-FlashHead-1.3B](https://github.com/Soul-AILab/SoulX-FlashHead) @ `9bc03de06bb0de82cd6bc477804512ae06144bf2`
Harness: `services/avatar_flashhead/spike/{bench_modal.py,fh_worker.py}` · Raw data: [`docs/flashhead_spike/`](flashhead_spike/)

## TL;DR

- **Gate passed.** Lite chunk inference is **0.17–0.36 s** for 0.96 s of audio (25 fps, 512×512), far below the 1.5 s gate. End-to-end added latency with the 0.96 s streaming buffer is **~1.2–1.3 s** on L40S eager, **~1.1–1.2 s** on L40S compiled.
- **Production recommendation: Lite, eager, one L40S worker per session, scaled to zero.** 66.8 fps eager / 105.2 fps compiled single-stream; 4 concurrent realtime streams per L40S; ~6 GiB VRAM per stream.
- **H100** is the density option: 7 concurrent realtime Lite streams (142 fps compiled, 73.5 fps eager single-stream). **A10 is too weak** for production: 36.3 fps eager, only 1 stream (2 streams drop to 20 fps). **Pro is experimental**: 31.6 fps compiled / 25.3 fps eager single-stream on H100, 2 streams fail.
- **Eager is the default** because compiled mode needs a **40–43 s warmup with a warm inductor cache (~190 s cold)**, which does not fit the agent's 45 s join timeout on a cold container. `FLASHHEAD_COMPILE=1` is opt-in for keep-warm deployments.
- **Cost:** L40S is **$1.9512/GPU-hr** (Modal base rate, checked 2026-11-09); all-in with 8 CPU / 48 GiB is **$2.71/hr** for one stream, **$0.68/stream-hr** at 4-stream density. H100 all-in is **$4.71/hr**; **$0.67/stream-hr** at 7-stream density.

## What was benchmarked

The spike mirrors the repo's streaming path (`generate_video.py --audio_encode_mode stream` / `gradio_app_streaming.py`):

- 8 s rolling 16 kHz audio window, wav2vec2-base-960h embedding over the window,
- last `frame_num` embedding frames (`audio_end_idx = cached_audio_duration × fps`),
- 4-step distilled DiT + VAE decode,
- drop the `motion_frames_num` overlap frames, keep `slice_len` new frames per chunk.

Lite produces **24 new frames (0.96 s)** per chunk; Pro produces **28 new frames (1.12 s)**. All runs are 512×512 at 25 fps. One worker process == one stream, which is how a per-session worker is deployed. Concurrency runs spawn N processes in one GPU container and require every worker's p95 chunk time ≤ its audio chunk duration and sustained fps ≥ 25.

## Single-stream performance

Full-run numbers (40 chunks, 6 warmup chunks for compiled / 3 for eager). `chunk` is audio-embed + DiT/VAE + to-CPU per 0.96 s (Lite) or 1.12 s (Pro) of audio.

| GPU | Model | Mode | mean (s) | p50 (s) | p95 (s) | max (s) | sustained fps | first chunk (s) | peak alloc (GiB) | nvidia-smi (MiB) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| L40S | Lite | compiled | 0.228 | 0.210 | 0.297 | 0.323 | 105.2 | 0.206 | 5.47 | 6131 |
| L40S | Lite | **eager** | 0.359 | 0.359 | 0.367 | 0.369 | **66.8** | 0.360 | 5.54 | 6195 |
| L40S | Lite | compiled + SageAttn v1 | 0.199 | 0.199 | 0.206 | 0.208 | 120.3 | 0.205 | 5.36 | 6015 |
| L40S | Lite | compiled + CUDA graphs | 0.190 | 0.190 | 0.194 | 0.195 | 126.2 | 0.190 | 5.93 | 6635 |
| H100 | Lite | compiled | 0.169 | 0.169 | 0.171 | 0.172 | 142.3 | 0.171 | 5.42 | 6259 |
| H100 | Lite | **eager** | 0.327 | 0.326 | 0.328 | 0.329 | **73.5** | 0.329 | 5.54 | 6369 |
| H100 | Lite | compiled + SageAttn v1 | 0.182 | 0.183 | 0.184 | 0.187 | 131.6 | 0.187 | 5.36 | 6189 |
| H100 | Pro | compiled | 0.886 | 0.886 | 0.891 | 0.892 | 31.6 | 0.876 | 10.16 | 11117 |
| H100 | Pro | **eager** | 1.106 | 1.106 | 1.109 | 1.110 | **25.3** | 1.109 | 7.47 | 8353 |
| H100 | Pro | compiled + SageAttn v1 | 0.854 | 0.853 | 0.858 | 0.861 | 32.8 | 0.853 | 10.12 | 11065 |
| A10 | Lite | compiled | 0.553 | 0.554 | 0.557 | 0.560 | 43.4 | 0.544 | 5.38 | 5808 |
| A10 | Lite | **eager** | 0.661 | 0.661 | 0.664 | 0.665 | **36.3** | 0.648 | 5.54 | 5968 |

Notes:

- The L40S quick run (same image, GPU warmed by a preceding compiled job) measured eager at **0.251 s / 95.7 fps** and compiled at **0.190 s / 126.0 fps**. The full run above is the conservative number; expect run-to-run variance of roughly ±20%.
- SageAttention v1 is a small win on L40S (120.3 vs 105.2 fps) but **slower on H100** (131.6 vs 142.3 fps). Keep it off; the default is `flash_attn_2`.
- CUDA graphs (`reduce-overhead`) is slightly faster than compiled (126.2 vs 105.2 fps on L40S) but has the same long cold warmup and no concurrency benefit. Not worth the extra complexity.
- Pro's eager single-stream 25.3 fps is exactly at the 25 fps line with almost no headroom (p95 1.109 s vs 1.12 s budget); compiled is the only safe Pro mode.

## Concurrency limits

Largest N where every worker stayed realtime (p95 ≤ audio chunk, sustained fps ≥ 25):

| GPU | Model | Max realtime streams | min fps at max | first failing N | fps at fail | nvidia-smi at max / fail (MiB) |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| L40S | Lite (compiled) | **4** | 29.7 | 5 | 24.2 | 24121 / 30150 |
| L40S | Lite (CUDA graphs) | **4** | 29.6 | 5 | 24.0 | 26529 / 33160 |
| H100 | Lite (compiled) | **7** | 29.8 | 8 | 25.8 (p95 0.977 > 0.96, rtfrac 0.87) | 43431 / 49635 |
| H100 | Pro (compiled) | **1** | 31.6 | 2 | 14.2 | 11117 / 22219 |
| A10 | Lite (compiled) | **1** | 43.4 | 2 | 20.0 | 5808 / 11605 |

The H100 n=8 run is a useful edge case: mean fps 25.9 looks fine, but the p95 chunk time exceeds the 0.96 s budget and only 87% of chunks are realtime, so it fails. The n=7 run has 29.8 fps minimum and p95 0.841 s — the safe density.

**VRAM per stream:** ~6.0–6.2 GiB nvidia-smi for Lite (5.4–5.5 GiB allocator peak), ~11.1 GiB for Pro (10.2 GiB peak). The A10's 24 GiB fits two Lite processes but not at realtime.

## Cold start and warmup

`call→container python` is the Modal cold start (image pull + container start); `model load` is `get_pipeline` (weights); `prepare` is `get_base_data` (VAE encode of the photo); `warmup` is the first chunks that trigger `torch.compile` (compiled) or the first eager calls.

| GPU | Model | Image cold start | Model load (compiled / eager) | Prepare (compiled cold / eager) | Compiled warmup (cold cache / warm cache) | Eager warmup |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| L40S | Lite | 82.7 s first pull; 18.1 s cached | 8.4 s / 3.8 s | 28.8 s / 0.34 s | 188.9 s / 40.9–43.6 s | 0.47 s |
| H100 | Lite | 4.3 s | 10.5 s / 4.2 s | 28.5 s / 0.42 s | 196.1 s / 42.4–52.0 s | 0.50 s |
| A10 | Lite | 4.3 s | 8.5 s / 4.1 s | 22.7 s / 0.42 s | 168.8 s / 40.9 s | 0.83 s |
| H100 | Pro | 4.6 s | 7.4 s / 2.7 s | 101.2 s / 0.65 s | 289.1 s / 49.4–51.8 s | 1.63 s |

The "warm cache" column is with the persisted `flashhead-inductor-cache` volume. The per-process model load is 3–10 s; the compiled warmup dominates. Eager total startup (model load + prepare + warmup) is **~4.6 s on L40S**, which fits the agent's 45 s join timeout comfortably even with a cold container. Compiled startup is **~50 s with a warm inductor cache** and **~230 s cold**, so compile mode requires a keep-warm container (or a raised join timeout) and is not the default.

## Eager vs compiled

| | Eager | Compiled |
| --- | --- | --- |
| L40S Lite chunk mean | 0.359 s | 0.228 s |
| L40S Lite single-stream fps | 66.8 | 105.2 |
| H100 Lite chunk mean | 0.327 s | 0.169 s |
| H100 Lite single-stream fps | 73.5 | 142.3 |
| A10 Lite chunk mean | 0.661 s | 0.553 s |
| A10 Lite single-stream fps | 36.3 | 43.4 |
| H100 Pro chunk mean | 1.106 s | 0.886 s |
| H100 Pro single-stream fps | 25.3 | 31.6 |
| Warmup (warm inductor cache) | <1 s | 40–52 s |
| Warmup (cold cache) | <1 s | 169–289 s |
| Concurrency at 25 fps | same or slightly lower | same |
| Operational risk | low | inductor cache volume, compile version pinning |

Compiled mode is a clear steady-state win (1.5–1.6×), but the 40–52 s warmup is incompatible with a cold per-session container and the agent's 45 s join timeout. The production default is **eager**; compile is a keep-warm / density optimization.

## Cost per stream-hour

Modal base GPU rates from <https://modal.com/pricing> (checked 2026-11-09):

| GPU | $/sec | $/GPU-hr |
| --- | ---: | ---: |
| L40S | 0.000542 | **1.9512** |
| H100 SXM5 | 0.001097 | **3.9492** |
| A10 | 0.000306 | **1.1016** |
| A100 80 GB | 0.000694 | **2.4984** |

CPU is $0.0000131/core/s and memory is $0.00000222/GiB/s. The production worker requests 8 CPU and 48 GiB, i.e. **$0.761/hr** of CPU+memory. Region selection can add 1.15–1.75× to base prices; the table below assumes the base `us-east` rate. Modal bills per second of container runtime, including model load, warmup and the `scaledown_window` idle period.

| Deployment | GPU-hr | CPU+mem/hr | All-in/hr | Streams | All-in per stream-hr |
| --- | ---: | ---: | ---: | ---: | ---: |
| L40S, 1 stream (per-session default) | 1.951 | 0.761 | **2.712** | 1 | **2.71** |
| L40S, 2 streams | 1.951 | 0.761 | 2.712 | 2 | 1.36 |
| L40S, 4 streams (max density) | 1.951 | 0.761 | 2.712 | 4 | **0.68** |
| H100, 1 stream | 3.949 | 0.761 | **4.710** | 1 | **4.71** |
| H100, 7 streams (max density) | 3.949 | 0.761 | 4.710 | 7 | **0.67** |
| A10, 1 stream | 1.102 | 0.761 | **1.862** | 1 | **1.86** |
| H100 Pro, 1 stream | 3.949 | 0.761 | **4.710** | 1 | **4.71** |

Worked example: a 10-minute Lite session on L40S eager (container start ~5 s + model load 4 s + prepare/warmup 1 s + 600 s session + 60 s scaledown) ≈ 670 s × $0.000753/s ≈ **$0.50**. At 4-stream density the GPU and CPU cost is shared, so the same 10-minute session is ~$0.13 of GPU + ~$0.13 CPU/mem ≈ **$0.26**. The per-session default is intentionally conservative: one GPU per live call, no noisy neighbours, no queueing.

## Quality: Lite vs Pro

The clips are in [`docs/flashhead_spike/`](flashhead_spike/). Lite and Pro were run on the same conditioning images and audio, so the pairs are directly comparable:

| Clip | Model | GPU | Frames | Duration | Bitrate | Mean frame-diff/s (first 8 s) |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `L40S_lite_girl_amy.mp4` | Lite | L40S | 312 | 12.48 s | 346 kbps | 2.04, 1.87, 2.15, 1.61, 1.91, 1.69, 1.27, 1.31 |
| `H100_lite_girl_amy.mp4` | Lite | H100 | 312 | 12.48 s | 344 kbps | 2.05, 1.87, 2.16, 1.59, 1.89, 1.71, 1.26, 1.30 |
| `H100_pro_girl_amy.mp4` | Pro | H100 | 364 | 14.56 s | 416 kbps | 2.30, 2.01, 2.02, 1.36, 2.13, 1.90, 1.90, 1.60 |
| `L40S_lite_glover_alan.mp4` | Lite | L40S | 312 | 12.48 s | 346 kbps | 2.25, 1.60, 1.64, 1.62, 1.26, 1.46, 1.39, 1.43 |
| `H100_pro_glover_alan.mp4` | Pro | H100 | 364 | 14.56 s | 416 kbps | 1.88, 1.86, 2.11, 1.88, 1.41, 1.99, 1.77, 1.78 |
| `L40S_lite_idle.mp4` | Lite | L40S | 264 | 10.56 s | 191 kbps | 1.53, 1.21, 1.12, 1.05, 1.04, 1.00, 0.99, 1.04 |
| `H100_pro_idle.mp4` | Pro | H100 | 252 | 10.08 s | 284 kbps | 1.12, 1.81, 1.37, 2.21, 1.21, 1.19, 1.15, 1.13 |
| `L40S_lite_speech_then_idle.mp4` | Lite | L40S | 480 | 19.20 s | 346 kbps | 2.25, 1.62, 1.64, 1.61, 1.29, 1.50, 1.41, 1.43 |
| `H100_pro_speech_then_idle.mp4` | Pro | H100 | 560 | 22.40 s | 416 kbps | 1.88, 1.86, 2.11, 1.88, 1.41, 1.99, 1.77, 1.78 |

Observations:

- Both models are 512×512 / 25 fps and preserve identity and lip sync on both faces. Pro has more per-frame motion energy and a higher bitrate (416 vs 344 kbps on speech; 284 vs 191 kbps on idle), consistent with a larger, more expressive model. Lite is smoother and slightly more conservative in head motion; Pro's idle clip has more visible micro-motion.
- Pro's advantage is real but not dramatic at 512×512; the 3.5× GPU cost and the loss of all concurrency are the dominant factors. **Lite is the production choice; Pro is experimental for single-stream, quality-first demos.**
- The clips are the evidence for a human visual review; the table above is the objective motion/bitrate summary. `speech_then_idle` is the important transition clip (speech → idle motion); `idle` shows whether the avatar stays alive between turns.

## Recommendation

1. **Production default:** SoulX-FlashHead **Lite**, **eager** (`FLASHHEAD_COMPILE=0`), **L40S**, one worker per session, `region="us-east"`, scale to zero. 66.8 fps single-stream, 4-stream density, ~6 GiB/stream, ~4.6 s model startup, ~1.2–1.3 s added end-to-end latency.
2. **Density / lowest latency:** **H100** with Lite (7 streams; 142 fps compiled, 73.5 fps eager). Use compiled only with a keep-warm container and the persisted inductor cache.
3. **A10:** not recommended. 36.3 fps eager single-stream and 2 streams fail at 20 fps; only for a low-cost single-stream demo if L40S is unavailable.
4. **Pro:** experimental, single-stream only on H100 (31.6 fps compiled / 25.3 fps eager); 2 streams fail. Use for quality-first demos, not production.
5. **Compile mode:** opt-in via `FLASHHEAD_COMPILE=1`; requires `flashhead-inductor-cache` and a warm container, or a raised `avatar_join_timeout`. Do not enable it for a cold per-session deployment.

## Integration notes and risks

- **Launch contract unchanged.** The agent posts `{room, livekit_url, token, persona_id, photo_key, agent_identity}` to `{FLASHHEAD_LAUNCH_URL}/launch`; the worker joins as `flashhead-avatar`, receives the agent's TTS over the `lk.audio_stream` DataStream, and publishes audio + video. The worker also accepts an optional `photo_url` for direct image fetches.
- **Playback RPCs.** The agent uses `DataStreamAudioOutput(..., wait_playback_start=True)`. The worker sends `lk.playback_started` when the first audio frame is pushed and `lk.playback_finished` (with `playback_position` and `interrupted`) at segment end / barge-in, exactly like `DataStreamAudioReceiver` in `livekit-agents`.
- **Barge-in.** On `lk.clear_buffer` the worker drops pending audio, resets the 8 s window and the model's motion latents, drains the output queue, and clears the `AVSynchronizer` audio/video queues. This stops playback promptly instead of letting the 1 s audio queue drain.
- **Photo fetch.** The API's `GET /v1/personas/{id}/photo` is a **client** route (`API_KEYS`), not an internal route. The worker uses `AVATAR_API_KEY` when set, otherwise `INTERNAL_API_TOKEN` (works only in dev/open mode). For production, add `AVATAR_API_KEY` to the `avatar-talk-agent` secret (or a dedicated `avatar-talk-flashhead` secret), or send `photo_url`. Without either, the worker falls back to the baked-in `/opt/flashhead/examples/girl.png` only when the payload has no photo at all.
- **Image patch.** mediapipe 0.10.9 pins `protobuf<4`; livekit 1.2 requires `protobuf>=5`. The image omits mediapipe and patches `flash_head/utils/facecrop.py` to import `CPUFaceHandler` lazily (see `services/avatar_flashhead/patches/lazy_mediapipe.py`). Face crop (`FLASHHEAD_FACE_CROP=1`) is therefore unsupported in the combined image; the control plane already square-crops persona photos, so this is not needed.
- **Cold start.** The first deploy's image pull can exceed the agent's 45 s join timeout on L40S (82.7 s observed). Pre-warm with `FLASHHEAD_KEEP_WARM=1` for the first demo, or launch one dummy session after deploying. Subsequent containers start in a few seconds.
- **Session lifecycle.** The worker exits when the agent leaves the room, the room disconnects, or `FLASHHEAD_MAX_SESSION_SECONDS` elapses. The Modal function timeout is 1 hour; `scaledown_window` is 60 s by default.
- **Concurrency.** The production worker is one GPU container per session (`max_inputs=1`). The 4/7-stream density numbers are available for a future multi-session scheduler but are not used by the default per-session deployment.

## Raw data

- `docs/flashhead_spike/*.json` — full result JSONs (`L40S_lite_full`, `L40S_lite_quick`, `L40S_lite_tune`, `H100_lite_full`, `H100_pro_full`, `A10_lite_perf`).
- `docs/flashhead_spike/*.mp4` — Lite/Pro quality clips for `girl_amy`, `glover_alan`, `idle`, `speech_then_idle`.
- Spike harness: `services/avatar_flashhead/spike/bench_modal.py`, `services/avatar_flashhead/spike/fh_worker.py`.
- Production image: `infra/modal/flashhead_app.py` (same torch 2.7.1/cu128, flash-attn 2.8.0.post2, diffusers, transformers 4.57.3 recipe as the spike, plus livekit-agents 1.8.11).

## Production smoke test

The deployed worker was exercised end-to-end on **L40S, eager, 512×512** with a cloud-side publisher/subscriber (`services/avatar_flashhead/scripts/smoke_modal.py`, a CPU container in `us-east` so the laptop's load does not affect the measurement). The publisher joins as the agent, sends 60 s of 16 kHz audio through the exact `DataStreamAudioOutput` wire format, and the subscriber measures the avatar's published video/audio.

| Run | Join (launch → avatar) | `playback_started` | `playback_position` | `interrupted` | Video received | Audio realtime | Worker `av_fps` |
| --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
| 60 s, cold container | 26.7 s | 28.9 s | 60.48 s | False | 21.4 fps (1446 frames / 67.5 s) | 1.001× | 19.6–25.0 (avg ~23) |
| 60 s, warm container | 6.5 s | 9.7 s | 60.48 s | False | 20.8 fps (1515 / 72.9 s) | 1.000× | 20.0–26.8 (avg ~23) |
| 60 s, 256×256 experiment | 53.8 s | 56.0 s | 60.48 s | False | 21.7 fps (1491 / 68.6 s) | 1.000× | 19.9–24.5 (avg ~23) |

What passed:

- The worker **joins the room, publishes audio + video, receives the agent's DataStream audio, and plays the full 60 s segment** (`playback_position` 60.48 s = 60 s + the final chunk's padding; `interrupted=False`; `playback_finished` received).
- Audio is **realtime** (received duration / wall time = 1.00×) and stays in sync with the video.
- The model's chunk time is **0.23–0.26 s** for 0.96 s of audio, matching the spike.
- A warm container joins in **6.5 s**; the agent's 45 s join timeout is comfortable. A cold container can take 25–80 s (image pull + model load), which is the main startup risk.

What did **not** meet the target:

- The published video averages **~21–23 fps**, not a clean 25 fps. The worker's capture pacer (`AVSynchronizer.actual_fps`) oscillates between ~20 and ~27 and logs a **~300–500 ms stall every ~3 s**; the subscriber receives ~21 fps. The model is not the bottleneck (chunk time 0.24 s). The stall is in the LiveKit media path (the synchronous `FfiClient.request` used by `AudioSource.capture_frame`/the video encoder), not in the model: changing the output from 512×512 to 256×256 and raising CPU from 8 to 16 did not change the average.
- The next production fix is to isolate the model in a **separate process** (it currently shares the GIL with the LiveKit event loop) and/or move the audio capture off the event loop. The current worker is usable for a demo but should be treated as **~21–23 fps** until that follow-up.

Spend: the L40S smoke runs (three 60 s sessions plus container/model startup and the failed scheduling attempts) consumed roughly **$0.8–1.0** of the $3 budget at $0.000542/GPU-s; the exact per-run container runtime is visible in the Modal app logs. The app is scaled to zero after the runs; `modal app list` shows `avatar-talk-flashhead` deployed with 0 tasks and `modal container list` shows no FlashHead container.

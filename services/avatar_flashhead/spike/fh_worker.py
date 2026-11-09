"""FlashHead benchmark worker. Runs INSIDE the Modal container, one process per simulated stream.

It mirrors the repo's streaming path (generate_video.py --audio_encode_mode stream / gradio_app_streaming.py):
an 8 s rolling 16 kHz audio window, wav2vec2 over the window, take the last `frame_num` embedding frames,
run the 4-step distilled DiT + VAE decode, drop the `motion_frames_num` overlap frames, keep the rest.

Spike code: not production. Invoked by bench_modal.py as `python fh_worker.py <json-config>`.
"""

import contextlib
import io
import json
import os
import sys
import time
from collections import deque

T_PROC_START = time.time()

cfg = json.loads(sys.argv[1])
REPO = cfg.get("repo", "/opt/flashhead")
os.chdir(REPO)  # infer_params.yaml is loaded via a relative path
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402
import torch  # noqa: E402

import flash_head.src.modules.flash_head_model as fh_model  # noqa: E402
import flash_head.src.pipeline.flash_head_pipeline as fh_pipe  # noqa: E402

fh_pipe.COMPILE_MODEL = bool(cfg.get("compile", True))
fh_pipe.COMPILE_VAE = bool(cfg.get("compile", True))
if not cfg.get("sage", False):
    fh_model.SAGE_ATTN_AVAILABLE = False
if cfg.get("attn") == "sdpa":
    fh_model.SAGE_ATTN_AVAILABLE = False
    fh_model.FLASH_ATTN_3_AVAILABLE = False
    fh_model.FLASH_ATTN_2_AVAILABLE = False

if cfg.get("compile_mode"):
    # the pipeline calls torch.compile(module) with defaults; inject a mode (e.g. "reduce-overhead" = CUDA graphs)
    _orig_compile = torch.compile

    def _compile_with_mode(fn=None, *a, **kw):
        kw.setdefault("mode", cfg["compile_mode"])
        return _orig_compile(fn, *a, **kw)

    fh_pipe.torch.compile = _compile_with_mode  # same module object as torch; patch is process-wide
    torch.compile = _compile_with_mode

import flash_head.inference as fh_inf  # noqa: E402

if cfg.get("frame_num"):
    fh_inf.infer_params["frame_num"] = int(cfg["frame_num"])

T_IMPORTED = time.time()


def attn_backend():
    if fh_model.SAGE_ATTN_AVAILABLE:
        return "sageattention"
    if fh_model.FLASH_ATTN_3_AVAILABLE:
        return "flash_attn_3"
    if fh_model.FLASH_ATTN_2_AVAILABLE:
        return "flash_attn_2"
    return "sdpa"


def quiet(fn, *a, **kw):
    # the pipeline prints per-step timings to stdout on every call; swallow them
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def sync():
    torch.cuda.synchronize()


def load_audio(spec, sr, seconds):
    """spec: path to a wav, 'silence', or 'path+silence:N' (speech followed by N s of silence)."""
    import librosa

    if spec == "silence":
        return np.zeros(int(sr * seconds), dtype=np.float32)
    tail = 0.0
    if "+silence:" in spec:
        spec, tail_s = spec.split("+silence:")
        tail = float(tail_s)
    y, _ = librosa.load(spec, sr=sr, mono=True)
    if tail:
        y = np.concatenate([y, np.zeros(int(sr * tail), dtype=y.dtype)])
    if seconds and len(y) < sr * seconds:  # loop to reach requested length
        reps = int(np.ceil(sr * seconds / len(y)))
        y = np.tile(y, reps)[: int(sr * seconds)]
    return y.astype(np.float32)


def pct(xs, p):
    return float(np.percentile(np.array(xs), p)) if xs else None


def main():
    out = {"cfg": cfg, "attn_backend": attn_backend(), "worker_id": cfg.get("worker_id", 0)}
    out["t_proc_start"] = T_PROC_START
    out["import_s"] = T_IMPORTED - T_PROC_START

    t = time.time()
    pipeline = quiet(
        fh_inf.get_pipeline,
        world_size=1,
        ckpt_dir=cfg["ckpt_dir"],
        model_type=cfg["model"],
        wav2vec_dir=cfg["wav2vec_dir"],
    )
    sync()
    out["model_load_s"] = time.time() - t
    out["vram_after_load_gib"] = torch.cuda.memory_allocated() / 2**30

    t = time.time()
    quiet(fh_inf.get_base_data, pipeline, cond_image_path_or_dir=cfg["image"], base_seed=cfg.get("seed", 42),
          use_face_crop=cfg.get("face_crop", False))
    sync()
    out["prepare_image_s"] = time.time() - t  # includes VAE-encode compile on first call when compile=True

    p = fh_inf.get_infer_params()
    sr, fps = p["sample_rate"], p["tgt_fps"]
    frame_num, motion = p["frame_num"], p["motion_frames_num"]
    slice_len = frame_num - motion
    slice_samples = slice_len * sr // fps
    cached = p["cached_audio_duration"]
    out["params"] = dict(sample_rate=sr, tgt_fps=fps, frame_num=frame_num, motion_frames_num=motion,
                         new_frames_per_chunk=slice_len, audio_s_per_chunk=slice_samples / sr,
                         cached_audio_duration=cached, height=p["height"], width=p["width"],
                         sample_steps=p["sample_steps"])
    audio_end_idx = cached * fps
    audio_start_idx = audio_end_idx - frame_num

    def new_stream():
        pipeline.reset_person_name()  # restart motion latents from the reference image
        return deque([0.0] * (sr * cached), maxlen=sr * cached)

    def step(dq, audio_slice):
        """One streaming chunk. Returns (uint8 frames [N,H,W,3] on CPU, timings dict)."""
        sync()
        t0 = time.perf_counter()
        dq.extend(audio_slice.tolist())
        emb = fh_inf.get_audio_embedding(pipeline, np.array(dq), audio_start_idx, audio_end_idx)
        sync()
        t1 = time.perf_counter()
        video = quiet(fh_inf.run_pipeline, pipeline, emb)
        sync()
        t2 = time.perf_counter()
        frames = video[motion:].to(torch.uint8).cpu()
        t3 = time.perf_counter()
        return frames, {"audio_embed": t1 - t0, "dit_vae": t2 - t1, "to_cpu": t3 - t2, "total": t3 - t0}

    # ---- warmup (first calls trigger torch.compile) ----
    warm_audio = load_audio(cfg.get("warm_audio", cfg["audio"]), sr, seconds=slice_samples * 12 / sr)
    dq = new_stream()
    warm_times = []
    for i in range(cfg.get("warmup", 6)):
        s = warm_audio[(i * slice_samples) % (len(warm_audio) - slice_samples):][:slice_samples]
        _, tm = step(dq, s)
        warm_times.append(tm["total"])
    out["warmup_chunk_s"] = warm_times
    out["ready_s_since_proc_start"] = time.time() - T_PROC_START

    # ---- barrier so concurrent workers start the measured section together ----
    bdir = cfg.get("barrier_dir")
    if bdir:
        open(os.path.join(bdir, f"ready_{out['worker_id']}"), "w").close()
        while not os.path.exists(os.path.join(bdir, "go")):
            time.sleep(0.01)

    # ---- measured streaming run ----
    n_chunks = cfg.get("n_chunks", 40)
    audio = load_audio(cfg["audio"], sr, seconds=cfg.get("audio_seconds", slice_samples * n_chunks / sr))
    pad = (-len(audio)) % slice_samples
    audio = np.concatenate([audio, np.zeros(pad, dtype=audio.dtype)])
    slices = audio.reshape(-1, slice_samples)[:n_chunks]

    torch.cuda.reset_peak_memory_stats()
    dq = new_stream()
    timings, frames_all = [], []
    t_run = time.perf_counter()
    for s in slices:
        frames, tm = step(dq, s)
        timings.append(tm)
        if cfg.get("save_video"):
            frames_all.append(frames.numpy())
    run_s = time.perf_counter() - t_run

    tot = [x["total"] for x in timings]
    out["chunks"] = len(timings)
    out["first_chunk_s"] = tot[0]
    out["chunk_total_mean_s"] = float(np.mean(tot))
    out["chunk_total_p50_s"] = pct(tot, 50)
    out["chunk_total_p95_s"] = pct(tot, 95)
    out["chunk_total_max_s"] = float(np.max(tot))
    for k in ("audio_embed", "dit_vae", "to_cpu"):
        out[f"{k}_mean_s"] = float(np.mean([x[k] for x in timings]))
    out["run_wall_s"] = run_s
    out["sustained_fps"] = len(timings) * slice_len / run_s
    out["realtime_chunks_frac"] = float(np.mean([x <= slice_samples / sr for x in tot]))
    out["peak_alloc_gib"] = torch.cuda.max_memory_allocated() / 2**30
    out["peak_reserved_gib"] = torch.cuda.max_memory_reserved() / 2**30
    out["timings"] = timings

    if cfg.get("save_video"):
        import imageio
        import subprocess

        vid = np.concatenate(frames_all, axis=0)
        tmp = cfg["save_video"].replace(".mp4", "_noaudio.mp4")
        with imageio.get_writer(tmp, fps=fps, codec="h264", quality=8, ffmpeg_params=["-bf", "0"]) as w:
            for f in vid:
                w.append_data(f)
        wav = cfg["save_video"].replace(".mp4", ".wav")
        import soundfile as sf

        sf.write(wav, audio[: len(slices) * slice_samples], sr)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-i", wav, "-c:v", "copy", "-c:a", "aac",
                        "-shortest", cfg["save_video"]], check=True)
        os.remove(tmp)
        out["saved_video"] = cfg["save_video"]
        out["saved_frames"] = int(vid.shape[0])
        # crude motion stats: mean abs diff between consecutive frames, per second
        d = np.abs(np.diff(vid.astype(np.int16), axis=0)).mean(axis=(1, 2, 3))
        out["frame_diff_per_sec"] = [float(d[i:i + fps].mean()) for i in range(0, len(d), fps)]

    with open(cfg["out_json"], "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main()

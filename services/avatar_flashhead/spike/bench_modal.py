"""Spike A: benchmark SoulX-FlashHead (Lite / Pro) on Modal GPUs.

Ephemeral only. Usage (from this directory; Modal CLI auth comes from ~/.modal.toml):

    modal run bench_modal.py::download                       # once: weights -> Volume "flashhead-weights"
    modal run bench_modal.py --gpu L40S --model lite --suite full
    modal run bench_modal.py --gpu H100 --model pro --suite quick

Env:
    FH_ASSETS   local dir with extra conditioning images / wavs (copied into the image at /assets)
    FH_OUT      local dir where result JSON and mp4 clips are written

Each benchmark job spawns N worker processes (fh_worker.py) inside one GPU container. One process == one
stream, which is how a per-session worker would be deployed; N>1 measures how many 25 fps streams fit.
"""

import json
import os
import time

import modal

T_MODULE_IMPORT = time.time()

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.environ.get("FH_ASSETS", os.path.join(HERE, "assets"))
OUT = os.environ.get("FH_OUT", os.path.join(HERE, "out"))

FLASHHEAD_COMMIT = "9bc03de06bb0de82cd6bc477804512ae06144bf2"
FA_WHEEL = (
    "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/"
    "flash_attn-2.8.0.post2+cu12torch2.7cxx11abiTRUE-cp310-cp310-linux_x86_64.whl"
)
CKPT = "/weights/SoulX-FlashHead-1_3B"
W2V = "/weights/wav2vec2-base-960h"

app = modal.App("flashhead-spike")
weights = modal.Volume.from_name("flashhead-weights", create_if_missing=True)
# persisted TorchInductor/Triton cache, keyed by GPU type, to measure compile cost with a warm cache
icache = modal.Volume.from_name("flashhead-inductor-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "ffmpeg", "build-essential", "libgl1", "libglib2.0-0")
    .pip_install("torch==2.7.1", "torchvision==0.22.1", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install(FA_WHEEL)
    .pip_install(
        "torch==2.7.1",  # keep pinned while resolving the rest
        "diffusers>=0.34.0",
        "transformers==4.57.3",
        "tokenizers>=0.20.3",
        "accelerate>=1.8.1",
        "xfuser>=0.4.3",
        "einops",
        "pyyaml",
        "loguru",
        "easydict",
        "ftfy",
        "imageio",
        "imageio-ffmpeg",
        "scikit-image",
        "pyloudnorm",
        "librosa",
        "soundfile",
        "mediapipe==0.10.9",
        "opencv-python-headless>=4.12.0.88",
        "sageattention==1.0.6",  # triton-only SageAttention v1; toggled per run (off by default)
    )
    .run_commands(
        f"git clone https://github.com/Soul-AILab/SoulX-FlashHead /opt/flashhead && "
        f"cd /opt/flashhead && git checkout {FLASHHEAD_COMMIT}",
        "python -c \"import torch, flash_attn; print(torch.__version__, torch.version.cuda, flash_attn.__version__)\"",
    )
    .env({"TORCHINDUCTOR_CACHE_DIR": "/tmp/inductor", "PYTHONUNBUFFERED": "1"})
    .add_local_file(os.path.join(HERE, "fh_worker.py"), "/opt/spike/fh_worker.py")
)
if os.path.isdir(ASSETS):
    image = image.add_local_dir(ASSETS, "/assets")

dl_image = modal.Image.debian_slim(python_version="3.11").pip_install("huggingface_hub[hf_transfer]==0.36.0").env(
    {"HF_HUB_ENABLE_HF_TRANSFER": "1"}
)


@app.function(image=dl_image, volumes={"/weights": weights}, timeout=3600, cpu=4)
def download(include_pro: bool = True):
    from huggingface_hub import snapshot_download

    pats = ["*.json", "Model_Lite/*", "VAE_LTX/*"] + (["Model_Pro/*", "VAE_Wan/*"] if include_pro else [])
    t = time.time()
    snapshot_download("Soul-AILab/SoulX-FlashHead-1_3B", local_dir=CKPT, allow_patterns=pats,
                      token=os.environ.get("HF_TOKEN"))
    snapshot_download("facebook/wav2vec2-base-960h", local_dir=W2V,
                      allow_patterns=["*.json", "*.txt", "model.safetensors"])
    weights.commit()
    os.system(f"du -sh {CKPT}/* {W2V}")
    return time.time() - t


def _gpu_mem_sampler(stop, store):
    import subprocess

    p = subprocess.Popen(
        ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits", "-lms", "250"],
        stdout=subprocess.PIPE, text=True)
    mem, util = [], []
    while not stop.is_set():
        line = p.stdout.readline()
        if not line:
            break
        try:
            m, u = [float(x) for x in line.strip().split(",")]
            mem.append(m)
            util.append(u)
        except ValueError:
            pass
    p.terminate()
    store["max_mem_used_mib"] = max(mem) if mem else None
    store["mean_util_pct"] = sum(util) / len(util) if util else None


@app.cls(image=image, volumes={"/weights": weights, "/icache": icache}, gpu="L40S", cpu=8, memory=49152, timeout=3600)
class Bench:
    @modal.enter()
    def enter(self):
        self.t_enter = time.time()

    @modal.method()
    def info(self):
        import subprocess

        smi = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip()
        return {"t_module_import": T_MODULE_IMPORT, "t_enter": self.t_enter, "t_now": time.time(), "gpu": smi}

    def _run_workers(self, cfg, n, timeout=1500):
        import subprocess
        import sys
        import tempfile
        import threading

        bdir = tempfile.mkdtemp()
        procs, logs = [], []
        t_spawn = time.time()
        for i in range(n):
            c = dict(cfg, worker_id=i, barrier_dir=bdir, out_json=f"{bdir}/out_{i}.json",
                     ckpt_dir=CKPT, wav2vec_dir=W2V)
            if c.get("save_video") and i > 0:
                c["save_video"] = None
            log = open(f"{bdir}/log_{i}.txt", "w")
            logs.append(f"{bdir}/log_{i}.txt")
            env = dict(os.environ)
            if c.get("cache_key"):
                env["TORCHINDUCTOR_CACHE_DIR"] = f"/icache/{c['cache_key']}"
            procs.append(subprocess.Popen([sys.executable, "/opt/spike/fh_worker.py", json.dumps(c)],
                                          stdout=log, stderr=subprocess.STDOUT, env=env))
        # wait until every worker is warmed up (or one died)
        while True:
            ready = sum(os.path.exists(f"{bdir}/ready_{i}") for i in range(n))
            dead = [p for p in procs if p.poll() is not None and p.returncode != 0]
            if ready == n or dead or time.time() - t_spawn > timeout:
                break
            time.sleep(0.2)
        t_ready = time.time()
        stop, store = threading.Event(), {}
        th = threading.Thread(target=_gpu_mem_sampler, args=(stop, store), daemon=True)
        th.start()
        open(f"{bdir}/go", "w").close()
        for p in procs:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
        stop.set()
        th.join(timeout=5)
        res = {"n_workers": n, "warm_wall_s": t_ready - t_spawn, **store, "workers": []}
        for i in range(n):
            f = f"{bdir}/out_{i}.json"
            if os.path.exists(f):
                res["workers"].append(json.load(open(f)))
            else:
                res["workers"].append({"error": open(logs[i]).read()[-4000:]})
        ok = [w for w in res["workers"] if "error" not in w]
        if len(ok) == n:
            res["min_worker_fps"] = min(w["sustained_fps"] for w in ok)
            res["max_worker_p95_s"] = max(w["chunk_total_p95_s"] for w in ok)
            res["agg_fps"] = sum(w["sustained_fps"] for w in ok)
            res["all_realtime"] = res["min_worker_fps"] >= 25.0 and all(
                w["chunk_total_p95_s"] <= w["params"]["audio_s_per_chunk"] for w in ok)
        else:
            res["all_realtime"] = False
        for w in ok:
            w.pop("timings", None) if n > 1 else None
        return res

    @modal.method()
    def run(self, jobs: list):
        results = []
        for job in jobs:
            name, cfg = job["name"], job["cfg"]
            print(f"=== job {name} ===", flush=True)
            t = time.time()
            if job.get("concurrency") == "auto":
                runs = []
                n = job.get("start_n", 2)
                while n <= job.get("max_n", 8):
                    r = self._run_workers(cfg, n)
                    runs.append(r)
                    print(f"  n={n} realtime={r['all_realtime']} min_fps={r.get('min_worker_fps')}"
                          f" mem={r.get('max_mem_used_mib')}", flush=True)
                    if not r["all_realtime"]:
                        break
                    n += 1
                res = {"name": name, "runs": runs}
            else:
                res = {"name": name, **self._run_workers(cfg, job.get("n", 1))}
            res["job_wall_s"] = time.time() - t
            if cfg.get("cache_key"):
                icache.commit()
            vids = {}
            for r in res.get("runs", [res]):
                for w in r["workers"]:
                    if w.get("saved_video") and os.path.exists(w["saved_video"]):
                        vids[os.path.basename(w["saved_video"])] = open(w["saved_video"], "rb").read()
            res["videos"] = vids
            results.append(res)
            w0 = res.get("workers", [{}])[0] if "workers" in res else {}
            print(f"  done in {res['job_wall_s']:.1f}s first={w0.get('first_chunk_s')} "
                  f"mean={w0.get('chunk_total_mean_s')} p95={w0.get('chunk_total_p95_s')} "
                  f"fps={w0.get('sustained_fps')} err={str(w0.get('error'))[-1500:] if 'error' in w0 else ''}",
                  flush=True)
        return results


def make_jobs(model, suite, cache_key=""):
    speech = "/opt/flashhead/examples/podcast_sichuan_16k.wav"
    base = dict(model=model, image="/opt/flashhead/examples/girl.png", audio=speech, compile=True, sage=False,
                warmup=6, n_chunks=40, cache_key=cache_key)
    jobs = [{"name": f"{model}_single_compiled", "n": 1, "cfg": dict(base)}]
    if suite in ("full", "quick", "perf"):
        jobs.append({"name": f"{model}_single_eager", "n": 1, "cfg": dict(base, compile=False, warmup=3, n_chunks=30)})
    if suite == "full":
        jobs.append({"name": f"{model}_single_compiled_sage1", "n": 1, "cfg": dict(base, sage=True)})
    if suite in ("full", "perf"):
        jobs.append({"name": f"{model}_concurrency", "concurrency": "auto", "cfg": dict(base, n_chunks=30)})
    if suite in ("full", "clips"):
        jobs += [
            {"name": f"{model}_clip_girl_amy", "n": 1,
             "cfg": dict(base, image="/opt/flashhead/examples/girl.png", audio="/assets/speech_amy.wav",
                         n_chunks=13, save_video=f"/tmp/{model}_girl_amy.mp4")},
            {"name": f"{model}_clip_glover_alan", "n": 1,
             "cfg": dict(base, image="/assets/glover.png", audio="/assets/speech_alan.wav",
                         n_chunks=13, save_video=f"/tmp/{model}_glover_alan.mp4")},
            {"name": f"{model}_idle_silence", "n": 1,
             "cfg": dict(base, audio="silence", audio_seconds=10, n_chunks=11, save_video=f"/tmp/{model}_idle.mp4")},
            {"name": f"{model}_speech_then_silence", "n": 1,
             "cfg": dict(base, image="/assets/glover.png", audio="/assets/speech_alan.wav+silence:6",
                         n_chunks=20, save_video=f"/tmp/{model}_speech_then_idle.mp4")},
        ]
    if suite == "tune":
        jobs = [{"name": f"{model}_single_compiled_cudagraphs", "n": 1,
                 "cfg": dict(base, compile_mode="reduce-overhead", warmup=8)},
                {"name": f"{model}_concurrency_cudagraphs", "concurrency": "auto", "start_n": 2,
                 "cfg": dict(base, compile_mode="reduce-overhead", warmup=8, n_chunks=30)}]
    if suite == "smallchunk":
        for fn in (17, 25):
            jobs.append({"name": f"{model}_frame_num_{fn}", "n": 1,
                         "cfg": dict(base, frame_num=fn, audio="/assets/speech_amy.wav", n_chunks=36,
                                     save_video=f"/tmp/{model}_girl_amy_fn{fn}.mp4")})
    return jobs


@app.local_entrypoint()
def main(gpu: str = "L40S", model: str = "lite", suite: str = "quick", tag: str = "", persist_cache: bool = True):
    os.makedirs(OUT, exist_ok=True)
    jobs = make_jobs(model, suite, cache_key=gpu if persist_cache else "")
    bench = Bench.with_options(gpu=gpu)()
    t_call = time.time()
    info = bench.info.remote()
    t_back = time.time()
    cold = {
        "gpu_requested": gpu,
        "gpu": info["gpu"],
        "call_to_container_python_s": info["t_module_import"] - t_call,
        "call_to_enter_s": info["t_enter"] - t_call,
        "info_roundtrip_s": t_back - t_call,
    }
    print("cold start:", json.dumps(cold))
    results = bench.run.remote(jobs)
    stem = f"{gpu}_{model}_{suite}{('_' + tag) if tag else ''}"
    for r in results:
        for fname, data in r.pop("videos", {}).items():
            p = os.path.join(OUT, f"{gpu}_{fname}")
            with open(p, "wb") as f:
                f.write(data)
            print("saved", p)
    with open(os.path.join(OUT, f"{stem}.json"), "w") as f:
        json.dump({"cold_start": cold, "results": results}, f, indent=1)
    # compact summary
    for r in results:
        for run in r.get("runs", [r]):
            ws = run.get("workers", [])
            w = ws[0] if ws else {}
            print(f"{r['name']:<34} n={run.get('n_workers')} load={w.get('model_load_s')} "
                  f"warm={w.get('warmup_chunk_s')} first={w.get('first_chunk_s')} mean={w.get('chunk_total_mean_s')} "
                  f"p95={w.get('chunk_total_p95_s')} fps={w.get('sustained_fps')} min_fps={run.get('min_worker_fps')} "
                  f"peak_alloc={w.get('peak_alloc_gib')} smi_max={run.get('max_mem_used_mib')} "
                  f"rt={run.get('all_realtime')} attn={w.get('attn_backend')} err={'error' in w}")
    print("results ->", os.path.join(OUT, f"{stem}.json"))

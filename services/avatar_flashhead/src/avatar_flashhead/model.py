"""SoulX-FlashHead model adapter (torch).

Imported only inside the Modal GPU image; the CPU-only package never imports
torch.  This wraps the exact inference helpers proven by the spike
(``services/avatar_flashhead/spike/fh_worker.py``).

The pipeline (weights) is loaded once per container; :meth:`prepare` swaps in
the conditioning photo for each session, which is what makes a keep-warm
container useful.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
import time

import numpy as np

from .video_generator import ModelParams

log = logging.getLogger(__name__)

REPO = os.environ.get("FLASHHEAD_REPO", "/opt/flashhead")
CKPT_DIR = os.environ.get("FLASHHEAD_CKPT", "/weights/SoulX-FlashHead-1_3B")
WAV2VEC_DIR = os.environ.get("FLASHHEAD_WAV2VEC", "/weights/wav2vec2-base-960h")


def _quiet(fn, *args, **kwargs):
    # The pipeline prints per-step timings to stdout on every call; swallow them.
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class FlashHeadModel:
    """Loads the Lite/Pro checkpoint and runs one streaming chunk at a time."""

    def __init__(
        self,
        *,
        model_type: str = "lite",
        compile: bool = False,
        sage: bool = False,
        use_face_crop: bool = False,
        seed: int = 42,
    ) -> None:
        self.model_type = model_type
        self.compile = compile
        self._use_face_crop = use_face_crop
        self._seed = seed
        self.image_path: str | None = None

        # flash_head/inference.py opens its YAML via a relative path.
        os.chdir(REPO)
        if REPO not in sys.path:
            sys.path.insert(0, REPO)

        import flash_head.src.modules.flash_head_model as fh_model
        import flash_head.src.pipeline.flash_head_pipeline as fh_pipe
        import torch

        # The worker also runs the LiveKit event loop and the WebRTC encoder in
        # this process.  Torch's default intra-op pool would grab every core and
        # starve them; one thread is enough for the wav2vec2 feature extractor
        # and the host<->device copies (the heavy work is on the GPU).
        torch.set_num_threads(1)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass

        fh_pipe.COMPILE_MODEL = bool(compile)
        fh_pipe.COMPILE_VAE = bool(compile)
        if not sage:
            fh_model.SAGE_ATTN_AVAILABLE = False

        import flash_head.inference as fh_inf

        self._torch = torch
        self._fh_inf = fh_inf
        self._fh_model = fh_model

        t0 = time.time()
        self._pipeline = _quiet(
            fh_inf.get_pipeline,
            world_size=1,
            ckpt_dir=CKPT_DIR,
            model_type=model_type,
            wav2vec_dir=WAV2VEC_DIR,
        )
        torch.cuda.synchronize()
        self.load_seconds = time.time() - t0

        p = fh_inf.get_infer_params()
        self.params = ModelParams(
            sample_rate=int(p["sample_rate"]),
            fps=int(p["tgt_fps"]),
            frame_num=int(p["frame_num"]),
            motion_frames_num=int(p["motion_frames_num"]),
            cached_audio_duration=int(p["cached_audio_duration"]),
            height=int(p["height"]),
            width=int(p["width"]),
        )
        self.vram_gib = torch.cuda.memory_allocated() / 2**30
        log.info(
            "FlashHead %s pipeline loaded in %.1fs (%.2f GiB, %s)",
            model_type,
            self.load_seconds,
            self.vram_gib,
            self.backend,
        )

    @property
    def backend(self) -> str:
        if self._fh_model.SAGE_ATTN_AVAILABLE:
            return "sageattention"
        if self._fh_model.FLASH_ATTN_3_AVAILABLE:
            return "flash_attn_3"
        if self._fh_model.FLASH_ATTN_2_AVAILABLE:
            return "flash_attn_2"
        return "sdpa"

    def prepare(self, image_path: str) -> None:
        """VAE-encode the conditioning photo and reset the motion stream."""
        t0 = time.time()
        _quiet(
            self._fh_inf.get_base_data,
            self._pipeline,
            cond_image_path_or_dir=image_path,
            base_seed=self._seed,
            use_face_crop=self._use_face_crop,
        )
        self._torch.cuda.synchronize()
        self.image_path = image_path
        self.prepare_seconds = time.time() - t0
        log.info("FlashHead conditioning image prepared in %.2fs", self.prepare_seconds)

    def reset(self) -> None:
        self._pipeline.reset_person_name()

    def generate(
        self, window: np.ndarray, audio_start_idx: int, audio_end_idx: int
    ) -> np.ndarray:
        emb = self._fh_inf.get_audio_embedding(
            self._pipeline, window, audio_start_idx, audio_end_idx
        )
        video = _quiet(self._fh_inf.run_pipeline, self._pipeline, emb)
        return video.to(self._torch.uint8).cpu().numpy()

    def warmup(self, chunks: int = 3) -> float:
        """Run a few silent chunks so the first real turn is not the slow path."""
        if chunks <= 0:
            return 0.0
        p = self.params
        window = np.zeros(p.window_samples, dtype=np.float32)
        t0 = time.time()
        for _ in range(chunks):
            self.generate(window, p.audio_start_idx, p.audio_end_idx)
        self.reset()
        elapsed = time.time() - t0
        log.info("FlashHead warmup: %d chunks in %.2fs", chunks, elapsed)
        return elapsed

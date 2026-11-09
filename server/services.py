"""Factories that turn config into Pipecat services.

Every provider choice lives here so the pipeline in bot.py never changes when a
provider does.
"""

from pathlib import Path

from loguru import logger
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService

from config import Settings


def _require(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is not set (see .env.example)")
    return value


def make_llm(cfg: Settings, system_instruction: str) -> LLMService:
    if cfg.llm_provider == "groq":
        from pipecat.services.groq.llm import GroqLLMService

        s = GroqLLMService.Settings(
            model=cfg.groq_llm_model,
            system_instruction=system_instruction,
            temperature=0.7,
        )
        # Only reasoning models (gpt-oss, qwen) accept reasoning_effort.
        if cfg.groq_reasoning_effort and ("gpt-oss" in cfg.groq_llm_model or "qwen" in cfg.groq_llm_model):
            s.reasoning_effort = cfg.groq_reasoning_effort
        return GroqLLMService(api_key=_require(cfg.groq_api_key, "GROQ_API_KEY"), settings=s)

    if cfg.llm_provider == "google":
        from pipecat.services.google.llm import GoogleLLMService

        return GoogleLLMService(
            api_key=_require(cfg.google_api_key, "GOOGLE_API_KEY"),
            settings=GoogleLLMService.Settings(
                model=cfg.google_llm_model,
                system_instruction=system_instruction,
                temperature=0.7,
            ),
        )

    raise ValueError(f"Unknown llm_provider {cfg.llm_provider!r}")


def make_stt(cfg: Settings) -> STTService:
    if cfg.stt_provider == "moonshine":
        from pipecat.services.moonshine.stt import MoonshineSTTService
        from pipecat.transcriptions.language import Language

        return MoonshineSTTService(
            settings=MoonshineSTTService.Settings(model=cfg.moonshine_model, language=Language.EN),
        )

    if cfg.stt_provider == "groq":
        from pipecat.services.groq.stt import GroqSTTService
        from pipecat.transcriptions.language import Language

        return GroqSTTService(
            api_key=_require(cfg.groq_api_key, "GROQ_API_KEY"),
            settings=GroqSTTService.Settings(model=cfg.groq_stt_model, language=Language.EN),
        )

    raise ValueError(f"Unknown stt_provider {cfg.stt_provider!r}")


KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


def _kokoro_files(cfg: Settings) -> tuple[Path, Path]:
    from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR

    model = "kokoro-v1.0.int8.onnx" if cfg.kokoro_precision == "int8" else "kokoro-v1.0.onnx"
    return KOKORO_CACHE_DIR / model, KOKORO_CACHE_DIR / "voices-v1.0.bin"


def _download_atomic(url: str, dest: Path) -> None:
    """Download to a temp file, then rename, so concurrent readers never see a partial file."""
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info(f"Downloading {url} -> {dest}")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)


def prefetch_models(cfg: Settings) -> None:
    """Fetch local model files once at server start (never inside a session)."""
    if cfg.tts_provider == "kokoro":
        for path in _kokoro_files(cfg):
            if not path.exists():
                _download_atomic(f"{KOKORO_RELEASE}/{path.name}", path)
    if cfg.stt_provider == "moonshine":
        from moonshine_voice import get_model_for_language, string_to_model_arch

        get_model_for_language("en", string_to_model_arch(cfg.moonshine_model))  # downloads once, cached


def make_tts(cfg: Settings) -> TTSService:
    if cfg.tts_provider == "kokoro":
        from pipecat.services.kokoro.tts import KokoroTTSService

        from tts_chunking import use_first_clause_chunking

        model_path, voices_path = _kokoro_files(cfg)
        tts = KokoroTTSService(
            model_path=str(model_path),
            voices_path=str(voices_path),
            settings=KokoroTTSService.Settings(voice=cfg.kokoro_voice, speed=cfg.kokoro_speed),
        )
        return use_first_clause_chunking(tts)

    if cfg.tts_provider == "groq":
        from pipecat.services.groq.tts import GroqTTSService

        return GroqTTSService(
            api_key=_require(cfg.groq_api_key, "GROQ_API_KEY"),
            settings=GroqTTSService.Settings(model=cfg.groq_tts_model, voice=cfg.groq_tts_voice),
        )

    raise ValueError(f"Unknown tts_provider {cfg.tts_provider!r}")


def make_avatar_video(cfg: Settings) -> FrameProcessor | None:
    """Server-side avatar renderer, inserted after TTS. None = browser renders the face."""
    if cfg.avatar_mode == "client3d":
        return None

    if cfg.avatar_mode == "simli":
        from pipecat.services.simli.video import SimliVideoService

        return SimliVideoService(
            api_key=_require(cfg.simli_api_key, "SIMLI_API_KEY"),
            face_id=_require(cfg.simli_face_id, "SIMLI_FACE_ID"),
        )

    raise NotImplementedError(f"avatar_mode {cfg.avatar_mode!r} is not implemented yet")

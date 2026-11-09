"""Data contracts shared by every service.

A Persona is everything needed to run one digital human: who it is (prompt),
how it sounds (voice), how it thinks (LLM), how it hears (STT) and how it looks
(avatar backend + source photo). Services exchange these as JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class AvatarBackend(StrEnum):
    SIMLI = "simli"  # managed photoreal (Simli face created from the persona photo)
    FLASHHEAD = "flashhead"  # self-hosted SoulX-FlashHead worker on a Modal GPU
    BROWSER3D = "browser3d"  # TalkingHead 3D avatar rendered in the browser from the audio
    NONE = "none"  # voice only


class AvatarStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    PROVISIONING = "provisioning"
    READY = "ready"
    FAILED = "failed"


class STTConfig(BaseModel):
    provider: str = "deepgram"
    model: str = "flux-general-en"
    language: str = "en"


class LLMConfig(BaseModel):
    # Fastest measured first-token time on the synthetic benchmark (docs/benchmarks.md).
    provider: str = "groq"
    model: str = "openai/gpt-oss-120b"
    temperature: float = 0.7
    # Tried in order if the primary fails ("provider/model").
    fallbacks: list[str] = Field(
        default_factory=lambda: ["openrouter/openai/gpt-4.1-mini", "openai/gpt-4.1-mini"]
    )


class VoiceConfig(BaseModel):
    provider: str = "cartesia"
    model: str = "sonic-3"
    voice_id: str | None = None  # provider default when None
    speed: float | None = None
    language: str = "en"


class AvatarConfig(BaseModel):
    backend: AvatarBackend = AvatarBackend.BROWSER3D
    status: AvatarStatus = AvatarStatus.NOT_REQUIRED
    photo_key: str | None = None  # object-storage key of the source photo
    simli_face_id: str | None = None
    error: str | None = None


class Persona(BaseModel):
    id: str = Field(default_factory=lambda: f"per_{uuid4().hex[:12]}")
    name: str = "Ava"
    system_prompt: str = ""
    greeting: str | None = "Greet the person warmly and briefly introduce yourself."
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    avatar: AvatarConfig = Field(default_factory=AvatarConfig)
    tools: list[str] = Field(default_factory=lambda: ["basics", "web_search"])
    max_session_minutes: int = 15
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionMetadata(BaseModel):
    """Attached to the LiveKit agent dispatch; the agent reads it to know what to run."""

    session_id: str = Field(default_factory=lambda: f"ses_{uuid4().hex[:12]}")
    persona_id: str | None = None
    # Inline persona (dev / synthetic tests); takes precedence over persona_id lookup.
    persona: Persona | None = None
    user_id: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class AvatarCue(BaseModel):
    """Body-language cue sent to browser-rendered avatars (topic: AVATAR_CUE_TOPIC)."""

    mood: str | None = None
    gesture: str | None = None


AVATAR_CUE_TOPIC = "avatar.cue"
AGENT_NAME = "avatar-agent"

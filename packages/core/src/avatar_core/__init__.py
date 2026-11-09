"""avatar_core: contracts and cross-cutting concerns shared by all avatar_talk services."""

from avatar_core.schemas import (
    AGENT_NAME,
    AVATAR_CUE_TOPIC,
    AvatarBackend,
    AvatarConfig,
    AvatarCue,
    AvatarStatus,
    LLMConfig,
    Persona,
    SessionMetadata,
    STTConfig,
    VoiceConfig,
)

__all__ = [
    "AGENT_NAME",
    "AVATAR_CUE_TOPIC",
    "AvatarBackend",
    "AvatarConfig",
    "AvatarCue",
    "AvatarStatus",
    "LLMConfig",
    "Persona",
    "SessionMetadata",
    "STTConfig",
    "VoiceConfig",
]

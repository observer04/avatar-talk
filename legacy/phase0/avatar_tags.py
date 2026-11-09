"""Strip inline avatar cues from the LLM stream and forward them to the client.

The LLM writes cues such as ``[mood:happy]`` or ``[gesture:thumbup]`` inline in
its reply. This processor sits between the LLM and TTS: it removes the cues so
they are never spoken, and sends each one to the browser as an RTVI server
message ``{"type": "avatar", "mood": ...}`` / ``{"type": "avatar", "gesture": ...}``.

Tokens arrive in fragments (``"[ges"``, ``"ture:wa"``, ``"ve] Hi"``), so the
parser buffers from ``[`` until the matching ``]``.
"""

import re

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

# Names understood by the browser renderer (TalkingHead setMood / playGesture).
MOODS = {"neutral", "happy", "sad", "angry", "fear", "disgust", "love"}
GESTURES = {"handup", "index", "ok", "thumbup", "thumbdown", "side", "shrug", "namaste", "yes", "no"}

_TAG_RE = re.compile(r"^\[\s*(mood|gesture)\s*:\s*([a-zA-Z_]+)\s*\]$")
_MAX_TAG_LEN = 32  # a '[' that runs longer than this is ordinary text


class TagParser:
    """Incremental parser: feed text fragments, get back (clean_text, cues)."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        out: list[str] = []
        cues: list[tuple[str, str]] = []
        for ch in text:
            if self._buf:
                self._buf += ch
                if ch == "]":
                    cue = parse_tag(self._buf)
                    if cue:
                        cues.append(cue)
                    else:
                        out.append(self._buf)
                    self._buf = ""
                elif len(self._buf) > _MAX_TAG_LEN or ch == "\n":
                    out.append(self._buf)
                    self._buf = ""
            elif ch == "[":
                self._buf = ch
            else:
                out.append(ch)
        return "".join(out), cues

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest

    def reset(self) -> None:
        self._buf = ""


def parse_tag(tag: str) -> tuple[str, str] | None:
    """Return (kind, value) for a valid cue, or None if it's not a cue."""
    m = _TAG_RE.match(tag)
    if not m:
        return None
    kind, value = m.group(1), m.group(2).lower()
    valid = MOODS if kind == "mood" else GESTURES
    if value not in valid:
        # Recognisably a cue, but an unknown name: drop it (never speak it).
        logger.debug(f"Ignoring unknown avatar {kind} {value!r}")
        return ("ignored", value)
    return (kind, value)


class AvatarTagProcessor(FrameProcessor):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._parser = TagParser()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            clean, cues = self._parser.feed(frame.text)
            for kind, value in cues:
                if kind != "ignored":
                    await self.push_frame(RTVIServerMessageFrame(data={"type": "avatar", kind: value}))
            if clean:
                frame.text = clean
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame | LLMFullResponseStartFrame):
            self._parser.reset()
        elif isinstance(frame, LLMFullResponseEndFrame):
            rest = self._parser.flush()
            if rest:
                await self.push_frame(LLMTextFrame(text=rest), direction)

        await self.push_frame(frame, direction)

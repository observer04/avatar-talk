"""Inline body-language cues: ``[mood:happy]``, ``[gesture:thumbup]``.

The LLM writes them inline; they must never be spoken or shown in the
transcript. Browser-rendered avatars receive them as messages; video avatars
simply ignore them (for now).
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable

log = logging.getLogger(__name__)

# Names understood by the browser renderer (TalkingHead setMood / playGesture).
MOODS = {"neutral", "happy", "sad", "angry", "fear", "disgust", "love"}
GESTURES = {"handup", "index", "ok", "thumbup", "thumbdown", "side", "shrug", "namaste", "yes", "no"}

_TAG_RE = re.compile(r"^\[\s*(mood|gesture)\s*:\s*([a-zA-Z_]+)\s*\]$")
_MAX_TAG_LEN = 32  # a '[' that runs longer than this is ordinary text

Cue = tuple[str, str]  # (kind, value)

CUE_PROMPT = """
Body language:
You control your face and hands by writing short cues inline. They are never spoken. Use them naturally and sparingly: at most one mood and one gesture per reply, placed right before the words they go with.
- Mood: [mood:happy] [mood:neutral] [mood:sad] [mood:angry] [mood:fear] [mood:disgust] [mood:love]
- Gesture: [gesture:yes] (nod) [gesture:no] (head shake) [gesture:handup] (raise hand / wave) [gesture:index] (point up) [gesture:ok] [gesture:thumbup] [gesture:thumbdown] [gesture:side] (open palm) [gesture:shrug] [gesture:namaste]
Example: "[mood:happy] Oh, that's great news! [gesture:thumbup] Congratulations."
""".strip()


def parse_tag(tag: str) -> Cue | None:
    """(kind, value) for a recognised cue; ("ignored", value) for a cue with an unknown name."""
    m = _TAG_RE.match(tag)
    if not m:
        return None
    kind, value = m.group(1), m.group(2).lower()
    if value not in (MOODS if kind == "mood" else GESTURES):
        return ("ignored", value)
    return (kind, value)


class TagParser:
    """Incremental parser: feed text fragments, get back (clean_text, cues)."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> tuple[str, list[Cue]]:
        out: list[str] = []
        cues: list[Cue] = []
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


async def strip_cues(
    text: AsyncIterable[str], on_cue: Callable[[Cue], None] | None = None
) -> AsyncIterator[str]:
    """Remove cues from a text stream, calling on_cue for each valid one.

    Chunks without '[' pass through untouched (keeps TimedString timing info).
    """
    parser = TagParser()
    async for chunk in text:
        if "[" not in chunk and not parser._buf:
            yield chunk
            continue
        clean, cues = parser.feed(chunk)
        for kind, value in cues:
            if kind != "ignored" and on_cue:
                on_cue((kind, value))
        if clean:
            yield clean
    rest = parser.flush()
    if rest:
        yield rest

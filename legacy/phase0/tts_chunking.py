"""Lower time-to-first-audio for non-streaming local TTS (Kokoro on CPU).

Kokoro renders a whole chunk before any audio comes out, so its first-audio
latency grows with the length of the first chunk (a 110-char opener took ~2.8 s
on a laptop CPU). This aggregator:

- releases the *first* clause of each reply early, at , ; : — once it has a few
  words (only when the punctuation is followed by a space, so "11:20" and
  "1,000" are never split);
- if the opening runs long with no clause punctuation, splits it at a word
  boundary;
- splits very long later sentences at clause boundaries.

Everything else is still spoken in whole sentences, which keeps prosody natural.
"""

from collections.abc import AsyncIterator

from pipecat.services.tts_service import TTSService
from pipecat.utils.text.base_text_aggregator import Aggregation, TextType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator, _LookaheadState

CLAUSE_PUNCTUATION = ",;:—–"


class FirstClauseAggregator(SimpleTextAggregator):
    def __init__(
        self,
        first_min_chars: int = 12,
        first_max_chars: int = 60,
        long_min_chars: int = 90,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._first_min = first_min_chars
        self._first_max = first_max_chars
        self._long_min = long_min_chars
        self._emitted_in_reply = False
        self._prev_char = ""

    def _emit_buffer(self) -> Aggregation:
        chunk = self._text.strip()
        self._text = ""
        self._emitted_in_reply = True
        return Aggregation(text=chunk, type=TextType.SENTENCE)

    async def aggregate(self, text: str) -> AsyncIterator[Aggregation]:
        if self._aggregation_type == TextType.TOKEN:
            async for agg in super().aggregate(text):
                yield agg
            return

        for char in text:
            prev, self._prev_char = self._prev_char, char
            self._text += char
            result = await self._check_sentence_with_lookahead(char)
            if result:
                self._emitted_in_reply = True
                yield result
                continue
            if not char.isspace() or self._lookahead_state is not _LookaheadState.IDLE:
                continue
            n = len(self._text.strip())
            if prev in CLAUSE_PUNCTUATION:
                if (not self._emitted_in_reply and n >= self._first_min) or n >= self._long_min:
                    yield self._emit_buffer()
            elif not self._emitted_in_reply and n >= self._first_max:
                yield self._emit_buffer()

    def _clear_reply_state(self):
        self._emitted_in_reply = False
        self._prev_char = ""

    async def flush(self) -> Aggregation | None:
        result = await super().flush()
        self._clear_reply_state()
        return result

    async def handle_interruption(self):
        await super().handle_interruption()
        self._clear_reply_state()

    async def reset(self):
        await super().reset()
        self._clear_reply_state()


def use_first_clause_chunking(tts: TTSService) -> TTSService:
    """Swap the TTS service's sentence aggregator for FirstClauseAggregator.

    Pipecat 1.15 has no constructor hook for this, so we replace the attribute;
    the guard keeps us honest if a future version renames it.
    """
    current = getattr(tts, "_text_aggregator", None)
    if not isinstance(current, SimpleTextAggregator) or current.aggregation_type != TextType.SENTENCE:
        return tts
    tts._text_aggregator = FirstClauseAggregator(
        aggregation_type=current.aggregation_type, language=current.language
    )
    return tts

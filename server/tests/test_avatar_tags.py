import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.tests.utils import run_test

from avatar_tags import AvatarTagProcessor, TagParser


def feed_all(chunks):
    p = TagParser()
    text, cues = "", []
    for c in chunks:
        t, cs = p.feed(c)
        text += t
        cues += cs
    return text + p.flush(), cues


def test_tag_split_across_tokens():
    text, cues = feed_all(["[mo", "od:hap", "py] Hello", " there [gest", "ure:thumbup", "] nice"])
    assert text == " Hello there  nice"
    assert cues == [("mood", "happy"), ("gesture", "thumbup")]


def test_non_tag_brackets_are_kept():
    text, cues = feed_all(["Use [brackets] like this", " [mood:"])
    assert text == "Use [brackets] like this [mood:"
    assert cues == []


def test_unknown_cue_is_dropped_not_spoken():
    text, cues = feed_all(["[gesture:moonwalk]Hi"])
    assert text == "Hi"
    assert cues == [("ignored", "moonwalk")]


def test_runaway_bracket_flushes_as_text():
    long = "[" + "x" * 40 + " end"
    text, _ = feed_all([long])
    assert text == long


@pytest.mark.asyncio
async def test_processor_emits_rtvi_and_strips_text():
    frames = [
        LLMFullResponseStartFrame(),
        LLMTextFrame(text="[mood:happy]"),
        LLMTextFrame(text=" Hi! [gesture:"),
        LLMTextFrame(text="namaste] Welcome."),
        LLMFullResponseEndFrame(),
    ]
    down, _ = await run_test(AvatarTagProcessor(), frames_to_send=frames)
    msgs = [f.data for f in down if isinstance(f, RTVIServerMessageFrame)]
    spoken = "".join(f.text for f in down if isinstance(f, LLMTextFrame))
    assert msgs == [{"type": "avatar", "mood": "happy"}, {"type": "avatar", "gesture": "namaste"}]
    assert spoken == " Hi!  Welcome."

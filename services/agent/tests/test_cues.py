from agent.cues import TagParser, strip_cues


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


def test_non_tag_brackets_kept_and_unknown_cues_dropped():
    text, cues = feed_all(["Use [brackets] here. [gesture:moonwalk]Hi"])
    assert text == "Use [brackets] here. Hi"
    assert cues == [("ignored", "moonwalk")]


async def test_strip_cues_stream_reports_valid_cues_only():
    async def gen():
        for c in ["[mood:", "happy] Hi! ", "[gesture:wave] [gesture:yes]ok"]:
            yield c

    seen = []
    out = "".join([c async for c in strip_cues(gen(), seen.append)])
    assert out == " Hi!  ok"
    assert seen == [("mood", "happy"), ("gesture", "yes")]

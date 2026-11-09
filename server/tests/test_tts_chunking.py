from tts_chunking import FirstClauseAggregator


async def collect(agg, chunks):
    out = []
    for c in chunks:
        async for a in agg.aggregate(c):
            out.append(a.text)
    rest = await agg.flush()
    if rest:
        out.append(rest.text)
    return out


async def test_first_clause_released_early_then_sentences():
    agg = FirstClauseAggregator()
    text = "It's about ten degrees in London, partly cloudy and calm. Want the forecast too? Sure, here it is."
    out = await collect(agg, [text[i:i + 7] for i in range(0, len(text), 7)])
    assert out[0] == "It's about ten degrees in London,"
    assert out[1] == "partly cloudy and calm."
    # later clauses in the same reply wait for full sentences
    assert "Sure, here it is." in out


async def test_short_opening_clause_not_split():
    out = await collect(FirstClauseAggregator(), ["Hi, I'm Ava. Nice to meet you."])
    assert out[0] == "Hi, I'm Ava."


async def test_flag_resets_between_replies():
    agg = FirstClauseAggregator()
    first = await collect(agg, ["In other words, that is a good question. Yes."])
    assert first[0] == "In other words,"
    out = await collect(agg, ["In my experience, it depends on the day. Okay."])
    assert out[0] == "In my experience,"


async def test_times_and_numbers_are_not_split():
    out = await collect(FirstClauseAggregator(), ["Right now in Tokyo it's 11:", "20 PM, and it costs 1,000 yen. Okay."])
    assert out[0] == "Right now in Tokyo it's 11:20 PM,"


async def test_long_opening_without_punctuation_splits_at_word_boundary():
    text = "A cheeky dragon named Ember discovers a love for cooking after stumbling onto a human kitchen, and more."
    out = await collect(FirstClauseAggregator(), [text])
    assert len(out[0]) <= 70 and not out[0].endswith(" ")
    assert " ".join(out).replace("  ", " ") == text

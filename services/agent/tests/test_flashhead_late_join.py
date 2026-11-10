"""A cold FlashHead worker joins after the session started voice-only: the agent's
audio must move to the avatar, but only once the agent is not mid-reply."""

import asyncio
from types import SimpleNamespace

import pytest
from agent.avatars import flashhead


class FakeOutput:
    def __init__(self) -> None:
        self.tail = None

    def replace_audio_tail(self, sink) -> None:
        self.tail = sink


@pytest.fixture
def joined(monkeypatch):
    event = asyncio.Event()

    async def wait_for_participant(*, room, identity):
        await event.wait()

    monkeypatch.setattr(flashhead.utils, "wait_for_participant", wait_for_participant)
    monkeypatch.setattr(flashhead, "_avatar_output", lambda room: "avatar-sink")
    return event


async def test_switches_after_late_join_once_agent_stops_speaking(joined):
    session = SimpleNamespace(agent_state="speaking", output=FakeOutput())
    task = asyncio.create_task(flashhead.FlashHeadBackend()._switch_when_ready(session, room=None))

    joined.set()
    await asyncio.sleep(0.3)
    assert session.output.tail is None  # still speaking: don't cut the reply off

    session.agent_state = "listening"
    await asyncio.wait_for(task, 1)
    assert session.output.tail == "avatar-sink"


async def test_gives_up_when_avatar_never_joins(joined, monkeypatch):
    monkeypatch.setattr(flashhead.settings, "flashhead_late_join_timeout", 0.05)
    session = SimpleNamespace(agent_state="listening", output=FakeOutput())
    await flashhead.FlashHeadBackend()._switch_when_ready(session, room=None)
    assert session.output.tail is None


async def test_aclose_cancels_pending_switch(joined):
    backend = flashhead.FlashHeadBackend()
    session = SimpleNamespace(agent_state="listening", output=FakeOutput())
    backend._late_task = asyncio.create_task(backend._switch_when_ready(session, room=None))
    await asyncio.sleep(0)
    await backend.aclose()
    await asyncio.sleep(0)
    assert backend._late_task.cancelled()

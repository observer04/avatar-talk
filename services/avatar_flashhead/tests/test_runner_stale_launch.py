"""A launch can wait in the GPU queue until after the call ended. The worker must
leave when the agent never shows up instead of holding the GPU for an hour."""

import asyncio

from avatar_flashhead.runner import AvatarRunner


class NeverStarts:
    async def start(self) -> None:
        await asyncio.Event().wait()


async def test_leaves_when_agent_never_joins():
    runner = AvatarRunner.__new__(AvatarRunner)
    runner._recv = NeverStarts()
    runner._done = asyncio.Event()
    runner._agent_wait_seconds = 0.05
    runner._reason = "unknown"

    assert await asyncio.wait_for(runner._start_receiver(), 1) is False
    assert "stale launch" in runner.reason

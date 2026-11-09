"""Web search via Tavily (answers + sources, designed for LLM consumption)."""

from __future__ import annotations

import aiohttp
from livekit.agents import RunContext, ToolError, function_tool

from agent.settings import settings
from agent.tools import filler


def enabled() -> bool:
    return bool(settings.tavily_api_key)


@function_tool
async def search_web(context: RunContext, query: str) -> dict:
    """Search the web for current information (news, facts, prices, events, anything recent).

    Args:
        query: What to search for, as a short natural-language query.
    """
    filler(context)
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json={"query": query, "max_results": 4, "include_answer": True, "search_depth": "basic"},
        ) as r:
            if r.status != 200:
                raise ToolError(f"Search failed ({r.status})")
            data = await r.json()
    return {
        "answer": data.get("answer"),
        "results": [
            {"title": x.get("title"), "snippet": (x.get("content") or "")[:400]}
            for x in data.get("results", [])[:4]
        ],
    }


TOOLS = [search_web]

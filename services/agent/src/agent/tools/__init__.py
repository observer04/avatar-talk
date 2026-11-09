"""Tool registry.

A tool module exposes ``TOOLS``: a list of LiveKit ``@function_tool`` functions.
Personas list the modules they may use (``persona.tools``). Tools that call
other microservices (memory, RAG, actions) are added as new modules here.
"""

from __future__ import annotations

import importlib
import logging
import random

from livekit.agents import RunContext

log = logging.getLogger(__name__)

_FILLERS = ("Let me check.", "One moment.", "Let me look that up.", "Just a sec.")


def filler(context: RunContext) -> None:
    """Speak a short filler while a slow tool runs (not awaited, not added to the chat)."""
    try:
        context.session.say(random.choice(_FILLERS), add_to_chat_ctx=False)
    except Exception as e:  # never let UX sugar break the tool
        log.debug("filler failed: %s", e)

AVAILABLE = ("basics", "web_search")


def load_tools(names: list[str]) -> list:
    tools: list = []
    for name in names:
        if name not in AVAILABLE:
            log.warning("unknown tool module %r (available: %s)", name, AVAILABLE)
            continue
        module = importlib.import_module(f"agent.tools.{name}")
        if hasattr(module, "enabled") and not module.enabled():
            log.info("tool module %s disabled (missing config)", name)
            continue
        tools.extend(module.TOOLS)
    return tools

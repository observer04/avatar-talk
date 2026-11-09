"""Tool registry.

Each module in this package exposes ``TOOLS``: a list of Pipecat "direct
functions" (async functions whose first parameter is ``params:
FunctionCallParams``; name, docstring and type hints become the tool schema).

To add a capability: create ``tools/<name>.py`` with a ``TOOLS`` list and add
``<name>`` to ``ENABLED_TOOLS`` in .env. Nothing else changes.

Decorate a tool with ``@slow`` if it usually takes more than ~1 s (network
lookups etc.): the avatar then says a short filler ("Let me check.") while it
runs instead of sitting in silence.
"""

import importlib

from loguru import logger


def slow(fn):
    fn.say_filler = True
    return fn


def slow_tool_names(tools: list) -> set[str]:
    return {f.__name__ for f in tools if getattr(f, "say_filler", False)}


def load_tools(module_names: list[str]) -> list:
    tools: list = []
    for name in module_names:
        module = importlib.import_module(f"tools.{name}")
        found = list(getattr(module, "TOOLS", []))
        logger.info(f"Loaded {len(found)} tool(s) from tools.{name}: {[f.__name__ for f in found]}")
        tools.extend(found)
    return tools

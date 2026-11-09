"""Structured logging with correlation IDs.

Every log line carries the service name and, inside a session, the session_id,
room and persona_id, so logs from the API, agent and avatar worker can be
joined on one conversation.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("log_context", default={})
_service_name = "avatar-talk"


def bind(**fields: Any) -> contextvars.Token:
    """Add correlation fields (session_id=..., room=...) to all logs in this context."""
    return _context.set({**_context.get(), **{k: v for k, v in fields.items() if v is not None}})


def current_context() -> dict[str, Any]:
    return dict(_context.get())


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.ctx = _context.get()
        record.service = _service_name
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", _service_name),
            "logger": record.name,
            "msg": record.getMessage(),
            **getattr(record, "ctx", {}),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = getattr(record, "ctx", {})
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<5} {record.name}: {record.getMessage()}"
        if ctx_str:
            base += f"  [{ctx_str}]"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def _install_record_factory() -> None:
    """Put correlation fields on every LogRecord, whatever handler formats it
    (e.g. LiveKit's own JSON handler prints non-standard record attributes)."""
    base = logging.getLogRecordFactory()
    if getattr(base, "_avatar_ctx", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        # Only these two attributes: frameworks pass their own `extra` keys (LiveKit
        # uses e.g. "room"), and logging refuses extras that overwrite record attributes.
        record.service = _service_name
        record.ctx = _context.get()
        return record

    factory._avatar_ctx = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def setup_logging(
    service_name: str, level: str = "INFO", fmt: str = "console", *, install_handler: bool = True
) -> None:
    """Configure logging. install_handler=False keeps a framework's own handler
    (LiveKit's CLI installs one) and only adds correlation fields to records."""
    global _service_name
    _service_name = service_name
    _install_record_factory()
    if not install_handler:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "botocore", "aiobotocore", "hpack", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

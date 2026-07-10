"""Structured JSON logging with context-variable correlation."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import logging


run_id_var: ContextVar[Optional[int]] = ContextVar("run_id", default=None)
exp_var: ContextVar[Optional[str]] = ContextVar("exp", default=None)
chunk_var: ContextVar[Optional[str]] = ContextVar("chunk", default=None)


class StructuredFormatter(logging.Formatter):
    """JSON formatter that emits all context variables alongside the record."""

    _SKIP_KEYS: set[str] = frozenset({
        "name", "msg", "args", "created", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs",
        "message", "pathname", "process", "processName",
        "relativeCreated", "thread", "threadName", "exc_info",
        "exc_text", "stack_info", "getMessage",
    })

    def format(self, record: logging.LogRecord) -> str:
        base: dict = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        rv = run_id_var.get()
        if rv is not None:
            base["run_id"] = rv
        ev = exp_var.get()
        if ev is not None:
            base["expiration"] = ev
        cv = chunk_var.get()
        if cv is not None:
            base["chunk"] = cv

        for key, value in record.__dict__.items():
            if key not in self._SKIP_KEYS:
                base[key] = value

        return json.dumps(base, default=str)


def setup_structured_logging(level: int = logging.INFO) -> None:
    """Replace root handlers with a single JSON-line handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
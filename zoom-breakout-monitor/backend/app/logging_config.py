"""Structured (JSON-line) logging.

Chat message *content* is never emitted at INFO level. Helpers in this module
are the single place that decides how much of a message may be logged.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn's own handlers would duplicate every line in a non-JSON format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    # The access log prints full URLs; keep it out of INFO-level output.
    logging.getLogger("uvicorn.access").setLevel(
        logging.DEBUG if level == "DEBUG" else logging.WARNING
    )


def redact(text: str | None, *, limit: int = 40) -> str:
    """Render message content safe for DEBUG-level logs.

    Returns a length descriptor rather than the text itself unless the logger
    is actually running at DEBUG, in which case a short prefix is included.
    """
    if text is None:
        return "<none>"
    if not logging.getLogger().isEnabledFor(logging.DEBUG):
        return f"<{len(text)} chars>"
    snippet = text[:limit]
    suffix = "…" if len(text) > limit else ""
    return f"{snippet}{suffix}"

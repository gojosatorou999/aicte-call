"""Structured JSON logging with PII redaction.

Every log record is a JSON object on stdout (optionally teed to a file).
The redaction processor scrubs anything that looks like a phone number or
a government-ID-like number before serialisation - belt and braces on top
of the "never log a raw msisdn" rule in the code.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, cast

import structlog

_PHONE_RE = re.compile(r"\+?\d{10,13}")
_LONG_ID_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")


def _redact_pii(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for k, v in list(event_dict.items()):
        if not isinstance(v, str):
            continue
        v = _PHONE_RE.sub("<redacted:phone>", v)
        v = _LONG_ID_RE.sub("<redacted:id>", v)
        event_dict[k] = v
    return event_dict


class _Tee:
    """Fan one write to several streams (stdout + a durable file copy)."""

    def __init__(self, *streams: Any) -> None:
        self._streams = streams

    def write(self, data: str) -> None:
        for stream in self._streams:
            with contextlib.suppress(Exception):  # a broken sink must not kill logging
                stream.write(data)

    def flush(self) -> None:
        for stream in self._streams:
            with contextlib.suppress(Exception):
                stream.flush()


def configure_logging(
    level: str = "INFO",
    service: str = "acp_voice",
    log_file: str | None = None,
) -> None:
    """Idempotent. Call once at process start."""
    sink: Any = sys.stdout
    if log_file:
        try:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8", buffering=1)
            sink = _Tee(sys.stdout, handle)
        except OSError:
            sink = sys.stdout  # an unwritable path must not stop boot

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_pii,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sink),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))

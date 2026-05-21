"""Stdlib ``logging.Handler`` that captures ADK log records per tick.

ADK Python uses the standard ``logging`` module under the
``google_adk.*`` namespace.  At ``DEBUG`` level the framework emits the
full LLM prompt + response on every model call (per the docs at
https://adk.dev/observability/logging/).  We attach a handler at that
level to the ``google_adk`` parent logger, buffer the records during a
tick, and drain them to ``runs/<id>/obs/logs/<tick>.json`` at tick end.

The handler also captures records from our own ``stockbot.*`` loggers —
report cache hits, retry attempts, contract violations, anything we
``logger.info`` / ``.debug`` / ``.warning`` deliberately on the
``stockbot.observability`` namespace.

Drain semantics: the buffer is reset on every drain so each per-tick
file is self-contained.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC
from pathlib import Path
from typing import Any


class TickBufferedLogHandler(logging.Handler):
    """Capture log records into an in-memory buffer; drain on demand.

    Attach to the ``google_adk`` and ``stockbot`` parent loggers at DEBUG
    level.  Records are converted to JSON-safe dicts at emit time so the
    buffer remains serialisable even if a contributing logger's args contain
    objects that go stale after the call returns.
    """

    def __init__(self) -> None:
        """Initialise the handler at DEBUG level with an empty buffer."""
        super().__init__(level=logging.DEBUG)

        # One dict per emitted record; preserves emission order.
        self._buffer: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Convert a LogRecord to a JSON-safe dict and append to the buffer.

        Uses ``record.getMessage()`` to apply % formatting against ``args``,
        so the captured ``message`` field is the fully-formatted string the
        user would see in a console handler.

        Parameters
        ----------
        record:
            The log record handed to us by the logging framework.
        """
        try:
            message = record.getMessage()
        except Exception:
            # Defensive — if formatting fails, fall back to the raw format
            # string so we still capture the event.
            message = str(record.msg)

        # ``record.created`` is a float epoch seconds; convert to ISO via the
        # built-in formatter for legibility.
        event: dict[str, Any] = {
            "ts":      _isoformat_ts(record.created),
            "level":   record.levelname,
            "logger":  record.name,
            "message": message,
        }

        # Optional structured fields — only include when present so the JSON
        # file stays compact for the common case.
        if record.exc_info:
            # ``logging.Formatter`` formats exc_info into a string when asked;
            # we synthesise a minimal formatter inline to avoid a class attr.
            event["exc_info"] = logging.Formatter().formatException(record.exc_info)

        # Custom attributes attached via ``logger.info(..., extra={...})``.
        # ``LogRecord.__dict__`` contains both stdlib fields and any extras;
        # filter to the keys *not* defined by stdlib so we capture user extras
        # only.
        extras = _extract_extras(record)
        if extras:
            event["extra"] = extras

        self._buffer.append(event)

    def drain_to_file(self, path: Path, *, tick_id: str | None = None) -> None:
        """Serialise the buffered events to ``path`` and reset the buffer.

        Parameters
        ----------
        path:
            Destination JSON file.  Parent directories are created.
        tick_id:
            Optional tick identifier embedded in the document.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        document: dict[str, Any] = {
            "tick_id": tick_id,
            "events":  list(self._buffer),
        }

        path.write_text(json.dumps(document, indent=2, default=str))

        self._buffer = []


def _isoformat_ts(epoch_seconds: float) -> str:
    """Return an ISO-8601 UTC timestamp from epoch seconds.

    Parameters
    ----------
    epoch_seconds:
        Seconds since the unix epoch (typically ``LogRecord.created``).

    Returns
    -------
    str
        ISO-8601 representation in UTC, e.g. ``"2026-05-21T08:14:32.123456+00:00"``.
    """
    from datetime import datetime

    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()


# Stdlib field names on ``LogRecord`` — anything outside this set on a record's
# ``__dict__`` was added via ``extra=`` and is worth preserving.  Snapshot taken
# from the Python 3.12 ``logging`` source.
_STDLIB_LOGRECORD_FIELDS: frozenset[str] = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


def _extract_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Return the user-attached ``extra=`` fields from a LogRecord.

    Filters out stdlib-owned attributes; the leftover are the keys callers
    passed via ``logger.info("...", extra={"k": v})``.

    Parameters
    ----------
    record:
        The log record under inspection.

    Returns
    -------
    dict
        Mapping of extra-field name to value.  Empty if no extras were set.
    """
    extras = {
        name: value
        for name, value in record.__dict__.items()
        if name not in _STDLIB_LOGRECORD_FIELDS and not name.startswith("_")
    }
    return extras

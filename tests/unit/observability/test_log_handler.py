"""Unit tests for ``observability.log_handler.TickBufferedLogHandler``.

Pin the capture-and-drain contract — emit collects records into the
buffer; drain_to_file serialises them as JSON and clears the buffer.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from observability.log_handler import TickBufferedLogHandler


def _attach(handler: logging.Handler, name: str) -> logging.Logger:
    """Build a fresh logger with the handler attached at DEBUG level.

    Uses a unique logger name per test so handlers don't bleed between tests
    in the same process (stdlib ``logging`` uses a global registry).
    """
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def test_emit_captures_level_and_logger_and_message(tmp_path: Path):
    """A simple ``logger.info`` call must produce a structured event."""
    handler = TickBufferedLogHandler()
    logger  = _attach(handler, "stockbot.test.simple")

    logger.info("hello %s", "world")

    out = tmp_path / "logs.json"
    handler.drain_to_file(out, tick_id="x")
    payload = json.loads(out.read_text())

    assert payload["tick_id"] == "x"
    assert len(payload["events"]) == 1

    event = payload["events"][0]

    assert event["level"]   == "INFO"
    assert event["logger"]  == "stockbot.test.simple"
    assert event["message"] == "hello world"


def test_emit_captures_extra_fields(tmp_path: Path):
    """The ``extra=`` kwarg must survive into the captured event."""
    handler = TickBufferedLogHandler()
    logger  = _attach(handler, "stockbot.test.extra")

    logger.info("cache_hit", extra={"ticker": "AAPL", "analyst": "news"})

    handler.drain_to_file(tmp_path / "out.json")
    payload = json.loads((tmp_path / "out.json").read_text())

    event = payload["events"][0]
    assert event["extra"] == {"ticker": "AAPL", "analyst": "news"}


def test_emit_captures_debug_level(tmp_path: Path):
    """DEBUG records (ADK's full prompt + response level) must be captured."""
    handler = TickBufferedLogHandler()
    logger  = _attach(handler, "stockbot.test.debug")

    logger.debug("full prompt: %s", "system instruction body")

    handler.drain_to_file(tmp_path / "out.json")
    events = json.loads((tmp_path / "out.json").read_text())["events"]

    assert events[0]["level"]   == "DEBUG"
    assert events[0]["message"] == "full prompt: system instruction body"


def test_drain_resets_buffer(tmp_path: Path):
    """Successive drains must not duplicate earlier records."""
    handler = TickBufferedLogHandler()
    logger  = _attach(handler, "stockbot.test.reset")

    logger.info("tick0")
    handler.drain_to_file(tmp_path / "t0.json")

    logger.info("tick1")
    handler.drain_to_file(tmp_path / "t1.json")

    t0_events = json.loads((tmp_path / "t0.json").read_text())["events"]
    t1_events = json.loads((tmp_path / "t1.json").read_text())["events"]

    assert [e["message"] for e in t0_events] == ["tick0"]
    assert [e["message"] for e in t1_events] == ["tick1"]


def test_emit_captures_exception_info(tmp_path: Path):
    """``logger.exception`` must surface the traceback in the event."""
    handler = TickBufferedLogHandler()
    logger  = _attach(handler, "stockbot.test.exc")

    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("something broke")

    handler.drain_to_file(tmp_path / "out.json")
    event = json.loads((tmp_path / "out.json").read_text())["events"][0]

    assert event["level"] == "ERROR"
    assert "ValueError: boom" in event["exc_info"]


def test_drain_creates_parent_dirs(tmp_path: Path):
    """The handler must create missing parent directories on drain."""
    handler = TickBufferedLogHandler()
    out = tmp_path / "nested" / "logs.json"

    handler.drain_to_file(out)

    assert out.exists()

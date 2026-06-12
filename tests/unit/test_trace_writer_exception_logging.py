"""S7 — strategist trace exception logs via logger.exception().

``observability/trace.py`` previously did
``with contextlib.suppress(Exception): tw.snapshot(...)`` — silently
swallowing any serialisation failure.  Tick 1's ``03_strategist`` trace
was missing in baseline-2025-09 because the LLM ran but the trace write
crashed and the suppress hid it.

The fix logs via ``logger.exception`` inside the suppress so single-tick
drops are not invisible while the suppress still keeps the run alive.

The actual helper exposed by the module is ``trace_maybe`` (promoted from
``_trace_maybe`` in A-097.o); this test uses the real name and the real
``(state, label, payload, *, state_keys=None)`` signature.
"""
from __future__ import annotations

import logging

import pytest

from observability.trace import trace_maybe


class _ExplodingTraceWriter:
    """Stand-in TraceWriter whose ``snapshot`` raises on every call."""

    def snapshot(self, *args, **kwargs) -> None:
        """Always raise to simulate a serialisation crash."""
        raise RuntimeError("simulated trace serialisation crash")


def test_trace_failure_logs_exception(caplog: pytest.LogCaptureFixture) -> None:
    """A snapshot crash logs an exception record but does not propagate."""

    # ``trace_maybe`` looks up ``state["temp:_trace"]`` via ``.get``; a plain
    # dict satisfies that interface without needing an ADK session object.
    state = {"temp:_trace": _ExplodingTraceWriter()}

    caplog.set_level(logging.WARNING, logger="observability.trace")

    # Should not raise — the try/except keeps the run alive.
    trace_maybe(state, label="03_strategist", payload={"x": 1})

    exception_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert exception_records, (
        "expected a logged warning/exception when the trace writer crashes"
    )
    # ``logger.exception`` attaches the exception traceback as ``exc_info``;
    # the crash message appears in ``exc_text`` (formatted traceback string)
    # rather than the log message itself.  Check both surfaces.
    def _record_contains_crash(record: logging.LogRecord) -> bool:
        """Return True if the log record carries the crash message anywhere."""
        if "simulated trace serialisation crash" in record.getMessage():
            return True

        exc_info = record.exc_info
        if exc_info and exc_info[1] is not None:
            return "simulated trace serialisation crash" in str(exc_info[1])

        return False

    assert any(_record_contains_crash(r) for r in exception_records), (
        f"expected the crash message in the log records, got: "
        f"{[(r.getMessage(), r.exc_info) for r in exception_records]}"
    )

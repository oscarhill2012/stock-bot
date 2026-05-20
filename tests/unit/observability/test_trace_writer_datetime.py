"""Guard test — TraceWriter.finalise must serialise datetime payloads.

The strategist after-model composite used to coerce datetimes out of
the LLM response before tracing.  A2.3 deletes that composite; the
trace writer is now relied on to handle datetime serialisation via
``json.dumps(default=str)``.

This test pins that contract by writing a section whose payload
contains a raw ``datetime`` and asserting ``finalise`` does not raise.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from observability.trace import TraceWriter


def test_trace_writer_serialises_datetime_payload(tmp_path: Path) -> None:
    """Writer must round-trip a datetime via default=str without raising."""
    tw = TraceWriter()
    tw.snapshot(
        "01_test_datetime",
        {"recorded_at": datetime(2026, 5, 20, 13, 30, tzinfo=UTC)},
    )

    out_path = tmp_path / "trace.json"
    # Must not raise on the datetime payload.
    tw.finalise(out_path)

    # And the resulting file must be valid JSON containing a string form
    # of the datetime (default=str coercion).
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert "01_test_datetime" in payload
    recorded = payload["01_test_datetime"]["data"]["recorded_at"]
    assert isinstance(recorded, str)
    assert "2026-05-20" in recorded

"""_trace_maybe is a single dict-lookup no-op when state has no '_trace' key."""
from __future__ import annotations


def test_trace_maybe_returns_quickly_with_no_trace():
    """No '_trace' in state → no allocation, no exception."""
    from observability.trace import _trace_maybe
    _trace_maybe({}, "01_x", {"data": "payload"})


def test_trace_maybe_routes_to_writer():
    """'_trace' in state → snapshot routed to the writer."""
    from observability.trace import TraceWriter, _trace_maybe
    tw = TraceWriter()
    _trace_maybe({"_trace": tw}, "01_x", {"data": 1})
    assert "01_x" in tw._sections

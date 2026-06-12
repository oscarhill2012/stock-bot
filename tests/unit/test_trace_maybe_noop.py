"""trace_maybe is a single dict-lookup no-op when state has no 'temp:_trace' key."""
from __future__ import annotations


def test_trace_maybe_returns_quickly_with_no_trace():
    """No 'temp:_trace' in state → no allocation, no exception."""
    from observability.trace import trace_maybe
    trace_maybe({}, "01_x", {"data": "payload"})


def test_trace_maybe_routes_to_writer():
    """'temp:_trace' in state → snapshot routed to the writer."""
    from observability.trace import TraceWriter, trace_maybe
    tw = TraceWriter()
    trace_maybe({"temp:_trace": tw}, "01_x", {"data": 1})
    assert "01_x" in tw._sections

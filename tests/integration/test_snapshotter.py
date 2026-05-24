from unittest.mock import MagicMock, patch

import pytest

from agents.snapshot.agent import build_snapshotter
from broker.fake import FakeBroker


def _make_ctx(state: dict) -> MagicMock:
    """Build a mock InvocationContext that satisfies the agent's needs.

    The snapshotter now yields an ``Event`` whose ``invocation_id`` field is
    a Pydantic-validated string, so the mock must return a real string
    rather than the default ``MagicMock`` attribute proxy.
    """

    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_snapshotter_writes_state():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    state = {"tick_id": "tick-001"}
    ctx = _make_ctx(state)
    with patch("yfinance.Ticker") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = MagicMock(
            empty=False,
            **{"__getitem__": lambda self, key: MagicMock(**{"iloc.__getitem__": lambda s, i: 470.0})}
        )
        mock_yf.return_value = mock_ticker
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    assert snap["bot_total_value"] == 10_000.0
    assert snap["tick_id"] == "tick-001"


@pytest.mark.asyncio
async def test_snapshotter_accepts_iso_string_as_of():
    """state["as_of"] arriving as an ISO-8601 string must not raise AsOfRequiredError.

    Locks in the fix that dropped the ``isinstance(raw_as_of, datetime)``
    pre-filter and now passes ``raw_as_of`` directly to ``resolve_as_of``.
    """
    from datetime import datetime

    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    iso_as_of = "2026-05-08T14:00:00+00:00"
    state = {
        "tick_id": "tick-iso",
        "as_of":   iso_as_of,          # ISO string, not datetime
    }
    ctx = _make_ctx(state)

    with patch("data.get_price_history",
               side_effect=Exception("no network in test")):
        # Snapshotter degrades to spy_price=0.0 on provider failure.
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    # recorded_at must be the parsed datetime, not the original string.
    expected_dt = datetime.fromisoformat(iso_as_of)
    # Compare naive (SQLite-friendly) datetimes.
    assert snap["recorded_at"].replace(tzinfo=None) == expected_dt.replace(tzinfo=None)

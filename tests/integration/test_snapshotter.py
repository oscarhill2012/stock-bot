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
    from broker.portfolio import Portfolio

    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)

    # A-072: Snapshotter reads state["portfolio"] (Phase 2 canonical) rather
    # than calling broker.get_portfolio().  Seed it with the expected value.
    portfolio = Portfolio(cash=10_000.0)
    state = {
        "tick_id":   "tick-001",
        "portfolio": portfolio.model_dump(mode="json"),
    }
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

    from broker.portfolio import Portfolio

    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    iso_as_of = "2026-05-08T14:00:00+00:00"

    # A-072: Snapshotter reads state["portfolio"] (Phase 2 canonical) rather
    # than calling broker.get_portfolio().  Seed it with a minimal portfolio.
    portfolio = Portfolio(cash=10_000.0)
    state = {
        "tick_id":   "tick-iso",
        "as_of":     iso_as_of,          # ISO string, not datetime
        "portfolio": portfolio.model_dump(mode="json"),
    }
    ctx = _make_ctx(state)

    with patch("data.get_price_history",
               side_effect=Exception("no network in test")):
        # Snapshotter degrades to spy_price=0.0 on provider failure.
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    # recorded_at is now ISO-stringified inside the snap dict so that
    # DatabaseSessionService can JSON-serialise the full session-state
    # write at end of tick — it cannot encode raw datetime objects (see
    # snapshot/agent.py comment for full reasoning).  The string must
    # still round-trip to the same instant as the original input.
    expected_dt = datetime.fromisoformat(iso_as_of)
    assert isinstance(snap["recorded_at"], str)
    actual_dt = datetime.fromisoformat(snap["recorded_at"])
    # Compare naive (SQLite-friendly) datetimes.
    assert actual_dt.replace(tzinfo=None) == expected_dt.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_snapshotter_raises_when_spy_fetch_fails_on_first_tick():
    """First tick anchors spy_start_price; a 0.0 anchor permanently
    invalidates every subsequent return calc.  The snapshotter must
    raise rather than anchor at 0.0.
    """
    from broker.portfolio import Portfolio

    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    portfolio = Portfolio(cash=10_000.0)
    state = {
        "tick_id":   "tick-001",                # no spy_start_price yet
        "portfolio": portfolio.model_dump(mode="json"),
    }
    ctx = _make_ctx(state)

    with (
        patch("data.get_price_history",
              side_effect=RuntimeError("spy upstream down")),
        pytest.raises(RuntimeError, match="spy upstream down"),
    ):
        async for _ in snapper._run_async_impl(ctx):
            pass


@pytest.mark.asyncio
async def test_snapshotter_reuses_prior_anchor_when_spy_fetch_fails_later():
    """Subsequent ticks log a loud WARNING and reuse the prior anchor;
    never silently substitute 0.0.
    """
    from broker.portfolio import Portfolio

    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    portfolio = Portfolio(cash=10_000.0)
    state = {
        "tick_id":          "tick-002",
        "portfolio":        portfolio.model_dump(mode="json"),
        "starting_capital": 10_000.0,
        "spy_start_price":  470.0,
        "last_spy_price":   480.0,              # carried from prior tick
    }
    ctx = _make_ctx(state)

    with patch("data.get_price_history",
               side_effect=RuntimeError("transient")):
        async for _ in snapper._run_async_impl(ctx):
            pass

    snap = state["last_snapshot"]
    # Anchor preserved; spy_price falls back to last good value, never 0.0.
    assert snap["spy_price"] == 480.0

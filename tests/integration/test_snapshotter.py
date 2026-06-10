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
    """Snapshot row records SPY anchor + bot total.

    Patches ``data.get_price_history`` (the real call site), not
    ``yfinance.Ticker``.  Asserts spy_price > 0 — a silent 0.0 anchor
    would invalidate every subsequent return calc, so the test must
    catch it.
    """
    from broker.portfolio import Portfolio
    from data.models.market import OHLCBar
    from data.models.price_history import PriceHistory

    broker  = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    portfolio = Portfolio(cash=10_000.0)
    state = {
        "tick_id":   "tick-001",
        "portfolio": portfolio.model_dump(mode="json"),
    }
    ctx = _make_ctx(state)

    fake_history = PriceHistory(
        ticker="SPY",
        bars=[OHLCBar(
            timestamp="2026-05-08T20:00:00+00:00",
            open=465.0, high=472.0, low=464.0, close=470.0, volume=1_000_000,
        )],
    )

    async def _fake_get_price_history(*_args, **_kwargs):
        return fake_history

    with patch("data.get_price_history", side_effect=_fake_get_price_history):
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    assert snap["bot_total_value"] == 10_000.0
    assert snap["tick_id"] == "tick-001"
    # Critical: the previous test patched the wrong target and accepted
    # spy_price=0.0, masking A-006.  Assert a real positive anchor.
    assert snap["spy_price"] > 0
    assert snap["spy_price"] == 470.0


@pytest.mark.asyncio
async def test_snapshotter_accepts_iso_string_as_of():
    """state["as_of"] arriving as an ISO-8601 string must not raise
    AsOfRequiredError.

    Locks in the fix that dropped the ``isinstance(raw_as_of, datetime)``
    pre-filter and now passes ``raw_as_of`` directly to ``resolve_as_of``.
    """
    from datetime import datetime

    from broker.portfolio import Portfolio
    from data.models.market import OHLCBar
    from data.models.price_history import PriceHistory

    broker  = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    iso_as_of = "2026-05-08T14:00:00+00:00"
    portfolio = Portfolio(cash=10_000.0)
    state = {
        "tick_id":   "tick-iso",
        "as_of":     iso_as_of,
        "portfolio": portfolio.model_dump(mode="json"),
    }
    ctx = _make_ctx(state)

    fake_history = PriceHistory(
        ticker="SPY",
        bars=[OHLCBar(
            timestamp=iso_as_of,
            open=465.0, high=472.0, low=464.0, close=470.0, volume=1_000_000,
        )],
    )

    async def _fake_get_price_history(*_a, **_kw):
        return fake_history

    with patch("data.get_price_history", side_effect=_fake_get_price_history):
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    expected_dt = datetime.fromisoformat(iso_as_of)
    assert isinstance(snap["recorded_at"], str)
    actual_dt = datetime.fromisoformat(snap["recorded_at"])
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
async def test_snapshotter_reuses_prior_anchor_when_spy_fetch_fails_later(caplog):
    """Subsequent ticks log a loud WARNING and reuse the prior anchor;
    never silently substitute 0.0.

    The WARNING must be a structured ``logger.warning(...)`` call (not a bare
    ``print``) so it is visible in log aggregators and captured by ``caplog``.
    Silent fallback to 0.0 would permanently corrupt every subsequent return
    calculation; the WARNING is the diagnostic signal that the fallback fired.
    """
    import logging

    from broker.portfolio import Portfolio

    broker  = FakeBroker(starting_cash=10_000.0, prices={})
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

    # Capture WARNING-level records from the snapshotter's logger.
    with caplog.at_level(logging.WARNING, logger="agents.snapshot.agent"):
        with patch("data.get_price_history",
                   side_effect=RuntimeError("transient")):
            async for _ in snapper._run_async_impl(ctx):
                pass

    snap = state["last_snapshot"]

    # Content assertion: anchor preserved; spy_price falls back to last good value, never 0.0.
    assert snap["spy_price"] == 480.0, (
        f"spy_price must fall back to last_spy_price=480.0; got {snap['spy_price']}"
    )

    # Structured-log assertion: the WARNING must have been emitted via
    # logger.warning so aggregators (and caplog in CI) can capture it.
    # A bare print() would pass the spy_price check above but be invisible here.
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "snapshotter" in r.getMessage()
    ]
    assert warning_records, (
        "A-031: the SPY-fetch fallback must emit a structured logger.warning — "
        "a bare print() is invisible to log aggregators and this assertion"
    )
    # The warning must carry exc_info so the original traceback is attached.
    assert warning_records[0].exc_info is not None, (
        "A-031: logger.warning must be called with exc_info=True so the "
        "transient failure traceback is preserved for post-mortem debugging"
    )

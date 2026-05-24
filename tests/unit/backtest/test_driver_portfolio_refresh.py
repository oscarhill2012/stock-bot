"""Tests that the backtest driver refreshes ``state["portfolio"]`` from the
broker at the start of every tick.

The live path (``orchestrator/tick.py:_build_initial_state``) rebuilds the
entire state from the broker on every Cloud Run Job invocation, so cross-tick
staleness is structurally impossible there.  The backtest driver keeps a
single ``state`` dict alive across the whole schedule, so any field sourced
from the broker has to be re-pulled at the tick boundary or it goes stale.

The bug this guards against:

  - Tick 1 BUYs MSFT.  Broker now holds MSFT.
  - Tick 2 starts.  ``state["user:positions"]`` is propagated by the executor's
    state_delta event, but ``state["portfolio"]`` is NOT — without the
    driver's refresh it still carries the empty-at-start portfolio dump.
  - Strategist's after-callback reads ``state["portfolio"]``, sees MSFT
    weight = 0.0, treats a 0.0 → 0.0 stance as ``"hold"`` rather than
    ``"close"``, and skips its own ``close && not close_reason`` guard.
  - Risk_gate then reads the *live* broker, sees MSFT held, and surfaces
    a ``StrategistContractViolation`` deep in the pipeline — the violation
    the strategist callback should have caught.

The refresh closes that gap.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backtest.driver import Driver
from backtest.schedule import Tick
from broker.fake import FakeBroker


def _make_driver(tmp_path: Path, broker: FakeBroker) -> Driver:
    """Build a minimal Driver wired to ``broker``.

    The Snapshotter never runs (we patch the Runner to a no-op), so
    ``enforce_pipeline_completion=False`` is essential — otherwise the
    driver would treat each tick as failed.
    """
    (tmp_path / "manifest.json").write_text("{}")
    return Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="test-window",
        failure_abort_ratio=0.99,                # never abort in this test
        enforce_pipeline_completion=False,
    )


def _two_tick_schedule() -> list[Tick]:
    """Two consecutive ticks on the same day — enough to test refresh-between."""
    return [
        Tick(as_of=datetime(2025, 9, 2, 13, 30, tzinfo=UTC), phase="open"),
        Tick(as_of=datetime(2025, 9, 2, 20, 0,  tzinfo=UTC), phase="close"),
    ]


@pytest.mark.asyncio
async def test_portfolio_is_refreshed_at_tick_start(tmp_path: Path) -> None:
    """When the broker's positions change between ticks, ``state["portfolio"]``
    on the next tick must reflect the post-change broker state.

    Setup:
      - Broker has $10K cash and no positions; MSFT priced at $400.
      - Initial ``state["portfolio"]`` is the empty dump.
      - The patched Runner is a no-op for tick 1, but in between ticks we
        simulate the executor having filled a BUY by calling
        ``broker.submit_market`` directly.  This is the same mutation path
        a real BUY would take.
      - Tick 2 should see the post-fill portfolio in ``state``.

    Assertion: after the run, ``state["portfolio"]["positions"]`` contains
    MSFT with the expected quantity — proving the driver re-pulled the
    broker's portfolio on tick 2 rather than reusing the empty dump from
    tick 1.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={"MSFT": 400.0})
    driver = _make_driver(tmp_path, broker)

    # ── Counter + side-effect runner ─────────────────────────────────────────
    # Between tick 1 and tick 2 we mutate the broker to add an MSFT position.
    # The patched runner does nothing per tick (the real pipeline never runs);
    # the mutation happens once via the counter so it lands between ticks.
    tick_count = {"n": 0}

    async def _runner_run_async(*args, **kwargs):
        """No-op stand-in for ADK's Runner.run_async.

        On the first call (tick 1) it leaves the broker untouched.  After
        the first tick completes, the side-effect on the broker is performed
        *outside* the runner — see the patch below.  This generator yields
        nothing so the driver's ``async for _ in runner.run_async(...)``
        loop exits immediately.
        """
        tick_count["n"] += 1
        if False:
            yield None                          # pragma: no cover

    # Initial state mirrors what runner.py seeds at run-start: empty portfolio.
    initial_portfolio = (await broker.get_portfolio()).model_dump(mode="json")
    assert initial_portfolio["positions"] == {}, "test precondition"

    state: dict = {
        "watchlist":       ["MSFT"],
        "tickers":         ["MSFT"],
        "portfolio":       initial_portfolio,
        "user:positions":  {},
    }

    # Patch the ADK Runner the driver instantiates per tick to return our
    # no-op runner.  Side-effect (the BUY) is wedged in via a wrapper around
    # ``_refresh_broker_prices`` so it fires *between* tick 1 and tick 2:
    # the wrapper runs at the start of every tick, and we trigger the fill
    # only on the second call.  This guarantees the BUY lands AFTER tick 1's
    # portfolio refresh and BEFORE tick 2's, which is exactly the window we
    # want to test.
    original_refresh = driver._refresh_broker_prices
    refresh_calls = {"n": 0}

    def _wrapped_refresh(watchlist, tick):
        """Wrap the price-refresh hook so we can inject a mid-run BUY."""
        refresh_calls["n"] += 1
        original_refresh(watchlist, tick)
        # On the *second* refresh call (i.e. before tick 2's portfolio
        # re-pull), simulate the executor having filled a BUY on tick 1.
        if refresh_calls["n"] == 2:
            import asyncio
            asyncio.get_event_loop()              # ensure loop exists
            # FakeBroker.submit_market is async — invoke it via the running
            # loop.  We can call it synchronously here because the driver
            # itself runs inside an event loop and we are still on its thread.
            broker._cash -= 5.0 * 400.0
            from broker.portfolio import Position
            broker._positions["MSFT"] = Position(
                quantity=5.0, avg_cost=400.0, last_price=400.0,
            )

    with patch.object(driver, "_refresh_broker_prices", _wrapped_refresh), \
         patch(
             "backtest.driver.Runner",
             return_value=MagicMock(run_async=_runner_run_async),
         ):
        await driver.run(state, _two_tick_schedule())

    # ── Assertions ──────────────────────────────────────────────────────────
    # After the run, state["portfolio"] must reflect the post-fill broker
    # state (MSFT held), NOT the empty initial portfolio.  This is the
    # behaviour the bug was missing.
    assert "MSFT" in state["portfolio"]["positions"], (
        "state['portfolio'] must be refreshed from the broker at tick start; "
        "instead it still carries the empty dump from before the BUY"
    )
    assert state["portfolio"]["positions"]["MSFT"]["quantity"] == 5.0


@pytest.mark.asyncio
async def test_portfolio_refreshed_even_when_initial_dump_already_present(
    tmp_path: Path,
) -> None:
    """The refresh must *overwrite* any existing ``state["portfolio"]``,
    not just populate when missing.

    The runner seeds ``state["portfolio"]`` before passing to the driver, so
    the bug is specifically about overwriting a stale value.  This test
    guards against a regression where the refresh becomes a ``setdefault``.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})

    # Pre-load the broker with an AAPL position so the broker state diverges
    # from the (deliberately stale) ``state["portfolio"]`` we'll provide.
    from broker.portfolio import Position
    broker._positions["AAPL"] = Position(
        quantity=3.0, avg_cost=180.0, last_price=200.0,
    )

    driver = _make_driver(tmp_path, broker)

    async def _noop_runner(*args, **kwargs):
        """No-op runner that yields nothing."""
        if False:
            yield None                          # pragma: no cover

    # Deliberately stale state: pretend the previous tick saw no positions.
    state: dict = {
        "watchlist":       ["AAPL"],
        "tickers":         ["AAPL"],
        "portfolio":       {"cash": 999_999.0, "positions": {}},   # obviously fake
        "user:positions":  {},
    }

    schedule = [Tick(as_of=datetime(2025, 9, 2, 13, 30, tzinfo=UTC), phase="open")]

    with patch(
        "backtest.driver.Runner",
        return_value=MagicMock(run_async=_noop_runner),
    ):
        await driver.run(state, schedule)

    # The stale dump must be replaced by the broker's real state.
    assert "AAPL" in state["portfolio"]["positions"]
    assert state["portfolio"]["positions"]["AAPL"]["quantity"] == 3.0
    # And the bogus cash figure must be gone.
    assert state["portfolio"]["cash"] != 999_999.0

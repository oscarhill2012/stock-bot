"""Contract test: backtest driver must read ``state["tickers"]`` (not
``state["watchlist"]``) for the per-tick broker price refresh.

A1.6 folds the two-key duplication into a single key. The driver's
``_refresh_broker_prices`` call inside ``Driver.run`` is the canonical
consumer; after A1.6 it must source the watchlist from
``state["tickers"]`` so live (which has no ``watchlist`` key) and
backtest agree on the same field.
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
    """Minimal Driver fixture — mirrors the pattern in
    ``tests/unit/backtest/test_driver_portfolio_refresh.py``."""

    (tmp_path / "manifest.json").write_text("{}")
    return Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="test-window",
        failure_abort_ratio=0.99,
        enforce_pipeline_completion=False,
    )


@pytest.mark.asyncio
async def test_driver_uses_tickers_for_price_refresh(tmp_path: Path) -> None:
    """The driver's per-tick price refresh must source its symbol list
    from ``state["tickers"]``. Confirmed by deliberately omitting
    ``state["watchlist"]`` — the refresh must still see the tickers.
    """

    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    driver = _make_driver(tmp_path, broker)

    # ``state`` carries only "tickers", not "watchlist". Pre-A1.6 the
    # driver would read state.get("watchlist", []) and pass [] to the
    # refresh helper.
    state: dict = {
        "tickers":        ["AAPL"],
        "portfolio":      (await broker.get_portfolio()).model_dump(mode="json"),
        "user:positions": {},
    }

    # Capture the symbol list the driver hands to _refresh_broker_prices.
    captured: list[list[str]] = []
    original_refresh = driver._refresh_broker_prices

    def _spy_refresh(watchlist, tick):
        """Record the symbol list the driver passes to the refresh hook."""

        captured.append(list(watchlist))
        original_refresh(watchlist, tick)

    async def _noop_runner(*args, **kwargs):
        """ADK Runner stand-in — yields nothing."""

        if False:                          # pragma: no cover
            yield None

    schedule = [Tick(as_of=datetime(2025, 9, 2, 13, 30, tzinfo=UTC), phase="open")]

    with patch.object(driver, "_refresh_broker_prices", _spy_refresh), \
         patch(
             "backtest.driver.Runner",
             return_value=MagicMock(run_async=_noop_runner),
         ):
        await driver.run(state, schedule)

    # The driver must have passed the AAPL list — proving it read
    # ``state["tickers"]`` rather than the missing ``state["watchlist"]``.
    assert captured == [["AAPL"]], (
        f"driver must source the refresh symbol list from state['tickers']; "
        f"captured: {captured!r}"
    )

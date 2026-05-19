"""Tests that the driver runs one tick end-to-end against a hand-populated cache.

Adaptation notes vs plan:
- ``OHLCBar`` has ``timestamp`` (datetime), not ``date``; no ``ticker`` or
  ``adj_close`` field.  The store write accepts ``ticker`` separately.
- ``Driver`` is a thin orchestration wrapper around ``build_pipeline``; the
  full pipeline (LLM strategist etc.) is NOT invoked here — the test only
  asserts that the driver loop runs without error and writes a trace file.
  The pipeline runs but the LLM calls will gracefully degrade (no API key in
  CI) so we do not assert on decision content.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers import _store_handle
from backtest.schedule import Tick
from data.models import OHLCBar


@pytest.fixture
def cache(tmp_path: Path) -> CachedDataStore:
    """Cache pre-populated with one ticker, one bar."""
    store = CachedDataStore(tmp_path / "cache.sqlite")
    store.write_ohlcv("AAPL", [
        OHLCBar(
            timestamp=datetime(2023, 3, 13, tzinfo=UTC),
            open=150.0,
            high=152.0,
            low=149.0,
            close=151.0,
            volume=1_000_000,
        ),
    ])
    _store_handle.set_store(store)
    yield store
    _store_handle.clear_store()


@pytest.mark.asyncio
async def test_driver_produces_one_trace_file(tmp_path: Path, cache) -> None:
    """One scheduled tick → one trace file written under ``<run_dir>/traces/``."""
    # Import here to confirm the module exists (the test was written failing first).
    from backtest.driver import Driver
    from broker.fake import FakeBroker

    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    driver = Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="test",
        # The real LLM agents in the pipeline gracefully degrade with no
        # API key, but the Snapshotter at the end of the pipeline never
        # gets called in that scenario.  Disable the strict completion
        # check so this driver-wiring smoke test keeps asserting only
        # "trace file was produced", which is what it was written to do.
        enforce_pipeline_completion=False,
    )

    schedule = [
        Tick(
            as_of=datetime(2023, 3, 13, 9, 30, tzinfo=UTC),
            phase="open",
        ),
    ]

    state = {"tickers": ["AAPL"], "watchlist": ["AAPL"]}
    await driver.run(state, schedule)

    traces = list((tmp_path / "traces").glob("*.json"))
    assert len(traces) == 1

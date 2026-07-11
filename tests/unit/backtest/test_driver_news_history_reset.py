"""Contract test: Driver.run resets the per-run news-history store.

PIT-correctness (Phase 14 Plan 3): each window replay must rebuild the
staleness history from that window's own news timeline — nothing may leak
in from a previous window (or a previous run) executed in this process.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agents.analysts.news.history as history
from backtest.driver import Driver
from broker.fake import FakeBroker


@pytest.mark.asyncio
async def test_driver_run_resets_the_news_history_store(tmp_path: Path) -> None:
    """A store instance that existed before Driver.run must be discarded."""
    # Simulate leakage: a store left over from a previous window replay.
    stale_store = history.get_news_history_store()

    driver = Driver(
        broker=FakeBroker(starting_cash=10_000.0, prices={}),
        run_dir=tmp_path,
        window_key="test-window",
        failure_abort_ratio=0.99,
        enforce_pipeline_completion=False,
        require_store=False,
    )

    # An empty schedule executes no ticks — we are testing only the
    # pre-flight reset, not the pipeline.
    await driver.run({"tickers": []}, [])

    assert history.get_news_history_store() is not stale_store

    # Leave no shared state behind for other tests.
    history.reset_news_history_store()

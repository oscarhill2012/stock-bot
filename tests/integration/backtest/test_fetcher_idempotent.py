"""Tests that the cache fetcher is idempotent across re-runs.

Adaptation note: the plan spec uses ``OHLCBar(ticker=..., date=..., adj_close=...)``,
but the live ``OHLCBar`` model uses ``timestamp: datetime`` with no ``ticker`` or
``adj_close`` field.  The test has been adjusted to match the real model so it
exercises the real writer path.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backtest.cache.fetcher import Fetcher
from backtest.cache.store import CachedDataStore
from backtest.windows import Window
from data.models import OHLCBar


@pytest.mark.asyncio
async def test_fetcher_skips_completed_combinations(tmp_path: Path) -> None:
    """Re-running the fetcher does not re-call providers for ok-marked rows."""
    store = CachedDataStore(tmp_path / "store.sqlite")
    window = Window(start=date(2023, 3, 6), end=date(2023, 3, 10), notes="")

    # Build five daily bars matching the window — timestamps match the live OHLCBar
    # model which carries ``timestamp: datetime`` (no ticker / adj_close fields).
    fake_bars = [
        OHLCBar(
            timestamp=datetime(2023, 3, d, 16, 0, tzinfo=UTC),
            open=1.0, high=2.0, low=0.5, close=1.5, volume=100,
        )
        for d in range(6, 11)
    ]

    fake_provider = AsyncMock(return_value=fake_bars)

    fetcher = Fetcher(
        store=store,
        window_key="svb-test",
        window=window,
        watchlist=["AAPL"],
        provider_fns={"ohlcv": fake_provider},   # one-domain test
        live_providers_for_domain={"ohlcv": "yfinance"},
    )

    # First run — provider should be called once.
    await fetcher.run()
    first_call_count = fake_provider.await_count

    assert first_call_count == 1, "expected exactly one provider call on first run"

    # Second run — the (AAPL, ohlcv) row is already marked ok; provider must not fire.
    await fetcher.run()
    assert fake_provider.await_count == first_call_count, (
        "second run must not call the provider again"
    )


@pytest.mark.asyncio
async def test_fetcher_retries_error_rows(tmp_path: Path) -> None:
    """A row that previously failed (status='error') is retried on re-run."""
    store = CachedDataStore(tmp_path / "store.sqlite")
    window = Window(start=date(2023, 3, 6), end=date(2023, 3, 10), notes="")

    # First call raises; second call succeeds.
    failing_provider = AsyncMock(side_effect=[
        RuntimeError("network failure"),
        [],
    ])

    fetcher = Fetcher(
        store=store,
        window_key="svb-test",
        window=window,
        watchlist=["AAPL"],
        provider_fns={"ohlcv": failing_provider},
        live_providers_for_domain={"ohlcv": "yfinance"},
    )

    # Run 1 — should call provider once (raises, writes error row).
    await fetcher.run()
    assert failing_provider.await_count == 1

    # Run 2 — error row exists but status='error', so provider is called again.
    await fetcher.run()
    assert failing_provider.await_count == 2, (
        "error rows must be retried on re-run"
    )

"""Tests that the cache fetcher is idempotent across re-runs.

Injects a fake OHLCV provider and verifies that a second ``fetcher.run()``
call does not invoke the provider again for any (ticker, domain) triple
that was already recorded as ``status='ok'`` in ``cache_runs``.
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

    store  = CachedDataStore(tmp_path / "store.sqlite")
    window = Window(start=date(2023, 3, 6), end=date(2023, 3, 10), notes="")

    # Build five fake bars — one per trading day in the window.
    # OHLCBar uses ``timestamp`` (datetime) not ``date``; there is no adj_close.
    fake_bars = [
        OHLCBar(
            timestamp=datetime(2023, 3, d, tzinfo=UTC),
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=100.0,
        )
        for d in range(6, 11)
    ]

    fake_provider = AsyncMock(return_value=fake_bars)

    fetcher = Fetcher(
        store=store,
        window_key="svb-test",
        window=window,
        watchlist=["AAPL"],
        provider_fns={"ohlcv": fake_provider},
        live_providers_for_domain={"ohlcv": "yfinance"},
    )

    # First run — provider must be called once.
    await fetcher.run()
    first_call_count = fake_provider.await_count
    assert first_call_count == 1, "expected exactly one provider call on first run"

    # Second run — idempotency: provider must NOT be called again.
    await fetcher.run()
    assert fake_provider.await_count == first_call_count, (
        "second run must not call the provider again for an already-ok combination"
    )

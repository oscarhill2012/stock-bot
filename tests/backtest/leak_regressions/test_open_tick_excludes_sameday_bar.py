"""Open-phase tick must not expose today's OHLCV bar (close not yet public)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers._store_handle import set_store
from backtest.providers.price_history_cache import fetch
from data.models import OHLCBar


@pytest.fixture()
def store_with_two_bars(tmp_path: Path) -> CachedDataStore:
    """A store containing yesterday's and today's daily bars for AAPL."""
    db_path = tmp_path / "cache.sqlite"
    store   = CachedDataStore(db_path)

    bars = [
        OHLCBar(
            timestamp=datetime(2023, 3, 9, 0, 0, tzinfo=UTC),
            open=100.0, high=101.0, low=99.0, close=100.5, volume=1_000_000,
        ),
        OHLCBar(
            timestamp=datetime(2023, 3, 10, 0, 0, tzinfo=UTC),
            open=100.6, high=102.0, low=98.0, close=99.5, volume=1_500_000,
        ),
    ]
    store.write_ohlcv("AAPL", bars)
    set_store(store)
    return store


@pytest.mark.asyncio
async def test_open_phase_excludes_today(store_with_two_bars: CachedDataStore) -> None:
    """At 09:30 open on 2023-03-10, only the 2023-03-09 bar must be visible."""
    result = await fetch(
        "AAPL",
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
        phase="open",
    )

    dates = [bar.timestamp.date() for bar in result.bars]
    assert date(2023, 3, 9)  in dates
    assert date(2023, 3, 10) not in dates


@pytest.mark.asyncio
async def test_close_phase_includes_today(store_with_two_bars: CachedDataStore) -> None:
    """At 16:00 close on 2023-03-10, today's bar IS public (close is closed)."""
    result = await fetch(
        "AAPL",
        as_of=datetime(2023, 3, 10, 16, 0, tzinfo=UTC),
        phase="close",
    )

    dates = [bar.timestamp.date() for bar in result.bars]
    assert date(2023, 3, 10) in dates


@pytest.mark.asyncio
async def test_missing_phase_defaults_to_open_behaviour(
    store_with_two_bars: CachedDataStore,
) -> None:
    """Default behaviour when phase is omitted is the conservative one (trim today)."""
    result = await fetch(
        "AAPL",
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
    )

    dates = [bar.timestamp.date() for bar in result.bars]
    assert date(2023, 3, 10) not in dates

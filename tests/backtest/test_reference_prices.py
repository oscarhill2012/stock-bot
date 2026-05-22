"""S1 — Phase 2 PIT-clamp for ``reference_prices``.

Verifies that ``_seed_reference_prices`` strips any bar whose timestamp
exceeds ``as_of`` when that argument is supplied.  The legacy ``as_of=None``
path (Phase 1 callers) is implicitly covered by the existing smoke tests.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.runner import _seed_reference_prices
from data.models import OHLCBar


@pytest.fixture
def store_with_spy(tmp_path: Path) -> CachedDataStore:
    """Create a temporary cache with two weeks of SPY bars.

    Parameters
    ----------
    tmp_path:
        pytest-supplied temporary directory, unique per test.

    Returns
    -------
    CachedDataStore
        Open store containing 14 daily bars for the SPY ticker,
        starting 2026-05-01 and running through 2026-05-14 inclusive.
    """

    db_path = tmp_path / "cache.sqlite"
    store = CachedDataStore(db_path)

    # Build 14 daily bars — each bar's timestamp is midnight UTC so
    # date-range queries via SQLite's date() function match correctly.
    bars = [
        OHLCBar(
            timestamp=datetime(2026, 5, 1, 0, 0, tzinfo=UTC) + timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low= 99.0 + i,
            close=100.5 + i,
            volume=1_000_000,
        )
        for i in range(14)
    ]
    store.write_ohlcv("SPY", bars)
    return store


def test_seed_clamps_to_as_of(store_with_spy: CachedDataStore) -> None:
    """No reference_prices bar may have ``ts > as_of``.

    Calls ``_seed_reference_prices`` with an ``as_of`` that falls mid-window
    (13:30 UTC on 2026-05-07) and asserts that every bar returned for SPY has
    a timestamp at or before that boundary.  Bars for 2026-05-08 through
    2026-05-14 must be absent from the result.
    """

    # as_of falls during the trading day on 2026-05-07 — bars after this
    # must be stripped even though they exist in the cache.
    as_of = datetime(2026, 5, 7, 13, 30, tzinfo=UTC)

    ref = _seed_reference_prices(
        store=store_with_spy,
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 14),
        as_of=as_of,
    )

    assert "SPY" in ref, "SPY should be present — bars exist in the window"

    for bar in ref["SPY"].bars:
        # SQLite returns naive datetimes; treat them as UTC for comparison
        # against the (always timezone-aware) ``as_of`` boundary.
        bar_ts = bar.timestamp.replace(tzinfo=UTC) if bar.timestamp.tzinfo is None else bar.timestamp
        assert bar_ts <= as_of, (
            f"reference_prices[SPY] leaked future bar at {bar.timestamp} "
            f"(as_of={as_of})"
        )

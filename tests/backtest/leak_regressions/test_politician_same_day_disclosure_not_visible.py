"""A 16:00 same-day disclosure must NOT be visible at the 09:30 open tick."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backtest.cache.store import CachedDataStore
from data.models import PoliticianTrade


def test_same_day_late_disclosure_hidden_at_open(tmp_path: Path) -> None:
    """Disclosure stamped 2023-03-10 16:00 must be invisible at 09:30 same day."""
    store = CachedDataStore(tmp_path / "cache.sqlite")

    trade = PoliticianTrade(
        ticker="AAPL",
        politician="Test",
        chamber="house",
        party="-",
        side="buy",
        transaction_date=datetime(2023, 3, 9, 0, 0, tzinfo=UTC),
        disclosure_date=datetime(2023, 3, 10, 16, 0, tzinfo=UTC),
        amount_min_usd=1,
        amount_max_usd=2,
    )
    store.write_politician_trades("AAPL", [trade])

    rows = store.read_politician_trades(
        "AAPL",
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
        lookback_days=30,
    )
    assert rows == []


def test_same_day_late_disclosure_visible_at_close(tmp_path: Path) -> None:
    """Same row IS visible at the 16:01 read."""
    store = CachedDataStore(tmp_path / "cache.sqlite")

    trade = PoliticianTrade(
        ticker="AAPL",
        politician="Test",
        chamber="house",
        party="-",
        side="buy",
        transaction_date=datetime(2023, 3, 9, 0, 0, tzinfo=UTC),
        disclosure_date=datetime(2023, 3, 10, 16, 0, tzinfo=UTC),
        amount_min_usd=1,
        amount_max_usd=2,
    )
    store.write_politician_trades("AAPL", [trade])

    rows = store.read_politician_trades(
        "AAPL",
        as_of=datetime(2023, 3, 10, 16, 1, tzinfo=UTC),
        lookback_days=30,
    )
    assert len(rows) == 1

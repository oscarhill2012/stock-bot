"""Tests for the point-in-time-filtered cache store.

The point-in-time property is the most important correctness rule in the whole
harness: reads must NEVER return a row whose canonical timestamp is after the
supplied ``as_of``.  Lookahead bias would silently invalidate every backtest.

Adaptation notes vs. plan spec:
- ``OHLCBar`` uses ``timestamp: datetime`` (not ``date: date``), carries no
  ``ticker`` or ``adj_close`` field.  The ``write_ohlcv`` / ``read_ohlcv``
  interface accepts a ``ticker`` argument separately and the store associates
  the ticker via the schema layer.
- ``read_ohlcv`` accepts ``date`` bounds (matching the plan spec); bars with a
  timestamp whose date falls in ``[start, end]`` are returned regardless of
  the intraday time component.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models import NewsArticle, OHLCBar


@pytest.fixture
def store(tmp_path: Path) -> CachedDataStore:
    """Fresh empty cache store rooted in a temp dir."""
    return CachedDataStore(tmp_path / "store.sqlite")


# ── News: point-in-time exclusion ────────────────────────────────────────────

def test_news_read_excludes_future_articles(store: CachedDataStore) -> None:
    """Articles published after ``as_of`` must not be returned."""
    articles = [
        NewsArticle(
            ticker="AAPL", url="https://x/1", headline="Past",
            summary="", source="t",
            published_at=datetime(2023, 3, 8, tzinfo=UTC),
        ),
        NewsArticle(
            ticker="AAPL", url="https://x/2", headline="Future",
            summary="", source="t",
            published_at=datetime(2023, 3, 20, tzinfo=UTC),
        ),
    ]
    store.write_news("AAPL", articles)

    result = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )

    assert [a.headline for a in result] == ["Past"]


def test_news_read_respects_lookback_lower_bound(store: CachedDataStore) -> None:
    """Articles older than ``lookback_days`` before ``as_of`` are excluded."""
    articles = [
        NewsArticle(
            ticker="AAPL", url="https://x/old", headline="Too Old",
            summary="", source="t",
            published_at=datetime(2023, 1, 1, tzinfo=UTC),
        ),
        NewsArticle(
            ticker="AAPL", url="https://x/recent", headline="Recent",
            summary="", source="t",
            published_at=datetime(2023, 3, 10, tzinfo=UTC),
        ),
    ]
    store.write_news("AAPL", articles)

    result = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )

    assert [a.headline for a in result] == ["Recent"]


# ── OHLCV: inclusive date range ───────────────────────────────────────────────

def test_ohlcv_read_returns_inclusive_range(store: CachedDataStore) -> None:
    """``read_ohlcv(start, end)`` returns bars with timestamp in ``[start, end]``."""
    # OHLCBar uses `timestamp: datetime` — create one bar per day.
    bars = [
        OHLCBar(
            timestamp=datetime(2023, 3, d, 16, 0, 0, tzinfo=UTC),
            open=1.0, high=2.0, low=0.5, close=1.5, volume=100,
        )
        for d in (6, 7, 8, 9, 10)
    ]
    store.write_ohlcv("AAPL", bars)

    # Use date bounds (matching the plan spec); bars are matched by calendar date
    # regardless of their intraday time component.
    result = store.read_ohlcv(
        "AAPL",
        start=date(2023, 3, 7),
        end=date(2023, 3, 9),
    )

    assert [b.timestamp for b in result] == [
        datetime(2023, 3, 7, 16, 0, 0, tzinfo=UTC),
        datetime(2023, 3, 8, 16, 0, 0, tzinfo=UTC),
        datetime(2023, 3, 9, 16, 0, 0, tzinfo=UTC),
    ]


# ── Write idempotency ─────────────────────────────────────────────────────────

def test_write_is_idempotent_on_primary_key(store: CachedDataStore) -> None:
    """Re-writing the same news article is a no-op, not a duplicate row."""
    article = NewsArticle(
        ticker="AAPL", url="https://x/dup", headline="H",
        summary="", source="t",
        published_at=datetime(2023, 3, 8, tzinfo=UTC),
    )
    store.write_news("AAPL", [article])
    store.write_news("AAPL", [article])

    result = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )
    assert len(result) == 1

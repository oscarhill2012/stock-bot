"""Test the per-domain skipped-write counter on CachedDataStore.

When a row with MISSING_TIMESTAMP is handed to a write_* method the
store drops it silently.  We need a counter so the fetcher can surface
the shrinkage in fill_audit.json (Phase 7 B3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models.missing import MISSING_TIMESTAMP


@pytest.fixture
def store(tmp_path: Path) -> CachedDataStore:
    """Provide a fresh on-disk CachedDataStore for each test."""
    return CachedDataStore(tmp_path / "cache.sqlite")


def test_drain_skipped_writes_returns_empty_on_fresh_store(store: CachedDataStore) -> None:
    """A freshly-constructed store has nothing to drain — counter starts at zero."""
    assert store.drain_skipped_writes() == {}


def test_writing_one_missing_timestamp_news_row_increments_news_counter(
    store: CachedDataStore,
) -> None:
    """Hand one MISSING_TIMESTAMP news row to write_news; expect skip counted."""
    from data.models.news import NewsArticle

    bad = NewsArticle(
        ticker="AAPL",
        headline="missing",
        url="https://example.com/a",
        published_at=MISSING_TIMESTAMP,
        source="test",
        summary="",
    )

    store.write_news("AAPL", [bad])

    counts = store.drain_skipped_writes()
    assert counts == {"news": 1}

    # Counter is drained on read — subsequent call must return empty.
    assert store.drain_skipped_writes() == {}


def test_multiple_missing_rows_accumulate_per_domain(store: CachedDataStore) -> None:
    """Multiple MISSING_TIMESTAMP rows in one write_news call each increment the counter."""
    from data.models.news import NewsArticle

    bad_rows = [
        NewsArticle(
            ticker="MSFT",
            headline=f"missing-{i}",
            url=f"https://example.com/{i}",
            published_at=MISSING_TIMESTAMP,
            source="test",
            summary="",
        )
        for i in range(3)
    ]

    store.write_news("MSFT", bad_rows)

    counts = store.drain_skipped_writes()
    assert counts == {"news": 3}

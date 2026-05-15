"""``AuditingStore`` decorator captures every cache-read row."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backtest.audit.auditing_store import AuditingStore
from backtest.cache.store import CachedDataStore
from data.models import NewsArticle


def test_read_news_captures_every_row(tmp_path: Path) -> None:
    """``AuditingStore.read_news`` returns the rows AND records them."""
    inner = CachedDataStore(tmp_path / "cache.sqlite")
    inner.write_news("AAPL", [
        NewsArticle(
            ticker="AAPL", headline="h", summary="", url="u", source="s",
            published_at=datetime(2023, 3, 5, 12, 0, tzinfo=UTC),
            sentiment=None,
        ),
    ])

    store = AuditingStore(inner=inner)
    rows = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC), lookback_days=10,
    )

    assert len(rows) == 1
    captured = store.drain_captured()
    assert captured["news"]["AAPL"][0].headline == "h"

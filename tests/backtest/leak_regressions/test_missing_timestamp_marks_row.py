"""Provider rows lacking an upstream timestamp must be excluded, not fabricated."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models import NewsArticle
from data.models.missing import MISSING_TIMESTAMP


def test_news_article_with_missing_timestamp_is_skipped(tmp_path: Path) -> None:
    """``write_news`` must skip rows whose ``published_at`` is the sentinel."""
    store = CachedDataStore(tmp_path / "cache.sqlite")

    articles = [
        NewsArticle(
            ticker="AAPL",
            headline="Real article",
            summary="",
            url="https://example.com/a",
            source="ex",
            published_at=datetime(2023, 3, 9, 12, 0),
            sentiment=None,
        ),
        NewsArticle(
            ticker="AAPL",
            headline="Article with no upstream timestamp",
            summary="",
            url="https://example.com/b",
            source="ex",
            published_at=MISSING_TIMESTAMP,
            sentiment=None,
        ),
    ]

    store.write_news("AAPL", articles)

    # lookback_days is large enough to span from 2099 back to 2023
    # (76 years ≈ 27,740 days; 30_000 provides a comfortable margin).
    rows = store.read_news("AAPL", as_of=datetime(2099, 1, 1), lookback_days=30_000)
    # Only the real article makes it in.  The sentinel-stamped row is excluded.
    assert len(rows) == 1
    assert rows[0].headline == "Real article"


@pytest.mark.asyncio
async def test_tiingo_propagates_sentinel_for_missing_published_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tiingo.fetch`` must NOT substitute wall-clock for missing publishedDate."""
    import data.providers.news.tiingo as mod

    fake_rows = [
        {
            "title": "no-date",
            "description": "",
            "url": "u",
            "source": "src",
            # publishedDate intentionally absent
        },
    ]

    monkeypatch.setenv("TIINGO_API_KEY", "x")
    monkeypatch.setattr(mod, "_fetch_news", lambda *a, **kw: fake_rows)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, 16, 0),
    )

    assert len(out) == 1
    assert out[0].published_at == MISSING_TIMESTAMP

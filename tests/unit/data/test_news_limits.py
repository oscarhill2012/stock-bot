"""Unit tests for the config-driven news article caps (Phase 14 Plan 2).

Two caps, one parity rule:

  * ``news_per_tick_limit``  — resolved by ``data.get_stock_news`` when the
    caller passes no explicit ``limit`` AND honoured by the backtest cache
    provider, so live and replay see the identical newest-N list (spec D2).
  * ``news_backfill_limit``  — the cache-fill cap consumed by
    ``scripts.backtest_fetch`` (replaces the old hardcoded 9000).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from data.models import NewsArticle


def test_fetch_defaults_expose_news_limits():
    """Both caps exist on FetchDefaults with the documented defaults."""
    from data.config import FetchDefaults

    defaults = FetchDefaults()

    assert defaults.news_per_tick_limit == 400
    assert defaults.news_backfill_limit == 9000


def test_config_json_carries_news_limits():
    """config/data.json declares both caps (config-file convention)."""
    from pathlib import Path

    from data.config import load_config_from

    cfg = load_config_from(Path("config/data.json"))

    assert cfg.defaults.news_per_tick_limit == 400
    assert cfg.defaults.news_backfill_limit == 9000


@pytest.mark.asyncio
async def test_dispatcher_resolves_per_tick_limit_from_config(monkeypatch):
    """get_stock_news with no explicit limit forwards the config value."""
    import data as data_pkg
    from data.config import get_config

    captured: dict = {}

    async def _fake_dispatch(domain, ticker, **kwargs):
        """Capture the kwargs the dispatcher would forward to the provider."""
        captured.update(kwargs)
        return []

    monkeypatch.setattr(data_pkg, "_dispatch", _fake_dispatch)

    await data_pkg.get_stock_news("AAPL", as_of=datetime(2026, 2, 10, tzinfo=UTC))

    assert captured["limit"] == get_config().defaults.news_per_tick_limit


@pytest.mark.asyncio
async def test_dispatcher_honours_explicit_limit(monkeypatch):
    """An explicit caller limit (e.g. the backfill) is forwarded untouched."""
    import data as data_pkg

    captured: dict = {}

    async def _fake_dispatch(domain, ticker, **kwargs):
        """Capture the forwarded kwargs."""
        captured.update(kwargs)
        return []

    monkeypatch.setattr(data_pkg, "_dispatch", _fake_dispatch)

    await data_pkg.get_stock_news(
        "AAPL", as_of=datetime(2026, 2, 10, tzinfo=UTC), limit=7,
    )

    assert captured["limit"] == 7


@pytest.mark.asyncio
async def test_cache_provider_applies_limit(monkeypatch):
    """The cache news provider head-slices to the dispatcher-resolved limit."""
    from backtest.providers import news_cache

    base = datetime(2026, 2, 10, 12, 0)

    # Five articles, newest first — matching read_news's descending order.
    articles = [
        NewsArticle(
            ticker="AAPL",
            headline=f"story {i}",
            url=f"https://example.com/{i}",
            published_at=base - timedelta(hours=i),
        )
        for i in range(5)
    ]

    class _Store:
        """Stub store returning the fixed descending article list."""

        def read_news(self, ticker, as_of, lookback_days):
            """Mimic CachedDataStore.read_news (most-recent first)."""
            return articles

    monkeypatch.setattr(news_cache, "get_store", lambda: _Store())

    out = await news_cache.fetch(
        "AAPL", as_of=base, lookback_days=7, limit=2,
    )

    # Newest two survive — identical to the live provider's sort-then-cap.
    assert out == articles[:2]


@pytest.mark.asyncio
async def test_cache_provider_unlimited_when_limit_none(monkeypatch):
    """limit=None keeps the whole lookback window (fill-time behaviour)."""
    from backtest.providers import news_cache

    base = datetime(2026, 2, 10, 12, 0)

    articles = [
        NewsArticle(
            ticker="AAPL",
            headline=f"story {i}",
            url=f"https://example.com/{i}",
            published_at=base - timedelta(hours=i),
        )
        for i in range(3)
    ]

    class _Store:
        """Stub store returning the fixed article list."""

        def read_news(self, ticker, as_of, lookback_days):
            """Mimic CachedDataStore.read_news."""
            return articles

    monkeypatch.setattr(news_cache, "get_store", lambda: _Store())

    out = await news_cache.fetch("AAPL", as_of=base, lookback_days=7, limit=None)

    assert out == articles

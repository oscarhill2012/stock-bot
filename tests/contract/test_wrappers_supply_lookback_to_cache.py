"""Contract test: data wrappers supply ``lookback_days`` to cache providers.

Phase 7.5 dropped the kwarg default on ``news_cache.fetch`` and
``filings_cache.fetch`` so that ``config/data.json`` is the single source
of truth.  That made the kwarg **required** — and if the public wrappers
``data.get_stock_news`` / ``data.get_company_filings`` did not forward
the value, every call under the cache provider would raise ``TypeError``,
silently swallowed by the analyst fetch try/except.  These tests pin the
forwarding so the regression cannot return.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path):
    """Wire a fresh in-temp-dir store and restore prior state on teardown."""
    from backtest.cache.store import CachedDataStore
    from backtest.providers import _store_handle

    store = CachedDataStore(tmp_path / "cache.sqlite")
    _store_handle.set_store(store)
    yield
    _store_handle._STORE = None


@pytest.fixture
def _cache_providers_active():
    """Flip news+filings to the cache provider, restore on teardown."""
    from backtest.providers import filings_cache, news_cache  # noqa: F401 — register
    from data.registry import set_active_provider

    # capture defaults via get_config() to restore correctly
    from data.config import get_config
    cfg = get_config()
    prev_news    = cfg.providers["news"]
    prev_filings = cfg.providers["filings"]

    set_active_provider("news", "cache")
    set_active_provider("filings", "cache")
    yield
    set_active_provider("news", prev_news)
    set_active_provider("filings", prev_filings)


def test_get_stock_news_forwards_lookback_to_cache(_cache_providers_active) -> None:
    """``get_stock_news`` must not raise TypeError under the cache provider.

    Reproduction of the Phase 7.5 regression: prior to the fix the call
    raised ``TypeError: fetch() missing 1 required keyword-only argument:
    'lookback_days'`` — silently swallowed by ``news/fetch.py``.
    """
    from data import get_stock_news

    result = asyncio.run(get_stock_news("AAPL", as_of=datetime.now(timezone.utc)))
    # Empty list is fine — the store has no data.  What matters is no TypeError.
    assert isinstance(result, list)


def test_get_company_filings_forwards_lookback_to_cache(_cache_providers_active) -> None:
    """``get_company_filings`` must not raise TypeError under the cache provider."""
    from data import get_company_filings

    result = asyncio.run(get_company_filings("AAPL", as_of=datetime.now(timezone.utc)))
    assert isinstance(result, list)

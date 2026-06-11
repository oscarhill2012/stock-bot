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
    """Wire a fresh in-temp-dir store and restore prior state on teardown.

    Yields the store so tests can seed rows directly.
    """
    from backtest.cache.store import CachedDataStore
    from backtest.providers import _store_handle

    store = CachedDataStore(tmp_path / "cache.sqlite")
    _store_handle.set_store(store)
    yield store
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


def test_get_company_filings_forwards_staleness_to_cache(_cache_providers_active) -> None:
    """``get_company_filings`` must not raise TypeError under the cache provider.

    ``filings_cache.fetch`` requires ``staleness_days`` (no kwarg default —
    config is the single source of truth), so this call only succeeds if the
    wrapper forwards the value.
    """
    from data import get_company_filings

    result = asyncio.run(get_company_filings("AAPL", as_of=datetime.now(timezone.utc)))
    assert isinstance(result, list)


def test_get_company_filings_staleness_sourced_from_config(
    _isolated_store, monkeypatch,
) -> None:
    """The 8-K staleness horizon must come from ``defaults.filings_8k_staleness_days``.

    Patches the config singleton with a sentinel horizon (909 days — far
    outside any plausible production value) and seeds an 8-K filed 800 days
    before ``as_of``.  Under the production default (90) the filing would be
    dropped as stale; it is only served if the wrapper actually forwards the
    sentinel from config to the cache provider's selector.
    """
    from datetime import timedelta

    from backtest.providers import filings_cache  # noqa: F401 — registers the provider
    from data import config as data_config_mod
    from data import get_company_filings
    from data.config import DataConfig, FetchDefaults
    from data.models import Filing

    sentinel_config = DataConfig(
        providers={
            "price_history":      "yfinance",
            "company_ratios":     "pit_composite",
            "news":               "finnhub",
            "social_sentiment":   "finnhub",
            "insider_trades":     "edgar",
            "politician_trades":  "fmp",
            "notable_holders":    "edgar",
            "filings":            "cache",   # route the wrapper to the cache provider
        },
        defaults=FetchDefaults(filings_8k_staleness_days=909),
        quiver_http_timeout_seconds=15.0,
    )
    monkeypatch.setattr(data_config_mod, "_cache", sentinel_config)

    as_of = datetime(2026, 3, 2, tzinfo=timezone.utc)
    _isolated_store.write_filings("AAPL", [
        Filing(
            ticker="AAPL",
            form_type="8-K",
            filed_at=as_of - timedelta(days=800),   # stale under 90, fresh under 909
            accession_no="E-old",
        ),
    ])

    result = asyncio.run(get_company_filings("AAPL", as_of=as_of))

    assert [f.accession_no for f in result] == ["E-old"]

"""Unit tests for data.aggregator — bundle composition + _safe error handling."""
from __future__ import annotations

import asyncio

import pytest

from data import config as data_config
from data.aggregator import get_stock_signal_bundle
from data.models import StockSignalBundle
from data.registry import DOMAINS, register


def _stub_all_domains_with(monkeypatch: pytest.MonkeyPatch, registry_isolation: None,
                            failing_domain: str | None = None) -> None:
    from data.models import (
        Filing,
        InsiderTrade,
        NewsArticle,
        NotableHolder,
        PoliticianTrade,
        SocialSentiment,
        StockStats,
    )

    @register("stats", "fake", upstream="stats_up", rate_per_minute=10_000, burst=10_000)
    async def _stats(ticker: str, *, period: str = "1y", interval: str = "1d", **kwargs) -> StockStats:
        # Accept ``as_of`` and any future kwargs forwarded by the aggregator.
        if failing_domain == "stats":
            raise RuntimeError("boom")
        return StockStats(ticker=ticker, history=[])

    @register("news", "fake", upstream="news_up", rate_per_minute=10_000, burst=10_000)
    async def _news(ticker: str, **opts) -> list[NewsArticle]:
        if failing_domain == "news":
            raise RuntimeError("boom")
        return []

    @register("social_sentiment", "fake", upstream="social_up", rate_per_minute=10_000, burst=10_000)
    async def _soc(ticker: str, **kwargs) -> SocialSentiment | None:
        # Accept ``as_of`` and any future kwargs forwarded by the aggregator.
        return None

    @register("insider_trades", "fake", upstream="ins_up", rate_per_minute=10_000, burst=10_000)
    async def _ins(ticker: str, **opts) -> list[InsiderTrade]:
        return []

    @register("politician_trades", "fake", upstream="pol_up", rate_per_minute=10_000, burst=10_000)
    async def _pol(ticker: str | None = None, **opts) -> list[PoliticianTrade]:
        return []

    @register("notable_holders", "fake", upstream="holders_up", rate_per_minute=10_000, burst=10_000)
    async def _holders(ticker: str, **opts) -> list[NotableHolder]:
        return []

    @register("filings", "fake", upstream="filings_up", rate_per_minute=10_000, burst=10_000)
    async def _filings(ticker: str, **opts) -> list[Filing]:
        return []

    monkeypatch.setattr(
        data_config, "_cache",
        data_config.DataConfig(providers={d: "fake" for d in DOMAINS}),
    )


def test_bundle_returns_stock_signal_bundle(monkeypatch, registry_isolation) -> None:
    _stub_all_domains_with(monkeypatch, registry_isolation)
    bundle = asyncio.run(get_stock_signal_bundle("AAPL"))
    assert isinstance(bundle, StockSignalBundle)
    assert bundle.ticker == "AAPL"
    assert bundle.errors == []


def test_bundle_captures_provider_failure(monkeypatch, registry_isolation) -> None:
    _stub_all_domains_with(monkeypatch, registry_isolation, failing_domain="news")
    bundle = asyncio.run(get_stock_signal_bundle("AAPL"))
    assert bundle.news == []
    assert len(bundle.errors) == 1
    err = bundle.errors[0]
    assert err.domain == "news"
    assert err.provider == "fake"
    assert "boom" in err.message

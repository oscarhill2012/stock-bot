"""Unit tests for data.aggregator — bundle composition + _safe error handling.

Phase 5 data-model split: ``stats`` domain retired; replaced by
``price_history`` and ``company_ratios``. Test stubs register both new domains
and assert on the updated ``StockSignalBundle`` field names.
"""
from __future__ import annotations

import asyncio

import pytest

from data import config as data_config
from data.aggregator import get_stock_signal_bundle
from data.models import CompanyRatios, PriceHistory, StockSignalBundle
from data.registry import DOMAINS, register


def _stub_all_domains_with(monkeypatch: pytest.MonkeyPatch, registry_isolation: None,
                            failing_domain: str | None = None) -> None:
    """Register fake providers for every active domain.

    Parameters
    ----------
    monkeypatch:
        pytest monkeypatch fixture.
    registry_isolation:
        The ``registry_isolation`` fixture (ensures a clean registry slate).
    failing_domain:
        If set, the provider for this domain raises ``RuntimeError("boom")``.
    """
    from data.models import (
        Filing,
        InsiderTrade,
        NewsArticle,
        NotableHolder,
        PoliticianTrade,
        SocialSentiment,
    )

    @register("price_history", "fake", upstream="ph_up", rate_per_minute=10_000, burst=10_000)
    async def _ph(ticker: str, *, period: str = "1y", interval: str = "1d", **kwargs) -> PriceHistory:
        # **kwargs absorbs as_of and any future dispatch-level additions.
        if failing_domain == "price_history":
            raise RuntimeError("boom")
        return PriceHistory(ticker=ticker, bars=[])

    @register("company_ratios", "fake", upstream="cr_up", rate_per_minute=10_000, burst=10_000)
    async def _cr(ticker: str, *, period: str = "1y", interval: str = "1d", **kwargs) -> CompanyRatios:
        # **kwargs absorbs as_of and any future dispatch-level additions.
        if failing_domain == "company_ratios":
            raise RuntimeError("boom")
        return CompanyRatios(ticker=ticker)

    @register("news", "fake", upstream="news_up", rate_per_minute=10_000, burst=10_000)
    async def _news(ticker: str, **opts) -> list[NewsArticle]:
        if failing_domain == "news":
            raise RuntimeError("boom")
        return []

    @register("social_sentiment", "fake", upstream="social_up", rate_per_minute=10_000, burst=10_000)
    async def _soc(ticker: str, **kwargs) -> SocialSentiment | None:
        # **kwargs absorbs as_of and any future dispatch-level additions.
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
    """A happy-path bundle call returns a valid StockSignalBundle."""
    _stub_all_domains_with(monkeypatch, registry_isolation)
    bundle = asyncio.run(get_stock_signal_bundle("AAPL"))
    assert isinstance(bundle, StockSignalBundle)
    assert bundle.ticker == "AAPL"
    assert bundle.errors == []

    # Phase 5: confirm new field names are present.
    assert bundle.price_history is not None
    assert bundle.ratios is not None


def test_bundle_captures_provider_failure(monkeypatch, registry_isolation) -> None:
    """A failing provider lands in bundle.errors rather than raising."""
    _stub_all_domains_with(monkeypatch, registry_isolation, failing_domain="news")
    bundle = asyncio.run(get_stock_signal_bundle("AAPL"))
    assert bundle.news == []
    assert len(bundle.errors) == 1
    err = bundle.errors[0]
    assert err.domain == "news"
    assert err.provider == "fake"
    assert "boom" in err.message

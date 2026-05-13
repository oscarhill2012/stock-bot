from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_ctx(tickers: list) -> MagicMock:
    state = {"tickers": tickers}
    ctx = MagicMock()
    ctx.state = state
    return ctx


@pytest.mark.asyncio
async def test_technical_fetch_writes_state():
    from agents.analysts.technical.fetch import technical_fetch_callback
    stats_mock = MagicMock()
    stats_mock.model_dump.return_value = {"ticker": "AAPL", "history": []}
    ctx = _make_ctx(["AAPL"])
    with patch("agents.analysts.technical.fetch.get_stock_stats", new=AsyncMock(return_value=stats_mock)):
        result = await technical_fetch_callback(ctx)
    assert result is None
    assert "AAPL" in ctx.state["technical_data"]


@pytest.mark.asyncio
async def test_fundamental_fetch_writes_state():
    """Phase 5: fundamental fetch produces a triad payload (stats, filings, insider)."""
    from agents.analysts.fundamental.fetch import fundamental_fetch_callback
    from data.models import Form4Bundle

    filing_mock = MagicMock()
    filing_mock.model_dump.return_value = {"form_type": "10-K"}
    bundle = Form4Bundle(trades=[], derivatives=[])

    ctx = _make_ctx(["AAPL"])
    with (
        patch("agents.analysts.fundamental.fetch.get_stock_stats", new=AsyncMock(return_value=None)),
        patch("agents.analysts.fundamental.fetch.get_company_filings", new=AsyncMock(return_value=[filing_mock])),
        patch("agents.analysts.fundamental.fetch.get_insider_trades", new=AsyncMock(return_value=bundle)),
    ):
        result = await fundamental_fetch_callback(ctx)

    assert result is None
    payload = ctx.state["fundamental_data"]["AAPL"]
    # New triad structure — filings are nested under "filings" key.
    assert payload["filings"][0]["form_type"] == "10-K"
    assert isinstance(payload["insider"], Form4Bundle)
    assert "stats" in payload


@pytest.mark.asyncio
async def test_news_fetch_writes_state():
    """Task 6: news fetch callback writes news_data (renamed from sentiment_data).

    The social_sentiment branch is removed — only news/ is fetched here.
    """
    from agents.analysts.news.fetch import news_fetch_callback

    news_mock = MagicMock()
    news_mock.model_dump.return_value = {"headline": "Good news"}

    ctx = _make_ctx(["AAPL"])
    with patch("agents.analysts.news.fetch.get_stock_news", new=AsyncMock(return_value=[news_mock])):
        result = await news_fetch_callback(ctx)

    assert result is None
    assert "AAPL" in ctx.state["news_data"]
    # Social sentinel no longer present in the news payload.
    assert "social" not in ctx.state["news_data"]["AAPL"]

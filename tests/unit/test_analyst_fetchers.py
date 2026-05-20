"""Unit tests for analyst fetch callbacks.

Phase 5 data-model split: ``get_stock_stats`` is retired. The technical fetch
callback now calls ``get_price_history`` + ``get_company_ratios`` separately;
the fundamental fetch callback calls only ``get_company_ratios`` (no OHLCV history).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.models import CompanyRatios, PriceHistory


def _make_ctx(tickers: list) -> MagicMock:
    """Construct a minimal ADK CallbackContext stub carrying the given tickers."""
    state = {"tickers": tickers}
    ctx = MagicMock()
    ctx.state = state
    return ctx


@pytest.mark.asyncio
async def test_technical_fetch_writes_state():
    """Technical fetch stores price_history + ratios sub-keys per ticker."""
    from agents.analysts.technical.fetch import technical_fetch_callback

    ph_mock = PriceHistory(ticker="AAPL", bars=[])
    cr_mock = CompanyRatios(ticker="AAPL", trailing_pe=25.0)

    ctx = _make_ctx(["AAPL"])
    with (
        patch(
            "agents.analysts.technical.fetch.get_price_history",
            new=AsyncMock(return_value=ph_mock),
        ),
        patch(
            "agents.analysts.technical.fetch.get_company_ratios",
            new=AsyncMock(return_value=cr_mock),
        ),
    ):
        result = await technical_fetch_callback(ctx)

    assert result is None
    # A2.6: fetch callback writes under the temp:-prefixed key.
    assert "AAPL" in ctx.state["temp:technical_data"]

    aapl_data = ctx.state["temp:technical_data"]["AAPL"]

    # Both sub-keys must be present.
    assert "price_history" in aapl_data
    assert "ratios" in aapl_data

    # Confirm the payloads are the model_dump() output of the fakes.
    assert aapl_data["price_history"]["ticker"] == "AAPL"
    assert aapl_data["price_history"]["bars"] == []
    assert aapl_data["ratios"]["trailing_pe"] == 25.0


@pytest.mark.asyncio
async def test_fundamental_fetch_writes_state():
    """Phase 5: fundamental fetch produces a triad payload (ratios, filings, insider)."""
    from agents.analysts.fundamental.fetch import fundamental_fetch_callback
    from data.models import Form4Bundle

    filing_mock = MagicMock()
    filing_mock.model_dump.return_value = {"form_type": "10-K"}
    bundle = Form4Bundle(trades=[], derivatives=[])
    cr_mock = CompanyRatios(ticker="AAPL")

    ctx = _make_ctx(["AAPL"])
    with (
        patch(
            "agents.analysts.fundamental.fetch.get_company_ratios",
            new=AsyncMock(return_value=cr_mock),
        ),
        patch(
            "agents.analysts.fundamental.fetch.get_company_filings",
            new=AsyncMock(return_value=[filing_mock]),
        ),
        patch(
            "agents.analysts.fundamental.fetch.get_insider_trades",
            new=AsyncMock(return_value=bundle),
        ),
    ):
        result = await fundamental_fetch_callback(ctx)

    assert result is None
    # A2.6: fetch callback writes under the temp:-prefixed key.
    payload = ctx.state["temp:fundamental_data"]["AAPL"]

    # New triad structure: ratios (not stats), filings, insider.
    assert "ratios" in payload
    assert payload["filings"][0]["form_type"] == "10-K"
    assert isinstance(payload["insider"], Form4Bundle)

    # The old "stats" key must be absent — confirms the split.
    assert "stats" not in payload


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
    # A2.6: fetch callback writes under the temp:-prefixed key.
    assert "AAPL" in ctx.state["temp:news_data"]
    # Social sentinel no longer present in the news payload.
    assert "social" not in ctx.state["temp:news_data"]["AAPL"]

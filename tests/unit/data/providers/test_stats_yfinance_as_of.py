"""yfinance providers accept ``as_of`` for dispatch parity (no data-logic change)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.models import CompanyRatios, PriceHistory


@pytest.mark.asyncio
async def test_fetch_price_history_accepts_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_price_history`` must accept ``as_of`` + ``**_unused``."""
    import data.providers.stats.yfinance as mod

    monkeypatch.setattr(
        mod, "_fetch_price_history",
        lambda ticker, period, interval: PriceHistory(ticker=ticker, bars=[]),
    )

    out = await mod.fetch_price_history(
        "AAPL",
        period="1y",
        interval="1d",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )

    assert isinstance(out, PriceHistory)
    assert out.ticker == "AAPL"


@pytest.mark.asyncio
async def test_fetch_company_ratios_accepts_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_company_ratios`` must accept ``as_of`` + ``**_unused``."""
    import data.providers.stats.yfinance as mod

    monkeypatch.setattr(
        mod, "_fetch_company_ratios",
        lambda ticker, period, interval: CompanyRatios(ticker=ticker),
    )

    out = await mod.fetch_company_ratios(
        "AAPL",
        period="1y",
        interval="1d",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )

    assert isinstance(out, CompanyRatios)
    assert out.ticker == "AAPL"

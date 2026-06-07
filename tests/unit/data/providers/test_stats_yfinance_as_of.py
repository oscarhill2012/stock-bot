"""yfinance providers — ``as_of`` parity for ``price_history``.

Note: the ``company_ratios`` registration was removed from the yfinance stats
module in the plan-08 provider cull (A-038).  ``pit_composite`` is now the
sole ``company_ratios`` provider.  The tests for the now-deleted
``fetch_company_ratios`` / ``_fetch_company_ratios`` functions have been
removed along with the registration.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.models import PriceHistory


@pytest.mark.asyncio
async def test_fetch_price_history_accepts_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_price_history`` must accept ``as_of`` + ``**_unused``."""
    import data.providers.stats.yfinance as mod

    monkeypatch.setattr(
        mod, "_fetch_price_history",
        lambda ticker, period, interval, as_of=None: PriceHistory(ticker=ticker, bars=[]),
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



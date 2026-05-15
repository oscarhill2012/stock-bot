"""Every public wrapper must dispatch without TypeError now that as_of is plumbed.

Guards the bug where leaf providers ignored ``as_of`` while wrappers passed it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest


@pytest.mark.asyncio
async def test_get_insider_trades_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_insider_trades`` must not TypeError when calling the active provider."""
    import data.providers.insider_trades.edgar as mod
    from data import get_insider_trades

    monkeypatch.setattr(mod, "_list_form4_filings", lambda s, lim, a: [])
    out = await get_insider_trades("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out.trades == []


@pytest.mark.asyncio
async def test_get_notable_holders_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_notable_holders`` must not TypeError when calling the active provider."""
    import data.providers.notable_holders.edgar as mod
    from data import get_notable_holders

    monkeypatch.setattr(mod, "_list_holder_filings", lambda s, sym, lim, a: [])
    out = await get_notable_holders("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out == []


@pytest.mark.asyncio
async def test_get_company_filings_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_company_filings`` must not TypeError when calling the active provider."""
    import data.providers.filings.edgar as mod
    from data import get_company_filings

    monkeypatch.setattr(mod, "_list_filings", lambda s, ft, lim, a: [])
    out = await get_company_filings(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        include_excerpts=False,
    )
    assert out == []


@pytest.mark.asyncio
async def test_get_stock_news_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_stock_news`` must not TypeError when calling the active provider."""
    import data.providers.news.finnhub as mod
    from data import get_stock_news

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: [])
    out = await get_stock_news(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )
    assert out == []


@pytest.mark.asyncio
async def test_get_public_figure_trades_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_public_figure_trades`` must not TypeError when calling the active provider."""
    from data import get_public_figure_trades

    monkeypatch.delenv("QUIVER_QUANT_API_KEY", raising=False)
    out = await get_public_figure_trades("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out == []

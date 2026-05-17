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
    """``get_stock_news`` must not TypeError when calling the active provider.

    Patches the active provider (``alpha_vantage``) so no API key or HTTP
    call is needed.  Updated from ``finnhub`` in Phase 6 when the active
    news provider was swapped from ``tiingo`` → ``alpha_vantage`` in
    data.json.

    Strategy: stub ``require_key`` (so key lookup short-circuits) and
    ``_chunk_window`` (so fetch produces no chunks and returns immediately
    with an empty list).  Both names are resolved in the provider module's
    own namespace at call time, so ``monkeypatch.setattr`` intercepts them.
    """
    import data.providers.news.alpha_vantage as mod
    from data import get_stock_news

    # Stub key lookup — returns a dummy string without touching .env.
    monkeypatch.setattr(mod, "require_key", lambda _env_var: "test-key")
    # Stub chunk generator — no chunks → no HTTP calls → empty result.
    monkeypatch.setattr(mod, "_chunk_window", lambda *_a, **_kw: [])

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

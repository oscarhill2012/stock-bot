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

    monkeypatch.setattr(mod, "_list_latest_filing", lambda s, f, a: [])
    monkeypatch.setattr(mod, "_list_filings_range", lambda s, f, lo, up: [])
    out = await get_company_filings(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        include_excerpts=False,
    )
    assert out == []


@pytest.mark.asyncio
async def test_get_company_filings_forwards_from_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """``from_date`` must reach the provider and trigger backfill-mode queries.

    The backtest cache-fill passes ``from_date`` (window start) through the
    wrapper; if the wrapper swallowed it, the provider would silently fall
    back to live mode and the cache would miss the in-window range — so this
    asserts a range query lower-bounded at ``from_date`` is actually issued.
    """
    import data.providers.filings.edgar as mod
    from data import get_company_filings

    window_start = date(2025, 9, 2)
    range_calls: list[tuple] = []

    def fake_range(symbol, forms, lower, upper):
        range_calls.append((forms, lower, upper))
        return []

    monkeypatch.setattr(mod, "_iter_latest_filing", lambda s, f, a: [])
    monkeypatch.setattr(mod, "_iter_filings_range", fake_range)

    out = await get_company_filings(
        "AAPL",
        as_of=datetime(2025, 10, 13, tzinfo=UTC),
        include_excerpts=False,
        from_date=window_start,
    )

    assert out == []
    window_lower = datetime.combine(window_start, datetime.min.time(), tzinfo=UTC)
    assert any(lower == window_lower for _, lower, _ in range_calls)


@pytest.mark.asyncio
async def test_get_stock_news_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_stock_news`` must not TypeError when calling the active provider.

    Patches the active provider (``finnhub``) so no API key or HTTP call is
    needed.  Finnhub is the active news provider; ``alpha_vantage`` was culled
    in the plan-08 provider cull (A-037).

    Strategy: stub ``require_key`` (so key lookup short-circuits) and
    ``_fetch_articles`` (the per-symbol HTTP call) so the fetch completes
    immediately with an empty list.  Both names are resolved in the provider
    module's own namespace at call time, so ``monkeypatch.setattr`` intercepts
    them.
    """
    import data.providers.news.finnhub as mod
    from data import get_stock_news

    # Stub key lookup — returns a dummy string without touching .env.
    monkeypatch.setattr(mod, "require_key", lambda _env_var: "test-key")
    # Stub the sync HTTP helper — returns [] so no network call is made.
    monkeypatch.setattr(mod, "_fetch_company_news", lambda *_a, **_kw: [])

    out = await get_stock_news(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )
    assert out == []


@pytest.mark.asyncio
async def test_get_public_figure_trades_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_public_figure_trades`` must not TypeError when calling the active provider.

    The active provider is ``fmp`` (Financial Modeling Prep).  We stub
    ``require_key`` and both network functions so no HTTP call or real key is
    needed.  The wrapper's ``as_of`` plumbing is what is being exercised here.
    """
    import data.providers.politician_trades.fmp as fmp_mod
    from data import get_public_figure_trades

    # Stub key lookup — returns a dummy string without touching .env.
    monkeypatch.setattr(fmp_mod, "require_key", lambda _env_var: "test-key")

    # Stub both network calls — return empty lists so the fetch completes
    # cleanly without issuing any real HTTP request.
    monkeypatch.setattr(fmp_mod, "_fetch_senate", lambda symbol, key: [])
    monkeypatch.setattr(fmp_mod, "_fetch_house",  lambda symbol, key: [])

    out = await get_public_figure_trades("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out == []

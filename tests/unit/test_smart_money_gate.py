"""Gate behaviour tests for smart_money_fetch_callback — Phase 5, Tasks 4 and 9.

After Task 4 the callback is scoped to external-observer flows only
(politician_trades + notable_holders).  The insider-trades path has been
removed; tests that relied on it are replaced by tests that confirm the
callback state-writing behaviour for the remaining two sources.

After Task 9 the "gate fires → skip-Content" pattern has been removed
entirely.  The callback always returns None; no-data handling is the
responsibility of SmartMoneyAnalyst._run_async_impl.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.analysts.smart_money.fetch import smart_money_fetch_callback


def _make_ctx(tickers: list) -> MagicMock:
    """Build a minimal CallbackContext-like stub with a state dict."""
    state = {"tickers": tickers}
    ctx = MagicMock()
    ctx.state = state
    return ctx


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_activity():
    """Callback always returns None even when politicians and holders are both empty.

    The old behaviour (returning a skip-Content) caused ADK to set
    end_invocation=True and bypass _run_async_impl — see Task 9 regression fix.
    """
    ctx = _make_ctx(["AAPL"])
    with (
        patch(
            "agents.analysts.smart_money.fetch.get_public_figure_trades",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "agents.analysts.smart_money.fetch.get_notable_holders",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await smart_money_fetch_callback(ctx)

    # Must be None — a Content return would trigger ADK's end_invocation shortcut.
    assert result is None

    # Callback must NOT pre-seed smart_money_verdicts; that is _run_async_impl's job.
    assert "smart_money_verdicts" not in ctx.state


@pytest.mark.asyncio
async def test_gate_passes_with_politician_trade():
    """Gate does not fire when at least one politician trade is present.

    After Phase 7.6 Task 17, the result is a ticker-first SmartMoneyRaw
    instance.  Uses a real PoliticianTrade so Pydantic's type validation on
    SmartMoneyRaw.politicians passes without errors.
    """
    from datetime import date

    from data.models.smart_money import SmartMoneyRaw
    from data.models.trades import PoliticianTrade

    ctx = _make_ctx(["AAPL"])
    politician = PoliticianTrade(
        ticker="AAPL",
        politician="Jane Doe",
        chamber="House",
        party="D",
        side="buy",
        transaction_date=date(2024, 1, 15),
        amount_min_usd=15_000,
        amount_max_usd=50_000,
    )
    with (
        patch(
            "agents.analysts.smart_money.fetch.get_public_figure_trades",
            new=AsyncMock(return_value=[politician]),
        ),
        patch(
            "agents.analysts.smart_money.fetch.get_notable_holders",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await smart_money_fetch_callback(ctx)

    assert result is None  # Gate did NOT fire.

    # Ticker-first: AAPL key holds a SmartMoneyRaw with one politician trade.
    payload = ctx.state["smart_money_data"]["AAPL"]
    assert isinstance(payload, SmartMoneyRaw)
    assert len(payload.politicians) == 1


@pytest.mark.asyncio
async def test_gate_passes_with_notable_holder():
    """Gate does not fire when at least one notable holder is present.

    After Phase 7.6 Task 17, the result is a ticker-first SmartMoneyRaw
    instance.  Uses a real NotableHolder so Pydantic's type validation on
    SmartMoneyRaw.notable_holders passes without errors.
    """
    from datetime import datetime, timezone

    from data.models.smart_money import SmartMoneyRaw
    from data.models.trades import NotableHolder

    ctx = _make_ctx(["MSFT"])
    holder = NotableHolder(
        ticker="MSFT",
        holder="BigFund LLC",
        form_type="SC 13G",
        intent="passive",
        is_amendment=False,
        filed_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        accession_no="0001234567-24-000001",
    )
    with (
        patch(
            "agents.analysts.smart_money.fetch.get_public_figure_trades",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "agents.analysts.smart_money.fetch.get_notable_holders",
            new=AsyncMock(return_value=[holder]),
        ),
    ):
        result = await smart_money_fetch_callback(ctx)

    assert result is None

    # Ticker-first: MSFT key holds a SmartMoneyRaw with one notable holder.
    payload = ctx.state["smart_money_data"]["MSFT"]
    assert isinstance(payload, SmartMoneyRaw)
    assert len(payload.notable_holders) == 1

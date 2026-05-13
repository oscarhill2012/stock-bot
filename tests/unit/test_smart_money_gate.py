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
    """Gate does not fire when at least one politician trade is present."""
    ctx = _make_ctx(["AAPL"])
    politician = MagicMock()
    politician.model_dump = lambda: {"side": "BUY", "amount": 50_000}
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
    assert "AAPL" in ctx.state["smart_money_data"]["politicians"]


@pytest.mark.asyncio
async def test_gate_passes_with_notable_holder():
    """Gate does not fire when at least one notable holder is present."""
    ctx = _make_ctx(["MSFT"])
    holder = MagicMock()
    holder.model_dump = lambda: {}
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
    assert "MSFT" in ctx.state["smart_money_data"]["notable_holders"]

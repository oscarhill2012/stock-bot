"""Gate behaviour tests for smart_money_fetch_callback — Phase 5, Task 4.

After Task 4 the callback is scoped to external-observer flows only
(politician_trades + notable_holders).  The insider-trades path has been
removed; tests that relied on it are replaced by tests that confirm the gate
fires / passes correctly based on the remaining two sources.
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
async def test_gate_fires_when_no_activity():
    """Gate fires (skip-Content returned) when politicians and holders are both empty."""
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

    assert result is not None
    assert "skipping" in result.parts[0].text
    assert ctx.state["smart_money_verdicts"] == []


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

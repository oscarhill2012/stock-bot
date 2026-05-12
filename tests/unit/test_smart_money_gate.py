from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.analysts.smart_money.fetch import smart_money_fetch_callback


def _make_ctx(tickers: list) -> MagicMock:
    state = {"tickers": tickers}
    ctx = MagicMock()
    ctx.state = state
    return ctx


@pytest.mark.asyncio
async def test_gate_fires_when_no_activity():
    ctx = _make_ctx(["AAPL"])
    with (
        patch("agents.analysts.smart_money.fetch.get_insider_trades", new=AsyncMock(return_value=[])),
        patch("agents.analysts.smart_money.fetch.get_public_figure_trades", new=AsyncMock(return_value=[])),
        patch("agents.analysts.smart_money.fetch.get_notable_holders", new=AsyncMock(return_value=[])),
    ):
        result = await smart_money_fetch_callback(ctx)
    assert result is not None
    assert "skipping" in result.parts[0].text
    assert ctx.state["smart_money_verdicts"] == []


@pytest.mark.asyncio
async def test_gate_passes_with_big_insider():
    ctx = _make_ctx(["AAPL"])
    insider = MagicMock()
    insider.transaction_value = 200_000
    insider.model_dump = lambda: {"transaction_value": 200_000}
    with (
        patch("agents.analysts.smart_money.fetch.get_insider_trades", new=AsyncMock(return_value=[insider])),
        patch("agents.analysts.smart_money.fetch.get_public_figure_trades", new=AsyncMock(return_value=[])),
        patch("agents.analysts.smart_money.fetch.get_notable_holders", new=AsyncMock(return_value=[])),
    ):
        result = await smart_money_fetch_callback(ctx)
    assert result is None  # did NOT gate
    assert "AAPL" in ctx.state["smart_money_data"]["insiders"]


@pytest.mark.asyncio
async def test_gate_passes_with_notable_holder():
    ctx = _make_ctx(["MSFT"])
    holder = MagicMock()
    holder.model_dump = lambda: {}
    with (
        patch("agents.analysts.smart_money.fetch.get_insider_trades", new=AsyncMock(return_value=[])),
        patch("agents.analysts.smart_money.fetch.get_public_figure_trades", new=AsyncMock(return_value=[])),
        patch("agents.analysts.smart_money.fetch.get_notable_holders", new=AsyncMock(return_value=[holder])),
    ):
        result = await smart_money_fetch_callback(ctx)
    assert result is None

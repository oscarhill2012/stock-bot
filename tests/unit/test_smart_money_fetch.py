"""Tests for smart_money_fetch_callback — Phase 5, Task 4.

After Phase 5 Task 4, smart_money_fetch_callback must NOT pull insider trades.
The callback is scoped to external-observer flows only: politician_trades and
notable_holders.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_ctx(tickers: list[str]) -> MagicMock:
    """Build a minimal CallbackContext-like stub with a state dict."""
    ctx = MagicMock()
    ctx.state = {"tickers": tickers}
    return ctx


# ---------------------------------------------------------------------------
# Core Phase-5 assertion: insider domain is never requested
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_smart_money_fetch_does_not_call_insider_trades():
    """After Phase 5, smart_money_fetch_callback no longer fetches insider trades.

    Patch both ``get_public_figure_trades`` and ``get_notable_holders`` so the
    callback can run to completion, then assert ``get_insider_trades`` was
    never imported or called.
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
        # Import after the patches are active so any stale reference is also covered.
        from agents.analysts.smart_money import fetch as fetch_mod

        # Confirm the module does NOT expose get_insider_trades at all.
        assert not hasattr(fetch_mod, "get_insider_trades"), (
            "smart_money.fetch should not import get_insider_trades after Phase 5 Task 4"
        )

        result = await fetch_mod.smart_money_fetch_callback(ctx)

    # With no activity the gate should fire (no-signal short-circuit).
    assert result is not None
    assert "skipping" in result.parts[0].text


@pytest.mark.asyncio
async def test_smart_money_fetch_writes_only_politicians_and_holders():
    """The smart_money_data state dict has exactly 'politicians' and 'notable_holders' keys.

    The 'insiders' key must be absent after Phase 5 Task 4.
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
        from agents.analysts.smart_money import fetch as fetch_mod
        await fetch_mod.smart_money_fetch_callback(ctx)

    data = ctx.state["smart_money_data"]
    assert "insiders" not in data, "insiders key must be absent from smart_money_data"
    assert "politicians" in data
    assert "notable_holders" in data


# ---------------------------------------------------------------------------
# Gate behaviour with external-observer signals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate_fires_when_no_activity():
    """Callback returns a skip Content when no politicians or notable holders are present."""
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
        from agents.analysts.smart_money import fetch as fetch_mod
        result = await fetch_mod.smart_money_fetch_callback(ctx)

    assert result is not None
    assert "skipping" in result.parts[0].text
    assert ctx.state["smart_money_verdicts"] == []


@pytest.mark.asyncio
async def test_gate_passes_with_politician_trade():
    """Callback returns None (allow LLM) when at least one politician trade exists."""
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
        from agents.analysts.smart_money import fetch as fetch_mod
        result = await fetch_mod.smart_money_fetch_callback(ctx)

    assert result is None  # Gate did NOT fire — signal present.
    assert "AAPL" in ctx.state["smart_money_data"]["politicians"]


@pytest.mark.asyncio
async def test_gate_passes_with_notable_holder():
    """Callback returns None when at least one notable holder is present."""
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
        from agents.analysts.smart_money import fetch as fetch_mod
        result = await fetch_mod.smart_money_fetch_callback(ctx)

    assert result is None
    assert "MSFT" in ctx.state["smart_money_data"]["notable_holders"]


@pytest.mark.asyncio
async def test_smart_money_data_ticker_shape():
    """Per-ticker dicts inside smart_money_data are correctly populated."""
    ctx = _make_ctx(["TSLA"])

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
        from agents.analysts.smart_money import fetch as fetch_mod
        await fetch_mod.smart_money_fetch_callback(ctx)

    data = ctx.state["smart_money_data"]
    assert "TSLA" in data["politicians"]
    assert "TSLA" in data["notable_holders"]
    assert isinstance(data["politicians"]["TSLA"], list)
    assert isinstance(data["notable_holders"]["TSLA"], list)

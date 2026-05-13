"""Tests for smart_money_fetch_callback — Phase 5, Tasks 4 and 9.

After Phase 5 Task 4, smart_money_fetch_callback must NOT pull insider trades.
The callback is scoped to external-observer flows only: politician_trades and
notable_holders.

After Phase 5 Task 9 (regression fix), the callback must always return None —
returning a Content object would cause ADK to set end_invocation=True, which
would bypass _run_async_impl and prevent per-ticker no-data verdicts from
being emitted.  No-data handling is delegated to SmartMoneyAnalyst._run_async_impl.
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

    # With no activity the callback must still return None — no-data handling
    # is delegated to _run_async_impl, not the fetch callback.
    assert result is None


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
async def test_fetch_always_returns_none_when_no_activity():
    """Callback returns None even when no politicians or notable holders are present.

    Regression test for Task 9 bug: the old code returned a Content object in
    the no-signal path, which caused ADK to set end_invocation=True and
    bypass _run_async_impl entirely — preventing per-ticker no-data verdicts
    from being emitted and blocking the after-agent-callback.
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
        result = await fetch_mod.smart_money_fetch_callback(ctx)

    # Must be None — Content would trigger ADK's end_invocation shortcut.
    assert result is None

    # Callback must NOT pre-seed smart_money_verdicts — that is _run_async_impl's job.
    assert "smart_money_verdicts" not in ctx.state


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


# ---------------------------------------------------------------------------
# Regression: _run_async_impl populates verdicts even with no smart_money_data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_async_impl_emits_no_data_verdicts_when_data_empty():
    """_run_async_impl writes per-ticker no-data verdicts when smart_money_data is empty.

    Regression test for the Task 9 bug: the old fetch callback returned a
    Content object on no-signal, which caused ADK to skip _run_async_impl.
    This test confirms that _run_async_impl is able to run independently and
    correctly populates state["smart_money_verdicts"] with is_no_data=True
    entries even when every ticker has no smart-money activity.
    """
    from agents.analysts.heuristics import SmartMoneyHeuristics
    from agents.analysts.smart_money.agent import SmartMoneyAnalyst

    heuristics = SmartMoneyHeuristics(
        multi_filer_min_count=3,
        high_activity_trade_count=5,
        lone_filer_confidence_floor=0.1,
        consensus_confidence_ceiling=0.9,
        magnitude_cap=1.0,
    )
    analyst = SmartMoneyAnalyst(heuristics=heuristics)

    # Simulate the state that the fetch callback writes after finding no signal.
    # smart_money_data exists but every ticker's sub-dicts are empty lists.
    state = {
        "tickers": ["AAPL", "MSFT"],
        "smart_money_data": {
            "politicians":     {"AAPL": [], "MSFT": []},
            "notable_holders": {"AAPL": [], "MSFT": []},
        },
    }

    ctx = MagicMock()
    ctx.session.state = state

    # Drive the async generator to completion.
    async for _ in analyst._run_async_impl(ctx):
        pass

    assert "smart_money_verdicts" in state, (
        "_run_async_impl must write smart_money_verdicts to state"
    )
    verdicts = state["smart_money_verdicts"]
    assert len(verdicts) == 2, "One verdict per ticker expected"

    verdict_by_ticker = {v["ticker"]: v for v in verdicts}
    for ticker in ("AAPL", "MSFT"):
        assert ticker in verdict_by_ticker, f"Missing verdict for {ticker}"
        assert verdict_by_ticker[ticker]["is_no_data"] is True, (
            f"Expected is_no_data=True for {ticker} with empty smart_money_data"
        )
        assert verdict_by_ticker[ticker]["lean"] == "neutral"


@pytest.mark.asyncio
async def test_run_async_impl_verdicts_have_required_fields():
    """Verdict dicts from _run_async_impl include all fields required by make_evidence_callback."""
    from agents.analysts.heuristics import SmartMoneyHeuristics
    from agents.analysts.smart_money.agent import SmartMoneyAnalyst

    heuristics = SmartMoneyHeuristics(
        multi_filer_min_count=3,
        high_activity_trade_count=5,
        lone_filer_confidence_floor=0.1,
        consensus_confidence_ceiling=0.9,
        magnitude_cap=1.0,
    )
    analyst = SmartMoneyAnalyst(heuristics=heuristics)

    state = {
        "tickers": ["GOOG"],
        "smart_money_data": {
            "politicians":     {"GOOG": []},
            "notable_holders": {"GOOG": []},
        },
    }

    ctx = MagicMock()
    ctx.session.state = state

    async for _ in analyst._run_async_impl(ctx):
        pass

    verdict = state["smart_money_verdicts"][0]

    # These fields are required by make_evidence_callback and AnalystVerdict.
    for field in ("ticker", "lean", "confidence", "magnitude", "is_no_data"):
        assert field in verdict, f"Required field '{field}' missing from verdict dict"

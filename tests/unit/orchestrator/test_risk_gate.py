"""Verb-aware risk-gate skip rule — Band 4 tests.

Four cases per plan §4.5:

1. test_risk_gate_passes_hold_through_unchanged
2. test_risk_gate_passes_update_through_unchanged
3. test_risk_gate_caps_open_at_max_position_weight
4. test_risk_gate_caps_add_at_max_delta_per_ticker

The risk gate lives in ``src/agents/risk_gate/agent.py``.  These tests drive
it end-to-end via ``_run_async_impl`` against a mock InvocationContext, then
inspect the yielded ``state_delta`` to confirm the verb-aware logic.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.risk_gate.agent import _NO_RISK_GATE_INTENTS, RiskGateAgent
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from orchestrator.state import MAX_DELTA_PER_TICKER, MAX_POSITION_WEIGHT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(state: dict) -> MagicMock:
    """Minimal InvocationContext stub."""

    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


async def _collect_deltas(agent: RiskGateAgent, state: dict) -> list[dict]:
    """Drive the risk gate and collect all state_delta dicts from yielded events."""

    ctx = _make_ctx(state)
    deltas = []
    async for event in agent._run_async_impl(ctx):
        if event.actions and event.actions.state_delta:
            deltas.append(dict(event.actions.state_delta))
    return deltas


def _decision_with_stances(stances: list[TickerStance]) -> StrategistDecision:
    """Build a minimal ``StrategistDecision`` from a list of stances."""

    # Build target_weights from stance weights (only for weight-bearing stances).
    target_weights = {
        s.ticker: (s.weight or 0.0)
        for s in stances
        if s.intent not in ("hold", "update", "close")
    }

    return StrategistDecision(
        stances        = stances,
        target_weights = target_weights,
        decision_tag   = "test",
        reasoning      = "test run",
        confidence     = 0.5,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_risk_gate_intents_constant_contains_hold_and_update():
    """The frozenset constant must contain exactly hold and update."""

    assert "hold"   in _NO_RISK_GATE_INTENTS
    assert "update" in _NO_RISK_GATE_INTENTS
    # Ensure open/close/add/trim are NOT in the skip set.
    assert "open"  not in _NO_RISK_GATE_INTENTS
    assert "close" not in _NO_RISK_GATE_INTENTS
    assert "add"   not in _NO_RISK_GATE_INTENTS
    assert "trim"  not in _NO_RISK_GATE_INTENTS


@pytest.mark.asyncio
async def test_risk_gate_passes_hold_through_unchanged():
    """``hold`` stances must not be touched by the clamping logic.

    The ticker for a hold stance should not appear in ``final_orders``
    (no broker call) and should not be clipped in any way.
    """

    hold_stance = TickerStance(
        ticker = "MSFT",
        intent = "hold",
        reason = "No new information",
    )
    decision = _decision_with_stances([hold_stance])

    agent = RiskGateAgent(broker=None)
    deltas = await _collect_deltas(agent, {"strategist_decision": decision})

    # There should be exactly one state_delta event.
    assert deltas, "RiskGate must yield a state_delta event"
    delta = deltas[0]

    # MSFT must not appear in final_orders — no trade was generated.
    orders = delta.get("final_orders", [])
    msft_orders = [o for o in orders if o.get("ticker") == "MSFT"]
    assert len(msft_orders) == 0, (
        "hold stance must not produce any broker order in final_orders"
    )

    # No clamp record for MSFT either.
    clamps = delta.get("risk_clamps_applied", [])
    msft_clamps = [c for c in clamps if c.get("ticker") == "MSFT"]
    assert len(msft_clamps) == 0, (
        "hold stance must not produce any clamp record"
    )


@pytest.mark.asyncio
async def test_risk_gate_passes_update_through_unchanged():
    """``update`` stances must not be touched by the clamping logic."""

    update_stance = TickerStance(
        ticker       = "NVDA",
        intent       = "update",
        reason       = "Raising target after earnings beat",
        target_price = 1100.0,
    )
    decision = _decision_with_stances([update_stance])

    agent = RiskGateAgent(broker=None)
    deltas = await _collect_deltas(agent, {"strategist_decision": decision})

    assert deltas
    delta = deltas[0]

    # No order or clamp for NVDA.
    orders = delta.get("final_orders", [])
    nvda_orders = [o for o in orders if o.get("ticker") == "NVDA"]
    assert len(nvda_orders) == 0, (
        "update stance must not produce any broker order in final_orders"
    )

    clamps = delta.get("risk_clamps_applied", [])
    nvda_clamps = [c for c in clamps if c.get("ticker") == "NVDA"]
    assert len(nvda_clamps) == 0, (
        "update stance must not produce any clamp record"
    )


@pytest.mark.asyncio
async def test_risk_gate_caps_open_at_max_position_weight():
    """An ``open`` stance requesting weight above ``MAX_POSITION_WEIGHT`` must be clamped.

    Verifies that the cap logic still applies to trading stances after the
    hold/update skip rule is installed.
    """

    # Request a weight well above the single-position cap.
    overweight = MAX_POSITION_WEIGHT + 0.15

    open_stance = TickerStance(
        ticker       = "AAPL",
        intent       = "open",
        weight       = overweight,
        target_price = 220.0,
        stop_price   = 180.0,
        catalyst     = "iPhone supercycle",
        horizon      = "swing",
        rationale    = "Strong product cycle",
    )
    decision = _decision_with_stances([open_stance])
    # Also put the weight in target_weights so the risk gate sees it.
    decision = StrategistDecision(
        stances        = [open_stance],
        target_weights = {"AAPL": overweight},
        decision_tag   = "test",
        reasoning      = "test",
        confidence     = 0.5,
    )

    agent = RiskGateAgent(broker=None)
    deltas = await _collect_deltas(agent, {"strategist_decision": decision})

    assert deltas
    delta = deltas[0]

    # A clamp record for AAPL must exist.
    clamps = delta.get("risk_clamps_applied", [])
    aapl_clamps = [c for c in clamps if c.get("ticker") == "AAPL"]
    assert len(aapl_clamps) > 0, (
        "open above MAX_POSITION_WEIGHT must produce a clamp record"
    )


@pytest.mark.asyncio
async def test_risk_gate_caps_add_at_max_delta_per_ticker():
    """An ``add`` stance requesting a delta above ``MAX_DELTA_PER_TICKER`` must be clamped."""

    # Simulate a position already held at 0.10; request a huge add to 0.50.
    current_weight = 0.10
    requested_weight = current_weight + MAX_DELTA_PER_TICKER + 0.10

    add_stance = TickerStance(
        ticker = "TSLA",
        intent = "add",
        weight = requested_weight,
    )

    # Build a fake broker that reports TSLA held at current_weight.
    # The ``weights_to_orders`` function needs a price for TSLA in the
    # portfolio positions map — supply a mock Position with last_price set.
    from broker.portfolio import Position

    mock_position = MagicMock(spec=Position)
    mock_position.quantity   = 10.0
    mock_position.avg_cost   = 250.0
    mock_position.last_price = 250.0

    mock_portfolio = MagicMock()
    mock_portfolio.current_weights.return_value = {"TSLA": current_weight}
    mock_portfolio.total_value = 10_000.0
    mock_portfolio.positions   = {"TSLA": mock_position}
    mock_broker = MagicMock()
    mock_broker.get_portfolio  = AsyncMock(return_value=mock_portfolio)

    decision = StrategistDecision(
        stances        = [add_stance],
        target_weights = {"TSLA": requested_weight},
        decision_tag   = "test",
        reasoning      = "test",
        confidence     = 0.5,
    )

    agent = RiskGateAgent(broker=mock_broker)
    deltas = await _collect_deltas(agent, {"strategist_decision": decision})

    assert deltas
    delta = deltas[0]

    # A clamp record for TSLA must exist.
    clamps = delta.get("risk_clamps_applied", [])
    tsla_clamps = [c for c in clamps if c.get("ticker") == "TSLA"]
    assert len(tsla_clamps) > 0, (
        "add above MAX_DELTA_PER_TICKER must produce a clamp record"
    )

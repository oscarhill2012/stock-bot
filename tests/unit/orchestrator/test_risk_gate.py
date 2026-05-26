"""Verb-aware risk-gate skip rule — Band 4 tests.

Three cases:

1. test_risk_gate_passes_hold_through_unchanged
2. test_risk_gate_passes_update_through_unchanged
3. test_risk_gate_caps_open_at_max_position_weight

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
from orchestrator.state import MAX_POSITION_WEIGHT

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
    # update stances carry no weight — they are prose-only.
    target_weights = {
        s.ticker: (s.weight or 0.0)
        for s in stances
        if s.intent not in ("update",)
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
    """``update`` stances must not be touched by the clamping logic.

    (Test name preserved for backwards compatibility with CI history;
    "hold" was the old verb, "update" is the iter-3 replacement.)

    The ticker for an update stance should not appear in ``final_orders``
    (no broker call) and should not be clipped in any way.
    """

    hold_stance = TickerStance(
        ticker = "MSFT",
        intent = "update",
        rationale = "No new information",
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
        ticker = "NVDA",
        intent = "update",
        rationale = "Raising view after earnings beat — no trade",
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

    # Note: overweight exceeds the buy delta cap (0.05), so we use
    # model_construct to bypass Pydantic validation — we're testing
    # the risk gate clamping logic, not the schema.
    open_stance = TickerStance.model_construct(
        ticker    = "AAPL",
        intent    = "buy",
        weight    = overweight,
        rationale = "Strong product cycle",
        catalyst  = "iPhone supercycle",
    )
    # Use model_construct on StrategistDecision as well — the schema validates
    # stances at construction time, which would reject the overweight buy stance
    # even though we are testing the risk gate's clamping logic (not the schema).
    decision = StrategistDecision.model_construct(
        stances        = [open_stance],
        target_weights = {"AAPL": overweight},
        decision_tag   = "test",
        reasoning      = "test",
        confidence     = 0.5,
        sell_reasons   = {},
        update_reasons = {},
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



"""Loud-failure tests for the risk_gate agent.

Each test in this file exists because the historical behaviour was to
silently return / no-op on a missing or malformed input. The new contract
is "raise on every missing-input case"; these tests pin that behaviour.
"""
import pytest

from agents.risk_gate.agent import RiskGateAgent, RiskGateInputError


@pytest.mark.asyncio
async def test_risk_gate_raises_when_strategist_decision_missing(
    fake_broker_factory,
    _invocation_context_with_state,
):
    """Missing strategist_decision is a wiring bug — must raise loudly."""
    ctx = _invocation_context_with_state(state={})  # no strategist_decision
    agent = RiskGateAgent(broker=fake_broker_factory())

    with pytest.raises(RiskGateInputError, match="strategist_decision"):
        async for _ in agent._run_async_impl(ctx):
            pass


@pytest.mark.asyncio
async def test_risk_gate_raises_when_strategist_decision_is_none(
    fake_broker_factory,
    _invocation_context_with_state,
):
    """Explicit None counts as missing — must raise, not silently skip."""
    ctx = _invocation_context_with_state(state={"strategist_decision": None})
    agent = RiskGateAgent(broker=fake_broker_factory())

    with pytest.raises(RiskGateInputError, match="strategist_decision"):
        async for _ in agent._run_async_impl(ctx):
            pass

"""RiskGate BaseAgent integration test."""
from unittest.mock import MagicMock

import pytest

from agents.risk_gate.agent import RiskGateAgent
from broker.fake import FakeBroker


def _make_ctx(state: dict) -> MagicMock:
    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_risk_gate_applies_constraints_and_sets_orders():
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0, "MSFT": 300.0})
    agent = RiskGateAgent(broker=broker)
    state = {
        "strategist_decision": {
            "target_weights": {"AAPL": 0.05, "MSFT": 0.0},
            "decision_tag": "test",
            "reasoning": "ok",
            "thesis": "ok",
            "confidence": 0.7,
            "close_reasons": {},
        },
        "positions": {},
    }
    ctx = _make_ctx(state)
    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in agent._run_async_impl(ctx):
        state.update(_event.actions.state_delta)
    assert "final_orders" in state
    assert "risk_clamps_applied" in state

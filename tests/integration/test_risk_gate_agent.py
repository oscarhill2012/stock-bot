"""RiskGate BaseAgent integration test."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.risk_gate.agent import RiskGateAgent
from broker.fake import FakeBroker


def _make_ctx(state: dict) -> MagicMock:
    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
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
            "updated_thesis": "ok",
            "confidence": 0.7,
            "new_positions": {},
            "close_reasons": {},
        },
        "positions": {},
    }
    ctx = _make_ctx(state)
    async for _ in agent._run_async_impl(ctx):
        pass
    assert "final_orders" in state
    assert "risk_clamps_applied" in state

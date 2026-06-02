"""RiskGate BaseAgent integration test."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.risk_gate.agent import RiskGateAgent
from broker.fake import FakeBroker
from broker.portfolio import Portfolio, Position


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK ``InvocationContext`` double for RiskGate.

    RiskGate reads ``ctx.session.state`` and uses ``ctx.invocation_id`` in
    the yielded ``Event``; the broker is injected through the agent's
    ``broker`` field, not via the context.

    Parameters
    ----------
    state:
        The session-state dict to expose through the mock context.

    Returns
    -------
    MagicMock
        A mock context suitable for passing to ``_run_async_impl``.
    """
    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_risk_gate_applies_constraints_and_sets_orders():
    """risk_gate produces final_orders and risk_clamps_applied in state_delta."""

    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0, "MSFT": 300.0})
    agent = RiskGateAgent(broker=broker)

    # Seed state["portfolio"] so the new A-072 read path finds it.
    state = {
        "strategist_decision": {
            "target_weights": {"AAPL": 0.05, "MSFT": 0.0},
            "decision_tag":   "test",
            "reasoning":      "ok",
            "thesis":         "ok",
            "confidence":     0.7,
            "close_reasons":  {},
        },
        # Portfolio with no open positions — matches the broker's starting state.
        "portfolio": Portfolio(cash=10_000.0).model_dump(mode="json"),
    }
    ctx = _make_ctx(state)

    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in agent._run_async_impl(ctx):
        state.update(_event.actions.state_delta)

    assert "final_orders"        in state
    assert "risk_clamps_applied" in state


@pytest.mark.asyncio
async def test_risk_gate_uses_state_portfolio_not_broker() -> None:
    """risk_gate must derive current_weights from state['portfolio'], never broker (A-072).

    The audit finding A-072 identified a mid-tick ``broker.get_portfolio()``
    re-pull inside the risk gate that duplicates the Phase-2 canonicalisation
    already stored in ``state['portfolio']``.  After the fix, the broker call
    must never be reached during ``_run_async_impl`` — this test enforces that
    contract by injecting a diverging portfolio into the broker and confirming
    the mock is never invoked.
    """
    # state['portfolio'] is the canonical source the clamp loop must read.
    state_portfolio = Portfolio(
        cash      = 50.0,
        positions = {"AAPL": Position(quantity=1.0, avg_cost=90.0, last_price=100.0)},
    )

    # Real FakeBroker (so the ``_prices`` gap-fill path works), but its
    # get_portfolio is swapped for a probe that returns a DIVERGING portfolio.
    # If risk_gate ever calls it, the assert_not_called below trips.
    broker = FakeBroker(starting_cash=0.0, prices={})
    broker.get_portfolio = AsyncMock(return_value=Portfolio(cash=0.0))

    agent = RiskGateAgent(broker=broker)

    state = {
        "strategist_decision": {
            "target_weights": {"AAPL": 0.05},
            "decision_tag":   "test",
            "reasoning":      "ok",
            "thesis":         "ok",
            "confidence":     0.7,
            "close_reasons":  {},
        },
        # Canonical portfolio seed — the risk gate must read from here, not
        # from broker.get_portfolio.
        "portfolio": state_portfolio.model_dump(mode="json"),
    }

    ctx = _make_ctx(state)
    async for _event in agent._run_async_impl(ctx):
        state.update(_event.actions.state_delta)

    # Core assertion: the broker's get_portfolio must never have been invoked.
    broker.get_portfolio.assert_not_called()

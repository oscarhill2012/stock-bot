"""Rule 1 conformance test for ``RiskGateAgent``.

RiskGate previously wrote ``final_orders`` and ``risk_clamps_applied``
directly to ``ctx.session.state`` and used the ``return / yield`` no-op
generator trick.  Contract Rule 1 (``docs/contract-invariants.md``
§C-Rule 1) demands a yielded ``Event`` whose ``actions.state_delta``
carries the writes.  This test locks the new shape in.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.risk_gate.agent import RiskGateAgent
from broker.fake import FakeBroker
from broker.portfolio import Portfolio


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK ``InvocationContext`` double for RiskGate.

    RiskGate reads ``ctx.session.state`` and uses ``ctx.invocation_id`` in
    the yielded ``Event``; the broker is injected through the agent's
    ``broker`` field, not via the context.
    """

    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_risk_gate_yields_state_delta_with_orders_and_clamps() -> None:
    """``_run_async_impl`` must yield one ``Event`` whose ``state_delta``
    carries both ``final_orders`` and ``risk_clamps_applied`` in a single
    payload.

    Why a single Event: the two writes are part of the same logical
    boundary (RiskGate's output handshake to Executor) and the contract
    treats co-emitted writes as one atomic update.
    """

    broker = FakeBroker(
        starting_cash=10_000.0,
        prices={"AAPL": 200.0, "MSFT": 300.0},
    )
    agent = RiskGateAgent(broker=broker)

    # The decision shape matches the existing integration test in
    # ``tests/integration/test_risk_gate_agent.py``.  AAPL has a positive
    # target weight so an order is generated; MSFT is left at zero.
    state: dict = {
        "strategist_decision": {
            "target_weights": {"AAPL": 0.05, "MSFT": 0.0},
            "decision_tag":   "test",
            "reasoning":      "ok",
            "thesis": "ok",
            "confidence":     0.7,
            "close_reasons":  {},
        },
        "positions": {},
        # A-072: seed state["portfolio"] so the risk gate reads from state
        # rather than broker.get_portfolio (which no longer exists as the
        # canonical path after the audit fix).
        "portfolio": Portfolio(cash=10_000.0).model_dump(mode="json"),
    }
    ctx = _make_ctx(state)

    events: list = []
    async for event in agent._run_async_impl(ctx):
        events.append(event)

    assert len(events) == 1, (
        f"expected exactly one yielded Event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    assert "final_orders" in delta
    assert "risk_clamps_applied" in delta
    assert isinstance(delta["final_orders"], list)
    assert isinstance(delta["risk_clamps_applied"], list)

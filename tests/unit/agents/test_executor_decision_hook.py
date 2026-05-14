"""The executor invokes a registered ``DecisionLogger`` after submitting orders."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.executor.agent import ExecutorAgent
from broker.fake import FakeBroker
from orchestrator.state import Order


@pytest.mark.asyncio
async def test_executor_calls_decision_logger_on_each_fill(tmp_path) -> None:
    """Filled orders fan out to ``state['_decision_logger'].on_executions``."""
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    agent  = ExecutorAgent(broker=broker, db_session=None)
    fake_logger = MagicMock()

    state = {
        "tick_id": "t1",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=1, est_price=150.0)
                 .model_dump(),
        ],
        "positions": {},
        "strategist_decision": {"new_positions": {"AAPL": {
            "opened_price": 150.0, "horizon": "swing",
            "rationale": "test", "opened_tag": "test",
            "opened_at": "2023-03-13T09:30:00+00:00",
        }}},
        "_decision_logger": fake_logger,
    }
    ctx = SimpleNamespace(session=SimpleNamespace(state=state))

    async for _ in agent._run_async_impl(ctx):
        pass

    fake_logger.on_executions.assert_called_once()


@pytest.mark.asyncio
async def test_executor_no_op_without_decision_logger() -> None:
    """Live runs without ``_decision_logger`` in state do not fail."""
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    agent  = ExecutorAgent(broker=broker, db_session=None)

    state = {
        "tick_id": "t2",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=1, est_price=150.0)
                 .model_dump(),
        ],
        "positions": {},
        "strategist_decision": {"new_positions": {"AAPL": {
            "opened_price": 150.0, "horizon": "swing",
            "rationale": "test", "opened_tag": "test",
            "opened_at": "2023-03-13T09:30:00+00:00",
        }}},
        # No "_decision_logger" key — simulates a live run.
    }
    ctx = SimpleNamespace(session=SimpleNamespace(state=state))

    # Must complete without raising.
    async for _ in agent._run_async_impl(ctx):
        pass

    assert state["executions"][0]["status"] == "filled"

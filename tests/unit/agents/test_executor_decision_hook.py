"""Regression tests for the DecisionLogger hook wired into ExecutorAgent."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.executor.agent import ExecutorAgent
from broker.fake import FakeBroker
from orchestrator.state import Order


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK InvocationContext stub from a plain state dict.

    Sets ``invocation_id`` to a real string because the executor now yields
    an ``Event`` whose ``invocation_id`` field is Pydantic-validated.
    """

    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_executor_no_op_without_decision_logger() -> None:
    """Executor runs normally when ``_decision_logger`` is absent from state.

    This guards the live-mode path: the hook must be a strict no-op when the
    key is not set, so the executor's existing behaviour is unchanged.
    """
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    agent = ExecutorAgent(broker=broker, db_session=None)

    state = {
        "tick_id": "t-noop",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=1, est_price=150.0).model_dump()
        ],
        "positions": {},
        "strategist_decision": {"new_positions": {}},
        # No ``_decision_logger`` key — the hook must not fire.
    }
    ctx = _make_ctx(state)

    async for _ in agent._run_async_impl(ctx):
        pass

    # Execution still recorded correctly — no side-effects from missing logger.
    assert len(state["executions"]) == 1
    assert state["executions"][0]["status"] == "filled"


@pytest.mark.asyncio
async def test_executor_calls_decision_logger_on_fill() -> None:
    """A registered ``DecisionLogger`` has ``on_executions`` called after a fill.

    Injects a MagicMock as the logger so we can assert it was invoked with the
    post-execution state snapshot.
    """
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    agent = ExecutorAgent(broker=broker, db_session=None)
    fake_logger = MagicMock()

    state = {
        "tick_id": "t1",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=1, est_price=150.0).model_dump()
        ],
        "positions": {},
        "strategist_decision": {
            "new_positions": {
                "AAPL": {
                    "opened_price": 150.0,
                    "horizon": "swing",
                    "rationale": "test",
                    "opened_tag": "test",
                    "opened_at": "2023-03-13T09:30:00+00:00",
                }
            }
        },
        "temp:_decision_logger": fake_logger,
    }
    ctx = _make_ctx(state)

    async for _ in agent._run_async_impl(ctx):
        pass

    # Logger must have been called exactly once with the state snapshot.
    fake_logger.on_executions.assert_called_once()

    # The argument passed must be a dict (a copy of state) containing executions.
    call_arg = fake_logger.on_executions.call_args[0][0]
    assert isinstance(call_arg, dict)
    assert "executions" in call_arg


@pytest.mark.asyncio
async def test_executor_logger_exception_does_not_abort_tick() -> None:
    """A crashing logger must not propagate — the tick result is still recorded.

    This verifies the defensive ``try / except`` wrapper in the hook.
    """
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    agent = ExecutorAgent(broker=broker, db_session=None)

    # Logger that always raises on ``on_executions``.
    broken_logger = MagicMock()
    broken_logger.on_executions.side_effect = RuntimeError("simulated logger crash")

    state = {
        "tick_id": "t-crash",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=1, est_price=150.0).model_dump()
        ],
        "positions": {},
        "strategist_decision": {"new_positions": {}},
        "temp:_decision_logger": broken_logger,
    }
    ctx = _make_ctx(state)

    # Must not raise — the executor completes despite the logger error.
    async for _ in agent._run_async_impl(ctx):
        pass

    # Fill was still recorded correctly.
    assert state["executions"][0]["status"] == "filled"

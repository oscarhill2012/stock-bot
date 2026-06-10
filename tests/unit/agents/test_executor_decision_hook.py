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
    """Executor runs normally when ``temp:_decision_logger`` is absent from state.

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
        "user:positions": {},   # prior held book (empty)
        "strategist_decision": {"stances": []},
        # No ``temp:_decision_logger`` key — the hook must not fire.
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
        "as_of":   "2023-03-13T09:30:00+00:00",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=1, est_price=150.0).model_dump()
        ],
        "user:positions": {},   # prior held book (empty)
        "strategist_decision": {
            # Band 6: executor assembles PositionThesis from the buy-intent
            # stance + fill price; ``new_positions`` is no longer needed here.
            "stances": [
                {
                    # Four-verb schema (buy / sell / update / no_action).
                    # Deleted fields horizon / target_price / stop_price / catalyst
                    # were removed in Plan-02; extra="forbid" rejects them.
                    "ticker":    "AAPL",
                    "intent":    "buy",
                    "weight":    0.10,
                    "rationale": "test",
                },
            ],
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
        "user:positions": {},   # prior held book (empty)
        "strategist_decision": {"stances": []},
        "temp:_decision_logger": broken_logger,
    }
    ctx = _make_ctx(state)

    # Must not raise — the executor completes despite the logger error.
    async for _ in agent._run_async_impl(ctx):
        pass

    # Fill was still recorded correctly.
    assert state["executions"][0]["status"] == "filled"


@pytest.mark.asyncio
async def test_executor_accepts_iso_string_as_of_on_sell() -> None:
    """state["as_of"] arriving as an ISO-8601 string must not raise when the
    executor calculates holding_hours for a SELL order.

    Locks in the fix that dropped the ``isinstance(raw_as_of, datetime)``
    pre-filter at the resolve_as_of call inside the SELL branch.
    """
    from broker.portfolio import Position

    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 160.0})
    # Seed broker with an existing AAPL position so the SELL can fill.
    broker._positions["AAPL"] = Position(quantity=1, avg_cost=150.0, last_price=160.0)
    agent = ExecutorAgent(broker=broker, db_session=None)

    iso_as_of = "2026-05-08T14:00:00+00:00"

    state = {
        "tick_id":  "t-iso-sell",
        "as_of":    iso_as_of,              # ISO string, not datetime
        "final_orders": [
            Order(ticker="AAPL", action="SELL", quantity=1, est_price=160.0).model_dump()
        ],
        # Seed the canonical cross-tick thesis-book — no bridge key (A-014).
        # The iso-as_of test only needs the opened_price and opened_at fields
        # to be present so the SELL path can compute holding_hours.
        "user:positions": {
            "AAPL": {
                "opened_price":   150.0,
                "rationale":      "test",
                "opened_at":      "2026-05-01T14:00:00+00:00",
                "opened_tick_id": "t-open",
            }
        },
        "strategist_decision": {"stances": []},
    }
    ctx = _make_ctx(state)

    # Must not raise — previously the isinstance pre-filter turned as_of to None
    # and STOCKBOT_STRICT_AS_OF=1 would cause AsOfRequiredError on SELL.
    async for _ in agent._run_async_impl(ctx):
        pass

    assert state["executions"][0]["status"] == "filled"

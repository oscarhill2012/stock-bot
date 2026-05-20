"""Executor integration tests with FakeBroker."""
from unittest.mock import MagicMock

import pytest

from agents.executor.agent import build_executor
from broker.fake import FakeBroker


def _make_ctx(state: dict) -> MagicMock:
    """Build a mock InvocationContext for the executor under test.

    The executor now yields an ``Event`` whose ``invocation_id`` field is a
    Pydantic-validated string, so the mock must return a real string rather
    than the default ``MagicMock`` attribute proxy.
    """

    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_executor_buy_fills():
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    executor = build_executor(broker)
    state = {
        "tick_id": "tick-1",
        "final_orders": [{"ticker": "AAPL", "action": "BUY", "quantity": 5.0, "est_price": 200.0}],
        "positions": {},
        "strategist_decision": {"decision_tag": "buy_aapl", "close_reasons": {}},
    }
    ctx = _make_ctx(state)
    async for _ in executor._run_async_impl(ctx):
        pass
    executions = state["executions"]
    assert len(executions) == 1
    assert executions[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_executor_idempotent():
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    executor = build_executor(broker)
    state = {
        "tick_id": "tick-1",
        "last_executed_tick_id": "tick-1",  # already executed
        "final_orders": [{"ticker": "AAPL", "action": "BUY", "quantity": 5.0, "est_price": 200.0}],
        "positions": {},
        "strategist_decision": {"decision_tag": "buy_aapl", "close_reasons": {}},
    }
    ctx = _make_ctx(state)
    async for _ in executor._run_async_impl(ctx):
        pass
    assert "executions" not in state  # no executions because idempotent skip


@pytest.mark.asyncio
async def test_executor_stamps_opened_price_on_buy():
    """The executor must stamp ``positions[ticker]["opened_price"]`` with the
    real fill price after a BUY clears.

    This is the architectural handoff between strategist and executor:
    the strategist emits ``new_positions[ticker]`` with ``opened_price=None``
    because it has no honest fill price at decision time; the executor
    knows the real fill price from the broker and stamps it into the
    position book.  Without this stamp, the next tick's held-view renderer
    would divide by ``None`` / ``0`` — which is exactly the
    ``ZeroDivisionError`` the pre-fix backtest produced.

    Setup: a strategist decision that opens a new AAPL position with no
    opened_price, paired with a FakeBroker that will fill at $215.50.
    Assert: after the executor runs, ``state["positions"]["AAPL"]`` carries
    the same thesis dict but with ``opened_price`` filled in at 215.50.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 215.50})
    executor = build_executor(broker)

    # Strategist-emitted thesis: rich on intent (target, stop, horizon,
    # rationale) but deliberately silent on opened_price.
    thesis_from_strategist = {
        "ticker":          "AAPL",
        "opened_at":       "2026-05-08T14:00:00+00:00",
        "opened_price":    None,                  # strategist cannot know the fill
        "opened_tag":      "morning_sweep",
        "rationale":       "earnings beat + insider buying",
        "horizon":         "swing",
        "target_price":    230.0,
        "stop_price":      200.0,
        "catalyst":        "Q3 earnings",
        "last_reviewed_at": "2026-05-08T14:00:00+00:00",
        "last_review_note": "",
        "opened_tick_id":  "tick-1",
    }

    state = {
        "tick_id":      "tick-1",
        "final_orders": [
            {"ticker": "AAPL", "action": "BUY", "quantity": 5.0, "est_price": 200.0},
        ],
        "positions":    {},
        "strategist_decision": {
            "decision_tag":  "morning_sweep",
            "new_positions": {"AAPL": thesis_from_strategist},
            "close_reasons": {},
        },
    }

    ctx = _make_ctx(state)
    async for _ in executor._run_async_impl(ctx):
        pass

    # The position book must now carry the thesis with opened_price stamped.
    assert "AAPL" in state["positions"]
    stamped = state["positions"]["AAPL"]
    assert stamped["opened_price"] == pytest.approx(215.50)

    # Every other field on the thesis must survive the round-trip unchanged
    # — we only stamp opened_price, never rewrite the intent fields.
    assert stamped["target_price"] == 230.0
    assert stamped["stop_price"]   == 200.0
    assert stamped["horizon"]      == "swing"
    assert stamped["rationale"]    == "earnings beat + insider buying"
    assert stamped["opened_tag"]   == "morning_sweep"
    assert stamped["opened_tick_id"] == "tick-1"

    # And critically: the strategist's source dict must not have been mutated
    # in place — downstream consumers (decision snapshot logger) still see
    # the original ``opened_price=None`` payload.
    source = state["strategist_decision"]["new_positions"]["AAPL"]
    assert source["opened_price"] is None


@pytest.mark.asyncio
async def test_executor_rejection_continues():
    broker = FakeBroker(starting_cash=100.0, prices={"AAPL": 200.0})  # not enough cash
    executor = build_executor(broker)
    state = {
        "tick_id": "tick-1",
        "final_orders": [{"ticker": "AAPL", "action": "BUY", "quantity": 5.0, "est_price": 200.0}],
        "positions": {},
        "strategist_decision": {"decision_tag": "buy_aapl", "close_reasons": {}},
    }
    ctx = _make_ctx(state)
    async for _ in executor._run_async_impl(ctx):
        pass
    executions = state["executions"]
    assert executions[0]["status"] == "rejected"

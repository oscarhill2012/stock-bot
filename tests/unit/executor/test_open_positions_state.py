"""Unit tests for executor BUY-thesis write and SELL tick-id FK population.

Covers:
- BUY: executor writes thesis dict into state["user:positions"][ticker].
- SELL: executor removes ticker from state["user:positions"].
- SELL + DB: executor populates opening_tick_id / closing_tick_id on TradeLogRow.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.executor.agent import ExecutorAgent
from broker.fake import FakeBroker
from orchestrator.persistence import Base, TradeLogRow
from orchestrator.state import Order

# ── Helpers ───────────────────────────────────────────────────────────────────


class _StubCtx:
    """Minimal ADK InvocationContext stand-in wrapping a plain dict as session state.

    Carries ``invocation_id`` as a real string because the executor now yields
    an ``Event`` whose ``invocation_id`` field is Pydantic-validated.
    """

    def __init__(self, state: dict) -> None:
        session = MagicMock()
        session.state = state
        self.session = session
        self.invocation_id = "test-invocation"


async def _run(agent: ExecutorAgent, state: dict) -> list:
    """Drive the executor's async generator to completion against the given state dict.

    Returns the list of events yielded so callers can inspect the state_delta.
    """
    ctx = _StubCtx(state)
    events = []
    async for ev in agent._run_async_impl(ctx):
        events.append(ev)
    return events


# ── Session fixture ───────────────────────────────────────────────────────────


@pytest.fixture
def session(tmp_path):
    """Yield a freshly-created SQLite session backed by a tmp file; close on teardown."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    yield s
    s.close()


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buy_writes_thesis_to_state_positions():
    """After a BUY executes, the thesis dict from new_positions lands in state["user:positions"].

    The executor must copy the thesis wholesale so downstream SELL logic
    can recover opened_price, opened_at, opened_tick_id, etc.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    executor = ExecutorAgent(broker=broker)

    thesis = {
        "opened_tick_id": "tick_X",
        "target_price": 220.0,
        "opened_at": datetime(2026, 4, 1, 14, tzinfo=UTC).isoformat(),
        "opened_price": 200.0,
        "horizon": "swing",
        "opened_tag": "open_aapl",
        "rationale": "strong momentum",
    }

    state = {
        "tick_id": "tick_X",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=5.0, est_price=200.0),
        ],
        "user:positions": {},
        "strategist_decision": {
            "decision_tag": "open_aapl",
            "close_reasons": {},
            # new_positions carries the thesis dict keyed by ticker
            "new_positions": {"AAPL": thesis},
        },
    }

    events = await _run(executor, state)

    # The thesis dict must now be stored under ``user:positions`` in the yielded
    # event's state_delta.  The ``_run_async_impl`` includes ``user:positions``
    # in the state_delta so it is persisted cross-tick by DatabaseSessionService.
    assert len(events) == 1, "BUY must cause executor to yield one state-delta event"
    delta = events[0].actions.state_delta
    assert "AAPL" in delta["user:positions"], "BUY did not write AAPL into state_delta['user:positions']"
    assert delta["user:positions"]["AAPL"] == thesis


@pytest.mark.asyncio
async def test_sell_removes_ticker_from_state_positions():
    """After a SELL executes, the ticker is removed from state["user:positions"].

    We pre-seed state["user:positions"] directly (simulating an earlier BUY tick),
    then drive a SELL and confirm the key is gone.
    """
    # Seed the broker so the SELL can succeed — need the position in the broker too.
    broker = FakeBroker(starting_cash=1_000.0, prices={"AAPL": 200.0})
    # Submit a BUY to the broker so it has an AAPL position to sell.
    await broker.submit_market("AAPL", "BUY", 5.0)

    executor = ExecutorAgent(broker=broker)

    # Pre-seed the position thesis in state (as the BUY would have left it).
    existing_thesis = {
        "opened_tick_id": "tick_OPEN",
        "opened_at": datetime(2026, 4, 1, 14, tzinfo=UTC).isoformat(),
        "opened_price": 200.0,
        "horizon": "swing",
        "opened_tag": "open_aapl",
        "rationale": "strong momentum",
    }

    state = {
        "tick_id": "tick_CLOSE",
        "final_orders": [
            Order(ticker="AAPL", action="SELL", quantity=5.0, est_price=200.0),
        ],
        "user:positions": {"AAPL": existing_thesis},
        "strategist_decision": {
            "decision_tag": "close_aapl",
            "close_reasons": {"AAPL": "target reached"},
        },
    }

    events = await _run(executor, state)

    # After a full SELL, AAPL must be absent from ``user:positions`` in the
    # yielded event's state_delta.
    assert len(events) == 1, "SELL must cause executor to yield one state-delta event"
    delta = events[0].actions.state_delta
    assert "AAPL" not in delta["user:positions"], "SELL did not remove AAPL from state_delta['user:positions']"


@pytest.mark.asyncio
async def test_sell_writes_tick_id_fks_to_trade_log(session):
    """SELL must populate opening_tick_id (from thesis) and closing_tick_id (from state).

    The opening_tick_id comes from thesis["opened_tick_id"]; closing_tick_id
    from state["tick_id"] at the time of sale. Both must round-trip through
    save_trade_log_entry into the TradeLogRow.
    """
    # Seed the broker so it has AAPL to sell.
    broker = FakeBroker(starting_cash=1_000.0, prices={"AAPL": 200.0})
    await broker.submit_market("AAPL", "BUY", 5.0)

    executor = ExecutorAgent(broker=broker, db_session=session)

    # Thesis carries the FK from the deliberation tick that opened the position.
    existing_thesis = {
        "opened_tick_id": "tick_OPEN",
        "opened_at": datetime(2026, 4, 1, 14, tzinfo=UTC).isoformat(),
        "opened_price": 200.0,
        "horizon": "swing",
        "opened_tag": "open_aapl",
        "rationale": "strong momentum",
    }

    state = {
        # tick_id here is the deliberation tick that is closing the position.
        "tick_id": "tick_CLOSE",
        "final_orders": [
            Order(ticker="AAPL", action="SELL", quantity=5.0, est_price=200.0),
        ],
        "user:positions": {"AAPL": existing_thesis},
        "strategist_decision": {
            "decision_tag": "close_aapl",
            "close_reasons": {"AAPL": "target reached"},
        },
    }

    await _run(executor, state)  # events not needed — asserting on DB row

    # Verify the TradeLogRow was written with the correct FK values.
    row = session.query(TradeLogRow).first()
    assert row is not None, "No TradeLogRow was written on SELL"
    assert row.opening_tick_id == "tick_OPEN", (
        f"Expected opening_tick_id='tick_OPEN', got {row.opening_tick_id!r}"
    )
    assert row.closing_tick_id == "tick_CLOSE", (
        f"Expected closing_tick_id='tick_CLOSE', got {row.closing_tick_id!r}"
    )

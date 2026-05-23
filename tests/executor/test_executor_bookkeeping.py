"""Executor bookkeeping tests — trim vs. full-exit behaviour.

Verifies that:
- A partial SELL (trim) preserves state["user:positions"][ticker] and writes no TradeLogRow.
- A full SELL (close) removes state["user:positions"][ticker] and writes exactly one TradeLogRow.

These are the two halves of the S2 gate: the executor must query the broker
post-fill for remaining quantity, not infer it from the order.
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

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

_TICKER = "TSLA"
_OPEN_PRICE = 100.0
_OPEN_AT = datetime(2026, 4, 1, 10, tzinfo=UTC)

# Minimal PositionThesis-shaped dict that the executor expects in state["user:positions"].
# Matches the fields accessed by the trade-log-write block in executor/agent.py.
_THESIS: dict = {
    "ticker":           _TICKER,
    "opened_at":        _OPEN_AT.isoformat(),
    "opened_price":     _OPEN_PRICE,
    "opened_tag":       "test_open",
    "rationale":        "test rationale",
    "horizon":          "swing",
    "target_price":     120.0,
    "stop_price":       90.0,
    "catalyst":         "test catalyst",
    "last_reviewed_at": _OPEN_AT.isoformat(),
    "last_review_note": "",
    "opened_tick_id":   "tick-open",
}


class _StubCtx:
    """Minimal ADK InvocationContext stand-in; mirrors the pattern in test_open_positions_state.py."""

    def __init__(self, state: dict) -> None:
        session = MagicMock()
        session.state = state
        self.session = session
        self.invocation_id = "test-invocation"


async def _run(agent: ExecutorAgent, state: dict) -> list:
    """Drive the executor's async generator to completion.

    Returns the list of events yielded so callers can inspect the state_delta.
    """
    ctx = _StubCtx(state)
    events = []
    async for ev in agent._run_async_impl(ctx):
        events.append(ev)
    return events


@pytest.fixture
def session(tmp_path):
    """Freshly-created SQLite session backed by a tmp file; closed on teardown."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    yield s
    s.close()


async def _broker_with_position(qty: float) -> FakeBroker:
    """Return a FakeBroker that already holds ``qty`` shares of TSLA at $100."""
    broker = FakeBroker(starting_cash=50_000.0, prices={_TICKER: _OPEN_PRICE})
    await broker.submit_market(_TICKER, "BUY", qty)
    return broker


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_trim_preserves_position_thesis(session):
    """A partial SELL must not delete the position slot or write a TradeLogRow.

    Scenario: broker holds 100 shares of TSLA; the executor is asked to sell
    only 1 share. After the fill, 99 shares remain, so the position thesis in
    state["user:positions"] must survive and no trade-log row must be written.
    """
    broker = await _broker_with_position(100.0)
    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id": "tick-trim",
        "final_orders": [
            Order(ticker=_TICKER, action="SELL", quantity=1.0, est_price=_OPEN_PRICE),
        ],
        "user:positions":      {_TICKER: dict(_THESIS)},
        "strategist_decision": {
            "decision_tag":  "trim_tsla",
            "close_reasons": {_TICKER: "trim only"},
        },
    }

    events = await _run(executor, state)

    # The position slot must still be present — the thesis was not wiped.
    # ``_run_async_impl`` includes ``user:positions`` in the yielded event's
    # state_delta; after a trim the ticker must survive in that delta.
    assert len(events) == 1, "executor must yield exactly one event"
    delta = events[0].actions.state_delta
    assert _TICKER in delta["user:positions"], (
        "Trim SELL must not delete the position slot from state_delta['user:positions']"
    )

    # No TradeLogRow should have been written for a mere trim.
    row_count = session.query(TradeLogRow).count()
    assert row_count == 0, (
        f"Trim SELL must not write a TradeLogRow; found {row_count} row(s)"
    )


async def test_full_exit_writes_one_trade_log_row_and_deletes(session):
    """A full SELL (100 % of held shares) must delete the position slot and write exactly one TradeLogRow.

    Scenario: broker holds 100 shares of TSLA; the executor sells all 100.
    After the fill, remaining_qty == 0.0, so state["user:positions"] must lose the
    TSLA key and exactly one TradeLogRow must be committed.
    """
    broker = await _broker_with_position(100.0)
    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id": "tick-close",
        "final_orders": [
            Order(ticker=_TICKER, action="SELL", quantity=100.0, est_price=_OPEN_PRICE),
        ],
        "user:positions":      {_TICKER: dict(_THESIS)},
        "strategist_decision": {
            "decision_tag":  "close_tsla",
            "close_reasons": {_TICKER: "target reached"},
        },
    }

    events = await _run(executor, state)

    # The position slot must be gone — the trade is closed.
    # ``_run_async_impl`` includes ``user:positions`` in the yielded event's
    # state_delta; after a full exit the ticker must be absent from that delta.
    assert len(events) == 1, "executor must yield exactly one event"
    delta = events[0].actions.state_delta
    assert _TICKER not in delta["user:positions"], (
        "Full SELL must remove the ticker from state_delta['user:positions']"
    )

    # Exactly one TradeLogRow must have been written.
    rows = session.query(TradeLogRow).all()
    assert len(rows) == 1, (
        f"Full SELL must write exactly one TradeLogRow; found {len(rows)} row(s)"
    )

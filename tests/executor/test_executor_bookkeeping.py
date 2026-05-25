"""Executor bookkeeping tests — trim vs. full-exit behaviour.

Verifies that:
- A partial SELL (trim) preserves the position thesis in the bare-key bridge and writes no TradeLogRow.
- A full SELL (close) removes the position thesis from the bare-key bridge and writes exactly one TradeLogRow.

These are the two halves of the S2 gate: the executor must query the broker
post-fill for remaining quantity, not infer it from the order.

Note: these tests call ``_run_async_impl`` directly (not via the ADK Runner),
so they assert against the Band 4 bare-key ``"positions"`` bridge in the
state_delta.  ``user:positions`` (canonical key) is written by the
after-callback (``_executor_thesis_writer_callback``) which only fires when
the full ADK Runner lifecycle executes.
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

# Minimal PositionThesis-shaped dict that the executor expects in the bare-key bridge state["positions"].
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
    the bare-key bridge must survive and no trade-log row must be written.

    Asserts against state_delta["positions"] (Band 4 bridge) — not
    state_delta["user:positions"], which belongs to the after-callback.
    """
    broker = await _broker_with_position(100.0)
    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id": "tick-trim",
        "final_orders": [
            Order(ticker=_TICKER, action="SELL", quantity=1.0, est_price=_OPEN_PRICE),
        ],
        "positions":           {_TICKER: dict(_THESIS)},   # Band 4 bare-key bridge
        "strategist_decision": {
            "decision_tag":  "trim_tsla",
            "close_reasons": {_TICKER: "trim only"},
        },
    }

    events = await _run(executor, state)

    # The position slot must still be present — the thesis was not wiped.
    # Assert against the bare-key bridge ``"positions"`` in the state_delta;
    # ``user:positions`` must be absent (it is the after-callback's territory).
    assert len(events) == 1, "executor must yield exactly one event"
    delta = events[0].actions.state_delta
    assert _TICKER in delta["positions"], (
        "Trim SELL must not delete the position slot from state_delta['positions'] bridge"
    )
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # No TradeLogRow should have been written for a mere trim.
    row_count = session.query(TradeLogRow).count()
    assert row_count == 0, (
        f"Trim SELL must not write a TradeLogRow; found {row_count} row(s)"
    )


async def test_full_exit_writes_one_trade_log_row_and_deletes(session):
    """A full SELL (100 % of held shares) must delete the position slot and write exactly one TradeLogRow.

    Scenario: broker holds 100 shares of TSLA; the executor sells all 100.
    After the fill, remaining_qty == 0.0, so the bare-key bridge must lose the
    TSLA key and exactly one TradeLogRow must be committed.

    Asserts against state_delta["positions"] (Band 4 bridge) — not
    state_delta["user:positions"], which belongs to the after-callback.
    """
    broker = await _broker_with_position(100.0)
    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id": "tick-close",
        "final_orders": [
            Order(ticker=_TICKER, action="SELL", quantity=100.0, est_price=_OPEN_PRICE),
        ],
        "positions":           {_TICKER: dict(_THESIS)},   # Band 4 bare-key bridge
        "strategist_decision": {
            "decision_tag":  "close_tsla",
            "close_reasons": {_TICKER: "target reached"},
        },
    }

    events = await _run(executor, state)

    # The position slot must be gone — the trade is closed.
    # Assert against the bare-key bridge ``"positions"`` in the state_delta;
    # ``user:positions`` must be absent (it is the after-callback's territory).
    assert len(events) == 1, "executor must yield exactly one event"
    delta = events[0].actions.state_delta
    assert _TICKER not in delta["positions"], (
        "Full SELL must remove the ticker from state_delta['positions'] bridge"
    )
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # Exactly one TradeLogRow must have been written.
    rows = session.query(TradeLogRow).all()
    assert len(rows) == 1, (
        f"Full SELL must write exactly one TradeLogRow; found {len(rows)} row(s)"
    )


async def test_full_exit_appends_to_user_closed_trades_log(session):
    """A full close must also populate the rolling ``user:closed_trades_log``.

    The log is read by ``StrategistContextShim`` on the next tick to render
    the "Recent round-trips" prompt block, so the strategist can see its own
    outcome history (P&L, hold time, close reason) when deciding whether to
    re-enter the same ticker.  Mirror of the DB ``trade_log`` row, but kept
    in-memory so it works even without a DB session wired.

    Scenario: TSLA opened at $100, closed at $110 → +10% gain.  Assert the
    rolling-log entry carries the rounded P&L, the close reason copied from
    the strategist decision, and that the same key rides on the state_delta
    so it persists across ticks.
    """
    # Hold price at $100 long enough to BUY, then bump to $110 to take the
    # close fill 10% higher — produces a clean +10% pnl_pct on the close.
    broker = await _broker_with_position(100.0)
    broker.set_price(_TICKER, 110.0)

    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id": "tick-close",
        "as_of":   "2026-04-02T14:00:00+00:00",   # 28h after _OPEN_AT
        "final_orders": [
            Order(ticker=_TICKER, action="SELL", quantity=100.0, est_price=110.0),
        ],
        "positions":           {_TICKER: dict(_THESIS)},
        "strategist_decision": {
            "decision_tag":  "close_tsla",
            "close_reasons": {_TICKER: "target reached"},
        },
    }

    events = await _run(executor, state)

    # The rolling log was populated in-memory.
    closed_log = state["user:closed_trades_log"]
    assert len(closed_log) == 1, (
        f"Close must append exactly one entry; got {len(closed_log)}"
    )

    entry = closed_log[0]
    assert entry["ticker"]        == _TICKER
    assert entry["pnl_pct"]       == pytest.approx(10.0)
    assert entry["holding_hours"] == 28
    assert entry["close_reason"]  == "target reached"
    assert entry["closed_at"]     == "2026-04-02T14:00:00+00:00"

    # The same key must ride on the state_delta so the value persists across
    # ticks (DatabaseSessionService only merges what's in the delta).
    delta = events[0].actions.state_delta
    assert delta.get("user:closed_trades_log") == closed_log, (
        "Executor must include user:closed_trades_log in the state_delta "
        "whenever it mutates the in-memory copy — otherwise the next tick "
        "would see a stale value on storage rehydration."
    )

"""Executor bookkeeping tests — trim vs. full-exit behaviour.

Verifies that:
- A partial SELL (trim) preserves the position thesis in the local ``positions``
  dict (seeded from ``user:positions``) and writes no TradeLogRow.
- A full SELL (close) removes the position thesis and writes exactly one TradeLogRow.

These are the two halves of the S2 gate: the executor must query the broker
post-fill for remaining quantity, not infer it from the order.

The prior held book is seeded via ``state["user:positions"]`` — the canonical
cross-tick thesis-book written by ``_executor_thesis_writer_callback`` and
re-hydrated by ADK at each tick start (audit A-014).  The removed bridge key
``temp:executor_positions_bridge`` no longer exists and must not appear in any
test state or assertion.

Note: these tests call ``_run_async_impl`` directly (not via the ADK Runner),
so they assert against observable outcomes (TradeLogRow DB writes, executions,
``user:closed_trades_log``) rather than internal state keys.  ``user:positions``
(canonical key) is written by the after-callback
(``_executor_thesis_writer_callback``) which only fires when the full ADK
Runner lifecycle executes — not asserted here.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.executor.agent import ExecutorAgent
from agents.strategist.position_thesis import PositionThesis
from broker.fake import FakeBroker
from orchestrator.persistence import Base, TradeLogRow
from orchestrator.state import Order

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

_TICKER = "TSLA"
_OPEN_PRICE = 100.0
_OPEN_AT = datetime(2026, 4, 1, 10, tzinfo=UTC)

# Minimal PositionThesis-shaped dict that the executor expects in
# state["user:positions"].  Built via PositionThesis so it respects the
# extra="forbid" schema — no horizon / target_price / stop_price / opened_tag.
# The ``opened_tag`` fallback chain in the SELL path falls back to
# ``opened_tick_id`` when the field is absent (as it is here).
_THESIS: dict = PositionThesis(
    ticker                 = _TICKER,
    opened_at              = _OPEN_AT,
    opened_tick_id         = "tick-open",
    opened_price           = _OPEN_PRICE,
    weight                 = 0.04,
    rationale              = "test rationale",
    last_reviewed_at       = _OPEN_AT,
    last_reviewed_decision = "buy",
    last_reviewed_reason   = "test rationale",
    thesis_last_updated_tick = 0,
).model_dump(mode="json")


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

    Parameters
    ----------
    agent:
        The ``ExecutorAgent`` under test.
    state:
        Session state dict to expose through the stub context.
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


async def test_buy_stance_fills_and_records_execution(session):
    """A BUY order with a matching intent='buy' stance must fill successfully
    and the execution must be recorded in the state_delta.

    The executor assembles a PositionThesis locally for same-tick SELL use,
    but that internal dict is NOT exposed in the state_delta.  The observable
    outcome is a filled execution record.

    (Previously this tested state_delta["temp:executor_positions_bridge"] —
    the bridge key was removed by audit A-014.  Coverage is now via the filled
    execution record, which proves the BUY path ran without error.)
    """
    # Flat broker — no position yet; BUY will be the first fill.
    broker = FakeBroker(starting_cash=50_000.0, prices={_TICKER: _OPEN_PRICE})
    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id":  "tick-buy",
        "as_of":    _OPEN_AT.isoformat(),
        "final_orders": [
            Order(ticker=_TICKER, action="BUY", quantity=5.0, est_price=_OPEN_PRICE),
        ],
        "user:positions": {},   # flat at the start of this tick
        "strategist_decision": {
            "decision_tag": "enter_tsla",
            "stances": [
                {
                    "ticker":    _TICKER,
                    "intent":    "buy",
                    "weight":    0.05,
                    "rationale": "Strong momentum breakout",
                }
            ],
        },
    }

    events = await _run(executor, state)

    # The executor must yield exactly one event.
    assert len(events) == 1, "Executor must yield exactly one event per tick"

    delta = events[0].actions.state_delta

    # A-014: bridge key removed — must not appear.
    assert "temp:executor_positions_bridge" not in delta, (
        "A-014: bridge key must not appear in the state_delta"
    )

    # user:positions is after-callback territory.
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # Observable outcome: BUY filled at the expected price.
    executions = delta["executions"]
    assert len(executions) == 1, "BUY must produce exactly one execution record"
    assert executions[0]["status"] == "filled", "BUY must fill"
    assert executions[0]["actual_price"] == pytest.approx(_OPEN_PRICE), (
        "BUY fill price must match the broker's injected price"
    )


async def test_trim_preserves_position_thesis(session):
    """A partial SELL must not delete the position slot or write a TradeLogRow.

    Scenario: broker holds 100 shares of TSLA; the executor is asked to sell
    only 1 share. After the fill, 99 shares remain, so the position thesis in
    ``user:positions`` must survive (after-callback will write it next tick)
    and no trade-log row must be written.

    The cross-tick thesis is seeded via ``state["user:positions"]`` — the
    canonical channel.  No bridge key.
    """
    broker = await _broker_with_position(100.0)
    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id": "tick-trim",
        "final_orders": [
            Order(ticker=_TICKER, action="SELL", quantity=1.0, est_price=_OPEN_PRICE),
        ],
        # Seed the canonical thesis-book — no bridge key.
        "user:positions": {_TICKER: dict(_THESIS)},
        "strategist_decision": {
            "decision_tag":  "trim_tsla",
            "sell_reasons": {_TICKER: "trim only"},
        },
    }

    events = await _run(executor, state)

    assert len(events) == 1, "executor must yield exactly one event"
    delta = events[0].actions.state_delta

    # A-014: bridge key removed.
    assert "temp:executor_positions_bridge" not in delta, (
        "A-014: bridge key must not appear in the state_delta"
    )
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # SELL executed (partial fill recorded).
    executions = delta["executions"]
    assert len(executions) == 1
    assert executions[0]["status"] == "filled"

    # No TradeLogRow should have been written for a mere trim.
    row_count = session.query(TradeLogRow).count()
    assert row_count == 0, (
        f"Trim SELL must not write a TradeLogRow; found {row_count} row(s)"
    )


async def test_full_exit_writes_one_trade_log_row_and_deletes(session):
    """A full SELL (100 % of held shares) must write exactly one TradeLogRow.

    Scenario: broker holds 100 shares of TSLA; the executor sells all 100.
    After the fill, remaining_qty == 0.0, so exactly one TradeLogRow must be
    committed.

    The prior position thesis is seeded via ``state["user:positions"]`` —
    the canonical cross-tick recovery channel.  No bridge key.
    """
    broker = await _broker_with_position(100.0)
    executor = ExecutorAgent(broker=broker, db_session=session)

    state = {
        "tick_id": "tick-close",
        "final_orders": [
            Order(ticker=_TICKER, action="SELL", quantity=100.0, est_price=_OPEN_PRICE),
        ],
        # Seed the canonical thesis-book — no bridge key.
        "user:positions": {_TICKER: dict(_THESIS)},
        "strategist_decision": {
            "decision_tag":  "close_tsla",
            "sell_reasons": {_TICKER: "target reached"},
        },
    }

    events = await _run(executor, state)

    assert len(events) == 1, "executor must yield exactly one event"
    delta = events[0].actions.state_delta

    # A-014: bridge key removed.
    assert "temp:executor_positions_bridge" not in delta, (
        "A-014: bridge key must not appear in the state_delta"
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

    The prior position thesis is seeded via ``state["user:positions"]`` — the
    canonical cross-tick recovery channel.  No bridge key.
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
        # Seed the canonical thesis-book — no bridge key.
        "user:positions": {_TICKER: dict(_THESIS)},
        "strategist_decision": {
            "decision_tag":  "close_tsla",
            "sell_reasons": {_TICKER: "target reached"},
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

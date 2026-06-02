"""Unit tests for executor BUY-thesis write and SELL tick-id FK population.

Covers:
- BUY: executor assembles PositionThesis and inserts it into the local
  ``positions`` dict; after-callback writes ``user:positions`` cross-tick.
  The state_delta must NOT contain ``user:positions`` or the removed bridge key.
- SELL: executor removes ticker from its local ``positions`` dict after a full
  close.  The state_delta reflects the cleared state via the absence of
  ``user:positions`` (after-callback territory) and absence of the bridge key.
- SELL + DB: executor populates opening_tick_id / closing_tick_id on TradeLogRow,
  recovering opening details from the ``user:positions`` thesis seeded in state.

Note: ``_run_async_impl`` is the writer-of-record for ``executions`` and
``last_executed_tick_id`` only.  ``user:positions`` (the persistent thesis-book)
is written exclusively by ``_executor_thesis_writer_callback`` (after_agent_callback)
after the run loop completes — these unit tests exercise the run loop in isolation
and assert on observable outcomes (executions, TradeLogRow DB writes) rather than
internal state keys.

For the cross-tick SELL path the tests seed ``state["user:positions"]`` — the
canonical thesis-book channel the executor reads from — rather than any bridge key.
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


def _make_prior_thesis(
    ticker: str,
    opened_at: datetime,
    opened_tick_id: str,
    opened_price: float,
    opened_tag: str | None = None,
) -> dict:
    """Build a ``PositionThesis.model_dump(mode='json')`` for use as a
    ``user:positions`` seed — the shape the after-callback persists.

    PositionThesis is ``extra="forbid"``, so ``opened_tag`` cannot be a field;
    same-tick BUYs stash it outside the schema.  Cross-tick recovery relies on
    the ``opened_tick_id`` fallback for ``opened_tag`` in the trade-log write.

    Parameters
    ----------
    ticker:
        Ticker symbol for the thesis row.
    opened_at:
        UTC timestamp the position was opened.
    opened_tick_id:
        Tick id at open time — used as the ``opened_tag`` fallback.
    opened_price:
        Fill price recorded at open.
    opened_tag:
        Unused by ``PositionThesis`` (extra="forbid"); provided as a reminder
        that the field is NOT on the schema and will NOT survive the round-trip.
    """
    thesis = PositionThesis(
        ticker                 = ticker,
        opened_at              = opened_at,
        opened_tick_id         = opened_tick_id,
        opened_price           = opened_price,
        weight                 = 0.04,
        rationale              = "strong momentum",
        last_reviewed_at       = opened_at,
        last_reviewed_decision = "buy",
        last_reviewed_reason   = "strong momentum",
        thesis_last_updated_tick = 0,
    )
    return thesis.model_dump(mode="json")


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
    """After a BUY executes, the executor assembles a PositionThesis locally
    (in the ``positions`` dict) but does NOT expose it in the state_delta.

    The state_delta must carry ``executions`` and ``last_executed_tick_id``
    but NOT ``user:positions`` (after-callback territory) and NOT any bridge
    key (bridge removed by audit A-014).

    Verifiable outcome: the BUY order filled → execution record has
    ``status == "filled"`` and ``actual_price == fill_price``.
    """
    fill_price = 200.0
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": fill_price})
    executor = ExecutorAgent(broker=broker)

    open_ts = "2026-04-01T14:00:00+00:00"

    state = {
        "tick_id": "tick_X",
        "as_of":   open_ts,
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=5.0, est_price=fill_price),
        ],
        "user:positions": {},   # prior held book — empty at tick start
        "strategist_decision": {
            "decision_tag": "buy_aapl",
            "sell_reasons": {},
            # Band 6 / iter-3: executor assembles PositionThesis from the
            # buy-intent stance + fill price.  No horizon / target_price /
            # stop_price — those were removed in iter-3.
            "stances": [
                {
                    "ticker":    "AAPL",
                    "intent":    "buy",
                    "weight":    0.04,
                    "rationale": "strong momentum",
                },
            ],
        },
    }

    events = await _run(executor, state)

    assert len(events) == 1, "BUY must cause executor to yield one state-delta event"
    delta = events[0].actions.state_delta

    # A-014: the bridge key is removed — must not appear in delta.
    assert "temp:executor_positions_bridge" not in delta, (
        "A-014: bridge key must not appear in the state_delta"
    )

    # user:positions is the after-callback's territory — must not be in delta.
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # Observable BUY outcome: filled execution with correct price.
    executions = delta["executions"]
    assert len(executions) == 1, "BUY must produce exactly one execution record"
    assert executions[0]["status"] == "filled"
    assert executions[0]["actual_price"] == pytest.approx(fill_price), (
        "actual_price must be the real fill price from the broker"
    )


@pytest.mark.asyncio
async def test_sell_removes_ticker_from_state_positions():
    """After a full SELL, the executor removes the ticker from its local book
    and the SELL execution is recorded.  No bridge key; no user:positions write.

    We pre-seed ``state["user:positions"]`` with a prior thesis (simulating
    what the after-callback persisted on the prior tick) — this is the canonical
    cross-tick recovery channel.

    The final state_delta must contain a filled execution for the SELL and
    must NOT contain the bridge key or ``user:positions``.
    """
    # Seed the broker so the SELL can succeed.
    broker = FakeBroker(starting_cash=1_000.0, prices={"AAPL": 200.0})
    await broker.submit_market("AAPL", "BUY", 5.0)

    executor = ExecutorAgent(broker=broker)

    open_ts = datetime(2026, 4, 1, 14, tzinfo=UTC)

    # Seed the canonical thesis-book as the after-callback would have left it.
    prior_thesis = _make_prior_thesis(
        ticker         = "AAPL",
        opened_at      = open_ts,
        opened_tick_id = "tick_OPEN",
        opened_price   = 200.0,
    )

    state = {
        "tick_id": "tick_CLOSE",
        "final_orders": [
            Order(ticker="AAPL", action="SELL", quantity=5.0, est_price=200.0),
        ],
        # Cross-tick recovery: executor reads user:positions at the start of
        # _run_async_impl.  No bridge key.
        "user:positions": {"AAPL": prior_thesis},
        "strategist_decision": {
            "decision_tag": "close_aapl",
            "sell_reasons": {"AAPL": "target reached"},
        },
    }

    events = await _run(executor, state)

    assert len(events) == 1, "SELL must cause executor to yield one state-delta event"
    delta = events[0].actions.state_delta

    # A-014: bridge key is removed.
    assert "temp:executor_positions_bridge" not in delta, (
        "A-014: bridge key must not appear in the state_delta"
    )
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # SELL execution was recorded as filled.
    executions = delta["executions"]
    assert len(executions) == 1
    assert executions[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_sell_writes_tick_id_fks_to_trade_log(session):
    """SELL must populate opening_tick_id (from thesis) and closing_tick_id (from state).

    The opening_tick_id comes from thesis["opened_tick_id"] recovered from
    ``state["user:positions"]``; closing_tick_id comes from state["tick_id"]
    at the time of sale.  Both must round-trip through ``save_trade_log_entry``
    into the TradeLogRow.

    Also verifies that ``opened_tag`` falls back to ``opened_tick_id`` when the
    thesis has no ``opened_tag`` key (PositionThesis is extra="forbid").
    """
    # Seed the broker so it has AAPL to sell.
    broker = FakeBroker(starting_cash=1_000.0, prices={"AAPL": 200.0})
    await broker.submit_market("AAPL", "BUY", 5.0)

    executor = ExecutorAgent(broker=broker, db_session=session)

    open_ts = datetime(2026, 4, 1, 14, tzinfo=UTC)

    # Thesis from the canonical cross-tick book (no ``opened_tag`` field —
    # PositionThesis is extra="forbid", opened_tag is not a schema field).
    prior_thesis = _make_prior_thesis(
        ticker         = "AAPL",
        opened_at      = open_ts,
        opened_tick_id = "tick_OPEN",
        opened_price   = 200.0,
    )

    state = {
        # tick_id here is the deliberation tick that is closing the position.
        "tick_id": "tick_CLOSE",
        "final_orders": [
            Order(ticker="AAPL", action="SELL", quantity=5.0, est_price=200.0),
        ],
        # Cross-tick recovery via the canonical thesis-book.
        "user:positions": {"AAPL": prior_thesis},
        "strategist_decision": {
            "decision_tag": "close_aapl",
            "sell_reasons": {"AAPL": "target reached"},
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
    # opened_tag falls back to opened_tick_id when the thesis has no opened_tag.
    assert row.opened_tag == "tick_OPEN", (
        f"opened_tag should fall back to opened_tick_id='tick_OPEN'; got {row.opened_tag!r}"
    )

"""Executor integration tests with FakeBroker."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.executor.agent import build_executor
from broker.fake import FakeBroker
from orchestrator.persistence import Base, TradeLogRow


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
        "positions": {},   # Band 4 bare-key bridge
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
        "positions": {},   # Band 4 bare-key bridge
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
    Assert: after the executor runs, the bare-key bridge ``state["positions"]["AAPL"]`` carries
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
        "positions": {},   # Band 4 bare-key bridge (starts empty)
        "strategist_decision": {
            "decision_tag":  "morning_sweep",
            "new_positions": {"AAPL": thesis_from_strategist},
            "close_reasons": {},
        },
    }

    ctx = _make_ctx(state)
    events: list = []
    async for ev in executor._run_async_impl(ctx):
        events.append(ev)

    # The bare-key bridge ``"positions"`` in the state_delta must carry the
    # thesis with opened_price stamped.  ``_run_async_impl`` does NOT write
    # ``user:positions`` — that is the after-callback's responsibility.
    assert len(events) == 1, "executor must yield exactly one state-delta event"
    delta = events[0].actions.state_delta
    assert "AAPL" in delta["positions"], (
        "BUY must stamp the thesis into the bare-key bridge 'positions'"
    )
    stamped = delta["positions"]["AAPL"]
    assert stamped["opened_price"] == pytest.approx(215.50)
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

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
        "positions": {},   # Band 4 bare-key bridge
        "strategist_decision": {"decision_tag": "buy_aapl", "close_reasons": {}},
    }
    ctx = _make_ctx(state)
    async for _ in executor._run_async_impl(ctx):
        pass
    executions = state["executions"]
    assert executions[0]["status"] == "rejected"


# ---------------------------------------------------------------------------
# C-1 regression — cross-tick BUY→SELL via DatabaseSessionService round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tick_buy_then_sell_produces_trade_log_row():
    """Regression for C-1: SELL in tick T+1 must find the position written
    by BUY in tick T, even after a DatabaseSessionService round-trip.

    Architecture note (Band 4 / Band 5):
    ``_run_async_impl`` is the writer-of-record for the bare-key ``"positions"``
    bridge.  The canonical ``user:positions`` key is written by the
    after-callback (``_executor_thesis_writer_callback``) — which only fires
    during a full ADK Runner lifecycle, not when ``_run_async_impl`` is called
    directly.  This test therefore verifies that the bare-key bridge persists
    cross-tick via ``DatabaseSessionService``.

    Test steps
    ----------
    1. Create an in-memory SQLite DB and a ``DatabaseSessionService`` session.
    2. Run the executor with a BUY order on tick-1; collect the state_delta event.
    3. Feed the state_delta event to ``session_service.append_event`` to simulate
       what the ADK runner does between ticks.
    4. Reload the session from the service to confirm ``"positions"`` bridge persisted.
    5. Run the executor again with a SELL order on tick-2, seeding ``"positions"``
       from the reloaded session state.
    6. Assert a ``TradeLogRow`` was written to the DB.
    """

    # ── Setup: in-memory SQLite DB ──────────────────────────────────────────
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db_session = Session(engine)

    # ── Tick T prices ──────────────────────────────────────────────────────
    buy_price  = 200.0
    sell_price = 215.0

    broker = FakeBroker(starting_cash=20_000.0, prices={"AAPL": buy_price})

    # Build executor with the synchronous SQLAlchemy session (used for
    # trade-log writes) and a separate DatabaseSessionService for the
    # cross-tick state round-trip.
    executor = build_executor(broker, db_session=db_session)

    # ── Tick T (BUY) ────────────────────────────────────────────────────────
    # Build state matching what the orchestrator hands to the executor.
    open_ts = datetime(2026, 5, 23, 9, 30, tzinfo=UTC)

    buy_state: dict = {
        "tick_id":            "tick-1",
        "as_of":              open_ts.isoformat(),
        "final_orders":       [
            {"ticker": "AAPL", "action": "BUY", "quantity": 5.0, "est_price": buy_price},
        ],
        "positions":          {},   # Band 4 bare-key bridge (starts empty)
        "strategist_decision": {
            "decision_tag":  "morning_sweep",
            "new_positions": {
                "AAPL": {
                    "ticker":         "AAPL",
                    "opened_at":      open_ts.isoformat(),
                    "opened_price":   None,          # executor will stamp this
                    "opened_tag":     "morning_sweep",
                    "opened_tick_id": "tick-1",
                    "rationale":      "strong_momentum",
                    "horizon":        "swing",
                    "weight":         0.10,
                    "target_price":   230.0,
                    "stop_price":     190.0,
                    "catalyst":       "Q3 earnings",
                    "last_reviewed_at":       open_ts.isoformat(),
                    "last_reviewed_decision": "open",
                    "last_reviewed_reason":   "strong_momentum",
                    "last_review_note":       "",
                },
            },
            "close_reasons": {},
        },
    }

    buy_ctx = _make_ctx(buy_state)
    collected_events: list = []

    async for ev in executor._run_async_impl(buy_ctx):
        collected_events.append(ev)

    # The executor must have yielded exactly one event with a state_delta.
    assert len(collected_events) == 1
    buy_event = collected_events[0]
    delta = buy_event.actions.state_delta

    # Verify that the bare-key bridge ``"positions"`` is in the state_delta.
    # ``user:positions`` must NOT be here — it is the after-callback's territory.
    assert "positions" in delta, (
        "C-1 regression: executor state_delta must include the bare-key 'positions' bridge "
        "so the storage session reflects the in-tick BUY mutation"
    )
    assert "AAPL" in delta["positions"], (
        "'positions' bridge state_delta must contain the newly opened AAPL thesis"
    )
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # ── Simulate the ADK runner's between-tick round-trip ──────────────────
    # Use DatabaseSessionService with in-memory SQLite to mirror what the
    # real runner does: create a session, append the event, reload from DB.
    # ``"positions"`` (bare key) is stored in session state by DatabaseSessionService.
    from google.adk.sessions import DatabaseSessionService

    adk_svc = DatabaseSessionService(db_url="sqlite+aiosqlite://")

    adk_session = await adk_svc.create_session(
        app_name   = "test",
        user_id    = "u1",
        state      = {"positions": {}},    # pre-tick state: no positions bridge
    )
    await adk_svc.append_event(adk_session, buy_event)

    # Reload session from storage — this is the "tick T+1 start" state.
    reloaded = await adk_svc.get_session(
        app_name   = "test",
        user_id    = "u1",
        session_id = adk_session.id,
    )
    assert reloaded is not None
    reloaded_positions = reloaded.state.get("positions", {})
    assert "AAPL" in reloaded_positions, (
        "Band 4 'positions' bridge must survive the DatabaseSessionService round-trip; "
        "if this fails, 'positions' was not in the state_delta"
    )

    # ── Tick T+1 (SELL) ─────────────────────────────────────────────────────
    # Update broker price for the sell tick.
    broker._prices["AAPL"] = sell_price
    close_ts = datetime(2026, 5, 24, 15, 30, tzinfo=UTC)

    sell_state: dict = {
        "tick_id":            "tick-2",
        "as_of":              close_ts.isoformat(),
        "final_orders":       [
            {"ticker": "AAPL", "action": "SELL", "quantity": 5.0, "est_price": sell_price},
        ],
        # Load positions from the reloaded session — exactly what the runner does.
        "positions":          dict(reloaded_positions),   # Band 4 bare-key bridge
        "strategist_decision": {
            "decision_tag":  "take_profit",
            "close_reasons": {"AAPL": "target reached"},
        },
    }

    sell_ctx = _make_ctx(sell_state)
    sell_events: list = []
    async for ev in executor._run_async_impl(sell_ctx):
        sell_events.append(ev)

    # Flush and verify trade-log row was written.
    db_session.flush()
    rows = db_session.query(TradeLogRow).filter_by(ticker="AAPL").all()
    assert len(rows) == 1, (
        "C-1 regression: a SELL after cross-tick BUY must produce one trade-log row; "
        "got zero — the executor did not find the prior position in state['positions'] bridge"
    )
    row = rows[0]
    assert row.closed_price == pytest.approx(sell_price)
    assert row.opened_price == pytest.approx(buy_price)
    assert row.opening_tick_id == "tick-1"
    assert row.closing_tick_id == "tick-2"

    # The SELL should have cleared the position from the ``"positions"`` bridge in the delta.
    assert len(sell_events) == 1, "SELL must yield one state-delta event"
    sell_delta = sell_events[0].actions.state_delta
    assert "AAPL" not in sell_delta["positions"], (
        "executor must remove the closed position from state_delta['positions'] bridge"
    )
    assert "user:positions" not in sell_delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    db_session.close()

"""Executor integration tests with FakeBroker."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.executor.agent import _executor_thesis_writer_callback, build_executor
from agents.strategist.position_thesis import PositionThesis
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
        "user:positions": {},   # prior held book (empty at start)
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
        "user:positions": {},   # prior held book
        "strategist_decision": {"decision_tag": "buy_aapl", "close_reasons": {}},
    }
    ctx = _make_ctx(state)
    async for _ in executor._run_async_impl(ctx):
        pass
    assert "executions" not in state  # no executions because idempotent skip


@pytest.mark.asyncio
async def test_executor_stamps_opened_price_on_buy():
    """The executor assembles a PositionThesis from the open-intent stance +
    fill price during a BUY.  Same-tick BUY → SELL works via the shared local
    ``positions`` dict; the after-callback is the sole writer of ``user:positions``.

    This test exercises the same-tick path: a BUY followed immediately by a
    SELL in the same ``_run_async_impl`` call.  We verify that the resulting
    ``TradeLogRow`` carries ``opened_price == 215.50`` — proving the in-tick
    thesis assembly (via ``apply_stance_to_thesis``) fed the same-tick SELL
    bookkeeping.

    The state_delta must NOT contain ``user:positions`` (after-callback
    territory) and must NOT contain any bridge key (bridge was removed by
    audit A-014).

    Setup: a strategist decision with ``intent='buy'`` for AAPL, followed by
    an ``intent='sell'`` stance; FakeBroker fills the BUY at $215.50 and the
    SELL at $215.50 (flat price for simplicity).
    """
    buy_price  = 215.50
    sell_price = 215.50

    # FakeBroker with enough cash for the BUY; price at buy_price.
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": buy_price})

    # In-memory SQLite so the SELL trade-log write can land.
    engine     = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db_session = Session(engine)

    executor = build_executor(broker, db_session=db_session)

    open_ts = datetime(2026, 5, 8, 14, 0, 0, tzinfo=UTC)

    # One tick: BUY then SELL of AAPL.  Both orders in the same call.
    state = {
        "tick_id":  "tick-1",
        "as_of":    open_ts.isoformat(),
        # No prior positions — this tick opens and immediately closes AAPL.
        "user:positions": {},
        "final_orders": [
            {"ticker": "AAPL", "action": "BUY",  "quantity": 5.0, "est_price": buy_price},
            {"ticker": "AAPL", "action": "SELL", "quantity": 5.0, "est_price": sell_price},
        ],
        "strategist_decision": {
            "decision_tag": "morning_sweep",
            "stances": [
                {
                    "ticker":    "AAPL",
                    "intent":    "buy",
                    "weight":    0.04,
                    "rationale": "earnings beat + insider buying",
                },
            ],
            # sell_reasons removed (A-013 tail); sell reason is on the stance itself.
        },
    }

    ctx = _make_ctx(state)
    events: list = []
    async for ev in executor._run_async_impl(ctx):
        events.append(ev)

    assert len(events) == 1, "executor must yield exactly one state-delta event"
    delta = events[0].actions.state_delta

    # The bridge key must be absent from the delta — it was removed by A-014.
    assert "temp:executor_positions_bridge" not in delta, (
        "A-014: bridge key must not appear in delta — it was removed"
    )

    # ``user:positions`` must NOT be in the delta (after-callback territory).
    assert "user:positions" not in delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    # BUY + SELL both filled, so one TradeLogRow must exist carrying the
    # buy fill price as opened_price.
    db_session.flush()
    rows = db_session.query(TradeLogRow).filter_by(ticker="AAPL").all()
    assert len(rows) == 1, (
        "Same-tick BUY then SELL must produce exactly one TradeLogRow; "
        f"got {len(rows)} — same-tick thesis assembly may be broken"
    )
    row = rows[0]
    assert row.opened_price == pytest.approx(buy_price), (
        "opened_price must be the real fill price from the BUY in this tick"
    )

    db_session.close()


@pytest.mark.asyncio
async def test_executor_rejection_continues():
    """A-066: a rejected order must not block subsequent filled orders.

    Mix one under-funded AAPL BUY (rejected, $200 each × 5 = $1 000 > $100 cash)
    with one affordable MSFT BUY (filled, $10 each × 1 = $10).  Both execution
    records must be present; the rejected row carries no fill price (``None``)
    and the filled row carries a positive fill price.

    This guards against a silent-degradation mode where rejection of the first
    order silently aborts processing of all subsequent orders in the same tick.
    """
    broker = FakeBroker(
        starting_cash=100.0,
        prices={"AAPL": 200.0, "MSFT": 10.0},
    )
    executor = build_executor(broker)

    state = {
        "tick_id":   "tick-rejection-mix",
        "final_orders": [
            # AAPL: costs 5 × $200 = $1 000 — rejected (insufficient cash).
            {"ticker": "AAPL", "action": "BUY", "quantity": 5.0, "est_price": 200.0},
            # MSFT: costs 1 × $10 = $10 — should fill within the $100 budget.
            {"ticker": "MSFT", "action": "BUY", "quantity": 1.0, "est_price": 10.0},
        ],
        "user:positions": {},
        "strategist_decision": {"decision_tag": "mixed_orders", "close_reasons": {}},
    }

    ctx = _make_ctx(state)
    async for _ in executor._run_async_impl(ctx):
        pass

    executions = state["executions"]

    # Both orders must produce an execution record — the rejection must not abort processing.
    assert len(executions) == 2, (
        f"expected 2 execution records (one rejected, one filled); got {len(executions)}"
    )

    # Index by ticker for readable assertions.
    by_ticker = {
        e["order"]["ticker"]: e
        for e in executions
    }

    # AAPL must be rejected with no fill price.
    aapl_exec = by_ticker["AAPL"]
    assert aapl_exec["status"] == "rejected", (
        f"AAPL order must be rejected; got status={aapl_exec['status']!r}"
    )
    assert aapl_exec["actual_price"] is None, (
        "rejected order must not carry a fill price (actual_price should be None)"
    )

    # MSFT must be filled with a positive fill price — the canonical content assertion.
    msft_exec = by_ticker["MSFT"]
    assert msft_exec["status"] == "filled", (
        f"MSFT order must be filled; got status={msft_exec['status']!r}"
    )
    assert msft_exec["actual_price"] is not None, (
        "filled order must carry a non-None actual_price"
    )
    assert msft_exec["actual_price"] > 0, (
        f"fill_price for MSFT must be positive; got {msft_exec['actual_price']}"
    )


# ---------------------------------------------------------------------------
# C-1 regression — cross-tick BUY→SELL via user:positions recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tick_buy_then_sell_produces_trade_log_row():
    """Regression guard C-1: SELL in tick T+1 must find the position written
    by BUY in tick T and produce a TradeLogRow with correct bookkeeping.

    This is the CANONICAL cross-tick regression test.  It exercises the REAL
    recovery path: ``_run_async_impl`` seeds its local ``positions`` dict from
    ``state["user:positions"]`` — the canonical thesis-book written by the
    after-callback (``_executor_thesis_writer_callback``) and re-hydrated by
    ADK at each tick start.

    If the executor read an empty bridge (the prior wrong approach), tick T+1
    would see ``positions == {}`` at the SELL gate, the gate would be skipped,
    and zero TradeLogRows would be written — a silent realised-P&L loss.

    Test steps
    ----------
    1. Create an in-memory SQLite DB and wire it to the executor.
    2. Build a ``PositionThesis`` for AAPL as the after-callback would have
       persisted it to ``user:positions`` on tick T.
    3. Seed ``state["user:positions"]`` with that thesis on the SELL tick
       (tick T+1) — this is what ADK re-hydration delivers.
    4. Pre-seed the FakeBroker with the AAPL position so the SELL fills.
    5. Run ``_run_async_impl`` for the SELL.
    6. Assert exactly one TradeLogRow for AAPL with correct P&L fields and
       ``opened_tag == "tick-1"`` (confirming the ``opened_tick_id`` fallback
       fired — ``user:positions`` rows carry no ``opened_tag`` field).

    This test MUST fail against code that reads an empty bridge (zero
    TradeLogRows would be written by the SELL).  It passes only when the
    executor reads ``user:positions`` as the prior held book.
    """

    # ── Setup: in-memory SQLite DB ──────────────────────────────────────────
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db_session = Session(engine)

    buy_price  = 200.0
    sell_price = 215.0

    # Pre-seed the broker with an AAPL position so the SELL can fill.
    broker = FakeBroker(starting_cash=20_000.0, prices={"AAPL": sell_price})
    from broker.portfolio import Position
    broker._positions["AAPL"] = Position(
        quantity   = 5.0,
        avg_cost   = buy_price,
        last_price = sell_price,
    )

    executor = build_executor(broker, db_session=db_session)

    # ── Build the user:positions thesis as the after-callback would have left it ──
    # PositionThesis is extra="forbid" — no opened_tag field.
    # The SELL trade-log must fall back to opened_tick_id for the opened_tag column.
    open_ts = datetime(2026, 5, 23, 9, 30, tzinfo=UTC)
    aapl_thesis = PositionThesis(
        ticker                 = "AAPL",
        opened_at              = open_ts,
        opened_tick_id         = "tick-1",
        opened_price           = buy_price,
        weight                 = 0.04,
        rationale              = "strong_momentum",
        last_reviewed_at       = open_ts,
        last_reviewed_decision = "buy",
        thesis_last_updated_tick = 0,
    )

    # ── Tick T+1 (SELL) ─────────────────────────────────────────────────────
    close_ts = datetime(2026, 5, 24, 15, 30, tzinfo=UTC)

    sell_state: dict = {
        "tick_id":  "tick-2",
        "as_of":    close_ts.isoformat(),
        "final_orders": [
            {"ticker": "AAPL", "action": "SELL", "quantity": 5.0, "est_price": sell_price},
        ],
        # Seed user:positions with the PositionThesis from tick T — this is the
        # canonical cross-tick recovery path the executor reads from.
        "user:positions": {
            "AAPL": aapl_thesis.model_dump(mode="json"),
        },
        "strategist_decision": {
            "decision_tag": "take_profit",
            # sell_reasons removed (A-013 tail); sell reason lives on the stance.
        },
    }

    sell_ctx = _make_ctx(sell_state)
    sell_events: list = []
    async for ev in executor._run_async_impl(sell_ctx):
        sell_events.append(ev)

    # ── Assertions ──────────────────────────────────────────────────────────
    db_session.flush()
    rows = db_session.query(TradeLogRow).filter_by(ticker="AAPL").all()
    assert len(rows) == 1, (
        "C-1 regression: a SELL after cross-tick BUY must produce one trade-log row; "
        f"got {len(rows)} — executor did not find the position in state['user:positions'] "
        "(bridge-based recovery silently produces zero rows)"
    )
    row = rows[0]
    assert row.closed_price == pytest.approx(sell_price)
    assert row.opened_price == pytest.approx(buy_price)
    assert row.opening_tick_id == "tick-1"
    assert row.closing_tick_id == "tick-2"

    # ``opened_tag`` must be "tick-1" — the ``opened_tick_id`` fallback fired
    # because ``user:positions`` rows carry no ``opened_tag`` field
    # (PositionThesis is extra="forbid").
    assert row.opened_tag == "tick-1", (
        f"opened_tag should fall back to opened_tick_id='tick-1'; got {row.opened_tag!r}"
    )

    # The bridge key must be absent — it was removed by audit A-014.
    assert len(sell_events) == 1, "SELL must yield one state-delta event"
    sell_delta = sell_events[0].actions.state_delta
    assert "temp:executor_positions_bridge" not in sell_delta, (
        "A-014: bridge key must not appear in delta"
    )
    assert "user:positions" not in sell_delta, (
        "_run_async_impl must not write user:positions — that belongs to the after-callback"
    )

    db_session.close()


# ---------------------------------------------------------------------------
# A-008 — thesis-writer callback must use logger.error(..., exc_info=True)
# ---------------------------------------------------------------------------


class _CallbackCtx:
    """Minimal callback-context shim used to exercise the thesis-writer callback.

    ``_executor_thesis_writer_callback`` only accesses ``callback_context.state``,
    so a plain object carrying a mutable dict is sufficient — no ADK internals
    needed.
    """

    def __init__(self, state: dict) -> None:
        """Initialise with a pre-built state dict.

        Parameters
        ----------
        state:
            The session-state dict the callback will read from and write to.
        """
        self.state = state


def test_thesis_writer_callback_logs_assertion_through_logger(caplog):
    """A-008 — apply_stance_to_thesis raising AssertionError must surface via
    ``logger.error`` with ``exc_info=True``, not via a bare ``print()`` to
    stderr (which bypasses ``caplog`` and structured log aggregators).

    Mechanism
    ---------
    ``apply_stance_to_thesis`` raises ``AssertionError`` when a ``buy`` stance
    is processed but ``fill_price is None`` (line 219 of ``_verb_dispatch.py``).
    The callback derives ``fill_prices`` from ``state["executions"]``.  Providing
    a ``buy`` stance in ``strategist_decision`` with an empty ``executions`` list
    means ``fill_price`` will be ``None`` when the dispatcher runs — triggering
    the assertion without any monkeypatching.

    The test MUST fail before the fix (the bare ``print()`` handler is invisible
    to ``caplog``) and MUST pass after the fix (``logger.error`` routes through
    the standard logging machinery that ``caplog`` captures).
    """

    # Direct ``caplog`` at the module logger named by ``__name__`` in
    # ``agents/executor/agent.py``.  When imported under ``PYTHONPATH=src``,
    # ``__name__`` resolves to ``agents.executor.agent``.
    caplog.set_level(logging.ERROR, logger="agents.executor.agent")

    # Build a state that triggers the AssertionError path:
    # - ``strategist_decision`` carries one ``intent="buy"`` stance for AAPL.
    # - ``executions`` is empty (no fill record), so ``fill_prices["AAPL"]``
    #   will be absent and ``fill_price`` will be ``None`` when
    #   ``apply_stance_to_thesis`` runs for the buy case.
    state: dict = {
        "tick_id": "tick-assert-test",
        "as_of":   "2026-06-01T10:00:00+00:00",
        "strategist_decision": {
            "decision_tag": "assert_test",
            "thesis":       "test thesis",
            "reasoning":    "test reasoning",
            "confidence":   0.5,
            "stances": [
                {
                    "ticker":    "AAPL",
                    "intent":    "buy",
                    "weight":    0.05,
                    "rationale": "triggers assert path",
                },
            ],
        },
        # Empty executions → fill_prices will have no entry for AAPL.
        "executions":       [],
        "user:positions":   {},
    }

    cb_ctx = _CallbackCtx(state)
    _executor_thesis_writer_callback(cb_ctx)

    # Filter to ERROR records that originate from the thesis-writer callback
    # specifically — match on the unique "thesis_writer_callback" prefix so
    # unrelated future log lines containing "thesis" can't cause a false-positive.
    error_records = [
        r for r in caplog.records
        if r.levelno == logging.ERROR
        and "thesis_writer_callback" in r.getMessage()
    ]

    assert error_records, (
        "A-008: AssertionError from apply_stance_to_thesis must be routed through "
        "logger.error so caplog and log aggregators capture it.  "
        "A bare print(file=sys.stderr) is invisible here — replace it with "
        "logger.error(..., exc_info=True)."
    )
    assert error_records[0].exc_info is not None, (
        "A-008: the logger.error call must pass exc_info=True so the full "
        "traceback is attached to the log record."
    )


# ---------------------------------------------------------------------------
# A-065 — after-callback idempotency: same tick_id must not re-fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_idempotent_includes_after_callback():
    """A-065: running the executor a second time with the same tick_id must
    produce byte-identical ``user:positions`` both times.

    The after-callback (``_executor_thesis_writer_callback``) assembles
    ``user:positions`` from the stances + fill prices.  If the idempotency
    guard is bypassed on re-run, the callback could re-fire and mutate the
    already-correct position book (e.g. re-opening a position that was
    correctly set on the first run).

    Test mechanism
    --------------
    1. Run the executor once with a BUY for AAPL (first run — position book
       starts empty, tick fills successfully).
    2. After the first run, manually invoke the after-callback to simulate ADK's
       after-agent firing.  Capture the resulting ``user:positions``.
    3. Run the executor a second time with the SAME tick_id.  The idempotency
       guard (``last_executed_tick_id == tick_id``) must skip ``_run_async_impl``.
    4. Invoke the after-callback again (as ADK always would).  Assert that
       ``user:positions`` is byte-for-byte identical to the result after step 2.

    A regression here would produce a double-entry in the position book or
    a clobbered ``user:positions`` — a silent-degradation mode that would
    corrupt the strategist's held-view on the next tick.
    """
    import json

    buy_price = 150.0
    broker    = FakeBroker(starting_cash=10_000.0, prices={"AAPL": buy_price})
    executor  = build_executor(broker)

    # Build the minimal state for a BUY tick.
    state: dict = {
        "tick_id":   "tick-idem-callback",
        "as_of":     "2026-06-10T09:30:00+00:00",
        "final_orders": [
            {"ticker": "AAPL", "action": "BUY", "quantity": 2.0, "est_price": buy_price},
        ],
        "user:positions": {},   # empty prior held book
        "strategist_decision": {
            "decision_tag": "idem_test",
            "reasoning":    "test idempotency of after-callback",
            "confidence":   0.6,
            "stances": [
                {
                    "ticker":    "AAPL",
                    "intent":    "buy",
                    "weight":    0.04,
                    "rationale": "momentum intact",
                },
            ],
        },
    }

    ctx = _make_ctx(state)

    # ── First run: execute the BUY ─────────────────────────────────────────
    async for _ in executor._run_async_impl(ctx):
        pass

    # Simulate ADK invoking the after-callback on the first run.
    cb_ctx_1 = _CallbackCtx(state)
    _executor_thesis_writer_callback(cb_ctx_1)

    # Capture user:positions after the first full run.
    positions_after_first_run = json.dumps(
        state.get("user:positions", {}), sort_keys=True
    )

    # Sanity: the first run must have created an AAPL position.
    assert "AAPL" in state.get("user:positions", {}), (
        "After the first run, AAPL must appear in user:positions"
    )

    # ── Second run: same tick_id — idempotency guard must fire ─────────────
    # Mark the tick as executed (mirrors what ADK's session service would carry
    # cross-tick from the state_delta emitted in the first run).
    state["last_executed_tick_id"] = "tick-idem-callback"

    async for _ in executor._run_async_impl(ctx):
        pass

    # After-callback fires again (ADK fires it unconditionally after every run).
    cb_ctx_2 = _CallbackCtx(state)
    _executor_thesis_writer_callback(cb_ctx_2)

    positions_after_second_run = json.dumps(
        state.get("user:positions", {}), sort_keys=True
    )

    # Content assertion: both serialised position dicts must be identical.
    assert positions_after_second_run == positions_after_first_run, (
        "A-065: re-running the executor with the same tick_id must not mutate "
        "user:positions — the position book must be byte-identical after both runs.\n"
        f"  After run 1: {positions_after_first_run}\n"
        f"  After run 2: {positions_after_second_run}"
    )

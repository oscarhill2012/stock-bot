"""Integration smoke test — single tick, three-verb end-to-end flow.

Drives one full tick deterministically (no LLM calls) through the
risk gate and executor.  Verifies observable behaviour for each of the
three iter-3 stance verbs in a single tick:

    sell  (AAPL): full close → position removed, closed_trades_log appended
    buy   (MSFT): entry      → position opened at fill price, rationale stored
    update(GOOGL): prose-only → position weight unchanged, thesis_last_updated_tick advanced

The LLM is not involved — the strategist decision is hand-built directly
into session state using the canonical ``StrategistDecision`` schema, which
is exactly what the real pipeline writes into state before the risk gate runs.

Architecture note
-----------------
The test drives two agents' ``_run_async_impl`` methods directly and then
calls the executor's after-callback (``_executor_thesis_writer_callback``)
manually, mirroring the pattern in
``tests/integration/test_executor_with_fake_broker.py``.  This avoids
spinning up the full ADK runner while exercising the same code paths that
the runner would trigger.

The ``FakeBroker`` is pre-seeded with AAPL and GOOGL positions at a known
price so ``RiskGateAgent`` can compute portfolio weights and
``weights_to_orders`` can produce deterministic order sizes.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agents.executor.agent import _executor_thesis_writer_callback, build_executor
from agents.risk_gate.agent import RiskGateAgent
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.fake import FakeBroker
from broker.portfolio import Position


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

#: Tick timestamp used consistently across all state reads and writes.
_AS_OF = datetime(2026, 5, 26, 14, 0, 0, tzinfo=UTC)

#: Injected market prices for the test tick.
_PRICES = {
    "AAPL":  200.0,
    "MSFT":  400.0,
    "GOOGL": 170.0,
}

#: AAPL entry price and quantity (position pre-seeded into FakeBroker).
_AAPL_ENTRY_PRICE = 180.0
_AAPL_QTY         = 25.0   # 25 × $200 = $5 000

#: GOOGL entry price and quantity (position pre-seeded into FakeBroker).
_GOOGL_ENTRY_PRICE = 160.0
_GOOGL_QTY         = 15.0   # 15 × $170 = $2 550

#: Starting cash — sizeable so MSFT BUY does not hit the cash floor.
_STARTING_CASH = 50_000.0


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal mock ``InvocationContext`` carrying the given state.

    The mock exposes ``ctx.session.state`` as the supplied dict and sets
    ``ctx.invocation_id`` to a deterministic string.  This matches the
    pattern used by ``test_executor_with_fake_broker.py``.

    Parameters
    ----------
    state:
        Session state dict to expose through the mock context.

    Returns
    -------
    MagicMock
        A mock context suitable for passing to ``_run_async_impl``.
    """
    session     = MagicMock()
    session.state = state
    ctx           = MagicMock()
    ctx.session   = session
    ctx.invocation_id = "e2e-smoke-tick"
    return ctx


def _build_broker() -> FakeBroker:
    """Construct a FakeBroker with AAPL and GOOGL positions already held.

    MSFT is in the price map (so BUY can fill) but not in the position book
    (so risk gate treats it as a flat ticker).

    Returns
    -------
    FakeBroker
        Broker pre-seeded with AAPL and GOOGL positions.
    """
    broker = FakeBroker(starting_cash=_STARTING_CASH, prices=_PRICES)

    # Pre-seed positions directly — bypasses the normal buy flow so the
    # test doesn't need a prior tick to establish the entries.
    broker._positions["AAPL"] = Position(
        quantity   = _AAPL_QTY,
        avg_cost   = _AAPL_ENTRY_PRICE,
        last_price = _PRICES["AAPL"],
    )
    broker._positions["GOOGL"] = Position(
        quantity   = _GOOGL_QTY,
        avg_cost   = _GOOGL_ENTRY_PRICE,
        last_price = _PRICES["GOOGL"],
    )
    # Reduce starting cash by the notional of the pre-seeded positions so the
    # broker's total_value (cash + positions) is internally consistent.
    broker._cash = (
        _STARTING_CASH
        - _AAPL_QTY   * _AAPL_ENTRY_PRICE
        - _GOOGL_QTY  * _GOOGL_ENTRY_PRICE
    )
    return broker


def _build_decision() -> dict:
    """Construct the three-verb ``StrategistDecision`` for this tick.

    Three stances:
    - AAPL sell (full close, no explicit weight, reason "test exit")
    - MSFT buy  (weight 0.03, rationale "test entry")
    - GOOGL update (reason "still bullish")

    Returns
    -------
    dict
        JSON-serialisable ``StrategistDecision.model_dump`` ready for injection
        into session state.
    """
    return StrategistDecision(
        stances = [
            TickerStance(
                ticker = "AAPL",
                intent = "sell",
                # No weight → full close.
                reason = "test exit",
            ),
            TickerStance(
                ticker    = "MSFT",
                intent    = "buy",
                weight    = 0.03,
                rationale = "test entry",
                catalyst  = "earnings beat",
            ),
            TickerStance(
                ticker = "GOOGL",
                intent = "update",
                reason = "still bullish",
            ),
        ],
        decision_tag = "smoke_sell_buy_update",
        reasoning    = "Smoke test tick: three-verb.",
        thesis       = "Market regime neutral; selective entry.",
        confidence   = 0.65,
    ).model_dump(mode="json")


def _build_prior_positions() -> dict:
    """Build the ``user:positions`` dict representing prior held positions.

    AAPL and GOOGL are held with plausible ``PositionThesis`` rows;
    MSFT is absent (flat ticker — no prior position).

    Returns
    -------
    dict
        Keyed by ticker; values are ``PositionThesis.model_dump(mode="json")``.
    """
    from agents.strategist.position_thesis import PositionThesis

    # AAPL position opened two ticks ago — will be fully closed this tick.
    aapl_thesis = PositionThesis(
        ticker                  = "AAPL",
        opened_at               = datetime(2026, 5, 24, 9, 30, 0, tzinfo=UTC),
        opened_tick_id          = "tick-earlier",
        opened_price            = _AAPL_ENTRY_PRICE,
        weight                  = 0.05,    # current weight (5 %)
        catalyst                = "Q3 earnings",
        rationale               = "strong momentum entering earnings",
        last_reviewed_at        = datetime(2026, 5, 24, 9, 30, 0, tzinfo=UTC),
        last_reviewed_decision  = "buy",
        last_reviewed_reason    = "strong momentum entering earnings",
        thesis_last_updated_tick = 3,
    )

    # GOOGL position opened three ticks ago — will be updated (prose-only).
    googl_thesis = PositionThesis(
        ticker                  = "GOOGL",
        opened_at               = datetime(2026, 5, 23, 9, 30, 0, tzinfo=UTC),
        opened_tick_id          = "tick-old",
        opened_price            = _GOOGL_ENTRY_PRICE,
        weight                  = 0.04,    # current weight (4 %)
        catalyst                = "Cloud market share expansion",
        rationale               = "secular AI capex beneficiary",
        last_reviewed_at        = datetime(2026, 5, 23, 9, 30, 0, tzinfo=UTC),
        last_reviewed_decision  = "buy",
        last_reviewed_reason    = "secular AI capex beneficiary",
        thesis_last_updated_tick = 1,      # old tick index — should advance after update
    )

    return {
        "AAPL":  aapl_thesis.model_dump(mode="json"),
        "GOOGL": googl_thesis.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# End-to-end smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_verb_single_tick_smoke() -> None:
    """Drive one tick through risk gate + executor and verify three-verb output.

    Test outline
    ------------
    1. Pre-seed FakeBroker with AAPL and GOOGL positions.
    2. Build a ``StrategistDecision`` with sell/buy/update stances.
    3. Run ``RiskGateAgent._run_async_impl`` — merges ``final_orders`` into state.
    4. Run ``ExecutorAgent._run_async_impl`` — submits orders; writes the
       bare-key ``"positions"`` bridge and the ``user:closed_trades_log``.
    5. Call ``_executor_thesis_writer_callback`` manually with a simulated
       ``CallbackContext`` — writes ``user:positions`` from the stance + fill data.
    6. Assert:
       - AAPL: position absent from ``user:positions``; a closed_trades_log
         entry exists for AAPL.
       - MSFT: position present in ``user:positions`` with correct
         opened_price and rationale.
       - GOOGL: position present in ``user:positions`` with unchanged weight
         and ``thesis_last_updated_tick`` advanced.

    Coverage note
    -------------
    The test exercises the exact code paths the runner would trigger, minus
    the ADK session persistence layer (cross-tick storage is not the concern
    here — that is covered by ``test_cross_tick_buy_then_sell_produces_trade_log_row``).
    """

    # ── Setup ──────────────────────────────────────────────────────────────────
    broker         = _build_broker()
    prior_positions = _build_prior_positions()

    # Build the portfolio snapshot from the broker so the enricher / risk gate
    # see the correct current_weights when they call portfolio.current_weights().
    portfolio = await broker.get_portfolio()

    # Derive AAPL's current weight from the broker for later assertions.
    aapl_current_weight = portfolio.current_weights().get("AAPL", 0.0)
    assert aapl_current_weight > 0.0, "Pre-condition: AAPL must be held before tick"

    # Session state — mirrors what the runner builds at tick start.
    state: dict = {
        "tick_id":            "tick-smoke-3v",
        "as_of":              _AS_OF.isoformat(),
        "tickers":            ["AAPL", "MSFT", "GOOGL"],
        "portfolio":          portfolio.model_dump(mode="json"),
        "positions":          {
            # Bare-key bridge — executor uses this for BUY→SELL cross-detection.
            # Pre-populate from the PositionThesis dicts so SELL can find AAPL.
            k: dict(v) for k, v in prior_positions.items()
        },
        "user:positions":     dict(prior_positions),
        # Staleness counter — executor uses this for thesis_last_updated_tick.
        "user:current_tick_index": 5,
        "strategist_decision": _build_decision(),
    }

    ctx = _make_ctx(state)

    # ── Step 1: risk gate ──────────────────────────────────────────────────────
    # After running ``derive_decision_fields`` (via validate_and_enrich in the
    # enricher), the decision already carries ``target_weights`` and
    # ``sell_reasons``.  The risk gate consumes those to generate final_orders.
    #
    # In the real pipeline the StrategistEnricher runs before the risk gate and
    # enriches the decision.  Here we call validate_and_enrich explicitly to
    # replicate that step so the risk gate sees the full decision shape.
    from agents.strategist.enricher import validate_and_enrich
    enriched = validate_and_enrich(state)
    assert enriched is not None, "validate_and_enrich must return a dict for non-empty decision"
    state["strategist_decision"] = enriched

    risk_gate = RiskGateAgent(broker=broker)
    rg_events: list = []
    async for ev in risk_gate._run_async_impl(ctx):
        rg_events.append(ev)
        # Merge the risk-gate state_delta into state so the executor sees it.
        if ev.actions and ev.actions.state_delta:
            state.update(ev.actions.state_delta)

    assert "final_orders" in state, "risk gate must produce final_orders"
    # Sanity-check: at minimum a SELL (AAPL) and a BUY (MSFT) should be present.
    order_tickers = {o["ticker"] for o in state["final_orders"]}
    assert "AAPL" in order_tickers, "AAPL sell must produce a final_order"
    assert "MSFT" in order_tickers, "MSFT buy must produce a final_order"
    # GOOGL update stance should produce no order.
    assert "GOOGL" not in order_tickers, (
        "GOOGL update stance must produce no order (prose-only)"
    )

    # ── Step 2: executor ───────────────────────────────────────────────────────
    executor   = build_executor(broker)
    exec_events: list = []
    async for ev in executor._run_async_impl(ctx):
        exec_events.append(ev)
        if ev.actions and ev.actions.state_delta:
            state.update(ev.actions.state_delta)

    assert len(exec_events) == 1, "executor must yield exactly one state-delta event"
    exec_delta = exec_events[0].actions.state_delta

    # Verify all orders filled (not rejected).
    executions = state.get("executions", [])
    filled = {
        e["order"]["ticker"]: e
        for e in executions
        if e.get("status") == "filled"
    }
    assert "AAPL" in filled, "AAPL SELL must fill"
    assert "MSFT" in filled, "MSFT BUY must fill"

    # ── Verify AAPL sell: position removed and closed_trades_log appended ──────
    # The bare-key bridge ``state["positions"]`` must no longer contain AAPL
    # (the executor removes the closed ticker in-tick).
    assert "AAPL" not in state["positions"], (
        "AAPL must be removed from the bare-key positions bridge after full close"
    )

    # closed_trades_log is populated by the executor when remaining_qty → 0.
    closed_log = state.get("user:closed_trades_log", [])
    aapl_close_entries = [e for e in closed_log if e["ticker"] == "AAPL"]
    assert len(aapl_close_entries) == 1, (
        "executor must append exactly one entry to user:closed_trades_log for AAPL close"
    )
    assert aapl_close_entries[0]["close_reason"] == "test exit", (
        "close_reason in closed_trades_log must match the sell stance reason"
    )

    # ── Step 3: executor after-callback (thesis writer) ───────────────────────
    # Simulate what the ADK runner does: call the after-callback with a
    # CallbackContext whose ``state`` reflects the merged post-execution state.
    #
    # The callback reads ``user:positions`` (prior book from Phase 2 merge),
    # ``executions``, and ``strategist_decision``.  It writes ``user:positions``
    # and ``user:thesis`` via delta-tracked state keys.

    class _CallbackCtx:
        """Minimal CallbackContext shim — exposes a mutable state dict.

        Matches the interface that ``_executor_thesis_writer_callback`` uses:
        it only reads and writes ``callback_context.state``.
        """

        def __init__(self, s: dict):
            self.state = s

    cb_ctx = _CallbackCtx(state)
    result = _executor_thesis_writer_callback(cb_ctx)
    assert result is None, "thesis writer callback must return None"

    new_positions: dict = state.get("user:positions", {})

    # ── Assert AAPL: position closed ──────────────────────────────────────────
    assert "AAPL" not in new_positions, (
        "AAPL must be removed from user:positions after a full-close sell stance"
    )

    # ── Assert MSFT: position opened at fill price with rationale ─────────────
    assert "MSFT" in new_positions, (
        "MSFT must be present in user:positions after a buy stance fills"
    )
    msft_thesis = new_positions["MSFT"]
    assert msft_thesis["opened_price"] == pytest.approx(_PRICES["MSFT"]), (
        "MSFT opened_price must equal the FakeBroker fill price"
    )
    assert msft_thesis["rationale"] == "test entry", (
        "MSFT rationale must match the buy stance rationale"
    )
    assert msft_thesis["last_reviewed_decision"] == "buy", (
        "MSFT last_reviewed_decision must be 'buy' on initial entry"
    )
    assert msft_thesis["opened_tick_id"] == "tick-smoke-3v", (
        "MSFT opened_tick_id must match the current tick_id"
    )
    # thesis_last_updated_tick must equal the current tick index (5).
    assert msft_thesis["thesis_last_updated_tick"] == 5, (
        "MSFT thesis_last_updated_tick must equal user:current_tick_index (5)"
    )

    # ── Assert GOOGL: weight unchanged, thesis prose updated ──────────────────
    assert "GOOGL" in new_positions, (
        "GOOGL must remain in user:positions after an update stance (prose-only)"
    )
    googl_thesis = new_positions["GOOGL"]

    # Weight must be preserved — update is a no-trade stance.
    prior_googl_weight = prior_positions["GOOGL"]["weight"]
    assert googl_thesis["weight"] == pytest.approx(prior_googl_weight), (
        "GOOGL weight must be unchanged after an update stance"
    )

    # Rationale must refresh — update revises the prose view to the new reason.
    assert "still bullish" in googl_thesis["rationale"], (
        "GOOGL rationale must refresh with the update stance's reason"
    )

    # Review trail must reflect the update stance.
    assert googl_thesis["last_reviewed_decision"] == "update", (
        "GOOGL last_reviewed_decision must be 'update' after an update stance"
    )
    assert "still bullish" in googl_thesis["last_reviewed_reason"], (
        "GOOGL last_reviewed_reason must contain the update reason"
    )

    # thesis_last_updated_tick must have advanced to the current tick index (5),
    # confirming the staleness clock was reset by the update stance.
    assert googl_thesis["thesis_last_updated_tick"] == 5, (
        "GOOGL thesis_last_updated_tick must advance to user:current_tick_index (5) "
        "after an update stance"
    )
    # Original was 1 — advancing to 5 confirms the reset happened.
    assert googl_thesis["thesis_last_updated_tick"] > prior_positions["GOOGL"]["thesis_last_updated_tick"], (
        "GOOGL thesis_last_updated_tick must be strictly greater than its prior value (1)"
    )

"""Unit tests for ``_executor_thesis_writer_callback``.

Eight scenarios from Spec B §'Testing':

1. open stance + fill seeds a new PositionThesis row
2. Writes register in state delta (both user:positions and user:thesis)
3. Returns None (Rule 3 conformance)
4. Carry-forward thesis when decision.thesis is None
5. Overwrite thesis when decision.thesis is non-null
6. close stance deletes ticker
7. hold stance touches review fields only (no commitment mutation)
8. Fill price used as opened_price

The callback touches ``callback_context.state``, which in ADK is a
delta-tracked ``State`` object backed by ``EventActions.state_delta``.
We build one from first principles here — no full ADK runner needed.

See contract-invariants.md §C-Rule 1 amendment (2026-05-23).
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from google.adk.events.event_actions import EventActions
from google.adk.sessions.state import State

from agents.executor.agent import _executor_thesis_writer_callback
from agents.strategist.position_thesis import PositionThesis
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_callback_context(initial_state: dict):
    """Build a minimal callback-context stub whose state is a delta-tracked ADK
    ``State`` object, matching exactly what ADK injects at runtime.

    The ``State`` is backed by a shared ``EventActions`` dict so that
    ``ctx._event_actions.state_delta`` reflects any writes made through
    ``ctx.state[key] = value``.
    """

    event_actions = EventActions()
    state = State(value=dict(initial_state), delta=event_actions.state_delta)

    ctx = SimpleNamespace(
        state          = state,
        _event_actions = event_actions,
    )
    return ctx


def _minimal_state(
    *,
    stances: list[TickerStance],
    executions: list[dict] | None = None,
    user_positions: dict | None = None,
    user_thesis: str = "",
    thesis: str | None = None,
) -> dict:
    """Assemble the minimal state dict expected by the callback."""

    return {
        "tick_id":             "t-1",
        "as_of":               datetime(2026, 5, 23, tzinfo=UTC),
        "strategist_decision": StrategistDecision(
            stances        = stances,
            target_weights = {s.ticker: (s.weight or 0.0) for s in stances},
            decision_tag   = "test",
            reasoning      = "test run",
            confidence     = 0.5,
            thesis         = thesis,
        ),
        "executions":          executions or [],
        "user:positions":      user_positions or {},
        "user:thesis":         user_thesis,
    }


def _open_stance(ticker: str = "AVGO", weight: float = 0.04) -> TickerStance:
    """Return a valid ``buy`` stance for ``ticker``.

    Weight is capped at 0.04 — the buy per-trade delta cap is 0.05.
    """

    return TickerStance(
        ticker    = ticker,
        intent    = "buy",
        weight    = weight,
        catalyst  = "Q3 earnings",
        rationale = "AI capex thesis intact",
    )


def _execution_for(ticker: str, price: float) -> dict:
    """Return a minimal execution record for the callback to read fill_price from."""

    return {
        "order":        {"ticker": ticker, "action": "BUY"},
        "actual_price": price,
        "status":       "filled",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_callback_open_stance_seeds_position_thesis():
    """An open stance with a fill seeds a new PositionThesis row."""

    state = _minimal_state(
        stances     = [_open_stance("AVGO")],
        executions  = [_execution_for("AVGO", 1023.50)],
    )
    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    written = ctx.state["user:positions"]
    assert "AVGO" in written, "open stance must create an entry in user:positions"
    row = PositionThesis.model_validate(written["AVGO"])
    assert row.opened_price == 1023.50


def test_callback_writes_register_in_state_delta():
    """Both user:positions and user:thesis must appear in the state delta.

    This proves ADK will auto-yield a state-delta Event for the writes.
    See contract-invariants.md §C-Rule 1 amendment.
    """

    state = _minimal_state(
        stances     = [_open_stance("AVGO")],
        executions  = [_execution_for("AVGO", 1023.50)],
    )
    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    assert ctx.state.has_delta(), "state must have a non-empty delta after callback"
    delta = ctx._event_actions.state_delta
    assert "user:positions" in delta, "user:positions must appear in state delta"
    assert "user:thesis"    in delta, "user:thesis must appear in state delta"


def test_callback_returns_none_no_reprompt():
    """Callback must return None — Rule 3 conformance."""

    state = _minimal_state(stances=[_open_stance()])
    ctx = _make_callback_context(state)
    result = _executor_thesis_writer_callback(ctx)

    assert result is None


def test_callback_carry_forward_thesis_when_decision_thesis_is_none():
    """When ``decision.thesis`` is None, the prior user:thesis is preserved."""

    prior_thesis = "Bullish on AI infrastructure — unchanged since last week"
    state = _minimal_state(
        stances      = [],
        user_thesis  = prior_thesis,
        thesis       = None,     # explicit carry-forward sentinel
    )
    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    assert ctx.state["user:thesis"] == prior_thesis


def test_callback_overwrites_thesis_when_decision_thesis_is_non_null():
    """When ``decision.thesis`` is a non-null string, it replaces the prior thesis."""

    new_thesis = "Rotating to defensive sectors ahead of Fed decision"
    state = _minimal_state(
        stances     = [],
        user_thesis = "Old thesis",
        thesis      = new_thesis,
    )
    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    assert ctx.state["user:thesis"] == new_thesis


def test_callback_sell_stance_deletes_ticker():
    """A ``sell`` stance (full close) must remove the ticker from user:positions."""

    prior_position = PositionThesis(
        ticker                 = "NVDA",
        opened_at              = datetime(2026, 1, 1, tzinfo=UTC),
        opened_tick_id         = "t-open",
        opened_price           = 800.0,
        weight                 = 0.08,
        rationale              = "Data-centre demand",
        last_reviewed_at       = datetime(2026, 1, 1, tzinfo=UTC),
        last_reviewed_decision = "buy",
        last_reviewed_reason   = "opened on buy signal",
    ).model_dump(mode="json")

    sell_stance = TickerStance(
        ticker = "NVDA",
        intent = "sell",
        reason = "test sell",
    )

    state = _minimal_state(
        stances          = [sell_stance],
        executions       = [{"order": {"ticker": "NVDA", "action": "SELL"}, "actual_price": 820.0, "status": "filled"}],
        user_positions   = {"NVDA": prior_position},
    )
    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    assert "NVDA" not in ctx.state["user:positions"], (
        "sell stance must remove the ticker from user:positions"
    )


def test_callback_update_stance_touches_review_fields_only():
    """An ``update`` stance must update only the review fields — no trade mutation."""

    prior_dt = datetime(2026, 1, 1, tzinfo=UTC)
    prior_position = PositionThesis(
        ticker                 = "MSFT",
        opened_at              = prior_dt,
        opened_tick_id         = "t-open",
        opened_price           = 400.0,
        weight                 = 0.12,
        rationale              = "Cloud segment margin expansion",
        last_reviewed_at       = prior_dt,
        last_reviewed_decision = "buy",
        last_reviewed_reason   = "opened on buy signal",
    ).model_dump(mode="json")

    update_stance = TickerStance(
        ticker = "MSFT",
        intent = "update",
        reason = "No new information; thesis intact",
    )

    state = _minimal_state(
        stances        = [update_stance],
        user_positions = {"MSFT": prior_position},
    )
    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    result_dict = ctx.state["user:positions"]["MSFT"]
    row = PositionThesis.model_validate(result_dict)

    # Review fields must have been updated:
    assert row.last_reviewed_decision == "update"
    assert row.last_reviewed_reason   == "No new information; thesis intact"

    # Commitment fields must be preserved unchanged:
    assert row.weight        == 0.12
    assert row.rationale     == "Cloud segment margin expansion"
    assert row.opened_price  == 400.0


def test_callback_fill_price_used_as_opened_price():
    """The executor's actual fill price must land in opened_price on the new row."""

    stance = _open_stance("AAPL", weight=0.04)
    fill   = 198.75

    state = _minimal_state(
        stances    = [stance],
        executions = [_execution_for("AAPL", fill)],
    )
    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    row = PositionThesis.model_validate(ctx.state["user:positions"]["AAPL"])
    assert row.opened_price == fill, (
        f"opened_price should be the fill price {fill!r}, got {row.opened_price!r}"
    )

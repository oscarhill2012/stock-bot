"""Unit tests for the pure verb-dispatch helpers in _verb_dispatch.py.

Tests are aligned with the four-verb schema:
    buy       — open (no live position) or add (live position).  Rationale
                refreshes on every buy — the agent is accountable for the
                view at the time of each sizing decision.
    sell      — partial trim (weight supplied) or full close (weight absent).
                Rationale untouched (the trim is a sizing change, not a
                view change — use ``update`` to revise the prose).
    update    — prose-only revision; no broker call.  Rationale mutates.
    no_action — explicit no-change; no broker call, no rationale change.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.executor._verb_dispatch import apply_stance_to_thesis, resolve_broker_call
from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_stance(**kwargs) -> TickerStance:
    """Build a minimal valid ``TickerStance`` with sensible defaults.

    Intent is required on every stance; callers must supply it via kwargs.
    Additional verb-conditional fields (weight, reason, rationale, catalyst)
    are passed through as needed for each test.
    """

    defaults = {
        "ticker": "AAPL",
    }
    defaults.update(kwargs)
    return TickerStance(**defaults)


def _make_prior_row(**kwargs) -> PositionThesis:
    """Build a minimal ``PositionThesis`` row for use as a ``prior_row`` input.

    Uses iter-3 schema: no horizon / target_price / stop_price.
    """

    ts = datetime(2026, 1, 1, tzinfo=UTC)

    defaults = {
        "ticker":                 "AAPL",
        "opened_at":              ts,
        "opened_tick_id":         "t-open",
        "opened_price":           150.0,
        "weight":                 0.10,
        "rationale":              "Original rationale — must not be mutated",
        "last_reviewed_at":       ts,
        "last_reviewed_decision": "buy",
        "last_reviewed_reason":   "opened",
    }
    defaults.update(kwargs)
    return PositionThesis(**defaults)


_TS = datetime(2026, 5, 23, tzinfo=UTC)
_TICK_ID = "t-1"


# ---------------------------------------------------------------------------
# resolve_broker_call tests
# ---------------------------------------------------------------------------


def test_resolve_broker_call_buy_returns_buy_to_weight():
    """``buy`` intent must return a BUY descriptor."""

    stance = _make_stance(
        intent    = "buy",
        weight    = 0.05,
        catalyst  = "Q4 earnings",
        rationale = "Strong momentum",
    )
    result = resolve_broker_call(stance, prior_row=None)

    assert result is not None
    assert result["action"] == "BUY"
    assert result["weight"] == pytest.approx(0.05)


def test_resolve_broker_call_sell_full_close_returns_sell_zero():
    """``sell`` with no weight (full close) must return a SELL to weight=0.0."""

    prior = _make_prior_row()
    stance = _make_stance(intent="sell", reason="thesis invalidated")
    result = resolve_broker_call(stance, prior_row=prior)

    assert result is not None
    assert result["action"] == "SELL"
    assert result["weight"] == 0.0


def test_resolve_broker_call_sell_partial_returns_sell_with_weight():
    """``sell`` with weight (partial trim) must return a SELL to that weight."""

    prior = _make_prior_row(weight=0.15)
    stance = _make_stance(
        intent = "sell",
        weight = 0.07,
        reason = "Taking some profit after 20 % move",
    )
    result = resolve_broker_call(stance, prior_row=prior)

    assert result is not None
    assert result["action"] == "SELL"
    assert result["weight"] == pytest.approx(0.07)


def test_resolve_broker_call_update_returns_none():
    """``update`` intent must return ``None`` — no broker call."""

    prior = _make_prior_row()
    stance = _make_stance(
        intent  = "update",
        reason  = "Revised view following macro data",
    )
    result = resolve_broker_call(stance, prior_row=prior)

    assert result is None


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests — buy (fresh entry, prior_row=None)
# ---------------------------------------------------------------------------


def test_apply_stance_buy_entry_seeds_new_position_with_fill_price():
    """``buy`` with no prior row must create a new ``PositionThesis`` using the fill price."""

    stance = _make_stance(
        intent    = "buy",
        weight    = 0.05,
        catalyst  = "Q4 earnings",
        rationale = "Strong momentum",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = None,
        fill_price = 155.75,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert isinstance(result, PositionThesis)
    assert result.opened_price == 155.75
    assert result.ticker == "AAPL"
    assert result.rationale == "Strong momentum"
    assert result.catalyst == "Q4 earnings"
    assert result.last_reviewed_decision == "buy"
    # iter-3: no horizon / target_price / stop_price on the thesis.
    assert not hasattr(result, "horizon")
    assert not hasattr(result, "target_price")
    assert not hasattr(result, "stop_price")


def test_apply_stance_buy_entry_sets_opened_tick_id_and_timestamps():
    """``buy`` entry must capture opened_tick_id and set opened_at == as_of."""

    stance = _make_stance(
        intent    = "buy",
        weight    = 0.03,
        rationale = "Momentum play",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = None,
        fill_price = 100.0,
        tick_id    = "tick-42",
        as_of      = _TS,
    )

    assert result is not None
    assert result.opened_tick_id == "tick-42"
    assert result.opened_at == _TS


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests — buy (position increase, prior_row present)
# ---------------------------------------------------------------------------


def test_apply_stance_buy_add_updates_weight_and_refreshes_rationale():
    """``buy`` on an existing position must update the weight and refresh rationale.

    The agent is on the record justifying each add — buy stances always
    overwrite the row's rationale with the latest reasoning.
    """

    prior = _make_prior_row(
        weight    = 0.03,
        rationale = "Original rationale from open",
    )
    stance = _make_stance(
        intent    = "buy",
        weight    = 0.05,
        rationale = "Adding on the dip",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 155.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.weight == pytest.approx(0.05), "buy must update the weight"
    assert result.rationale == "Adding on the dip", (
        "buy-add must refresh rationale with the new stance reasoning"
    )
    assert result.last_reviewed_decision == "buy"


def test_apply_stance_buy_add_refreshes_catalyst_when_supplied():
    """``buy`` add with a catalyst must update the catalyst field."""

    prior = _make_prior_row(catalyst="Old catalyst")
    stance = _make_stance(
        intent   = "buy",
        weight   = 0.05,
        catalyst = "New catalyst event",
        rationale = "More upside",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 155.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.catalyst == "New catalyst event"


def test_apply_stance_buy_add_preserves_catalyst_when_none():
    """``buy`` add without a catalyst must preserve the existing catalyst."""

    prior = _make_prior_row(catalyst="Original catalyst")
    stance = _make_stance(
        intent    = "buy",
        weight    = 0.05,
        # No catalyst supplied on this add stance.
        rationale = "Adding to winner",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 155.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.catalyst == "Original catalyst"


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests — sell (partial trim)
# ---------------------------------------------------------------------------


def test_apply_stance_sell_trim_updates_weight_preserves_rationale():
    """``sell`` with weight (partial trim) must update weight and preserve rationale.

    Trims are sizing changes, not view changes — the prose stays as the
    last buy/update wrote it.  Use ``update`` to revise the rationale.
    """

    original_rationale = "Original thesis locked at open"
    prior = _make_prior_row(
        weight    = 0.10,
        rationale = original_rationale,
    )
    stance = _make_stance(
        intent  = "sell",
        weight  = 0.05,
        reason  = "Partial profit-take",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 165.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.rationale == original_rationale, (
        "sell trim must not overwrite rationale — Invariant 3"
    )
    assert result.weight == pytest.approx(0.05), "sell trim must update the weight"
    assert result.last_reviewed_decision == "sell"
    assert result.last_reviewed_at == _TS
    assert result.last_reviewed_reason == "Partial profit-take"


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests — sell (full close)
# ---------------------------------------------------------------------------


def test_apply_stance_sell_full_close_returns_none():
    """``sell`` without weight (full close) must return ``None`` so the caller drops the ticker."""

    prior = _make_prior_row()
    stance = _make_stance(intent="sell", reason="thesis invalidated")

    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 175.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is None


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests — update
# ---------------------------------------------------------------------------


def test_apply_stance_update_refreshes_rationale_and_review_trail():
    """``update`` refreshes the rationale (the prose view) and the review trail.

    Sizing fields (weight, opened_price, opened_at) and catalyst stay
    pinned to the original entry — update revises the view, not the
    commitment.
    """

    prior = _make_prior_row(
        weight    = 0.10,
        catalyst  = "Q4 earnings",
        rationale = "Original rationale at open",
    )
    new_ts = datetime(2026, 5, 24, tzinfo=UTC)

    stance = _make_stance(
        intent  = "update",
        reason  = "Revised macro view",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = None,
        tick_id    = "t-2",
        as_of      = new_ts,
    )

    assert result is not None
    # Review fields updated:
    assert result.last_reviewed_at       == new_ts
    assert result.last_reviewed_decision == "update"
    assert result.last_reviewed_reason   == "Revised macro view"

    # Rationale refreshes to the new reason — the agent's standing view
    # is what we record going forward.
    assert result.rationale == "Revised macro view"

    # Sizing + catalyst preserved:
    assert result.weight       == prior.weight
    assert result.catalyst     == prior.catalyst
    assert result.opened_price == prior.opened_price
    assert result.opened_at    == prior.opened_at


# ---------------------------------------------------------------------------
# Task 6 (iter-3): buy intent replaces open; no horizon/target/stop fields
# ---------------------------------------------------------------------------


def test_apply_stance_to_thesis_buy_only_reads_rationale_and_catalyst():
    """apply_stance_to_thesis on a buy stance produces a thesis with rationale + catalyst,
    no horizon/target/stop fields."""
    from datetime import datetime, timezone
    from agents.executor._verb_dispatch import apply_stance_to_thesis
    from agents.strategist.stance_schema import TickerStance

    stance = TickerStance(
        ticker="AAPL", intent="buy", weight=0.03,
        rationale="iPhone launch", catalyst="iPhone 17 launch event",
    )
    thesis = apply_stance_to_thesis(
        stance, prior_row=None, fill_price=210.0,
        tick_id="tick-1", as_of=datetime.now(timezone.utc),
    )
    assert thesis.rationale == "iPhone launch"
    assert thesis.catalyst == "iPhone 17 launch event"
    assert thesis.opened_price == 210.0
    assert not hasattr(thesis, "horizon")
    assert not hasattr(thesis, "target_price")
    assert not hasattr(thesis, "stop_price")


# ---------------------------------------------------------------------------
# Task 9 (iter-3): thesis_last_updated_tick is written on buy and update
# ---------------------------------------------------------------------------


def test_buy_stance_writes_thesis_last_updated_tick():
    """apply_stance_to_thesis on a buy must set thesis_last_updated_tick = current_tick_index."""

    from datetime import datetime, timezone

    from agents.executor._verb_dispatch import apply_stance_to_thesis
    from agents.strategist.stance_schema import TickerStance

    stance = TickerStance(ticker="AAPL", intent="buy", weight=0.03, rationale="x")
    thesis = apply_stance_to_thesis(
        stance,
        prior_row          = None,
        fill_price         = 100.0,
        tick_id            = "tick-1",
        as_of              = datetime.now(timezone.utc),
        current_tick_index = 7,
    )

    assert thesis is not None
    assert thesis.thesis_last_updated_tick == 7, (
        "buy stance must stamp thesis_last_updated_tick with current_tick_index"
    )


def test_buy_add_stance_writes_thesis_last_updated_tick():
    """apply_stance_to_thesis on a buy-add (prior_row present) must update thesis_last_updated_tick."""

    from datetime import datetime, timezone

    from agents.executor._verb_dispatch import apply_stance_to_thesis
    from agents.strategist.stance_schema import TickerStance

    prior = _make_prior_row(weight=0.03)
    stance = TickerStance(
        ticker="AAPL", intent="buy", weight=0.05, rationale="Adding on the dip"
    )
    thesis = apply_stance_to_thesis(
        stance,
        prior_row          = prior,
        fill_price         = 145.0,
        tick_id            = "tick-5",
        as_of              = datetime.now(timezone.utc),
        current_tick_index = 12,
    )

    assert thesis is not None
    assert thesis.thesis_last_updated_tick == 12, (
        "buy-add stance must bump thesis_last_updated_tick to current_tick_index"
    )


def test_update_stance_writes_thesis_last_updated_tick():
    """apply_stance_to_thesis on an update must set thesis_last_updated_tick = current_tick_index."""

    from datetime import datetime, timezone

    from agents.executor._verb_dispatch import apply_stance_to_thesis
    from agents.strategist.stance_schema import TickerStance

    prior = _make_prior_row(weight=0.08)
    stance = TickerStance(
        ticker="AAPL", intent="update", reason="Revised macro backdrop"
    )
    thesis = apply_stance_to_thesis(
        stance,
        prior_row          = prior,
        fill_price         = None,
        tick_id            = "tick-9",
        as_of              = datetime.now(timezone.utc),
        current_tick_index = 9,
    )

    assert thesis is not None
    assert thesis.thesis_last_updated_tick == 9, (
        "update stance must stamp thesis_last_updated_tick with current_tick_index"
    )


def test_sell_trim_does_not_update_thesis_last_updated_tick():
    """apply_stance_to_thesis on a partial sell must NOT change thesis_last_updated_tick.

    Partial trims reduce weight but do not revise the thesis prose,
    so the staleness clock must not reset.
    """

    from datetime import datetime, timezone

    from agents.executor._verb_dispatch import apply_stance_to_thesis
    from agents.strategist.stance_schema import TickerStance

    # Seed a prior row with a known tick index (simulating a thesis
    # written at tick 3 that has not been updated since).
    prior = _make_prior_row(weight=0.10, thesis_last_updated_tick=3)

    stance = TickerStance(ticker="AAPL", intent="sell", weight=0.05, reason="Profit take")
    thesis = apply_stance_to_thesis(
        stance,
        prior_row          = prior,
        fill_price         = 160.0,
        tick_id            = "tick-10",
        as_of              = datetime.now(timezone.utc),
        current_tick_index = 10,
    )

    assert thesis is not None
    assert thesis.thesis_last_updated_tick == 3, (
        "partial sell must NOT bump thesis_last_updated_tick — "
        "the staleness clock only resets on buy or update stances"
    )

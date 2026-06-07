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

from agents.executor._verb_dispatch import apply_stance_to_thesis
from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_stance(**kwargs) -> TickerStance:
    """Build a minimal valid ``TickerStance`` with sensible defaults.

    Intent is required on every stance; callers must supply it via kwargs.
    Additional verb-conditional fields (weight, rationale) are passed
    through as needed for each test.
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
    }
    defaults.update(kwargs)
    return PositionThesis(**defaults)


_TS = datetime(2026, 5, 23, tzinfo=UTC)
_TICK_ID = "t-1"


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests — buy (fresh entry, prior_row=None)
# ---------------------------------------------------------------------------


def test_apply_stance_buy_entry_seeds_new_position_with_fill_price():
    """``buy`` with no prior row must create a new ``PositionThesis`` using the fill price."""

    stance = _make_stance(
        intent    = "buy",
        weight    = 0.05,
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
        rationale  = "Partial profit-take",
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
    # last_reviewed_reason was removed (A-013 tail); trim reasons are not
    # independently tracked — the stance.rationale is not written to the thesis
    # on sell (rationale is preserved from the last buy/update).


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests — sell (full close)
# ---------------------------------------------------------------------------


def test_apply_stance_sell_full_close_returns_none():
    """``sell`` without weight (full close) must return ``None`` so the caller drops the ticker."""

    prior = _make_prior_row()
    stance = _make_stance(intent="sell", rationale="thesis invalidated")

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

    Sizing fields (weight, opened_price, opened_at) stay pinned to the
    original entry — update revises the view, not the commitment.
    """

    prior = _make_prior_row(
        weight    = 0.10,
        rationale = "Original rationale at open",
    )
    new_ts = datetime(2026, 5, 24, tzinfo=UTC)

    stance = _make_stance(
        intent  = "update",
        rationale  = "Revised macro view",
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
    # last_reviewed_reason was removed (A-013 tail); the update reason now
    # lives exclusively in ``rationale`` — the verb-dispatch ``update`` branch
    # sets rationale = stance.rationale.

    # Rationale refreshes to the new reason — the agent's standing view
    # is what we record going forward.
    assert result.rationale == "Revised macro view"

    # Sizing preserved:
    assert result.weight       == prior.weight
    assert result.opened_price == prior.opened_price
    assert result.opened_at    == prior.opened_at


# ---------------------------------------------------------------------------
# Task 6 (iter-3): buy intent replaces open; no horizon/target/stop fields
# ---------------------------------------------------------------------------


def test_apply_stance_to_thesis_buy_only_reads_rationale():
    """apply_stance_to_thesis on a buy stance produces a thesis with rationale,
    no horizon/target/stop/catalyst fields."""
    from datetime import datetime, timezone
    from agents.executor._verb_dispatch import apply_stance_to_thesis
    from agents.strategist.stance_schema import TickerStance

    stance = TickerStance(
        ticker="AAPL", intent="buy", weight=0.03,
        rationale="iPhone launch",
    )
    thesis = apply_stance_to_thesis(
        stance, prior_row=None, fill_price=210.0,
        tick_id="tick-1", as_of=datetime.now(timezone.utc),
    )
    assert thesis.rationale == "iPhone launch"
    assert thesis.opened_price == 210.0
    assert not hasattr(thesis, "horizon")
    assert not hasattr(thesis, "target_price")
    assert not hasattr(thesis, "stop_price")
    assert not hasattr(thesis, "catalyst")


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
        ticker="AAPL", intent="update", rationale="Revised macro backdrop"
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

    stance = TickerStance(ticker="AAPL", intent="sell", weight=0.05, rationale="Profit take")
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

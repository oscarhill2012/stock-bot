"""Unit tests for the pure verb-dispatch helpers in _verb_dispatch.py.

10 tests — one per acceptance bullet in Spec B §'Testing':

1. resolve_broker_call(open)  → BUY
2. resolve_broker_call(close) → SELL
3. resolve_broker_call(hold)  → None
4. resolve_broker_call(update)→ None
5. apply_stance_to_thesis(open) seeds new row with fill_price
6. apply_stance_to_thesis(hold) touches review fields only
7. apply_stance_to_thesis(update) mutates target/stop/catalyst/horizon
8. apply_stance_to_thesis(update) does NOT mutate rationale
9. apply_stance_to_thesis(close) returns None signalling deletion
10. apply_stance_to_thesis(add) preserves rationale
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

    The legacy ``preferred_weight`` / ``conviction`` fields are required on
    the model (not optional) — supply them so we bypass the legacy validator
    and only trigger the intent-based one.
    """

    defaults = {
        "ticker":           "AAPL",
        "preferred_weight": 0.0,
        "conviction":       0.5,
    }
    defaults.update(kwargs)
    return TickerStance(**defaults)


def _make_prior_row(**kwargs) -> PositionThesis:
    """Build a minimal ``PositionThesis`` row for use as a ``prior_row`` input."""

    ts = datetime(2026, 1, 1, tzinfo=UTC)

    defaults = {
        "ticker":                 "AAPL",
        "opened_at":              ts,
        "opened_tick_id":         "t-open",
        "opened_price":           150.0,
        "weight":                 0.10,
        "horizon":                "swing",
        "rationale":              "Original rationale — must not be mutated",
        "last_reviewed_at":       ts,
        "last_reviewed_decision": "open",
        "last_reviewed_reason":   "opened",
    }
    defaults.update(kwargs)
    return PositionThesis(**defaults)


_TS = datetime(2026, 5, 23, tzinfo=UTC)
_TICK_ID = "t-1"


# ---------------------------------------------------------------------------
# resolve_broker_call tests
# ---------------------------------------------------------------------------


def test_resolve_broker_call_open_returns_buy_to_weight():
    """``open`` intent must return a BUY descriptor."""

    stance = _make_stance(
        intent        = "open",
        weight        = 0.10,
        target_price  = 200.0,
        stop_price    = 140.0,
        catalyst      = "Q4 earnings",
        horizon       = "swing",
        rationale     = "Strong momentum",
    )
    result = resolve_broker_call(stance, prior_row=None)

    assert result is not None
    assert result["action"] == "BUY"


def test_resolve_broker_call_close_returns_sell_all():
    """``close`` intent must return a SELL descriptor."""

    prior = _make_prior_row()
    stance = _make_stance(intent="close")
    result = resolve_broker_call(stance, prior_row=prior)

    assert result is not None
    assert result["action"] == "SELL"
    assert result["weight"] == 0.0


def test_resolve_broker_call_hold_returns_none():
    """``hold`` intent must return ``None`` — no broker call."""

    prior = _make_prior_row()
    stance = _make_stance(
        intent  = "hold",
        reason  = "Waiting for catalyst",
    )
    result = resolve_broker_call(stance, prior_row=prior)

    assert result is None


def test_resolve_broker_call_update_returns_none():
    """``update`` intent must return ``None`` — no broker call."""

    prior = _make_prior_row()
    stance = _make_stance(
        intent        = "update",
        reason        = "Raising target after earnings beat",
        target_price  = 220.0,
    )
    result = resolve_broker_call(stance, prior_row=prior)

    assert result is None


# ---------------------------------------------------------------------------
# apply_stance_to_thesis tests
# ---------------------------------------------------------------------------


def test_apply_stance_open_seeds_new_position_with_fill_price():
    """``open`` must create a new ``PositionThesis`` row using the fill price."""

    stance = _make_stance(
        intent        = "open",
        weight        = 0.10,
        target_price  = 200.0,
        stop_price    = 140.0,
        catalyst      = "Q4 earnings",
        horizon       = "swing",
        rationale     = "Strong momentum",
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
    assert result.last_reviewed_decision == "open"


def test_apply_stance_hold_touches_review_fields_only():
    """``hold`` must only update review fields; all commitment fields survive unchanged."""

    prior = _make_prior_row(
        weight        = 0.15,
        target_price  = 200.0,
        stop_price    = 140.0,
        rationale     = "Original rationale — must not be mutated",
        horizon       = "swing",
    )
    new_ts = datetime(2026, 5, 24, tzinfo=UTC)

    stance = _make_stance(
        intent  = "hold",
        reason  = "Still waiting for Q4 catalyst",
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
    assert result.last_reviewed_decision == "hold"
    assert result.last_reviewed_reason   == "Still waiting for Q4 catalyst"

    # All commitment fields preserved:
    assert result.weight       == prior.weight
    assert result.target_price == prior.target_price
    assert result.stop_price   == prior.stop_price
    assert result.rationale    == prior.rationale
    assert result.horizon      == prior.horizon
    assert result.opened_price == prior.opened_price
    assert result.opened_at    == prior.opened_at


def test_apply_stance_update_mutates_target_stop_catalyst_horizon():
    """``update`` must overwrite the mutable commitment fields where supplied."""

    prior = _make_prior_row(
        target_price = 200.0,
        stop_price   = 140.0,
        catalyst     = "Q4 earnings",
        horizon      = "swing",
    )
    stance = _make_stance(
        intent       = "update",
        reason       = "Raising target after beat",
        target_price = 225.0,
        stop_price   = 160.0,
        catalyst     = "Follow-on expansion",
        horizon      = "long_term",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = None,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.target_price == 225.0
    assert result.stop_price   == 160.0
    assert result.catalyst     == "Follow-on expansion"
    assert result.horizon      == "long_term"


def test_apply_stance_update_does_not_mutate_rationale():
    """``update`` must NOT overwrite ``rationale`` — Invariant 3."""

    original_rationale = "Original rationale — frozen at open"
    prior = _make_prior_row(rationale=original_rationale)

    stance = _make_stance(
        intent       = "update",
        reason       = "Raising target",
        target_price = 225.0,
        # Note: rationale is intentionally absent from the update stance
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = None,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.rationale == original_rationale, (
        "update stance must not overwrite rationale — Invariant 3"
    )


def test_apply_stance_close_returns_none_signalling_deletion():
    """``close`` must return ``None`` so the caller drops the ticker."""

    prior = _make_prior_row()
    stance = _make_stance(intent="close")

    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 175.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is None


def test_apply_stance_add_preserves_rationale():
    """``add`` must NOT overwrite ``rationale`` — Invariant 3."""

    original_rationale = "Locked-in rationale from open"
    prior = _make_prior_row(
        weight    = 0.05,
        rationale = original_rationale,
    )
    stance = _make_stance(
        intent  = "add",
        weight  = 0.10,
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 155.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.rationale == original_rationale, (
        "add stance must not overwrite rationale — Invariant 3"
    )
    assert result.weight == 0.10, "add must update the weight"


# ---------------------------------------------------------------------------
# trim verb tests (m-1 additions)
# ---------------------------------------------------------------------------


def test_resolve_broker_call_trim_returns_sell_call():
    """``trim`` intent must return a SELL descriptor with the reduced weight.

    Mirrors the ``add`` test — the broker leg of a trim is a SELL to the
    new (lower) weight, not a full close.
    """

    prior = _make_prior_row(weight=0.15)
    stance = _make_stance(
        intent  = "trim",
        weight  = 0.07,
        reason  = "Taking some profit after 20 % move",
    )
    result = resolve_broker_call(stance, prior_row=prior)

    assert result is not None
    assert result["action"] == "SELL"
    assert result["weight"] == pytest.approx(0.07)


def test_apply_stance_to_thesis_trim_preserves_rationale():
    """``trim`` must NOT overwrite ``rationale`` — Invariant 3 symmetry."""

    original_rationale = "Original thesis locked at open"
    prior = _make_prior_row(
        weight    = 0.15,
        rationale = original_rationale,
    )
    stance = _make_stance(
        intent  = "trim",
        weight  = 0.07,
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
        "trim stance must not overwrite rationale — Invariant 3"
    )
    # Weight is updated to the reduced amount.
    assert result.weight == pytest.approx(0.07)


def test_apply_stance_to_thesis_trim_updates_last_reviewed_decision():
    """``trim`` must stamp ``last_reviewed_decision = "trim"`` on the row."""

    prior = _make_prior_row(
        weight                = 0.15,
        last_reviewed_decision = "open",
    )
    stance = _make_stance(
        intent  = "trim",
        weight  = 0.07,
        reason  = "Locking in gains",
    )
    result = apply_stance_to_thesis(
        stance,
        prior_row  = prior,
        fill_price = 165.0,
        tick_id    = _TICK_ID,
        as_of      = _TS,
    )

    assert result is not None
    assert result.last_reviewed_decision == "trim"
    assert result.last_reviewed_at       == _TS
    assert result.last_reviewed_reason   == "Locking in gains"

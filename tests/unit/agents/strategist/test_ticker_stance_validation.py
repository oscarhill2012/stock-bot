"""``TickerStance`` intent-based field-validation tests — iter-3 three-verb schema.

Rewrote for the iter-3 schema rewrite (buy / sell / update vocabulary).
The pre-iter-3 tests for the old six-verb set (open / add / trim / close /
hold / update-with-thesis-fields) were deleted — those verbs are now
rejected at the schema level.

Verb rules (iter-3 schema):
    buy:    weight (0 < w ≤ 0.05) + rationale required.
    sell:   reason required; weight optional (absent = full close).
    update: reason required; no weight, no rationale, no catalyst.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _buy(**overrides) -> dict:
    """Return a minimal valid buy stance dict."""
    base = dict(
        ticker="AAPL",
        intent="buy",
        weight=0.03,
        rationale="FCF + insider buying; iPhone supercycle ahead.",
    )
    base.update(overrides)
    return base


def _sell(**overrides) -> dict:
    """Return a minimal valid sell (full close) stance dict."""
    base = dict(
        ticker="AAPL",
        intent="sell",
        reason="Guidance cut invalidates thesis.",
    )
    base.update(overrides)
    return base


def _update(**overrides) -> dict:
    """Return a minimal valid update stance dict."""
    base = dict(
        ticker="AAPL",
        intent="update",
        reason="Raising my Q4 revenue estimate after data-centre capex read.",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Per-verb happy-path tests
# ---------------------------------------------------------------------------


def test_buy_minimal_valid():
    """A buy stance with weight + rationale validates without error."""
    stance = TickerStance.model_validate(_buy())
    assert stance.intent == "buy"
    assert stance.weight == 0.03
    assert stance.rationale is not None


def test_sell_minimal_valid():
    """A sell stance (full close) requires only reason."""
    stance = TickerStance.model_validate(_sell())
    assert stance.intent == "sell"
    assert stance.weight is None
    assert stance.reason is not None


def test_sell_partial_with_weight():
    """A sell stance with weight is a partial trim."""
    stance = TickerStance.model_validate(_sell(weight=0.02))
    assert stance.intent == "sell"
    assert stance.weight == 0.02


def test_update_minimal_valid():
    """An update stance requires only reason."""
    stance = TickerStance.model_validate(_update())
    assert stance.intent == "update"
    assert stance.reason is not None


# ---------------------------------------------------------------------------
# Per-verb missing-field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", ["weight", "rationale"])
def test_buy_missing_required_field(missing_field: str):
    """buy without weight or rationale must raise ValidationError."""
    data = _buy()
    data[missing_field] = None

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert missing_field in msg


def test_buy_weight_above_cap_raises():
    """buy weight above the 5 % per-trade cap must raise ValidationError."""
    data = _buy(weight=0.06)

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "buy" in msg


def test_sell_missing_reason_raises():
    """sell without reason raises ValidationError — silent exits are forbidden."""
    data = _sell(reason=None)

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "reason" in msg.lower() or "sell" in msg


def test_update_missing_reason_raises():
    """update without reason raises ValidationError."""
    data = _update(reason=None)

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "reason" in msg.lower() or "update" in msg


def test_update_with_weight_raises():
    """update with weight raises ValidationError — no trade occurs on update."""
    data = _update(weight=0.03)

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    assert "weight" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Old verb rejection guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_verb", ["open", "add", "trim", "close", "hold"])
def test_old_verbs_rejected(old_verb: str):
    """All pre-iter-3 verbs must raise ValidationError with a migration hint."""
    with pytest.raises(ValidationError) as exc_info:
        TickerStance(ticker="AAPL", intent=old_verb)

    err = str(exc_info.value)
    # The error must mention the new vocabulary so the caller can self-correct.
    assert "buy" in err and "sell" in err


# ---------------------------------------------------------------------------
# Buy-stance rationale regression guard
# ---------------------------------------------------------------------------


def test_buy_stance_missing_rationale():
    """A buy stance missing rationale must fail at the schema level.

    Regression guard: rationale is required on buy because it seeds the
    PositionThesis row (Invariant 3 — frozen at entry).  Omitting it must
    raise early at parse time with a message that names 'rationale', so the
    LLM's re-prompt includes the correct field.
    """
    data = dict(
        ticker="MSFT",
        intent="buy",
        weight=0.04,
        # rationale intentionally omitted — must raise, not pass silently
    )

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "rationale" in msg

"""``TickerStance`` intent-based field-validation tests — Spec B Band 3 §Task 3.5.

Covers the per-verb required-field rules enforced by
``TickerStance._require_intent_fields``.  One test per verb asserts the
happy path (minimal valid stance).  One parametrised test asserts that
each individually-missing required field produces a ``ValidationError``.

Verb rules (per Spec B §'Validation rules'):
    open:   weight, target_price, stop_price, catalyst, horizon, rationale
            all required.
    add:    weight only required.
    trim:   weight + reason required.
    close:  no additional fields required beyond ticker + intent.
    hold:   reason required.
    update: reason + at least one of target_price / stop_price /
            catalyst / horizon required.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open(**overrides) -> dict:
    """Return a fully-populated open stance dict, with optional field overrides."""
    base = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="open",
        weight=0.08,
        target_price=210.0,
        stop_price=185.0,
        catalyst="AI-driven revenue acceleration",
        horizon="swing",
        rationale="Strong FCF and secular growth tailwind.",
    )
    base.update(overrides)
    return base


def _add(**overrides) -> dict:
    """Return a minimal valid add stance dict."""
    base = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="add",
        weight=0.12,
    )
    base.update(overrides)
    return base


def _trim(**overrides) -> dict:
    """Return a minimal valid trim stance dict."""
    base = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="trim",
        weight=0.05,
        reason="Price hit first target; locking in partial gains.",
    )
    base.update(overrides)
    return base


def _close(**overrides) -> dict:
    """Return a minimal valid close stance dict."""
    base = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="close",
    )
    base.update(overrides)
    return base


def _hold(**overrides) -> dict:
    """Return a minimal valid hold stance dict."""
    base = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="hold",
        reason="No change in thesis; price action still intact.",
    )
    base.update(overrides)
    return base


def _update(**overrides) -> dict:
    """Return a minimal valid update stance dict."""
    base = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="update",
        reason="Raised target after stronger-than-expected earnings.",
        target_price=225.0,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Per-verb happy-path tests
# ---------------------------------------------------------------------------


def test_open_minimal_valid():
    """A fully-populated open stance validates without error."""
    stance = TickerStance.model_validate(_open())
    assert stance.intent == "open"
    assert stance.weight == 0.08
    assert stance.rationale == "Strong FCF and secular growth tailwind."


def test_add_minimal_valid():
    """An add stance needs only weight beyond ticker/intent."""
    stance = TickerStance.model_validate(_add())
    assert stance.intent == "add"
    assert stance.weight == 0.12


def test_trim_minimal_valid():
    """A trim stance needs weight + reason."""
    stance = TickerStance.model_validate(_trim())
    assert stance.intent == "trim"
    assert stance.weight == 0.05
    assert stance.reason is not None


def test_close_minimal_valid():
    """A close stance requires no fields beyond ticker and intent."""
    stance = TickerStance.model_validate(_close())
    assert stance.intent == "close"


def test_hold_minimal_valid():
    """A hold stance requires only reason."""
    stance = TickerStance.model_validate(_hold())
    assert stance.intent == "hold"
    assert stance.reason is not None


def test_update_minimal_valid():
    """An update stance requires reason + at least one commitment field."""
    stance = TickerStance.model_validate(_update())
    assert stance.intent == "update"
    assert stance.reason is not None
    # At least one of the mutable fields is populated.
    assert stance.target_price is not None


def test_update_valid_with_only_stop_price():
    """update with only stop_price as the mutated field is sufficient."""
    data = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="update",
        reason="Stop raised to protect gains.",
        stop_price=195.0,
    )
    stance = TickerStance.model_validate(data)
    assert stance.stop_price == 195.0


def test_update_valid_with_only_horizon():
    """update with only horizon as the mutated field is sufficient."""
    data = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="update",
        reason="Extending horizon after catalyst delay.",
        horizon="long_term",
    )
    stance = TickerStance.model_validate(data)
    assert stance.horizon == "long_term"


# ---------------------------------------------------------------------------
# Per-verb missing-field validation (parametrised)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", [
    "weight",
    "target_price",
    "stop_price",
    "catalyst",
    "horizon",
    "rationale",
])
def test_open_missing_required_field(missing_field: str):
    """Every required field for open, individually absent, must raise ValidationError."""
    data = _open()
    data[missing_field] = None

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    # The error message should name the violated rule clearly.
    msg = str(exc_info.value)
    assert "open" in msg, f"Expected 'open' in error message; got: {msg}"


def test_add_missing_weight():
    """add without weight raises ValidationError."""
    data = _add(weight=None)

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "add" in msg


@pytest.mark.parametrize("missing_field", ["weight", "reason"])
def test_trim_missing_required_field(missing_field: str):
    """trim without weight or reason raises ValidationError."""
    data = _trim()
    data[missing_field] = None

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "trim" in msg


def test_hold_missing_reason():
    """hold without reason raises ValidationError."""
    data = _hold(reason=None)

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "hold" in msg


def test_update_missing_reason():
    """update without reason raises ValidationError."""
    data = _update(reason=None)

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "update" in msg


def test_update_missing_all_commitment_fields():
    """update with reason but no mutable fields raises ValidationError."""
    data = dict(
        preferred_weight=0.0, conviction=0.0,
        ticker="AAPL",
        intent="update",
        reason="I have a reason but nothing to change.",
        # target_price, stop_price, catalyst, horizon all absent/null
    )

    with pytest.raises(ValidationError) as exc_info:
        TickerStance.model_validate(data)

    msg = str(exc_info.value)
    assert "update" in msg
    # Should suggest the alternative verb.
    assert "hold" in msg

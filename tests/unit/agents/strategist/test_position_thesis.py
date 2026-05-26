"""``PositionThesis`` model tests — schema evolution gate + round-trip.

Updated for iter-3: ``target_price``, ``stop_price``, and ``horizon`` were
removed from ``PositionThesis`` in Task 3.  The V1 frozen fixture (tests/
fixtures/position_thesis_v1.json) was updated accordingly, and the
``test_position_thesis_horizon_validates_enum`` test was replaced with
``test_position_thesis_rejects_extra_fields`` which pins the iter-3
``extra="forbid"`` contract.

Tests:
    1. Round-trip through model_dump_json → model_validate_json.
    2. Extra fields (stale horizon / target_price) are rejected loudly.
    3. V1 frozen fixture deserialises with the current code (schema-evolution gate).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.strategist.position_thesis import PositionThesis

# Path to the frozen V1 wire-shape fixture.  Any new required field added to
# PositionThesis without a default will break this test — that is the gate.
# The fixture was updated in iter-3 to remove target_price / stop_price /
# horizon and to set last_reviewed_decision to "buy" (the new iter-3 Literal).
FIXTURE_PATH = (
    Path(__file__).parents[3]
    / "fixtures" / "position_thesis_v1.json"
)


def test_position_thesis_round_trips_through_json():
    """Round-trip a populated row through model_dump / model_validate."""

    fixture = json.loads(FIXTURE_PATH.read_text())
    thesis  = PositionThesis.model_validate(fixture)

    restored = PositionThesis.model_validate_json(thesis.model_dump_json())
    assert restored == thesis


def test_position_thesis_rejects_extra_fields():
    """Stale fields (horizon, target_price, stop_price) must raise ValidationError.

    ``extra="forbid"`` on PositionThesis ensures any caller still writing
    the iter-2 fields gets a loud failure rather than silent truncation.
    """
    fixture = json.loads(FIXTURE_PATH.read_text())
    fixture["horizon"] = "swing"        # removed in iter-3 — must reject

    with pytest.raises(ValidationError):
        PositionThesis.model_validate(fixture)


def test_position_thesis_v1_frozen_payload_deserialises():
    """The V1 wire shape MUST deserialise with the current code.

    Adding a new field is OK if and only if it has a default.  This
    test is the gate: if you add a field without a default, the
    fixture stops deserialising and you get a loud failure at PR
    time.
    """

    fixture = json.loads(FIXTURE_PATH.read_text())
    thesis  = PositionThesis.model_validate(fixture)

    # Spot-check immutable fields survived round-trip.
    assert thesis.opened_price > 0
    assert thesis.rationale != ""


def test_position_thesis_has_no_horizon_target_stop():
    """PositionThesis after iter-3 carries only prose + opened context."""
    from agents.strategist.position_thesis import PositionThesis

    fields = set(PositionThesis.model_fields.keys())
    assert "target_price" not in fields
    assert "stop_price" not in fields
    assert "horizon" not in fields

    assert "rationale" in fields
    assert "opened_price" in fields
    assert "opened_at" in fields


def test_extra_field_target_price_rejected():
    """Stale callers passing target_price get a loud ValidationError."""
    from agents.strategist.position_thesis import PositionThesis
    from datetime import datetime, timezone

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PositionThesis(
            ticker="AAPL",
            opened_at=datetime.now(timezone.utc),
            opened_price=100.0,
            rationale="x",
            target_price=120.0,
        )

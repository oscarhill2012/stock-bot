"""``PositionThesis`` model tests — schema evolution gate + round-trip.

Three tests, per Spec B Plan 1 §Task 3.4:
    1. Round-trip through model_dump_json → model_validate_json.
    2. Bad horizon value raises ValidationError.
    3. V1 frozen fixture deserialises with the current code (schema-evolution gate).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.strategist.position_thesis import PositionThesis

# Path to the frozen V1 wire-shape fixture.  Any new field added to
# PositionThesis without a default value will break this test — that is
# the intended gate.
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


def test_position_thesis_horizon_validates_enum():
    """A bad horizon value must raise ValidationError."""

    fixture = json.loads(FIXTURE_PATH.read_text())
    fixture["horizon"] = "bogus"

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

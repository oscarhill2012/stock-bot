from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.strategist.position_thesis import PositionThesis
from agents.strategist.schema import StrategistDecision
from config.strategist import get_strategist_config  # used by test_strategist_decision_rejects_long_reasoning


def _now():
    """Return a UTC-aware timestamp suitable for PositionThesis fixtures."""
    return datetime.now(tz=UTC)


def test_strategist_decision_rejects_long_reasoning():
    """``reasoning`` is capped by the *schema* cap, which is the prompt cap
    in ``config/strategist.json`` plus ``slack_percent`` headroom (see the
    "two-tier convention" note in ``src/config/strategist.py``).  The test
    reads the live schema cap from config so that retuning either the
    prompt cap or the slack does not silently break this regression.
    """

    cfg        = get_strategist_config()
    schema_cap = cfg.schema_cap(cfg.decision_caps.reasoning_max_chars)

    with pytest.raises(ValidationError):
        StrategistDecision(
            target_weights={"AAPL": 0.1},
            decision_tag="test",
            reasoning="x" * (schema_cap + 1),  # one char over the *schema* (slack-applied) cap
            thesis="ok",
            confidence=0.7,
        )


def test_strategist_decision_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        StrategistDecision(
            target_weights={},
            decision_tag="test",
            reasoning="ok",
            thesis="ok",
            confidence=1.5,
        )


def test_position_thesis_valid():
    """PositionThesis (iter-3 schema) round-trips the core fields correctly."""
    pt = PositionThesis(
        ticker="AAPL",
        opened_at=_now(),
        opened_tick_id="tick_001",
        opened_price=200.0,
        weight=0.05,
        rationale="Strong momentum",
        last_reviewed_at=_now(),
        last_reviewed_decision="buy",
        last_reviewed_reason="Initial entry.",
    )
    assert pt.ticker == "AAPL"
    assert pt.rationale == "Strong momentum"


def test_position_thesis_rejects_old_fields():
    """The iter-3 ``PositionThesis`` uses ``extra='forbid'`` — passing old fields
    like ``horizon``, ``target_price``, or ``stop_price`` must raise ValidationError.

    This pins the regression that stale callers (serialised pre-iter-3 state,
    old test fixtures) cannot silently populate removed fields.
    """

    with pytest.raises(ValidationError):
        PositionThesis(
            ticker="AAPL",
            opened_at=_now(),
            opened_tick_id="tick_001",
            opened_price=200.0,
            weight=0.05,
            rationale="Strong momentum",
            last_reviewed_at=_now(),
            last_reviewed_decision="buy",
            last_reviewed_reason="Initial entry.",
            horizon="swing",        # removed in iter-3 — must be rejected
        )

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.strategist.schema import PositionThesis, StrategistDecision
from config.strategist import get_strategist_config


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
    pt = PositionThesis(
        ticker="AAPL",
        opened_at=_now(),
        opened_price=200.0,
        opened_tag="buy_signal",
        rationale="Strong momentum",
        horizon="swing",
        last_reviewed_at=_now(),
    )
    assert pt.horizon == "swing"


def test_position_thesis_rejects_long_rationale():
    """``PositionThesis.rationale`` is capped by the *schema* cap (prompt
    cap + ``slack_percent`` headroom — see the "two-tier convention" note
    in ``src/config/strategist.py``).  Reads the live schema cap from
    config so retuning does not silently invalidate this regression.
    """

    cfg        = get_strategist_config()
    schema_cap = cfg.schema_cap(cfg.position_thesis_caps.rationale_max_chars)

    with pytest.raises(ValidationError):
        PositionThesis(
            ticker="AAPL",
            opened_at=_now(),
            opened_price=200.0,
            opened_tag="x",
            rationale="x" * (schema_cap + 1),  # one char over the *schema* (slack-applied) cap
            horizon="swing",
            last_reviewed_at=_now(),
        )

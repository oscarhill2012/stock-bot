from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from agents.strategist.schema import PositionThesis, StrategistDecision


def _now():
    return datetime.now(tz=timezone.utc)


def test_strategist_decision_rejects_long_reasoning():
    with pytest.raises(ValidationError):
        StrategistDecision(
            target_weights={"AAPL": 0.1},
            decision_tag="test",
            reasoning="x" * 301,
            updated_thesis="ok",
            confidence=0.7,
        )


def test_strategist_decision_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        StrategistDecision(
            target_weights={},
            decision_tag="test",
            reasoning="ok",
            updated_thesis="ok",
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
    with pytest.raises(ValidationError):
        PositionThesis(
            ticker="AAPL",
            opened_at=_now(),
            opened_price=200.0,
            opened_tag="x",
            rationale="x" * 401,
            horizon="swing",
            last_reviewed_at=_now(),
        )

"""Strategist contract checks for position open/close transitions."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from orchestrator.state import MIN_HELD_WEIGHT


class StrategistContractViolation(RuntimeError):
    """Strategist failed to honour position-lifecycle invariants."""


def validate_lifecycle_contract(
    *,
    new_weights: dict[str, float],
    current_weights: dict[str, float],
    new_positions: dict[str, Any],
    close_reasons: dict[str, str],
) -> None:
    for t, new_w in new_weights.items():
        was_open = current_weights.get(t, 0.0) >= MIN_HELD_WEIGHT
        will_be_open = new_w >= MIN_HELD_WEIGHT
        if not was_open and will_be_open and t not in new_positions:
            raise StrategistContractViolation(
                f"Opening {t} (current 0 -> {new_w}) without PositionThesis"
            )
        if was_open and not will_be_open and t not in close_reasons:
            raise StrategistContractViolation(
                f"Closing {t} ({current_weights.get(t)} -> {new_w}) without close_reason"
            )


def _stub_position_thesis(ticker: str):
    """Test helper. Real PositionThesis comes in Phase F."""
    from pydantic import BaseModel

    class _PositionThesisStub(BaseModel):
        ticker: str
        opened_at: datetime
        opened_price: float = 0.0
        opened_tag: str = "test"
        rationale: str = ""
        horizon: str = "swing"
        last_reviewed_at: datetime
        last_review_note: str = ""

    return _PositionThesisStub(
        ticker=ticker,
        opened_at=datetime.now(tz=UTC),
        last_reviewed_at=datetime.now(tz=UTC),
    )

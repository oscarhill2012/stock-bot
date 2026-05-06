"""The risk gate's clamping steps, in fixed order."""
from __future__ import annotations

from orchestrator.state import (
    CASH_FLOOR_WEIGHT,
    MAX_DELTA_PER_TICKER,
    MAX_POSITION_WEIGHT,
    MAX_TOTAL_TURNOVER,
    ClampRecord,
)


def _clamp_negatives(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    for t, w in list(weights.items()):
        if w < 0:
            clamps.append(ClampRecord(rule="no_short", ticker=t, before=w, after=0.0))
            weights[t] = 0.0

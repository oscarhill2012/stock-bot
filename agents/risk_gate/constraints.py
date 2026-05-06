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


def _clamp_max_position(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    for t, w in list(weights.items()):
        if w > MAX_POSITION_WEIGHT:
            clamps.append(
                ClampRecord(rule="max_position", ticker=t, before=w, after=MAX_POSITION_WEIGHT)
            )
            weights[t] = MAX_POSITION_WEIGHT


def _clamp_cash_floor(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    total = sum(weights.values())
    threshold = 1.0 - CASH_FLOOR_WEIGHT
    if total <= threshold:
        return
    scale = threshold / total
    for t in list(weights.keys()):
        before = weights[t]
        after = before * scale
        if before != after:
            clamps.append(
                ClampRecord(rule="cash_floor", ticker=t, before=before, after=after)
            )
            weights[t] = after


def _clamp_max_delta(
    proposed: dict[str, float],
    current: dict[str, float],
    clamps: list[ClampRecord],
) -> None:
    for t, p in list(proposed.items()):
        c = current.get(t, 0.0)
        delta = p - c
        if abs(delta) > MAX_DELTA_PER_TICKER:
            capped = MAX_DELTA_PER_TICKER if delta > 0 else -MAX_DELTA_PER_TICKER
            new_w = c + capped
            clamps.append(
                ClampRecord(rule="max_delta", ticker=t, before=p, after=new_w)
            )
            proposed[t] = new_w

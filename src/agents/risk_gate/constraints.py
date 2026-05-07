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
    """Zero out any short positions — bot is long-only."""
    for t, w in list(weights.items()):
        if w < 0:
            clamps.append(ClampRecord(rule="no_short", ticker=t, before=w, after=0.0))
            weights[t] = 0.0


def _clamp_max_position(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    """Cap any single ticker at MAX_POSITION_WEIGHT to limit concentration risk."""
    for t, w in list(weights.items()):
        if w > MAX_POSITION_WEIGHT:
            clamps.append(
                ClampRecord(rule="max_position", ticker=t, before=w, after=MAX_POSITION_WEIGHT)
            )
            weights[t] = MAX_POSITION_WEIGHT


def _clamp_cash_floor(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    """Scale all weights down proportionally so at least CASH_FLOOR_WEIGHT stays as cash."""
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
    """Limit the per-ticker weight change each tick to prevent sudden large swings."""
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


def _clamp_max_turnover(
    proposed: dict[str, float],
    current: dict[str, float],
    clamps: list[ClampRecord],
) -> None:
    """Scale all deltas so total portfolio turnover stays within MAX_TOTAL_TURNOVER."""
    deltas = {t: proposed[t] - current.get(t, 0.0) for t in proposed}
    turnover = sum(abs(d) for d in deltas.values())
    if turnover <= MAX_TOTAL_TURNOVER:
        return

    scale = MAX_TOTAL_TURNOVER / turnover
    for t in list(proposed.keys()):
        before = proposed[t]
        new_delta = deltas[t] * scale
        after = current.get(t, 0.0) + new_delta
        if before != after:
            clamps.append(
                ClampRecord(rule="max_turnover", ticker=t, before=before, after=after)
            )
            proposed[t] = after


def apply_constraints(
    proposed: dict[str, float],
    current: dict[str, float],
) -> list[ClampRecord]:
    """Mutate `proposed` in-place to satisfy all hard rules. Returns clamp telemetry.

    Rules are applied in order: negatives → max position → cash floor →
    max delta → max turnover. Each step may further constrain the weights
    that a previous step already modified.
    """
    clamps: list[ClampRecord] = []
    _clamp_negatives(proposed, clamps)
    _clamp_max_position(proposed, clamps)
    _clamp_cash_floor(proposed, clamps)
    _clamp_max_delta(proposed, current, clamps)
    _clamp_max_turnover(proposed, current, clamps)
    return clamps

"""The risk gate's clamping steps, in fixed order.

Two categories of constraint live here:

1. **Stance-level** — ``apply_buy_delta_clamp`` fires on ``TickerStance``
   objects before the target weights are written into the proposed dict.
   This is defence-in-depth against callers that bypass the schema-level
   validator (e.g. via ``model_construct``); both layers read the same
   ``max_delta_per_buy`` value from ``config/risk_gate.json``.

2. **Weight-level** — ``apply_constraints`` operates on the proposed
   ``{ticker: float}`` weight dict and enforces concentration, cash-floor,
   and turnover caps.  There is no per-ticker net-delta cap: sells are
   intentionally unrestricted on a per-stance basis and the buy direction
   is already bounded upstream by ``apply_buy_delta_clamp``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.state import (
    CASH_FLOOR_WEIGHT,
    MAX_POSITION_WEIGHT,
    MAX_TOTAL_TURNOVER,
    ClampRecord,
)

if TYPE_CHECKING:
    # Avoid a hard circular import at runtime — only needed for type hints.
    from agents.strategist.stance_schema import TickerStance
    from config.risk_gate import RiskGateConfig


def apply_buy_delta_clamp(
    stances: list["TickerStance"],
    config: "RiskGateConfig",
) -> list[ClampRecord]:
    """Clamp any buy stance whose weight exceeds the configured per-trade cap.

    This is the risk-gate's defence-in-depth layer.  ``TickerStance`` already
    forbids ``weight > 0.05`` at construction time, but that validation is
    bypassable via ``model_construct``.  This function catches any leakage.

    Mutates each offending stance's ``weight`` in-place.

    Parameters
    ----------
    stances:
        The list of ``TickerStance`` objects emitted by the strategist.
        Only stances with ``intent == "buy"`` are inspected; others pass
        through unchanged.
    config:
        Loaded ``RiskGateConfig`` — supplies ``max_delta_per_buy``.

    Returns
    -------
    list[ClampRecord]
        One ``ClampRecord(rule='buy_delta_exceeded')`` for each stance that
        was clamped.  Empty if no stances exceeded the cap.
    """
    cap = config.max_delta_per_buy
    clamps: list[ClampRecord] = []

    for stance in stances:
        # Only buy stances carry a weight delta; sell and update pass through.
        if stance.intent != "buy":
            continue

        if stance.weight is not None and stance.weight > cap:
            clamps.append(
                ClampRecord(
                    rule="buy_delta_exceeded",
                    ticker=stance.ticker,
                    before=stance.weight,
                    after=cap,
                )
            )
            # Mutate in-place — the stance object is used directly by the
            # caller to build the proposed-weights dict.
            stance.weight = cap

    return clamps


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
    max turnover.  Each step may further constrain the weights that a
    previous step already modified.  The per-buy delta cap fires earlier
    in the pipeline (``apply_buy_delta_clamp``) and is not repeated here.
    """
    clamps: list[ClampRecord] = []
    _clamp_negatives(proposed, clamps)
    _clamp_max_position(proposed, clamps)
    _clamp_cash_floor(proposed, clamps)
    _clamp_max_turnover(proposed, current, clamps)
    return clamps

"""The risk gate's clamping steps, in fixed order.

A single entry point — ``apply_constraints`` — owns the full clamp sequence:

1. **Stance-level buy-delta** — fires first on ``TickerStance`` objects.
   This is defence-in-depth against callers that bypass the schema-level
   validator (e.g. via ``model_construct``); both layers read the same
   ``max_delta_per_buy`` value from ``config/risk_gate.json``.  Only buy
   stances carry a weight delta; sell and update stances pass through
   unchanged.

2. **Weight-level constraints** — operates on the proposed
   ``{ticker: float}`` weight dict and enforces four hard rules in order:
   no-short, concentration cap, cash floor, and total-turnover cap.
   There is no per-ticker net-delta cap: sells are intentionally
   unrestricted on a per-stance basis; the buy direction is bounded by
   the buy-delta step above.
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


def _clamp_buy_deltas(
    stances: list[TickerStance],
    config: RiskGateConfig,
    clamps: list[ClampRecord],
) -> None:
    """Clamp any buy stance whose weight exceeds the configured per-trade cap.

    This is the risk-gate's defence-in-depth layer.  ``TickerStance`` already
    forbids ``weight > max_delta_per_buy`` at construction time, but that
    validation is bypassable via ``model_construct``.  This helper catches any
    leakage and mutates each offending stance's ``weight`` in-place.

    Parameters
    ----------
    stances:
        The list of ``TickerStance`` objects emitted by the strategist.
        Only stances with ``intent == "buy"`` are inspected; others pass
        through unchanged.
    config:
        Loaded ``RiskGateConfig`` — supplies ``max_delta_per_buy``.
    clamps:
        The shared clamp-record accumulator.  One
        ``ClampRecord(rule='buy_delta_exceeded')`` is appended for each
        stance that was clamped.
    """
    cap = config.max_delta_per_buy

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
    *,
    stances: list[TickerStance],
    config: RiskGateConfig,
) -> list[ClampRecord]:
    """Mutate ``proposed`` in-place to satisfy all hard rules. Returns clamp telemetry.

    Rules are applied in fixed order:

    1. **Buy-delta** (stance-level) — mutates ``stance.weight`` in-place for
       any buy stance exceeding ``config.max_delta_per_buy``; does NOT touch
       the ``proposed`` dict (the two are intentionally decoupled).
       Emits ``buy_delta_exceeded`` records.
    2. **No-short** — zeros any negative weight in ``proposed``.
    3. **Max position** — caps each ticker at ``MAX_POSITION_WEIGHT``.
    4. **Cash floor** — scales all weights so at least ``CASH_FLOOR_WEIGHT``
       remains as cash.
    5. **Max turnover** — scales deltas so total one-tick churn stays within
       ``MAX_TOTAL_TURNOVER``.

    Parameters
    ----------
    proposed:
        Target weight dict ``{ticker: float}``.  Mutated in-place by the
        weight-level rules.
    current:
        Current portfolio weight dict.  Read-only — used for turnover
        delta calculations.
    stances:
        List of ``TickerStance`` objects from the strategist.  The
        buy-delta step mutates weights in-place; non-buy stances are
        skipped.  Pass ``[]`` when there are no stances (buy-delta becomes
        a no-op).  Required keyword argument — omitting it would silently
        bypass the buy-delta clamp, which is the project's recurring
        silent-degradation bug class.
    config:
        Loaded ``RiskGateConfig`` — supplies ``max_delta_per_buy`` and the
        other threshold values.  Required keyword argument for the same
        reason as ``stances``.

    Returns
    -------
    list[ClampRecord]
        All clamp records in order: ``buy_delta_exceeded`` records first,
        then ``no_short``, ``max_position``, ``cash_floor``, ``max_turnover``.
        Empty if no constraint fired.
    """
    clamps: list[ClampRecord] = []

    # Step 1 — buy-delta (stance-level, defence-in-depth).
    # Must run first so records appear before the weight-level records in the
    # returned list (Guardrail 2).  Note: this mutates stance.weight but does
    # NOT touch proposed — the two are intentionally decoupled (Guardrail 1).
    _clamp_buy_deltas(stances, config, clamps)

    # Steps 2–5 — weight-level constraints, in documented order.
    _clamp_negatives(proposed, clamps)
    _clamp_max_position(proposed, clamps)
    _clamp_cash_floor(proposed, clamps)
    _clamp_max_turnover(proposed, current, clamps)

    return clamps

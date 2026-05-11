"""Derive legacy decision fields from per-ticker stances.

The strategist's after-callback runs ``derive_legacy_fields`` to populate
``StrategistDecision.target_weights`` / ``new_positions`` / ``close_reasons`` /
``trim_reasons`` from the LLM-emitted ``stances``. Downstream agents (risk_gate,
executor, memory_writer) keep their existing input shape, so this function acts
as the translation layer between the richer per-ticker stance model and the
flat legacy fields.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.schema import PositionThesis
from agents.strategist.stance_schema import TickerStance


@dataclass(frozen=True)
class TickContext:
    """Inputs the derivation needs that aren't carried on the stance itself.

    Args:
        tick_id: Unique identifier for this decision tick (e.g. ``"tick_042"``).
        decision_tag: Snake-case label attached to this tick's decision
            (e.g. ``"morning_sweep_2026_05_08"``).
        now: Timestamp of the current tick, used as ``opened_at`` for new positions.
        current_prices: Mapping of ticker → current market price. Used to
            populate ``PositionThesis.opened_price`` when opening a position.
        current_weights: Mapping of ticker → current portfolio weight. Used to
            determine the lifecycle action (open / close / trim / add / hold).
    """

    tick_id: str
    decision_tag: str
    now: datetime
    current_prices: dict[str, float]
    current_weights: dict[str, float]


@dataclass(frozen=True)
class DerivedFields:
    """The four-dict output shape consumed by existing downstream agents.

    Args:
        target_weights: Target portfolio weight for every stance ticker,
            including holds (weight unchanged) and closes (weight → 0.0).
        new_positions: Newly opened positions, keyed by ticker. Only populated
            for ``open`` lifecycle actions.
        close_reasons: Human-readable reason for each full exit, keyed by ticker.
            Only populated when the stance supplies a ``close_reason``.
        trim_reasons: Human-readable reason for each partial size reduction,
            keyed by ticker. Only populated when the stance supplies a ``trim_reason``.

    Note:
        ``frozen=True`` prevents field reassignment but does not deep-freeze the
        dict contents; callers should treat the four dicts as read-only by
        convention.
    """

    target_weights: dict[str, float]
    new_positions: dict[str, PositionThesis]
    close_reasons: dict[str, str]
    trim_reasons: dict[str, str]


def derive_legacy_fields(
    stances: Iterable[TickerStance],
    ctx: TickContext,
) -> DerivedFields:
    """Translate a list of per-ticker stances into the legacy flat decision fields.

    This function is **pure** — it reads from ``stances`` and ``ctx`` and returns
    a ``DerivedFields`` snapshot with no side effects. It is called from the
    strategist's after-callback (C9), which is responsible for validating the
    stances and ensuring ``ctx.current_prices`` is populated before calling here.

    Each stance is processed independently:

    - ``target_weights`` is populated for *every* stance regardless of action,
      including holds.
    - ``new_positions`` fires only on ``"open"`` (current weight flat →
      preferred weight live).  The ``horizon`` field on ``PositionThesis`` is
      required and non-Optional, so ``stance.horizon or "swing"`` provides a
      safe fallback if the LLM omitted it; the after-callback in C9 will
      reprompt for a proper value before persisting in production.
    - ``close_reasons`` fires only on ``"close"`` and only when the stance
      actually carries a ``close_reason``.  An empty ``close_reason`` on a
      close action is a silent skip here; the after-callback rejects such
      output before calling derivation in production.
    - ``trim_reasons`` mirrors ``close_reasons`` for the ``"trim"`` action.
    - ``"add"`` and ``"hold"`` actions only contribute to ``target_weights``.

    Parameters
    ----------
    stances:
        Iterable of ``TickerStance`` objects — one per watchlist ticker per tick.
    ctx:
        ``TickContext`` carrying tick metadata and current portfolio state.

    Returns
    -------
    DerivedFields
        Frozen snapshot of the four derived dicts, ready to merge into
        ``StrategistDecision``.
    """
    target_weights: dict[str, float] = {}
    new_positions: dict[str, PositionThesis] = {}
    close_reasons: dict[str, str] = {}
    trim_reasons: dict[str, str] = {}

    for stance in stances:

        # Every stance contributes its preferred weight regardless of action.
        target_weights[stance.ticker] = stance.preferred_weight

        # Determine what needs to happen based on current vs preferred weight.
        current = ctx.current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(current, stance.preferred_weight)

        if action == "open":
            # Construct a PositionThesis for the newly opened position.
            # `stance.horizon or "swing"` is a defensive fallback: PositionThesis
            # requires a non-None Literal horizon, but the LLM may omit it on an
            # open stance.  The after-callback will reprompt in production; here we
            # default to "swing" to keep the function total (never raises).
            opened_price = ctx.current_prices.get(stance.ticker, 0.0)
            new_positions[stance.ticker] = PositionThesis(
                ticker=stance.ticker,
                opened_at=ctx.now,
                opened_price=opened_price,
                opened_tag=ctx.decision_tag,
                rationale=stance.rationale,
                horizon=stance.horizon or "swing",
                target_price=stance.target_price,
                stop_price=stance.stop_price,
                catalyst=stance.catalyst,
                last_reviewed_at=ctx.now,
                last_review_note="",
                opened_tick_id=ctx.tick_id,
            )

        elif action == "close" and stance.close_reason:
            # Record the exit reason; silently skip if close_reason is absent.
            close_reasons[stance.ticker] = stance.close_reason

        elif action == "trim" and stance.trim_reason:
            # Record the trim reason; silently skip if trim_reason is absent.
            trim_reasons[stance.ticker] = stance.trim_reason

        # "add" and "hold" actions: target_weights already set above; nothing else needed.

    return DerivedFields(
        target_weights=target_weights,
        new_positions=new_positions,
        close_reasons=close_reasons,
        trim_reasons=trim_reasons,
    )

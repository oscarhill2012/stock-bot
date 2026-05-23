"""Derive legacy decision fields from per-ticker stances.

The strategist's after-callback runs ``derive_legacy_fields`` to populate
``StrategistDecision.target_weights`` / ``close_reasons`` / ``trim_reasons``
from the LLM-emitted ``stances``. Downstream agents (risk_gate, executor,
memory_writer) keep their existing input shape, so this function acts as the
translation layer between the richer per-ticker stance model and the flat
legacy fields.

``new_positions`` was a derived field that pre-computed a ``PositionThesis``
for every ``open`` stance at decision time.  It was removed in Band 6: the
executor now assembles the thesis itself from the fill price + stance via
``apply_stance_to_thesis``.  The strategist never had an honest fill price, so
this was always a leaky abstraction.

``StrategistContractViolation`` lives here (rather than
``agents.risk_gate.lifecycle``) because it is raised by the strategist's own
validation callback and should be co-located with the derivation it guards.
``agents.risk_gate.lifecycle`` is deleted in Band 6; all importers now point
here.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.stance_schema import TickerStance
from orchestrator.state import ORDER_EPSILON


class StrategistContractViolation(RuntimeError):
    """Raised when the strategist's output violates a position-lifecycle invariant.

    The strategist's after-callback raises this for off-watchlist tickers, missing
    close_reasons, or missing trim_reasons.  The risk_gate also raises this if a
    close slips through without a reason.  Callers (pipeline, backtest runner)
    should treat this as a hard tick failure.
    """


def derive_decision_tag(*, prior: float, new: float) -> str:
    """Categorise a (prior, new) weight pair as one of six decision tags.

    | Tag        | Condition                                          |
    |------------|----------------------------------------------------|
    | entry      | prior ≈ 0 AND new > 0                              |
    | ramp       | 0 < prior < new                                    |
    | trim       | prior > new > 0                                    |
    | exit       | prior > 0 AND new ≈ 0                              |
    | hold_flat  | prior ≈ 0 AND new ≈ 0                              |
    | hold       | prior == new AND prior > 0                         |

    ``ORDER_EPSILON`` (1e-6) is the zero threshold so dust positions do
    not flip exit/entry into trim/ramp.  Downstream Spec B / Spec C
    memory writers use this tag as the intent key, giving each ticker a
    discriminating signal rather than the constant ``catalyst_driven_entry``
    the LLM was emitting for every tick.

    Args:
        prior: The ticker's current portfolio weight before this tick.
        new:   The ticker's preferred weight after this tick.

    Returns:
        One of: ``"entry"``, ``"ramp"``, ``"trim"``, ``"exit"``,
        ``"hold_flat"``, or ``"hold"``.
    """

    prior_zero = prior < ORDER_EPSILON
    new_zero   = new   < ORDER_EPSILON

    if prior_zero and new_zero:
        return "hold_flat"
    if prior_zero and not new_zero:
        return "entry"
    if not prior_zero and new_zero:
        return "exit"

    if new > prior:
        return "ramp"
    if new < prior:
        return "trim"
    return "hold"


@dataclass(frozen=True)
class TickContext:
    """Inputs the derivation needs that aren't carried on the stance itself.

    Args:
        tick_id: Unique identifier for this decision tick (e.g. ``"tick_042"``).
        decision_tag: Snake-case label attached to this tick's decision
            (e.g. ``"morning_sweep_2026_05_08"``).
        now: Timestamp of the current tick, used as ``opened_at`` for new positions.
        current_weights: Mapping of ticker → current portfolio weight. Used to
            determine the lifecycle action (open / close / trim / add / hold).
        watchlist: The full watchlist for this tick.  Derivation pads
            ``target_weights`` for every watchlist ticker so downstream
            agents (risk_gate, executor) always see an exhaustive dict:
            tickers the strategist did not emit a stance for are filled
            with their current weight (held → carry-forward; flat → 0.0).

    Note:
        ``current_prices`` deliberately omitted: the strategist no longer
        stamps ``opened_price`` on freshly-opened ``PositionThesis``
        rows.  That field is the executor's responsibility now, since
        the strategist runs before the order fills and has no honest
        price to record.  See ``PositionThesis`` docstring.
    """

    tick_id: str
    decision_tag: str
    now: datetime
    current_weights: dict[str, float]
    watchlist: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DerivedFields:
    """Derived output shape consumed by downstream agents.

    ``new_positions`` was removed in Band 6: the executor now assembles the
    ``PositionThesis`` itself from the fill price + stance after the broker
    confirms the BUY.  The strategist never had an honest fill price, so
    pre-computing it here was always a leaky abstraction.

    Args:
        target_weights: Target portfolio weight for every stance ticker,
            including holds (weight unchanged) and closes (weight → 0.0).
        close_reasons: Human-readable reason for each full exit, keyed by ticker.
            Only populated when the stance supplies a ``close_reason``.
        trim_reasons: Human-readable reason for each partial size reduction,
            keyed by ticker. Only populated when the stance supplies a ``trim_reason``.
        decision_tags: Per-ticker intent tag derived from (prior, new) weight pair.

    Note:
        ``frozen=True`` prevents field reassignment but does not deep-freeze the
        dict contents; callers should treat the dicts as read-only by convention.
    """

    target_weights: dict[str, float]
    close_reasons: dict[str, str]
    trim_reasons: dict[str, str]
    decision_tags: dict[str, str]
    """Per-ticker intent tag derived from (prior, new) weight — one of:
    ``entry``, ``ramp``, ``trim``, ``exit``, ``hold_flat``, ``hold``.
    Spec B / Spec C memory writers use this as a discriminating intent key
    (S6 — replaces the constant ``catalyst_driven_entry`` the LLM emitted).
    Carry-forward tickers are included with their implicit action tag.
    """


def derive_legacy_fields(
    stances: Iterable[TickerStance],
    ctx: TickContext,
) -> DerivedFields:
    """Translate a list of per-ticker stances into the derived decision fields.

    This function is **pure** — it reads from ``stances`` and ``ctx`` and returns
    a ``DerivedFields`` snapshot with no side effects. It is called from the
    strategist's after-callback (C9), which is responsible for validating the
    stances before calling here.

    Active-stances model:

        The strategist only emits stances for tickers it wants to *change*
        (open / add / trim / close).  Any watchlist ticker the strategist
        does NOT emit a stance for is treated as carry-forward — held
        positions stay held at their current weight; flat tickers stay
        flat.  Derivation pads ``target_weights`` accordingly so
        downstream agents (risk_gate, executor) always see an exhaustive
        dict.

    Each emitted stance is processed independently:

    - ``target_weights`` is populated for *every* emitted stance regardless
      of action.
    - ``close_reasons`` fires only on ``"close"`` and only when the stance
      actually carries a ``close_reason``.  An empty ``close_reason`` on a
      close action is a silent skip here; the after-callback rejects such
      output before calling derivation in production.
    - ``trim_reasons`` mirrors ``close_reasons`` for the ``"trim"`` action.
    - ``"open"``, ``"add"``, and ``"hold"`` actions only contribute to
      ``target_weights``.

    Note: ``new_positions`` was removed in Band 6.  The executor now assembles
    the ``PositionThesis`` for each ``open`` stance itself, using
    ``apply_stance_to_thesis`` from ``executor._verb_dispatch`` with the real
    fill price from the broker.  Pre-computing it here was always wrong because
    the strategist runs before the order fills and has no honest fill price.

    Then ``target_weights`` is padded for un-emitted watchlist tickers
    using carry-forward semantics (current weight if held; 0.0 if flat).

    Parameters
    ----------
    stances:
        Iterable of ``TickerStance`` objects — one per *active* ticker for
        this tick (not every watchlist ticker; omissions = carry-forward).
    ctx:
        ``TickContext`` carrying tick metadata, current portfolio state,
        and the full watchlist used for carry-forward padding.

    Returns
    -------
    DerivedFields
        Frozen snapshot of the derived dicts, ready to merge into
        ``StrategistDecision``.
    """
    target_weights: dict[str, float] = {}
    close_reasons: dict[str, str] = {}
    trim_reasons: dict[str, str] = {}
    decision_tags: dict[str, str] = {}

    # ── Pass 1: emitted stances ───────────────────────────────────────────────
    # Whatever the strategist explicitly said about a ticker takes precedence
    # over the carry-forward default applied in Pass 2 below.
    emitted: set[str] = set()
    for stance in stances:

        emitted.add(stance.ticker)

        # Every stance contributes its preferred weight regardless of action.
        target_weights[stance.ticker] = stance.preferred_weight

        # Determine what needs to happen based on current vs preferred weight.
        current = ctx.current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(current, stance.preferred_weight)

        # S6: derive a per-ticker intent tag from the (prior, new) weight pair.
        # This replaces the constant ``catalyst_driven_entry`` the LLM was emitting
        # for every tick — giving Spec B / Spec C memory writers a discriminating
        # key they can actually use to distinguish entries from exits, holds, etc.
        decision_tags[stance.ticker] = derive_decision_tag(
            prior=current,
            new=stance.preferred_weight,
        )

        # ``open`` and ``add`` stances only need target_weights set above.
        # The executor assembles the PositionThesis for ``open`` stances
        # itself using apply_stance_to_thesis + the real fill price.

        if action == "close" and stance.close_reason:
            # Record the exit reason; silently skip if close_reason is absent.
            close_reasons[stance.ticker] = stance.close_reason

        elif action == "trim" and stance.trim_reason:
            # Record the trim reason; silently skip if trim_reason is absent.
            trim_reasons[stance.ticker] = stance.trim_reason

        # "add" and "hold" actions: target_weights already set above; nothing else needed.

    # ── Pass 2: carry-forward padding ────────────────────────────────────────
    # Any watchlist ticker the strategist did NOT emit a stance for keeps its
    # current weight (held → continue holding; flat → continue flat).  This
    # is what makes "omission = implicit hold" safe — downstream sees a full
    # target_weights dict and the risk_gate / executor do not need to know
    # the difference between "explicit hold" and "implicit hold".
    for ticker in ctx.watchlist:
        if ticker not in emitted:
            carry_weight = ctx.current_weights.get(ticker, 0.0)
            target_weights[ticker] = carry_weight

            # S6: carry-forward tickers also get a decision tag so downstream
            # agents have a complete per-ticker intent map for every watchlist
            # entry, not just the ones the strategist explicitly emitted a stance for.
            decision_tags[ticker] = derive_decision_tag(
                prior=carry_weight,
                new=carry_weight,
            )

    return DerivedFields(
        target_weights=target_weights,
        close_reasons=close_reasons,
        trim_reasons=trim_reasons,
        decision_tags=decision_tags,
    )

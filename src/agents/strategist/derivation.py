"""Derive canonical decision fields from per-ticker stances.

The strategist's after-callback runs ``derive_decision_fields`` to populate
``StrategistDecision.target_weights`` / ``close_reasons`` / ``trim_reasons``
from the LLM-emitted ``stances``.  Downstream agents (risk_gate, executor,
memory_writer) keep their existing input shape, so this function acts as the
translation layer between the richer per-ticker stance model and the flat
derived fields.

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

from agents.strategist.stance_schema import TickerStance
from orchestrator.state import ORDER_EPSILON


class StrategistContractViolation(RuntimeError):
    """Raised when the strategist's output violates a position-lifecycle invariant.

    The strategist's after-callback raises this for off-watchlist tickers, missing
    close reasons, or missing trim reasons.  The risk_gate also raises this if a
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
            verify that held tickers have explicit stances (Spec B / D3).
        watchlist: The full watchlist for this tick.  Derivation pads
            ``target_weights`` for every watchlist ticker so downstream
            agents (risk_gate, executor) always see an exhaustive dict.
            Flat tickers (current weight ≈ 0) the strategist did not emit
            a stance for are padded to 0.0.  Held tickers MUST have an
            explicit stance — omission raises ``StrategistContractViolation``
            (Spec B / D3).

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
            Only populated when the stance supplies a ``reason`` with ``intent=="close"``.
        trim_reasons: Human-readable reason for each partial size reduction,
            keyed by ticker. Only populated when the stance supplies a ``reason``
            with ``intent=="trim"``.
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


def derive_decision_fields(
    stances: Iterable[TickerStance],
    ctx: TickContext,
) -> DerivedFields:
    """Translate a list of per-ticker stances into the derived decision fields.

    This function is **pure** — it reads from ``stances`` and ``ctx`` and returns
    a ``DerivedFields`` snapshot with no side effects. It is called from the
    strategist's after-callback (C9), which is responsible for validating the
    stances before calling here.

    Reads ``stance.intent`` as the canonical action verb and ``stance.weight``
    as the target portfolio weight.  The legacy ``preferred_weight`` field is
    no longer consulted — ``intent is None`` on any stance raises
    ``StrategistContractViolation`` immediately (silent legacy-path fallback
    is the recurring bug class, per ``feedback_silent_failures_loud_tests``).

    Active-stances model (Spec B / D3):

        The strategist MUST emit a stance for every pre-tick held ticker
        (open / add / trim / close / hold / update).  Silent carry-forward
        of held positions is no longer permitted; omitting a held ticker
        raises ``StrategistContractViolation``.

        Flat watchlist tickers (current weight ≈ 0) remain optional — the
        strategist only needs to emit a stance when it wants to *change* a
        flat ticker's exposure (open / add).  Flat tickers the strategist
        does NOT mention are padded to 0.0 in Pass 2 so downstream agents
        always see an exhaustive ``target_weights`` dict.

    Each emitted stance is processed independently:

    - ``target_weights`` is populated for *every* emitted stance.
      Opens/adds/trims write ``stance.weight``; closes write ``0.0``
      (the full exit); holds/updates write ``stance.weight or 0.0``
      (preserving the current weight where weight is not re-stated).
    - ``close_reasons`` fires only on ``"close"`` and requires a non-empty
      ``stance.reason`` — raising ``StrategistContractViolation`` if absent,
      because silent exits without a reason are forbidden audit failures.
    - ``trim_reasons`` mirrors ``close_reasons`` for the ``"trim"`` action.
    - ``"open"``, ``"add"``, ``"hold"``, and ``"update"`` actions only
      contribute to ``target_weights``.

    Note: ``new_positions`` was removed in Band 6.  The executor now assembles
    the ``PositionThesis`` for each ``open`` stance itself, using
    ``apply_stance_to_thesis`` from ``executor._verb_dispatch`` with the real
    fill price from the broker.  Pre-computing it here was always wrong because
    the strategist runs before the order fills and has no honest fill price.

    Then ``target_weights`` is padded for un-emitted FLAT watchlist tickers
    to 0.0 (the active-stances model).  Held tickers must have been covered
    by an explicit stance — Pass 1.5 enforces this with
    ``StrategistContractViolation`` (Spec B / D3).

    Parameters
    ----------
    stances:
        Iterable of ``TickerStance`` objects — one per *active* ticker for
        this tick (not every watchlist ticker; omissions = carry-forward for
        flat tickers only).
    ctx:
        ``TickContext`` carrying tick metadata, current portfolio state,
        and the full watchlist used for carry-forward padding.

    Returns
    -------
    DerivedFields
        Frozen snapshot of the derived dicts, ready to merge into
        ``StrategistDecision``.

    Raises
    ------
    StrategistContractViolation
        - When any stance has ``intent is None`` (no silent legacy fallback).
        - When a ``"close"`` stance has no ``reason`` (audit requirement).
        - When a ``"trim"`` stance has no ``reason`` (audit requirement).
        - When a pre-tick held ticker has no stance (Spec B / D3).
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

        # Guard: intent MUST be present — no silent legacy-path fallback.
        # Silently falling through to preferred_weight was the recurring bug class
        # (see auto-memory feedback_silent_failures_loud_tests).
        if stance.intent is None:
            raise StrategistContractViolation(
                f"Stance for {stance.ticker!r} has intent=None.  "
                f"Every stance must carry an explicit intent verb "
                f"(open / add / trim / close / hold / update)."
            )

        # Read the canonical action directly from intent — no weight comparison.
        action = stance.intent

        # Derive the new target weight from the stance's explicit weight field.
        # Closes always target 0.0 (full exit); holds/updates use current weight
        # if no new weight was stated (weight=None); opens/adds/trims use weight.
        if action == "close":
            target_weights[stance.ticker] = 0.0

        else:
            # ``or 0.0`` handles close/hold/update where weight is None —
            # the risk-gate and executor both tolerate 0.0 as "no change".
            target_weights[stance.ticker] = stance.weight or 0.0

        # S6: derive a per-ticker intent tag from the (prior, new) weight pair.
        # Replaces the constant ``catalyst_driven_entry`` the LLM emitted for
        # every tick — gives Spec B / Spec C memory writers a discriminating key.
        current = ctx.current_weights.get(stance.ticker, 0.0)

        decision_tags[stance.ticker] = derive_decision_tag(
            prior=current,
            new=target_weights[stance.ticker],
        )

        # ── Close reason — required, not optional ────────────────────────────
        # A full exit without a reason is an audit failure; raise rather than
        # silently skipping (the prior implementation used a falsy check and
        # skipped silently, which is the recurring bug class).
        if action == "close":
            if not stance.reason:
                raise StrategistContractViolation(
                    f"Stance for {stance.ticker!r} has intent='close' but "
                    f"reason is missing or empty.  Every close must articulate "
                    f"why the position is being exited so the audit trail is complete."
                )
            close_reasons[stance.ticker] = stance.reason

        # ── Trim reason — required, not optional ────────────────────────────
        # Same principle as close: a trim without a reason is a silent failure.
        elif action == "trim":
            if not stance.reason:
                raise StrategistContractViolation(
                    f"Stance for {stance.ticker!r} has intent='trim' but "
                    f"reason is missing or empty.  Every trim must articulate "
                    f"why the position size is being reduced."
                )
            trim_reasons[stance.ticker] = stance.reason

        # "open", "add", "hold", "update" only contribute to target_weights above.

    # ── Pass 1.5: stance required per held position (Spec B / D3) ────────────
    # Every pre-tick held ticker MUST have been touched by a stance above.
    # Silent carry-forward of held positions is no longer permitted — the
    # strategist must explicitly engage with each held position on every
    # tick (Principle 3 of the spec).  Flat tickers remain optional (the
    # active-stances model survives for them — Pass 2 below).
    #
    # The threshold is ``ORDER_EPSILON`` (1e-6), not strict ``> 0.0``: a
    # broker-side close can leave a sub-epsilon dust quantity (e.g. 3.55e-15
    # observed on AMD post-close 2026-05-25) whose weight reads as positive
    # under strict comparison but is operationally flat.  The shim's
    # ``user:positions`` thesis-registry view of "held" already implicitly
    # filters dust by removing closed theses, so without this epsilon the
    # two layers disagree and the strategist is rejected for missing a
    # stance on a ticker the prompt never asked it to engage with.  See
    # ``docs/todo-fixes.md`` §5.3 for the broader open question on whether
    # weight-based portfolio maths is the right primitive at all.
    held_tickers = {
        t for t, w in ctx.current_weights.items() if w >= ORDER_EPSILON
    }
    uncovered_held = held_tickers - emitted

    if uncovered_held:
        # Sort for deterministic error messages — easier to grep for in logs.
        names = ", ".join(sorted(uncovered_held))
        raise StrategistContractViolation(
            f"Held position(s) {{{names}}} have no matching stance in the "
            f"strategist's output.  Every pre-tick held ticker must be "
            f"explicitly engaged with on every tick (Spec B / D3) — emit a "
            f"hold / trim / close / update stance for each."
        )

    # ── Pass 2: carry-forward padding for FLAT tickers only ──────────────────
    # Any *flat* watchlist ticker the strategist did not emit a stance for
    # keeps its current weight (0.0) — the active-stances model survives for
    # flat tickers since the LLM has no view to commit to.  Held tickers are
    # NOT padded here — Pass 1.5 above guarantees they were covered by an
    # explicit stance.
    for ticker in ctx.watchlist:
        if ticker in emitted:
            continue

        # By construction (Pass 1.5), ticker is NOT in held_tickers — so its
        # current weight is 0.0 (or absent) and we pad with 0.0.
        target_weights[ticker] = 0.0
        decision_tags[ticker]  = derive_decision_tag(prior=0.0, new=0.0)

    return DerivedFields(
        target_weights=target_weights,
        close_reasons=close_reasons,
        trim_reasons=trim_reasons,
        decision_tags=decision_tags,
    )

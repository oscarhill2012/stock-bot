"""Derive canonical decision fields from per-ticker stances.

The strategist's after-callback runs ``derive_decision_fields`` to populate
``StrategistDecision.target_weights`` from the LLM-emitted ``stances``.
Downstream agents (risk_gate, executor, memory_writer) keep their existing
input shape, so this function acts as the translation layer between the
richer per-ticker stance model and the derived weight dict.

Three-verb vocabulary (iter-3 schema rewrite)
---------------------------------------------
    buy    — additive delta; increases current weight by ``stance.weight``.
    sell   — reductive delta; absent weight = full close (target 0.0);
             present weight = reduce current by that delta (clamped ≥ 0).
    update — prose-only; no trade; carries current weight forward verbatim.

Held-ticker omission policy
----------------------------
Held tickers the strategist does NOT emit a stance for are **implicitly held**:
their current weight is carried forward in Pass 2 without error.  This replaces
the former Spec-B / D3 rule (omission raised ``StrategistContractViolation``)
which the iter-3 schema rewrite supersedes.  Flat tickers (weight ≈ 0) the
strategist does not mention are padded to 0.0 as before.

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
    """Raised when a stance has ``intent=None``.

    Every stance emitted by the strategist must carry an explicit intent verb
    (buy / sell / update).  ``derive_decision_fields`` raises this if a stance
    slips through with ``intent=None`` — the only remaining caller-side
    violation in the iter-3 schema path.  Callers (pipeline, backtest runner)
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
    not flip exit/entry into trim/ramp.  The tag was designed to give Spec B /
    Spec C memory writers a discriminating intent key rather than the constant
    ``catalyst_driven_entry`` the LLM was emitting every tick, but no downstream
    consumer currently reads ``decision_tags`` — the field is dormant scaffolding
    retained for the planned Spec B/C memory-writer path (A-097.aa).

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
        current_weights: Mapping of ticker → current portfolio weight.  Used
            to compute carry-forward weights for held and flat tickers.
        watchlist: The full watchlist for this tick.  Derivation pads
            ``target_weights`` for every watchlist ticker so downstream
            agents (risk_gate, executor) always see an exhaustive dict.
            Flat tickers (current weight ≈ 0) the strategist did not emit
            a stance for are padded to 0.0.  Held tickers with no stance
            are now implicitly held — their current weight carries forward
            without error (iter-3 schema rewrite).
        held_tickers: Explicit set of currently-held tickers.  When provided,
            this overrides the weight-threshold computation used as a fallback
            (``current_weights ≥ ORDER_EPSILON``).  Callers that don't supply
            this get the computed set automatically.
        tick_id: Unique identifier for this decision tick (e.g. ``"tick_042"``).
            Optional — pipeline passes it; unit tests may omit it.
        decision_tag: Snake-case label attached to this tick's decision
            (e.g. ``"morning_sweep_2026_05_08"``).
            Optional — pipeline passes it; unit tests may omit it.
        now: Timestamp of the current tick, used as ``opened_at`` for new
            positions.  Optional — pipeline passes it; unit tests may omit it.

    Note:
        ``current_prices`` deliberately omitted: the strategist no longer
        stamps ``opened_price`` on freshly-opened ``PositionThesis``
        rows.  That field is the executor's responsibility now, since
        the strategist runs before the order fills and has no honest
        price to record.  See ``PositionThesis`` docstring.
    """

    current_weights: dict[str, float]
    watchlist: list[str] = field(default_factory=list)

    # Explicit held-tickers set; when None the derivation computes it from
    # current_weights using the ORDER_EPSILON threshold.
    held_tickers: set[str] | None = None

    # Pipeline-facing fields — optional so unit tests can construct a
    # minimal TickContext without threading through tick metadata.
    tick_id: str | None = None
    decision_tag: str | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class DerivedFields:
    """Derived output shape consumed by downstream agents.

    ``new_positions`` was removed in Band 6: the executor now assembles the
    ``PositionThesis`` itself from the fill price + stance after the broker
    confirms the BUY.  The strategist never had an honest fill price, so
    pre-computing it here was always a leaky abstraction.

    A-013 tail (collapsed): ``sell_reasons`` and ``update_reasons`` were
    removed — they duplicated ``TickerStance.rationale`` verbatim.  Consumers
    that need the sell prose for a ticker should find the matching stance in
    ``StrategistDecision.stances`` directly.

    Args:
        target_weights: Target portfolio weight for every stance ticker,
            including carries (weight unchanged) and closes (weight → 0.0).
            Padded for every watchlist ticker so downstream agents always see
            an exhaustive dict.
        decision_tags: Per-ticker intent tag derived from the (prior, new)
            weight pair — one of ``entry``, ``ramp``, ``trim``, ``exit``,
            ``hold_flat``, ``hold``.  Carry-forward tickers are included with
            their implicit action tag.  This field is computed but NOT currently
            consumed by any downstream code — it is dormant scaffolding retained
            for the planned Spec B/C memory-writer path; no reader exists today.

    Note:
        ``frozen=True`` prevents field reassignment but does not deep-freeze the
        dict contents; callers should treat the dicts as read-only by convention.
    """

    target_weights: dict[str, float]
    decision_tags: dict[str, str]


def derive_decision_fields(
    stances: Iterable[TickerStance],
    ctx: TickContext,
) -> DerivedFields:
    """Translate a list of per-ticker stances into the derived decision fields.

    This function is **pure** — it reads from ``stances`` and ``ctx`` and
    returns a ``DerivedFields`` snapshot with no side effects.  It is called
    from the strategist's after-callback (C9), which is responsible for
    validating the stances before calling here.

    Verb dispatch (four-verb schema)
    ---------------------------------
    Reads ``stance.intent`` as the canonical action verb:

    - ``buy``       — additive delta.  ``stance.weight`` is added to the
                      current position weight (buy is always a delta, not
                      an absolute target).  Requires weight and rationale
                      (enforced by ``TickerStance`` validator).
    - ``sell``      — reductive delta.  Absent weight ⇒ full close
                      (target 0.0); present weight ⇒ reduce current by
                      that delta, clamped ≥ 0.  The rationale lives on
                      the stance itself (A-013 tail — ``sell_reasons`` dict
                      was deleted).
    - ``update``    — prose-only revision.  No trade; current weight
                      carries forward verbatim.  The rationale lives on
                      the stance itself (``update_reasons`` dict was
                      deleted).
    - ``no_action`` — explicit "considered, no change."  No trade; current
                      weight carries forward.

    Carry-forward
    -------------
    Tickers the strategist does not emit a stance for at all still receive
    a target-weight entry padded to their current weight, so downstream
    agents always see an exhaustive ``target_weights`` dict.  Under the
    four-verb schema the strategist is expected to emit one stance per
    watchlist ticker (``no_action`` covers "no change"), so the
    carry-forward path mainly catches off-watchlist held positions.

    Note: ``new_positions`` was removed in Band 6.  The executor now assembles
    the ``PositionThesis`` for each ``buy`` stance itself, using
    ``apply_stance_to_thesis`` from ``executor._verb_dispatch`` with the real
    fill price from the broker.  Pre-computing it here was always wrong because
    the strategist runs before the order fills and has no honest fill price.

    Parameters
    ----------
    stances:
        Iterable of ``TickerStance`` objects — one per ticker the strategist
        has something to say about this tick.  Omissions are valid for both
        held (implicit hold) and flat tickers (implicit no-position).
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
        When any stance has ``intent is None`` (no silent legacy fallback;
        every stance must carry an explicit intent verb).
    """
    target_weights: dict[str, float] = {}
    decision_tags: dict[str, str] = {}

    # ── Pass 1: emitted stances ───────────────────────────────────────────────
    # Whatever the strategist explicitly said about a ticker takes precedence
    # over the carry-forward default applied in Pass 2 below.
    emitted: set[str] = set()

    for stance in stances:

        emitted.add(stance.ticker)

        # Guard: intent MUST be present — no silent legacy-path fallback.
        # Silently falling through was the recurring bug class
        # (see auto-memory feedback_silent_failures_loud_tests).
        if stance.intent is None:
            raise StrategistContractViolation(
                f"Stance for {stance.ticker!r} has intent=None.  Every stance "
                f"must carry an explicit intent (buy / sell / update / no_action)."
            )

        current = ctx.current_weights.get(stance.ticker, 0.0)

        match stance.intent:

            case "buy":
                # weight is the DELTA — increase current position by that much.
                target_weights[stance.ticker] = current + stance.weight

            case "sell":
                # weight absent ⇒ full close; weight present ⇒ reduce by delta
                # (clamped to current; risk gate will surface clamps as audit).
                # Rationale lives on the stance itself (A-013 tail — no dict).
                if stance.weight is None:
                    target_weights[stance.ticker] = 0.0
                else:
                    target_weights[stance.ticker] = max(0.0, current - stance.weight)

            case "update":
                # No trade — current weight carries forward verbatim.
                # Rationale lives on the stance itself (A-013 tail — no dict).
                target_weights[stance.ticker] = current

            case "no_action":
                # Explicit "considered, no change" — carry the current weight
                # forward verbatim, no reason to record.  The presence of the
                # stance is itself the audit signal.
                target_weights[stance.ticker] = current

        # S6: derive a per-ticker intent tag from the (prior, new) weight pair.
        # The tag was designed for Spec B/C memory writers, but no downstream
        # code currently reads ``decision_tags`` — it is computed but dormant
        # (retained for the planned Spec B/C memory-writer path, A-097.aa).
        decision_tags[stance.ticker] = derive_decision_tag(
            prior=current,
            new=target_weights[stance.ticker],
        )

    # ── Pass 1.5: resolve held_tickers ───────────────────────────────────────
    # Use the caller-supplied set if present; otherwise compute from
    # current_weights using ORDER_EPSILON as the "is held" threshold.
    #
    # The threshold avoids misidentifying sub-epsilon dust quantities (e.g.
    # 3.55e-15 observed on AMD post-close 2026-05-25) as held positions.
    if ctx.held_tickers is not None:
        held_tickers = ctx.held_tickers
    else:
        held_tickers = {
            t for t, w in ctx.current_weights.items() if w >= ORDER_EPSILON
        }

    # ── Pass 2: carry-forward for un-emitted tickers ──────────────────────────
    # Any ticker not covered by a stance above gets its current weight carried
    # forward.  For held tickers this is an implicit hold; for flat watchlist
    # tickers it pads to 0.0.  Both cases are valid under the iter-3 model —
    # the former Spec-B / D3 "error on omission" rule is intentionally removed.
    all_relevant = set(ctx.watchlist) | held_tickers

    for ticker in all_relevant:
        if ticker in emitted:
            continue

        # Carry forward the current weight (0.0 for flat tickers).
        current = ctx.current_weights.get(ticker, 0.0)
        target_weights[ticker] = current
        decision_tags[ticker]  = derive_decision_tag(prior=current, new=current)

    return DerivedFields(
        target_weights=target_weights,
        decision_tags=decision_tags,
    )

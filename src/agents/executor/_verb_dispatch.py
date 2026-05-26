"""Verb-dispatch helpers shared between Executor's run loop and its
after_agent_callback writer.

Both functions are pure — no state mutation, no I/O.  Living inside
the executor package keeps the verb semantics in exactly one place
under the agent that owns both the broker dispatch and the
persistence write.

Verb vocabulary (iter-3, three-verb canonical form)
----------------------------------------------------
    buy    — enter a flat ticker (prior_row is None) or increase an
             existing position (prior_row is present), or promote a
             watched thesis to a held position (``prior_row.is_watched``).
    sell   — reduce or fully close a position.  ``stance.weight``
             present ⇒ partial trim to that weight; absent ⇒ full
             close (caller drops the ticker).  Raises if prior_row is
             None or watched (sell only makes sense on a held row).
    update — prose-only revision; no broker call, no weight change.
             Creates a watched thesis when prior_row is None, refreshes
             the watched view when ``prior_row.is_watched``, or updates
             the review trail on a held row (rationale FROZEN, Invariant 3).

Critical invariant (Invariant 3 — from Spec B):
    ``rationale`` is FROZEN after the initial buy (or watched→held
    promotion) for HELD rows.  ``sell`` and ``update`` on held rows
    MUST NOT mutate it.  Tests in ``test_verb_dispatch.py`` codify this.

    Watched rows are explicitly exempt: their rationale evolves with
    every ``update`` stance.  Invariant 3 attaches at the moment the
    watched row is promoted to held.
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance

# Verbs that never produce a broker call.
_NO_TRADE_INTENTS: Final[frozenset[str]] = frozenset({"update"})


def resolve_broker_call(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
) -> dict | None:
    """Map a stance to a minimal broker-call descriptor.

    Parameters
    ----------
    stance
        The risk-gated stance from the strategist.
    prior_row
        The existing ``PositionThesis`` for this ticker (``None`` if
        the ticker is flat).  Used to distinguish a full close (absent
        ``stance.weight``) from a partial trim.

    Returns
    -------
    dict | None
        ``None`` for ``update`` (no broker dispatch).
        Otherwise a dict with ``{"action": "BUY"|"SELL", "weight": float}``
        describing the broker call direction and target weight.

        Note: the Executor's ``_run_async_impl`` constructs the actual
        ``Order`` object using ``final_orders`` from the risk gate —
        this helper exists primarily to gate whether a broker call is
        needed at all, and to provide the verb-dispatch logic in one
        auditable place.
    """

    if stance.intent in _NO_TRADE_INTENTS:
        # No-trade verbs — the broker does nothing for these.
        return None

    match stance.intent:

        case "buy":
            # Enter a new long position or increase an existing one.
            # ``stance.weight`` is required on buy stances (schema validator
            # enforces this — safe to access directly here).
            return {"action": "BUY", "weight": stance.weight}

        case "sell":
            # Partial trim or full close.
            # Absent weight (schema allows it for sell) means full exit → 0.0.
            if stance.weight is not None:
                # Partial trim — reduce to the supplied weight.
                return {"action": "SELL", "weight": stance.weight}
            else:
                # Full close — sell the entire held position to zero.
                return {"action": "SELL", "weight": 0.0}

        case _:
            # Unknown / ``None`` intent — no safe default broker action exists,
            # so we deliberately do not dispatch.  The caller is responsible for
            # raising or handling this case; returning ``None`` here surfaces
            # the gap rather than silently mis-routing the order.
            return None


def apply_stance_to_thesis(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
    fill_price: float | None,
    tick_id: str,
    as_of: datetime,
    current_tick_index: int = 0,
) -> PositionThesis | None:
    """Compute the new PositionThesis row for one ticker after a stance.

    Parameters
    ----------
    stance
        The risk-gated stance.  Must use the iter-3 three-verb vocabulary:
        ``buy`` / ``sell`` / ``update``.
    prior_row
        Existing ``PositionThesis`` (``None`` if ticker was flat or never
        seen before).  May be a watched row (``prior_row.is_watched``)
        for tickers the strategist tracks but has not yet bought.
    fill_price
        Actual fill price from Executor's broker call.  Required for
        ``buy`` stances (used as ``opened_price`` on entry or promotion).
        ``None`` for ``sell`` / ``update`` (no broker call ran, or
        is irrelevant).
    tick_id
        Identifier for the current tick (for ``last_reviewed_at`` /
        ``opened_tick_id``).
    as_of
        Tick timestamp (UTC) for ``last_reviewed_at`` and ``opened_at``.
    current_tick_index
        Window-relative integer tick counter.  Written into
        ``thesis_last_updated_tick`` on ``buy`` and ``update`` stances so
        ``context_shim`` can render staleness.  Defaults to 0 for callers
        that do not yet carry the tick index (e.g. legacy tests).

    Returns
    -------
    PositionThesis | None
        ``None`` when the stance is a full ``sell`` (i.e. ``stance.weight``
        is absent — caller must drop the ticker from the new positions
        dict).  Otherwise the updated row.

    Notes
    -----
    Invariant 3: ``rationale`` is FROZEN at the initial buy (or watched→held
    promotion) for HELD rows.  ``sell`` / ``update`` on held rows MUST NOT
    mutate it.

    Watched rows (``prior_row.is_watched``) are exempt — their rationale
    evolves freely with every ``update`` stance.  Invariant 3 attaches at
    the moment of promotion.

    Raises
    ------
    AssertionError
        ``buy`` without a fill price (caller bug).
    ValueError
        ``sell`` when prior_row is None or is a watched row — sell only
        makes sense against a real held position.
    """

    match stance.intent:

        case "buy":
            # ``buy`` covers three sub-cases, distinguished by prior_row state:
            #   1. Fresh entry — prior_row is None → seed a brand-new held row.
            #   2. Promotion — prior_row.is_watched → populate entry fields,
            #      FREEZE rationale to the buy stance's rationale.  The
            #      watched view's rationale is discarded (Invariant 3
            #      attaches at this moment).
            #   3. Position increase / add — held prior_row → bump weight,
            #      preserve immutable fields, refresh review trail.
            assert fill_price is not None, "buy without fill price — caller bug"

            # Fresh entry and watched-→-held promotion produce structurally
            # identical rows — both seed every entry field from the buy stance
            # and take the buy stance's rationale as the FROZEN entry rationale.
            # Collapse them into a single branch.
            if prior_row is None or prior_row.is_watched:
                # ── Fresh entry / watched promotion ──────────────────────────
                # Seed a brand-new held PositionThesis row.  For promotions,
                # the prior watched rationale is intentionally discarded —
                # the buy stance's rationale wins because Invariant 3 attaches
                # here.  thesis_last_updated_tick starts from the current tick
                # so the staleness counter does not begin in the past.
                return PositionThesis(
                    ticker                    = stance.ticker,
                    opened_at                 = as_of,
                    opened_tick_id            = tick_id,
                    opened_price              = fill_price,
                    weight                    = stance.weight,
                    catalyst                  = stance.catalyst,
                    rationale                 = stance.rationale,
                    last_reviewed_at          = as_of,
                    last_reviewed_decision    = "buy",
                    last_reviewed_reason      = stance.rationale or "",
                    thesis_last_updated_tick  = current_tick_index,
                )

            else:
                # ── Position increase (add) on a held row ────────────────────
                # Weight bump on an existing held row — preserve every
                # immutable field, including rationale (Invariant 3).  A
                # buy-add affirms the thesis, so we refresh
                # thesis_last_updated_tick to reset the staleness clock.
                return prior_row.model_copy(update={
                    "weight":                    stance.weight,
                    # Refresh the optional catalyst if the buy stance supplies one.
                    "catalyst":                  stance.catalyst if stance.catalyst is not None else prior_row.catalyst,
                    # Review trail — updated on every stance that touches this row.
                    "last_reviewed_at":          as_of,
                    "last_reviewed_decision":    "buy",
                    "last_reviewed_reason":      stance.rationale or "",
                    # rationale is intentionally NOT included here — Invariant 3.
                    "thesis_last_updated_tick":  current_tick_index,
                })

        case "sell":
            # ``sell`` covers both "trim" (weight supplied) and "close"
            # (weight absent) in the iter-3 schema.
            # ``sell`` only makes sense on a held position — a watched thesis
            # has no position to sell.
            if prior_row is None or prior_row.is_watched:
                raise ValueError(
                    f"apply_stance_to_thesis: 'sell' for {stance.ticker!r} but "
                    f"no held prior_row found — sell requires an active held "
                    f"position (prior_row is None or watched are both invalid)."
                )

            if stance.weight is None:
                # ── Full close ───────────────────────────────────────────────
                # Return None — the caller removes the ticker from the position
                # book.  No thesis row remains after a full exit.
                return None

            else:
                # ── Partial trim ─────────────────────────────────────────────
                # Weight reduction — preserve rationale; refresh review fields.
                return prior_row.model_copy(update={
                    "weight":                 stance.weight,
                    "last_reviewed_at":       as_of,
                    "last_reviewed_decision": "sell",
                    "last_reviewed_reason":   stance.reason or "",
                    # rationale is intentionally NOT included here — Invariant 3.
                })

        case "update":
            # ``update`` covers three sub-cases, distinguished by prior_row:
            #   1. prior_row is None → create a new watched row.  The
            #      strategist is recording an evolving view on a ticker it
            #      doesn't yet hold.  stance.reason seeds the initial rationale.
            #   2. prior_row.is_watched → refresh the watched view; rationale
            #      IS mutable on watched rows.
            #   3. held prior_row → prose-only revision on a held position;
            #      rationale FROZEN (Invariant 3); only review trail refreshes.

            if prior_row is None:
                # ── New watched thesis ───────────────────────────────────────
                # The strategist wants to record a view on a ticker it doesn't
                # yet own.  Seed a watched row — entry fields are left as their
                # default (None), which the model validator enforces as the
                # "all-or-nothing" rule for a watched row.  stance.reason
                # becomes the initial rationale (watched rationale is mutable —
                # it will evolve on future update stances).
                return PositionThesis(
                    ticker                    = stance.ticker,
                    rationale                 = stance.reason or "",
                    last_reviewed_at          = as_of,
                    last_reviewed_decision    = "update",
                    last_reviewed_reason      = stance.reason or "",
                    thesis_last_updated_tick  = current_tick_index,
                )

            elif prior_row.is_watched:
                # ── Refresh watched view ─────────────────────────────────────
                # Rationale mutates here — watched rows are exempt from
                # Invariant 3.  This is the core of the "evolving view"
                # feature: each update replaces the prior watched rationale
                # with the strategist's latest thinking on this ticker.
                return prior_row.model_copy(update={
                    "rationale":                 stance.reason or "",
                    "last_reviewed_at":          as_of,
                    "last_reviewed_decision":    "update",
                    "last_reviewed_reason":      stance.reason or "",
                    "thesis_last_updated_tick":  current_tick_index,
                })

            else:
                # ── Held update — rationale FROZEN (Invariant 3) ────────────
                # Prose-only revision on a held position — refresh review trail
                # only.  thesis_last_updated_tick resets because the strategist
                # explicitly revised the view of this position.
                return prior_row.model_copy(update={
                    "last_reviewed_at":          as_of,
                    "last_reviewed_decision":    "update",
                    "last_reviewed_reason":      stance.reason or "",
                    # rationale is intentionally NOT included here — Invariant 3.
                    "thesis_last_updated_tick":  current_tick_index,
                })

        case _:
            # ``intent`` is None (legacy stance) or an unknown value.
            # Return the prior row unchanged if present, or None if there
            # is no row.
            return prior_row

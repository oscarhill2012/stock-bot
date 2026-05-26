"""Verb-dispatch helpers shared between Executor's run loop and its
after_agent_callback writer.

Both functions are pure — no state mutation, no I/O.  Living inside
the executor package keeps the verb semantics in exactly one place
under the agent that owns both the broker dispatch and the
persistence write.

Verb vocabulary (iter-3, three-verb canonical form)
----------------------------------------------------
    buy    — enter a flat ticker (prior_row is None) or increase an
             existing position (prior_row is present).  Requires
             fill_price.
    sell   — reduce or fully close a position.  ``stance.weight``
             present ⇒ partial trim to that weight; absent ⇒ full
             close (caller drops the ticker).
    update — prose-only revision; no broker call, no weight change.

Critical invariant (Invariant 3 — from Spec B):
    ``rationale`` is FROZEN after the initial buy.  ``sell`` /
    ``update`` MUST NOT mutate it.  Tests in ``test_verb_dispatch.py``
    codify this.
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
        Existing ``PositionThesis`` (``None`` if ticker was flat).
    fill_price
        Actual fill price from Executor's broker call.  Required for
        ``buy`` stances (used as ``opened_price`` on entry, or to record
        the add price for position increases).  ``None`` for ``sell`` /
        ``update`` (no broker call ran, or is irrelevant).
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
    Invariant 3: ``rationale`` is FROZEN at the initial buy.  ``sell`` /
    ``update`` MUST NOT mutate it.  Tests in ``test_verb_dispatch.py``
    codify this.

    Raises
    ------
    AssertionError
        On invalid combinations, e.g. ``buy`` against a held ticker with
        ``prior_row is None`` when weight would imply a fresh open (the
        distinction is clear — prior_row=None → open, prior_row≠None →
        add; both are handled correctly below).  Also raised for ``buy``
        without a fill price.
    ValueError
        For ``sell`` / ``update`` with no prior row (the ticker must
        already be in the position book).
    """

    match stance.intent:

        case "buy":
            # ``buy`` covers both "open" (prior_row is None) and "add"
            # (prior_row is present) in the iter-3 schema.
            assert fill_price is not None, "buy without fill price — caller bug"

            if prior_row is None:
                # ── Fresh entry ──────────────────────────────────────────────
                # Seed a brand-new PositionThesis row.
                # No horizon / target_price / stop_price — iter-3 removed them.
                # Set thesis_last_updated_tick so the context_shim staleness
                # counter starts from this tick, not from the zero default.
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
                # ── Position increase (add) ──────────────────────────────────
                # Weight bump — preserve every immutable field, including
                # rationale (Invariant 3).  A buy-add is a thesis-affirmation
                # so we also refresh thesis_last_updated_tick.
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
            if prior_row is None:
                raise ValueError(
                    f"apply_stance_to_thesis: 'sell' for {stance.ticker!r} but "
                    f"no prior_row found — ticker must already be in position book"
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
            # Prose-only revision — refresh review trail; no broker call,
            # no weight change.  Preserves all immutable fields including
            # rationale (Invariant 3).
            # thesis_last_updated_tick IS refreshed here: an update stance
            # explicitly revises the strategist's view of this position, so
            # the staleness clock resets.
            if prior_row is None:
                raise ValueError(
                    f"apply_stance_to_thesis: 'update' for {stance.ticker!r} but "
                    f"no prior_row found — ticker must already be in position book"
                )

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

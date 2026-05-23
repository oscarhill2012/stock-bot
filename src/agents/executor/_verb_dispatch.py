"""Verb-dispatch helpers shared between Executor's run loop and its
after_agent_callback writer.

Both functions are pure — no state mutation, no I/O.  Living inside
the executor package keeps the verb semantics in exactly one place
under the agent that owns both the broker dispatch and the
persistence write.

Critical invariant (Invariant 3 — from Spec B):
    ``rationale`` is FROZEN after open.  ``add`` / ``trim`` / ``hold`` /
    ``update`` MUST NOT mutate it.  Tests in ``test_verb_dispatch.py``
    codify this.
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance

# Verbs that never produce a broker call.
_NO_TRADE_INTENTS: Final[frozenset[str]] = frozenset({"hold", "update"})


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
        the ticker is flat).  Required to compute the delta on
        ``add`` / ``trim`` and to size the ``close`` against the
        currently-held weight.

    Returns
    -------
    dict | None
        ``None`` for ``hold`` and ``update`` (no broker dispatch).
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

        case "open":
            # Enter a new long position to the specified weight.
            return {"action": "BUY", "weight": stance.weight}

        case "add":
            # Increase an existing position to the new (higher) weight.
            return {"action": "BUY", "weight": stance.weight}

        case "trim":
            # Reduce an existing position to the new (lower) weight.
            return {"action": "SELL", "weight": stance.weight}

        case "close":
            # Full exit — sell the entire held position to zero.
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
) -> PositionThesis | None:
    """Compute the new PositionThesis row for one ticker after a stance.

    Parameters
    ----------
    stance
        The risk-gated stance.
    prior_row
        Existing ``PositionThesis`` (``None`` if ticker was flat).
    fill_price
        Actual fill price from Executor's broker call.  Used as
        ``opened_price`` on ``open`` and to size ``add`` / ``trim``.
        ``None`` for ``hold`` / ``update`` (no broker call ran).
    tick_id
        Identifier for the current tick (for ``last_reviewed_at`` /
        ``opened_tick_id``).
    as_of
        Tick timestamp (UTC) for ``last_reviewed_at`` and ``opened_at``.

    Returns
    -------
    PositionThesis | None
        ``None`` when the stance is ``close`` (caller must drop the
        ticker from the new positions dict).  Otherwise the updated
        row.

    Notes
    -----
    Invariant 3: ``rationale`` is FROZEN at open.  ``add`` / ``trim``
    / ``hold`` / ``update`` MUST NOT mutate it.  Tests in
    ``test_verb_dispatch.py`` codify this.

    Raises
    ------
    AssertionError
        On invalid combinations, e.g. ``open`` against a held ticker or
        ``open`` without a fill price.  These are always caller bugs —
        loud assertions are preferable to silent coercions.
    ValueError
        For ``add`` / ``trim`` / ``hold`` / ``update`` with no prior row
        (the ticker must already be in the position book).
    """

    match stance.intent:

        case "open":
            # Seed a brand-new PositionThesis row.
            # Both invariants are caller-side contracts — raise loudly.
            assert prior_row is None,      "open against held ticker — caller bug"
            assert fill_price is not None, "open without fill price — caller bug"

            return PositionThesis(
                ticker                 = stance.ticker,
                opened_at              = as_of,
                opened_tick_id         = tick_id,
                opened_price           = fill_price,
                weight                 = stance.weight,
                target_price           = stance.target_price,
                stop_price             = stance.stop_price,
                catalyst               = stance.catalyst,
                horizon                = stance.horizon,
                rationale              = stance.rationale,
                last_reviewed_at       = as_of,
                last_reviewed_decision = "open",
                last_reviewed_reason   = stance.rationale or "",
            )

        case "add":
            # Weight bump — preserve every immutable field, including rationale.
            if prior_row is None:
                raise ValueError(
                    f"apply_stance_to_thesis: 'add' for {stance.ticker!r} but "
                    f"no prior_row found — ticker must already be in position book"
                )

            return prior_row.model_copy(update={
                "weight":                 stance.weight,
                # Optionally update mutable commitment fields if the stance
                # supplies them — useful when the strategist refines the
                # thesis on a size-up.
                "target_price":           stance.target_price if stance.target_price is not None else prior_row.target_price,
                "stop_price":             stance.stop_price   if stance.stop_price   is not None else prior_row.stop_price,
                "catalyst":               stance.catalyst      if stance.catalyst      is not None else prior_row.catalyst,
                "horizon":                stance.horizon       if stance.horizon       is not None else prior_row.horizon,
                # Review trail — updated on every stance that touches this row.
                "last_reviewed_at":       as_of,
                "last_reviewed_decision": "add",
                "last_reviewed_reason":   stance.reason or "",
                # rationale is intentionally NOT included here — Invariant 3.
            })

        case "trim":
            # Weight reduction — preserve rationale; refresh review fields.
            if prior_row is None:
                raise ValueError(
                    f"apply_stance_to_thesis: 'trim' for {stance.ticker!r} but "
                    f"no prior_row found — ticker must already be in position book"
                )

            return prior_row.model_copy(update={
                "weight":                 stance.weight,
                "last_reviewed_at":       as_of,
                "last_reviewed_decision": "trim",
                "last_reviewed_reason":   stance.reason or "",
                # rationale is intentionally NOT included here — Invariant 3.
            })

        case "close":
            # Full exit — the caller drops the ticker from the position book.
            return None

        case "hold":
            # No-trade review — preserve every commitment field including weight.
            if prior_row is None:
                raise ValueError(
                    f"apply_stance_to_thesis: 'hold' for {stance.ticker!r} but "
                    f"no prior_row found — ticker must already be in position book"
                )

            return prior_row.model_copy(update={
                "last_reviewed_at":       as_of,
                "last_reviewed_decision": "hold",
                "last_reviewed_reason":   stance.reason or "",
                # All commitment fields (weight, target_price, stop_price,
                # catalyst, horizon, rationale) are PRESERVED.
                # rationale is intentionally NOT included here — Invariant 3.
            })

        case "update":
            # Mutate target_price / stop_price / catalyst / horizon where
            # supplied; preserve rationale; refresh review trail.
            if prior_row is None:
                raise ValueError(
                    f"apply_stance_to_thesis: 'update' for {stance.ticker!r} but "
                    f"no prior_row found — ticker must already be in position book"
                )

            return prior_row.model_copy(update={
                # Only overwrite mutable fields that the stance explicitly supplies.
                "target_price":           stance.target_price if stance.target_price is not None else prior_row.target_price,
                "stop_price":             stance.stop_price   if stance.stop_price   is not None else prior_row.stop_price,
                "catalyst":               stance.catalyst      if stance.catalyst      is not None else prior_row.catalyst,
                "horizon":                stance.horizon       if stance.horizon       is not None else prior_row.horizon,
                "last_reviewed_at":       as_of,
                "last_reviewed_decision": "update",
                "last_reviewed_reason":   stance.reason or "",
                # rationale is intentionally NOT included here — Invariant 3.
            })

        case _:
            # ``intent`` is None (legacy stance) or an unknown value.
            # Legacy stances carry no PositionThesis metadata — return the
            # prior row unchanged if present, or None if there is no row.
            # This preserves backwards compatibility while the codebase
            # migrates to intent-based stances.
            return prior_row

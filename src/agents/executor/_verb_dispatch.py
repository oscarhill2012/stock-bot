"""Verb-dispatch helpers shared between Executor's run loop and its
after_agent_callback writer.

Both functions are pure — no state mutation, no I/O.  Living inside
the executor package keeps the verb semantics in exactly one place
under the agent that owns both the broker dispatch and the
persistence write.

Verb vocabulary (four-verb canonical form)
------------------------------------------
    buy       — open a position on a thesis (the row is seeded if it
                doesn't exist yet) or add to an existing position.
                Refreshes the row's rationale every time — the agent is
                on the record justifying each entry and each add.

    sell      — reduce or fully close an existing position.  Partial
                trim updates ``weight``; full close (absent
                ``stance.weight``) removes the row entirely (caller drops
                the ticker).  ``sell`` on a row with no live position —
                or on a ticker the agent has never written a thesis for —
                is a strategist hallucination: logged, counted, skipped.

    update    — revise the prose thesis without trading.  Works whether
                or not a position exists; seeds a new row if needed.
                Refreshes rationale freely.

    no_action — explicit "considered, no change."  No broker call.
                Refreshes the review trail on an existing row so the
                audit shows the agent re-examined the ticker, but does
                NOT reset ``thesis_last_updated_tick`` (staleness measures
                real revisions, not passive confirmations).  No-op when
                the agent has never written a thesis for the ticker.

Hallucinated stance handling
----------------------------
``sell`` on a row with no live position is a strategist bug — the agent
can't sell what it doesn't hold.  Rather than aborting the tick, the
dispatcher logs the violation loudly and returns a sentinel so the
caller can:

  1. count the occurrence (``hallucinated_stances`` per-tick counter),
  2. leave the thesis book unchanged for that ticker,
  3. continue processing the rest of the stances.

This matches the "log + skip + count" policy — silent failures are bad,
but a single hallucinated verb should not bring down a whole backtest.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Final

from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance

logger = logging.getLogger(__name__)

# Verbs that never produce a broker call.
_NO_TRADE_INTENTS: Final[frozenset[str]] = frozenset({"update", "no_action"})


# Sentinel returned by ``apply_stance_to_thesis`` when the stance is a
# strategist hallucination (sell on a non-held row).  Distinct from
# ``None`` (which means "full close — caller drops the ticker") so the
# caller can branch on it explicitly.
class _Hallucinated:
    """Sentinel — strategist emitted an invalid verb for the prior state.

    The caller treats this as "leave the row alone" and bumps the
    hallucination counter.  A class-with-singleton (not just a string)
    so accidental truthy checks can't confuse it with a real row.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<Hallucinated>"


HALLUCINATED: Final = _Hallucinated()


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
        The existing ``PositionThesis`` for this ticker (``None`` if the
        agent has no thesis on this ticker yet).  Used to distinguish a
        full close (absent ``stance.weight``) from a partial trim.

    Returns
    -------
    dict | None
        ``None`` for verbs that do not trade (``update``, ``no_action``).
        Otherwise a dict with ``{"action": "BUY"|"SELL", "weight": float}``
        describing the broker call direction and target weight.

        Note: the Executor's ``_run_async_impl`` constructs the actual
        ``Order`` object using ``final_orders`` from the risk gate — this
        helper exists primarily to gate whether a broker call is needed
        at all, and to provide the verb-dispatch logic in one auditable
        place.
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


def _has_live_position(row: PositionThesis | None) -> bool:
    """Return True when ``row`` describes a ticker the agent currently owns.

    A live position has its entry fields populated by a prior ``buy``
    stance.  A thesis row whose entry fields are still ``None`` is the
    agent's standing view on a ticker it has chosen not to (yet) own.
    """

    return row is not None and row.opened_at is not None


def apply_stance_to_thesis(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
    fill_price: float | None,
    tick_id: str,
    as_of: datetime,
    current_tick_index: int = 0,
):
    """Compute the new PositionThesis row for one ticker after a stance.

    Parameters
    ----------
    stance
        The risk-gated stance.  Must use the four-verb vocabulary:
        ``buy`` / ``sell`` / ``update`` / ``no_action``.
    prior_row
        Existing ``PositionThesis`` for this ticker, or ``None`` if the
        agent has never written a thesis for it.  A row may exist without
        a live position attached (``prior_row.opened_at is None``) — the
        agent has a view but no exposure.
    fill_price
        Actual fill price from Executor's broker call.  Required for
        ``buy`` stances (used as ``opened_price`` on entry or add).
        ``None`` for the other verbs.
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
    PositionThesis | None | HALLUCINATED
        - ``PositionThesis`` — the updated (or freshly seeded) row.
        - ``None`` — the stance was a full ``sell`` (``stance.weight``
          absent on a live position); the caller drops the ticker.
        - ``HALLUCINATED`` — the stance was invalid for the prior state
          (``sell`` on a row with no live position, or ``sell`` on a
          ticker with no prior row at all).  The caller leaves the
          existing row alone and bumps the hallucination counter.

    Raises
    ------
    AssertionError
        ``buy`` without a fill price (caller bug, not strategist bug).
    """

    match stance.intent:

        case "buy":
            # ``buy`` covers two structurally-distinct cases, distinguished
            # by whether the agent has a live position already:
            #   1. No live position (prior_row is None or its entry fields
            #      are None) → seed the entry fields from the buy stance.
            #      The buy stance's rationale becomes the row's rationale.
            #   2. Live position → add to it.  Weight bumps to the new
            #      target; rationale REFRESHES from the buy stance (the
            #      agent must justify each add).
            assert fill_price is not None, "buy without fill price — caller bug"

            if not _has_live_position(prior_row):
                # ── Open / seed a row from scratch ────────────────────────────
                # Either there's no prior row at all, or there's a no-position
                # thesis row whose rationale we now overwrite (the buy stance
                # supersedes the prior watching-view).
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
                # ── Add to a live position ──────────────────────────────────
                # Bump weight, refresh catalyst if supplied, refresh rationale
                # (accountability: the agent justifies each add), reset
                # staleness counter.  ``opened_at`` / ``opened_tick_id`` /
                # ``opened_price`` stay frozen at their first-entry values —
                # they record the original open, not the most recent add.
                return prior_row.model_copy(update={
                    "weight":                    stance.weight,
                    "catalyst":                  stance.catalyst if stance.catalyst is not None else prior_row.catalyst,
                    "rationale":                 stance.rationale,
                    "last_reviewed_at":          as_of,
                    "last_reviewed_decision":    "buy",
                    "last_reviewed_reason":      stance.rationale or "",
                    "thesis_last_updated_tick":  current_tick_index,
                })

        case "sell":
            # ``sell`` only makes sense against a live position.  No live
            # position ⇒ the strategist hallucinated — log loudly and let
            # the caller count + skip.
            if not _has_live_position(prior_row):
                # Stable message key — picked up by the reporting layer's
                # log aggregator (``_aggregate_obs_artefacts``) to count
                # strategist hallucinations across a run.  ``extra`` carries
                # the per-occurrence context for log readers.
                logger.warning(
                    "hallucinated_stance",
                    extra={
                        "ticker":     stance.ticker,
                        "intent":     stance.intent,
                        "prior_row":  "None" if prior_row is None else "no-position",
                    },
                )
                return HALLUCINATED

            if stance.weight is None:
                # ── Full close ───────────────────────────────────────────────
                # Return None — the caller removes the ticker from the position
                # book.  No thesis row remains after a full exit; the trade
                # lands in the trade log + closed-trades rolling memory.
                return None

            else:
                # ── Partial trim ─────────────────────────────────────────────
                # Reduce weight; refresh review trail.  Rationale stays put
                # — sell does not justify a thesis change (use update for
                # that).  Staleness counter is NOT reset (a trim is not a
                # revision of the view).
                return prior_row.model_copy(update={
                    "weight":                 stance.weight,
                    "last_reviewed_at":       as_of,
                    "last_reviewed_decision": "sell",
                    "last_reviewed_reason":   stance.reason or "",
                })

        case "update":
            # ``update`` revises prose.  Two sub-cases:
            #   1. No prior row → seed a no-position thesis row.  The agent
            #      is writing a fresh view on a ticker it hasn't (yet)
            #      bought.  Entry fields default to None.
            #   2. Existing row (with or without live position) → refresh
            #      rationale + review trail.  Works on both no-position
            #      thesis rows and live positions: in both cases the agent's
            #      current view is what we record.

            if prior_row is None:
                # ── Seed a no-position thesis row ────────────────────────────
                return PositionThesis(
                    ticker                    = stance.ticker,
                    rationale                 = stance.reason or "",
                    last_reviewed_at          = as_of,
                    last_reviewed_decision    = "update",
                    last_reviewed_reason      = stance.reason or "",
                    thesis_last_updated_tick  = current_tick_index,
                )

            else:
                # ── Refresh rationale on an existing row ────────────────────
                # Works whether or not the agent owns the underlying ticker —
                # rationale is mutable across the board.
                return prior_row.model_copy(update={
                    "rationale":                 stance.reason or "",
                    "last_reviewed_at":          as_of,
                    "last_reviewed_decision":    "update",
                    "last_reviewed_reason":      stance.reason or "",
                    "thesis_last_updated_tick":  current_tick_index,
                })

        case "no_action":
            # ``no_action`` is "I considered this and chose not to act."
            # No trade, no prose change.  Two sub-cases:
            #   1. No prior row → nothing to update; return None so the
            #      caller leaves the book unchanged for this ticker.
            #      (We do NOT seed a row here — no_action shouldn't create
            #      content, only acknowledge it.)
            #   2. Existing row → refresh the review trail so the audit
            #      shows the agent re-examined the ticker.  ``rationale``
            #      and ``thesis_last_updated_tick`` are untouched —
            #      staleness measures real revisions, not passive
            #      confirmations.

            if prior_row is None:
                # No thesis row, no position — nothing to acknowledge.  The
                # caller leaves the book unchanged for this ticker.
                return None

            return prior_row.model_copy(update={
                "last_reviewed_at":       as_of,
                "last_reviewed_decision": "no_action",
                "last_reviewed_reason":   "",
                # thesis_last_updated_tick deliberately NOT refreshed.
            })

        case _:
            # ``intent`` is None (legacy stance) or an unknown value.
            # Return the prior row unchanged if present, or None if there
            # is no row.
            return prior_row

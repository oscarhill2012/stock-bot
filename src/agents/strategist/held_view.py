"""Render the Held Positions block injected into the strategist's prompt.

Reads thesis data from ``state["user:positions"]`` (a ``dict[ticker,
PositionThesis-shaped dict]``) and live price/weight data from
``state["portfolio"]`` (a ``Portfolio`` instance or its serialised
dict equivalent).  Spec B rewrites the renderer to emit two blocks per
position:

  * **Your commitments on entry** — the immutable promise the strategist
    made at open (rationale, target, stop, catalyst, horizon).
  * **Evolution** — what has changed since open (price drift, time
    held, distance to target / stop in $ and %, last-reviewed verb).

``last_reviewed_reason`` is persisted to the audit trail but NEVER
rendered into the next tick's prompt (Principle 2 / Invariant 4) — the
LLM must not anchor on its own prior-tick justification.

The function is *total* — it never raises.  Entries whose thesis cannot
be coerced to ``PositionThesis`` are silently skipped so one corrupt
entry in state does not abort the tick.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.strategist.position_thesis import PositionThesis
from broker.portfolio import Portfolio


# Number of trading hours per day used to convert raw elapsed hours into
# the "D trading days" approximation rendered in the Evolution block.
# NYSE regular hours are 09:30-16:00 = 6.5h; we use 6.5 to keep the
# arithmetic honest on backtests that tick at hourly cadence.
_TRADING_HOURS_PER_DAY: float = 6.5


# ---------------------------------------------------------------------------
# Internal coercion helpers
# ---------------------------------------------------------------------------

def _coerce_thesis(value: Any) -> PositionThesis:
    """Return a ``PositionThesis`` whether ``value`` is an instance or a dict."""

    if isinstance(value, PositionThesis):
        return value
    return PositionThesis.model_validate(value)


def _coerce_portfolio(value: Any) -> Portfolio:
    """Return a ``Portfolio`` whether ``value`` is an instance or a dict."""

    if isinstance(value, Portfolio):
        return value
    return Portfolio.model_validate(value)


# ---------------------------------------------------------------------------
# Evolution arithmetic — small pure helpers so the formatter stays flat
# ---------------------------------------------------------------------------

def _hours_between(earlier: datetime, later: datetime) -> float:
    """Return the elapsed hours between two UTC datetimes (non-negative).

    Parameters
    ----------
    earlier:
        The start datetime (typically ``thesis.opened_at``).
    later:
        The end datetime (typically the current tick ``as_of``).

    Returns
    -------
    float
        Elapsed hours, clamped to a minimum of 0.0.
    """

    delta = later - earlier
    return max(delta.total_seconds() / 3600.0, 0.0)


def _pct_change(*, from_price: float, to_price: float) -> float | None:
    """Return ``(to - from) / from * 100`` or ``None`` when ``from == 0``.

    Parameters
    ----------
    from_price:
        The reference price (denominator).
    to_price:
        The target price (numerator contribution).

    Returns
    -------
    float | None
        Signed percentage, or ``None`` if ``from_price`` is zero (avoids
        divide-by-zero on positions whose entry price is not yet known).
    """

    if from_price == 0.0:
        return None
    return (to_price - from_price) / from_price * 100.0


# ---------------------------------------------------------------------------
# Single-position formatter
# ---------------------------------------------------------------------------

def _format_one(
    thesis:    PositionThesis,
    portfolio: Portfolio,
    *,
    as_of:     datetime,
) -> str:
    """Render one position as a two-block (commitments + evolution) string.

    Parameters
    ----------
    thesis:
        The ``PositionThesis`` for this position.
    portfolio:
        Current portfolio snapshot — supplies live price for evolution.
    as_of:
        Current tick timestamp — used to compute "Held for" elapsed time.

    Returns
    -------
    str
        A multi-line block joined by ``\\n``, ready to splice into a prompt.
    """

    ticker        = thesis.ticker
    weights       = portfolio.current_weights()
    curr_weight   = weights.get(ticker, 0.0)
    pos           = portfolio.positions.get(ticker)
    current_price = pos.last_price if pos is not None else None

    # Header line — when the position was opened and at what price.
    # Guard against a misleading "$0.00" when opened_price is unknown /
    # unrecorded; legacy persistence rows may carry 0.0 as a sentinel.
    opened_str = thesis.opened_at.strftime("%Y-%m-%d %H:%M")
    if thesis.opened_price > 0.0:
        opened_price_str = (
            f"at ${thesis.opened_price:.2f}  (tick {thesis.opened_tick_id})"
        )
    else:
        opened_price_str = (
            f"(entry price unknown)  (tick {thesis.opened_tick_id})"
        )

    lines: list[str] = [
        ticker,
        f"  Opened on {opened_str} {opened_price_str}",
    ]

    # ── Your commitments on entry ────────────────────────────────────────
    # The immutable promise the strategist made when opening the position.
    # Rationale stays visible per Principle 1 (anti-anchoring via framing,
    # not hiding) — we label it "commitments", not "prior conclusion".
    lines.append("  Your commitments on entry:")
    lines.append(f"    Rationale:  {thesis.rationale}")

    if thesis.target_price is not None:
        target_pct = _pct_change(
            from_price = thesis.opened_price,
            to_price   = thesis.target_price,
        )
        pct_str = f"  ({target_pct:+.1f}% from entry)" if target_pct is not None else ""
        lines.append(f"    Target:     ${thesis.target_price:.2f}{pct_str}")
    else:
        lines.append("    Target:     (no target set)")

    if thesis.stop_price is not None:
        stop_pct = _pct_change(
            from_price = thesis.opened_price,
            to_price   = thesis.stop_price,
        )
        pct_str = f"  ({stop_pct:+.1f}% from entry)" if stop_pct is not None else ""
        lines.append(f"    Stop:       ${thesis.stop_price:.2f}{pct_str}")
    else:
        lines.append("    Stop:       (no stop set)")

    lines.append(f"    Catalyst:   {thesis.catalyst or '(none recorded)'}")
    lines.append(f"    Horizon:    {thesis.horizon}")

    # ── Evolution ────────────────────────────────────────────────────────
    # What has changed since open — the structural source of prompt
    # diversity across ticks. Even with a stable held set, these lines
    # mutate as price moves and time advances.
    lines.append("  Evolution:")

    elapsed_hours = _hours_between(thesis.opened_at, as_of)
    elapsed_days  = elapsed_hours / _TRADING_HOURS_PER_DAY

    # "N ticks" — we approximate one tick per hour of trading time. The
    # exact tick count is also available on PositionThesis via
    # opened_tick_id arithmetic in a future revision; for V1 this hour
    # proxy is good enough for the LLM to reason about freshness.
    elapsed_ticks = int(round(elapsed_hours))
    lines.append(
        f"    Held for:   {elapsed_ticks} ticks · "
        f"{elapsed_hours:.1f}h · {elapsed_days:.1f} trading days"
    )

    if current_price is not None and current_price > 0:
        # Now line — current price + signed pct from entry + portfolio weight.
        from_entry = _pct_change(
            from_price = thesis.opened_price,
            to_price   = current_price,
        )
        from_entry_str = f"  ({from_entry:+.1f}% from entry)" if from_entry is not None else ""
        lines.append(
            f"    Now:        ${current_price:.2f}{from_entry_str}  |  "
            f"weight {curr_weight:.3f}"
        )

        # To-target / to-stop — distance from CURRENT price (not entry).
        # Tells the LLM how much further the catalyst still has to run.
        if thesis.target_price is not None:
            delta_target = thesis.target_price - current_price
            pct_target   = _pct_change(from_price=current_price, to_price=thesis.target_price)
            pct_str      = f"  ({pct_target:+.1f}% from now)" if pct_target is not None else ""
            lines.append(f"    To target:  ${delta_target:+.2f}{pct_str}")
        else:
            lines.append("    To target:  (no target set)")

        if thesis.stop_price is not None:
            delta_stop = thesis.stop_price - current_price
            pct_stop   = _pct_change(from_price=current_price, to_price=thesis.stop_price)
            pct_str    = f"  ({pct_stop:+.1f}% from now)" if pct_stop is not None else ""
            lines.append(f"    To stop:    ${delta_stop:+.2f}{pct_str}")
        else:
            lines.append("    To stop:    (no stop set)")
    else:
        # No live price — render placeholders so the LLM still sees the row.
        lines.append("    Now:        (price unavailable)")
        lines.append("    To target:  (price unavailable)")
        lines.append("    To stop:    (price unavailable)")

    # Reviewed line — last-reviewed timestamp + the verb that produced
    # the review. ``last_reviewed_reason`` is DELIBERATELY OMITTED here
    # per Principle 2 / Invariant 4.
    reviewed_str = thesis.last_reviewed_at.strftime("%Y-%m-%d %H:%M")
    lines.append(
        f"    Reviewed:   {reviewed_str} ({thesis.last_reviewed_decision})"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_held_positions_view(
    positions: dict[str, Any],
    portfolio: Any,
    *,
    as_of:     datetime,
) -> str:
    """Render every held position as a structured block for prompt injection.

    Accepts ``positions`` values that are either ``PositionThesis``
    instances or their ``model_dump(mode="json")`` dict equivalents.
    ``portfolio`` may likewise be a ``Portfolio`` instance or its
    serialised dict form.  ``as_of`` is the current tick timestamp used
    to compute the "Held for" evolution column.

    The function is *total* — it never raises.  Entries whose thesis
    cannot be coerced are silently skipped; the remaining entries are
    still rendered.  An entirely empty or unrenderable set of positions
    returns the flat-portfolio sentinel.

    Parameters
    ----------
    positions:
        Mapping of ticker → ``PositionThesis`` (instance or dict).
    portfolio:
        Current portfolio snapshot (``Portfolio`` instance or dict).
    as_of:
        Current tick timestamp.  Required (no default) so the caller is
        forced to thread the replay clock through — wall-clock fallback
        belongs at the call site, not buried here.

    Returns
    -------
    str
        Human-readable block suitable for splicing into an LLM prompt,
        or the flat-portfolio sentinel when there are no valid positions.
    """

    if not positions:
        return "(No held positions — portfolio is flat.)"

    pf = _coerce_portfolio(portfolio)

    blocks: list[str] = []
    for ticker in sorted(positions.keys()):
        try:
            thesis = _coerce_thesis(positions[ticker])
        except Exception:  # noqa: BLE001 — defensive at rendering boundary;
            # one corrupt position dict must not crash the tick.
            continue
        blocks.append(_format_one(thesis, pf, as_of=as_of))

    if not blocks:
        return "(No held positions — portfolio is flat.)"

    # Separate each position block with a blank line for legibility in the prompt.
    return "\n\n".join(blocks)

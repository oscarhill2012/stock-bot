"""Render the Held Positions block that is injected into the strategist's prompt.

Pulls thesis data from ``state["positions"]`` (a ``dict[ticker, thesis_dict]``)
and live price/weight data from ``state["portfolio"]`` (a ``Portfolio`` instance
or its serialised dict equivalent).

The function is *total* — it never raises. Entries whose thesis cannot be
coerced to ``PositionThesis`` are silently skipped so that one corrupt entry
in state does not abort the entire tick.
"""
from __future__ import annotations

from typing import Any

from agents.strategist.schema import PositionThesis
from broker.portfolio import Portfolio

# ---------------------------------------------------------------------------
# Internal coercion helpers
# ---------------------------------------------------------------------------

def _coerce_thesis(value: Any) -> PositionThesis:
    """Return a ``PositionThesis`` regardless of whether *value* is already an
    instance or a plain ``dict`` / JSON-compatible mapping."""
    if isinstance(value, PositionThesis):
        return value
    return PositionThesis.model_validate(value)


def _coerce_portfolio(value: Any) -> Portfolio:
    """Return a ``Portfolio`` regardless of whether *value* is already an
    instance or a plain ``dict`` / JSON-compatible mapping."""
    if isinstance(value, Portfolio):
        return value
    return Portfolio.model_validate(value)


# ---------------------------------------------------------------------------
# Single-position formatter
# ---------------------------------------------------------------------------

def _format_one(thesis: PositionThesis, portfolio: Portfolio) -> str:
    """Format a single held position as a multi-line human-readable block.

    Parameters
    ----------
    thesis:
        The ``PositionThesis`` for this position.
    portfolio:
        The current portfolio snapshot, used to look up live price and weight.

    Returns
    -------
    str
        A block of lines (joined by ``\\n``) ready to embed in a prompt.
    """
    ticker = thesis.ticker
    pos = portfolio.positions.get(ticker)
    weights = portfolio.current_weights()
    curr_weight = weights.get(ticker, 0.0)

    lines: list[str] = [ticker]

    # ``opened_price`` may legitimately be ``None`` (strategist proposed an
    # open and the executor hasn't stamped the fill price yet — only
    # observable within a single tick) or ``0.0`` (legacy persistence from
    # before the executor took ownership of opened_price).  In either case
    # we treat the open price as unknown and skip the percent-from-open
    # arithmetic that used to divide by zero.  ``has_open_price`` is the
    # single guard for every code path that would have done the division.
    has_open_price = (
        thesis.opened_price is not None and thesis.opened_price > 0.0
    )

    # Header line — when the position was opened and at what price.  We surface
    # the *current* portfolio weight on this line (not the weight at open) so
    # the LLM can see entry-price alongside present portfolio weight in a
    # single glance; the same weight reappears on the "Now:" line for symmetry
    # with the live-price block.
    opened_str = thesis.opened_at.strftime("%Y-%m-%d %H:%M")
    if has_open_price:
        lines.append(
            f"  Opened:    {opened_str} at ${thesis.opened_price:.2f}, "
            f"weight {curr_weight:.3f}"
        )
    else:
        # Price not yet known — show the timestamp and the placeholder so
        # the prompt still communicates *when* the position was opened.
        lines.append(
            f"  Opened:    {opened_str} (open price pending), "
            f"weight {curr_weight:.3f}"
        )

    lines.append(f"  Why:       {thesis.rationale}")

    # Target / stop block — show '(none set at open)' only when both are absent.
    if thesis.target_price is None and thesis.stop_price is None:
        lines.append("  Aim:       (none set at open)")
    else:

        # Show the absolute target / stop price always; suffix the signed
        # percent-from-open only when we actually have an open price to
        # divide by.  This keeps the LLM-facing prompt informative for
        # positions that were just opened this tick (no fill stamp yet)
        # without crashing on the divide-by-zero we hit pre-fix.
        if thesis.target_price is not None:
            if has_open_price:
                target_pct = (thesis.target_price - thesis.opened_price) / thesis.opened_price * 100
                target_part = f"target ${thesis.target_price:.2f} ({target_pct:+.1f}% from open)"
            else:
                target_part = f"target ${thesis.target_price:.2f}"
        else:
            target_part = "target (none)"

        if thesis.stop_price is not None:
            if has_open_price:
                stop_pct = (thesis.stop_price - thesis.opened_price) / thesis.opened_price * 100
                stop_part = f"stop ${thesis.stop_price:.2f} ({stop_pct:+.1f}% from open)"
            else:
                stop_part = f"stop ${thesis.stop_price:.2f}"
        else:
            stop_part = "stop (none)"

        lines.append(f"  Aim:       {target_part}  |  {stop_part}")

    lines.append(f"  Horizon:   {thesis.horizon}")

    # Catalyst is optional — omit the line entirely when not set.
    if thesis.catalyst:
        lines.append(f"  Catalyst:  {thesis.catalyst}")

    # Live price and unrealised P&L — omit if price is unavailable or zero,
    # and drop the unrealised-pct portion if we have no open price to compare
    # against (same divide-by-zero defence as the target / stop block).
    if pos is None or pos.last_price <= 0:
        lines.append("  Now:       (price unavailable)")
    elif has_open_price:
        # Signed percentage from the open price (not avg_cost) — convention:
        # we track performance against *our decision price*, not blended cost.
        pnl_pct = (pos.last_price - thesis.opened_price) / thesis.opened_price * 100
        lines.append(
            f"  Now:       ${pos.last_price:.2f}  |  weight {curr_weight:.3f}  "
            f"|  {pnl_pct:+.2f}% unrealised"
        )
    else:
        # We have a live price but no open price yet — show the live price
        # and weight, but skip the unrealised-pct (nothing to compare to).
        lines.append(
            f"  Now:       ${pos.last_price:.2f}  |  weight {curr_weight:.3f}  "
            f"|  (unrealised pending open price)"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_held_positions_view(
    positions: dict[str, Any],
    portfolio: Any,
) -> str:
    """Render every held position as a structured block for prompt injection.

    Accepts ``positions`` values that are either ``PositionThesis`` instances
    or their ``model_dump(mode="json")`` dict equivalents.  ``portfolio`` may
    likewise be a ``Portfolio`` instance or its serialised dict form.

    The function is *total* — it never raises.  Entries whose thesis cannot be
    coerced (e.g. corrupt or missing keys in ``state["positions"]``) are
    silently skipped; the remaining entries are still rendered.  An entirely
    empty or unrenderable set of positions returns the flat-portfolio message.

    Parameters
    ----------
    positions:
        Mapping of ticker → ``PositionThesis`` (instance or dict).
    portfolio:
        Current portfolio snapshot (``Portfolio`` instance or dict).

    Returns
    -------
    str
        Human-readable block suitable for splicing into an LLM prompt, or the
        flat-portfolio sentinel string when there are no valid positions.
    """
    if not positions:
        return "(No held positions — portfolio is flat.)"

    pf = _coerce_portfolio(portfolio)

    blocks: list[str] = []
    for ticker in sorted(positions.keys()):
        try:
            thesis = _coerce_thesis(positions[ticker])
        except Exception:  # noqa: BLE001 — defensive at rendering boundary;
            # one corrupt position dict must not crash the tick
            continue
        blocks.append(_format_one(thesis, pf))

    if not blocks:
        return "(No held positions — portfolio is flat.)"

    # Separate each position block with a blank line for readability in the prompt.
    return "\n\n".join(blocks)

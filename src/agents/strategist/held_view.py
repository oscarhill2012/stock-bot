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

    # Header line — when the position was opened and at what price.
    opened_str = thesis.opened_at.strftime("%Y-%m-%d %H:%M")
    lines.append(
        f"  Opened:    {opened_str} at ${thesis.opened_price:.2f}, "
        f"weight {curr_weight:.3f}"
    )

    lines.append(f"  Why:       {thesis.rationale}")

    # Target / stop block — show '(none set at open)' only when both are absent.
    if thesis.target_price is None and thesis.stop_price is None:
        lines.append("  Aim:       (none set at open)")
    else:
        # Signed percentage from the open price for both target and stop.
        target_part = (
            f"target ${thesis.target_price:.2f} "
            f"({(thesis.target_price - thesis.opened_price) / thesis.opened_price * 100:+.1f}% from open)"
            if thesis.target_price is not None
            else "target (none)"
        )
        stop_part = (
            f"stop ${thesis.stop_price:.2f} "
            f"({(thesis.stop_price - thesis.opened_price) / thesis.opened_price * 100:+.1f}% from open)"
            if thesis.stop_price is not None
            else "stop (none)"
        )
        lines.append(f"  Aim:       {target_part}  |  {stop_part}")

    lines.append(f"  Horizon:   {thesis.horizon}")

    # Catalyst is optional — omit the line entirely when not set.
    if thesis.catalyst:
        lines.append(f"  Catalyst:  {thesis.catalyst}")

    # Live price and unrealised P&L — omit if price is unavailable or zero.
    if pos is None or pos.last_price <= 0:
        lines.append("  Now:       (price unavailable)")
    else:
        # Signed percentage from the open price (not avg_cost) — convention:
        # we track performance against *our decision price*, not blended cost.
        pnl_pct = (pos.last_price - thesis.opened_price) / thesis.opened_price * 100
        lines.append(
            f"  Now:       ${pos.last_price:.2f}  |  weight {curr_weight:.3f}  "
            f"|  {pnl_pct:+.2f}% unrealised"
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

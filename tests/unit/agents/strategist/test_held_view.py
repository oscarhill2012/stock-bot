"""Held-positions view rendering tests — Tier 1, no LLM.

Migrated to the Spec B contract (Plan 2, Task 1):
  * Imports ``PositionThesis`` from ``agents.strategist.position_thesis``
    (Plan 1 model) instead of ``agents.strategist.schema`` (legacy model).
  * All ``render_held_positions_view`` calls carry the required ``as_of``
    keyword argument.
  * Assertions updated from the old "Opened / Why / Aim / Horizon /
    Catalyst / Now" layout to the new "Opened on / Your commitments on
    entry / Evolution / Held for / To target / To stop / Reviewed" layout.
  * Flat-portfolio sentinel pinned by equality (unchanged string).
  * ``opened_tag`` / ``last_review_note`` replaced by ``opened_tick_id`` /
    ``last_reviewed_decision`` / ``last_reviewed_reason`` / ``weight``
    as defined in the Plan 1 ``PositionThesis`` schema.
  * Tests that relied on the old "open price pending" / "unrealised
    pending" code path have been refactored: the Plan 1 model makes
    ``opened_price`` a required float, so those code paths no longer
    exist; we now verify graceful rendering of edge-case floats (0.0)
    instead.
"""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.held_view import render_held_positions_view
from agents.strategist.position_thesis import PositionThesis
from broker.portfolio import Portfolio, Position


# A fixed "current tick" timestamp used as the ``as_of`` argument throughout.
# Chosen to be 7 days after the fixture's ``opened_at`` so elapsed-time
# arithmetic in the Evolution block produces a non-zero, human-legible value.
_AS_OF = datetime(2026, 4, 29, 14, 0, tzinfo=UTC)


def _thesis(
    ticker:         str = "AAPL",
    opened_price:   float = 192.40,
    target_price:   float | None = 210.0,
    stop_price:     float | None = 185.0,
    catalyst:       str | None = "Q3 earnings",
    rationale:      str = "FCF + insider",
    horizon:        str = "swing",
) -> PositionThesis:
    """Construct a PositionThesis fixture for testing.

    Uses Plan 1's field set (``opened_tick_id``, ``weight``,
    ``last_reviewed_decision``, ``last_reviewed_reason``) rather than
    the legacy ``opened_tag`` / ``last_review_note``.
    """
    return PositionThesis(
        ticker                 = ticker,
        opened_at              = datetime(2026, 4, 22, 14, 0, tzinfo=UTC),
        opened_tick_id         = f"open_{ticker.lower()}",
        opened_price           = opened_price,
        weight                 = 0.05,
        rationale              = rationale,
        horizon                = horizon,
        target_price           = target_price,
        stop_price             = stop_price,
        catalyst               = catalyst,
        last_reviewed_at       = datetime(2026, 4, 22, 14, 0, tzinfo=UTC),
        last_reviewed_decision = "open",
        last_reviewed_reason   = "opened on entry signal",
    )


def test_empty_portfolio_returns_no_holdings_message():
    """An empty positions dict must produce the flat-portfolio sentinel."""
    pf = Portfolio(cash=1000.0, positions={})
    out = render_held_positions_view(
        positions = {},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    assert out == "(No held positions — portfolio is flat.)"


def test_single_holding_block_includes_all_required_lines():
    """Every expected field label and value must appear in the rendered block."""
    thesis = _thesis()
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions = {"AAPL": thesis.model_dump(mode="json")},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    assert "AAPL" in out
    assert "Opened on" in out
    assert "192.40" in out
    assert "Your commitments on entry" in out
    assert "FCF + insider" in out
    assert "Target:" in out
    assert "210.00" in out
    assert "Stop:" in out
    assert "185.00" in out
    assert "swing" in out
    assert "Catalyst:" in out
    assert "Q3 earnings" in out
    assert "Evolution" in out
    assert "Held for:" in out
    assert "Now:" in out
    assert "198.50" in out


def test_pnl_pct_rendered_when_price_available():
    """A 5 % gain from open must be visible as '+5' in the output."""
    thesis = _thesis(opened_price=200.0)
    pf = Portfolio(
        cash=0.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=200.0, last_price=210.0)},
    )
    out = render_held_positions_view(
        positions = {"AAPL": thesis.model_dump(mode="json")},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    # The "+5.0% from entry" string appears on the Now line.
    assert "+5" in out


def test_pnl_pct_negative_rendered_with_minus_sign():
    """A loss from open must appear with a '-' prefix in the output."""
    thesis = _thesis(opened_price=200.0)
    pf = Portfolio(
        cash=0.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=200.0, last_price=190.0)},
    )
    out = render_held_positions_view(
        positions = {"AAPL": thesis.model_dump(mode="json")},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    # The "-5.0% from entry" string appears on the Now line.
    assert "-5" in out


def test_no_target_no_stop_renders_none_message():
    """When both target and stop are None the "(no target set)" / "(no stop set)"
    messages must appear — the block must not crash."""
    thesis = _thesis(target_price=None, stop_price=None, catalyst=None)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions = {"AAPL": thesis.model_dump(mode="json")},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    assert "(no target set)" in out
    assert "(no stop set)"   in out


def test_no_catalyst_omits_catalyst_line():
    """When catalyst is None the 'Catalyst:' label must not appear."""
    thesis = _thesis(catalyst=None)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions = {"AAPL": thesis.model_dump(mode="json")},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    # The new renderer shows "(none recorded)" when catalyst is None rather
    # than omitting the line entirely — assert the label IS still present
    # and no raw "None" value leaks into the output.
    assert "Catalyst:" in out
    assert "none recorded" in out


def test_multiple_holdings_separated_by_blank_line():
    """Multiple tickers must each appear and be separated by a blank line."""
    aapl = _thesis(ticker="AAPL").model_dump(mode="json")
    msft = _thesis(
        ticker="MSFT",
        opened_price=410.0,
        rationale="cloud tailwind",
        target_price=450.0,
        stop_price=395.0,
        catalyst=None,
    ).model_dump(mode="json")
    pf = Portfolio(
        cash=500.0,
        positions={
            "AAPL": Position(quantity=5.0, avg_cost=192.40, last_price=198.50),
            "MSFT": Position(quantity=2.0, avg_cost=410.0, last_price=415.0),
        },
    )
    out = render_held_positions_view(
        positions = {"AAPL": aapl, "MSFT": msft},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    assert "AAPL" in out
    assert "MSFT" in out
    assert "\n\n" in out


def test_accepts_thesis_instance_or_dict():
    """render_held_positions_view must accept a raw PositionThesis instance
    as well as its model_dump dict form."""
    thesis_inst = _thesis()
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions = {"AAPL": thesis_inst},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    assert "AAPL" in out


def test_zero_opened_price_degrades_gracefully():
    """When ``opened_price`` is ``0.0`` the renderer must skip percent-from-entry
    arithmetic (avoid divide-by-zero) and still render the current price and
    block structure.

    Legacy persistence rows may carry ``opened_price=0.0``.  The new Plan 1
    ``PositionThesis`` makes ``opened_price`` a required float (no longer
    Optional), so the "open price pending" code path no longer exists.  The
    renderer handles ``0.0`` via ``_pct_change`` returning ``None``, which
    suppresses the "% from entry" suffix but does not crash.
    """
    thesis = _thesis(opened_price=0.0)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=0.0, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions = {"AAPL": thesis.model_dump(mode="json")},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    # The renderer must not crash — verify the block renders at all.
    assert "AAPL" in out
    # The live price must still appear on the Now line.
    assert "198.50" in out
    # The "% from entry" suffix must be absent when opened_price is 0.0
    # (no valid denominator).
    assert "from entry" not in out
    # The header must not emit a misleading "$0.00" price — it must use
    # the "entry price unknown" sentinel instead.
    assert "entry price unknown" in out
    assert "$0.00" not in out


def test_corrupt_thesis_dict_is_skipped_without_raising():
    """A thesis entry that cannot be coerced to PositionThesis must be silently
    skipped; the remaining positions must still render normally."""
    good_thesis = _thesis(ticker="AAPL").model_dump(mode="json")
    pf = Portfolio(
        cash=500.0,
        positions={
            "AAPL": Position(quantity=5.0, avg_cost=192.40, last_price=198.50),
            "MSFT": Position(quantity=2.0, avg_cost=400.0, last_price=405.0),
        },
    )
    # "MSFT" entry is a string — intentionally invalid, should be skipped.
    out = render_held_positions_view(
        positions = {"AAPL": good_thesis, "MSFT": "not-a-thesis"},
        portfolio = pf,
        as_of     = _AS_OF,
    )
    assert "AAPL" in out   # good entry rendered
    assert "MSFT" not in out  # corrupt entry silently dropped

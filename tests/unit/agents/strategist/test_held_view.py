"""Held-positions view rendering tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.held_view import render_held_positions_view
from agents.strategist.schema import PositionThesis
from broker.portfolio import Portfolio, Position


def _thesis(
    ticker: str = "AAPL",
    opened_price: float | None = 192.40,
    target_price: float | None = 210.0,
    stop_price: float | None = 185.0,
    catalyst: str | None = "Q3 earnings",
    rationale: str = "FCF + insider",
    horizon: str = "swing",
) -> PositionThesis:
    """Construct a PositionThesis fixture for testing."""
    return PositionThesis(
        ticker=ticker,
        opened_at=datetime(2026, 4, 22, 14, 0, tzinfo=UTC),
        opened_price=opened_price,
        opened_tag=f"open_{ticker.lower()}",
        rationale=rationale,
        horizon=horizon,
        target_price=target_price,
        stop_price=stop_price,
        catalyst=catalyst,
        last_reviewed_at=datetime(2026, 4, 22, 14, 0, tzinfo=UTC),
    )


def test_empty_portfolio_returns_no_holdings_message():
    """An empty positions dict must produce the flat-portfolio message."""
    pf = Portfolio(cash=1000.0, positions={})
    out = render_held_positions_view(positions={}, portfolio=pf)
    assert "No held positions" in out


def test_single_holding_block_includes_all_required_lines():
    """Every expected field label and value must appear in the rendered block."""
    thesis = _thesis()
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf
    )
    assert "AAPL" in out
    assert "Opened:" in out
    assert "192.40" in out
    assert "Why:" in out
    assert "FCF + insider" in out
    assert "Aim:" in out
    assert "210.00" in out
    assert "185.00" in out
    assert "Horizon:" in out
    assert "swing" in out
    assert "Catalyst:" in out
    assert "Q3 earnings" in out
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
        positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf
    )
    assert "+5" in out  # 5 % gain from open


def test_pnl_pct_negative_rendered_with_minus_sign():
    """A loss from open must appear with a '-' prefix in the output."""
    thesis = _thesis(opened_price=200.0)
    pf = Portfolio(
        cash=0.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=200.0, last_price=190.0)},
    )
    out = render_held_positions_view(
        positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf
    )
    assert "-5" in out  # 5 % loss from open


def test_no_target_no_stop_renders_none_message():
    """When both target and stop are None the '(none set at open)' message must appear."""
    thesis = _thesis(target_price=None, stop_price=None, catalyst=None)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf
    )
    assert "(none set at open)" in out


def test_no_catalyst_omits_catalyst_line():
    """When catalyst is None the 'Catalyst:' label must not appear."""
    thesis = _thesis(catalyst=None)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf
    )
    assert "Catalyst:" not in out


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
        positions={"AAPL": aapl, "MSFT": msft}, portfolio=pf,
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
    out = render_held_positions_view(positions={"AAPL": thesis_inst}, portfolio=pf)
    assert "AAPL" in out


def test_none_opened_price_renders_pending_placeholders_without_raising():
    """When ``opened_price`` is ``None`` (executor has not yet stamped the fill
    price) the renderer must avoid every divide-by-zero path and instead emit
    explicit "pending" placeholders.

    Concretely, on a freshly-opened position the strategist emits
    ``opened_price=None`` (see ``PositionThesis`` docstring for the executor
    handoff).  Within the same tick, before the executor's BUY clears, the
    held-view renderer used to divide ``(target_price - None) / None`` and
    crash the tick — the pre-fix backtest produced exactly this
    ``ZeroDivisionError``.  This test pins the post-fix behaviour:

      - the header line shows "(open price pending)" instead of "$0.00"
      - the Aim line shows absolute target / stop prices but omits the
        signed percent-from-open (no honest denominator)
      - the Now line shows the live price + weight and an
        "(unrealised pending open price)" placeholder
    """
    thesis = _thesis(opened_price=None)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=0.0, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf
    )
    assert "(open price pending)" in out
    assert "$210.00" in out                    # absolute target still shown
    assert "$185.00" in out                    # absolute stop still shown
    assert "from open" not in out              # percent-from-open suppressed
    assert "(unrealised pending open price)" in out
    assert "$198.50" in out                    # live price still rendered


def test_zero_opened_price_treated_as_unknown():
    """Legacy persistence rows may still carry ``opened_price=0.0``; the
    renderer must treat ``0.0`` the same as ``None`` to avoid the
    divide-by-zero that the pre-fix code produced.

    The architectural fix made ``opened_price`` Optional, but rows persisted
    before the fix can still appear with ``0.0`` after a round-trip through
    ``model_dump`` / DB.  Treating both values identically is the single
    guard the renderer needs.
    """
    thesis = _thesis(opened_price=0.0)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=0.0, last_price=198.50)},
    )
    out = render_held_positions_view(
        positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf
    )
    assert "(open price pending)" in out
    assert "from open" not in out
    assert "(unrealised pending open price)" in out


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
        positions={"AAPL": good_thesis, "MSFT": "not-a-thesis"}, portfolio=pf
    )
    assert "AAPL" in out   # good entry rendered
    assert "MSFT" not in out  # corrupt entry silently dropped

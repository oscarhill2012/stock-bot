"""Chunk 5 — held-view rendering tests for the Spec B rewrite.

The pre-spec ``render_held_positions_view`` rendered an "Opened / Why /
Aim / Horizon / Catalyst / Now" block. The Spec B rewrite splits that
into two blocks per position:

  * ``Your commitments on entry`` — the immutable promise the strategist
    made at open (rationale, target, stop, catalyst, horizon).
  * ``Evolution`` — what has changed since open (price drift, time held,
    distance to target / stop in $ and %, the verb used on the most
    recent review).

This test module covers the new contract end-to-end:
  * empty positions → flat-portfolio fallback unchanged.
  * populated positions → both blocks rendered.
  * Invariant 4 — ``last_reviewed_reason`` MUST NOT appear in the
    rendered text (Principle 2 — the LLM should never read its own
    prior-tick justification).
  * percent-to-target / percent-to-stop arithmetic is computed from the
    CURRENT price (not the entry price), so the LLM sees how much
    further the catalyst has to run.
  * null target / stop renders "no target set" rather than crashing.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.strategist.held_view import render_held_positions_view
from agents.strategist.position_thesis import PositionThesis
from broker.portfolio import Portfolio, Position


def _thesis(
    *,
    ticker:                 str = "AVGO",
    opened_at:              datetime = datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
    opened_tick_id:         str = "tick_001",
    opened_price:           float = 100.0,
    weight:                 float = 0.05,
    target_price:           float | None = 120.0,
    stop_price:             float | None =  90.0,
    catalyst:               str | None  = "Q3 guidance call",
    horizon:                str = "swing",
    rationale:              str = "Cloud-AI margin expansion thesis",
    last_reviewed_at:       datetime = datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
    last_reviewed_decision: str = "open",
    last_reviewed_reason:   str = "INVARIANT-4-CANARY: this string must never appear in held-view output",
) -> PositionThesis:
    """Construct a PositionThesis fixture with all fields under test control."""

    return PositionThesis(
        ticker                 = ticker,
        opened_at              = opened_at,
        opened_tick_id         = opened_tick_id,
        opened_price           = opened_price,
        weight                 = weight,
        target_price           = target_price,
        stop_price             = stop_price,
        catalyst               = catalyst,
        horizon                = horizon,
        rationale              = rationale,
        last_reviewed_at       = last_reviewed_at,
        last_reviewed_decision = last_reviewed_decision,
        last_reviewed_reason   = last_reviewed_reason,
    )


def _portfolio(ticker: str = "AVGO", last_price: float = 110.0) -> Portfolio:
    """Single-position portfolio at ``last_price`` so evolution columns can compute."""

    return Portfolio(
        cash      = 950.0,
        positions = {ticker: Position(quantity=1.0, avg_cost=100.0, last_price=last_price)},
    )


def test_held_view_empty_renders_cold_start_fallback() -> None:
    """An empty positions dict must produce the flat-portfolio sentinel."""

    out = render_held_positions_view(
        positions = {},
        portfolio = Portfolio(cash=1000.0, positions={}),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )
    assert out == "(No held positions — portfolio is flat.)"


def test_held_view_renders_evolution_columns() -> None:
    """Populated positions must render both commitments and evolution blocks."""

    thesis = _thesis()
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(last_price=110.0),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )

    # Both block headers must be present.
    assert "Your commitments on entry"   in out
    assert "Evolution"                   in out

    # Evolution columns block — see spec's PositionThesis evolution section
    # in docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md.
    assert "Held for:"   in out                       # time-elapsed line
    assert "Now:"        in out                       # current price line
    assert "To target:"  in out                       # distance-to-target line
    assert "To stop:"    in out                       # distance-to-stop line
    assert "Reviewed:"   in out                       # last_reviewed line
    assert "(open)"      in out                       # last_reviewed_decision rendered alongside


def test_held_view_does_not_leak_last_reviewed_reason() -> None:
    """Invariant 4 — the rendered text must NOT contain ``last_reviewed_reason``.

    Principle 2 of the spec — the LLM must never read its own prior-tick
    'what's changed' justification. The canary string on the fixture is
    explicitly distinctive so a substring search is sufficient.
    """

    thesis = _thesis()
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )
    assert "INVARIANT-4-CANARY" not in out


def test_held_view_computes_pct_to_target_and_stop_correctly() -> None:
    """Distance-to-target and distance-to-stop are computed from CURRENT price.

    Entry 100, current 110, target 120, stop 90.
    To-target: (120 - 110) / 110 = +9.09 %.
    To-stop:   (90  - 110) / 110 = -18.18 %.
    """

    thesis = _thesis(opened_price=100.0, target_price=120.0, stop_price=90.0)
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(last_price=110.0),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )

    # Pin both the label and the signed-dollar + percentage together so a
    # regression in either the label or the arithmetic causes a test failure.
    # Format: "    To target:  ${delta:+.2f}  ({pct:+.1f}% from now)"
    assert "To target:  $+10.00  (+9.1% from now)" in out
    assert "To stop:    $-20.00  (-18.2% from now)" in out


def test_held_view_handles_null_target_and_stop() -> None:
    """Null target / stop must render "no target set" / "no stop set" — never crash."""

    thesis = _thesis(target_price=None, stop_price=None)
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )

    assert "Your commitments on entry" in out
    # The non-negotiable contract: no crash and no division-by-None.
    assert "AVGO" in out
    # Renderer must emit the explicit sentinel strings for null target / stop.
    assert "(no target set)" in out
    assert "(no stop set)"   in out

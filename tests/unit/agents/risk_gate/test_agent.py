"""Unit tests for the risk-gate buy-delta clamp (Task 10 — iter-3 schema rewrite).

The risk gate's ``apply_buy_delta_clamp`` helper enforces a per-trade delta
cap on buy stances before their target weights reach the constraint loop.
This is defence-in-depth: the ``TickerStance`` schema already forbids
``weight > 0.05`` at construction time; the risk-gate clamp fires if a
caller ever bypasses that validation (e.g. by constructing the object via
``model_construct`` without validators).

Interface under test
--------------------
``constraints.apply_buy_delta_clamp(stances, config)``
    Mutates ``stances`` in-place (clamping weight on any buy stance that
    exceeds ``config.max_delta_per_buy``) and returns a list of
    ``ClampRecord`` objects — one per clamped stance.

We also test the ``position_cap_exceeded`` path (the existing
``max_position`` clamp from ``apply_constraints``) to confirm it still
fires correctly under the new config-driven code path.
"""
from __future__ import annotations

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_buy_stance(ticker: str, weight: float):
    """Construct a TickerStance via model_construct to bypass the schema
    cap and let the risk-gate clamp fire (the scenario being tested).

    ``model_construct`` skips Pydantic validators — we deliberately
    want a weight above the schema cap so the risk-gate clamp has
    something to act on.

    Parameters
    ----------
    ticker:  The stock ticker symbol.
    weight:  Target buy-delta weight (may exceed the schema-level cap).

    Returns
    -------
    TickerStance with intent='buy'.
    """
    from agents.strategist.stance_schema import TickerStance
    return TickerStance.model_construct(
        ticker=ticker,
        intent="buy",
        weight=weight,
        rationale="test bypass — validators skipped intentionally",
        catalyst=None,
    )


# ── buy-delta clamp tests ─────────────────────────────────────────────────────

def test_buy_delta_at_cap_passes_unchanged():
    """A buy stance whose weight equals max_delta_per_buy should pass through
    the clamp without modification and produce no clamp record."""
    from agents.risk_gate.constraints import apply_buy_delta_clamp
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    stance = _make_buy_stance("AAPL", cfg.max_delta_per_buy)

    clamps = apply_buy_delta_clamp([stance], cfg)

    assert stance.weight == cfg.max_delta_per_buy
    assert clamps == []


def test_buy_delta_below_cap_passes_unchanged():
    """A buy stance whose weight is well below the cap should be untouched."""
    from agents.risk_gate.constraints import apply_buy_delta_clamp
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    stance = _make_buy_stance("TSLA", 0.02)  # below 0.05 cap

    clamps = apply_buy_delta_clamp([stance], cfg)

    assert stance.weight == pytest.approx(0.02)
    assert clamps == []


def test_buy_delta_above_cap_is_clamped():
    """A buy stance whose weight exceeds max_delta_per_buy must be
    clamped to the cap and a ClampRecord with reason 'buy_delta_exceeded'
    must be emitted.

    This is the core defence-in-depth scenario: a caller that bypassed the
    schema validator (e.g. via model_construct) still gets clamped at the
    risk-gate layer.
    """
    from agents.risk_gate.constraints import apply_buy_delta_clamp
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    over_cap = cfg.max_delta_per_buy + 0.03   # any delta above the configured cap

    stance = _make_buy_stance("NVDA", over_cap)
    clamps = apply_buy_delta_clamp([stance], cfg)

    # Weight must be clamped to the cap.
    assert stance.weight == pytest.approx(cfg.max_delta_per_buy)

    # Exactly one ClampRecord must be emitted.
    assert len(clamps) == 1
    assert clamps[0].rule == "buy_delta_exceeded"
    assert clamps[0].ticker == "NVDA"
    assert clamps[0].before == pytest.approx(over_cap)
    assert clamps[0].after == pytest.approx(cfg.max_delta_per_buy)


def test_sell_and_update_stances_are_not_clamped():
    """Sell and update stances must pass through the buy-delta clamp
    untouched — the clamp is buy-only."""
    from agents.risk_gate.constraints import apply_buy_delta_clamp
    from agents.strategist.stance_schema import TickerStance
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()

    # Sell with explicit partial weight (within sell range).
    sell_stance = TickerStance(ticker="MSFT", intent="sell", weight=0.10, rationale="exit")
    update_stance = TickerStance(ticker="GOOG", intent="update", rationale="thesis revision")

    clamps = apply_buy_delta_clamp([sell_stance, update_stance], cfg)

    assert sell_stance.weight == pytest.approx(0.10)
    assert clamps == []


def test_multiple_buys_all_clamped():
    """All buy stances above the cap in a mixed list are clamped; below-cap
    stances are left unchanged. The returned list length matches the number
    of clamped stances."""
    from agents.risk_gate.constraints import apply_buy_delta_clamp
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    cap = cfg.max_delta_per_buy

    over1 = _make_buy_stance("AAPL", cap + 0.02)   # will be clamped
    over2 = _make_buy_stance("TSLA", cap + 0.05)   # will be clamped
    under = _make_buy_stance("AMZN", cap - 0.01)   # will NOT be clamped

    clamps = apply_buy_delta_clamp([over1, over2, under], cfg)

    assert over1.weight == pytest.approx(cap)
    assert over2.weight == pytest.approx(cap)
    assert under.weight == pytest.approx(cap - 0.01)
    assert len(clamps) == 2
    assert {c.ticker for c in clamps} == {"AAPL", "TSLA"}
    assert all(c.rule == "buy_delta_exceeded" for c in clamps)


# ── position-cap clamp integration test ─────────────────────────────────────

def test_position_cap_clamp_fires_via_apply_constraints():
    """A proposed weight above MAX_POSITION_WEIGHT triggers a
    'max_position' ClampRecord in apply_constraints.

    This confirms the existing position-cap logic still works correctly
    alongside the new buy-delta clamp.
    """
    from agents.risk_gate.constraints import apply_constraints

    proposed = {"AAPL": 0.99}   # far above 0.20 cap
    current  = {"AAPL": 0.18}

    clamps = apply_constraints(proposed, current)

    position_clamps = [c for c in clamps if c.rule == "max_position"]
    assert position_clamps, "expected a max_position ClampRecord"
    assert position_clamps[0].ticker == "AAPL"
    assert position_clamps[0].after == pytest.approx(0.20)

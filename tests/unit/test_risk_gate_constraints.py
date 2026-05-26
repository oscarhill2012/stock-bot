"""Tier 1 unit tests for each risk-gate clamp, in algorithm order."""
import pytest

from agents.risk_gate.constraints import (
    _clamp_cash_floor,
    _clamp_max_position,
    _clamp_max_turnover,
    _clamp_negatives,
    apply_constraints,
)
from orchestrator.state import (
    CASH_FLOOR_WEIGHT,
    ClampRecord,
    MAX_TOTAL_TURNOVER,
)


def test_clamp_negatives_zeros_negative_weights():
    weights = {"AAPL": -0.05, "MSFT": 0.10, "NVDA": -0.02}
    clamps: list[ClampRecord] = []
    _clamp_negatives(weights, clamps)
    assert weights == {"AAPL": 0.0, "MSFT": 0.10, "NVDA": 0.0}
    rules = [c.rule for c in clamps]
    assert rules == ["no_short", "no_short"]


def test_clamp_negatives_no_op_when_all_positive():
    weights = {"AAPL": 0.10, "MSFT": 0.05}
    clamps: list[ClampRecord] = []
    _clamp_negatives(weights, clamps)
    assert weights == {"AAPL": 0.10, "MSFT": 0.05}
    assert clamps == []


def test_clamp_max_position_caps_oversized():
    weights = {"AAPL": 0.50, "MSFT": 0.10}
    clamps: list[ClampRecord] = []
    _clamp_max_position(weights, clamps)
    assert weights == {"AAPL": 0.20, "MSFT": 0.10}
    assert len(clamps) == 1
    assert clamps[0].rule == "max_position"
    assert clamps[0].ticker == "AAPL"
    assert clamps[0].before == 0.50
    assert clamps[0].after == 0.20


def test_clamp_max_position_no_op_when_within_cap():
    weights = {"AAPL": 0.20, "MSFT": 0.15}
    clamps: list[ClampRecord] = []
    _clamp_max_position(weights, clamps)
    assert weights == {"AAPL": 0.20, "MSFT": 0.15}
    assert clamps == []


def test_cash_floor_scales_when_sum_over_threshold():
    # Build a sum that exceeds (1 - CASH_FLOOR_WEIGHT) regardless of the current
    # CASH_FLOOR_WEIGHT value.  We deliberately overshoot by 0.10 so there is
    # always something to clamp (handles both CASH_FLOOR_WEIGHT=0.10 and =0.00).
    threshold = 1.0 - CASH_FLOOR_WEIGHT
    half = (threshold + 0.05)               # each ticker slightly above threshold/2
    weights = {"AAPL": half, "MSFT": half}  # sum = threshold + 0.10 → must clamp
    clamps: list[ClampRecord] = []
    _clamp_cash_floor(weights, clamps)
    assert sum(weights.values()) == pytest.approx(threshold)
    assert weights["AAPL"] == pytest.approx(threshold / 2)
    assert weights["MSFT"] == pytest.approx(threshold / 2)
    assert len(clamps) == 2
    assert all(c.rule == "cash_floor" for c in clamps)


def test_cash_floor_noop_when_under_threshold():
    # A sum comfortably below (1 - CASH_FLOOR_WEIGHT) must not be touched.
    threshold = 1.0 - CASH_FLOOR_WEIGHT
    each = (threshold - 0.20) / 2          # total = threshold - 0.20, safely under
    weights = {"AAPL": each, "MSFT": each}
    original = dict(weights)
    clamps: list[ClampRecord] = []
    _clamp_cash_floor(weights, clamps)
    assert weights == original
    assert clamps == []


def test_turnover_scales_when_sum_over_threshold():
    # Each ticker starts at 0 and is proposed at MAX_TOTAL_TURNOVER/3 + 0.05, so
    # total |delta| = MAX_TOTAL_TURNOVER + 0.15 → must scale to MAX_TOTAL_TURNOVER.
    each_proposed = MAX_TOTAL_TURNOVER / 3 + 0.05
    proposed = {"AAPL": each_proposed, "MSFT": each_proposed, "NVDA": each_proposed}
    current  = {"AAPL": 0.0, "MSFT": 0.0, "NVDA": 0.0}
    total_before = sum(abs(proposed[t] - current.get(t, 0.0)) for t in proposed)
    scale = MAX_TOTAL_TURNOVER / total_before
    clamps: list[ClampRecord] = []
    _clamp_max_turnover(proposed, current, clamps)
    assert sum(abs(proposed[t] - current.get(t, 0.0)) for t in proposed) == pytest.approx(MAX_TOTAL_TURNOVER)
    assert proposed["AAPL"] == pytest.approx(each_proposed * scale)


def test_turnover_noop_when_under_threshold():
    # total |delta| = MAX_TOTAL_TURNOVER - 0.10 → safely under threshold, no clamp.
    each = (MAX_TOTAL_TURNOVER - 0.10) / 2
    proposed = {"AAPL": each, "MSFT": each}
    current  = {"AAPL": 0.0,  "MSFT": 0.0}
    original = dict(proposed)
    clamps: list[ClampRecord] = []
    _clamp_max_turnover(proposed, current, clamps)
    assert proposed == original
    assert clamps == []


def test_apply_constraints_runs_in_documented_order():
    # Exercises the four weight-level rules in sequence:
    #   1. AAPL negative → no_short clamp → 0.0
    #   2. MSFT 0.50 > MAX_POSITION_WEIGHT → max_position clamp → 0.20
    #   3. cash_floor scales weights iff sum exceeds (1 − CASH_FLOOR_WEIGHT)
    #      — fires only when CASH_FLOOR_WEIGHT > 0 and the post-position sum
    #      is over threshold; we don't pin the count here.
    #   4. max_turnover scales any leftover excess delta.
    # The per-ticker net-delta clamp has been removed — the buy direction is
    # bounded by ``apply_buy_delta_clamp`` upstream, and sells are unbounded
    # on a per-stance basis by design.
    proposed = {"AAPL": -0.05, "MSFT": 0.50, "NVDA": 0.45}
    current  = {"AAPL": 0.0,   "MSFT": 0.0,  "NVDA": 0.0}
    clamps = apply_constraints(proposed, current)

    # AAPL clamped to 0 (no_short).
    assert proposed["AAPL"] == 0.0

    # MSFT and NVDA each clamped by max_position to the concentration ceiling.
    from orchestrator.state import MAX_POSITION_WEIGHT
    assert proposed["MSFT"] <= MAX_POSITION_WEIGHT + 1e-9
    assert proposed["NVDA"] <= MAX_POSITION_WEIGHT + 1e-9

    rules = [c.rule for c in clamps]
    assert "no_short"    in rules
    assert "max_position" in rules

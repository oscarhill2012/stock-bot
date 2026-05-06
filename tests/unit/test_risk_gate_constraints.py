"""Tier 1 unit tests for each risk-gate clamp, in algorithm order."""
import pytest

from agents.risk_gate.constraints import _clamp_negatives, _clamp_max_position, _clamp_cash_floor, _clamp_max_delta, _clamp_max_turnover
from orchestrator.state import ClampRecord


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
    weights = {"AAPL": 0.50, "MSFT": 0.50}     # sum = 1.0, must shrink to 0.90
    clamps: list[ClampRecord] = []
    _clamp_cash_floor(weights, clamps)
    assert sum(weights.values()) == pytest.approx(0.90)
    assert weights["AAPL"] == pytest.approx(0.45)
    assert weights["MSFT"] == pytest.approx(0.45)
    assert len(clamps) == 2
    assert all(c.rule == "cash_floor" for c in clamps)


def test_cash_floor_noop_when_under_threshold():
    weights = {"AAPL": 0.40, "MSFT": 0.40}     # sum = 0.80, fine
    clamps: list[ClampRecord] = []
    _clamp_cash_floor(weights, clamps)
    assert weights == {"AAPL": 0.40, "MSFT": 0.40}
    assert clamps == []


def test_max_delta_caps_per_ticker_buy():
    proposed = {"AAPL": 0.10}
    current = {"AAPL": 0.05}                   # delta = +0.05, must cap at +0.01
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["AAPL"] == pytest.approx(0.06)
    assert clamps[0].rule == "max_delta"


def test_max_delta_caps_per_ticker_sell():
    proposed = {"AAPL": 0.0}
    current = {"AAPL": 0.05}                   # delta = -0.05, must cap at -0.01
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["AAPL"] == pytest.approx(0.04)


def test_max_delta_no_op_within_threshold():
    proposed = {"AAPL": 0.06}
    current = {"AAPL": 0.05}                   # delta = +0.01 — exactly at threshold
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["AAPL"] == 0.06
    assert clamps == []


def test_max_delta_handles_new_position():
    proposed = {"NVDA": 0.05}
    current = {}                               # opening — full 0.05 must clamp to 0.01
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["NVDA"] == pytest.approx(0.01)


def test_turnover_scales_when_sum_over_threshold():
    proposed = {"AAPL": 0.20, "MSFT": 0.20, "NVDA": 0.20}
    current  = {"AAPL": 0.0,  "MSFT": 0.0,  "NVDA": 0.0}
    # total |delta| = 0.60; must scale all to total = 0.30 (each ÷ 2)
    clamps: list[ClampRecord] = []
    _clamp_max_turnover(proposed, current, clamps)
    assert sum(abs(proposed[t] - current.get(t, 0.0)) for t in proposed) == pytest.approx(0.30)
    assert proposed["AAPL"] == pytest.approx(0.10)


def test_turnover_noop_when_under_threshold():
    proposed = {"AAPL": 0.10, "MSFT": 0.10}
    current  = {"AAPL": 0.0,  "MSFT": 0.0}     # total |delta| = 0.20, fine
    clamps: list[ClampRecord] = []
    _clamp_max_turnover(proposed, current, clamps)
    assert proposed == {"AAPL": 0.10, "MSFT": 0.10}
    assert clamps == []

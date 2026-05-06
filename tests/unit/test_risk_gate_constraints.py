"""Tier 1 unit tests for each risk-gate clamp, in algorithm order."""
import pytest

from agents.risk_gate.constraints import _clamp_negatives, _clamp_max_position
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

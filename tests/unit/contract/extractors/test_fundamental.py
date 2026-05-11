"""Fundamental feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.fundamental import extract_fundamental_features

FIXTURE = Path("tests/fixtures/contract/fundamental_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    expected = {
        "pe_trailing", "pe_forward", "peg",
        "revenue_growth_yoy", "profit_margin", "debt_to_equity",
        "fcf_yield_pct", "roe", "analyst_rating_avg",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r}"


def test_pe_values_carried_through(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    assert features["pe_trailing"] == pytest.approx(28.5)
    assert features["pe_forward"] == pytest.approx(26.0)


def test_fcf_yield_computed_from_fcf_and_market_cap(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    expected = (95_000_000_000 / 3_000_000_000_000) * 100
    assert features["fcf_yield_pct"] == pytest.approx(expected, rel=0.01)


def test_handles_empty_data_gracefully():
    features = extract_fundamental_features({}, ticker="AAPL")
    for v in features.values():
        assert v == 0.0


def test_handles_zero_market_cap_in_fcf_yield():
    features = extract_fundamental_features(
        {"free_cash_flow": 1_000_000, "market_cap": 0}, ticker="AAPL"
    )
    assert features["fcf_yield_pct"] == 0.0  # no divide-by-zero

"""Technical feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.technical import extract_technical_features

FIXTURE = Path("tests/fixtures/contract/technical_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    expected = {
        "rsi_14", "pct_change_5d", "pct_change_20d",
        "vol_ratio_20d", "atr_pct_14",
        "dist_from_high_52w_pct", "dist_from_low_52w_pct",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r}"


def test_uptrend_fixture_has_positive_5d_change(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert features["pct_change_5d"] > 0


def test_uptrend_fixture_rsi_above_50(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    # Steady uptrend should put RSI in the 50–100 range
    assert features["rsi_14"] > 50.0
    assert features["rsi_14"] <= 100.0


def test_dist_from_52w_high_negative(aapl_data):
    """Latest close (193.5) is below 52w high (200) → negative percent."""
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert features["dist_from_high_52w_pct"] < 0


def test_handles_empty_data_gracefully():
    """Empty data → all-zero features (no exception)."""
    features = extract_technical_features({}, ticker="AAPL")
    for v in features.values():
        assert v == 0.0


def test_handles_short_history_gracefully():
    """Too few price bars to compute RSI(14) → returns 0.0 for indicators that need history."""
    short = {
        "ticker": "AAPL",
        "price_history": [
            {"date": "2026-05-07", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"date": "2026-05-08", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
        ],
    }
    features = extract_technical_features(short, ticker="AAPL")
    # Should not raise. RSI/ATR should be 0.0 (insufficient history).
    assert features["rsi_14"] == 0.0
    assert features["atr_pct_14"] == 0.0

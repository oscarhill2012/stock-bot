"""Technical feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from contract.extractors.technical import _KEYS, extract_technical_features
from data.models.company_ratios import CompanyRatios

FIXTURE = Path("tests/fixtures/contract/technical_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    """The returned dict must contain exactly the keys declared in _KEYS."""
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert set(features.keys()) == set(_KEYS)


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


# ---------------------------------------------------------------------------
# Task 2.2 — Fix A: golden/death cross + beta damping from ratios sub-key
# ---------------------------------------------------------------------------

def test_technical_emits_golden_cross_when_50d_above_200d():
    """50-day MA above 200-day MA AND price above 50-day → golden_cross == 1.0."""
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=180.0, fifty_day_average=170.0,
        two_hundred_day_average=150.0, beta=1.2,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    assert features["golden_cross"] == 1.0
    assert features["death_cross"] == 0.0


def test_technical_emits_death_cross_when_50d_below_200d():
    """50-day MA below 200-day MA AND price below 50-day → death_cross == 1.0."""
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=140.0, fifty_day_average=145.0,
        two_hundred_day_average=160.0, beta=1.2,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    assert features["death_cross"] == 1.0
    assert features["golden_cross"] == 0.0


def test_technical_emits_beta_confidence_damping():
    """beta_confidence_damping should be 1/(1+|beta-1|) and non-zero when beta is set."""
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=150.0, beta=1.5,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    # beta=1.5 → |1.5-1| = 0.5 → 1/(1+0.5) = 0.6667
    assert abs(features["beta_confidence_damping"] - (1.0 / 1.5)) < 1e-6


# ---------------------------------------------------------------------------
# Task 2.3 — Fix B: 52-week distance from bars fallback
# ---------------------------------------------------------------------------

def _bar(close: float) -> dict:
    """Construct a minimal OHLCV bar dict for testing."""
    return {
        "timestamp": datetime(2023, 3, 10, tzinfo=UTC).isoformat(),
        "open": close, "high": close, "low": close,
        "close": close, "volume": 1_000_000,
    }


def test_technical_emits_52w_distance_from_bars():
    """52-week high/low computed from bars when ratios fast-path is absent.

    Distances are expressed as signed percentages matching the verdict heuristic
    convention (e.g. -33.33 = 33.33 % below the 52-week high).
    """
    bars = [_bar(100.0) for _ in range(260)]
    # Override one bar in the middle to be the 52-week high.
    bars[100]["close"] = 180.0
    bars[100]["high"]  = 180.0
    # Current price (last bar).
    bars[-1]["close"] = 120.0
    bars[-1]["high"]  = 120.0

    raw = {"ticker": "AAPL", "bars": bars, "ratios": {}}
    features = extract_technical_features(raw, state={})

    # dist_from_high = (last / high52 - 1) × 100  →  (120/180 - 1) × 100 = -33.33…
    expected_high_dist = (120.0 / 180.0 - 1.0) * 100.0
    assert abs(features["dist_from_high_52w_pct"] - expected_high_dist) < 1e-4


def test_technical_52w_ratios_fast_path_takes_priority():
    """When ratios contain fifty_two_week_high, bars-derived value is ignored."""
    bars = [_bar(100.0) for _ in range(30)]
    bars[-1]["close"] = 95.0

    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=95.0, fifty_two_week_high=200.0, fifty_two_week_low=80.0,
    )
    raw = {"ticker": "AAPL", "bars": bars, "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})

    # (95 / 200 - 1) × 100 = -52.5 %
    expected = (95.0 / 200.0 - 1.0) * 100.0
    assert abs(features["dist_from_high_52w_pct"] - expected) < 1e-4

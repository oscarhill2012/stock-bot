"""Fundamental feature extractor tests — Tier 1, no LLM.

Phase 5 update: the extractor now accepts a triad payload shape
``{"ratios": dict, "filings": list, "insider": Form4Bundle}``.  Fixtures and
tests have been updated accordingly; the locked key catalogue now includes
insider and filings-derived columns.

Phase 5 data-model split: the ``"stats"`` key is renamed ``"ratios"`` at the
fetch-callback and extractor levels.  Fixture wrappers updated here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.fundamental import _KEYS, extract_fundamental_features
from data.models import Form4Bundle

FIXTURE = Path("tests/fixtures/contract/fundamental_aapl.json")


@pytest.fixture
def aapl_data():
    """Load the AAPL fixture and wrap it in the Phase 5 triad shape.

    Uses ``"ratios"`` key (renamed from ``"stats"`` in the Phase 5 data-model split).
    """
    ratios = json.loads(FIXTURE.read_text())
    return {
        "ratios": ratios,
        "filings": [],
        "insider": Form4Bundle(trades=[], derivatives=[]),
    }


def test_extracts_required_keys(aapl_data):
    """The returned dict must contain exactly the keys declared in _KEYS."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    assert set(features.keys()) == set(_KEYS)


def test_all_features_are_floats(aapl_data):
    """Every value in the feature dict must be a plain float."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r}"


def test_pe_values_carried_through(aapl_data):
    """P/E values from the ratios sub-dict must survive extraction unchanged."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    assert features["pe_trailing"] == pytest.approx(28.5)
    assert features["pe_forward"] == pytest.approx(26.0)


def test_fcf_yield_computed_from_fcf_and_market_cap(aapl_data):
    """fcf_yield_pct = (fcf / market_cap) × 100 using ratios sub-dict values."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    expected = (95_000_000_000 / 3_000_000_000_000) * 100
    assert features["fcf_yield_pct"] == pytest.approx(expected, rel=0.01)


def test_handles_empty_data_gracefully():
    """An entirely empty raw dict must return all-zero features without error."""
    features = extract_fundamental_features({}, ticker="AAPL")
    for v in features.values():
        assert v == 0.0


def test_handles_zero_market_cap_in_fcf_yield():
    """Zero market cap in ratios must not raise ZeroDivisionError."""
    features = extract_fundamental_features(
        {
            "ratios": {"free_cash_flow": 1_000_000, "market_cap": 0},
            "filings": [],
            "insider": Form4Bundle(trades=[], derivatives=[]),
        },
        ticker="AAPL",
    )
    assert features["fcf_yield_pct"] == 0.0

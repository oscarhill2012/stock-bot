"""Smart-money feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.smart_money import extract_smart_money_features

AAPL_FIXTURE = Path("tests/fixtures/contract/smart_money_aapl.json")
NODATA_FIXTURE = Path("tests/fixtures/contract/smart_money_no_data.json")


@pytest.fixture
def aapl_data():
    return json.loads(AAPL_FIXTURE.read_text())


@pytest.fixture
def empty_data():
    return json.loads(NODATA_FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    expected = {
        "n_politicians", "n_buys_30d", "n_sells_30d",
        "total_dollar_value_buys", "total_dollar_value_sells",
        "net_flow_dollar", "is_no_data",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    for v in features.values():
        assert isinstance(v, float)


def test_unique_filer_count(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    # Three distinct filers in the fixture
    assert features["n_politicians"] == 3.0


def test_buy_sell_counts(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["n_buys_30d"] == 3.0
    assert features["n_sells_30d"] == 1.0


def test_dollar_totals(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["total_dollar_value_buys"] == 250_000 + 100_000 + 75_000
    assert features["total_dollar_value_sells"] == 50_000.0
    assert features["net_flow_dollar"] == (425_000 - 50_000)


def test_is_no_data_zero_when_filings_present(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["is_no_data"] == 0.0


def test_is_no_data_one_when_no_filings(empty_data):
    features = extract_smart_money_features(empty_data, ticker="TSLA")
    assert features["is_no_data"] == 1.0
    assert features["n_politicians"] == 0.0
    assert features["n_buys_30d"] == 0.0
    assert features["total_dollar_value_buys"] == 0.0


def test_is_no_data_one_when_empty_dict():
    features = extract_smart_money_features({}, ticker="UNKNOWN")
    assert features["is_no_data"] == 1.0

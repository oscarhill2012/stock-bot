"""News feature extractor tests — Tier 1, no LLM.

Renamed from test_sentiment.py in Task 6.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.news import extract_news_features

FIXTURE = Path("tests/fixtures/contract/news_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    expected = {
        "news_count_7d", "pct_news_positive_7d", "pct_news_negative_7d",
        "headline_polarity_mean_7d", "social_volume_z",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    for v in features.values():
        assert isinstance(v, float)


def test_news_count_matches_fixture(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    assert features["news_count_7d"] == 7.0


def test_positive_share_calculated(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    # 5 of 7 items have polarity > 0 → ~71%
    assert features["pct_news_positive_7d"] == pytest.approx(5 / 7 * 100, rel=0.01)


def test_polarity_mean(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    polarities = [0.8, 0.6, 0.5, -0.4, 0.1, 0.2, 0.0]
    assert features["headline_polarity_mean_7d"] == pytest.approx(sum(polarities) / len(polarities), rel=0.01)


def test_social_volume_z_passthrough(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    assert features["social_volume_z"] == pytest.approx(1.4)


def test_handles_empty_news():
    features = extract_news_features({"news_items": []}, ticker="AAPL")
    assert features["news_count_7d"] == 0.0
    assert features["pct_news_positive_7d"] == 0.0
    assert features["headline_polarity_mean_7d"] == 0.0


def test_handles_missing_social_volume():
    """social_volume_z is optional — defaults to 0.0 when no provider supplies it."""
    features = extract_news_features({"news_items": []}, ticker="AAPL")
    assert features["social_volume_z"] == 0.0

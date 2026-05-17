"""News feature extractor tests — Tier 1, no LLM.

Renamed from test_sentiment.py in Task 6.
Phase 7 (Task 2.9 / Fix I): extractor now reads ``sentiment`` field on article
dicts, not ``polarity``.  Fixture updated accordingly.
Phase 7 (Task 2.10 / Fix J): new tests for time-weighted counters and recency.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from contract.extractors.news import _KEYS, extract_news_features

FIXTURE = Path("tests/fixtures/contract/news_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    """The returned dict must contain exactly the keys declared in _KEYS."""
    features = extract_news_features(aapl_data, ticker="AAPL")
    assert set(features.keys()) == set(_KEYS)


def test_all_features_are_floats(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    for v in features.values():
        assert isinstance(v, float)


def test_news_count_matches_fixture(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    assert features["news_count_7d"] == 7.0


def test_positive_share_calculated(aapl_data):
    """5 of 7 items have sentiment > 0 → ~71%."""
    features = extract_news_features(aapl_data, ticker="AAPL")
    # 5 of 7 items have sentiment > 0 → ~71%
    assert features["pct_news_positive_7d"] == pytest.approx(5 / 7 * 100, rel=0.01)


def test_polarity_mean(aapl_data):
    """headline_polarity_mean (and the back-compat _7d alias) must match the fixture sentiments."""
    features = extract_news_features(aapl_data, ticker="AAPL")
    sentiments = [0.8, 0.6, 0.5, -0.4, 0.1, 0.2, 0.0]
    expected = sum(sentiments) / len(sentiments)
    assert features["headline_polarity_mean"] == pytest.approx(expected, rel=0.01)
    # Back-compat alias must carry the same value.
    assert features["headline_polarity_mean_7d"] == pytest.approx(expected, rel=0.01)


def test_social_volume_z_passthrough(aapl_data):
    features = extract_news_features(aapl_data, ticker="AAPL")
    assert features["social_volume_z"] == pytest.approx(1.4)


def test_handles_empty_news():
    features = extract_news_features({"news_items": []}, ticker="AAPL")
    assert features["news_count_7d"] == 0.0
    assert features["pct_news_positive_7d"] == 0.0
    assert features["headline_polarity_mean"] == 0.0


def test_handles_missing_social_volume():
    """social_volume_z is optional — defaults to 0.0 when no provider supplies it."""
    features = extract_news_features({"news_items": []}, ticker="AAPL")
    assert features["social_volume_z"] == 0.0


# ---------------------------------------------------------------------------
# Task 2.9 — Fix I: reads ``sentiment`` field, not ``polarity``
# ---------------------------------------------------------------------------

def test_news_reads_sentiment_field_not_polarity():
    """Extractor must use the ``sentiment`` key from article dicts."""
    articles = [
        {
            "ticker": "AAPL", "headline": "Beat", "url": "u", "source": "av",
            "published_at": datetime(2023, 3, 10, 9, tzinfo=UTC).isoformat(),
            "sentiment": 0.5,
        },
        {
            "ticker": "AAPL", "headline": "Miss", "url": "u", "source": "av",
            "published_at": datetime(2023, 3, 10, 8, tzinfo=UTC).isoformat(),
            "sentiment": -0.3,
        },
    ]
    raw = {"ticker": "AAPL", "articles": articles}
    state = {"as_of": "2023-03-10T12:00:00+00:00"}
    f = extract_news_features(raw, state=state)
    # mean of (0.5, -0.3) = 0.1
    assert abs(f["headline_polarity_mean"] - 0.1) < 1e-9


# ---------------------------------------------------------------------------
# Task 2.10 — Fix J: time-weighted counters and recency score
# ---------------------------------------------------------------------------

def test_news_emits_time_weighted_counters_and_recency():
    """24 h / 72 h counters and recency-weighted polarity must be populated."""
    now = datetime(2023, 3, 10, 12, tzinfo=UTC)

    def _art(hours_ago: int, s: float) -> dict:
        """Construct a minimal article dict a given number of hours before ``now``."""
        pub = now - timedelta(hours=hours_ago)
        return {
            "ticker": "AAPL", "headline": "x", "url": "u", "source": "av",
            "published_at": pub.isoformat(),
            "sentiment": s,
        }

    raw = {
        "ticker": "AAPL",
        "articles": [_art(2, 0.8), _art(50, -0.2), _art(120, 0.3)],
    }
    state = {"as_of": now.isoformat()}
    f = extract_news_features(raw, state=state)

    # 2 h ago → within 24 h window.
    assert f["news_count_24h"] == 1
    # 2 h and 50 h → within 72 h window.
    assert f["news_count_72h"] == 2
    # Youngest article is 2 hours old.
    assert f["hours_since_latest_news"] == pytest.approx(2.0, abs=0.1)
    # Recency-weighted score: the 2-h article (0.8) dominates because decay is fast.
    # Its weight ≈ exp(-2*ln2/24) ≈ 0.944; 50-h article weight ≈ exp(-50*ln2/24) ≈ 0.236.
    assert f["headline_polarity_recency_weighted"] > 0.3


def test_news_no_articles_has_large_hours_since_latest():
    """When there are no articles, hours_since_latest_news must be the 9999 sentinel."""
    f = extract_news_features({"articles": []}, state={})
    assert f["hours_since_latest_news"] == pytest.approx(9999.0)

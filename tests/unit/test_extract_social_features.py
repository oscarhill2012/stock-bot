"""Tier-1 tests for extract_social_features.

Phase 7 (Task 2.11 / Fix K): updated to use the new typed-snapshot list shape
``{"snapshots": [...], "aggregate_score": ...}`` instead of the old
per-platform dict-of-dict.  The extractor signature is unchanged.
"""
from __future__ import annotations

from contract.extractors.social import extract_social_features


def test_extractor_emits_expected_keys():
    """All Phase-7 social feature keys are present."""
    features = extract_social_features({}, "AAPL")
    for key in (
        "mention_count_total", "mention_count_reddit", "mention_count_twitter",
        "social_aggregate_score", "aggregate_score",  # back-compat alias
        "score_velocity_24h",
        "platform_score_disagreement", "is_no_data",
    ):
        assert key in features


def test_extractor_no_data_path():
    """Empty payload sets is_no_data=True and zero counts."""
    f = extract_social_features({}, "AAPL")
    assert f["mention_count_total"] == 0.0
    assert f["is_no_data"] is True


def test_extractor_aggregates_across_platforms():
    """Reddit + Twitter snapshot counts sum into mention_count_total."""
    payload = {
        "snapshots": [
            {"platform": "reddit",  "mention_count": 50,  "positive_score": 0.4, "negative_score": 0.1, "score": 0.3},
            {"platform": "twitter", "mention_count": 120, "positive_score": 0.2, "negative_score": 0.2, "score": 0.0},
        ],
        "aggregate_score": 0.15,
    }
    f = extract_social_features(payload, "AAPL")
    assert f["mention_count_total"] == 170.0
    assert f["mention_count_reddit"] == 50.0
    assert f["mention_count_twitter"] == 120.0
    assert f["is_no_data"] is False


def test_platform_score_disagreement_high_when_platforms_diverge():
    """abs(reddit_net - twitter_net) above zero registers in platform_score_disagreement."""
    payload = {
        "snapshots": [
            {"platform": "reddit",  "mention_count": 50,  "positive_score": 0.8, "negative_score": 0.0, "score": 0.8},
            {"platform": "twitter", "mention_count": 120, "positive_score": 0.0, "negative_score": 0.8, "score": -0.8},
        ],
        "aggregate_score": -0.2,
    }
    f = extract_social_features(payload, "AAPL")
    assert f["platform_score_disagreement"] > 0.5

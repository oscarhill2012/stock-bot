"""Tier-1 tests for derive_social_verdict."""
from __future__ import annotations

import pytest  # noqa: F401  (retained for future parameterised expansion)

from agents.analysts.heuristics import SocialHeuristics
from contract.extractors.social import derive_social_verdict


def _h() -> SocialHeuristics:
    """Canonical fixture matching config defaults."""
    return SocialHeuristics(
        score_neutral_band=0.05, score_to_magnitude_scale=2.0,
        high_volume_mentions=200, high_volume_magnitude_boost=0.15,
        confidence_volume_floor=30, platform_disagreement_threshold=0.3,
        confidence_base=0.4, confidence_boost_step=0.2,
        confidence_penalty_step=0.2, magnitude_cap=1.0,
    )


def _features(**overrides) -> dict:
    """Build a minimal valid feature dict, allowing key overrides."""
    base = {
        "mention_count_total": 100.0, "mention_count_reddit": 50.0,
        "mention_count_twitter": 50.0, "aggregate_score": 0.0,
        "score_velocity_24h": 0.0, "platform_score_disagreement": 0.0,
        "is_no_data": 0.0,
    }
    base.update(overrides)
    return base


def test_no_data_path():
    """is_no_data=1.0 returns the no-data verdict shape."""
    v = derive_social_verdict(_features(mention_count_total=0, is_no_data=1.0), _h())
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0
    assert v.is_no_data is True


def test_positive_cluster_is_bullish():
    """Positive aggregate score above neutral band leans bullish."""
    v = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=100), _h())
    assert v.lean == "bullish"
    assert v.magnitude > 0.0


def test_negative_cluster_is_bearish():
    """Negative aggregate score below neutral band leans bearish."""
    v = derive_social_verdict(_features(aggregate_score=-0.4), _h())
    assert v.lean == "bearish"


def test_neutral_band_keeps_lean_neutral():
    """Aggregate score inside the band leans neutral."""
    v = derive_social_verdict(_features(aggregate_score=0.02), _h())
    assert v.lean == "neutral"


def test_high_volume_boosts_magnitude():
    """mention_count > high_volume_mentions adds high_volume_magnitude_boost."""
    low  = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=10), _h())
    high = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=500), _h())
    assert high.magnitude >= low.magnitude


def test_platform_disagreement_penalises_confidence():
    """Reddit/Twitter divergence above threshold drops confidence."""
    agree    = derive_social_verdict(
        _features(aggregate_score=0.4, platform_score_disagreement=0.0, mention_count_total=100),
        _h(),
    )
    disagree = derive_social_verdict(
        _features(aggregate_score=0.4, platform_score_disagreement=0.5, mention_count_total=100),
        _h(),
    )
    assert disagree.confidence < agree.confidence


def test_key_factors_use_closed_vocabulary():
    """All emitted key_factors fall inside the closed vocabulary."""
    v = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=500), _h())
    allowed = {
        "positive", "negative", "mixed", "high_volume", "low_volume",
        "reddit_dominant", "twitter_dominant", "platforms_agree", "platforms_disagree",
    }
    for tag in v.key_factors:
        assert tag in allowed, f"out-of-vocab tag: {tag}"

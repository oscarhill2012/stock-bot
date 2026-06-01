"""Social feature extractor tests — Tier 1, no LLM.

Phase 7 (Task 2.11 / Fix K): extractor now reads typed snapshot list shape
``{"snapshots": [...], "aggregate_score": ...}`` instead of the old
per-platform dict-of-dict.  Signature unchanged.
"""
from __future__ import annotations

import pytest

from contract.extractors.social import extract_social_features
from data.models.sentiment import SocialSentimentSnapshot


def test_social_reads_typed_snapshot_score():
    """Extractor must read the typed-snapshot shape emitted by fetch.py after Fix K."""
    snap = SocialSentimentSnapshot(
        platform="reddit",
        mention_count=100, positive_score=0.65, negative_score=0.15,
        score=0.5,
    )
    raw = {
        "ticker": "AAPL",
        "snapshots": [snap.model_dump()],
        "aggregate_score": 0.5,
    }
    f = extract_social_features(raw, "AAPL")
    assert f["social_aggregate_score"] == pytest.approx(0.5)
    # v1 placeholder — see inline comment in extractor re: Row #13 follow-up.
    assert f["score_velocity_24h"] == pytest.approx(0.0)


def test_social_is_no_data_when_snapshots_empty():
    """Soft-fail branch: dead Social analyst per spec decision 9.3."""
    raw = {"ticker": "AAPL", "snapshots": [], "aggregate_score": None}
    f = extract_social_features(raw, "AAPL")
    assert f["is_no_data"] is True


def test_social_mention_counts_bucketed_by_platform():
    """mention_count_reddit and mention_count_twitter must reflect per-platform snapshot sums."""
    raw = {
        "ticker": "AAPL",
        "snapshots": [
            {"platform": "reddit",  "mention_count": 80, "positive_score": 0.5, "negative_score": 0.1, "score": 0.4},
            {"platform": "twitter", "mention_count": 20, "positive_score": 0.3, "negative_score": 0.2, "score": 0.1},
        ],
        "aggregate_score": 0.3,
    }
    f = extract_social_features(raw, "AAPL")
    assert f["mention_count_reddit"]  == pytest.approx(80.0)
    assert f["mention_count_twitter"] == pytest.approx(20.0)
    assert f["mention_count_total"]   == pytest.approx(100.0)
    assert f["is_no_data"] is False


def test_social_is_no_data_when_raw_empty():
    """Completely empty payload → is_no_data."""
    f = extract_social_features({}, "AAPL")
    assert f["is_no_data"] is True


def test_social_aggregate_score_back_compat_alias():
    """Both ``social_aggregate_score`` and ``aggregate_score`` keys must carry the same value."""
    raw = {
        "ticker": "AAPL",
        "snapshots": [
            {"platform": "reddit", "mention_count": 50, "positive_score": 0.6,
             "negative_score": 0.1, "score": 0.5},
        ],
        "aggregate_score": 0.7,
    }
    f = extract_social_features(raw, "AAPL")
    assert f["social_aggregate_score"] == pytest.approx(f["aggregate_score"])


def test_deterministic_verdict_no_longer_fabricates_report() -> None:
    """A-016 / A-049 regression: social extractor must leave
    report=None and let rationale carry the one-liner.

    Feature keys match the real extractor output — mention_count_total,
    mention_count_reddit, mention_count_twitter (not the 24h/7d aliases
    that appeared in the plan text which referred to a stale shape).
    score=0.4 exceeds score_neutral_band=0.05 so lean is bullish.
    """
    features = {
        "mention_count_total":         50.0,
        "mention_count_reddit":        40.0,
        "mention_count_twitter":       10.0,
        "social_aggregate_score":      0.4,
        "aggregate_score":             0.4,
        "score_velocity_24h":          0.0,
        "platform_score_disagreement": 0.2,
        "is_no_data":                  0.0,
    }

    # Load heuristics from the real config so thresholds are consistent.
    import json
    import pathlib

    from agents.analysts.heuristics import SocialHeuristics
    from contract.extractors.social import derive_social_verdict
    raw_cfg = json.loads(
        (pathlib.Path(__file__).parent.parent.parent.parent.parent
         / "config" / "analyst_heuristics.json").read_text()
    )
    h = SocialHeuristics(**raw_cfg["social"])

    v = derive_social_verdict(features, h)

    assert v.is_no_data is False
    assert v.report is None
    assert v.rationale != ""


def test_no_data_branches_use_canonical_builder() -> None:
    """Both empty-input branches yield the canonical no-data shape."""
    import json
    import pathlib

    from agents.analysts.heuristics import SocialHeuristics
    from contract.extractors.social import derive_social_verdict
    raw_cfg = json.loads(
        (pathlib.Path(__file__).parent.parent.parent.parent.parent
         / "config" / "analyst_heuristics.json").read_text()
    )
    h = SocialHeuristics(**raw_cfg["social"])

    # Branch 1: is_no_data sentinel — partial feature dict is fine; extractor
    # short-circuits before touching the other keys.
    features_sentinel = {"is_no_data": 1.0}
    v1 = derive_social_verdict(features_sentinel, h)
    assert v1.is_no_data is True
    assert v1.report is None
    assert v1.rationale

    # Branch 2: zero total mentions — all keys present, mention_count_total=0
    # trips the second no-data guard.
    features_empty = {
        "mention_count_total":         0.0,
        "mention_count_reddit":        0.0,
        "mention_count_twitter":       0.0,
        "social_aggregate_score":      0.0,
        "aggregate_score":             0.0,
        "score_velocity_24h":          0.0,
        "platform_score_disagreement": 0.0,
        "is_no_data":                  0.0,
    }
    v2 = derive_social_verdict(features_empty, h)
    assert v2.is_no_data is True
    assert v2.report is None
    assert v2.rationale

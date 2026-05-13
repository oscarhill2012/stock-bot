"""Digest defaults tests — Tier 1, no LLM."""
from __future__ import annotations

from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE
from contract.evidence import AnalystName  # noqa: F401  (used in type-checks)


def test_default_weights_cover_expected_analysts():
    # Phase 5 Task 7: 'social' is now the fifth analyst alongside the existing four.
    assert set(DEFAULT_ANALYST_WEIGHTS.keys()) == {
        "technical", "fundamental", "news", "social", "smart_money"
    }


def test_default_weights_are_all_one():
    for w in DEFAULT_ANALYST_WEIGHTS.values():
        assert w == 1.0


def test_dead_zone_is_a_positive_float_under_one():
    assert isinstance(DIRECTION_DEAD_ZONE, float)
    assert 0.0 < DIRECTION_DEAD_ZONE < 1.0

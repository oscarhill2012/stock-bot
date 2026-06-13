"""Digest defaults tests — Tier 1, no LLM."""
from __future__ import annotations

from contract.digest import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE
from contract.evidence import AnalystName  # noqa: F401  (used in type-checks)


def test_default_weights_cover_expected_analysts():
    # Only analysts that are BOTH wired into the pipeline AND consumed by the
    # strategist context shim belong here.  Social and smart_money are shelved
    # — adding phantom entries causes false-positive missing_analyst_slot
    # WARNINGs and deflates aggregate magnitude (~40 % dilution with 5 slots
    # but only 3 contributors).
    assert set(DEFAULT_ANALYST_WEIGHTS.keys()) == {
        "technical", "fundamental", "news",
    }


def test_default_weights_are_all_one():
    for w in DEFAULT_ANALYST_WEIGHTS.values():
        assert w == 1.0


def test_dead_zone_is_a_positive_float_under_one():
    assert isinstance(DIRECTION_DEAD_ZONE, float)
    assert 0.0 < DIRECTION_DEAD_ZONE < 1.0

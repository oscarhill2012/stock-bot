"""Lifecycle derivation tests — Tier 1, no LLM."""
from __future__ import annotations

from agents.strategist.lifecycle import (
    OPEN_EPSILON,
    SIZE_CHANGE_EPSILON,
    derive_lifecycle_action,
)


def test_open_when_current_zero_preferred_above_epsilon():
    assert derive_lifecycle_action(0.0, 0.05) == "open"


def test_close_when_current_above_epsilon_preferred_zero():
    assert derive_lifecycle_action(0.08, 0.0) == "close"


def test_close_when_preferred_below_open_epsilon():
    assert derive_lifecycle_action(0.08, 0.001) == "close"


def test_trim_when_preferred_meaningfully_lower_but_above_zero():
    # current 0.10, preferred 0.05, delta = 0.05 > SIZE_CHANGE_EPSILON
    assert derive_lifecycle_action(0.10, 0.05) == "trim"


def test_add_when_preferred_meaningfully_higher():
    # current 0.05, preferred 0.10, delta = 0.05 > SIZE_CHANGE_EPSILON
    assert derive_lifecycle_action(0.05, 0.10) == "add"


def test_hold_when_change_below_threshold():
    # 0.05 → 0.06, delta 0.01 < SIZE_CHANGE_EPSILON
    assert derive_lifecycle_action(0.05, 0.06) == "hold"


def test_hold_when_both_below_open_epsilon():
    assert derive_lifecycle_action(0.001, 0.002) == "hold"


def test_constants_are_floats():
    assert isinstance(OPEN_EPSILON, float)
    assert isinstance(SIZE_CHANGE_EPSILON, float)
    assert 0.0 < OPEN_EPSILON < SIZE_CHANGE_EPSILON < 1.0


def test_open_at_exact_epsilon_boundary():
    """current = 0, preferred = OPEN_EPSILON exactly → not yet "open" (uses strictly-greater)."""
    assert derive_lifecycle_action(0.0, OPEN_EPSILON) == "hold"


def test_close_at_exact_epsilon_boundary():
    """current = OPEN_EPSILON exactly → not 'close'; strictly-greater means it is treated as flat."""
    assert derive_lifecycle_action(OPEN_EPSILON, 0.0) == "hold"  # neither was meaningfully held
    # …but if current is strictly above OPEN_EPSILON, close fires:
    assert derive_lifecycle_action(OPEN_EPSILON + 0.0001, 0.0) == "close"

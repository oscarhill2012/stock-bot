"""S6 — derive ``decision_tag`` from (prior, new) weight pairs.

``decision_tag`` was a constant ``catalyst_driven_entry`` across all 46
ticks of baseline-2025-09; this makes it useless as a memory key for
Spec B / Spec C.  The fix derives the tag from prior-vs-new weight.
"""
from __future__ import annotations

import pytest

from agents.strategist.derivation import derive_decision_tag


@pytest.mark.parametrize(
    "prior, new, expected",
    [
        (0.0,  0.05, "entry"),
        (0.02, 0.05, "ramp"),
        (0.05, 0.02, "trim"),
        (0.05, 0.0,  "exit"),
        (0.0,  0.0,  "hold_flat"),
        (0.05, 0.05, "hold"),
    ],
)
def test_decision_tag_categories(prior: float, new: float, expected: str) -> None:
    """Each (prior, new) pair maps to the expected enum tag."""
    assert derive_decision_tag(prior=prior, new=new) == expected


def test_decision_tag_uses_epsilon_for_zero_comparison() -> None:
    """Dust-sized residual weights are treated as flat for tagging."""
    assert derive_decision_tag(prior=0.05, new=1e-9) == "exit"
    assert derive_decision_tag(prior=1e-9, new=0.05) == "entry"

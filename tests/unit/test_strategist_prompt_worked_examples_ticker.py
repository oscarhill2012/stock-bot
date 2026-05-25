"""Strategist shape example uses a generic placeholder, not a real ticker.

Previously the example carried ``"AAPL"`` (M5 mild-bias source) and was
then loosened to ``"XYZ"``.  The current convention uses the explicit
placeholder ``<ticker>`` to make the "shape only" contract unambiguous
and remove any residual anchoring effect (see the prompt-audit
discussion that introduced full-ambiguity placeholders for lean,
magnitude, confidence, weight, etc.).  This test pins that invariant.
"""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_shape_example_uses_placeholder_ticker() -> None:
    """The shape example references ``<ticker>`` rather than a real symbol.

    The block is sliced out by its header so the assertion isn't affected
    by stray references elsewhere in the prompt.
    """

    header = "Placeholders\nonly; open + hold shown"
    body   = STRATEGIST_INSTRUCTION.split(header, 1)[1]

    # Generic placeholder present; no real or named-placeholder tickers leak in.
    assert "<ticker>" in body
    assert "AAPL"     not in body
    assert "XYZ"      not in body

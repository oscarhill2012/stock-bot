"""M5 — strategist worked examples use the generic XYZ ticker.

The previous AAPL anchoring was a known mild-bias source where the LLM
latched onto the specific ticker when reasoning about the example shape.
The fix is purely cosmetic — replace AAPL with XYZ.
"""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_worked_examples_use_xyz() -> None:
    """Both worked examples reference XYZ rather than AAPL."""

    # The worked-examples section lives under ``## Two worked examples``.
    # Slice that section out of the full instruction so the assertion
    # is unaffected by stray AAPL/XYZ references elsewhere.
    header   = "## Two worked examples"
    body     = STRATEGIST_INSTRUCTION.split(header, 1)[1]
    examples = body.split("\n\n", 4)[:3]
    examples_text = "\n\n".join(examples)

    assert "XYZ" in examples_text
    assert "AAPL" not in examples_text

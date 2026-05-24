"""M5 — strategist worked examples use the generic XYZ ticker.

The previous AAPL anchoring was a known mild-bias source where the LLM
latched onto the specific ticker when reasoning about the example shape.
The fix is purely cosmetic — replace AAPL with XYZ.
"""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_worked_examples_use_xyz() -> None:
    """The worked example references XYZ rather than AAPL.

    Post-dedupe (commit 9bd16e4) the prompt carries a single worked
    example introduced by ``Worked example —``; the prior two-example
    block was collapsed.  The M5 invariant — generic placeholder ticker
    instead of AAPL — still holds and is what this test pins.
    """

    # Slice the worked-example block out of the full instruction so the
    # assertion is unaffected by stray AAPL/XYZ references elsewhere.
    header        = "Worked example"
    body          = STRATEGIST_INSTRUCTION.split(header, 1)[1]
    examples_text = body  # one example, runs to end of template

    assert "XYZ"  in examples_text
    assert "AAPL" not in examples_text

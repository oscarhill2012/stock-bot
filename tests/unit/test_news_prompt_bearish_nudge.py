"""M4 — news prompt treats surprise direction symmetrically (no bullish skew).

News verdict stance distribution was 467 bullish vs 25 bearish across
baseline-2025-09.  The original corrective anchor was a short list of common
bearish triggers the model should not round up to neutral.

Phase 14 replaced the old positive/negative reactive decision rule with a
direction-agnostic STEP 2 ("DIRECTION"): the model judges the SURPRISE
direction versus expectations — explicitly NOT the headline's emotional
tone — for whichever genuine surprise STEP 1 found, with no special-casing
that favours bullish over bearish. The bullish-skew regression this test
guards against is now addressed structurally (the decision procedure has no
asymmetric "bullish needs X, bearish needs Y" branching) rather than via an
explicit bearish-trigger list, so the assertions are updated to pin the new
symmetric-direction wording while keeping the original intent: the prompt
must not steer the model toward defaulting bearish news to neutral.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_news_bearish_triggers_present() -> None:
    """The rendered news prompt judges surprise direction symmetrically —
    positive or negative, on the SURPRISE itself rather than the headline's
    tone — with no bullish-favouring special-casing in STEP 2 or STEP 3."""

    rendered = build_news_instruction(_vocab())
    for fragment in (
        "positive or negative",
        "Judge the SURPRISE direction",
        "not the headline's emotional tone",
        "Lean WITH the surprise direction",
    ):
        assert fragment in rendered, f"missing direction-symmetry fragment: {fragment}"

"""M4 — news prompt contains explicit bearish-trigger guidance.

News verdict stance distribution was 467 bullish vs 25 bearish across
baseline-2025-09.  The corrective anchor is a short list of common
bearish triggers the model should not round up to neutral.
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
    """The rendered news prompt cites the canonical bearish anchors."""

    rendered = build_news_instruction(_vocab())
    for fragment in (
        "missed guidance",
        "downgrade",
        "supplier loss",
        "executive departure",
        "regulatory action",
        "do NOT default to neutral",
    ):
        assert fragment in rendered, f"missing bearish-anchor fragment: {fragment}"

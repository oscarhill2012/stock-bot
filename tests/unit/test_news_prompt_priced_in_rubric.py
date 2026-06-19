"""News prompt — novelty / already-priced-in rubric regression guards.

Iter-5 backtest showed the news analyst leaning bullish on every ticker at a
tick: the old decision rule mapped any positive coverage straight to bullish
("positive -> bullish") and scaled confidence with raw article COUNT, so
quiet large-caps with lots of generic positive filler scored confident
bullish.  The strategist, seeing news bullish everywhere, effectively ignored
the channel.

The corrective rubric makes the lean depend on genuinely-NEW,
company-specific, not-yet-priced-in information; decouples confidence from
article count; adds a source-credibility filter (pundit/price commentary is
noise); and forbids repeated key_factors tags.

These tests pin the load-bearing semantic markers.  Written against ideas,
not exact wording.
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


def test_lean_requires_new_incremental_information() -> None:
    """Positive coverage of a stable name is the default state, not a bullish
    signal; bullish requires a genuinely-new company-specific catalyst."""

    rendered = build_news_instruction(_vocab())

    assert "is NOT a reason to be bullish" in rendered
    assert "genuinely-new positive company-specific catalyst" in rendered
    assert "Do NOT default to bullish." in rendered


def test_confidence_not_driven_by_article_count() -> None:
    """The old 'confidence scales with headline count' anchor is replaced —
    confidence tracks materiality and novelty, not volume of filler."""

    rendered = build_news_instruction(_vocab())

    assert "NOT driven by article COUNT" in rendered
    # The discredited count-driven anchor must be gone.
    assert "Confidence scales with headline count" not in rendered


def test_source_quality_filter_present() -> None:
    """Pundit opinion and price commentary must be treated as noise, not as
    drivers; factual disclosures and consensus-moving notes carry weight."""

    rendered = build_news_instruction(_vocab())

    assert "Source and signal quality" in rendered
    assert "Cramer says" in rendered
    assert "relative-strength" in rendered
    assert "merely restate consensus" in rendered


def test_key_factors_must_be_distinct() -> None:
    """key_factors degenerated into the same tag repeated 3-5 times; the
    prompt must demand distinct tags."""

    rendered = build_news_instruction(_vocab())

    assert "DISTINCT tags only" in rendered

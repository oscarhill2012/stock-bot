"""News prompt — novelty / already-priced-in rubric regression guards.

Iter-5 backtest showed the news analyst leaning bullish on every ticker at a
tick: the old decision rule mapped any positive coverage straight to bullish
("positive -> bullish") and scaled confidence with raw article COUNT, so
quiet large-caps with lots of generic positive filler scored confident
bullish.  The strategist, seeing news bullish everywhere, effectively ignored
the channel.

The original corrective rubric made the lean depend on genuinely-NEW,
company-specific, not-yet-priced-in information; decoupled confidence from
article count; added a source-credibility filter (pundit/price commentary is
noise); and forbade repeated key_factors tags.

Phase 14 replaced that reactive "sentiment -> lean" rubric outright with a
surprise-classification + post-news-drift decision procedure (see
``src/agents/analysts/news/prompts.py``): STEP 1 classifies each fresh
article as a GENUINE SURPRISE or noise, and a noise-only tick is a "perfectly
good outcome" rather than something the model must be talked out of
defaulting to bullish. The same underlying regression (bullish-by-default on
generic positive coverage, confidence inflated by article volume, pundit
noise treated as signal) is now guarded against by the new framing, so these
tests are updated to pin the NEW load-bearing markers while keeping the
original intent: don't manufacture a lean from noise, don't let confidence
be volume-driven, and keep key_factors to the closed vocabulary only.

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
    """A lean must come from a GENUINE SURPRISE, not generic positive
    coverage; classifying everything as noise (i.e. no lean) is explicitly
    endorsed as the common, correct outcome rather than something to talk
    the model out of."""

    rendered = build_news_instruction(_vocab())

    assert "GENUINE SURPRISE" in rendered
    assert "Classifying everything as noise is a" in rendered
    assert "Do NOT manufacture a lean" in rendered


def test_confidence_not_driven_by_article_count() -> None:
    """Confidence tracks whether a genuine surprise (or live drift) exists,
    not the volume of coverage — noise-only ticks must be LOW confidence
    neutrals rather than scaling up with article count."""

    rendered = build_news_instruction(_vocab())

    assert "Noise-only ticks are LOW confidence neutrals" in rendered
    # The discredited count-driven anchor must be gone.
    assert "Confidence scales with headline count" not in rendered
    assert "NOT driven by article COUNT" not in rendered


def test_source_quality_filter_present() -> None:
    """Rehashed / commentary-only / vague coverage must be classified as
    noise rather than a driver; only specific, material information (numbers
    vs expectations, a decision, a contract, a regulatory outcome, a
    guidance change) counts as a genuine surprise."""

    rendered = build_news_instruction(_vocab())

    assert "or is it noise" in rendered
    assert "rehash phrased past the filter, commentary" in rendered
    assert "too vague to shift expectations" in rendered


def test_key_factors_must_be_distinct() -> None:
    """key_factors must be drawn EXCLUSIVELY from the closed vocabulary —
    the Phase 14 rewrite drops the standalone "distinct tags" mandate in
    favour of anchoring key_factors entirely to the closed-vocab catalyst /
    novelty / direction / material tags, which structurally rules out
    inventing repeated filler tags outside that list."""

    rendered = build_news_instruction(_vocab())

    assert "EXCLUSIVELY from this closed vocabulary" in rendered
    assert "Never invent tags outside this list" in rendered

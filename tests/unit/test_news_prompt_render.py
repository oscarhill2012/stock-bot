"""Tier-1 tests for the News LLM prompt template.

These tests validate that ``build_news_instruction`` correctly substitutes
closed-vocabulary tokens and produces a prompt containing:

- No residual vocab-slot ``{..}`` tokens (all three vocab groups resolved).
- All closed-vocab terms from the test vocabulary.
- The two expected ADK runtime placeholders ``{news_context}`` and
  ``{tickers}`` that remain for ADK's ``inject_session_state`` to fill.
- No polarity-numeric phrasing (positive_score, negative_score, mention_count)
  that was removed in the Phase 5 narrowing.

Note on runtime placeholders
-----------------------------
``{news_context}`` and ``{tickers}`` are intentionally preserved in the rendered
string so ADK's ``inject_session_state`` fills them with live data each tick.
All other ``{..}`` tokens must be resolved at construction time.
"""
from __future__ import annotations

import re

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    """Return a representative test vocabulary instance."""
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_placeholders_resolve() -> None:
    """All vocab {placeholder} tokens are substituted by build_news_instruction."""
    rendered = build_news_instruction(_vocab())

    # Vocab slot tokens must be gone after construction-time substitution.
    for tok in ("{catalyst_options}", "{novelty_options}", "{direction_options}"):
        assert tok not in rendered, f"Vocab token '{tok}' should be resolved but is still present"

    # Strip the two known ADK runtime keys and confirm nothing else remains.
    stripped = (
        rendered
        .replace("{news_context}", "")
        .replace("{tickers}", "")
    )
    assert not re.search(r"\{[a-z_]+\}", stripped), (
        "Unexpected single-brace token found in rendered prompt "
        "(only {news_context} and {tickers} are allowed)"
    )


def test_vocab_terms_present() -> None:
    """Each closed-vocab term appears in the rendered prompt."""
    rendered = build_news_instruction(_vocab())
    for term in ("earnings", "guidance", "m_and_a", "high", "medium", "positive", "negative"):
        assert term in rendered, f"Expected vocab term '{term}' not found in prompt"


def test_no_polarity_numerics_in_prompt() -> None:
    """The news LLM no longer sees polarity statistics — pulled from the prompt."""
    rendered = build_news_instruction(_vocab())
    # Spot-check that historical numeric-block phrasing is absent.
    for forbidden in ("positive_score", "negative_score", "mention_count"):
        assert forbidden not in rendered, (
            f"Forbidden numeric field '{forbidden}' found in prompt — should be removed"
        )


def test_runtime_placeholders_present() -> None:
    """The ADK state placeholders survive vocab substitution intact."""
    rendered = build_news_instruction(_vocab())

    # ADK fills these from session state each tick.
    assert "{news_context}" in rendered, (
        "ADK runtime placeholder {news_context} is missing from rendered prompt"
    )
    assert "{tickers}" in rendered, (
        "ADK runtime placeholder {tickers} is missing from rendered prompt"
    )


def test_lean_options_in_prompt() -> None:
    """The prompt lists the three valid lean values."""
    rendered = build_news_instruction(_vocab())
    for lean in ("bullish", "bearish", "neutral"):
        assert lean in rendered, f"Lean option '{lean}' not found in rendered prompt"


def test_decision_rule_present() -> None:
    """The decision-rule block (direction → lean, novelty → magnitude) is present."""
    rendered = build_news_instruction(_vocab())
    # Expect mention of direction driving lean.
    assert "direction" in rendered.lower(), "Expected direction decision rule not found"
    # Expect mention of novelty driving magnitude.
    assert "novelty" in rendered.lower(), "Expected novelty decision rule not found"

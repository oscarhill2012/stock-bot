"""Tier-1 tests for the News LLM prompt template.

These tests validate that ``build_news_instruction`` correctly substitutes
closed-vocabulary tokens and produces a prompt containing:

- No residual vocab-slot ``{..}`` tokens (all three vocab groups resolved).
- All closed-vocab terms from the test vocabulary.
- The two expected runtime placeholders ``{news_context}`` and ``{ticker}``
  that survive vocab substitution intact (the per-ticker branch factory
  substitutes both of these at branch-construction time — ``{ticker}``
  becomes the literal ticker symbol and ``{news_context}`` becomes the
  ADK state key ``{temp:news_context_<TICKER>}``).
- No polarity-numeric phrasing (positive_score, negative_score, mention_count)
  that was removed in the Phase 5 narrowing.

Note on runtime placeholders
-----------------------------
Phase 9 changed the delivery mechanism: the base template preserves
``{news_context}`` and ``{ticker}``.  The per-ticker factory
(``build_news_branch_for_ticker``) then substitutes both at build time
so each branch's instruction is already specialised.  ``{tickers}`` (the
old multi-ticker watchlist key) is gone from the Phase 9 template.
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

    # Strip the two known per-ticker-factory-substituted placeholders and
    # confirm nothing else remains.  Phase 9 uses {news_context} and {ticker}
    # (the per-ticker factory then replaces both at branch-construction time).
    # {tickers} (the old multi-ticker watchlist key) is gone from the template.
    stripped = (
        rendered
        .replace("{news_context}", "")
        .replace("{ticker}", "")
    )
    assert not re.search(r"\{[a-z_]+\}", stripped), (
        "Unexpected single-brace token found in rendered prompt "
        "(only {news_context} and {ticker} are allowed to survive vocab substitution)"
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
    """The per-ticker-factory placeholders survive vocab substitution intact.

    Phase 9: the base template preserves ``{news_context}`` and ``{ticker}``
    after vocab substitution.  The per-ticker branch factory
    (``build_news_branch_for_ticker``) then substitutes both at build time —
    ``{ticker}`` becomes the literal symbol and ``{news_context}`` becomes
    ``{temp:news_context_<TICKER>}`` (the ADK state key written by
    ``NewsFetchAgent``).

    Note: ``{tickers}`` (the old multi-ticker watchlist key) is absent from
    the Phase 9 per-ticker template.
    """
    rendered = build_news_instruction(_vocab())

    # Both placeholders must survive vocab substitution intact.
    assert "{news_context}" in rendered, (
        "Placeholder {news_context} is missing from rendered prompt"
    )
    assert "{ticker}" in rendered, (
        "Placeholder {ticker} is missing from rendered prompt"
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


def test_report_schema_instructions_present() -> None:
    """The report schema block is present in the rendered prompt.

    Asserts both the section heading and the driver count constraint so
    the LLM receives the full shape specification (summary + 2-4 drivers).
    The 2026-05-25 prompt rewrite reworded the driver-count phrasing as
    ``list of 2-4 entries`` (lifted into a single sentence rather than a
    table column); the constraint itself is unchanged.
    """
    rendered = build_news_instruction(_vocab())

    assert "Report schema:" in rendered, (
        "'Report schema:' heading not found — LLM will not know to emit a report"
    )
    assert "2-4 entries" in rendered, (
        "'2-4 entries' constraint not found — driver count mandate missing"
    )

"""M1 — anti-truncation guard present in news + fundamental prompts.

Five of 28 LLM retries in baseline-2025-09 were JSON-truncation EOFs
where the model ran into ``max_output_tokens`` while repeating a token.
A one-line prompt guard nudges the model away from the
``AMZN_AMZN_AMZN_…`` / ``\\n\\n\\n…`` / ``0000000000…`` failure mode.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts        import build_news_instruction


_GUARD_FRAGMENT = "repeat a token or symbol three or more times in a row"


def _news_vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_news_prompt_has_anti_truncation_guard() -> None:
    rendered = build_news_instruction(_news_vocab())
    assert _GUARD_FRAGMENT in rendered


# Fundamental case added in Task 11 — placeholder kept so the two halves
# of M1 are visible in one file.

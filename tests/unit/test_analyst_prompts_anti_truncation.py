"""M1 — anti-truncation guard present in news + fundamental prompts.

Five of 28 LLM retries in baseline-2025-09 were JSON-truncation EOFs
where the model ran into ``max_output_tokens`` while repeating a token.
A one-line prompt guard nudges the model away from the
``AMZN_AMZN_AMZN_…`` / ``\\n\\n\\n…`` / ``0000000000…`` failure mode.
"""
from __future__ import annotations

from agents.analysts.heuristics import FundamentalVocabulary, NewsVocabulary
from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.news.prompts        import build_news_instruction


_GUARD_FRAGMENT = "repeat a token or symbol three or more times in a row"


def _news_vocab() -> NewsVocabulary:
    """Return a representative NewsVocabulary for anti-truncation guard tests."""
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def _fundamental_vocab() -> FundamentalVocabulary:
    """Return a representative FundamentalVocabulary — mirrors test_fundamental_prompt_render.py."""
    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=["regulatory", "litigation", "cybersecurity", "going_concern"],
        insider_signals=["cluster_buying", "cluster_selling", "planned_sale_dominant", "mixed"],
    )


def test_news_prompt_has_anti_truncation_guard() -> None:
    """The news prompt must contain the M1 anti-truncation guard fragment."""
    rendered = build_news_instruction(_news_vocab())
    assert _GUARD_FRAGMENT in rendered


def test_fundamental_prompt_has_anti_truncation_guard() -> None:
    """The fundamental prompt must contain the M1 anti-truncation guard fragment."""
    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert _GUARD_FRAGMENT in rendered

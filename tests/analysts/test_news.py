"""News analyst agent structural tests — A2.5: factory now returns a YieldingAnalystWrapper.

The module-level singleton ``news_analyst`` is a ``YieldingAnalystWrapper``
named ``"NewsAnalystBranch"``.  The inner ``LlmAgent`` is accessible via
``.inner`` — tests that need to inspect LlmAgent attributes do so through
that attribute.

Renamed from test_sentiment.py in Task 6.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._base_yield import YieldingAnalystWrapper
from agents.analysts.news.agent import news_analyst


def test_news_analyst_is_yielding_wrapper() -> None:
    """The module singleton must now be a YieldingAnalystWrapper (A2.5)."""
    assert isinstance(news_analyst, YieldingAnalystWrapper)


def test_news_analyst_branch_name() -> None:
    """Outer wrapper name is 'NewsAnalystBranch'."""
    assert news_analyst.name == "NewsAnalystBranch"


def test_news_analyst_inner_is_llm_agent() -> None:
    """Inner agent must still be the LlmAgent with correct output_key."""
    assert isinstance(news_analyst.inner, LlmAgent)
    assert news_analyst.inner.output_key == "news_verdicts"

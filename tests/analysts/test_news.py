"""News analyst agent structural tests — A2.5: factory now returns a YieldingAnalystWrapper.

The :func:`build_news_analyst` factory returns a ``YieldingAnalystWrapper``
named ``"NewsAnalystBranch"``.  The inner ``LlmAgent`` is accessible via
``.inner`` — tests that need to inspect LlmAgent attributes do so through
that attribute.

Pre-2026-05-21 this file imported a module-level ``news_analyst`` singleton
built at import time.  Both that singleton and the hardcoded model literal
have been deleted (the model ID now lives in
``config/models.json::news_analyst`` via
``src.config.models.get_models_config``); these tests now construct a fresh
analyst via :func:`build_news_analyst` for each test.

Renamed from test_sentiment.py in Task 6.
"""
from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent

from agents.analysts._base_yield import YieldingAnalystWrapper
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.agent import build_news_analyst


@pytest.fixture(scope="module")
def news_analyst() -> YieldingAnalystWrapper:
    """Build a fresh ``NewsAnalystBranch`` once per test module.

    Loads the closed-vocab heuristics from disk (same call the production
    pipeline makes) and hands the ``news_vocabulary`` to the factory.  The
    resulting wrapper is shared across this module's structural tests —
    these tests only inspect identity / type / attributes, so module scope
    is safe.
    """
    h = load_heuristics()
    return build_news_analyst(h.news_vocabulary)


def test_news_analyst_is_yielding_wrapper(news_analyst: YieldingAnalystWrapper) -> None:
    """The factory output must be a YieldingAnalystWrapper (A2.5)."""
    assert isinstance(news_analyst, YieldingAnalystWrapper)


def test_news_analyst_branch_name(news_analyst: YieldingAnalystWrapper) -> None:
    """Outer wrapper name is 'NewsAnalystBranch'."""
    assert news_analyst.name == "NewsAnalystBranch"


def test_news_analyst_inner_is_llm_agent(news_analyst: YieldingAnalystWrapper) -> None:
    """Inner agent must still be the LlmAgent with correct output_key."""
    assert isinstance(news_analyst.inner, LlmAgent)
    assert news_analyst.inner.output_key == "news_verdicts"

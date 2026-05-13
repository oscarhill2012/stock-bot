"""News analyst agent structural tests — Tier 1, no LLM.

Renamed from test_sentiment.py in Task 6.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts.news.agent import news_analyst


def test_news_analyst_is_llm_agent():
    assert isinstance(news_analyst, LlmAgent)


def test_news_analyst_output_key():
    assert news_analyst.output_key == "news_verdicts"

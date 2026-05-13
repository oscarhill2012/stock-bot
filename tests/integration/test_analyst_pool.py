"""Tier 1 structural test — no LLM calls."""
from __future__ import annotations

from google.adk.agents import ParallelAgent

from agents.analysts import analyst_pool


def test_analyst_pool_is_parallel_agent():
    assert isinstance(analyst_pool, ParallelAgent)


def test_analyst_pool_has_four_agents():
    assert len(analyst_pool.sub_agents) == 4


def test_analyst_pool_agent_names():
    names = {a.name for a in analyst_pool.sub_agents}
    assert names == {"TechnicalAnalyst", "FundamentalAnalyst", "NewsAnalyst", "SmartMoneyAnalyst"}

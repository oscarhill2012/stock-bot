"""Tier 1 structural test — no LLM calls."""
from __future__ import annotations

from google.adk.agents import ParallelAgent

from agents.analysts import analyst_pool


def test_analyst_pool_is_parallel_agent():
    assert isinstance(analyst_pool, ParallelAgent)


def test_analyst_pool_has_five_agents():
    """Task 7 adds SocialAnalyst as the fifth child of the pool."""
    assert len(analyst_pool.sub_agents) == 5


def test_analyst_pool_agent_names():
    """A2.5: Fund and News slots are now YieldingAnalystWrapper branches.

    Their names changed from the inner LlmAgent names ("FundamentalAnalyst",
    "NewsAnalyst") to the wrapper names ("FundamentalAnalystBranch",
    "NewsAnalystBranch").  TechnicalAnalyst, SocialAnalyst, and
    SmartMoneyAnalyst are unchanged.
    """
    names = {a.name for a in analyst_pool.sub_agents}
    assert names == {
        "TechnicalAnalyst", "FundamentalAnalystBranch", "NewsAnalystBranch",
        "SocialAnalyst", "SmartMoneyAnalyst",
    }

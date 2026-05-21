"""Structural tests for the AnalystPool — no LLM calls.

A2.7 restructured the pool from a single 4-wide ParallelAgent into a
SequentialAgent so that Fundamental and News each own the state_delta rail
unambiguously.  Technical and Social remain parallel (distinct output keys,
Rule 4 satisfied).
"""
from __future__ import annotations

from google.adk.agents import ParallelAgent, SequentialAgent

from orchestrator.pipeline import _build_analyst_pool


def test_analyst_pool_is_sequential_agent():
    """Root of the pool must be a SequentialAgent after A2.7."""
    pool = _build_analyst_pool()
    assert isinstance(pool, SequentialAgent)


def test_analyst_pool_has_three_children():
    """Pool has three children: Parallel[Tech,Social], Fund, News."""
    pool = _build_analyst_pool()
    assert len(pool.sub_agents) == 3


def test_analyst_pool_first_child_is_parallel():
    """First child wraps the two deterministic BaseAgent analysts."""
    pool = _build_analyst_pool()
    first = pool.sub_agents[0]
    assert isinstance(first, ParallelAgent)
    assert len(first.sub_agents) == 2


def test_analyst_pool_agent_names():
    """A2.7 topology + retry wrap: Parallel[Tech,Social] + Retrying Fund + News.

    Fund and News are wrapped in ``RetryingAgentWrapper`` at the pipeline level
    so a Vertex 429 on the underlying LLM is retried with exponential backoff.
    The retry wrapper's name ends in "Retrying" (e.g. "FundamentalAnalystRetrying")
    — its inner ``YieldingAnalystWrapper`` still uses the "*Branch" name, but
    that's not what the pool's ``sub_agents`` exposes.

    Technical and Social are deterministic BaseAgent analysts in the parallel
    tier — no LLM, so no retry wrapper.
    """
    pool = _build_analyst_pool()

    # Parallel tier — deterministic analysts.
    parallel_names = {a.name for a in pool.sub_agents[0].sub_agents}
    assert parallel_names == {"TechnicalAnalyst", "SocialAnalyst"}

    # Sequential tier — LLM-backed analysts, each wrapped in RetryingAgentWrapper.
    sequential_names = {a.name for a in pool.sub_agents[1:]}
    assert sequential_names == {"FundamentalAnalystRetrying", "NewsAnalystRetrying"}

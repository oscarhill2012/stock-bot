"""Structural tests for the AnalystPool — no LLM calls.

Phase 9 post-parallelism: the AnalystPool is a ``ParallelAgent`` so the
deterministic block, Fundamental, and News all run concurrently.  The
A2.7 sequential-rail guard is retired because per-ticker fan-out writes
only to disjoint durable keys (``news_verdicts``/``news_evidence`` vs
``fundamental_verdicts``/``fundamental_evidence``).

Fundamental and News are per-ticker fan-out branches
(``SequentialAgent[FetchAgent, ParallelAgent[per-ticker branches], JoinerAgent]``).
Branch names are ``"FundamentalAnalystBranch"`` and ``"NewsAnalystBranch"``.
"""
from __future__ import annotations

from google.adk.agents import ParallelAgent

from orchestrator.pipeline import _build_analyst_pool


def test_analyst_pool_is_sequential_agent():
    """Root of the pool must be a ParallelAgent post-Phase-9 parallelism.

    Name kept for backwards compatibility with the test discovery surface,
    but the assertion now requires ``ParallelAgent`` — the across-analyst
    sequential chain was retired once per-ticker fan-out made the durable
    state keys disjoint.
    """
    pool = _build_analyst_pool(tickers=["AAPL"])
    assert isinstance(pool, ParallelAgent)


def test_analyst_pool_has_three_children():
    """Pool has three children: Parallel[Tech,Social], Fund, News."""
    pool = _build_analyst_pool(tickers=["AAPL"])
    assert len(pool.sub_agents) == 3


def test_analyst_pool_first_child_is_parallel():
    """First child wraps the two deterministic BaseAgent analysts."""
    pool = _build_analyst_pool(tickers=["AAPL"])
    first = pool.sub_agents[0]
    assert isinstance(first, ParallelAgent)
    assert len(first.sub_agents) == 2


def test_analyst_pool_agent_names():
    """A2.7 topology + Phase-9 fan-out: Parallel[Tech,Social] + Fund branch + News branch.

    Phase 9 replaces the pre-Phase-9 ``RetryingAgentWrapper`` wrappers
    (``"FundamentalAnalystRetrying"``, ``"NewsAnalystRetrying"``) with
    ``SequentialAgent`` fan-out branches named ``"FundamentalAnalystBranch"``
    and ``"NewsAnalystBranch"``.  Retries now live inside each per-ticker
    child at the ``IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))``
    layer.

    Technical and Social are deterministic BaseAgent analysts in the parallel
    tier — no LLM, so no retry wrapper.
    """
    pool = _build_analyst_pool(tickers=["AAPL"])

    # Parallel tier — deterministic analysts.
    parallel_names = {a.name for a in pool.sub_agents[0].sub_agents}
    assert parallel_names == {"TechnicalAnalyst", "SocialAnalyst"}

    # Sequential tier — Phase-9 fan-out branches (not RetryingAgentWrapper wrappers).
    sequential_names = {a.name for a in pool.sub_agents[1:]}
    assert sequential_names == {"FundamentalAnalystBranch", "NewsAnalystBranch"}

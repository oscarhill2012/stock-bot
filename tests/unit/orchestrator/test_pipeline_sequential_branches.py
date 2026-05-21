"""Topology guard — AnalystPool must be SequentialAgent[Parallel[Tech,Social], Fund, News].

A2.7 changes the analyst pool from a single 4-wide ParallelAgent into a
sequential chain so Fundamental and News each own the state_delta rail
unambiguously.  Technical and Social remain parallel (no shared writes
after A1's BaseAgent state_delta conversion).
"""
from __future__ import annotations


def test_analyst_pool_topology() -> None:
    """Pool is SequentialAgent whose first child is a 2-wide ParallelAgent."""
    from google.adk.agents import ParallelAgent, SequentialAgent

    from orchestrator.pipeline import _build_analyst_pool

    pool = _build_analyst_pool()

    assert isinstance(pool, SequentialAgent), (
        f"AnalystPool root must be SequentialAgent, got {type(pool).__name__}"
    )
    assert len(pool.sub_agents) == 3, (
        f"AnalystPool must have three children "
        f"(Parallel[Tech,Social], Fund, News); got {len(pool.sub_agents)}"
    )

    first = pool.sub_agents[0]
    assert isinstance(first, ParallelAgent), (
        f"First child must be a ParallelAgent (Technical + Social); "
        f"got {type(first).__name__}"
    )
    assert len(first.sub_agents) == 2, (
        f"Parallel branch must have two children (Tech + Social); "
        f"got {len(first.sub_agents)}"
    )

    # Names check — order matters for trace readability.
    assert {a.name for a in first.sub_agents} == {
        "TechnicalAnalyst", "SocialAnalyst",
    }

    # Second and third children are the Fund + News branches.  Each is now
    # wrapped in a ``RetryingAgentWrapper`` at the pipeline-composition layer
    # so a Vertex 429 on the inner LlmAgent triggers exponential backoff;
    # those wrappers' names end in "Retrying".  The retry wrapper's
    # ``.inner`` still points at the original ``YieldingAnalystWrapper``
    # whose name ends in "Branch", but that's an implementation detail
    # below the pipeline level.
    branch_names = {a.name for a in pool.sub_agents[1:]}
    assert branch_names == {"FundamentalAnalystRetrying", "NewsAnalystRetrying"}, (
        f"Sequential branches must be Fund + News retry wrappers; got {branch_names}"
    )

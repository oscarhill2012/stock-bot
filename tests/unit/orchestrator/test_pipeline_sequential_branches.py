"""Topology guard — AnalystPool must be ParallelAgent[Parallel[Tech,Social], Fund, News].

A2.7 originally chained Fundamental and News sequentially so each owned
the ``state_delta`` rail unambiguously.  Phase 9 retires that guard:
per-ticker fan-out writes only to disjoint durable keys
(``news_verdicts``/``news_evidence`` vs ``fundamental_verdicts``/
``fundamental_evidence``), and ``IsolatedFailureWrapper`` prevents sibling
cancellation inside ADK's ``asyncio.TaskGroup``, so the two LLM branches
can safely run concurrently with each other and with the deterministic
Parallel block.

Fundamental and News are per-ticker fan-out branches
(``SequentialAgent[FetchAgent, ParallelAgent[per-ticker branches], JoinerAgent]``).
Branch names are ``"FundamentalAnalystBranch"`` / ``"NewsAnalystBranch"``.
"""
from __future__ import annotations


def test_analyst_pool_topology() -> None:
    """Pool is ParallelAgent whose first child is a 2-wide ParallelAgent.

    Phase 9 post-parallelism: ``_build_analyst_pool`` returns a
    ``ParallelAgent`` so the deterministic block, Fundamental, and News all
    run concurrently.  Branches are named ``FundamentalAnalystBranch`` /
    ``NewsAnalystBranch`` (SequentialAgent fan-outs).
    """
    from google.adk.agents import ParallelAgent

    from orchestrator.pipeline import _build_analyst_pool

    # tickers= is required; a single-ticker list is sufficient for
    # topology assertions that do not inspect per-ticker fan-out count.
    pool = _build_analyst_pool(tickers=["AAPL"])

    assert isinstance(pool, ParallelAgent), (
        f"AnalystPool root must be ParallelAgent, got {type(pool).__name__}"
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

    # Phase 9: Fund + News are now SequentialAgent fan-out branches named
    # ``FundamentalAnalystBranch`` and ``NewsAnalystBranch``.  The pre-Phase-9
    # ``RetryingAgentWrapper`` wrappers (``FundamentalAnalystRetrying``,
    # ``NewsAnalystRetrying``) are gone — retries live inside each per-ticker
    # child at the ``IsolatedFailureWrapper(Retrying(LlmAgent))`` layer.
    branch_names = {a.name for a in pool.sub_agents[1:]}
    assert branch_names == {"FundamentalAnalystBranch", "NewsAnalystBranch"}, (
        f"Sequential branches must be Fund + News fan-out branches; got {branch_names}"
    )

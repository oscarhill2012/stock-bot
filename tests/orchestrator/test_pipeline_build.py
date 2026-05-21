"""build_pipeline accepts the current watchlist explicitly (Phase 9)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orchestrator.pipeline import build_pipeline


def test_build_pipeline_requires_tickers_kwarg():
    """tickers is a required keyword argument.

    Calling ``build_pipeline`` without ``tickers`` must raise ``TypeError``
    so callers that forget to pass the watchlist fail loudly at startup
    rather than silently producing a pipeline with no per-ticker branches.
    """

    broker = MagicMock()

    with pytest.raises(TypeError):
        build_pipeline(broker, db_session=None)  # missing tickers


def test_build_pipeline_threads_tickers_into_analyst_pool():
    """The composed pipeline contains a NewsAnalystBranch sized to the watchlist.

    Drills into the agent tree produced by ``build_pipeline`` and asserts:
    - The first sub-agent of the top-level ``HourlyTick`` is the
      ``AnalystPool`` (now a ``ParallelAgent`` post-Phase-9 parallelism).
    - Within it, the ``NewsAnalystBranch`` sequential agent exists.
    - The branch is ``[FetchAgent, ParallelAgent[per-ticker × N], JoinerAgent]``
      (3 sub-agents at the outer level, with ``N`` per-ticker children inside
      the fan-out).
    - Each per-ticker child is bound to one of the supplied ticker symbols
      (via its ``.ticker`` attribute on ``IsolatedFailureWrapper``).
    """

    from agents.analysts.news.agent import build_news_branch  # noqa: F401 — imported for context
    from agents.isolated_failure import IsolatedFailureWrapper  # noqa: F401

    broker  = MagicMock()
    tickers = ["AAPL", "MSFT", "GOOG"]

    pipeline = build_pipeline(broker, db_session=None, tickers=tickers)

    # Drill into the AnalystPool to find the News branch.
    analyst_pool = pipeline.sub_agents[0]
    news_branch  = next(
        sa for sa in analyst_pool.sub_agents if sa.name == "NewsAnalystBranch"
    )

    # Fetch + ParallelAgent fan-out + Joiner = 3 outer sub-agents
    assert len(news_branch.sub_agents) == 3

    fanout = news_branch.sub_agents[1]
    per_ticker = fanout.sub_agents
    assert len(per_ticker) == len(tickers)
    assert {p.ticker for p in per_ticker} == set(tickers)

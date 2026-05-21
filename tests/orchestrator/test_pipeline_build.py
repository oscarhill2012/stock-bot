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

    Drills into the SequentialAgent tree produced by ``build_pipeline`` and
    asserts that:
    - The first sub-agent of the top-level ``HourlyTick`` is the
      ``AnalystPool`` sequential branch.
    - Within it, the ``NewsAnalystBranch`` sequential agent exists.
    - The branch contains exactly ``len(tickers) + 2`` sub-agents
      (FetchAgent + one per-ticker branch + JoinerAgent).
    - Each per-ticker sub-agent is bound to one of the supplied ticker
      symbols (via its ``.ticker`` attribute on ``IsolatedFailureWrapper``).
    """

    from agents.analysts.news.agent import build_news_branch  # noqa: F401 — imported for context
    from agents.isolated_failure import IsolatedFailureWrapper  # noqa: F401

    broker  = MagicMock()
    tickers = ["AAPL", "MSFT", "GOOG"]

    pipeline = build_pipeline(broker, db_session=None, tickers=tickers)

    # Drill into the SequentialAgent tree to find the News branch.
    analyst_pool = pipeline.sub_agents[0]
    news_branch  = next(
        sa for sa in analyst_pool.sub_agents if sa.name == "NewsAnalystBranch"
    )

    # FetchAgent + 3 per-ticker branches + JoinerAgent = 5
    assert len(news_branch.sub_agents) == 5

    per_ticker = news_branch.sub_agents[1:-1]
    assert {p.ticker for p in per_ticker} == set(tickers)

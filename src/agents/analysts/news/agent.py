"""News analyst SequentialAgent branch — per-ticker fan-out (Phase 9).

Builds: SequentialAgent[NewsFetchAgent, *per-ticker branches, NewsJoinerAgent]

Where each per-ticker branch is
``IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))`` constructed via
``build_news_branch_for_ticker``.  Composition is done here rather than in
``orchestrator.pipeline`` so the per-tick pipeline build (driver.py /
tick.py) calls a single factory.

The legacy ``build_news_analyst`` factory (one LlmAgent over a
``VerdictBatch``) is retired in Phase 9 — every call site (pipeline, tests)
is updated to use ``build_news_branch`` instead.  See
``docs/Phase9-agent-fanning-per-ticker/spec.md``.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.fetch_agent import NewsFetchAgent
from agents.analysts.news.joiner import NewsJoinerAgent
from agents.analysts.news.per_ticker import build_news_branch_for_ticker


def build_news_branch(
    vocab: NewsVocabulary,
    *,
    tickers: list[str],
) -> SequentialAgent:
    """Construct the per-tick News analyst branch from the current watchlist.

    Args:
        vocab:    Validated NewsVocabulary holding closed-vocab tag lists.
        tickers:  The watchlist as known at pipeline-build time.  An empty
                  list is permitted — the branch becomes a fetch+joiner
                  no-op that still emits canonical (empty) news_verdicts /
                  news_evidence so downstream consumers see consistent
                  shapes.

    Returns:
        SequentialAgent named ``"NewsAnalystBranch"`` composed of
        ``[NewsFetchAgent, *per-ticker branches, NewsJoinerAgent]``.

    The caller (``orchestrator.pipeline._build_analyst_pool``) is
    responsible for invoking this once per tick with the current watchlist
    — see the Phase 9 spec §7 for the per-tick rebuild rationale.
    """
    # Build one isolated per-ticker LlmAgent branch for every ticker in
    # the watchlist.  An empty watchlist is valid — the SequentialAgent
    # then contains only [FetchAgent, JoinerAgent] which is a no-op pass.
    # ``ticker_index`` is 1-based so the terminal log shows ``1/N … N/N``.
    ticker_count = len(tickers)
    per_ticker = [
        build_news_branch_for_ticker(
            ticker, vocab,
            ticker_index = i + 1,
            ticker_count = ticker_count,
        )
        for i, ticker in enumerate(tickers)
    ]

    return SequentialAgent(
        name="NewsAnalystBranch",
        sub_agents=[
            NewsFetchAgent(name="NewsFetch"),
            *per_ticker,
            NewsJoinerAgent(name="NewsJoiner"),
        ],
    )

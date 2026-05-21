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

from google.adk.agents import ParallelAgent, SequentialAgent

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

    # Phase 9 parallelism: per-ticker branches fan out concurrently inside a
    # ParallelAgent.  ADK's ParallelAgent shallow-copies InvocationContext so
    # branches share session.state; collisions are avoided because every
    # branch writes only to its own ``temp:news_verdict_<TICKER>`` /
    # ``temp:news_context_<TICKER>`` keys.  The surrounding Sequential
    # preserves the Fetch -> Fan-out -> Joiner ordering: the Parallel block
    # only yields its terminator once every child completes, so the joiner
    # observes a fully populated state.
    fanout = ParallelAgent(
        name="NewsPerTickerFanout",
        sub_agents=per_ticker,
    )

    return SequentialAgent(
        name="NewsAnalystBranch",
        sub_agents=[
            NewsFetchAgent(name="NewsFetch"),
            fanout,
            NewsJoinerAgent(name="NewsJoiner"),
        ],
    )

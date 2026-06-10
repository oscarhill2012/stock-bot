"""The top-level News and Fundamental branches are SequentialAgents of
[FetchAgent, ParallelAgent[per-ticker branches], JoinerAgent].

Phase 9 post-parallelism: per-ticker branches fan out concurrently inside
a ``ParallelAgent`` so all tickers' LLM calls overlap; the surrounding
Sequential preserves Fetch -> Fan-out -> Joiner ordering.
"""
from __future__ import annotations

from google.adk.agents import ParallelAgent, SequentialAgent

from agents.analysts.fundamental.agent import build_fundamental_branch
from agents.analysts.fundamental.fetch_agent import FundamentalFetchAgent
from agents.analysts.fundamental.joiner import FundamentalJoinerAgent
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.agent import build_news_branch
from agents.analysts.news.fetch_agent import NewsFetchAgent
from agents.analysts.news.joiner import NewsJoinerAgent
from agents.isolated_failure import IsolatedFailureWrapper


def test_news_branch_shape():
    """[NewsFetchAgent, ParallelAgent[IsolatedFailureWrapper×N], NewsJoinerAgent]."""

    h = load_heuristics()
    branch = build_news_branch(h.news_vocabulary, tickers=["AAPL", "MSFT", "GOOG"])

    assert isinstance(branch, SequentialAgent)
    subs = branch.sub_agents
    assert len(subs) == 3  # fetch + fan-out + joiner

    assert isinstance(subs[0],  NewsFetchAgent)
    assert isinstance(subs[1],  ParallelAgent)
    assert isinstance(subs[-1], NewsJoinerAgent)

    fanout_children = subs[1].sub_agents
    assert len(fanout_children) == 3
    for inner in fanout_children:
        assert isinstance(inner, IsolatedFailureWrapper)
        assert inner.analyst == "news"


def test_fundamental_branch_shape():
    """[FundamentalFetchAgent, ParallelAgent[IsolatedFailureWrapper×N], FundamentalJoinerAgent]."""

    h = load_heuristics()
    branch = build_fundamental_branch(h.fundamental_vocabulary, tickers=["AAPL", "MSFT"])

    assert isinstance(branch, SequentialAgent)
    subs = branch.sub_agents
    assert len(subs) == 3

    assert isinstance(subs[0],  FundamentalFetchAgent)
    assert isinstance(subs[1],  ParallelAgent)
    assert isinstance(subs[-1], FundamentalJoinerAgent)

    fanout_children = subs[1].sub_agents
    assert len(fanout_children) == 2
    for inner in fanout_children:
        assert isinstance(inner, IsolatedFailureWrapper)
        assert inner.analyst == "fundamental"


def test_empty_watchlist_yields_minimal_branch():
    """Zero tickers → [FetchAgent, empty ParallelAgent, JoinerAgent] (valid no-op)."""

    h = load_heuristics()
    branch = build_news_branch(h.news_vocabulary, tickers=[])

    subs = branch.sub_agents
    assert len(subs) == 3
    assert isinstance(subs[0],  NewsFetchAgent)
    assert isinstance(subs[1],  ParallelAgent)
    assert subs[1].sub_agents == []
    assert isinstance(subs[-1], NewsJoinerAgent)

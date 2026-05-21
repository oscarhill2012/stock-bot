"""The top-level News and Fundamental branches are SequentialAgents of
[FetchAgent, *per-ticker branches, JoinerAgent]."""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.analysts.fundamental.agent import build_fundamental_branch
from agents.analysts.fundamental.fetch_agent import FundamentalFetchAgent
from agents.analysts.fundamental.joiner import FundamentalJoinerAgent
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.agent import build_news_branch
from agents.analysts.news.fetch_agent import NewsFetchAgent
from agents.analysts.news.joiner import NewsJoinerAgent
from agents.isolated_failure import IsolatedFailureWrapper


def test_news_branch_shape():
    """[NewsFetchAgent, IsolatedFailureWrapper×N, NewsJoinerAgent]."""

    h = load_heuristics()
    branch = build_news_branch(h.news_vocabulary, tickers=["AAPL", "MSFT", "GOOG"])

    assert isinstance(branch, SequentialAgent)
    subs = branch.sub_agents
    assert len(subs) == 5  # fetch + 3 per-ticker + joiner

    assert isinstance(subs[0],  NewsFetchAgent)
    assert isinstance(subs[-1], NewsJoinerAgent)
    for inner in subs[1:-1]:
        assert isinstance(inner, IsolatedFailureWrapper)
        assert inner.analyst == "news"


def test_fundamental_branch_shape():
    """[FundamentalFetchAgent, IsolatedFailureWrapper×N, FundamentalJoinerAgent]."""

    h = load_heuristics()
    branch = build_fundamental_branch(h.fundamental_vocabulary, tickers=["AAPL", "MSFT"])

    assert isinstance(branch, SequentialAgent)
    subs = branch.sub_agents
    assert len(subs) == 4

    assert isinstance(subs[0],  FundamentalFetchAgent)
    assert isinstance(subs[-1], FundamentalJoinerAgent)
    for inner in subs[1:-1]:
        assert isinstance(inner, IsolatedFailureWrapper)
        assert inner.analyst == "fundamental"


def test_empty_watchlist_yields_minimal_branch():
    """Zero tickers → just [FetchAgent, JoinerAgent] (still a valid no-op branch)."""

    h = load_heuristics()
    branch = build_news_branch(h.news_vocabulary, tickers=[])

    subs = branch.sub_agents
    assert len(subs) == 2
    assert isinstance(subs[0],  NewsFetchAgent)
    assert isinstance(subs[-1], NewsJoinerAgent)

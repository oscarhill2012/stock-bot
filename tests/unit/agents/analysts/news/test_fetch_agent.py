"""Unit tests for NewsFetchAgent — Phase 14 routed + staleness-filtered flow.

Plan 2's ``route_articles`` and the embedding backend are both stubbed in
most tests below: these tests pin THIS agent's contract (fetch → union-dedup
→ route → partition → render → single state_delta event), not the router's
or the embedder's.  The one exception is
``test_company_name_matching_routes_prose_headline_to_the_ticker``, which
uses the REAL router to guard the company-name wiring specifically —
see its docstring.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.news.fetch_agent import NewsFetchAgent
from agents.analysts.news.history import NewsHistoryStore
from data.models import NewsArticle

_AS_OF = "2026-07-06T14:00:00"


def _article(title: str, summary: str, published: str, url: str) -> dict:
    """Build a serialised article dict in the provider shape."""
    return {"title": title, "summary": summary,
            "published_at": published, "url": url}


async def _stub_embed(text: str) -> list[float]:
    """Deterministic vectors: 'beats' stories are parallel to each other and
    orthogonal to everything else."""
    if "beats" in text:
        return [1.0, 0.0, 0.0]
    return [0.0, 1.0, 0.0]


def _fake_router(company: dict, macro: list | None = None):
    """Build a route_articles stand-in returning a fixed RoutedArticles shape.

    Parameters:
        company: ticker → article list mapping to return.
        macro:   macro-stream articles (default empty).

    Returns:
        A callable matching ``route_articles(articles, watchlist, ...)``.
    """
    def _route(articles: list, watchlist: list[str], **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(company=company, macro=macro or [])

    return _route


async def _run_agent(state: dict) -> tuple[list, dict]:
    """Create a session with ``state``, run NewsFetchAgent, return (events, delta).

    Parameters:
        state: initial session state (tickers, as_of, ...).

    Returns:
        The event list and the final event's state_delta dict.
    """
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t-1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )
    events = [ev async for ev in agent.run_async(ctx)]
    return events, events[-1].actions.state_delta


@pytest.mark.asyncio
async def test_fresh_articles_land_in_per_ticker_context_and_data():
    """Happy path: routed novel articles render in FRESH and populate
    temp:news_data with news/fresh/stale slices."""
    art = _article("AAPL beats on earnings", "Big beat.",
                   "2026-07-05T12:00:00", "https://news/a1")
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[art])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": [art]})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        _events, delta = await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})

    data = delta["temp:news_data"]["AAPL"]
    assert data["fresh"] == [art]           # POSITIVE: the article IS fresh
    assert data["stale"] == []
    assert data["news"] == [art]

    context = delta["temp:news_context_AAPL"]
    assert "FRESH ARTICLES" in context
    assert "AAPL beats on earnings" in context
    assert "Big beat." in context


@pytest.mark.asyncio
async def test_previously_seen_article_moves_to_stale_on_the_next_tick():
    """The same store across two agent runs: tick 2 renders the article
    headline-only under PREVIOUSLY SEEN.  This is the agent-level staleness
    guarantee the spec's D4 token reduction rests on."""
    art = _article("AAPL beats on earnings", "Big beat.",
                   "2026-07-05T12:00:00", "https://news/a1")
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[art])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": [art]})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})
        _events, delta = await _run_agent(
            {"tickers": ["AAPL"], "as_of": "2026-07-07T14:00:00"},
        )

    data = delta["temp:news_data"]["AAPL"]
    assert data["stale"] == [art]           # POSITIVE: it IS filtered
    assert data["fresh"] == []

    context = delta["temp:news_context_AAPL"]
    assert "PREVIOUSLY SEEN" in context
    assert "AAPL beats on earnings" in context
    assert "Big beat." not in context       # stale renders headline-only


@pytest.mark.asyncio
async def test_macro_stream_is_not_consumed_here():
    """Roundup/macro articles routed to .macro belong to Plan 5's analyst —
    the per-ticker context must not contain them."""
    roundup = _article("Markets roundup: five movers", "Blah.",
                       "2026-07-05T12:00:00", "https://news/r1")
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[roundup])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": []}, macro=[roundup])),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        _events, delta = await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})

    assert "(no news available)" in delta["temp:news_context_AAPL"]
    assert "Markets roundup" not in delta["temp:news_context_AAPL"]


@pytest.mark.asyncio
async def test_provider_failure_degrades_that_ticker_to_empty():
    """A per-ticker provider error must not kill the branch (isolation) —
    that ticker just renders the no-news placeholder."""
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(side_effect=RuntimeError("provider down"))),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": []})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
    ):
        _events, delta = await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})

    assert "(no news available)" in delta["temp:news_context_AAPL"]


@pytest.mark.asyncio
async def test_embedding_failure_fails_the_agent_loudly():
    """Unlike provider failures, an embedding outage is NOT isolated — the
    staleness verdicts would be garbage, so the agent must raise."""
    art = _article("AAPL beats on earnings", "Big beat.",
                   "2026-07-05T12:00:00", "https://news/a1")

    async def _broken(text: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    store = NewsHistoryStore(embed_fn=_broken)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[art])),
        patch("agents.analysts.news.fetch_agent.route_articles",
              new=_fake_router({"AAPL": [art]})),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
        pytest.raises(RuntimeError, match="embedding endpoint down"),
    ):
        await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})


@pytest.mark.asyncio
async def test_company_name_matching_routes_prose_headline_to_the_ticker():
    """Guards the company_names wiring into the REAL router.

    All the tests above stub ``route_articles`` entirely, so none of them
    would notice if the agent stopped passing ``company_names`` through to
    the real router — the article would then only match on the bare ticker
    symbol, which almost never appears in financial-journalism prose, and
    the company stream would silently collapse to empty (the exact
    silent-degradation bug class this project bans).  This test uses the
    REAL ``route_articles`` (only the provider, history store, and watchlist
    lookup are patched) with a headline that names the company ("Apple") but
    never the ticker symbol ("AAPL") — it must still land in AAPL's fresh
    bucket and render under FRESH ARTICLES.
    """
    art = NewsArticle(
        ticker="AAPL",
        headline="Apple unveils record quarter",
        summary="Broad strength across product lines.",
        url="https://news/apple-record-quarter",
        published_at="2026-07-05T12:00:00",
    )
    store = NewsHistoryStore(embed_fn=_stub_embed)

    with (
        patch("agents.analysts.news.fetch_agent.get_stock_news",
              new=AsyncMock(return_value=[art])),
        patch("agents.analysts.news.fetch_agent.get_news_history_store",
              return_value=store),
        patch("agents.analysts.news.fetch_agent.get_watchlist_with_names",
              return_value=[{"symbol": "AAPL", "name": "Apple"}]),
    ):
        _events, delta = await _run_agent({"tickers": ["AAPL"], "as_of": _AS_OF})

    data = delta["temp:news_data"]["AAPL"]
    assert len(data["fresh"]) == 1           # POSITIVE: routed to AAPL, not macro
    assert data["stale"] == []

    context = delta["temp:news_context_AAPL"]
    assert "FRESH ARTICLES" in context
    assert "Apple unveils record quarter" in context

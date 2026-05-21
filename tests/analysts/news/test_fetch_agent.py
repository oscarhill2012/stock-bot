"""Unit tests for NewsFetchAgent.

The fetch agent runs ONCE per tick, calls the news provider for every
watchlist ticker, and yields exactly one state_delta event containing:
  - temp:news_data — dict keyed by ticker (machine-readable)
  - temp:news_context_<TICKER> — per-ticker formatted text block (one key
    per ticker; consumed by that ticker's LlmAgent via {news_context})
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.news.fetch_agent import NewsFetchAgent


@pytest.mark.asyncio
async def test_fetch_writes_per_ticker_context_keys():
    """One temp:news_context_<TICKER> key is written per watchlist ticker."""

    tickers = ["AAPL", "MSFT"]

    fake_news = {
        "AAPL": [{"title": "AAPL beats", "summary": "Strong quarter.", "published_at": "2026-05-21"}],
        "MSFT": [{"title": "MSFT guides up", "summary": "Cloud strong.", "published_at": "2026-05-21"}],
    }

    async def _mock_get_stock_news(ticker, as_of=None):
        return fake_news.get(ticker, [])

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={
            "tickers": tickers,
            "as_of":   datetime(2026, 5, 21, 14, 0),
        },
        session_id="t1",
    )

    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-1",
        agent=agent,
    )

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock_get_stock_news):
        events = [ev async for ev in agent.run_async(ctx)]

    assert len(events) == 1
    state_delta = events[0].actions.state_delta

    # temp:news_data carries the machine-readable per-ticker dict.
    assert "temp:news_data" in state_delta
    nd = state_delta["temp:news_data"]
    assert "AAPL" in nd and "MSFT" in nd
    assert nd["AAPL"]["news"][0]["title"] == "AAPL beats"

    # One temp:news_context_<TICKER> per ticker, each containing only that ticker's block.
    assert "temp:news_context_AAPL" in state_delta
    assert "temp:news_context_MSFT" in state_delta
    assert "AAPL beats" in state_delta["temp:news_context_AAPL"]
    assert "MSFT guides up" not in state_delta["temp:news_context_AAPL"]
    assert "MSFT guides up" in state_delta["temp:news_context_MSFT"]


@pytest.mark.asyncio
async def test_fetch_degrades_on_provider_error():
    """A provider exception for one ticker yields an empty context block for it."""

    async def _flaky_get_stock_news(ticker, as_of=None):
        if ticker == "MSFT":
            raise RuntimeError("provider down")
        return [{"title": "AAPL beats", "summary": "ok.", "published_at": "2026-05-21"}]

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )

    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(
        session_service=svc, session=session,
        invocation_id="inv-1", agent=agent,
    )

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _flaky_get_stock_news):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta
    # MSFT entry exists but is empty.
    assert sd["temp:news_data"]["MSFT"]["news"] == []
    # Per-ticker context for MSFT still exists, just empty/placeholder.
    assert "temp:news_context_MSFT" in sd
    assert "(no news available)" in sd["temp:news_context_MSFT"]


@pytest.mark.asyncio
async def test_fetch_writes_aggregate_news_context_for_trace():
    """The aggregate ``temp:news_context`` key (multi-ticker joined block) is
    retained for trace/debug surfaces — see Phase 9 spec §1.
    """

    async def _mock(ticker, as_of=None):
        return [{"title": f"{ticker} hed", "summary": "body", "published_at": "2026-05-21"}]

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(session_service=svc, session=session,
                            invocation_id="inv-1", agent=agent)

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta
    assert "temp:news_context" in sd
    # Both ticker headers appear in the joined block.
    assert "=== AAPL ===" in sd["temp:news_context"]
    assert "=== MSFT ===" in sd["temp:news_context"]

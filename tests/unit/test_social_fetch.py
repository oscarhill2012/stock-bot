"""Tier-1 tests: social_fetch_callback writes state['social_data'] and returns None.

The callback's sole responsibility is fetching raw social data and storing it
under ``state["social_data"]``.  Verdict derivation is handled by
``SocialAnalyst._run_async_impl`` — the callback must NOT derive verdicts and
must NOT return a skip-Content (doing so would prevent the after-callback from
ever firing).
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_social_fetch_writes_state_dict(monkeypatch):
    """Callback populates social_data keyed by ticker."""
    from agents.analysts.social import fetch as fetch_mod
    from data.models import SocialSentiment, SocialSentimentSnapshot

    fake_result = SocialSentiment(
        ticker="AAPL",
        snapshots=[
            SocialSentimentSnapshot(
                platform="reddit",
                mention_count=10,
                positive_score=0.3,
                negative_score=0.1,
                score=0.2,
            )
        ],
        aggregate_score=0.2,
    )

    async def fake_get_social_sentiment(ticker, *, as_of=None, **kwargs):
        # Accept ``as_of`` forwarded by the social fetch callback (Phase C migration).
        assert ticker == "AAPL"
        return fake_result

    monkeypatch.setattr(fetch_mod, "get_social_sentiment", fake_get_social_sentiment)

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.state = {"tickers": ["AAPL"]}
    result = await fetch_mod.social_fetch_callback(ctx)

    # social_data must be populated for the agent body to consume.
    assert "AAPL" in ctx.state["social_data"]

    # Callback must NOT derive verdicts — that is _run_async_impl's job.
    assert "social_verdicts" not in ctx.state

    # Callback must return None so ADK continues into _run_async_impl and
    # does NOT set ctx.end_invocation = True (which would skip the
    # after_agent_callback).
    assert result is None


@pytest.mark.asyncio
async def test_social_fetch_writes_per_platform_shape(monkeypatch):
    """Fetched data is stored as {platform: {mention_count, positive_score, negative_score}}."""
    from agents.analysts.social import fetch as fetch_mod
    from data.models import SocialSentiment, SocialSentimentSnapshot

    fake_result = SocialSentiment(
        ticker="MSFT",
        snapshots=[
            SocialSentimentSnapshot(
                platform="reddit",
                mention_count=5,
                positive_score=0.4,
                negative_score=0.2,
                score=0.2,
            ),
            SocialSentimentSnapshot(
                platform="twitter",
                mention_count=15,
                positive_score=0.6,
                negative_score=0.1,
                score=0.5,
            ),
        ],
        aggregate_score=0.4,
    )

    async def fake_get_social_sentiment(ticker, *, as_of=None, **kwargs):
        # Accept ``as_of`` forwarded by the social fetch callback (Phase C migration).
        return fake_result

    monkeypatch.setattr(fetch_mod, "get_social_sentiment", fake_get_social_sentiment)

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.state = {"tickers": ["MSFT"]}
    await fetch_mod.social_fetch_callback(ctx)

    data = ctx.state["social_data"]["MSFT"]
    assert "reddit" in data
    assert "twitter" in data
    assert data["reddit"]["mention_count"] == 5
    assert data["twitter"]["mention_count"] == 15


@pytest.mark.asyncio
async def test_social_fetch_empty_on_provider_failure(monkeypatch):
    """When the provider raises, social_data[ticker] is set to {} (no crash)."""
    from agents.analysts.social import fetch as fetch_mod

    async def failing_get_social_sentiment(ticker):
        raise RuntimeError("provider down")

    monkeypatch.setattr(fetch_mod, "get_social_sentiment", failing_get_social_sentiment)

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.state = {"tickers": ["GOOG"]}
    result = await fetch_mod.social_fetch_callback(ctx)

    assert ctx.state["social_data"]["GOOG"] == {}
    assert result is None

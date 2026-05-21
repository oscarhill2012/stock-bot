"""Tests that each analyst fetch callback reads ``state['as_of']`` and forwards it.

Covers the three remaining legacy callbacks — technical, social, and smart_money.
The news and fundamental callbacks were retired in Phase 9 (replaced by
``NewsFetchAgent`` and ``FundamentalFetchAgent``); their as_of tests have been
removed from this file.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


FIXED = datetime(2023, 3, 15, 9, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_technical_fetch_forwards_as_of() -> None:
    """``technical_fetch_callback`` passes ``as_of`` from state into both wrappers."""
    from agents.analysts.technical.fetch import technical_fetch_callback

    state: dict = {"tickers": ["AAPL"], "as_of": FIXED}
    ctx = SimpleNamespace(state=state)

    with (
        patch(
            "agents.analysts.technical.fetch.get_price_history",
            new=AsyncMock(return_value=None),
        ) as ph,
        patch(
            "agents.analysts.technical.fetch.get_company_ratios",
            new=AsyncMock(return_value=None),
        ) as cr,
    ):
        await technical_fetch_callback(ctx)

    assert ph.await_args.kwargs.get("as_of") == FIXED, (
        "get_price_history did not receive as_of"
    )
    assert cr.await_args.kwargs.get("as_of") == FIXED, (
        "get_company_ratios did not receive as_of"
    )


@pytest.mark.asyncio
async def test_social_fetch_forwards_as_of() -> None:
    """``social_fetch_callback`` passes ``as_of`` from state into ``get_social_sentiment``."""
    from agents.analysts.social.fetch import social_fetch_callback

    # SocialSentiment with an empty snapshots list avoids attribute errors.
    mock_sentiment = MagicMock()
    mock_sentiment.snapshots = []

    state: dict = {"tickers": ["AAPL"], "as_of": FIXED}
    ctx = SimpleNamespace(state=state)

    with patch(
        "agents.analysts.social.fetch.get_social_sentiment",
        new=AsyncMock(return_value=mock_sentiment),
    ) as m:
        await social_fetch_callback(ctx)

    assert m.await_args.kwargs.get("as_of") == FIXED, (
        "get_social_sentiment did not receive as_of"
    )


@pytest.mark.asyncio
async def test_smart_money_fetch_forwards_as_of() -> None:
    """``smart_money_fetch_callback`` passes ``as_of`` from state to both wrappers."""
    from agents.analysts.smart_money.fetch import smart_money_fetch_callback

    state: dict = {"tickers": ["AAPL"], "as_of": FIXED}
    ctx = SimpleNamespace(state=state)

    with (
        patch(
            "agents.analysts.smart_money.fetch.get_public_figure_trades",
            new=AsyncMock(return_value=[]),
        ) as pol,
        patch(
            "agents.analysts.smart_money.fetch.get_notable_holders",
            new=AsyncMock(return_value=[]),
        ) as holders,
    ):
        await smart_money_fetch_callback(ctx)

    assert pol.await_args.kwargs.get("as_of") == FIXED, (
        "get_public_figure_trades did not receive as_of"
    )
    assert holders.await_args.kwargs.get("as_of") == FIXED, (
        "get_notable_holders did not receive as_of"
    )

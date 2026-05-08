"""Sentiment analyst data fetch callback."""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_stock_news, get_social_sentiment

logger = logging.getLogger(__name__)


async def sentiment_fetch_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Fetch news headlines and social sentiment for every watchlist ticker before the LLM runs."""
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    sentiment_data = {}
    for ticker in tickers:
        try:
            news = await get_stock_news(ticker)
        except Exception as exc:
            logger.warning("news fetch failed for %s: %s", ticker, exc)
            news = []
        try:
            social = await get_social_sentiment(ticker)
        except Exception as exc:
            logger.warning("social-sentiment fetch failed for %s: %s", ticker, exc)
            social = None
        sentiment_data[ticker] = {
            "news": [a.model_dump() if hasattr(a, "model_dump") else a for a in news],
            "social": social.model_dump() if hasattr(social, "model_dump") else social,
        }

    state["sentiment_data"] = sentiment_data
    return None

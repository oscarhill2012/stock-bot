"""Sentiment analyst data fetch callback."""
from __future__ import annotations

from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_stock_news, get_social_sentiment


async def sentiment_fetch_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])
    sentiment_data = {}
    for ticker in tickers:
        news = await get_stock_news(ticker)
        social = await get_social_sentiment(ticker)
        sentiment_data[ticker] = {
            "news": [a.model_dump() if hasattr(a, "model_dump") else a for a in news],
            "social": social.model_dump() if hasattr(social, "model_dump") else social,
        }
    state["sentiment_data"] = sentiment_data
    return None

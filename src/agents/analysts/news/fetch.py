"""News analyst data fetch callback.

Fetches news headlines for every watchlist ticker before the LLM runs.
Narrowed from the old sentiment_fetch_callback to ``news/`` only — the
``social_sentiment/`` branch is removed here and migrates to the new
Social analyst in Task 7.
"""
from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_stock_news

logger = logging.getLogger(__name__)


async def news_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch news headlines for every watchlist ticker before the LLM runs.

    Reads ``state["tickers"]`` and calls the news provider for each ticker.
    Writes a dict keyed by ticker to ``state["news_data"]``.  Each per-ticker
    value contains a ``"news"`` list of serialised ``NewsArticle`` dicts.

    The social_sentiment branch has been removed — that data now belongs to
    the Social analyst (Task 7).

    Args:
        callback_context: ADK callback context carrying the mutable pipeline state.

    Returns:
        None — this callback never short-circuits the agent run.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    news_data = {}

    for ticker in tickers:
        try:
            news = await get_stock_news(ticker)
        except Exception as exc:
            logger.warning("news fetch failed for %s: %s", ticker, exc)
            news = []

        news_data[ticker] = {
            "news": [a.model_dump() if hasattr(a, "model_dump") else a for a in news],
        }

    state["news_data"] = news_data
    return None

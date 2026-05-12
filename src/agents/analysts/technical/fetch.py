"""Technical analyst data fetch callback."""
from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_stock_stats

logger = logging.getLogger(__name__)


async def technical_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch OHLCV + fundamentals for every watchlist ticker before the LLM runs."""
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    technical_data = {}
    for ticker in tickers:
        try:
            stats = await get_stock_stats(ticker)
        except Exception as exc:
            logger.warning("stats fetch failed for %s: %s", ticker, exc)
            stats = None
        technical_data[ticker] = stats.model_dump() if hasattr(stats, "model_dump") else stats

    state["technical_data"] = technical_data
    return None

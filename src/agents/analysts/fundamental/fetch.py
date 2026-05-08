"""Fundamental analyst data fetch callback."""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_company_filings

logger = logging.getLogger(__name__)


async def fundamental_fetch_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Fetch recent SEC filings for every watchlist ticker before the LLM runs."""
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    fundamental_data = {}
    for ticker in tickers:
        try:
            filings = await get_company_filings(ticker)
        except Exception as exc:
            logger.warning("filings fetch failed for %s: %s", ticker, exc)
            filings = []
        fundamental_data[ticker] = [
            f.model_dump() if hasattr(f, "model_dump") else f for f in filings
        ]

    state["fundamental_data"] = fundamental_data
    return None

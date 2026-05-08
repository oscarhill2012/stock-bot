"""Smart Money gate: skip LLM if no material insider/politician/holder activity."""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import (
    get_insider_trades,
    get_notable_holders,
    get_public_figure_trades,
)

INSIDER_THRESHOLD = 100_000
INSIDER_LOOKBACK_DAYS = 14
POLITICIAN_LOOKBACK_DAYS = 30
HOLDER_LOOKBACK_DAYS = 90

logger = logging.getLogger(__name__)


async def smart_money_fetch_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Fetch smart-money data; return Content to skip LLM if no signal detected."""
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    smart_money_data: dict = {
        "insiders": {},
        "politicians": {},
        "notable_holders": {},
    }
    has_signal = False

    for ticker in tickers:
        try:
            insiders = await get_insider_trades(ticker, lookback_days=INSIDER_LOOKBACK_DAYS)
        except Exception as exc:
            logger.warning("insider_trades fetch failed for %s: %s", ticker, exc)
            insiders = []
        try:
            politicians = await get_public_figure_trades(ticker, lookback_days=POLITICIAN_LOOKBACK_DAYS)
        except Exception as exc:
            logger.warning("politician_trades fetch failed for %s: %s", ticker, exc)
            politicians = []
        try:
            holders = await get_notable_holders(ticker, lookback_days=HOLDER_LOOKBACK_DAYS)
        except Exception as exc:
            logger.warning("notable_holders fetch failed for %s: %s", ticker, exc)
            holders = []

        smart_money_data["insiders"][ticker] = [
            t.model_dump() if hasattr(t, "model_dump") else t for t in insiders
        ]
        smart_money_data["politicians"][ticker] = [
            t.model_dump() if hasattr(t, "model_dump") else t for t in politicians
        ]
        smart_money_data["notable_holders"][ticker] = [
            h.model_dump() if hasattr(h, "model_dump") else h for h in holders
        ]

        big_insiders = [
            t for t in insiders
            if abs(getattr(t, "transaction_value", 0) or 0) >= INSIDER_THRESHOLD
        ]
        if big_insiders or politicians or holders:
            has_signal = True

    state["smart_money_data"] = smart_money_data

    if not has_signal:
        state["smart_money_signals"] = []
        return genai_types.Content(
            parts=[genai_types.Part(text="no smart money signal — skipping")],
            role="model",
        )
    return None

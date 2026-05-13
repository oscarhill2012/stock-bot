"""Fundamental analyst data fetch callback.

Phase 5 introduces a triad of data domains for the Fundamental analyst:

- **stats** — company fundamentals (P/E, ROE, FCF, etc.) via the active stats provider.
- **filings** — recent SEC filings (10-K, 10-Q, 8-K) with MD&A / risk-factor excerpts.
- **insider** — Form 4 insider trades and derivative transactions as a ``Form4Bundle``.

Each domain is fetched independently.  A failure in one domain is logged and
falls back to a safe empty value so that the other two domains are still
available to the downstream extractor and LLM.

The resulting ``state["fundamental_data"]`` layout per ticker is::

    {
        "stats":   <dict from StockStats.model_dump() | None on failure>,
        "filings": [<Filing.model_dump()>, ...],
        "insider": <Form4Bundle instance>,
    }
"""
from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_company_filings, get_insider_trades, get_stock_stats
from data.models import Form4Bundle

logger = logging.getLogger(__name__)

# Lookback window for Form 4 insider trades passed to the provider.
_INSIDER_LOOKBACK_DAYS = 30


async def fundamental_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch stats, SEC filings, and insider trades for every watchlist ticker.

    Iterates ``state["tickers"]`` and, for each ticker, dispatches three
    independent provider calls.  Partial failures are tolerated — each domain
    catches its own exception, logs a warning, and falls back to an empty
    payload rather than aborting the entire ticker's fetch.

    Parameters
    ----------
    callback_context:
        ADK callback context.  ``callback_context.state["tickers"]`` must be a
        list of ticker strings.

    Returns
    -------
    google.genai.types.Content | None
        Always ``None`` — this callback never short-circuits the LLM call.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    fundamental_data: dict[str, dict] = {}

    for ticker in tickers:
        # --- stats ---
        try:
            stats_obj = await get_stock_stats(ticker)
            stats_payload = (
                stats_obj.model_dump() if hasattr(stats_obj, "model_dump") else stats_obj
            )
        except Exception as exc:
            logger.warning("stats fetch failed for %s: %s", ticker, exc)
            stats_payload = None

        # --- filings ---
        try:
            filings = await get_company_filings(ticker)
            filings_payload = [
                f.model_dump() if hasattr(f, "model_dump") else f for f in filings
            ]
        except Exception as exc:
            logger.warning("filings fetch failed for %s: %s", ticker, exc)
            filings_payload = []

        # --- insider trades (Form 4) ---
        try:
            insider_bundle = await get_insider_trades(
                ticker, lookback_days=_INSIDER_LOOKBACK_DAYS
            )
            # Store the raw Form4Bundle so the extractor can access typed fields
            # directly without re-parsing a dict.
            if not isinstance(insider_bundle, Form4Bundle):
                # Guard: if the provider returned something unexpected, wrap it.
                logger.warning(
                    "insider_trades for %s returned %s, expected Form4Bundle — using empty bundle",
                    ticker,
                    type(insider_bundle).__name__,
                )
                insider_bundle = Form4Bundle(trades=[], derivatives=[])
        except Exception as exc:
            logger.warning("insider_trades fetch failed for %s: %s", ticker, exc)
            insider_bundle = Form4Bundle(trades=[], derivatives=[])

        fundamental_data[ticker] = {
            "stats": stats_payload,
            "filings": filings_payload,
            "insider": insider_bundle,
        }

    state["fundamental_data"] = fundamental_data
    return None

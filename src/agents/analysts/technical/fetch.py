"""Technical analyst data fetch callback.

Fetches the OHLCV price history *and* scalar company ratios for every
watchlist ticker. Writes ``state["technical_data"][ticker]`` with two sub-keys:

- ``price_history`` — dict from ``PriceHistory.model_dump()``; the extractor
  reads bars from here.
- ``ratios`` — dict from ``CompanyRatios.model_dump()``; reserved for future
  cross-feature work (e.g. dividend-yield-aware overrides). Not required by
  the current extractor.

Phase 5 redesign: the old ``get_stock_stats`` call (which bundled both OHLCV
history and fundamentals together) is replaced by two separate provider calls
sharing the same underlying yfinance round-trip via an LRU cache.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_company_ratios, get_price_history
from observability.trace import _trace_maybe

logger = logging.getLogger(__name__)


async def technical_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch ``PriceHistory`` and ``CompanyRatios`` for every watchlist ticker.

    Iterates ``state["tickers"]`` and, for each ticker, dispatches two
    independent provider calls. Partial failures are tolerated — each domain
    catches its own exception, logs a warning, and falls back to ``None``.

    Writes ``state["technical_data"]`` as a dict keyed by ticker, each value
    being a dict with ``"price_history"`` and ``"ratios"`` sub-keys.

    Parameters
    ----------
    callback_context:
        ADK callback context. ``callback_context.state["tickers"]`` must be a
        list of ticker strings.

    Returns
    -------
    google.genai.types.Content | None
        Always ``None`` — this callback never short-circuits the LLM call.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    # Pull the historical clock from session state; default to wall-clock for live.
    as_of: datetime = state.get("as_of") or datetime.now(tz=UTC)

    technical_data: dict[str, dict] = {}

    for ticker in tickers:

        # --- price history ---
        try:
            ph = await get_price_history(ticker, as_of=as_of)
            ph_payload = ph.model_dump() if hasattr(ph, "model_dump") else ph
        except Exception as exc:
            logger.warning("price_history fetch failed for %s: %s", ticker, exc)
            ph_payload = None

        # --- ratios ---
        try:
            cr = await get_company_ratios(ticker, as_of=as_of)
            cr_payload = cr.model_dump() if hasattr(cr, "model_dump") else cr
        except Exception as exc:
            logger.warning("company_ratios fetch failed for %s: %s", ticker, exc)
            cr_payload = None

        technical_data[ticker] = {
            "price_history": ph_payload,
            "ratios":        cr_payload,
        }

    state["technical_data"] = technical_data

    # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
    _trace_maybe(state, "01_fetch_technical", technical_data)

    return None

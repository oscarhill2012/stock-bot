"""Fetch callback for the Social analyst.

This callback does one job: fetch social-sentiment data and write it to
state so that ``SocialAnalyst._run_async_impl`` can run the deterministic
verdict logic.

Returning ``None`` allows ADK to continue into ``_run_async_impl`` rather
than short-circuiting the agent.  Verdict derivation lives in the agent
body, not here.
"""
from __future__ import annotations

import logging
from datetime import datetime

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_social_sentiment
from data.providers.social_sentiment.finnhub import PremiumGatedError
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe

logger = logging.getLogger(__name__)


async def social_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch social-sentiment data for every watchlist ticker.

    Reads ``state["tickers"]``.  For each ticker, fetches the Finnhub
    Reddit + Twitter aggregates and stores the raw per-platform dicts in
    ``state["temp:social_data"]`` under the ticker symbol.

    Returns ``None`` so ADK continues into ``SocialAnalyst._run_async_impl``
    which handles verdict derivation.

    Args:
        callback_context: ADK callback context carrying the mutable pipeline
                          state.

    Returns:
        ``None`` — the agent body runs normally after this callback.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", []) or []

    # Pull the historical clock from session state; default to wall-clock for live.
    as_of: datetime = resolve_as_of(
        state.get("as_of"), allow_wallclock=True, site="social/fetch",
    )

    social_data: dict[str, dict] = {}

    for ticker in tickers:
        # ── Fetch ─────────────────────────────────────────────────────────────
        try:
            sentiment = await get_social_sentiment(ticker, as_of=as_of)
        except Exception as exc:
            # Premium-gate is the only documented soft-fail path; anything else
            # is a real provider failure and the warning should surface it.
            if isinstance(exc, PremiumGatedError):
                logger.info("social_sentiment premium-gated for %s", ticker)
            else:
                logger.warning("social_sentiment fetch failed for %s: %s", ticker, exc)
            sentiment = None

        # Pass the typed snapshots through as a list — the extractor reads
        # raw["snapshots"] (list of SocialSentimentSnapshot.model_dump()) and
        # raw["aggregate_score"].  The old per-platform dict-of-dict shape has
        # been removed (Phase 7, Fix K / Task 2.11).
        if sentiment is not None:
            social_data[ticker] = {
                "snapshots":       [s.model_dump() for s in sentiment.snapshots],
                "aggregate_score": sentiment.aggregate_score,
            }
        else:
            social_data[ticker] = {"snapshots": [], "aggregate_score": None}

    # Prefixed ``temp:`` — consumed within the same invocation by
    # ``SocialAnalyst._run_async_impl``; must not survive to the next tick.
    state["temp:social_data"] = social_data

    # Surface trace — no-op unless state["temp:_trace"] is set by trace_tick.py.
    _trace_maybe(state, "01_fetch_social", social_data)

    # Return None so the agent body (_run_async_impl) continues normally.
    return None

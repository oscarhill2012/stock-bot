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

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_social_sentiment

logger = logging.getLogger(__name__)


async def social_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch social-sentiment data for every watchlist ticker.

    Reads ``state["tickers"]``.  For each ticker, fetches the Finnhub
    Reddit + Twitter aggregates and stores the raw per-platform dicts in
    ``state["social_data"]`` under the ticker symbol.

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

    social_data: dict[str, dict] = {}

    for ticker in tickers:
        # ── Fetch ─────────────────────────────────────────────────────────────
        try:
            sentiment = await get_social_sentiment(ticker)
        except Exception as exc:
            logger.warning("social_sentiment fetch failed for %s: %s", ticker, exc)
            sentiment = None

        # Convert the structured SocialSentiment model to the dict shape that
        # extract_social_features expects: {"reddit": {...}, "twitter": {...}}.
        if sentiment is not None:
            per_platform: dict[str, dict] = {}
            for snap in sentiment.snapshots:
                per_platform[snap.platform] = {
                    "mention_count":   snap.mention_count,
                    "positive_score":  snap.positive_score,
                    "negative_score":  snap.negative_score,
                }
            social_data[ticker] = per_platform
        else:
            social_data[ticker] = {}

    state["social_data"] = social_data

    # Return None so the agent body (_run_async_impl) continues normally.
    return None

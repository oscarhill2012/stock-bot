"""News analyst data fetch callback.

Fetches news headlines for every watchlist ticker before the LLM runs.
Narrowed from the old sentiment_fetch_callback to ``news/`` only — the
``social_sentiment/`` branch is removed here and migrates to the new
Social analyst (Task 7).

Phase 5 (Task 11) adds a second state write: ``state["news_context"]``, a
human-readable multi-ticker text block that the News LLM instruction template
references as the ``{news_context}`` ADK runtime placeholder.  This mirrors
the ``fundamental_context`` pattern introduced in Task 10 — keeping the
machine-readable raw dict (``news_data``) separate from the LLM-readable
formatted text (``news_context``) so the prompt stays compact.
"""
from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_stock_news

logger = logging.getLogger(__name__)

# Maximum number of headlines to include per ticker in the LLM context block.
# Caps token usage while keeping the most recent signal visible.
_MAX_HEADLINES = 10

# Maximum character length for an article summary excerpt shown to the LLM.
_MAX_SUMMARY_CHARS = 300


def _build_ticker_news_context(ticker: str, articles: list) -> str:
    """Build the LLM-readable context block for a single ticker's news.

    Formats headlines and article summaries into a text block suitable for
    direct inclusion in an LLM prompt.  Only the most recent
    ``_MAX_HEADLINES`` articles are included; summaries are truncated to
    ``_MAX_SUMMARY_CHARS`` characters to control token usage.

    Parameters
    ----------
    ticker:
        Ticker symbol label.
    articles:
        List of article dicts (serialised ``NewsArticle`` instances) or raw
        dict-like objects from the provider.

    Returns
    -------
    str
        A formatted text block ready for concatenation into ``news_context``.
    """
    lines: list[str] = [f"=== {ticker} ==="]

    if not articles:
        lines.append("  (no news available)")
        return "\n".join(lines)

    # Limit to the most recent N articles.
    recent = articles[:_MAX_HEADLINES]

    for i, article in enumerate(recent, start=1):
        # Support both dict access and attribute access depending on how the
        # provider serialised the NewsArticle.
        if isinstance(article, dict):
            headline  = article.get("title") or article.get("headline") or "(no title)"
            summary   = (article.get("summary") or "").strip()
            published = article.get("published_at") or article.get("date") or ""
        else:
            headline  = getattr(article, "title", None) or getattr(article, "headline", "(no title)")
            summary   = (getattr(article, "summary", None) or "").strip()
            published = getattr(article, "published_at", None) or getattr(article, "date", "") or ""

        date_str = f" [{published}]" if published else ""
        lines.append(f"  [{i}]{date_str} {headline}")

        if summary:
            # Truncate to avoid token bloat while preserving the key content.
            lines.append(f"       {summary[:_MAX_SUMMARY_CHARS]}")

    return "\n".join(lines)


async def news_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch news headlines for every watchlist ticker before the LLM runs.

    Reads ``state["tickers"]`` and calls the news provider for each ticker.
    Writes two state keys:

    - ``state["news_data"]`` — machine-readable dict keyed by ticker, each
      value containing a ``"news"`` list of serialised ``NewsArticle`` dicts.
      Consumed by the feature extractor after-callback.
    - ``state["news_context"]`` — human-readable multi-ticker text block that
      ADK's instruction template fills into the LLM prompt via the
      ``{news_context}`` placeholder each tick.

    The social_sentiment branch has been removed — that data now belongs to
    the Social analyst (Task 7).

    Args:
        callback_context: ADK callback context carrying the mutable pipeline state.

    Returns:
        None — this callback never short-circuits the agent run.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    news_data: dict[str, dict] = {}
    context_blocks: list[str] = []

    for ticker in tickers:
        try:
            news = await get_stock_news(ticker)
        except Exception as exc:
            logger.warning("news fetch failed for %s: %s", ticker, exc)
            news = []

        # Serialise to dicts for the machine-readable store.
        serialised = [
            a.model_dump() if hasattr(a, "model_dump") else a for a in news
        ]

        news_data[ticker] = {"news": serialised}

        # Build the LLM-readable context block for this ticker and accumulate.
        context_blocks.append(_build_ticker_news_context(ticker, serialised))

    state["news_data"] = news_data

    # Join all per-ticker blocks into one string for the {news_context} ADK
    # instruction placeholder — mirrors the fundamental_context pattern.
    state["news_context"] = "\n\n".join(context_blocks) if context_blocks else "(no news data)"

    return None

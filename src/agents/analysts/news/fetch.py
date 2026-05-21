"""News analyst fetch helpers.

Provides the per-ticker formatting helpers used by ``NewsFetchAgent``
(``agents.analysts.news.fetch_agent``).

The legacy ``news_fetch_callback`` (an ADK ``before_agent_callback``) was
retired in Phase 9 when the per-ticker fan-out design replaced the batched
``NewsAnalyst`` LlmAgent.  Only the formatting helpers remain here so that
``NewsFetchAgent`` can reuse the article-truncation and context-block logic
without duplicating it.
"""
from __future__ import annotations

import logging

from config.analysts import NewsCaps, get_analysts_config

_logger = logging.getLogger(__name__)


def _caps() -> NewsCaps:
    """Return the ``NewsCaps`` section from the analysts config.

    Reads caps lazily on first call — avoids running the config loader at
    module import time, which simplifies test isolation.

    Returns
    -------
    NewsCaps
        Validated caps object containing ``max_articles_per_ticker`` and
        ``max_summary_chars`` as configured in ``config/analysts.json``.
    """
    return get_analysts_config().news


def _build_ticker_news_context(ticker: str, articles: list) -> str:
    """Build the LLM-readable context block for a single ticker's news.

    Formats headlines and article summaries into a text block suitable for
    direct inclusion in an LLM prompt.  Only the most recent
    ``max_articles`` articles are included; summaries are truncated to
    ``max_summary_chars`` characters to control token usage. Both caps are
    read from ``config/analysts.json`` via ``_caps()``.

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

    # Read caps from config — done once per call, not per article.
    caps = _caps()

    # Limit to the most recent N articles.
    recent = articles[:caps.max_articles_per_ticker]

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
            lines.append(f"       {summary[:caps.max_summary_chars]}")

    return "\n".join(lines)

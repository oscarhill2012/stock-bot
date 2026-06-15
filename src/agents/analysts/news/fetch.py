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
from datetime import UTC, datetime

from config.analysts import NewsCaps, get_analysts_config

_logger = logging.getLogger(__name__)

# Sentinel value written by providers when no timestamp is available.
_MISSING_SENTINEL = "MISSING_TIMESTAMP"


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


def _parse_published(raw: str | datetime | None) -> datetime | None:
    """Attempt to parse an article's ``published_at`` value into a naive UTC datetime.

    Accepts ISO-format strings (with or without a ``Z`` suffix, with or
    without timezone info), bare ``datetime`` objects (naive or aware), and
    the sentinel value ``"MISSING_TIMESTAMP"``.  Returns ``None`` if the
    value is absent, empty, the sentinel, or unparseable — callers should
    treat ``None`` as *age unknown*.

    Coerces all parsed values to **naive UTC** so callers can safely
    subtract a similarly-coerced ``as_of`` without risking a
    timezone-subtraction ``TypeError``.

    Parameters
    ----------
    raw:
        The raw ``published_at`` field from the article dict/model.

    Returns
    -------
    datetime | None
        A naive UTC ``datetime``, or ``None`` if parsing failed.
    """
    if not raw or raw == _MISSING_SENTINEL:
        return None

    # Already a datetime — strip timezone info if present to get naive UTC.
    if isinstance(raw, datetime):
        if raw.tzinfo is not None:
            return raw.astimezone(UTC).replace(tzinfo=None)
        return raw

    # String path: normalise the "Z" Zulu suffix that fromisoformat rejects
    # on Python < 3.11, then attempt a parse.
    normalised = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalised)
    except (ValueError, TypeError):
        _logger.debug("Unparseable published_at value: %r", raw)
        return None

    # Strip timezone to produce a naive UTC value.
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _build_ticker_news_context(ticker: str, articles: list, *, as_of: datetime) -> str:
    """Build the LLM-readable context block for a single ticker's news.

    Formats headlines and article summaries into a text block suitable for
    direct inclusion in an LLM prompt.  Only the most recent
    ``max_articles`` articles are included; summaries are truncated to
    ``max_summary_chars`` characters to control token usage. Both caps are
    read from ``config/analysts.json`` via ``_caps()``.

    An ``As of:`` anchor line is rendered immediately after the ticker
    header so the LLM knows the exact reference date.  Each article shows
    its publication date *and* its age in whole days relative to ``as_of``
    (e.g. ``[2025-10-10, 5d ago]``), enabling the model to reason about
    whether a story is still fresh or has likely been priced in already.

    Parameters
    ----------
    ticker:
        Ticker symbol label.
    articles:
        List of article dicts (serialised ``NewsArticle`` instances) or raw
        dict-like objects from the provider.
    as_of:
        The historical clock value for this tick.  Used as the reference
        point for computing per-article age.  May be tz-aware or naive;
        the helper normalises internally.

    Returns
    -------
    str
        A formatted text block ready for concatenation into ``news_context``.
    """
    # Normalise as_of to naive UTC for consistent age arithmetic.
    as_of_naive = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo is not None else as_of

    lines: list[str] = [f"=== {ticker} ==="]

    # Render the reference anchor so the LLM knows what "now" is.
    lines.append(f"  As of: {as_of_naive.date().isoformat()}")

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

        # Attempt to compute article age relative to the as_of reference point.
        published_dt = _parse_published(published)

        if published_dt is not None:
            # Clamp negative ages (dirty future-dated data) to 0 — all
            # fetched articles are PIT-correct (<= as_of), so negatives
            # only arise from data hygiene issues.
            age_days  = max(0, (as_of_naive - published_dt).days)
            age_str   = f"{age_days}d ago"
            date_part = str(published)[:10]  # ISO date prefix (YYYY-MM-DD)
            date_str  = f" [{date_part}, {age_str}]"
        elif published:
            # We have a raw value but could not parse it — show it as-is
            # so the LLM still sees some date information.
            date_str = f" [{published}, age unknown]"
        else:
            # No publication date at all.
            date_str = " [age unknown]"

        lines.append(f"  [{i}]{date_str} {headline}")

        if summary:
            # Truncate to avoid token bloat while preserving the key content.
            lines.append(f"       {summary[:caps.max_summary_chars]}")

    return "\n".join(lines)

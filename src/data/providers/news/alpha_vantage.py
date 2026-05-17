"""Alpha Vantage ``NEWS_SENTIMENT`` provider — backtest cache-fill only.

**Backtest context**
This provider is the designated *backtest cache-fill* source for news data.
Alpha Vantage has a verified historical archive going back to at least 2023
(confirmed in Phase 0 preflight notes A6 — 21 / 25 / 9 articles per week
across Jan / Jun / Dec 2023 SVB-era windows, no rate-limit messaging).

**Free-tier budget**
The AV free tier allows 25 requests/day.  A 50-ticker watchlist therefore
requires ≈ 2 calendar days of staggered fill per ticker-day of history (run
overnight in batches of ~25 tickers/day).  This is acceptable for a one-shot
fill operation; once cached, backtest replay makes no further AV calls.

**Live provider**
The live runtime news provider is NOT selected in v1.  Per the project's
"provider switching must be one config flip" rule, swapping to a paid-tier
provider (e.g. AV Premium, Finnhub, Polygon.io) is a single edit in
``config/data.json`` — ``news: "alpha_vantage"`` → ``news: "<paid-provider>"``.
No code change is needed.

**Fields populated**
- ``NewsArticle.sentiment``  ← ``overall_sentiment_score``  (per-article, [-1, 1])
- ``NewsArticle.relevance``  ← ``ticker_sentiment[].relevance_score`` for the
  requested ticker ([0, 1]); ``None`` if the ticker is absent from the list.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from data.models.news import NewsArticle
from data.registry import register
from data.secrets import require_key

# Alpha Vantage REST base URL — all queries go through the single /query endpoint.
_BASE = "https://www.alphavantage.co/query"

# Hard limit on articles returned per request (AV maximum is 1000; 200 is a
# safe default that keeps response sizes manageable without losing coverage).
_DEFAULT_ARTICLE_LIMIT = 200


def _parse_ts(s: str) -> datetime:
    """Parse an Alpha Vantage timestamp string into a UTC-aware datetime.

    Alpha Vantage uses ``"YYYYMMDDTHHMMSS"`` (UTC, no zone suffix).

    Parameters
    ----------
    s:
        Raw timestamp string from the AV ``feed[]`` array.

    Returns
    -------
    datetime
        UTC-aware datetime object.
    """
    return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


@register(
    domain="news",
    name="alpha_vantage",
    upstream="alpha_vantage",
    rate_per_minute=5,
    burst=2,
    # AV free tier is 25 req/day (≈ 1 req/min averaged over a working day).
    # Conservative 5/min gives headroom for brief bursts while keeping the
    # daily budget intact when multiple provider domains share the upstream.
)
async def fetch(
    ticker: str,
    *,
    as_of: date,
    lookback_days: int = 7,
    **_: Any,
) -> list[NewsArticle]:
    """Return news articles for ``ticker`` from Alpha Vantage ``NEWS_SENTIMENT``.

    Queries a ``[as_of - lookback_days, as_of]`` window and maps the raw feed
    into ``NewsArticle`` objects with ``sentiment`` and ``relevance`` populated.

    Point-in-time correctness: ``time_to`` is set to ``as_of T23:59``, so no
    articles published after ``as_of`` are included.

    Parameters
    ----------
    ticker:
        Stock symbol (e.g. ``"AAPL"``).  Case-insensitive — normalised to
        upper-case before the API call.
    as_of:
        The simulation / backtest date.  Used as the upper bound of the query
        window.
    lookback_days:
        Number of calendar days to look back from ``as_of``.  Defaults to 7.
    **_:
        Absorbs extra keyword arguments forwarded by ``dispatch`` so callers
        do not need to filter kwargs before calling this function.

    Returns
    -------
    list[NewsArticle]
        Articles in the query window, ordered as returned by AV (newest-first
        by default).  Returns an empty list if the API key is absent or the
        response feed is empty.

    Raises
    ------
    data.secrets.SecretMissingError
        If ``ALPHA_VANTAGE_API_KEY`` is not set in the environment.
    httpx.HTTPStatusError
        On a non-2xx HTTP response from Alpha Vantage.
    """
    symbol = ticker.upper()

    # Retrieve the key at call time so the module can be imported without a
    # configured .env (import-time failures would break the registry).
    api_key = require_key("ALPHA_VANTAGE_API_KEY")

    # Build the time window.
    start = as_of - timedelta(days=lookback_days)

    params = {
        "function": "NEWS_SENTIMENT",
        "tickers":  symbol,
        "time_from": start.strftime("%Y%m%dT0000"),
        "time_to":   as_of.strftime("%Y%m%dT2359"),
        "limit":     _DEFAULT_ARTICLE_LIMIT,
        "apikey":    api_key,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(_BASE, params=params)
        resp.raise_for_status()
        payload: dict = resp.json() or {}

    articles: list[NewsArticle] = []

    for row in payload.get("feed") or []:
        # ── Per-ticker relevance ─────────────────────────────────────────────
        # AV returns a ``ticker_sentiment`` list with one entry per symbol
        # mentioned in the article.  Extract the relevance score for the
        # requested ticker; default to None if the ticker is absent.
        relevance: float | None = None

        for ts in row.get("ticker_sentiment") or []:
            if ts.get("ticker") == symbol:
                try:
                    relevance = float(ts["relevance_score"])
                except (KeyError, TypeError, ValueError):
                    relevance = None
                break

        # ── Sentiment ────────────────────────────────────────────────────────
        # ``overall_sentiment_score`` is a float in [-1, 1] at article level.
        # It may be absent for older feed items — treat missing as None.
        sentiment: float | None = None
        raw_sentiment = row.get("overall_sentiment_score")
        if raw_sentiment is not None:
            try:
                sentiment = float(raw_sentiment)
            except (TypeError, ValueError):
                sentiment = None

        articles.append(NewsArticle(
            ticker=symbol,
            headline=row.get("title") or "",
            summary=row.get("summary") or "",
            url=row.get("url") or "",
            source=row.get("source") or "alpha_vantage",
            published_at=_parse_ts(row["time_published"]),
            sentiment=sentiment,
            relevance=relevance,
        ))

    return articles

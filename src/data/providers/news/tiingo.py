"""Tiingo News provider — historical news for backfill (free tier, 1000/day per ticker).

Endpoint:
    https://api.tiingo.com/tiingo/news?tickers=AAPL&startDate=2023-03-01&endDate=2023-03-15&token=...

Returns up to 1000 articles per call.  We pass ``startDate``/``endDate`` from
``from_date``/``to_date`` so backfill receives PIT-correct news.  Live callers
that omit those defaults to ``(as_of - 7d, as_of.date())`` via the wrapper.

Soft-fails to ``[]`` when ``TIINGO_API_KEY`` is unset so the live pipeline can
fall back to another news provider via config.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime
from typing import Any

import requests

from data.registry import register
from data.retry import with_retry

from ...models import NewsArticle

logger = logging.getLogger(__name__)

_BASE_URL     = "https://api.tiingo.com/tiingo/news"
_HTTP_TIMEOUT = 15.0
_PAGE_LIMIT   = 1000  # free-tier per-call cap


def _parse_published(raw: Any) -> datetime:
    """Coerce Tiingo's ISO ``publishedDate`` into a timezone-aware ``datetime``.

    Parameters
    ----------
    raw:
        The raw ``publishedDate`` value from the Tiingo JSON row.

    Returns
    -------
    datetime
        A timezone-aware datetime; returns ``MISSING_TIMESTAMP`` when the
        value is missing or unparseable so the cache writer can skip the
        row deliberately rather than fabricating wall-clock substitution.
    """
    from data.models.missing import MISSING_TIMESTAMP

    if raw is None:
        return MISSING_TIMESTAMP

    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return MISSING_TIMESTAMP

    # Ensure the datetime is always timezone-aware.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt


@with_retry
def _fetch_news(symbol: str, start: str, end: str, api_key: str, limit: int) -> list[dict]:
    """Hit the Tiingo News endpoint and return raw JSON rows.

    Parameters
    ----------
    symbol:
        Uppercase ticker symbol (e.g. ``"AAPL"``).
    start:
        ISO date string for ``startDate`` (e.g. ``"2023-03-01"``).
    end:
        ISO date string for ``endDate`` (e.g. ``"2023-03-15"``).
    api_key:
        Tiingo API token.
    limit:
        Maximum number of articles to request.

    Returns
    -------
    list[dict]
        Raw JSON rows from the Tiingo response; empty list on empty response.
    """
    params = {
        "tickers":   symbol,
        "startDate": start,
        "endDate":   end,
        "token":     api_key,
        "limit":     limit,
    }

    resp = requests.get(_BASE_URL, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()

    # Empty body (e.g. 204 No Content) → treat as no articles.
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


@register(
    domain="news",
    name="tiingo",
    upstream="tiingo",
    rate_per_minute=60,
    burst=20,
)
async def fetch(
    ticker: str,
    *,
    from_date: date,
    to_date: date,
    as_of: datetime,
    limit: int | None = 50,
    **_unused,
) -> list[NewsArticle]:
    """News articles for ``ticker`` published in ``[from_date, to_date]``.

    Tiingo applies the date filter server-side so we only need to project
    each row into a ``NewsArticle``.  Returns ``[]`` on missing API key.

    Parameters
    ----------
    ticker:
        Stock symbol to query (case-insensitive; normalised to uppercase).
    from_date:
        Inclusive start of the date range (maps to Tiingo's ``startDate``).
    to_date:
        Inclusive end of the date range (maps to Tiingo's ``endDate``).
    as_of:
        Point-in-time reference used by the backtest harness for cache keying.
        Not forwarded to the API — Tiingo's date-range filter handles PIT.
    limit:
        Cap on the number of articles returned.  ``None`` uses ``_PAGE_LIMIT``.
    **_unused:
        Absorbed for registry signature parity with other domain providers.

    Returns
    -------
    list[NewsArticle]
        Parsed articles, or ``[]`` if ``TIINGO_API_KEY`` is unset.
    """
    api_key = os.getenv("TIINGO_API_KEY")
    if not api_key:
        logger.debug("TIINGO_API_KEY unset — fetch returning []")
        return []

    symbol     = ticker.upper()
    page_limit = limit or _PAGE_LIMIT

    rows = await asyncio.to_thread(
        _fetch_news,
        symbol,
        from_date.isoformat(),
        to_date.isoformat(),
        api_key,
        page_limit,
    )

    out: list[NewsArticle] = []
    for row in rows:
        out.append(
            NewsArticle(
                ticker=symbol,
                headline=row.get("title", "") or "",
                summary=row.get("description", "") or "",
                url=row.get("url", "") or "",
                source=row.get("source", "") or "",
                published_at=_parse_published(row.get("publishedDate")),
                sentiment=None,
            )
        )

    return out

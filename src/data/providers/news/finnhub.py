"""Finnhub news provider — `company_news` endpoint (rate-limited via registry)."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import finnhub

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import NewsArticle


def _client() -> finnhub.Client:
    return finnhub.Client(api_key=require_key("FINNHUB_API_KEY"))


@with_retry
def _fetch_company_news(symbol: str, from_iso: str, to_iso: str) -> list[dict]:
    return _client().company_news(symbol, _from=from_iso, to=to_iso) or []


@register(
    domain="news",
    name="finnhub",
    upstream="finnhub",
    rate_per_minute=60,
    burst=30,
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
    """Recent news articles for ``ticker`` from Finnhub's ``company_news`` endpoint.

    ``as_of`` is accepted for signature parity with other domains' providers but
    Finnhub already filters by ``from_date``/``to_date`` so no additional logic
    is needed.
    """
    symbol = ticker.upper()
    raw    = await asyncio.to_thread(
        _fetch_company_news, symbol, from_date.isoformat(), to_date.isoformat()
    )
    if not raw:
        return []

    raw.sort(key=lambda a: a.get("datetime", 0), reverse=True)
    if limit is not None:
        raw = raw[:limit]

    articles: list[NewsArticle] = []
    for item in raw:
        ts = item.get("datetime")
        published = (
            datetime.fromtimestamp(ts, tz=UTC)
            if isinstance(ts, (int, float)) and ts > 0
            else datetime.now(UTC)
        )
        articles.append(
            NewsArticle(
                ticker=symbol,
                headline=item.get("headline", "") or "",
                summary=item.get("summary", "") or "",
                url=item.get("url", "") or "",
                source=item.get("source", "") or "",
                published_at=published,
                sentiment=None,
            )
        )
    return articles

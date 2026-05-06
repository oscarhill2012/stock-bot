"""`get_stock_news` — Finnhub `company_news` endpoint (async, rate-limited).

Awaits a token from the shared FINNHUB limiter (60/min, 30 burst) before
issuing the upstream call. The underlying `finnhub-python` client is
sync, so we run it in a thread via `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Optional

import finnhub

from ..models import NewsArticle
from ..rate_limit import FINNHUB
from ..retry import with_retry
from ..settings import get_settings, require


def _client() -> finnhub.Client:
    s = get_settings()
    api_key = require("FINNHUB_API_KEY", s.finnhub_api_key, "get_stock_news")
    return finnhub.Client(api_key=api_key)


@with_retry
def _fetch_company_news(symbol: str, from_iso: str, to_iso: str) -> list[dict]:
    return _client().company_news(symbol, _from=from_iso, to=to_iso) or []


async def get_stock_news(
    ticker: str,
    from_date: date,
    to_date: date,
    *,
    limit: Optional[int] = 50,
) -> list[NewsArticle]:
    symbol = ticker.upper()
    await FINNHUB.acquire()
    raw = await asyncio.to_thread(
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
            datetime.fromtimestamp(ts, tz=timezone.utc)
            if isinstance(ts, (int, float)) and ts > 0
            else datetime.now(timezone.utc)
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

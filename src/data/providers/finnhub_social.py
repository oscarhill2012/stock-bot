"""`get_social_sentiment` — Finnhub Reddit + Twitter aggregate (async, rate-limited)."""
from __future__ import annotations

import asyncio
from typing import Any

import finnhub

from ..models import SocialSentiment, SocialSentimentSnapshot
from ..rate_limit import FINNHUB
from ..retry import with_retry
from ..settings import get_settings, require


def _client() -> finnhub.Client:
    s = get_settings()
    api_key = require("FINNHUB_API_KEY", s.finnhub_api_key, "get_social_sentiment")
    return finnhub.Client(api_key=api_key)


@with_retry
def _fetch_social(symbol: str) -> dict:
    return _client().stock_social_sentiment(symbol) or {}


def _summarise(rows: list[dict[str, Any]], platform: str) -> SocialSentimentSnapshot:
    if not rows:
        return SocialSentimentSnapshot(platform=platform)  # type: ignore[arg-type]

    mentions = 0
    pos = 0.0
    neg = 0.0
    for r in rows:
        mentions += int(r.get("mention", 0) or 0)
        pos += float(r.get("positiveScore", 0) or 0)
        neg += float(r.get("negativeScore", 0) or 0)

    n = max(len(rows), 1)
    avg_pos = pos / n
    avg_neg = neg / n
    return SocialSentimentSnapshot(
        platform=platform,  # type: ignore[arg-type]
        mention_count=mentions,
        positive_score=avg_pos,
        negative_score=avg_neg,
        score=avg_pos - avg_neg,
    )


async def get_social_sentiment(ticker: str) -> SocialSentiment:
    symbol = ticker.upper()
    await FINNHUB.acquire()
    payload = await asyncio.to_thread(_fetch_social, symbol)

    snapshots = [
        _summarise(payload.get("reddit") or [], "reddit"),
        _summarise(payload.get("twitter") or [], "twitter"),
    ]

    total_mentions = sum(s.mention_count for s in snapshots) or 1
    aggregate = sum(s.score * max(s.mention_count, 1) for s in snapshots) / total_mentions

    return SocialSentiment(
        ticker=symbol,
        snapshots=snapshots,
        aggregate_score=aggregate,
    )

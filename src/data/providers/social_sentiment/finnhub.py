"""Finnhub social-sentiment provider (rate-limited via registry)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import finnhub

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import SocialSentiment, SocialSentimentSnapshot

logger = logging.getLogger(__name__)


class PremiumGatedError(RuntimeError):
    """Raised when Finnhub returns a 403 on the premium-only social
    sentiment endpoint.

    Distinct from arbitrary API errors so consumers may choose to soft-fail
    this specific case without masking real auth/rate-limit/server failures.
    """


def _client() -> finnhub.Client:
    return finnhub.Client(api_key=require_key("FINNHUB_API_KEY"))


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


@register(
    domain="social_sentiment",
    name="finnhub",
    upstream="finnhub",
    # Must match every other ``upstream="finnhub"`` declaration — the
    # registry enforces a single (rate, burst) per upstream.  See
    # news/finnhub.py for the rationale behind 50/10.
    rate_per_minute=50,
    burst=10,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    **_unused,
) -> SocialSentiment:
    """Reddit/Twitter sentiment snapshot for ``ticker`` from Finnhub.

    ``as_of`` is accepted for dispatch parity.  Finnhub's social sentiment
    endpoint is premium-only and soft-fails on the free tier; ``as_of`` is
    not used by the current implementation.
    """
    symbol = ticker.upper()

    try:
        payload = await asyncio.to_thread(_fetch_social, symbol)
    except finnhub.FinnhubAPIException as exc:
        # The premium-only endpoint returns 403 on the free tier.  Promote
        # exactly that condition to a typed PremiumGatedError; every other API
        # error (auth, 429, 5xx) raises through so the operator notices instead
        # of silently receiving an empty SocialSentiment.
        if getattr(exc, "status_code", None) == 403:
            raise PremiumGatedError(
                f"social_sentiment/finnhub: premium-gated for {symbol} ({exc})"
            ) from exc
        raise

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

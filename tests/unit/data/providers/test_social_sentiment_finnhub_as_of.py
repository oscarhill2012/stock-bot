"""``social_sentiment/finnhub.fetch`` accepts ``as_of`` without using it."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_accepts_as_of_kwarg() -> None:
    """``fetch`` must accept ``as_of`` and any extra kwargs from dispatch."""
    import data.providers.social_sentiment.finnhub as mod

    # No FINNHUB_API_KEY assumed — provider soft-fails to empty SocialSentiment.
    result = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=14,  # type: ignore[call-arg]
    )

    # Soft-fail contract — provider returns a non-exception value.
    assert result is not None

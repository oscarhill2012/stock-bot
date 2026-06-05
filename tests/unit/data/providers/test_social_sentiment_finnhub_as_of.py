"""``social_sentiment/finnhub.fetch`` accepts ``as_of`` without using it."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_fetch_accepts_as_of_kwarg() -> None:
    """``fetch`` must accept ``as_of`` and any extra kwargs from dispatch."""
    import data.providers.social_sentiment.finnhub as mod

    # Patch the upstream call so the test is deterministic and offline — a
    # successful empty payload exercises the kwarg-acceptance contract without
    # depending on a live key or the premium-gate 403 path.
    with patch.object(mod, "_fetch_social", return_value={}):
        result = await mod.fetch(
            "AAPL",
            as_of=datetime(2023, 3, 15, tzinfo=UTC),
            lookback_days=14,  # type: ignore[call-arg]
        )

    assert result is not None

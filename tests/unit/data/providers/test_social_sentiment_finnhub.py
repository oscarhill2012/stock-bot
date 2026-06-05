"""Boundary tests for the Finnhub social-sentiment provider.

Verifies the provider raises loudly on auth/rate-limit/server errors
rather than returning a synthetic-empty SocialSentiment that downstream
code cannot distinguish from "no mentions".
"""
from unittest.mock import MagicMock, patch

import finnhub
import pytest

from data.providers.social_sentiment import finnhub as provider


def _make_finnhub_exc(status_code: int, message: str) -> finnhub.FinnhubAPIException:
    """Build a ``FinnhubAPIException`` from raw primitives.

    The Finnhub library constructs exceptions from a response object (not a
    string), so we mock the minimal response interface it expects.

    Args:
        status_code: HTTP status code to embed in the exception.
        message:     Error body text to embed (placed under the ``"error"`` key).

    Returns:
        A ``FinnhubAPIException`` instance with the given code and message.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"error": message}
    return finnhub.FinnhubAPIException(mock_resp)


@pytest.mark.asyncio
async def test_fetch_raises_on_non_premium_api_exception():
    """A 429 / 500 / auth error must raise, not return empty."""
    err = _make_finnhub_exc(429, "rate limited")
    with (
        patch.object(provider, "_fetch_social", side_effect=err),
        pytest.raises(finnhub.FinnhubAPIException),
    ):
        await provider.fetch("AAPL", as_of=None)


@pytest.mark.asyncio
async def test_fetch_raises_premium_gated_on_403():
    """A 403 (free-tier premium gate) raises a typed PremiumGatedError so
    callers may choose to soft-fail explicitly.  No silent empty fallback.
    """
    err = _make_finnhub_exc(
        403, "API limit reached. Please use a higher rate limit"
    )
    with (
        patch.object(provider, "_fetch_social", side_effect=err),
        pytest.raises(provider.PremiumGatedError),
    ):
        await provider.fetch("AAPL", as_of=None)

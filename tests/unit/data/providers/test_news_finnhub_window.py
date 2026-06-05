"""News-provider boundary tests — reversed windows must raise, not return []."""
from datetime import UTC, datetime

import pytest

from data.providers.news import alpha_vantage as av_provider
from data.providers.news import finnhub as provider


@pytest.mark.asyncio
async def test_fetch_raises_on_reversed_window():
    """from_date > to_date is a caller bug; silently returning [] hides it
    until a backtest produces an inexplicably empty newsfeed.
    """
    as_of = datetime(2026, 3, 15, tzinfo=UTC)
    with pytest.raises(ValueError, match="reversed news window"):
        await provider.fetch(
            "AAPL",
            from_date = datetime(2026, 3, 10, tzinfo=UTC),
            to_date   = datetime(2026, 3, 5,  tzinfo=UTC),   # before from
            as_of     = as_of,
        )


@pytest.mark.asyncio
async def test_alpha_vantage_fetch_raises_on_reversed_window(monkeypatch):
    """Same loud-fail contract for the Alpha Vantage news provider.

    ``require_key`` is stubbed so this test does not depend on
    ``ALPHA_VANTAGE_API_KEY`` being present in the environment.  The
    reversed-window check fires before any HTTP call, so no network stub
    is needed — the stub just prevents ``SecretMissingError`` from
    shadowing the ``ValueError`` we are testing.
    """
    monkeypatch.setattr(av_provider, "require_key", lambda _: "test-token")

    as_of = datetime(2026, 3, 15, tzinfo=UTC)
    with pytest.raises(ValueError, match="reversed news window"):
        await av_provider.fetch(
            "AAPL",
            from_date = datetime(2026, 3, 10, tzinfo=UTC),
            to_date   = datetime(2026, 3, 5,  tzinfo=UTC),
            as_of     = as_of,
        )

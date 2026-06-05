"""Verify providers raise SecretMissingError when their API key is unset.

Previously these providers returned [] on missing key, which is
indistinguishable downstream from "no data" and hid mis-configuration.
"""
from datetime import UTC, datetime

import pytest

from data.secrets import SecretMissingError


@pytest.mark.asyncio
async def test_tiingo_news_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    from data.providers.news import tiingo
    with pytest.raises(SecretMissingError, match="TIINGO_API_KEY"):
        await tiingo.fetch(
            "AAPL",
            from_date = datetime(2026, 3, 1,  tzinfo=UTC),
            to_date   = datetime(2026, 3, 10, tzinfo=UTC),
            as_of     = datetime(2026, 3, 10, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_quiver_politician_trades_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("QUIVER_QUANT_API_KEY", raising=False)
    from data.providers.politician_trades import quiver
    with pytest.raises(SecretMissingError, match="QUIVER_QUANT_API_KEY"):
        await quiver.fetch(
            "AAPL",
            as_of         = datetime(2026, 3, 10, tzinfo=UTC),
            lookback_days = 30,
        )


@pytest.mark.asyncio
async def test_fmp_politician_trades_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    from data.providers.politician_trades import fmp
    with pytest.raises(SecretMissingError, match="FMP_API_KEY"):
        await fmp.fetch(
            "AAPL",
            as_of         = datetime(2026, 3, 10, tzinfo=UTC),
            lookback_days = 30,
        )

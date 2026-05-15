"""``notable_holders/edgar.fetch`` honours ``as_of`` for the filing window."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_filing_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` derives the filing-date window from ``as_of``."""
    import data.providers.notable_holders.edgar as mod

    captured: dict = {}

    def fake_list(symbol: str, lookback_days: int, limit: int, as_of: datetime) -> list:
        captured["symbol"]        = symbol
        captured["lookback_days"] = lookback_days
        captured["as_of"]         = as_of
        return []

    monkeypatch.setattr(mod, "_list_holder_filings", fake_list)

    await mod.fetch(
        "AAPL",
        lookback_days=180,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["as_of"] == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)
    assert captured["lookback_days"] == 180


@pytest.mark.asyncio
async def test_fetch_accepts_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``**_unused`` lets the registry dispatch any kwarg safely."""
    import data.providers.notable_holders.edgar as mod

    monkeypatch.setattr(mod, "_list_holder_filings", lambda s, lookback, lim, a: [])

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )
    assert out == []

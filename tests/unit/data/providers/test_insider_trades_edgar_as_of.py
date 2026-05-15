"""``insider_trades/edgar.fetch`` honours ``as_of`` for the date window."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_lookback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` must derive the filing-date window from ``as_of``, not wall-clock today."""
    import data.providers.insider_trades.edgar as mod

    captured: dict = {}

    def fake_list(symbol: str, lookback_days: int, as_of: datetime) -> list:
        captured["symbol"]        = symbol
        captured["lookback_days"] = lookback_days
        captured["as_of"]         = as_of
        return []

    monkeypatch.setattr(mod, "_list_form4_filings", fake_list)

    await mod.fetch(
        "AAPL",
        lookback_days=30,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["symbol"]        == "AAPL"
    assert captured["lookback_days"] == 30
    assert captured["as_of"]         == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fetch_swallows_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider must accept extra kwargs other providers care about (``**_unused``)."""
    import data.providers.insider_trades.edgar as mod

    monkeypatch.setattr(mod, "_list_form4_filings", lambda s, days, a: [])

    # ``from_date`` is meaningless to insider_trades but news providers take it —
    # the registry dispatches the same kwargs to every domain.
    result = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )

    assert result.trades == []

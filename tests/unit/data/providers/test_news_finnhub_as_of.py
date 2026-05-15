"""``news/finnhub.fetch`` accepts ``as_of`` without using it for data logic."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_accepts_as_of_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` must accept ``as_of`` even though it relies on ``from_date``/``to_date``."""
    import data.providers.news.finnhub as mod

    captured: dict = {}

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        captured["symbol"]   = symbol
        captured["from_iso"] = from_iso
        captured["to_iso"]   = to_iso
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert out == []
    assert captured["symbol"]   == "AAPL"
    assert captured["from_iso"] == "2023-03-01"
    assert captured["to_iso"]   == "2023-03-15"


@pytest.mark.asyncio
async def test_fetch_accepts_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``**_unused`` absorbs kwargs other providers consume."""
    import data.providers.news.finnhub as mod

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: [])

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )
    assert out == []

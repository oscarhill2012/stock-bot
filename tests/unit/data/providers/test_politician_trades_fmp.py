"""``politician_trades/fmp.fetch`` merges senate + house feeds with PIT cutoff."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.models import PoliticianTrade


@pytest.mark.asyncio
async def test_fmp_merges_senate_and_house(monkeypatch: pytest.MonkeyPatch) -> None:
    import data.providers.politician_trades.fmp as mod
    monkeypatch.setenv("FMP_API_KEY", "fake")

    monkeypatch.setattr(mod, "_fetch_senate", lambda symbol, key: [
        {
            "transactionDate": "2023-02-20",
            "disclosureDate":  "2023-03-05",
            "firstName":       "Nancy",
            "lastName":        "Pelosi",
            "office":          "House",
            "owner":           "self",
            "type":            "Purchase",
            "amount":          "$15,001 - $50,000",
        },
    ])
    monkeypatch.setattr(mod, "_fetch_house", lambda symbol, key: [
        {
            "transactionDate": "2023-02-25",
            "disclosureDate":  "2023-03-07",
            "firstName":       "Tommy",
            "lastName":        "Tuberville",
            "office":          "Senate",
            "owner":           "self",
            "type":            "Sale",
            "amount":          "$50,001 - $100,000",
        },
    ])

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=90,
    )

    assert len(out) == 2
    assert all(isinstance(t, PoliticianTrade) for t in out)
    sides = {t.side for t in out}
    assert sides == {"buy", "sell"}


@pytest.mark.asyncio
async def test_fmp_applies_as_of_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trades disclosed after ``as_of`` must drop (lookahead protection)."""
    import data.providers.politician_trades.fmp as mod
    monkeypatch.setenv("FMP_API_KEY", "fake")

    monkeypatch.setattr(mod, "_fetch_senate", lambda symbol, key: [
        {
            "transactionDate": "2023-04-20",
            "disclosureDate":  "2023-04-25",
            "firstName":       "Future",
            "lastName":        "Trader",
            "office":          "Senate",
            "type":            "Purchase",
            "amount":          "$1,001 - $15,000",
        },
    ])
    monkeypatch.setattr(mod, "_fetch_house", lambda symbol, key: [])

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=90,
    )
    assert out == []


def test_fmp_registers_on_import() -> None:
    import data.providers.politician_trades.fmp  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("politician_trades", "fmp")]
    assert entry.upstream == "fmp"
    assert _LIMITERS["fmp"].rate_per_minute > 0

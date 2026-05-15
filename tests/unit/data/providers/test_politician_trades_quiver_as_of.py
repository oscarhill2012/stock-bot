"""``politician_trades/quiver.fetch`` honours ``as_of`` for the cutoff."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cutoff must be ``as_of - lookback``, not ``date.today() - lookback``."""
    import data.providers.politician_trades.quiver as mod

    # Force the soft-fail path so we don't need a real API key — but we still
    # want to see fetch happily accept the as_of kwarg.
    monkeypatch.delenv("QUIVER_QUANT_API_KEY", raising=False)

    out = await mod.fetch(
        "AAPL",
        lookback_days=90,
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    # Soft-fail returns [] when the API key is missing.
    assert out == []


@pytest.mark.asyncio
async def test_fetch_applies_as_of_cutoff_to_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a payload is returned, trades older than ``as_of - lookback`` must drop."""
    import data.providers.politician_trades.quiver as mod

    monkeypatch.setenv("QUIVER_QUANT_API_KEY", "fake-key")

    # Older than 90d from 2023-03-15 → must drop.
    # Inside window → must include.
    monkeypatch.setattr(mod, "_fetch_trades", lambda symbol, key: [
        {"TransactionDate": "2022-12-01", "Representative": "Old Trader", "Transaction": "Buy"},
        {"TransactionDate": "2023-02-10", "Representative": "Recent Trader", "Transaction": "Buy"},
    ])

    out = await mod.fetch(
        "AAPL",
        lookback_days=90,
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    names = [t.politician for t in out]
    assert "Recent Trader" in names
    assert "Old Trader"    not in names

"""``filings/edgar.fetch`` filters by ``as_of`` so backfill ignores future filings."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_passes_as_of_to_lister(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_list_filings`` must receive ``as_of`` and apply it as the upper bound."""
    import data.providers.filings.edgar as mod

    captured: dict = {}

    def fake_list(symbol: str, form_types: tuple, limit: int, as_of: datetime) -> list:
        captured["symbol"]     = symbol
        captured["form_types"] = form_types
        captured["limit"]      = limit
        captured["as_of"]      = as_of
        return []

    monkeypatch.setattr(mod, "_list_filings", fake_list)

    await mod.fetch(
        "AAPL",
        form_types=("10-K", "10-Q"),
        limit=5,
        include_excerpts=False,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["as_of"]      == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)
    assert captured["form_types"] == ("10-K", "10-Q")
    assert captured["limit"]      == 5


@pytest.mark.asyncio
async def test_fetch_accepts_extra_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` should accept and silently discard unknown keyword arguments via ``**_unused``."""
    import data.providers.filings.edgar as mod

    monkeypatch.setattr(mod, "_list_filings", lambda s, ft, lim, a: [])
    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )
    assert out == []

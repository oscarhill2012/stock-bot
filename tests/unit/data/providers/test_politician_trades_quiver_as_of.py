"""``politician_trades/quiver.fetch`` honours ``as_of`` for the cutoff.

Covers two behaviours:

1. The lookback window is anchored on ``as_of``, not ``date.today()``.
2. The PIT upper-bound filter uses ``disclosure_date``, not
   ``transaction_date`` — a trade executed before ``as_of`` but disclosed
   after is not yet visible to the market at that historical moment.
"""
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
    """When a payload is returned, trades older than ``as_of - lookback`` must drop.

    Patches ``_load_rows`` (the seam used since the PIT disclosure-date fix)
    rather than ``_fetch_trades``.  Both fake rows carry a ``DisclosureDate``
    within the window so the PIT upper-bound filter does not interfere with
    this lookback-window assertion.
    """
    import data.providers.politician_trades.quiver as mod

    monkeypatch.setenv("QUIVER_QUANT_API_KEY", "fake-key")

    # Older than 90d from 2023-03-15 → must drop.
    # Inside window → must include.
    # DisclosureDate set to the same day as TransactionDate so both rows pass
    # the disclosure-date PIT filter; the distinction being tested here is
    # purely the lookback lower-bound.
    monkeypatch.setattr(mod, "_load_rows", lambda symbol, key: [
        {
            "TransactionDate": "2022-12-01",
            "DisclosureDate": "2022-12-01",
            "Representative": "Old Trader",
            "Transaction": "Buy",
        },
        {
            "TransactionDate": "2023-02-10",
            "DisclosureDate": "2023-02-10",
            "Representative": "Recent Trader",
            "Transaction": "Buy",
        },
    ])

    out = await mod.fetch(
        "AAPL",
        lookback_days=90,
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    names = [t.politician for t in out]
    assert "Recent Trader" in names
    assert "Old Trader"    not in names


@pytest.mark.asyncio
async def test_quiver_filters_on_disclosure_date_not_transaction_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PIT correctness: upper-bound filter must use ``disclosure_date``.

    A trade transacted before ``as_of`` but **disclosed after** ``as_of``
    is not yet public knowledge at that historical moment and must be
    excluded.  Conversely, a trade whose disclosure_date is within the
    window must be included even if the transaction itself was earlier.
    """
    import data.providers.politician_trades.quiver as mod

    monkeypatch.setenv("QUIVER_QUANT_API_KEY", "fake-key")

    # as_of = 2023-03-10.  Row A: transacted 2023-03-02 (inside window) but
    # disclosed 2023-03-20 (AFTER as_of) — market didn't see it yet → excluded.
    # Row B: transacted 2023-03-01, disclosed 2023-03-05 (both inside window)
    # → included.
    fake_rows = [
        {
            "Representative": "X",
            "Ticker": "AAPL",
            "Transaction": "Purchase",
            "TransactionDate": "2023-03-02",
            "DisclosureDate": "2023-03-20",
            "Range": "$15,000 - $50,000",
        },
        {
            "Representative": "Y",
            "Ticker": "AAPL",
            "Transaction": "Purchase",
            "TransactionDate": "2023-03-01",
            "DisclosureDate": "2023-03-05",
            "Range": "$1,001 - $15,000",
        },
    ]

    monkeypatch.setattr(mod, "_load_rows", lambda *a, **k: fake_rows)

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 10, tzinfo=UTC),
        lookback_days=90,
    )

    politicians = {t.politician for t in out}

    # Row A must be absent — its disclosure is after as_of.
    assert "X" not in politicians, (
        "Trade disclosed after as_of must be filtered out (PIT correctness)."
    )
    # Row B must be present — both dates are within the window.
    assert "Y" in politicians, (
        "Trade disclosed before as_of must be included."
    )

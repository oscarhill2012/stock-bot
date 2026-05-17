"""Unit tests for ``data.providers.earnings.finnhub``.

All HTTP calls are monkeypatched — no real network traffic.

The dual PIT filter tested here (date <= as_of AND epsActual is not None)
is documented in ``src/data/providers/earnings/finnhub.py`` and originates
from Phase -1 preflight verification (preflight-notes A6, 2026-05-17).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest


class _AsyncCM:
    """Minimal async context-manager that yields a stub httpx response.

    Wraps a pre-built ``MagicMock`` response so that
    ``async with httpx.AsyncClient(...) as client`` resolves to an object
    whose ``get()`` coroutine returns the stub.

    If this helper turns up in a third test file, hoist it into
    ``tests/unit/data/providers/conftest.py``.

    Parameters
    ----------
    resp:
        The ``MagicMock`` that represents the HTTP response.
    """

    def __init__(self, resp: MagicMock) -> None:
        self._resp = resp

    async def __aenter__(self) -> _AsyncCM:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, *args, **kwargs) -> MagicMock:
        """Simulate ``AsyncClient.get(...)`` returning the stub response."""
        return self._resp

    async def post(self, *args, **kwargs) -> MagicMock:
        """Simulate ``AsyncClient.post(...)`` returning the stub response."""
        return self._resp


# ---------------------------------------------------------------------------
# Helper — build a fake httpx.AsyncClient that always returns `payload`
# ---------------------------------------------------------------------------

def _make_fake_client(payload: dict) -> MagicMock:
    """Return a MagicMock response whose ``.json()`` yields ``payload``."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = payload
    fake_resp.raise_for_status = lambda: None
    return fake_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_earnings_finnhub_returns_history(monkeypatch):
    """Happy-path: a single announced report within the window is returned."""
    from data.providers.earnings import finnhub as mod

    payload = {"earningsCalendar": [
        {
            "symbol": "AAPL", "date": "2023-02-02",
            "epsActual": 1.88, "epsEstimate": 1.94,
            "revenueActual": 1.17e11, "revenueEstimate": 1.21e11,
            "quarter": 1, "year": 2023,
        },
    ]}

    # Patch the API key lookup so no real .env is needed.
    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10), lookback_quarters=4)

    assert len(out.reports) == 1
    assert out.reports[0].ticker == "AAPL"
    assert out.reports[0].fiscal_period == "Q1 2023"
    assert out.reports[0].eps_actual == 1.88


@pytest.mark.asyncio
async def test_earnings_finnhub_filters_future_reports(monkeypatch):
    """PIT filter (a): a report dated after ``as_of`` must be excluded."""
    from data.providers.earnings import finnhub as mod

    payload = {"earningsCalendar": [
        # Before as_of — keep.
        {
            "symbol": "AAPL", "date": "2023-02-02",
            "epsActual": 1.88, "epsEstimate": 1.94,
            "quarter": 1, "year": 2023,
        },
        # After as_of (2023-03-10) — drop.
        {
            "symbol": "AAPL", "date": "2023-05-04",
            "epsActual": 1.52, "epsEstimate": 1.43,
            "quarter": 2, "year": 2023,
        },
    ]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10), lookback_quarters=4)

    assert len(out.reports) == 1
    assert out.reports[0].report_date == date(2023, 2, 2)


@pytest.mark.asyncio
async def test_earnings_finnhub_filters_unannounced_rows(monkeypatch):
    """PIT filter (b): rows with epsActual=null must be excluded.

    Dual PIT filter (Phase -1 verification 2026-05-17): the Finnhub API
    returns FUTURE-dated rows with epsActual=null even when as_of is in the
    past.  The provider must drop these too — otherwise the bot would
    "know" about earnings the moment they were scheduled, not when they
    were announced.

    Real-world example: probing ``from=today, to=today+90d`` on 2026-05-17
    returned AAPL Q3 2026 dated 2026-07-29 with epsActual=null.
    """
    from data.providers.earnings import finnhub as mod

    payload = {"earningsCalendar": [
        # Already announced — keep.
        {
            "symbol": "AAPL", "date": "2023-02-02",
            "epsActual": 1.88, "epsEstimate": 1.94,
            "quarter": 1, "year": 2023,
        },
        # Scheduled but not yet announced (epsActual=null) — drop.
        {
            "symbol": "AAPL", "date": "2023-02-15",
            "epsActual": None, "epsEstimate": 1.70,
            "quarter": 1, "year": 2023,
        },
    ]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10), lookback_quarters=4)

    assert len(out.reports) == 1
    assert out.reports[0].eps_actual == 1.88


@pytest.mark.asyncio
async def test_earnings_finnhub_lookback_quarters_cap(monkeypatch):
    """``lookback_quarters`` caps the number of returned reports."""
    from data.providers.earnings import finnhub as mod

    # Three announced rows, all within as_of.
    payload = {"earningsCalendar": [
        {"symbol": "AAPL", "date": "2022-08-04", "epsActual": 1.20,
         "epsEstimate": 1.10, "quarter": 3, "year": 2022},
        {"symbol": "AAPL", "date": "2022-11-03", "epsActual": 1.30,
         "epsEstimate": 1.25, "quarter": 4, "year": 2022},
        {"symbol": "AAPL", "date": "2023-02-02", "epsActual": 1.88,
         "epsEstimate": 1.94, "quarter": 1, "year": 2023},
    ]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10), lookback_quarters=2)

    # Only the two newest should appear.
    assert len(out.reports) == 2
    assert out.reports[0].report_date == date(2023, 2, 2)
    assert out.reports[1].report_date == date(2022, 11, 3)


@pytest.mark.asyncio
async def test_earnings_finnhub_surprise_pct_computed(monkeypatch):
    """``surprise_pct`` is populated when both EPS values are non-zero."""
    from data.providers.earnings import finnhub as mod

    payload = {"earningsCalendar": [
        {"symbol": "AAPL", "date": "2023-02-02",
         "epsActual": 1.88, "epsEstimate": 1.94,
         "quarter": 1, "year": 2023},
    ]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10), lookback_quarters=4)

    report = out.reports[0]
    expected = (1.88 - 1.94) / abs(1.94) * 100.0
    assert report.surprise_pct is not None
    assert abs(report.surprise_pct - expected) < 1e-6


@pytest.mark.asyncio
async def test_earnings_finnhub_empty_calendar(monkeypatch):
    """An empty ``earningsCalendar`` list returns an empty ``EarningsHistory``."""
    from data.providers.earnings import finnhub as mod

    payload = {"earningsCalendar": []}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10), lookback_quarters=4)

    assert out.ticker == "AAPL"
    assert out.reports == []

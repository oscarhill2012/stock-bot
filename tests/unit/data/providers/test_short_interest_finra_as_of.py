"""Unit tests for ``data.providers.short_interest.finra``.

All HTTP calls are monkeypatched — no real network traffic.

Key invariants tested
---------------------
- **Synthesis correctness**: the provider sums short/total volume within each
  trade date across all marketCode venues, *then* sums across days.  Naive
  sum-across-all-rows would over-count by the venue count.  (Phase -1 finding:
  AAPL on a single date returned 3 rows for 3 venues.)
- **PIT gate**: rows with ``tradeReportDate > as_of`` are dropped before
  aggregation.
- **Soft-fail on missing credentials**: when ``_get_token`` returns ``None``
  and ``_refresh_token`` cannot obtain creds, ``fetch`` returns ``[]``.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

# ── _AsyncCM helper ───────────────────────────────────────────────────────────

class _AsyncCM:
    """Minimal async context-manager that yields a stub httpx client.

    Wraps a pre-built ``MagicMock`` response so that
    ``async with httpx.AsyncClient(...) as client`` resolves to an object
    whose ``get()`` / ``post()`` coroutines return the stub.

    If this helper appears in a third test file, hoist it into
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


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_token_resp() -> MagicMock:
    """Return a stub token response with a 12-hour lifetime."""
    resp = MagicMock()
    resp.json.return_value = {"access_token": "tok-xyz", "expires_in": 43200}
    resp.raise_for_status = lambda: None
    return resp


# ── Core synthesis test ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finra_synthesises_30d_snapshot_from_regshodaily(monkeypatch):
    """The provider must synthesise a single ShortInterestSnapshot from a
    rolling 30-day window of regShoDaily rows, summing per-day across
    venues first then across days.

    Two trade dates × 2 venues each:

    - 2023-03-08: short = 10 + 5 = 15,  total = 100 + 50 = 150
    - 2023-03-09: short = 20 + 10 = 30, total = 200 + 100 = 300

    Expected:
    - short_interest       = 15 + 30 = 45
    - average_daily_volume = (150 + 300) / 2 = 225
    - days_to_cover        = 45 / 225 = 0.20
    - settlement_date      = 2023-03-09  (max tradeReportDate)
    - source               = "finra_regsho_synthesised"
    """
    from data.providers.short_interest import finra as mod

    data_resp = MagicMock()
    data_resp.json.return_value = [
        {
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "tradeReportDate": "2023-03-08",
            "marketCode": "B",
            "shortParQuantity": 10,
            "shortExemptParQuantity": 0,
            "totalParQuantity": 100,
            "reportingFacilityCode": "NCTRF",
        },
        {
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "tradeReportDate": "2023-03-08",
            "marketCode": "Q",
            "shortParQuantity": 5,
            "shortExemptParQuantity": 0,
            "totalParQuantity": 50,
            "reportingFacilityCode": "NCTRF",
        },
        {
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "tradeReportDate": "2023-03-09",
            "marketCode": "B",
            "shortParQuantity": 20,
            "shortExemptParQuantity": 0,
            "totalParQuantity": 200,
            "reportingFacilityCode": "NCTRF",
        },
        {
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "tradeReportDate": "2023-03-09",
            "marketCode": "Q",
            "shortParQuantity": 10,
            "shortExemptParQuantity": 0,
            "totalParQuantity": 100,
            "reportingFacilityCode": "NCTRF",
        },
    ]
    data_resp.raise_for_status = lambda: None

    # Provide two AsyncClient contexts: first for the token POST, second for
    # the data GET.  The iterator is consumed by the monkeypatched AsyncClient
    # constructor in order of call.
    cm_calls = iter([_AsyncCM(_make_token_resp()), _AsyncCM(data_resp)])
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: next(cm_calls))

    # Stub require_key so _refresh_token can proceed without real creds.
    monkeypatch.setattr(mod, "require_key", lambda _key: "stub-cred")

    # Reset the token cache so _get_token() returns None and the test
    # exercises the full _refresh_token → fetch path.
    monkeypatch.setitem(mod._token_cache, "token", None)
    monkeypatch.setitem(mod._token_cache, "expires_at", 0.0)

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15), lookback_days=30)

    assert len(out) == 1
    snap = out[0]
    assert snap.ticker == "AAPL"
    assert snap.settlement_date == date(2023, 3, 9)        # max(tradeReportDate)
    assert snap.report_publish_date == date(2023, 3, 9)    # collapses to settlement
    assert snap.short_interest == 45.0                     # sum-within-day then sum-across-days
    assert snap.average_daily_volume == 225.0              # mean across days
    assert abs(snap.days_to_cover - 0.20) < 1e-6
    assert snap.source == "finra_regsho_synthesised"


# ── PIT gate test ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finra_filters_rows_after_as_of(monkeypatch):
    """tradeReportDate > as_of must be dropped before aggregation.

    One visible row (2023-03-08) and one future row (2023-03-20, after
    as_of=2023-03-15).  The 999-share row must be ignored; only the 10-share
    row contributes to the snapshot.
    """
    from data.providers.short_interest import finra as mod

    data_resp = MagicMock()
    data_resp.json.return_value = [
        # Visible — within PIT window:
        {
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "tradeReportDate": "2023-03-08",
            "marketCode": "B",
            "shortParQuantity": 10,
            "shortExemptParQuantity": 0,
            "totalParQuantity": 100,
            "reportingFacilityCode": "NCTRF",
        },
        # Future — must be dropped (tradeReportDate > as_of):
        {
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "tradeReportDate": "2023-03-20",
            "marketCode": "B",
            "shortParQuantity": 999,
            "shortExemptParQuantity": 0,
            "totalParQuantity": 9999,
            "reportingFacilityCode": "NCTRF",
        },
    ]
    data_resp.raise_for_status = lambda: None

    cm_calls = iter([_AsyncCM(_make_token_resp()), _AsyncCM(data_resp)])
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: next(cm_calls))
    monkeypatch.setattr(mod, "require_key", lambda _key: "stub-cred")

    monkeypatch.setitem(mod._token_cache, "token", None)
    monkeypatch.setitem(mod._token_cache, "expires_at", 0.0)

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15), lookback_days=30)

    assert len(out) == 1
    # Only the 10-share row is visible — the 999-share row is dropped.
    assert out[0].short_interest == 10.0


# ── Soft-fail on missing credentials ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_finra_returns_empty_when_no_credentials(monkeypatch):
    """Soft-fail when FINRA OAuth credentials are absent.

    Patching ``_get_token`` to return ``None`` forces a ``_refresh_token``
    attempt, which will fail to find ``FINRA_CLIENT_ID`` / ``FINRA_CLIENT_SECRET``
    and return ``None``.  ``fetch`` must return an empty list rather than
    raising.
    """
    from data.providers.short_interest import finra as mod

    # Replace _get_token with a sync no-op so the cache is always cold.
    monkeypatch.setattr(mod, "_get_token", lambda *_: None)

    # Replace _refresh_token with an async no-op — avoids any env dependency
    # and makes the test hermetic even if FINRA creds happen to be present.
    async def _no_refresh() -> None:
        return None

    monkeypatch.setattr(mod, "_refresh_token", _no_refresh)

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15))
    assert out == []


# ── Integration smoke (slow / network) ───────────────────────────────────────

@pytest.mark.slow
@pytest.mark.asyncio
async def test_finra_integration_real_network():
    """Live call to FINRA regShoDaily for AAPL.

    Requires ``FINRA_CLIENT_ID`` and ``FINRA_CLIENT_SECRET`` in the
    environment.  Marked ``@pytest.mark.slow`` — excluded from the default
    test run.

    Asserts the returned snapshot has plausible field values but does not
    pin exact numbers (the FINRA dataset updates daily).
    """
    from datetime import date as _date

    from data.providers.short_interest import finra as mod

    # Use a fixed historical date so the result is reproducible.
    as_of = _date(2026, 5, 7)

    out = await mod.fetch("AAPL", as_of=as_of, lookback_days=30)

    if not out:
        # No creds in this environment — skip rather than fail.
        pytest.skip("FINRA credentials not configured; skipping live integration test")

    assert len(out) == 1
    snap = out[0]
    assert snap.ticker == "AAPL"
    assert snap.short_interest > 0
    assert snap.settlement_date <= as_of
    assert snap.source == "finra_regsho_synthesised"

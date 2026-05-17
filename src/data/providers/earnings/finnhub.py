"""Finnhub earnings_calendar provider — populates ``EarningsHistory``.

Endpoint: ``GET /calendar/earnings?symbol=&from=&to=&token=``
Free tier: 60 req/min.

Point-in-time (PIT) correctness
--------------------------------
The Finnhub API has two documented but easily overlooked behaviours that
would silently contaminate a backtest with future information:

  (a) **Future-dated rows** — even when the ``to`` query parameter equals
      ``as_of``, Finnhub may return rows with ``date > as_of`` (observed
      empirically on 2026-05-17, Phase -1 preflight notes A6).

  (b) **Scheduled-but-unannounced rows** — the API returns rows for earnings
      that have been *scheduled* but not yet *reported*, identifiable by
      ``epsActual == null``.  These would let the bot "know" about earnings
      the instant they were calendared rather than the instant they were
      announced.

Both cases are eliminated by the **dual PIT filter** applied to every row:

  1. ``report_date > as_of``  → drop (future event)
  2. ``eps_actual is None``   → drop (scheduled but not yet announced)

This module is the authoritative reference for this filter.  Any caching
layer that wraps these calls must preserve the raw payload so the filter can
be re-applied at read time if needed.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from data.models.earnings import EarningsHistory, EarningsReport
from data.registry import register
from data.secrets import require_key

# Finnhub v1 REST base URL.
_BASE = "https://finnhub.io/api/v1"


@register(
    domain="earnings",
    name="finnhub",
    upstream="finnhub",
    rate_per_minute=60,
    burst=30,  # matches all other finnhub upstream declarations
)
async def fetch(
    ticker: str,
    *,
    as_of: date,
    lookback_quarters: int = 4,
    **_: Any,
) -> EarningsHistory:
    """Return up to ``lookback_quarters`` earnings reports for ``ticker``.

    All returned reports satisfy the dual PIT filter:
    ``report_date <= as_of`` AND ``eps_actual is not None``.

    Parameters
    ----------
    ticker:
        Upper-cased stock symbol (e.g. ``"AAPL"``).
    as_of:
        The simulation/backtest date.  No future data will be returned.
    lookback_quarters:
        Maximum number of quarterly reports to return, newest-first.
        Defaults to 4 (≈ one year of history).
    **_:
        Absorbs extra keyword arguments passed by ``dispatch`` (e.g.
        ``as_of_dt``, ``limit``) so callers do not need to filter kwargs
        before calling.

    Returns
    -------
    EarningsHistory
        Wrapper containing zero-or-more ``EarningsReport`` records ordered
        newest-first.  Returns an empty history if the API key is absent or
        the API returns no usable rows.
    """
    symbol = ticker.upper()

    # Retrieve the API key at call time (not import time) — absence raises
    # SecretMissingError.  Callers that want a soft-fail should catch that.
    token = require_key("FINNHUB_API_KEY")

    # Extend the query window generously: 90 days per quarter + 30 days
    # slack for reporting delays, so we don't accidentally exclude the
    # oldest quarter requested.
    start = as_of - timedelta(days=lookback_quarters * 90 + 30)

    params = {
        "symbol": symbol,
        "from":   start.isoformat(),
        "to":     as_of.isoformat(),
        "token":  token,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(f"{_BASE}/calendar/earnings", params=params)
        resp.raise_for_status()
        payload: dict = resp.json() or {}

    reports: list[EarningsReport] = []

    for row in payload.get("earningsCalendar") or []:
        rdate  = date.fromisoformat(row["date"])
        eps_a  = row.get("epsActual")

        # ── Dual PIT filter ──────────────────────────────────────────────
        # (a) Drop future events — the API ignores the `to` param reliably.
        if rdate > as_of:
            continue

        # (b) Drop scheduled-but-not-yet-announced rows.  Finnhub uses null
        #     (Python None) for epsActual when the event hasn't been reported.
        #     An empty string is treated the same way defensively.
        if eps_a in (None, ""):
            continue
        # ─────────────────────────────────────────────────────────────────

        eps_e = row.get("epsEstimate")

        # Compute EPS surprise only when both values are present and the
        # estimate is non-zero (division-by-zero guard).
        surprise: float | None = None
        if eps_e not in (None, 0, 0.0):
            surprise = (float(eps_a) - float(eps_e)) / abs(float(eps_e)) * 100.0

        reports.append(EarningsReport(
            ticker=row.get("symbol") or symbol,
            report_date=rdate,
            fiscal_period=f"Q{row.get('quarter')} {row.get('year')}",
            eps_actual=float(eps_a),
            eps_estimate=float(eps_e) if eps_e is not None else None,
            revenue_actual=row.get("revenueActual"),
            revenue_estimate=row.get("revenueEstimate"),
            surprise_pct=surprise,
        ))

    # Newest-first so callers can slice [:N] for recency without sorting.
    reports.sort(key=lambda r: r.report_date, reverse=True)

    return EarningsHistory(ticker=symbol, reports=reports[:lookback_quarters])

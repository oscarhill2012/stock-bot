"""FINRA short-interest provider — synthesised from regShoDaily.

v1 ships outcome (b) per Phase -1 verification (2026-05-17): the only live
FINRA dataset on the OAuth tier is ``regShoDaily`` (daily short SALE volume
per ticker per venue).  There is no true short-interest snapshot endpoint on
the free / OAuth tier (``shortInterestExch`` returns 404; the ``otcMarket``
metadata endpoint surfaces no sibling candidate).  Outcome (b) is therefore
promoted to the primary and only path in v1.

Synthesis
---------
The provider synthesises a single ``ShortInterestSnapshot`` from the last
``lookback_days`` (default 30) of ``regShoDaily`` rows:

  short_interest        = sum-across-days( sum-across-venues( shortParQuantity ))
  average_daily_volume  = mean-across-days( sum-across-venues( totalParQuantity ))
  days_to_cover         = short_interest / average_daily_volume
  settlement_date       = max(tradeReportDate) across all visible rows
  report_publish_date   = same as settlement_date (regShoDaily is published
                          T+1 with no biweekly lag, so the PIT gate collapses)
  source                = "finra_regsho_synthesised"  (proxy marker)

Per-date aggregation gotcha (Phase -1 finding)
----------------------------------------------
On a single trade date, ``regShoDaily`` can return **multiple rows per
ticker** (one per marketCode venue — AAPL on 2026-05-07 returned 3 rows with
different ``marketCode`` values).  Naive sum-across-rows over-counts by the
venue count.  The synthesis sums within-day first, then across days:

  short_volume_30d = Σ_d( Σ_v( shortParQuantity[d, v] ) )

PIT gate
--------
Any row with ``tradeReportDate > as_of`` is dropped before aggregation so
that no future data contaminates a backtest tick.

OAuth2
------
FINRA uses client-credentials OAuth2.  The bearer token is fetched once via
``_refresh_token`` and cached module-level for ~12 h.  ``_get_token`` is
synchronous (reads the in-process cache) so tests can monkeypatch it without
needing an async mock.  When the cache is cold or expired, ``fetch`` calls
the async ``_refresh_token`` before proceeding.

Credentials: ``FINRA_CLIENT_ID`` and ``FINRA_CLIENT_SECRET`` environment
variables, loaded via ``data.secrets.require_key``.  Missing credentials
cause ``fetch`` to return an empty list (soft-fail) rather than raising.

API notes (Phase -1 preflight A2)
----------------------------------
- Token URL: ``https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token``
  with ``?grant_type=client_credentials`` and HTTP Basic auth.
- Data URL: ``https://api.finra.org/data/group/otcMarket/name/regShoDaily``
- **Must send ``Accept: application/json``** — the default response is CSV.
- Returns a top-level JSON array (no ``{"data": [...]}`` wrapper).
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import httpx

from data.models.short_interest import ShortInterestSnapshot
from data.registry import register
from data.secrets import SecretMissingError, require_key

# ── Endpoint constants ────────────────────────────────────────────────────────

_TOKEN_URL = (
    "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"
    "?grant_type=client_credentials"
)
_DATA_URL = "https://api.finra.org/data/group/otcMarket/name/regShoDaily"

# FINRA's default response format is CSV; JSON must be requested explicitly.
_JSON_HEADERS = {"Accept": "application/json"}

# Default request timeout (seconds).
_HTTP_TIMEOUT = 15.0


# ── Module-level token cache ──────────────────────────────────────────────────

_token_cache: dict[str, Any] = {
    "token": None,
    "expires_at": 0.0,
}


def _get_token() -> str | None:
    """Return the cached bearer token if still valid, else ``None``.

    Checks the module-level token cache.  If the cached token has more than
    60 s of remaining lifetime it is returned immediately.  Otherwise returns
    ``None`` to signal that ``fetch`` should call ``_refresh_token``.

    This function is **synchronous** so tests can monkeypatch it with a plain
    ``lambda`` without needing ``AsyncMock``.  When the test patches it to
    ``lambda *_: None``, ``fetch`` falls through to ``_refresh_token``, which
    catches ``SecretMissingError`` and returns ``None`` when credentials are
    absent — resulting in ``fetch`` returning an empty list.

    Returns
    -------
    str | None
        A valid bearer token, or ``None`` if the cache is cold / expired.
    """
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]
    return None


async def _refresh_token() -> str | None:
    """Fetch a fresh OAuth2 bearer token from FINRA and update the cache.

    Uses HTTP Basic auth (client-id / client-secret) against the FINRA EWS
    token endpoint.  Caches the result module-level for the lifetime reported
    in ``expires_in`` (typically 43 200 s ≈ 12 h).

    Returns
    -------
    str | None
        The new bearer token, or ``None`` if credentials are missing.

    Raises
    ------
    httpx.HTTPStatusError
        Propagated directly if the FINRA token endpoint returns a non-2xx
        status (e.g. 401 Invalid client).
    """
    try:
        client_id = require_key("FINRA_CLIENT_ID")
        client_secret = require_key("FINRA_CLIENT_SECRET")
    except SecretMissingError:
        return None

    auth = (client_id, client_secret)
    async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT), auth=auth) as client:
        resp = await client.post(_TOKEN_URL)
        resp.raise_for_status()
        payload = resp.json()

    token = payload["access_token"]
    expires_in = float(payload.get("expires_in", 43200))

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + expires_in

    return token


def _synthesise(
    rows: list[dict],
    symbol: str,
    as_of: date,
) -> ShortInterestSnapshot | None:
    """Aggregate raw ``regShoDaily`` rows into a single synthesised snapshot.

    Applies the PIT gate (drops rows with ``tradeReportDate > as_of``), sums
    within each trade date across all ``marketCode`` venues, then aggregates
    across days.

    Parameters
    ----------
    rows:
        Raw JSON rows from the FINRA ``regShoDaily`` endpoint (top-level array).
    symbol:
        Upper-cased ticker symbol — written into the snapshot ``ticker`` field.
    as_of:
        PIT boundary.  Any row with ``tradeReportDate > as_of`` is excluded.

    Returns
    -------
    ShortInterestSnapshot | None
        The synthesised snapshot, or ``None`` if no in-PIT rows exist (caller
        should skip emitting in that case).
    """
    # ── Step 1: PIT gate — drop any row whose trade date is after as_of ──────
    visible = [
        r for r in rows
        if date.fromisoformat(r["tradeReportDate"]) <= as_of
    ]
    if not visible:
        return None

    # ── Step 2: Sum within each trade date across all marketCode venues ───────
    #
    # Phase -1 finding: AAPL on a single date returned 3 rows (venues B, Q,
    # and one other).  Naive sum-across-all-rows over-counts by the venue
    # count.  We must aggregate per-day first.
    per_day_short: dict[date, float] = defaultdict(float)
    per_day_total: dict[date, float] = defaultdict(float)

    for row in visible:
        trade_date = date.fromisoformat(row["tradeReportDate"])
        per_day_short[trade_date] += float(row.get("shortParQuantity") or 0)
        per_day_total[trade_date] += float(row.get("totalParQuantity") or 0)

    # ── Step 3: Aggregate across days ─────────────────────────────────────────
    short_cumulative = sum(per_day_short.values())
    n_days = max(len(per_day_total), 1)
    total_mean = sum(per_day_total.values()) / n_days

    settlement = max(per_day_short.keys())

    # Avoid division by zero if all totalParQuantity values are zero.
    days_to_cover: float | None = None
    if total_mean > 0:
        days_to_cover = short_cumulative / total_mean

    return ShortInterestSnapshot(
        ticker=symbol,
        settlement_date=settlement,
        report_publish_date=settlement,        # regShoDaily has no biweekly lag
        short_interest=short_cumulative,
        average_daily_volume=total_mean,
        days_to_cover=days_to_cover,
        source="finra_regsho_synthesised",
    )


@register(
    "short_interest",
    "finra",
    upstream="finra",
    rate_per_minute=30,
    burst=10,
)
async def fetch(
    ticker: str,
    *,
    as_of: date,
    lookback_days: int = 30,
    **_: Any,
) -> list[ShortInterestSnapshot]:
    """Return a single synthesised short-interest snapshot for ``ticker``.

    The snapshot aggregates ``lookback_days`` of ``regShoDaily`` rows,
    summing per-venue short and total volume within each day before summing
    across days.  Returns an empty list when FINRA credentials are absent
    (soft-fail) or when no in-PIT rows exist.

    Parameters
    ----------
    ticker:
        Upper-cased stock symbol (e.g. ``"AAPL"``).
    as_of:
        The simulation / backtest date.  Any row with
        ``tradeReportDate > as_of`` is excluded before aggregation.
    lookback_days:
        Rolling window width in calendar days (default 30).
    **_:
        Absorbs extra keyword arguments forwarded by ``dispatch``.

    Returns
    -------
    list[ShortInterestSnapshot]
        A list containing one synthesised snapshot, or an empty list.
    """
    symbol = ticker.upper()

    # Check in-process token cache first (synchronous — testable via monkeypatch).
    token = _get_token()

    # Cache miss — attempt a fresh token fetch.
    if token is None:
        token = await _refresh_token()

    # If we still have no token (missing credentials), soft-fail.
    if token is None:
        return []

    start = as_of - timedelta(days=lookback_days)

    headers = {
        **_JSON_HEADERS,
        "Authorization": f"Bearer {token}",
    }
    params = {
        "securitiesInformationProcessorSymbolIdentifier": symbol,
        # FINRA filter syntax: ge:<date>,le:<date>
        "tradeReportDate": f"ge:{start.isoformat()},le:{as_of.isoformat()}",
        # 30 days × ~3 venues per ticker ≈ 90 rows; 500 is a comfortable ceiling.
        "limit": 500,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT), headers=headers) as client:
        resp = await client.get(_DATA_URL, params=params)
        resp.raise_for_status()
        rows: list[dict] = resp.json() or []

    snap = _synthesise(rows, symbol, as_of)
    return [snap] if snap else []

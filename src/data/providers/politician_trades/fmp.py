"""FMP politician-trades provider — free 250/day, covers Senate + House.

Endpoints (FMP v4):
    https://financialmodelingprep.com/api/v4/senate-trading?symbol=AAPL&apikey=...
    https://financialmodelingprep.com/api/v4/senate-disclosure?symbol=AAPL&apikey=...

Both feeds use the same JSON row shape (``transactionDate``, ``disclosureDate``,
``firstName``, ``lastName``, ``office``, ``type``, ``amount``).  This provider
merges them, then applies the standard ``as_of`` cutoff + lookback window.

Raises ``SecretMissingError`` when ``FMP_API_KEY`` is unset so that
mis-configuration is surfaced loudly rather than silently returning no data.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import requests

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import PoliticianTrade

# Shared parsing helpers — identical logic used by both the FMP and Quiver
# providers; kept in one place so a fix in one applies to both.
from ._common import _coerce_side, _parse_amount_range, _parse_date

_BASE_URL     = "https://financialmodelingprep.com/api/v4"
_HTTP_TIMEOUT = 15.0


@with_retry
def _fetch_senate(symbol: str, api_key: str) -> list[dict]:
    """Call FMP ``/senate-trading?symbol=...`` and return raw rows.

    Parameters
    ----------
    symbol:
        The ticker symbol to query.
    api_key:
        The FMP API key to authenticate with.

    Returns
    -------
    list[dict]
        Raw JSON rows from the endpoint, or an empty list on no content.
    """
    url    = f"{_BASE_URL}/senate-trading"
    params = {"symbol": symbol, "apikey": api_key}
    resp   = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


@with_retry
def _fetch_house(symbol: str, api_key: str) -> list[dict]:
    """Call FMP ``/senate-disclosure?symbol=...`` (covers House) and return raw rows.

    Despite the endpoint name, this feed contains House disclosures.  FMP
    uses ``office`` within the row to indicate which chamber the politician
    sits in.

    Parameters
    ----------
    symbol:
        The ticker symbol to query.
    api_key:
        The FMP API key to authenticate with.

    Returns
    -------
    list[dict]
        Raw JSON rows from the endpoint, or an empty list on no content.
    """
    url    = f"{_BASE_URL}/senate-disclosure"
    params = {"symbol": symbol, "apikey": api_key}
    resp   = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


def _row_to_trade(row: dict, symbol: str) -> PoliticianTrade | None:
    """Project one FMP row into a ``PoliticianTrade``.

    Returns ``None`` when the row lacks a parseable ``transactionDate``,
    as the date is required for PIT filtering.

    Parameters
    ----------
    row:
        A single raw JSON row from either FMP endpoint.
    symbol:
        The ticker symbol the row belongs to (injected since FMP omits it).

    Returns
    -------
    PoliticianTrade | None
        Populated model, or ``None`` if the row is unusable.
    """
    txn_date = _parse_date(row.get("transactionDate"))
    if txn_date is None:
        return None

    disclosure = _parse_date(row.get("disclosureDate"))
    amount_min, amount_max = _parse_amount_range(row.get("amount"))

    # Combine firstName + lastName, falling back to "unknown" if both are absent.
    politician = " ".join(
        part for part in (row.get("firstName"), row.get("lastName")) if part
    ) or "unknown"

    return PoliticianTrade(
        ticker=symbol,
        politician=politician,
        chamber=row.get("office") or None,
        party=row.get("party") or None,
        side=_coerce_side(row.get("type")),
        transaction_date=txn_date,
        disclosure_date=disclosure,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
    )


@register(
    domain="politician_trades",
    name="fmp",
    upstream="fmp",
    rate_per_minute=20,
    burst=10,
)
async def fetch(
    ticker: str | None = None,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[PoliticianTrade]:
    """Senate + House trades for ``ticker`` filed in ``(as_of - lookback, as_of]``.

    Merges FMP's two endpoints into one list of ``PoliticianTrade``s.  Applies
    the same PIT cutoff as the cache reader: the effective date is
    ``COALESCE(disclosure_date, transaction_date)`` — whichever is known first.

    Raises ``SecretMissingError`` when ``FMP_API_KEY`` is unset.  Raises
    ``ValueError`` when ``ticker`` is empty — FMP requires a symbol and an
    empty ticker is a caller bug, not a recoverable condition.

    Parameters
    ----------
    ticker:
        Stock ticker symbol to filter trades by.  ``None`` or empty raises
        ``ValueError`` — FMP requires a symbol.
    as_of:
        The historical reference point.  Trades with an effective PIT date
        after this moment are excluded to prevent lookahead bias.
    lookback_days:
        How many calendar days before ``as_of`` to include.  Defaults to 90.
    **_unused:
        Absorbs extra keyword arguments forwarded by the provider registry
        (e.g. ``window_start``, ``window_end``) so callers need not know the
        exact signature of each registered provider.

    Returns
    -------
    list[PoliticianTrade]
        Filtered, merged trades from both FMP congressional-disclosure feeds.
    """
    api_key = require_key("FMP_API_KEY")

    symbol = (ticker or "").upper()
    if not symbol:
        # Empty ticker is a caller bug, not an API-key issue.
        raise ValueError("fmp.politician_trades: ticker is required and was empty")

    # Fetch both endpoints concurrently to minimise latency.
    senate, house = await asyncio.gather(
        asyncio.to_thread(_fetch_senate, symbol, api_key),
        asyncio.to_thread(_fetch_house,  symbol, api_key),
    )

    # PIT window: (lower, upper] — inclusive upper, exclusive lower.
    lower = as_of.date() - timedelta(days=lookback_days)
    upper = as_of.date()

    out: list[PoliticianTrade] = []
    for row in (*senate, *house):
        trade = _row_to_trade(row, symbol)
        if trade is None:
            continue

        # Use disclosure date where available; fall back to transaction date.
        pit = trade.disclosure_date or trade.transaction_date
        if pit <= lower or pit > upper:
            continue

        out.append(trade)

    return out

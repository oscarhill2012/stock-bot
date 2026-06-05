"""Quiver Quant congressional-trades provider.

Raises ``SecretMissingError`` when ``QUIVER_QUANT_API_KEY`` is unset so that
mis-configuration is surfaced loudly rather than silently returning no data.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

import requests

from data.config import get_config
from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import PoliticianTrade, TradeSide

_BASE_URL = "https://api.quiverquant.com/beta"

logger = logging.getLogger(__name__)

_SIDE_MAP: dict[str, TradeSide] = {
    "purchase": "buy",
    "buy": "buy",
    "sale": "sell",
    "sale (full)": "sell",
    "sale (partial)": "sell",
    "sell": "sell",
    "exchange": "exchange",
}


def _coerce_side(raw: Any) -> TradeSide:
    if not raw:
        return "unknown"
    return _SIDE_MAP.get(str(raw).strip().lower(), "unknown")


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _parse_amount_range(raw: Any) -> tuple[float | None, float | None]:
    if raw is None:
        return None, None
    text = str(raw).replace("$", "").replace(",", "").strip()
    if not text:
        return None, None
    parts = [p.strip() for p in text.split("-")]
    try:
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
        return float(parts[0]), float(parts[0])
    except ValueError:
        return None, None


@with_retry
def _fetch_trades(symbol: str | None, api_key: str) -> list[dict]:
    """Fetch raw congressional-trade rows from Quiver Quant's API.

    Parameters
    ----------
    symbol:
        Ticker to filter server-side; ``None`` returns all tickers.
    api_key:
        Bearer token for the Quiver Quant API.

    Returns
    -------
    list[dict]
        Raw JSON rows from the upstream response (empty list on empty body).
    """
    url = f"{_BASE_URL}/live/congresstrading"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    params: dict[str, Any] = {}
    if symbol:
        params["ticker"] = symbol

    # Read the timeout from centralised config rather than a module constant,
    # so config/data.json is the single source of truth for this value.
    timeout = get_config().quiver_http_timeout_seconds
    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


def _load_rows(symbol: str | None, api_key: str) -> list[dict]:
    """Thin wrapper around ``_fetch_trades`` used as the seam for test monkeypatching.

    Keeping the network call behind a named, non-decorated function lets unit
    tests replace ``_load_rows`` with a fake without having to pierce the
    ``@with_retry`` decorator on ``_fetch_trades``.

    Parameters
    ----------
    symbol:
        Ticker symbol passed straight through to ``_fetch_trades``.
    api_key:
        API key passed straight through to ``_fetch_trades``.

    Returns
    -------
    list[dict]
        Raw rows from the upstream API (or a test stub).
    """
    return _fetch_trades(symbol, api_key)


@register(
    domain="politician_trades",
    name="quiver",
    upstream="quiver",
    rate_per_minute=30,
    burst=10,
)
async def fetch(
    ticker: str | None = None,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[PoliticianTrade]:
    """Congressional trades for ``ticker`` reported within ``(as_of - lookback_days, as_of]``.

    Anchored on ``as_of`` so backfill never returns trades that did not yet
    exist at the historical moment.  Raises ``SecretMissingError`` when
    ``QUIVER_QUANT_API_KEY`` is unset.

    Parameters
    ----------
    ticker:
        Stock ticker symbol to filter trades by; ``None`` returns all tickers.
    as_of:
        The historical reference point.  Replaces ``date.today()`` so that
        replays see only trades disclosed at or before this moment.
    lookback_days:
        How many calendar days before ``as_of`` to include.
    **_unused:
        Absorbs extra keyword arguments forwarded by the provider registry
        (e.g. ``window_start``, ``window_end``) so callers need not know the
        exact signature of each registered provider.
    """
    api_key = require_key("QUIVER_QUANT_API_KEY")

    symbol  = ticker.upper() if ticker else None
    payload = await asyncio.to_thread(_load_rows, symbol, api_key)

    # Use as_of rather than date.today() so replays see the correct window.
    cutoff = as_of.date() - timedelta(days=lookback_days)
    upper  = as_of.date()

    trades: list[PoliticianTrade] = []
    for item in payload:
        txn_date = _parse_date(item.get("TransactionDate") or item.get("Traded"))

        # PIT correctness: the market only learns of a trade when it is disclosed
        # (STOCK Act filing), not when the transaction occurred.  Filter on
        # disclosure_date for the upper bound — a trade transacted before as_of
        # but disclosed after as_of is invisible at that historical moment.
        disc_date = _parse_date(
            item.get("DisclosureDate") or item.get("ReportDate") or item.get("Disclosed")
        )

        if txn_date is None or txn_date <= cutoff:
            continue
        if disc_date is None or disc_date > upper:
            # No known disclosure date, or disclosed after as_of — not yet public.
            continue
        amount_min, amount_max = _parse_amount_range(
            item.get("Range") or item.get("Amount") or item.get("Trade_Size_USD")
        )
        trades.append(
            PoliticianTrade(
                ticker=(item.get("Ticker") or symbol or "").upper(),
                politician=item.get("Representative") or item.get("Senator") or item.get("Name") or "unknown",
                chamber=item.get("Chamber") or item.get("House") or None,
                party=item.get("Party"),
                side=_coerce_side(item.get("Transaction") or item.get("Type")),
                transaction_date=txn_date,
                disclosure_date=disc_date,
                amount_min_usd=amount_min,
                amount_max_usd=amount_max,
            )
        )
    return trades

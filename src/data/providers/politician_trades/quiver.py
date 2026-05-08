"""Quiver Quant congressional-trades provider (soft-fail when key is unset)."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import requests

from data.registry import register
from data.retry import with_retry

from ...models import PoliticianTrade, TradeSide

_BASE_URL = "https://api.quiverquant.com/beta"
_HTTP_TIMEOUT = 15.0  # mirrors today's settings.http_timeout_seconds default

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
    url = f"{_BASE_URL}/live/congresstrading"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    params: dict[str, Any] = {}
    if symbol:
        params["ticker"] = symbol

    resp = requests.get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


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
    lookback_days: int = 90,
) -> list[PoliticianTrade]:
    api_key = os.getenv("QUIVER_QUANT_API_KEY")
    if not api_key:
        # Soft-fail: free tier unavailable. EDGAR's notable_holders carries
        # the smart-money signal until the key returns.
        logger.debug("QUIVER_QUANT_API_KEY unset — fetch returning []")
        return []

    symbol = ticker.upper() if ticker else None
    payload = await asyncio.to_thread(_fetch_trades, symbol, api_key)

    cutoff = date.today() - timedelta(days=lookback_days)
    trades: list[PoliticianTrade] = []
    for item in payload:
        txn_date = _parse_date(item.get("TransactionDate") or item.get("Traded"))
        if txn_date is None or txn_date < cutoff:
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
                disclosure_date=_parse_date(item.get("ReportDate") or item.get("Disclosed")),
                amount_min_usd=amount_min,
                amount_max_usd=amount_max,
            )
        )
    return trades

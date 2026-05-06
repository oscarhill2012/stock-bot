"""`get_public_figure_trades` — Quiver Quant congressional disclosures (async, rate-limited).

Quiver's free tier is currently unavailable. Until access is restored
this provider soft-fails to `[]` when `QUIVER_QUANT_API_KEY` is unset —
the bundle keeps building and the EDGAR-based `get_notable_holders`
provider fills the "smart money" slot in the meantime. Restoring Quiver
is a matter of adding the key back to `.env`; no code change needed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

from ..models import PoliticianTrade, TradeSide
from ..rate_limit import QUIVER
from ..retry import with_retry
from ..settings import get_settings

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


def _parse_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _parse_amount_range(raw: Any) -> tuple[Optional[float], Optional[float]]:
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
def _fetch_trades(symbol: Optional[str], api_key: str) -> list[dict]:
    s = get_settings()
    url = f"{s.quiver_base_url.rstrip('/')}/live/congresstrading"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    params: dict[str, Any] = {}
    if symbol:
        params["ticker"] = symbol

    resp = requests.get(url, headers=headers, params=params, timeout=s.http_timeout_seconds)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


async def get_public_figure_trades(
    ticker: Optional[str] = None,
    lookback_days: int = 90,
) -> list[PoliticianTrade]:
    api_key = get_settings().quiver_quant_api_key
    if not api_key:
        # Soft-fail: Quiver free tier unavailable. EDGAR's notable_holders
        # carries the "smart money" signal until the key returns.
        logger.debug("QUIVER_QUANT_API_KEY unset — get_public_figure_trades returning []")
        return []

    symbol = ticker.upper() if ticker else None
    await QUIVER.acquire()
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

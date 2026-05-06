"""`get_public_figure_trades` — Quiver Quant congressional disclosures (async, rate-limited)."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

from ..models import PoliticianTrade, TradeSide
from ..rate_limit import QUIVER
from ..retry import with_retry
from ..settings import get_settings, require

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
def _fetch_trades(symbol: Optional[str]) -> list[dict]:
    s = get_settings()
    api_key = require("QUIVER_QUANT_API_KEY", s.quiver_quant_api_key, "get_public_figure_trades")

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
    symbol = ticker.upper() if ticker else None
    await QUIVER.acquire()
    payload = await asyncio.to_thread(_fetch_trades, symbol)

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

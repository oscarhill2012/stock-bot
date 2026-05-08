"""yfinance stats provider — OHLCV history + fundamentals (rate-limited via registry)."""
from __future__ import annotations

import asyncio
import math
from typing import Any

import yfinance as yf

from data.registry import register
from data.retry import with_retry

from ...models import OHLCBar, StockStats


def _f(d: dict[str, Any], *keys: str) -> float | None:
    """Try each key in order; return the first finite float found, or None."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return None


@with_retry
def _fetch_stats(symbol: str, period: str, interval: str) -> StockStats:
    yt = yf.Ticker(symbol)
    df = yt.history(period=period, interval=interval, auto_adjust=True)

    bars: list[OHLCBar] = []
    if df is not None and not df.empty:
        for ts, row in df.iterrows():
            bars.append(
                OHLCBar(
                    timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0) or 0),
                )
            )

    info: dict[str, Any] = {}
    try:
        info = yt.info or {}
    except Exception:
        info = {}

    fast: dict[str, Any] = {}
    try:
        fast = dict(yt.fast_info) if yt.fast_info else {}
    except Exception:
        fast = {}

    return StockStats(
        ticker=symbol,
        history=bars,
        market_cap=_f(info, "marketCap") or _f(fast, "market_cap", "marketCap"),
        trailing_pe=_f(info, "trailingPE"),
        forward_pe=_f(info, "forwardPE"),
        beta=_f(info, "beta"),
        dividend_yield=_f(info, "dividendYield"),
        fifty_day_average=_f(info, "fiftyDayAverage")
        or _f(fast, "fifty_day_average", "fiftyDayAverage"),
        two_hundred_day_average=_f(info, "twoHundredDayAverage")
        or _f(fast, "two_hundred_day_average", "twoHundredDayAverage"),
        last_price=_f(fast, "last_price", "lastPrice")
        or _f(info, "currentPrice", "regularMarketPrice"),
        sector=info.get("sector"),
        long_name=info.get("longName") or info.get("shortName"),
    )


@register(
    domain="stats",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch(ticker: str, *, period: str = "1y", interval: str = "1d") -> StockStats:
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_stats, symbol, period, interval)

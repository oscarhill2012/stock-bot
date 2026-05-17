"""yfinance providers — split into price history + company ratios.

The underlying yfinance call is shared per-ticker per-tick by an in-memory
LRU cache keyed on ``(symbol, period, interval)`` so that requesting both
``price_history`` and ``ratios`` for the same ticker does not double the
yfinance hit. This relies on each tick running in a fresh OS process (e.g. Cloud Run Jobs). For
in-process multi-tick callers — test harnesses, long-running daemon modes — call
``_yt_raw.cache_clear()`` between ticks to avoid serving stale data.

Provenance notes
----------------
``forward_pe`` and ``analyst_rating_avg`` are flagged as **snapshot-leaky**:
yfinance serves wall-clock-current values, so these fields carry implicit
look-ahead when used in historical backtests.  Do not use them as PIT signals
without first routing through the ``pit_composite`` provider.
"""
from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from functools import lru_cache
from typing import Any

import yfinance as yf

from data.registry import register
from data.retry import with_retry

from ...models import CompanyRatios, OHLCBar, PriceHistory


def _f(d: dict[str, Any], *keys: str) -> float | None:
    """Try each key in order; return the first finite float found, or ``None``.

    Parameters
    ----------
    d:
        Source dict (e.g. yfinance ``info`` or ``fast_info``).
    *keys:
        Key names to try in order.

    Returns
    -------
    float | None
        First finite float value found, or ``None`` if none qualify.
    """
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


@lru_cache(maxsize=128)
def _yt_raw(symbol: str, period: str, interval: str) -> dict[str, Any]:
    """Fetch the raw yfinance payload once per ``(symbol, period, interval)``.

    Returns a dict with ``history`` (DataFrame), ``info`` (dict), and
    ``fast`` (dict). Shared between the price-history and ratios providers
    so a single tick that needs both pays only one yfinance round-trip.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    period:
        yfinance history period string (e.g. ``"1y"``).
    interval:
        yfinance history interval string (e.g. ``"1d"``).

    Returns
    -------
    dict
        Keys: ``"history"`` (DataFrame), ``"info"`` (dict), ``"fast"`` (dict).
    """
    yt = yf.Ticker(symbol)
    df = yt.history(period=period, interval=interval, auto_adjust=True)

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

    return {"history": df, "info": info, "fast": fast}


@with_retry
def _fetch_price_history(symbol: str, period: str, interval: str) -> PriceHistory:
    """Project the yfinance OHLCV frame into a ``PriceHistory``.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    period, interval:
        Passed through to ``_yt_raw`` — keyed by the LRU cache.

    Returns
    -------
    PriceHistory
        Bars ordered oldest -> newest. Empty list when yfinance returns no data.
    """
    raw = _yt_raw(symbol, period, interval)
    df = raw["history"]

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

    return PriceHistory(ticker=symbol, bars=bars)


def _fetch_info_dict(symbol: str, period: str, interval: str) -> dict[str, Any]:
    """Return the raw yfinance ``info`` dict for ``symbol``.

    Extracted as a separate, monkeypatchable function so that unit tests can
    inject a synthetic ``info`` payload without touching the LRU-cached
    ``_yt_raw`` call.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    period, interval:
        Forwarded to ``_yt_raw`` — determines the cache key for the shared
        yfinance payload.

    Returns
    -------
    dict[str, Any]
        The ``yt.info`` dict, or ``{}`` when yfinance raises.
    """
    return _yt_raw(symbol, period, interval)["info"]


@with_retry
def _fetch_company_ratios(
    symbol: str,
    period: str,
    interval: str,
    as_of: date | datetime | None = None,
) -> CompanyRatios:
    """Project the yfinance ``info`` + ``fast_info`` dicts into a ``CompanyRatios``.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    period, interval:
        Passed through to ``_yt_raw`` — keyed by the LRU cache.
    as_of:
        The point-in-time date to stamp on the returned ``CompanyRatios``.
        Accepts either a ``date`` or a ``datetime`` (converted to ``date``).
        ``None`` leaves ``CompanyRatios.as_of`` unset.

    Returns
    -------
    CompanyRatios
        All optional fundamental fields populated where yfinance provides data.
        Non-finite floats are normalised to ``None`` by ``_f``.

    Notes
    -----
    ``forward_pe`` and ``analyst_rating_avg`` carry **snapshot-leaky**
    provenance — yfinance serves wall-clock values, so these fields may embed
    look-ahead information when used in a historical backtest context.
    """
    raw = _yt_raw(symbol, period, interval)
    info = _fetch_info_dict(symbol, period, interval)
    fast = raw["fast"]

    # Normalise as_of to a plain date for the model field.
    as_of_date: date | None = None
    if isinstance(as_of, datetime):
        as_of_date = as_of.date()
    elif isinstance(as_of, date):
        as_of_date = as_of

    return CompanyRatios(
        ticker=symbol,
        as_of=as_of_date,

        long_name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        market_cap=_f(info, "marketCap") or _f(fast, "market_cap", "marketCap"),
        trailing_pe=_f(info, "trailingPE"),

        # snapshot-leaky: forward_pe is a consensus estimate served at wall-clock
        # time — not a historical point-in-time figure.
        forward_pe=_f(info, "forwardPE"),

        beta=_f(info, "beta"),
        dividend_yield=_f(info, "dividendYield"),
        fifty_day_average=_f(info, "fiftyDayAverage")
        or _f(fast, "fifty_day_average", "fiftyDayAverage"),
        two_hundred_day_average=_f(info, "twoHundredDayAverage")
        or _f(fast, "two_hundred_day_average", "twoHundredDayAverage"),
        last_price=_f(fast, "last_price", "lastPrice")
        or _f(info, "currentPrice", "regularMarketPrice"),

        # 52-week extremes — added Phase 7 task 4.6.
        fifty_two_week_high=_f(info, "fiftyTwoWeekHigh"),
        fifty_two_week_low=_f(info, "fiftyTwoWeekLow"),

        # snapshot-leaky: analyst consensus figures are served at wall-clock time.
        analyst_rating_avg=_f(info, "recommendationMean"),
        number_of_analyst_opinions=(
            int(info["numberOfAnalystOpinions"])
            if info.get("numberOfAnalystOpinions") is not None
            else None
        ),
    )


@register(
    domain="price_history",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch_price_history(
    ticker: str,
    *,
    period: str = "1y",
    interval: str = "1d",
    as_of: datetime,
    **_unused,
) -> PriceHistory:
    """Async wrapper for the price-history fetch — runs the blocking call off-thread.

    ``as_of`` is accepted for dispatch parity but yfinance's period queries are
    wall-clock anchored — live behaviour is unchanged.  Backfill callers slice
    the returned ``max``-period history client-side to the historical window
    (see ``scripts/backtest_fetch.py``).

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    period:
        yfinance history period (default ``"1y"``).
    interval:
        yfinance history interval (default ``"1d"``).
    as_of:
        Required for interface parity with cache-backed providers; ignored here
        as yfinance serves wall-clock-current data.
    **_unused:
        Absorbs any additional kwargs passed by the dispatch layer.

    Returns
    -------
    PriceHistory
        OHLCV bars ordered oldest -> newest.
    """
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_price_history, symbol, period, interval)


@register(
    domain="company_ratios",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch_company_ratios(
    ticker: str,
    *,
    period: str = "1y",
    interval: str = "1d",
    as_of: datetime,
    **_unused,
) -> CompanyRatios:
    """Async wrapper for the ratios fetch — runs the blocking call off-thread.

    ``as_of`` is stamped onto the returned ``CompanyRatios.as_of`` field so
    that cache-backed layers can use it as a PIT gate.  yfinance's ``info``
    endpoint still serves wall-clock-current data, so **this provider is
    unsuitable for historical PIT queries** — use ``pit_composite`` for
    backtests.  ``forward_pe`` and ``analyst_rating_avg`` are snapshot-leaky
    fields (see module docstring).

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    period:
        yfinance history period (default ``"1y"``).
    interval:
        yfinance history interval (default ``"1d"``).
    as_of:
        Stamped onto ``CompanyRatios.as_of``; does not change the data yfinance
        fetches (still wall-clock current).
    **_unused:
        Absorbs any additional kwargs passed by the dispatch layer.

    Returns
    -------
    CompanyRatios
        Scalar fundamentals + summary stats, including 52-week extremes and
        analyst consensus counters.
    """
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_company_ratios, symbol, period, interval, as_of)

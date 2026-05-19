"""yfinance providers — split into price history + company ratios.

The underlying yfinance call is shared per-ticker per-tick by an in-memory
LRU cache keyed on ``(symbol, period, interval)`` so that requesting both
``price_history`` and ``ratios`` for the same ticker does not double the
yfinance hit. This relies on each tick running in a fresh OS process (e.g. Cloud Run Jobs). For
in-process multi-tick callers — test harnesses, long-running daemon modes — call
``_yt_raw.cache_clear()`` between ticks to avoid serving stale data.

PIT (as_of) protection for OHLCV
--------------------------------
``_yt_raw`` now pulls *raw* bars (``auto_adjust=False``) plus the corporate-
actions table (``yt.actions``).  ``_fetch_price_history`` applies back-
adjustment via ``_pit_adjust`` using **only** actions with ex-date on or
before the caller's ``as_of``.  This prevents the silent PIT leak from
``auto_adjust=True``, which retroactively folds *every* split / dividend
between a bar's date and "now" into the historical close — embedding
post-window information into in-window bars when used for backtest replay.
``as_of=datetime.now()`` on a live tick degrades cleanly to "apply all known
actions" which is semantically equivalent to the previous auto_adjust=True
behaviour (numerically near-identical because recent bars carry few
unrealised actions ahead of them).

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

import pandas as pd
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

    Returns a dict with ``history`` (raw, unadjusted OHLCV DataFrame),
    ``actions`` (the splits + dividends table from ``yt.actions``),
    ``info`` (dict), and ``fast`` (dict).  Shared between the price-history
    and ratios providers so a single tick that needs both pays only one
    yfinance round-trip.

    ``auto_adjust=False`` is deliberate — back-adjustment is applied later
    in ``_fetch_price_history`` via ``_pit_adjust`` using only the actions
    that fall on or before the caller's ``as_of``.  See the module docstring
    for the rationale.

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
        Keys: ``"history"`` (DataFrame of raw bars), ``"actions"`` (DataFrame
        of split + dividend events keyed by ex-date), ``"info"`` (dict),
        ``"fast"`` (dict).
    """
    yt = yf.Ticker(symbol)
    df = yt.history(period=period, interval=interval, auto_adjust=False)

    # Corporate-actions table — DatetimeIndex keyed by ex-date with
    # ``Dividends`` and ``Stock Splits`` columns.  Empty DataFrame when
    # the ticker has no recorded actions (or yfinance errors out).
    actions: pd.DataFrame
    try:
        actions = yt.actions if yt.actions is not None else pd.DataFrame()
    except Exception:
        actions = pd.DataFrame()

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

    return {"history": df, "actions": actions, "info": info, "fast": fast}


def _pit_adjust(
    df: pd.DataFrame | None,
    actions: pd.DataFrame | None,
    as_of: datetime | date | None,
) -> pd.DataFrame | None:
    """Back-adjust raw OHLCV bars using only actions with ex-date <= ``as_of``.

    Splits and cash dividends with ex-date ``e`` are applied to every bar
    whose date is **strictly before** ``e`` — never to bars on or after ``e``,
    which already trade in post-event currency.  Actions with ex-date *after*
    ``as_of`` are deliberately ignored: they had not yet been disclosed at
    ``as_of``, so applying them would leak future information into the bar.

    Arithmetic
    ----------
    * Splits: divide OHLC by the split factor; multiply Volume by the same.
      A 4-for-1 split has factor 4.0 → pre-split closes become ``close / 4``.
    * Dividends: subtract the per-share dividend amount from OHLC.  This is
      the simple subtractive back-adjustment (matches the intent of yfinance's
      auto_adjust=True for indicator-level use, accurate to sub-percent on
      typical large-cap dividends).

    Events are walked newest-first so each adjustment sees bars that have
    not yet been altered by later events, mirroring yfinance's own
    back-adjustment ordering.

    Parameters
    ----------
    df:
        Raw OHLCV DataFrame from ``_yt_raw["history"]``.  ``None`` or empty
        returns immediately.
    actions:
        Corporate-actions DataFrame from ``_yt_raw["actions"]`` with
        ``Dividends`` and ``Stock Splits`` columns.  ``None`` or empty
        returns ``df`` unchanged.
    as_of:
        PIT cutoff — only actions with ex-date on or before this are applied.
        ``None`` returns ``df`` unchanged (raw bars).

    Returns
    -------
    pd.DataFrame | None
        A new DataFrame with adjusted OHLC + Volume, or the input when no
        adjustment was required.
    """
    if df is None or df.empty:
        return df
    if actions is None or actions.empty or as_of is None:
        return df

    # Normalise as_of to a date for comparison against ex-date timestamps.
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of

    # Clamp the action set to events known on or before as_of.
    pit_actions = actions[actions.index.date <= as_of_date]
    if pit_actions.empty:
        return df

    df = df.copy()

    # Volume comes back from yfinance as ``int64``.  Under pandas >= 3.0 an
    # in-place ``int64 *= float`` is forbidden when the product cannot be
    # losslessly stored as int64 — e.g. a 3-for-2 split (factor 1.5) on a
    # 1_000_001-share bar yields 1_500_001.5, which raises
    # ``LossySetitemError``.  We promote Volume to ``float64`` once here so
    # split multiplication is always safe; downstream we already coerce
    # each volume to a Python ``float`` when building ``OHLCBar``, so this
    # upcast is invisible to callers.
    if "Volume" in df.columns and df["Volume"].dtype.kind in ("i", "u"):
        df["Volume"] = df["Volume"].astype("float64")

    # Apply newest-first so each event sees bars unaltered by later events.
    for ex_date, row in pit_actions.iloc[::-1].iterrows():
        ex_date_d = ex_date.date() if hasattr(ex_date, "date") else ex_date

        # Mask: every bar strictly before the ex-date is in scope.
        mask = df.index.date < ex_date_d
        if not mask.any():
            continue

        split = float(row.get("Stock Splits", 0) or 0)
        div   = float(row.get("Dividends",    0) or 0)

        if split and split != 0:
            df.loc[mask, ["Open", "High", "Low", "Close"]] /= split
            df.loc[mask, "Volume"]                          *= split

        if div:
            df.loc[mask, ["Open", "High", "Low", "Close"]] -= div

    return df


@with_retry
def _fetch_price_history(
    symbol: str,
    period: str,
    interval: str,
    as_of: datetime | date | None = None,
) -> PriceHistory:
    """Project the yfinance OHLCV frame into a ``PriceHistory``, PIT-clamped.

    Raw bars are PIT-back-adjusted via ``_pit_adjust`` using only the corporate
    actions known on or before ``as_of``.  When ``as_of`` is ``None`` the
    bars are returned raw (unadjusted) — callers should pass an explicit
    ``as_of`` to get an adjusted series.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    period, interval:
        Passed through to ``_yt_raw`` — keyed by the LRU cache.
    as_of:
        PIT cutoff for back-adjustment.  Live callers pass
        ``datetime.now()`` (or near-equivalent); backtest callers pass
        ``window.end``.  ``None`` disables adjustment entirely.

    Returns
    -------
    PriceHistory
        Bars ordered oldest -> newest. Empty list when yfinance returns no data.
    """
    raw = _yt_raw(symbol, period, interval)
    df  = _pit_adjust(raw.get("history"), raw.get("actions"), as_of)

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


def _sync_bulk_download(
    symbols: tuple[str, ...],
    period: str,
    interval: str,
    as_of: date,
) -> dict[str, PriceHistory]:
    """Synchronous core of ``_bulk_download`` — runs in the thread pool.

    Calls ``yf.download`` once for all ``symbols`` and unpacks the returned
    MultiIndex DataFrame into one ``PriceHistory`` per symbol. Rows where a
    symbol's fields cannot be parsed (``KeyError`` / ``ValueError``) are
    silently skipped so a single bad ticker cannot corrupt the whole batch.

    Parameters
    ----------
    symbols:
        Tuple of ticker symbols to fetch in bulk.
    period:
        yfinance history period string (e.g. ``"1y"``).
    interval:
        yfinance history interval string (e.g. ``"1d"``).
    as_of:
        Point-in-time date — accepted for interface parity but unused here
        because yfinance period queries are wall-clock anchored.

    Returns
    -------
    dict[str, PriceHistory]
        One ``PriceHistory`` per requested symbol, keyed by symbol string.
        Bars are ordered oldest → newest.
    """
    df = yf.download(
        list(symbols),
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=True,
    )

    out: dict[str, PriceHistory] = {}

    for sym in symbols:
        bars: list[OHLCBar] = []

        for ts, row in df.iterrows():
            try:
                bars.append(OHLCBar(
                    timestamp=ts.to_pydatetime(),
                    open=float(row[("Open", sym)]),
                    high=float(row[("High", sym)]),
                    low=float(row[("Low", sym)]),
                    close=float(row[("Close", sym)]),
                    volume=int(row[("Volume", sym)]),
                ))
            except (KeyError, ValueError):
                # Missing or non-numeric data for this row — skip silently.
                continue

        out[sym] = PriceHistory(ticker=sym, bars=bars)

    return out


async def _bulk_download(
    symbols: tuple[str, ...],
    *,
    period: str,
    interval: str,
    as_of: date,
) -> dict[str, PriceHistory]:
    """Bulk yfinance download — single round-trip for multiple symbols.

    Issues exactly one ``yf.download`` call and unpacks the resulting
    MultiIndex DataFrame into one ``PriceHistory`` per requested symbol.
    This is significantly faster than 12 sequential per-ticker calls and
    avoids burning 12 token-bucket slots on the yfinance provider.

    Parameters
    ----------
    symbols:
        Tuple of ticker symbols (e.g. ``("SPY", "XLK", ...)``).
    period:
        yfinance history period string (e.g. ``"1y"``).
    interval:
        yfinance history interval string (e.g. ``"1d"``).
    as_of:
        Point-in-time reference date — accepted for interface parity, not
        forwarded to yfinance (which uses wall-clock anchored periods).

    Returns
    -------
    dict[str, PriceHistory]
        One ``PriceHistory`` per requested symbol, keyed by symbol string.
    """
    return await asyncio.to_thread(
        _sync_bulk_download, symbols, period, interval, as_of,
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

    ``as_of`` is now **honoured** as a PIT cutoff for corporate-action
    back-adjustment.  yfinance still returns its full ``period``-anchored
    bar set, but ``_pit_adjust`` filters splits / dividends to those with
    ex-date on or before ``as_of`` before applying them — eliminating the
    silent leak where post-window corporate actions would otherwise be
    folded into in-window bars by ``auto_adjust=True``.  Live callers pass
    ``as_of=datetime.now()`` and degrade cleanly to "apply all known
    actions" (semantically equivalent to the previous behaviour).

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    period:
        yfinance history period (default ``"1y"``).
    interval:
        yfinance history interval (default ``"1d"``).
    as_of:
        PIT cutoff for corporate-action back-adjustment.  Required.
    **_unused:
        Absorbs any additional kwargs passed by the dispatch layer.

    Returns
    -------
    PriceHistory
        OHLCV bars ordered oldest -> newest, PIT-back-adjusted to ``as_of``.
    """
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_price_history, symbol, period, interval, as_of)


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

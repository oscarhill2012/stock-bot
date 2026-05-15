"""CLI: fill the backtest golden cache for one window × the configured watchlist.

Usage (from project root):

    PYTHONPATH=src python -m scripts.backtest_fetch --window svb-stress-2023-03
    PYTHONPATH=src python -m scripts.backtest_fetch --window svb-stress-2023-03 \\
        --watchlist config/watchlist.json

The script resolves the window from ``config/backtest_windows.json``, loads the
watchlist (default ``config/watchlist.json``), builds the live-provider function
map, and invokes ``Fetcher.run()``.

Network errors are caught per (ticker, domain) and recorded in ``cache_runs``
as ``status='error'``.  The script always exits cleanly — inspect the audit
table to find any gaps.

Adaptation notes vs plan:
- ``market_meta`` / ``get_stock_stats`` do not exist in the real codebase.
  The cache uses ``company_ratios`` / ``write_company_ratios``; the live
  provider is ``get_company_ratios``.
- OHLCV is fetched via ``get_price_history``, which dispatches through the
  registered yfinance provider and returns a ``PriceHistory`` (a list of
  ``OHLCBar`` instances) without needing a raw ``yf.download`` call.
- ``social_sentiment`` is skipped — no historical data source for backfill.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Provider function factories
# ---------------------------------------------------------------------------
# Each inner async function is typed as ``fn(ticker, *, start, end)`` —
# the Fetcher calls them with those keyword arguments.  Functions that
# need an ``as_of`` datetime construct it from ``end`` (market-close time
# on the last day of the window).
# ---------------------------------------------------------------------------

_NY = ZoneInfo("America/New_York")


def _as_of_close(end) -> datetime:
    """Return a market-close datetime on ``end`` in New York time.

    Matches the ``as_of`` timestamps used by live ticks so that the cache
    stores data exactly as it would have been seen at 16:00 ET on that date.

    Parameters
    ----------
    end:
        A ``datetime.date`` representing the last day of a window.

    Returns
    -------
    datetime
        A timezone-aware datetime at 16:00 America/New_York on ``end``.
    """
    return datetime.combine(end, time(16, 0), tzinfo=_NY)


async def _fill_quarterly_ratios(ticker: str, start, end) -> list:
    """Fetch one ``CompanyRatios`` snapshot per quarter-end in ``[start, end]``.

    Calls the active company_ratios provider once per quarter-end date and
    returns ``list[(snapshot, quarter_end_date)]`` so ``Fetcher._fetch_one``
    can unpack each tuple into the store's ``write_company_ratios`` signature.

    The replay reader uses ``as_of_date <= as_of`` so multiple snapshots
    inside one window let the analyst see the right quarter's fundamentals
    rather than a single window-end snapshot.

    Parameters
    ----------
    ticker:
        Stock ticker symbol (e.g. ``"AAPL"``).
    start:
        First date of the backtest window (inclusive).
    end:
        Last date of the backtest window (inclusive).

    Returns
    -------
    list[tuple[CompanyRatios, date]]
        One entry per quarter-end that falls within ``[start, end]``, or a
        single window-end snapshot if no quarter-end falls inside the range.
    """
    from datetime import date as _date

    from data import get_company_ratios

    # Calendar quarter-end dates: 31-Mar, 30-Jun, 30-Sep, 31-Dec.
    _Q_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))

    candidates: list[_date] = []
    for year in range(start.year, end.year + 1):
        for month, day in _Q_ENDS:
            candidates.append(_date(year, month, day))

    targets = [d for d in candidates if start <= d <= end]
    if not targets:
        # Window doesn't span any quarter-end — fall back to a single snapshot
        # at window-end so the cache is not entirely empty.
        targets = [end]

    out: list = []
    for qe in targets:
        snapshot = await get_company_ratios(
            ticker,
            period="max",
            interval="1d",
            as_of=datetime.combine(qe, time(16, 0), tzinfo=_NY),
        )
        out.append((snapshot, qe))
    return out


def _build_provider_fns() -> dict:
    """Return the domain → public-wrapper fetch-function map for the Fetcher.

    Each function has the signature ``async fn(ticker, *, start, end)`` and
    delegates to the matching ``data.get_*`` wrapper.  Whatever provider is
    active in ``config/data.json`` is used automatically — switching is a
    config-only operation.

    Returns
    -------
    dict[str, Callable]
        Keys mirror ``CachedDataStore`` writer domains.
    """
    from data import (
        get_company_filings,
        get_insider_trades,
        get_notable_holders,
        get_price_history,
        get_public_figure_trades,
        get_stock_news,
    )

    async def _ohlcv(ticker: str, *, start, end) -> list:
        """Pull max-period history through the active price-history provider, then slice."""
        history = await get_price_history(
            ticker, period="max", interval="1d", as_of=_as_of_close(end),
        )
        return [bar for bar in history.bars if start <= bar.timestamp.date() <= end]

    async def _company_ratios(ticker: str, *, start, end) -> list:
        """Fan out quarter-end as_ofs across the window for PIT-correct snapshots."""
        return await _fill_quarterly_ratios(ticker, start, end)

    async def _news(ticker: str, *, start, end) -> list:
        """Fetch news articles published within the window."""
        return await get_stock_news(
            ticker, from_date=start, to_date=end, as_of=_as_of_close(end),
        )

    async def _filings(ticker: str, *, start, end) -> list:
        """Fetch SEC filings filed on or before window-close."""
        return await get_company_filings(ticker, as_of=_as_of_close(end))

    async def _insider_trades(ticker: str, *, start, end) -> list:
        """Fetch Form 4 insider trades for the window period."""
        lookback = (end - start).days + 14
        return await get_insider_trades(
            ticker, lookback_days=lookback, as_of=_as_of_close(end),
        )

    async def _politician_trades(ticker: str, *, start, end) -> list:
        """Fetch congressional/politician trades disclosed during the window."""
        lookback = (end - start).days + 14
        return await get_public_figure_trades(
            ticker, lookback_days=lookback, as_of=_as_of_close(end),
        )

    async def _notable_holders(ticker: str, *, start, end) -> list:
        """Fetch SC-13D/13G/13F filings filed before window-close."""
        return await get_notable_holders(ticker, as_of=_as_of_close(end))

    return {
        "ohlcv":             _ohlcv,
        "company_ratios":    _company_ratios,
        "news":              _news,
        "filings":           _filings,
        "insider_trades":    _insider_trades,
        "politician_trades": _politician_trades,
        "notable_holders":   _notable_holders,
    }


def _build_provider_name_map() -> dict[str, str]:
    """Return domain → provider-name string for the cache_runs audit column.

    Reads the live ``config/data.json`` provider names where they exist, then
    adds ``ohlcv → yfinance`` (a cache-layer domain that has no direct
    ``data.json`` analogue — it maps to ``price_history`` in the live layer).

    Returns
    -------
    dict[str, str]
        E.g. ``{"ohlcv": "yfinance", "company_ratios": "yfinance", ...}``.
    """
    from data.config import get_config

    cfg      = get_config()
    live_map = dict(cfg.providers)  # domain → provider name from data.json

    return {
        "ohlcv":             live_map.get("price_history", "yfinance"),
        "company_ratios":    live_map.get("company_ratios", "yfinance"),
        "news":              live_map.get("news", "unknown"),
        "filings":           live_map.get("filings", "unknown"),
        "insider_trades":    live_map.get("insider_trades", "unknown"),
        "politician_trades": live_map.get("politician_trades", "unknown"),
        "notable_holders":   live_map.get("notable_holders", "unknown"),
    }


# ---------------------------------------------------------------------------
# Async main
# ---------------------------------------------------------------------------

async def _main_async(args: argparse.Namespace) -> None:
    """Resolve configuration, build the Fetcher, and run the cache fill.

    Parameters
    ----------
    args:
        Parsed CLI arguments (``args.window``, ``args.watchlist``).
    """
    from backtest.cache.fetcher import Fetcher
    from backtest.cache.store import CachedDataStore
    from backtest.windows import load_windows

    settings  = json.loads(Path("config/backtest_settings.json").read_text())
    watchlist_path = Path(args.watchlist)
    watchlist = json.loads(watchlist_path.read_text())["tickers"]

    windows = load_windows(Path("config/backtest_windows.json"))
    if args.window not in windows:
        raise SystemExit(
            f"Unknown window key '{args.window}'. "
            f"Available: {sorted(windows)}"
        )
    window = windows[args.window]

    store = CachedDataStore(Path(settings["cache_path"]))

    fetcher = Fetcher(
        store=store,
        window_key=args.window,
        window=window,
        watchlist=watchlist,
        provider_fns=_build_provider_fns(),
        live_providers_for_domain=_build_provider_name_map(),
    )

    logging.info(
        "Starting cache fill: window=%s tickers=%d domains=%d",
        args.window,
        len(watchlist),
        len(_build_provider_fns()),
    )

    await fetcher.run()

    logging.info("Cache fill complete.")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse CLI arguments and run the async cache fill."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Fill the backtest golden cache for one window × the watchlist. "
            "Idempotent — re-running skips already-ok (ticker, domain) pairs."
        )
    )
    parser.add_argument(
        "--window",
        required=True,
        help="Window key from config/backtest_windows.json (e.g. svb-stress-2023-03).",
    )
    parser.add_argument(
        "--watchlist",
        default="config/watchlist.json",
        help="Path to a JSON file with a 'tickers' list (default: config/watchlist.json).",
    )

    asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

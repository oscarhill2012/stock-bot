"""CLI: fill the backtest cache for one window × the configured watchlist.

Usage
-----
::

    PYTHONPATH=src python -m scripts.backtest_fetch --window svb-stress-2023-03
    PYTHONPATH=src python -m scripts.backtest_fetch --window svb-stress-2023-03 --tickers AAPL,MSFT

The script reads ``config/backtest_settings.json`` for the cache path, and
``config/watchlist.json`` for the default ticker list.  Pass ``--tickers`` to
override the watchlist (comma-separated, no spaces) or ``--watchlist`` to
point at an alternative watchlist JSON file with a ``"tickers"`` key.

Domains fetched
---------------
- ``ohlcv``             — daily bars via yfinance (direct download, no analyst wrapper)
- ``market_meta``       — one fundamentals snapshot at the window end via ``get_stock_stats``
- ``news``              — articles via ``get_stock_news``
- ``filings``           — SEC filings via ``get_company_filings``
- ``insider_trades``    — Form 4 data via ``get_insider_trades``
- ``politician_trades`` — STOCK Act disclosures via ``get_public_figure_trades``
- ``notable_holders``   — 13D/13G holders via ``get_notable_holders``

``social_sentiment`` is intentionally omitted — the social cache provider
returns ``None`` because historical social data is not available from free
sources; it will be wired up under a separate backlog item.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

# Provider imports — these trigger the registry @register decorators.  They
# must be top-level so the registry is fully populated before dispatch runs.
from backtest.cache.fetcher import Fetcher
from backtest.cache.store import CachedDataStore
from backtest.windows import load_windows
from data import (
    get_company_filings,
    get_insider_trades,
    get_notable_holders,
    get_public_figure_trades,
    get_stock_news,
    get_stock_stats,
)

# New York close is the canonical "as-of" time for non-OHLCV domains — it
# represents the point at which all intraday information for that date is
# settled.
_NY = ZoneInfo("America/New_York")
_CLOSE_TIME = time(16, 0)


def _build_provider_fns() -> dict:
    """Return the domain → live-provider async function map for the fetcher.

    Each function accepts ``(ticker, *, start, end)`` and returns a list (or
    list of tuples for ``market_meta``) suitable for the corresponding store
    writer.

    ``ohlcv`` is implemented inline because no analyst-layer wrapper covers a
    plain historical bar download by date range — analysts use ``get_stock_stats``
    which returns a windowed *period* view rather than an explicit ``[start, end]``
    slice.
    """

    async def _ohlcv(ticker: str, *, start, end):
        """Fetch daily bars in ``[start, end]`` via yfinance directly.

        yfinance's ``download`` method accepts ``start`` / ``end`` date objects,
        which avoids time-zone arithmetic at this layer.

        Parameters
        ----------
        ticker:
            Equity symbol.
        start, end:
            Inclusive date range (``datetime.date`` objects).

        Returns
        -------
        list[OHLCBar]
            One bar per trading day, timestamps set to 16:00 UTC (proxy for
            close time; the live OHLCBar model has no intraday time).
        """
        import yfinance as yf

        from data.models import OHLCBar

        df = yf.download(
            ticker,
            start=start,
            end=end,
            auto_adjust=True,   # adjust for splits/dividends in the download
            progress=False,
        )

        bars: list[OHLCBar] = []
        for row_date, row in df.iterrows():
            # yfinance returns a MultiIndex when fetching a single ticker with
            # auto_adjust — flatten by grabbing the first level.
            open_  = float(row["Open"].iloc[0]   if hasattr(row["Open"],   "iloc") else row["Open"])
            high   = float(row["High"].iloc[0]   if hasattr(row["High"],   "iloc") else row["High"])
            low    = float(row["Low"].iloc[0]    if hasattr(row["Low"],    "iloc") else row["Low"])
            close  = float(row["Close"].iloc[0]  if hasattr(row["Close"],  "iloc") else row["Close"])
            volume = float(row["Volume"].iloc[0] if hasattr(row["Volume"], "iloc") else row["Volume"])

            # Set timestamp to midnight UTC for the bar date — close-of-day is
            # implicit; the store reader handles the full-day range.
            ts = datetime(
                row_date.year, row_date.month, row_date.day,
                tzinfo=UTC,
            )
            bars.append(OHLCBar(
                timestamp=ts,
                open=open_, high=high, low=low, close=close, volume=volume,
            ))

        return bars

    async def _market_meta(ticker: str, *, start, end):
        """One fundamentals snapshot captured at the window end.

        Fundamentals change slowly, so a single snapshot at the close of the
        last window day is sufficient.  Returns a list of ``(StockStats,
        as_of_date)`` tuples so the caller can handle the no-data case
        uniformly.

        Parameters
        ----------
        ticker:
            Equity symbol.
        start, end:
            Window date range (only ``end`` is used here).

        Returns
        -------
        list[tuple[StockStats, date]]
            Zero or one element.
        """
        as_of = datetime.combine(end, _CLOSE_TIME, tzinfo=_NY)
        snap  = await get_stock_stats(ticker, as_of=as_of)
        return [(snap, end)] if snap is not None else []

    async def _news(ticker: str, *, start, end):
        """Fetch news articles published up to the window end.

        Parameters
        ----------
        ticker:
            Equity symbol.
        start, end:
            Window date range.

        Returns
        -------
        list[NewsArticle]
        """
        as_of = datetime.combine(end, _CLOSE_TIME, tzinfo=_NY)
        return await get_stock_news(ticker, as_of=as_of)

    async def _filings(ticker: str, *, start, end):
        """Fetch SEC filings visible as of the window end.

        Parameters
        ----------
        ticker:
            Equity symbol.
        start, end:
            Window date range.

        Returns
        -------
        list[Filing]
        """
        as_of = datetime.combine(end, _CLOSE_TIME, tzinfo=_NY)
        return await get_company_filings(ticker, as_of=as_of)

    async def _insider(ticker: str, *, start, end):
        """Fetch insider Form 4 trades filed up to the window end.

        Parameters
        ----------
        ticker:
            Equity symbol.
        start, end:
            Window date range.

        Returns
        -------
        list[InsiderTrade]
        """
        as_of = datetime.combine(end, _CLOSE_TIME, tzinfo=_NY)
        return await get_insider_trades(ticker, as_of=as_of)

    async def _politician(ticker: str, *, start, end):
        """Fetch STOCK Act politician trade disclosures up to the window end.

        Parameters
        ----------
        ticker:
            Equity symbol.
        start, end:
            Window date range.

        Returns
        -------
        list[PoliticianTrade]
        """
        as_of = datetime.combine(end, _CLOSE_TIME, tzinfo=_NY)
        return await get_public_figure_trades(ticker, as_of=as_of)

    async def _holders(ticker: str, *, start, end):
        """Fetch 13D/13G notable holders disclosed up to the window end.

        Parameters
        ----------
        ticker:
            Equity symbol.
        start, end:
            Window date range.

        Returns
        -------
        list[NotableHolder]
        """
        as_of = datetime.combine(end, _CLOSE_TIME, tzinfo=_NY)
        return await get_notable_holders(ticker, as_of=as_of)

    return {
        "ohlcv":             _ohlcv,
        "market_meta":       _market_meta,
        "news":              _news,
        "filings":           _filings,
        "insider_trades":    _insider,
        "politician_trades": _politician,
        "notable_holders":   _holders,
    }


def _resolve_watchlist(args: argparse.Namespace) -> list[str]:
    """Determine the ticker list from CLI args or config files.

    Priority:
    1. ``--tickers`` comma-separated inline list
    2. ``--watchlist`` path to a JSON file with a ``"tickers"`` key
    3. Default ``config/watchlist.json``

    Parameters
    ----------
    args:
        Parsed CLI arguments.

    Returns
    -------
    list[str]
        Upper-cased ticker symbols.
    """
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    path = Path(args.watchlist) if args.watchlist else Path("config/watchlist.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [t.upper() for t in data["tickers"]]


async def _main_async(args: argparse.Namespace) -> None:
    """Resolve config, build the fetcher, and run it.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    """
    settings = json.loads(
        Path("config/backtest_settings.json").read_text(encoding="utf-8")
    )
    windows  = load_windows(Path("config/backtest_windows.json"))

    if args.window not in windows:
        raise SystemExit(
            f"Unknown window key: {args.window!r}. "
            f"Available: {sorted(windows)}"
        )

    window    = windows[args.window]
    watchlist = _resolve_watchlist(args)

    logging.info(
        "Starting cache fill: window=%s (%s → %s), tickers=%d",
        args.window, window.start, window.end, len(watchlist),
    )

    store = CachedDataStore(Path(settings["cache_path"]))

    # Record the provider name for each domain in the audit log.  ``market_meta``
    # maps to the ``stats`` provider; ``ohlcv`` is always yfinance direct.
    live_for_domain = {
        "ohlcv":             "yfinance",
        "market_meta":       "yfinance",
        "news":              "finnhub",
        "filings":           "edgar",
        "insider_trades":    "edgar",
        "politician_trades": "fmp",
        "notable_holders":   "edgar",
    }

    fetcher = Fetcher(
        store=store,
        window_key=args.window,
        window=window,
        watchlist=watchlist,
        provider_fns=_build_provider_fns(),
        live_providers_for_domain=live_for_domain,
    )

    await fetcher.run()

    logging.info("Cache fill complete for window=%s", args.window)


def main() -> None:
    """CLI entrypoint — configure logging and parse args before delegating."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Fill the backtest cache for one window.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--window",
        required=True,
        help="Window key in config/backtest_windows.json (e.g. svb-stress-2023-03)",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ticker overrides (e.g. AAPL,MSFT). "
             "Overrides --watchlist and config/watchlist.json.",
    )
    parser.add_argument(
        "--watchlist",
        default=None,
        help="Path to a JSON file with a 'tickers' list. "
             "Defaults to config/watchlist.json.",
    )

    asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

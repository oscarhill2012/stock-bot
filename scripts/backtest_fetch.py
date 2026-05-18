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
from datetime import datetime, time, timedelta
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


# ---------------------------------------------------------------------------
# Per-domain lookback that the live analysts request at replay time.
#
# Mirrored verbatim from the analyst modules so the fetcher's window-coverage
# arithmetic can guarantee that, at every tick T in the replay window, the
# cache holds every row the analyst will ask for in (T - lookback, T].
#
# Without this, a tick at window-start would receive a strict subset of the
# rows the live analyst would have seen — silently degrading news / insider /
# politician verdicts for the first ~lookback days of the replay.
#
# Currently a duplicate of the analyst constants (cited inline below).  The
# long-run fix is B30 in docs/superpowers/backlog.md — make config/data.json
# the single source of truth for both analysts and the fetcher, then delete
# this dict.
# ---------------------------------------------------------------------------
_ANALYST_LOOKBACK_DAYS: dict[str, int] = {
    "news":              7,   # src/data/__init__.py:188 (get_stock_news default; news analyst passes no kwargs)
    "insider_trades":    30,  # src/agents/analysts/fundamental/fetch.py:53 — _INSIDER_LOOKBACK_DAYS
    "politician_trades": 30,  # src/agents/analysts/smart_money/fetch.py:38 — POLITICIAN_LOOKBACK_DAYS
    # notable_holders: 90 — already covered by the get_notable_holders default
    #   (180d) which the _notable_holders fetcher inherits at as_of=window_end.
    # filings: covered by the live provider's own filing-cycle default at
    #   as_of=window_end (10-K annual / 10-Q quarterly cadence).
}


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


def _build_provider_fns(warmup_days: int = 30) -> dict:
    """Return the domain → public-wrapper fetch-function map for the Fetcher.

    Each function has the signature ``async fn(ticker, *, start, end)`` and
    delegates to the matching ``data.get_*`` wrapper.  Whatever provider is
    active in ``config/data.json`` is used automatically — switching is a
    config-only operation.

    Parameters
    ----------
    warmup_days:
        Number of extra calendar days of OHLCV history to include *before*
        the window start.  Rolling indicators such as RSI(14), ATR(14), and
        pct_change_20d need at least this many bars of prior history to
        produce valid values on the first tick; without them the technical
        extractor's no-data heuristic fires for the whole window.

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
        """Pull max-period history through the active price-history provider, then slice.

        The lower bound is extended by ``warmup_days`` so that rolling
        indicators (RSI(14), ATR(14), pct_change_20d) have enough bars of
        prior history to compute correctly on the first replay tick.  Without
        this buffer the technical extractor trips its no-data heuristic for
        the entire window.
        """
        warmup_start = start - timedelta(days=warmup_days)
        history = await get_price_history(
            ticker, period="max", interval="1d", as_of=_as_of_close(end),
        )
        # Include warm-up bars (before `start`) so indicators can initialise,
        # but cap at the window end — bars after `end` are not PIT-safe.
        return [bar for bar in history.bars if warmup_start <= bar.timestamp.date() <= end]

    async def _company_ratios(ticker: str, *, start, end) -> list:
        """Fan out quarter-end as_ofs across the window for PIT-correct snapshots."""
        return await _fill_quarterly_ratios(ticker, start, end)

    async def _news(ticker: str, *, start, end) -> list:
        """Fetch news articles for the window plus the analyst's prior-context lookback.

        ``from_date`` is extended backward by
        ``_ANALYST_LOOKBACK_DAYS["news"]`` so the very-first replay tick can
        still serve the news analyst's default 7-day historical window
        (``get_stock_news`` extends ``as_of`` back by 7 days when the caller
        passes no ``from_date``).  Without the extension the first ~7 trading
        days of the replay see an empty news pane regardless of what was
        actually published in the run-up to the window.
        """
        pre_window_buffer = timedelta(days=_ANALYST_LOOKBACK_DAYS["news"])
        return await get_stock_news(
            ticker,
            from_date=start - pre_window_buffer,
            to_date=end,
            as_of=_as_of_close(end),
        )

    async def _filings(ticker: str, *, start, end) -> list:
        """Fetch SEC filings filed on or before window-close."""
        return await get_company_filings(ticker, as_of=_as_of_close(end))

    async def _insider_trades(ticker: str, *, start, end) -> list:
        """Fetch Form 4 insider trades for the window plus the analyst's lookback.

        Lookback formula:
            window-span (in days) + ``_ANALYST_LOOKBACK_DAYS["insider_trades"]``

        The window-span piece covers every tick in the replay; the analyst
        piece extends coverage backwards from window-start so the very-first
        tick can still serve the analyst's full 30-day lookback request
        (otherwise rows filed within ``[window_start - 30, window_start]``
        would be absent from the cache).

        The edgar provider returns a ``Form4Bundle`` containing both
        ``trades`` and ``derivatives``, but the cache's
        ``write_insider_trades`` writer only persists the trades list —
        there is no cache writer for derivatives yet.  Unwrap the bundle
        here so the fetcher hands the writer the expected
        ``list[InsiderTrade]`` shape.
        """
        lookback = (end - start).days + _ANALYST_LOOKBACK_DAYS["insider_trades"]
        bundle = await get_insider_trades(
            ticker, lookback_days=lookback, as_of=_as_of_close(end),
        )
        return bundle.trades

    async def _politician_trades(ticker: str, *, start, end) -> list:
        """Fetch congressional/politician trades for the window plus the analyst's lookback.

        Lookback formula:
            window-span (in days) + ``_ANALYST_LOOKBACK_DAYS["politician_trades"]``

        Same rationale as ``_insider_trades``: the analyst-side lookback piece
        extends coverage backwards from window-start so the smart-money
        analyst's first-tick request is fully served by the cache.
        """
        lookback = (end - start).days + _ANALYST_LOOKBACK_DAYS["politician_trades"]
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
        # politician_trades intentionally disabled — there is no free
        # historical source (FMP's senate/house endpoints require a paid
        # tier; Quiver's historic data is also paid).  The smart_money
        # analyst already degrades gracefully when politician data is
        # absent (see src/agents/analysts/smart_money/fetch.py:88-93),
        # so the fill skips the domain entirely rather than logging a
        # 403 per ticker.  The `_politician_trades` provider function is
        # retained as a placeholder — re-enable by uncommenting the line
        # below once a working free provider lands.
        # "politician_trades": _politician_trades,
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
# Reference-symbol OHLCV fill
# ---------------------------------------------------------------------------

# Mirrors orchestrator.tick._REFERENCE_SYMBOLS exactly.  SPY is the broad-market
# benchmark; the 11 SPDR sector ETFs cover every S&P 500 constituent sector.
# Kept as a module-level constant so tests and runner.py can import it if needed.
_REFERENCE_SYMBOLS: tuple[str, ...] = (
    "SPY",                                            # broad market
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",       # SPDR sector ETFs (batch 1)
    "XLI", "XLB", "XLRE", "XLU", "XLC",              # SPDR sector ETFs (batch 2)
)


async def _fill_reference_ohlcv(
    *,
    store,
    window,
    warmup_days: int,
) -> None:
    """Fetch and cache OHLCV bars for SPY and SPDR sector ETFs.

    These symbols are not in the watchlist but are required by the technical
    extractor to compute ``relative_strength_vs_spy_*`` and
    ``relative_strength_vs_sector_*`` features.  They need OHLCV only — no
    other domains.  Bars are written via ``store.write_ohlcv`` using the same
    warm-up extension applied to watchlist tickers.

    Errors per symbol are logged and skipped so a delisted ETF does not abort
    the entire fill.

    Parameters
    ----------
    store:
        Open ``CachedDataStore`` instance (already has the DB connection).
    window:
        Resolved ``BacktestWindow`` with ``.start`` and ``.end`` date attrs.
    warmup_days:
        Extra calendar days before ``window.start`` to include for indicator
        warm-up (same value used for the main watchlist fill).
    """
    from data import get_price_history

    warmup_start = window.start - timedelta(days=warmup_days)

    for symbol in _REFERENCE_SYMBOLS:
        try:
            history = await get_price_history(
                symbol,
                period="max",
                interval="1d",
                as_of=_as_of_close(window.end),
            )
            bars = [
                b for b in history.bars
                if warmup_start <= b.timestamp.date() <= window.end
            ]
            if bars:
                store.write_ohlcv(symbol, bars)
                logging.info(
                    "Reference OHLCV: %s — %d bars written (warmup from %s to %s)",
                    symbol, len(bars), warmup_start, window.end,
                )
            else:
                logging.warning(
                    "Reference OHLCV: %s — no bars found in [%s, %s]",
                    symbol, warmup_start, window.end,
                )
        except Exception:
            logging.exception(
                "Reference OHLCV fetch failed for %s — skipping", symbol,
            )


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

    # Read warm-up days from settings; fall back to 30 if absent (safe default
    # that covers RSI(14), ATR(14), and pct_change_20d's longest lookback).
    warmup_days: int = settings.get("ohlcv_warmup_days", 30)

    store = CachedDataStore(Path(settings["cache_path"]))

    fetcher = Fetcher(
        store=store,
        window_key=args.window,
        window=window,
        watchlist=watchlist,
        provider_fns=_build_provider_fns(warmup_days=warmup_days),
        live_providers_for_domain=_build_provider_name_map(),
        refetch_domains=set(args.refetch_domain),
    )

    logging.info(
        "Starting cache fill: window=%s tickers=%d domains=%d",
        args.window,
        len(watchlist),
        len(_build_provider_fns()),
    )

    await fetcher.run()

    # ── Reference-symbol OHLCV fill ────────────────────────────────────────────
    # SPY and the 11 SPDR sector ETFs are not in the watchlist, but the
    # technical extractor needs their price history to compute
    # relative_strength_vs_spy_* and relative_strength_vs_sector_* features.
    # Fetch and cache them now using the same warm-up extension as the main fill.
    # Only OHLCV is needed — no other domains.
    await _fill_reference_ohlcv(
        store=store,
        window=window,
        warmup_days=warmup_days,
    )

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
    parser.add_argument(
        "--refetch-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help=(
            "Force re-fetch of the named domain even when cache_runs has "
            "status='ok'.  Pass multiple times to refetch several domains, "
            "e.g. --refetch-domain news --refetch-domain filings."
        ),
    )

    asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

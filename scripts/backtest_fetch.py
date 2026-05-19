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



async def _fill_per_tick_ratios(ticker: str, start, end) -> list:
    """Fetch one ``CompanyRatios`` snapshot per NYSE trading day in ``[start, end]``.

    Live-equivalent semantics: every live tick calls
    ``get_company_ratios(ticker, as_of=now())``, which dispatches to the active
    company_ratios provider and returns the most-recent filing's XBRL facts
    plus price-derived fields current to that moment.  The cache pre-records
    the same call offline, once per trading day at market close, so replay
    returns the snapshot a live agent would have seen.

    Implementation notes
    --------------------
    - The session schedule (and especially early-close days) comes from
      ``pandas_market_calendars`` — same source the tick generator uses — so
      cache-fill aligns exactly with the ticks the driver later emits.
    - Close-of-day is the chosen as_of moment because the
      ``CompanyRatiosRow`` table is keyed by ``(ticker, as_of_date)``; two
      same-date snapshots (e.g. one at open + one at close) would collide on
      the primary key.  At replay, ``read_company_ratios`` returns the latest
      snapshot with ``as_of_date <= query.date()``, so a close-of-day row
      correctly serves both the open and close ticks of the next session and
      the close tick of its own session.

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
        One ``(snapshot, trading_date)`` entry per NYSE session in
        ``[start, end]``.  Empty when the window contains no NYSE sessions
        (e.g. the window is a single weekend day).
    """
    import pandas_market_calendars as mcal

    from data import get_company_ratios

    # NYSE is the only calendar used elsewhere in the harness — see
    # backtest/schedule.py for the rationale.  Importing the calendar here
    # rather than reusing schedule.generate_ticks keeps the cache-fill
    # decoupled from the ``ticks_per_day`` setting (which is a replay-time
    # concern, not a fill-time one — we always want one snapshot per session
    # at fill).
    nyse  = mcal.get_calendar("NYSE")
    sched = nyse.schedule(start_date=start, end_date=end)

    out: list = []

    for _, row in sched.iterrows():
        # ``market_close`` is a tz-aware pandas Timestamp in
        # America/New_York; ``to_pydatetime()`` preserves the tz.  On
        # early-close days the calendar already encodes the truncated time.
        close_dt = row["market_close"].to_pydatetime()

        snapshot = await get_company_ratios(
            ticker,
            period   = "max",
            interval = "1d",
            as_of    = close_dt,
        )
        out.append((snapshot, close_dt.date()))

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
        """One PIT snapshot per NYSE trading day — live-equivalent semantics.

        See ``_fill_per_tick_ratios`` for the rationale.  In short: live
        calls ``get_company_ratios`` every tick, so the cache pre-records
        the same call once per trading session at close.
        """
        return await _fill_per_tick_ratios(ticker, start, end)

    async def _news(ticker: str, *, start, end) -> list:
        """Fetch news articles for the window plus the analyst's prior-context lookback.

        ``from_date`` is extended backward by ``defaults.news_lookback_days``
        (read from ``config/data.json``) so the very-first replay tick can
        still serve the news analyst's default lookback window.  Without the
        extension the first ~N trading days of the replay see an empty news
        pane regardless of what was actually published in the run-up to the
        window.

        ``limit`` is set explicitly to ``2000`` (rather than relying on the
        dispatcher's default of ``50``) because the dispatcher's cap is sized
        for live ticks — at backtest cache-fill time we want to preserve the
        full chunked Finnhub pull across the whole window.  Without the
        override, a high-volume ticker (MSFT, AAPL) whose per-week chunk
        already returns 200+ articles would have its earliest weeks
        discarded by the newest-first ``[:50]`` slice in the provider, and
        replay ticks near the start of the window would see an effectively
        empty news pane.  ``2000`` is generous enough to absorb six weeks of
        even the noisiest names while still capping memory in pathological
        cases; per-tick analysts still serve their usual 20-article slice
        from the cache.
        """
        from data.config import get_config
        pre_window_buffer = timedelta(days=get_config().defaults.news_lookback_days)
        return await get_stock_news(
            ticker,
            from_date=start - pre_window_buffer,
            to_date=end,
            as_of=_as_of_close(end),
            limit=2000,
        )

    async def _filings(ticker: str, *, start, end) -> list:
        """Fetch SEC filings filed on or before window-close.

        ``limit`` (``filings_per_form``) and ``include_excerpts``
        (``include_filing_excerpts``) are sourced from ``config/data.json``
        so the cache-fill and the live tick agree on row counts and excerpt
        attachment.  Without this, the dispatcher's hardcoded defaults
        (``limit=5``, ``include_excerpts=True``) would silently override the
        configured values.  ``filings_lookback_days`` is consumed inside
        ``get_company_filings`` itself, so the caller does not forward it
        directly.
        """
        from data.config import get_config
        defaults = get_config().defaults
        return await get_company_filings(
            ticker,
            as_of=_as_of_close(end),
            limit=defaults.filings_per_form,
            include_excerpts=defaults.include_filing_excerpts,
        )

    async def _insider_trades(ticker: str, *, start, end) -> list:
        """Fetch Form 4 insider trades for the window plus the analyst's lookback.

        Lookback formula:
            window-span (in days) + ``defaults.insider_lookback_days``
            (sourced from ``config/data.json``)

        The window-span piece covers every tick in the replay; the analyst
        piece extends coverage backwards from window-start so the very-first
        tick can still serve the analyst's full lookback request
        (otherwise rows filed within ``[window_start - lookback, window_start]``
        would be absent from the cache).

        The edgar provider returns a ``Form4Bundle`` containing both
        ``trades`` and ``derivatives``, but the cache's
        ``write_insider_trades`` writer only persists the trades list —
        there is no cache writer for derivatives yet.  Unwrap the bundle
        here so the fetcher hands the writer the expected
        ``list[InsiderTrade]`` shape.
        """
        from data.config import get_config
        lookback = (end - start).days + get_config().defaults.insider_lookback_days
        bundle = await get_insider_trades(
            ticker, lookback_days=lookback, as_of=_as_of_close(end),
        )
        return bundle.trades

    async def _politician_trades(ticker: str, *, start, end) -> list:
        """Fetch congressional/politician trades for the window plus the analyst's lookback.

        Lookback formula:
            window-span (in days) + ``defaults.politician_lookback_days``
            (sourced from ``config/data.json``)

        Same rationale as ``_insider_trades``: the analyst-side lookback piece
        extends coverage backwards from window-start so the smart-money
        analyst's first-tick request is fully served by the cache.
        """
        from data.config import get_config
        lookback = (end - start).days + get_config().defaults.politician_lookback_days
        return await get_public_figure_trades(
            ticker, lookback_days=lookback, as_of=_as_of_close(end),
        )

    async def _notable_holders(ticker: str, *, start, end) -> list:
        """Fetch SC-13D/13G/13F filings filed before window-close.

        ``lookback_days`` (``notable_holder_lookback_days``) and ``limit``
        (``notable_holder_limit``) are sourced from ``config/data.json`` so
        the cache-fill matches the live tick.  Without this, the
        dispatcher's hardcoded defaults (``lookback_days=180``, ``limit=20``)
        would silently override the configured values.
        """
        from data.config import get_config
        defaults = get_config().defaults
        return await get_notable_holders(
            ticker,
            lookback_days=defaults.notable_holder_lookback_days,
            limit=defaults.notable_holder_limit,
            as_of=_as_of_close(end),
        )

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
        #
        # notable_holders intentionally disabled (2026-05-19) — the
        # edgartools-backed provider issues ``Company(symbol).get_filings()``
        # which returns filings where ``symbol`` is the *filer* (the issuer
        # disclosing its own 10-K / 10-Q / 8-K).  For SC 13D / 13G the
        # canonical query needs ``symbol`` as the *subject* (the company
        # being held), which edgartools does not expose.  Past fills wrote
        # rows where the "holder" was the issuer itself (e.g. JPM rows with
        # holder=JPMORGAN CHASE & CO), so the data is misleading rather
        # than merely sparse.  The smart_money analyst is also shelved in
        # ``orchestrator.pipeline._build_analyst_pool`` while this is out
        # — re-enable both together once a subject-side provider lands.
        # "notable_holders":   _notable_holders,
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
    from backtest.settings import get_backtest_settings
    from backtest.windows import load_windows

    settings  = get_backtest_settings()
    watchlist_path = Path(args.watchlist)
    watchlist = json.loads(watchlist_path.read_text())["tickers"]

    windows = load_windows(Path("config/backtest_windows.json"))
    if args.window not in windows:
        raise SystemExit(
            f"Unknown window key '{args.window}'. "
            f"Available: {sorted(windows)}"
        )
    window = windows[args.window]

    # Read warm-up days from settings — already validated by BacktestSettings.
    warmup_days: int = settings.ohlcv_warmup_days

    # Per-window cache lives at ``<backtests_root>/<window>/store.sqlite``.
    # Ensure the parent directory exists before opening so a clean repo can
    # be fetched into without manual ``mkdir``.
    from backtest.settings import cache_path_for_window
    cache_path = cache_path_for_window(settings, args.window)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    store = CachedDataStore(cache_path)

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

    # ── Quieten chatty libraries ────────────────────────────────────────────
    # These libraries log at INFO level for routine internals (edgar
    # repeats "Identity of the Edgar REST client set to..." on every fetch;
    # yfinance / urllib3 emit per-request lines).  Drop them to WARNING so
    # the fetcher's own progress lines are readable.
    for noisy in ("edgar", "edgar.core", "yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

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

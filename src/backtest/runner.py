"""End-to-end run orchestrator: window + watchlist → cache wiring → driver.

One ``Runner`` instance can execute multiple windows sequentially.  Call
``.run(window_key)`` once per window; it:

1. Resolves the window dates and materialises a fresh run directory under
   ``<runs_root>/<run-id>/``.
2. Opens the golden cache store and installs it as the global singleton.
3. Swaps every data domain to its ``"cache"`` provider for the duration of
   the run (restored in ``finally``).
4. Pre-flights the watchlist — drops tickers with no OHLCV bars in the window.
5. Creates a per-run SQLite DB (isolated from ``data/stockbot.db``).
6. Constructs a ``Driver`` and hands it the generated tick schedule.
7. Returns a ``RunResult`` with the run ID, run directory, and final status.

Adaptation notes vs plan:
- ``stats_cache`` import in the plan does not exist — the ``stats`` domain was
  retired in Phase 5 and split into ``price_history`` and ``company_ratios``.
  The actual provider modules are imported explicitly below.
- All eight domains now have a ``"cache"`` provider (including
  ``social_sentiment`` which unconditionally returns ``None``); all are set to
  ``"cache"`` for the backtest run.
- ``make_engine`` / ``create_all`` live in ``orchestrator.persistence``
  (not a separate ``persistence`` package).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from backtest.cache.store import CachedDataStore
from backtest.decision_logger import DecisionLogger
from backtest.driver import Driver

# Importing each cache-provider module triggers its ``@register`` decorator,
# making the ``"cache"`` name available to ``data.registry.dispatch``.
from backtest.providers import (  # noqa: F401
    _store_handle,
    company_ratios_cache,
    filings_cache,
    insider_trades_cache,
    news_cache,
    notable_holders_cache,
    politician_trades_cache,
    price_history_cache,
    social_sentiment_cache,
)
from backtest.schedule import generate_ticks
from backtest.settings import BacktestSettings
from backtest.windows import load_windows
from broker.fake import FakeBroker
from data.registry import DOMAINS, set_active_provider
from orchestrator.persistence import create_all, make_engine

logger = logging.getLogger(__name__)


def _seed_reference_prices(
    *,
    store,
    window_start: date,
    window_end: date,
    as_of: datetime | None = None,
) -> dict:
    """Build ``state["reference_prices"]`` from cached SPY + sector ETF bars.

    Mirrors what ``orchestrator.tick._fetch_reference_prices`` does on live
    runs — returns a ``{symbol: PriceHistory}`` dict so the technical
    extractor can compute ``relative_strength_vs_spy_*`` and
    ``relative_strength_vs_sector_*`` features during backtest replay.

    Phase 2 PIT contract: when ``as_of`` is supplied, any bar whose
    ``timestamp`` exceeds ``as_of`` is stripped before the ``PriceHistory``
    is constructed.  This ensures the tick-scoped reference data is
    lookahead-free — a bar for 2026-05-08 must not appear at the
    2026-05-07 13:30 tick boundary.

    ``as_of=None`` preserves legacy Phase 1 behaviour (full window returned
    unfiltered).  The Phase 1 seed call in ``Runner._run_async`` uses
    ``as_of=None`` and is kept as a no-op safety net — the per-tick refresh
    in ``Driver.run`` (Phase 2) overwrites ``state["reference_prices"]``
    before any analyst callback fires.

    Bars are read over the full window (including warm-up bars written by
    ``scripts.backtest_fetch._fill_reference_ohlcv``).  Symbols absent from
    the cache are silently omitted — the extractor already handles a missing
    ``"SPY"`` key by skipping those features.

    Parameters
    ----------
    store:
        Open ``CachedDataStore`` instance.
    window_start:
        Inclusive lower-bound date for the OHLCV read.
    window_end:
        Inclusive upper-bound date for the OHLCV read.
    as_of:
        Optional point-in-time boundary.  When supplied, any bar with
        ``timestamp > as_of`` is excluded from the result.  ``None``
        disables the filter (legacy Phase 1 callers).

    Returns
    -------
    dict[str, PriceHistory]
        One ``PriceHistory`` per reference symbol found in the cache,
        with bars clamped to ``as_of`` when provided.
    """
    from data.models import PriceHistory
    from data.reference_symbols import REFERENCE_SYMBOLS

    ref: dict = {}

    for symbol in REFERENCE_SYMBOLS:
        bars = store.read_ohlcv(symbol, window_start, window_end)

        # Phase 2 PIT clamp — strip bars that postdate the current tick
        # boundary.  Without this, a per-tick call at 2026-05-07 13:30
        # would serve the full window including bars for 2026-05-08+,
        # introducing future-data lookahead into every relative-strength
        # computation for the remainder of the window.
        #
        # SQLite returns naive datetimes (no tzinfo) for DateTime columns.
        # All OHLCV bars are stored as midnight UTC, so we treat any
        # timezone-naive bar timestamp as UTC when comparing against the
        # (always timezone-aware) ``as_of``.  This avoids a
        # TypeError: can't compare offset-naive and offset-aware datetimes.
        if as_of is not None:
            def _as_utc(ts: datetime) -> datetime:
                """Attach UTC tzinfo to a naive datetime; pass through aware ones."""
                return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts

            bars = [b for b in bars if _as_utc(b.timestamp) <= as_of]

        if bars:
            ref[symbol] = PriceHistory(ticker=symbol, bars=bars)

    return ref


def _seed_initial_prices(
    *,
    store,
    tickers: list[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, float]:
    """Return a ``{ticker: price}`` map for FakeBroker bootstrap.

    For each ticker we read the OHLCV slice for the full backtest window
    and take the *first* bar's close price.  Tickers with no bar in the
    window keep ``0.0`` — this preserves the previous behaviour for
    genuinely-absent symbols but eliminates the artefact at tick 1 for
    every ticker that does have data.

    Parameters
    ----------
    store :
        Any object exposing ``read_ohlcv(ticker, start, end) -> list[bar]``
        where each ``bar`` has a ``close`` attribute.
    tickers :
        Watchlist tickers to seed.
    window_start, window_end :
        Inclusive backtest window bounds.

    Returns
    -------
    dict[str, float]
        Seed prices for FakeBroker construction.
    """

    prices: dict[str, float] = {}

    for ticker in tickers:
        bars = store.read_ohlcv(ticker, window_start, window_end)
        prices[ticker] = float(bars[0].close) if bars else 0.0

    return prices


@dataclass
class RunResult:
    """Summary of one backtest run, returned to the CLI.

    Attributes
    ----------
    run_id:
        Stable identifier for this run (``<window-key>-<git-sha7>``).
    run_dir:
        Absolute path to the run artefact directory.
    status:
        Final run status as written to ``manifest.json`` — one of
        ``"completed"``, ``"completed_with_failures"``, or ``"aborted"``.
    """

    run_id:  str
    run_dir: Path
    status:  str


class Runner:
    """One end-to-end backtest run orchestrator.

    Parameters
    ----------
    settings:
        Optional pre-loaded ``BacktestSettings`` instance.  When ``None``, the
        singleton from ``backtest.settings.get_backtest_settings()`` is used.
        Tests inject a sandboxed instance here to avoid touching real config files.
    windows_path:
        Path to ``config/backtest_windows.json``.
    watchlist_path:
        Path to ``config/watchlist.json``.
    """

    def __init__(
        self,
        *,
        settings:       BacktestSettings | None = None,
        windows_path:   Path                    = Path("config/backtest_windows.json"),
        watchlist_path: Path                    = Path("config/watchlist.json"),
    ) -> None:
        """Load config files; defer actual run setup to ``.run()``.

        Parameters
        ----------
        settings:
            Optional pre-loaded ``BacktestSettings``.  When ``None``, the
            singleton from ``backtest.settings.get_backtest_settings()`` is
            used.  Tests inject a sandboxed instance here.
        windows_path:
            Path to ``config/backtest_windows.json``.
        watchlist_path:
            Path to ``config/watchlist.json``.
        """
        # Local imports kept inside ``__init__`` so importing this module
        # stays cheap (no settings parse, no .env read at import time).
        from dotenv import load_dotenv

        from backtest.settings import get_backtest_settings

        # Load ``.env`` here as well as in ``scripts/backtest_run.py`` so
        # programmatic callers (integration tests, ad-hoc scripts that import
        # ``Runner`` directly) also get LLM / API credentials into the env
        # before the pipeline starts building agents.  In a cache-only
        # backtest the lazy load in ``data.secrets._ensure_loaded`` is never
        # triggered, so without this call ADK's Vertex AI client cannot find
        # ``GOOGLE_GENAI_USE_VERTEXAI`` / ``GOOGLE_CLOUD_PROJECT`` and the
        # tick aborts with "No API key was provided".  ``load_dotenv`` is
        # idempotent and respects pre-existing env vars (override=False).
        load_dotenv()

        self._settings  = settings if settings is not None else get_backtest_settings()
        self._windows   = load_windows(Path(windows_path))
        self._watchlist = json.loads(Path(watchlist_path).read_text())["tickers"]

    @staticmethod
    def _runs_root_from_config(window: str) -> Path:
        """Return the per-window runs directory from the active backtest settings.

        Convenience helper for scripts that need to locate an existing run
        directory without constructing a full ``Runner`` instance.  Resolves
        to ``<backtests_root>/<window>/runs/`` under the per-window layout.

        Parameters
        ----------
        window:
            Window key (e.g. ``"svb-stress-2023-03"``) — required because
            each window has its own runs subtree.

        Returns
        -------
        Path
            The per-window runs directory (not guaranteed to exist).
        """
        from backtest.settings import get_backtest_settings, runs_root_for_window

        return runs_root_for_window(get_backtest_settings(), window)

    def run(
        self,
        window_key: str,
        watchlist: list[str] | None = None,
        *,
        tick_limit:      int | None = None,
        run_id_override: str | None = None,
        fresh:           bool = False,
    ) -> RunResult:
        """Materialise the run, drive every tick, return a ``RunResult``.

        Parameters
        ----------
        window_key:
            Era slug matching a key in ``config/backtest_windows.json``
            (e.g. ``"svb-stress-2023-03"``).
        watchlist:
            Optional override list of ticker symbols.  Defaults to the full
            watchlist from ``config/watchlist.json``.
        tick_limit:
            Optional cap on the number of ticks to execute — the generated
            tick schedule is sliced to ``[:tick_limit]`` before being handed
            to the driver.  Used by trial / sanity-check runs.  ``None``
            (the default) runs every scheduled tick.
        run_id_override:
            Optional human-readable name to use as the run-id (and therefore
            the artefact directory) instead of the default
            ``<window>-<git-sha7>``.  Lets sanity runs land in a predictable
            location like ``runs/trial-run/`` rather than under a SHA.
        fresh:
            When ``True``, delete ``runs/<run-id>/session.sqlite`` before the
            run begins so the new run starts from an empty ``user_state`` row.
            Prevents prior-run thesis from leaking into a re-run of the same
            window.  Has no effect when there is no existing session file.

        Returns
        -------
        RunResult
            Summary with ``run_id``, ``run_dir``, and ``status``.
        """
        import asyncio
        return asyncio.run(
            self._run_async(
                window_key,
                watchlist,
                tick_limit      = tick_limit,
                run_id_override = run_id_override,
                fresh           = fresh,
            )
        )

    # ── private implementation ──────────────────────────────────────────────────

    async def _run_async(
        self,
        window_key: str,
        watchlist: list[str] | None,
        *,
        tick_limit:      int | None = None,
        run_id_override: str | None = None,
        fresh:           bool = False,
    ) -> RunResult:
        """Async implementation of the full run lifecycle."""
        # Belt-and-braces: scripts.backtest_run also sets this, but defending
        # in depth means a programmatic Runner.run caller can't accidentally
        # leak wall-clock time into the dataset.
        # Save the previous value so we can restore it in the outer finally
        # block — this prevents test-environment contamination when Runner is
        # invoked programmatically (e.g. in integration tests) without a full
        # process exit.  The capture and set are inside the try so that *any*
        # exception during pre-flight (cache open, provider swap, broker init,
        # engine setup) still triggers the restore, not just exceptions that
        # occur during the tick-loop itself.
        _prev_strict = os.environ.get("STOCKBOT_STRICT_AS_OF")
        try:
            os.environ["STOCKBOT_STRICT_AS_OF"] = "1"

            # Resolve per-window paths up front — every artefact for this
            # window lives under ``<backtests_root>/<window_key>/``.
            from backtest.settings import cache_path_for_window, runs_root_for_window

            window     = self._windows[window_key]
            wl         = list(watchlist or self._watchlist)
            # Default to ``<window>-<git-sha7>`` so concurrent runs can't
            # collide; a caller-supplied ``run_id_override`` wins outright
            # (used by trial / sanity runs that want a stable directory name).
            run_id     = run_id_override or f"{window_key}-{_git_sha7()}"
            runs_root  = runs_root_for_window(self._settings, window_key)
            run_dir    = runs_root / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            # ── --fresh cleanup ─────────────────────────────────────────────────
            # When ``fresh=True``, delete the per-run ADK session database before
            # the run starts so the new run cannot inherit prior-run thesis.  A
            # ``--fresh`` re-run of a window MUST begin with an empty
            # ``user_state`` row — otherwise ``user:positions`` / ``user:thesis``
            # from the previous run leak into tick 1.
            session_sqlite = run_dir / "session.sqlite"
            if fresh and session_sqlite.exists():
                session_sqlite.unlink()
                logger.info(
                    "--fresh: deleted %s so run %s starts with empty user_state",
                    session_sqlite, run_id,
                )

            # ── SIGINT / SIGTERM handler ────────────────────────────────────────
            # Registered here so we have ``run_dir`` in scope.  The handler writes
            # ``manifest.status = "interrupted"`` and re-raises ``KeyboardInterrupt``
            # so the process exits non-zero.  Previous handlers are restored in the
            # ``finally`` block below, whether the run completes normally or not.
            _interrupted: list[bool] = [False]  # mutable container for closure

            def _signal_handler(signum: int, frame: object) -> None:  # noqa: ANN001
                """Write interrupted manifest and propagate the interrupt signal."""
                if _interrupted[0]:
                    # Second signal — skip manifest update and raise immediately.
                    raise KeyboardInterrupt(f"signal {signum}")

                _interrupted[0] = True
                logger.warning(
                    "Run %s interrupted by signal %s — writing manifest", run_id, signum
                )
                try:
                    manifest_path = run_dir / "manifest.json"
                    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
                    manifest["status"]         = "interrupted"
                    manifest["interrupted_at"] = datetime.now(tz=UTC).isoformat()
                    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
                except Exception:
                    logger.exception("Failed to write interrupted manifest for %s", run_id)
                raise KeyboardInterrupt(f"signal {signum}")

            _prev_sigint  = signal.signal(signal.SIGINT,  _signal_handler)
            _prev_sigterm = signal.signal(signal.SIGTERM, _signal_handler)

            # ── open the golden cache store ─────────────────────────────────────
            # Per-window: ``<backtests_root>/<window>/store.sqlite``.  The
            # parent directory is created so a first-run fetch can land here
            # cleanly when the user hasn't pre-fetched.
            cache_path = cache_path_for_window(self._settings, window_key)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            store = CachedDataStore(cache_path)
            _store_handle.set_store(store)

            # ── pre-flight: drop tickers with no OHLCV in the window ───────────
            skipped:     list[str] = []
            wl_filtered: list[str] = []
            for ticker in wl:
                if store.read_ohlcv(ticker, window.start, window.end):
                    wl_filtered.append(ticker)
                else:
                    logger.warning(
                        "Skipping %s — no OHLCV bars in window [%s, %s]",
                        ticker, window.start, window.end,
                    )
                    skipped.append(ticker)

            # ── swap all domains to their cache providers ───────────────────────
            # Collect restore callables so a crashed run does not leak state into
            # a later live invocation or test that runs in the same process.
            restores: list = []
            for domain in DOMAINS:
                restores.append(set_active_provider(domain, "cache"))

            # ── broker, DB, decision logger ─────────────────────────────────────
            # Seed the broker with real close prices from the first available
            # OHLCV bar so tick-1 equity metrics are not artefactual.
            seed_prices = _seed_initial_prices(
                store=store,
                tickers=wl_filtered,
                window_start=window.start,
                window_end=window.end,
            )
            broker = FakeBroker(
                starting_cash=self._settings.fake_broker_starting_cash,
                prices=seed_prices,
            )

            # Each run gets its own SQLite file — never touches data/stockbot.db.
            engine     = make_engine(f"sqlite:///{run_dir / 'db.sqlite'}")
            create_all(engine)
            from sqlalchemy.orm import sessionmaker
            Session    = sessionmaker(bind=engine)
            db_session = Session()

            dl = DecisionLogger(
                output_dir=run_dir / "decisions",
                window_key=window_key,
            )

            # ── write initial manifest ──────────────────────────────────────────
            manifest: dict = {
                "run_id":           run_id,
                "window_key":       window_key,
                "window":           {"start": str(window.start), "end": str(window.end)},
                "watchlist":        wl_filtered,
                "skipped_tickers":  skipped,
                "git_sha":          _git_sha_full(),
                "started_at":       datetime.now(tz=UTC).isoformat(),
                "status":           "running",
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

            # ── build and run the driver ────────────────────────────────────────
            # Per-run SQLite for ADK session state.  Placed alongside the other
            # artefacts under ``runs/<run-id>/``.  aiosqlite driver is required
            # because DatabaseSessionService uses an async SQLAlchemy engine.
            session_sqlite_url = (
                f"sqlite+aiosqlite:///{run_dir / 'session.sqlite'}"
            )
            driver = Driver(
                broker=broker,
                run_id=run_id,
                run_dir=run_dir,
                window_key=window_key,
                session_db_url=session_sqlite_url,
                db_session=db_session,
                decision_logger=dl,
                failure_abort_ratio=self._settings.failed_tick_abort_ratio,
                # Forward settings so the driver can refresh
                # ``report/equity_curve.png`` + ``report/metrics.md`` at the
                # end of every tick (per-tick progress dashboard).  The
                # end-of-run ``report()`` call below still runs unconditionally
                # — it adds the forward-return backfill that ``report_progress``
                # deliberately skips.
                settings=self._settings,
            )
            schedule = generate_ticks(window.start, window.end)

            # Apply optional cap from trial / sanity runs — slice rather than
            # mutating the underlying generator so the schedule object stays a
            # plain list, which the driver iterates directly.
            if tick_limit is not None:
                schedule = schedule[:tick_limit]
                logger.info(
                    "tick_limit=%d in effect — executing %d of the scheduled ticks",
                    tick_limit, len(schedule),
                )

            # Seed the same initial state keys that ``orchestrator/tick.py``
            # provides on live runs.  The strategist prompt template references
            # ``{portfolio}`` directly (resolved by ADK's instruction-variable
            # machinery), and several before-callbacks read ``portfolio``,
            # ``memory_buffer``, and ``day_digest`` at the start of each tick.
            # Without these keys the ADK runner raises
            # ``KeyError: 'Context variable not found: portfolio'`` before the
            # pipeline can execute even one agent.
            portfolio = await broker.get_portfolio()

            # Populate reference_prices from the cache so the technical extractor
            # can compute relative_strength_vs_spy_* and
            # relative_strength_vs_sector_* features.  On live runs this is
            # done by orchestrator.tick._fetch_reference_prices (a yfinance
            # bulk-download); here we read from the golden-cache store instead.
            # SPY/ETF bars must have been written by backtest_fetch's
            # _fill_reference_ohlcv pass — absent symbols are silently omitted.
            reference_prices = _seed_reference_prices(
                store=store,
                window_start=window.start,
                window_end=window.end,
            )

            state: dict = {
                # A1.6 — ``tickers`` is the single canonical watchlist
                # key.  The previous duplicate ``watchlist`` seed has
                # been dropped; the driver now sources its per-tick
                # price refresh from ``state["tickers"]`` so live and
                # backtest agree on the same field.
                "tickers":          wl_filtered,
                "portfolio":        portfolio.model_dump(mode="json"),
                # ``positions`` is intentionally absent — it has migrated to
                # ``user:positions`` (Spec B, Band 4).  ADK's user_state merge
                # re-hydrates ``user:positions`` from the DatabaseSessionService
                # row on tick 2+; Band 4 will wire the Executor writer-of-record
                # to persist it there.
                #
                # ``thesis`` is intentionally absent — it has migrated to
                # ``user:thesis`` (Spec B, Band 2).  The strategist prompt
                # template resolves the optional placeholder ``{user:thesis?}``
                # directly from ``state["user:thesis"]`` (ADK's instruction-
                # variable machinery), yielding an empty string on cold start
                # when the key is absent — so no bare ``thesis`` seed is needed
                # here and the prior context-shim bridge has been removed.
                "memory_buffer":    [],
                "day_digest":       "",
                # Dump each PriceHistory to a JSON-safe dict so the ADK
                # SqlSessionService (plain json.dumps under the hood) doesn't
                # choke on Pydantic objects.  Mirrors orchestrator.tick.  The
                # technical extractor coerces dicts back to PriceHistory on
                # read — see src/contract/extractors/technical.py.
                "reference_prices": {
                    sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
                },
            }

            status = "completed"
            try:
                await driver.run(state, schedule)
            except RuntimeError as exc:
                logger.error("run %s aborted: %s", run_id, exc)
                status = "aborted"
            finally:
                # Restore live domain mappings and signal handlers regardless of
                # success, abort, or interrupt.  Always runs even if KeyboardInterrupt
                # propagates — the caller (CLI / asyncio.run) will exit non-zero.
                for restore in restores:
                    restore()
                _store_handle.clear_store()
                db_session.close()
                # Restore the signal handlers registered before this run started.
                signal.signal(signal.SIGINT,  _prev_sigint)
                signal.signal(signal.SIGTERM, _prev_sigterm)

        finally:
            # Restore strict-mode env var to its pre-run value so that
            # programmatic callers (e.g. test suites) don't inherit it.
            # This outer finally fires even if pre-flight raises before the
            # inner try/finally is reached, closing the coverage gap.
            if _prev_strict is None:
                os.environ.pop("STOCKBOT_STRICT_AS_OF", None)
            else:
                os.environ["STOCKBOT_STRICT_AS_OF"] = _prev_strict

        # Re-read manifest (driver wrote the final status) and add finished_at.
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["finished_at"] = datetime.now(tz=UTC).isoformat()
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        # Generate the report unconditionally — if the run aborted, the report
        # still tells us what *did* happen up to the abort point.  Pass the
        # ``window_key`` so ``report()`` can locate the per-window cache.
        try:
            from backtest.reporting import report
            report(run_dir, self._settings, window=window_key)
        except Exception:
            logger.exception("report generation failed for %s", run_id)

        return RunResult(
            run_id=run_id,
            run_dir=run_dir,
            status=manifest.get("status", status),
        )


# ── helpers ────────────────────────────────────────────────────────────────────

def _git_sha7() -> str:
    """Return the short (7-char) git SHA for HEAD; ``"unknown"`` on failure.

    Returns
    -------
    str
        Seven-character hex SHA, or ``"unknown"`` if git is unavailable.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _git_sha_full() -> str:
    """Return the full 40-char git SHA for HEAD; ``"unknown"`` on failure.

    Returns
    -------
    str
        Full hex SHA, or ``"unknown"`` if git is unavailable.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"

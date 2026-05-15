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
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backtest.cache.store import CachedDataStore
from backtest.decision_logger import DecisionLogger
from backtest.driver import Driver
from backtest.providers import _store_handle

# Importing each cache-provider module triggers its ``@register`` decorator,
# making the ``"cache"`` name available to ``data.registry.dispatch``.
from backtest.providers import (  # noqa: F401
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
from backtest.windows import load_windows
from broker.fake import FakeBroker
from data.registry import DOMAINS, set_active_provider
from orchestrator.persistence import create_all, make_engine

logger = logging.getLogger(__name__)


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
    settings_path:
        Path to ``config/backtest_settings.json``.
    windows_path:
        Path to ``config/backtest_windows.json``.
    watchlist_path:
        Path to ``config/watchlist.json``.
    """

    def __init__(
        self,
        *,
        settings_path:  Path = Path("config/backtest_settings.json"),
        windows_path:   Path = Path("config/backtest_windows.json"),
        watchlist_path: Path = Path("config/watchlist.json"),
    ) -> None:
        """Load config files; defer actual run setup to ``.run()``."""
        self._settings  = json.loads(Path(settings_path).read_text())
        self._windows   = load_windows(Path(windows_path))
        self._watchlist = json.loads(Path(watchlist_path).read_text())["tickers"]

    def run(
        self,
        window_key: str,
        watchlist: list[str] | None = None,
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

        Returns
        -------
        RunResult
            Summary with ``run_id``, ``run_dir``, and ``status``.
        """
        import asyncio
        return asyncio.run(self._run_async(window_key, watchlist))

    # ── private implementation ──────────────────────────────────────────────────

    async def _run_async(
        self,
        window_key: str,
        watchlist: list[str] | None,
    ) -> RunResult:
        """Async implementation of the full run lifecycle."""
        window  = self._windows[window_key]
        wl      = list(watchlist or self._watchlist)
        run_id  = f"{window_key}-{_git_sha7()}"
        run_dir = Path(self._settings["runs_root"]) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # ── open the golden cache store ─────────────────────────────────────
        store = CachedDataStore(Path(self._settings["cache_path"]))
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
        broker = FakeBroker(
            starting_cash=self._settings["fake_broker_starting_cash"],
            prices={ticker: 0.0 for ticker in wl_filtered},
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
        driver = Driver(
            broker=broker,
            run_id=run_id,
            run_dir=run_dir,
            window_key=window_key,
            db_session=db_session,
            decision_logger=dl,
            failure_abort_ratio=self._settings["failed_tick_abort_ratio"],
        )
        schedule = generate_ticks(window.start, window.end)
        state    = {"tickers": wl_filtered, "watchlist": wl_filtered}

        status = "completed"
        try:
            await driver.run(state, schedule)
        except RuntimeError as exc:
            logger.error("run %s aborted: %s", run_id, exc)
            status = "aborted"
        finally:
            # Restore live domain mappings regardless of success or failure.
            for restore in restores:
                restore()
            _store_handle.clear_store()
            db_session.close()

        # Re-read manifest (driver wrote the final status) and add finished_at.
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["finished_at"] = datetime.now(tz=UTC).isoformat()
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        # Generate the report unconditionally — if the run aborted, the report
        # still tells us what *did* happen up to the abort point.
        try:
            from backtest.reporting import report
            report(run_dir, self._settings)
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

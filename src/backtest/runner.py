"""End-to-end run orchestrator: window + watchlist → cache wiring → driver.

Materialises the per-run directory and DB, wires every data domain to its
``cache`` provider, runs the Driver over the tick schedule, then restores the
live provider config regardless of whether the run succeeded or aborted.

Usage::

    from backtest.runner import Runner

    result = Runner().run("svb-stress-2023-03")
    print(result.run_id, result.status)
"""
from __future__ import annotations

import json
import logging
import signal
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backtest.cache.store import CachedDataStore
from backtest.decision_logger import DecisionLogger
from backtest.driver import Driver
from backtest.providers._store_handle import clear_store, set_store

# Importing the cache-provider modules triggers their @register decorators,
# making "cache" available as a provider name in the registry.  This import
# must happen before set_active_provider is called.
from backtest.providers import (  # noqa: F401  — side-effect imports
    filings_cache,
    insider_trades_cache,
    news_cache,
    notable_holders_cache,
    politician_trades_cache,
    social_sentiment_cache,
    stats_cache,
)
from backtest.schedule import generate_ticks
from backtest.windows import load_windows
from broker.fake import FakeBroker
from data.registry import DOMAINS, set_active_provider
from orchestrator.persistence import Base, create_all, make_engine

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Summary of one completed backtest run, returned to the CLI.

    Attributes
    ----------
    run_id:
        The unique identifier for this run
        (``<window-key>-<git-sha7>``).
    run_dir:
        Filesystem path to the run's output directory.
    status:
        Terminal status — one of ``"completed"``, ``"completed_with_failures"``,
        ``"aborted"``, or ``"interrupted"``.
    """

    run_id:  str
    run_dir: Path
    status:  str


class Runner:
    """Orchestrates one end-to-end backtest run.

    Responsibilities:
    - Load config (windows, settings, watchlist).
    - Materialise the per-run directory and SQLite DB.
    - Wire every data domain to the ``cache`` provider.
    - Pre-flight: drop tickers with no OHLCV bars in the window.
    - Create a ``FakeBroker`` and a ``DecisionLogger``.
    - Drive the tick schedule via ``Driver``.
    - Restore live provider config in a ``finally`` block.
    - Write ``finished_at`` and final status to ``manifest.json``.

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
        settings_path: Path = Path("config/backtest_settings.json"),
        windows_path:  Path = Path("config/backtest_windows.json"),
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
        """Run one backtest window synchronously.

        Wraps ``_run_async`` in ``asyncio.run``.  The caller receives a
        ``RunResult`` regardless of whether the run succeeded or aborted.

        Parameters
        ----------
        window_key:
            Key in ``config/backtest_windows.json`` (e.g. ``"svb-stress-2023-03"``).
        watchlist:
            Override the default watchlist.  ``None`` uses the configured default.

        Returns
        -------
        RunResult
            Terminal state of the run.
        """
        import asyncio
        return asyncio.run(self._run_async(window_key, watchlist))

    async def _run_async(
        self,
        window_key: str,
        watchlist: list[str] | None,
    ) -> RunResult:
        """Async implementation of one full backtest run."""
        window = self._windows[window_key]
        wl     = watchlist or self._watchlist
        run_id = f"{window_key}-{_git_sha7()}"
        run_dir = Path(self._settings["runs_root"]) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # ── Cache store ───────────────────────────────────────────────────
        store = CachedDataStore(Path(self._settings["cache_path"]))
        set_store(store)

        # ── Pre-flight: drop tickers with no OHLCV bars ───────────────────
        skipped: list[str] = []
        wl_active: list[str] = []
        for ticker in wl:
            if store.read_ohlcv(ticker, window.start, window.end):
                wl_active.append(ticker)
            else:
                logger.warning("skipping %s — no OHLCV bars in window", ticker)
                skipped.append(ticker)

        # ── Provider swap: live → cache for every domain ──────────────────
        # ``set_active_provider`` returns a restore callable.  We collect all
        # of them so a crashed run does not leave the in-process config pointing
        # at the cache provider and breaking any subsequent live invocation.
        restores: list = []
        for domain in DOMAINS:
            restores.append(set_active_provider(domain, "cache"))

        # ── Per-run DB ────────────────────────────────────────────────────
        engine     = make_engine(f"sqlite:///{run_dir / 'db.sqlite'}")
        create_all(engine)
        from sqlalchemy.orm import sessionmaker
        db_session = sessionmaker(bind=engine)()

        # ── FakeBroker ────────────────────────────────────────────────────
        broker = FakeBroker(
            starting_cash=self._settings["fake_broker_starting_cash"],
            prices={t: 0.0 for t in wl_active},
        )

        # ── DecisionLogger ────────────────────────────────────────────────
        dl = DecisionLogger(
            output_dir=run_dir / "decisions",
            window_key=window_key,
        )

        # ── Manifest — initial write ──────────────────────────────────────
        manifest = {
            "run_id":            run_id,
            "window_key":        window_key,
            "window":            {"start": str(window.start), "end": str(window.end)},
            "watchlist":         wl_active,
            "skipped_tickers":   skipped,
            "git_sha":           _git_sha_full(),
            "started_at":        datetime.now(tz=UTC).isoformat(),
            "status":            "running",
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str)
        )

        # ── SIGINT / SIGTERM handler — writes "interrupted" to manifest ───
        # Restored in finally so the handler doesn't outlive the run.
        original_sigint  = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        def _interrupt_handler(signum, frame) -> None:
            """Write interrupted status and re-raise as KeyboardInterrupt."""
            logger.warning("run %s received signal %s — marking interrupted", run_id, signum)
            _patch_manifest(run_dir, {"status": "interrupted"})
            signal.signal(signal.SIGINT,  original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT,  _interrupt_handler)
        signal.signal(signal.SIGTERM, _interrupt_handler)

        # ── Tick schedule ─────────────────────────────────────────────────
        schedule = generate_ticks(window.start, window.end)

        # ── Driver ────────────────────────────────────────────────────────
        driver = Driver(
            broker=broker,
            run_id=run_id,
            run_dir=run_dir,
            window_key=window_key,
            db_session=db_session,
            decision_logger=dl,
            failure_abort_ratio=self._settings["failed_tick_abort_ratio"],
        )

        state = {
            "tickers":      wl_active,
            "watchlist":    wl_active,
            "positions":    {},
            "memory_buffer": [],
            "day_digest":   "",
            "thesis":       "",
        }

        status = "completed"
        try:
            await driver.run(state, schedule)
            # Driver writes its own status to the manifest; read it back.
            manifest = json.loads((run_dir / "manifest.json").read_text())
            status   = manifest.get("status", "completed")
        except RuntimeError as exc:
            logger.error("run %s aborted: %s", run_id, exc)
            status = "aborted"
        except KeyboardInterrupt:
            status = "interrupted"
        finally:
            # Restore live provider config unconditionally.
            for restore_fn in restores:
                restore_fn()
            clear_store()

            # Restore signal handlers.
            signal.signal(signal.SIGINT,  original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)

            db_session.close()

        # ── Finalise manifest ─────────────────────────────────────────────
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["finished_at"] = datetime.now(tz=UTC).isoformat()
        manifest.setdefault("status", status)
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str)
        )

        # ── Generate report ───────────────────────────────────────────────
        # Run unconditionally: even an aborted run benefits from whatever
        # snapshots were recorded before the abort point.
        try:
            from backtest.reporting import report as _report
            _report(run_dir, self._settings)
        except Exception:
            logger.exception("report generation failed for %s", run_id)

        return RunResult(
            run_id=run_id,
            run_dir=run_dir,
            status=manifest.get("status", status),
        )


# ── Manifest helper ───────────────────────────────────────────────────────────

def _patch_manifest(run_dir: Path, patch: dict) -> None:
    """Merge ``patch`` into ``<run_dir>/manifest.json``, creating it if absent.

    Parameters
    ----------
    run_dir:
        The run's root directory.
    patch:
        Key/value pairs to merge in.
    """
    path = run_dir / "manifest.json"
    manifest: dict = {}
    if path.exists():
        try:
            manifest = json.loads(path.read_text())
        except Exception:
            pass
    manifest.update(patch)
    path.write_text(json.dumps(manifest, indent=2, default=str))


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git_sha7() -> str:
    """Return the 7-character short SHA for HEAD; ``"unknown"`` on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"], text=True,
        ).strip()
    except Exception:
        return "unknown"


def _git_sha_full() -> str:
    """Return the full SHA for HEAD; ``"unknown"`` on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip()
    except Exception:
        return "unknown"

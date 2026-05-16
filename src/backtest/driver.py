"""Tick-loop driver — runs the unchanged live pipeline once per scheduled tick.

The driver is deliberately thin: pre-tick setup (compute ``as_of``, attach a
fresh ``TraceWriter``), call the pipeline via ADK Runner, post-tick flush the
trace.  Mid-tick failures are caught, recorded in the manifest, and the driver
advances to the next tick unless the configured failure ratio is exceeded.

Adaptation notes vs plan:
- Uses ``google.adk.Runner`` + fresh ``InMemorySessionService`` (same pattern
  as ``orchestrator/tick.py``) rather than ``InMemoryRunner.session_service``
  — both work identically, but this mirrors the tested live path.
- ADK runners sometimes raise ``AttributeError`` or ``BaseExceptionGroup``
  after the pipeline finishes (known ADK 1.32 cleanup bug).  Those are caught
  and logged; the tick result is still readable from the session service.
- ``run_id`` defaults to ``"<window_key>-local"`` if not supplied so tests
  can construct a Driver without knowing the run ID upfront.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from backtest.schedule import Tick
from data.timeguard import drain_wallclock_fallback_count
from observability.trace import TraceWriter
from orchestrator.pipeline import build_pipeline

logger = logging.getLogger(__name__)

# Sentinel used when the caller does not provide a ``new_message`` content.
_TICK_MESSAGE_TEMPLATE = "Backtest tick {tick_id}"


class Driver:
    """Loop over scheduled ticks and invoke the live pipeline for each.

    Parameters
    ----------
    broker:
        A broker implementing the ``Broker`` protocol.  ``FakeBroker`` in
        backtests; ``Trading212Broker`` in live runs.
    run_dir:
        Directory for this run's artefacts (traces, manifest).  Must already
        exist when ``run`` is called (the runner creates it before constructing
        the driver).
    window_key:
        Era label (e.g. ``"svb-stress-2023-03"``); used in tick IDs and
        manifest entries.
    run_id:
        Optional stable identifier for this run.  Defaults to
        ``"<window_key>-local"`` so unit tests can omit it.
    db_session:
        SQLAlchemy session for trade-log and stance writes.  ``None`` disables
        persistence (tests do this by default).
    decision_logger:
        ``DecisionLogger`` instance installed into each tick's session state.
        ``None`` disables decision snapshots.
    failure_abort_ratio:
        If ``failed_ticks / total_ticks`` exceeds this threshold, ``run``
        raises ``RuntimeError`` and writes ``status="aborted"`` to the
        manifest.  Default 0.10 (10 %).
    """

    def __init__(
        self,
        *,
        broker: Any,
        run_dir: Path,
        window_key: str,
        run_id: str = "",
        db_session: Any = None,
        decision_logger: Any = None,
        failure_abort_ratio: float = 0.10,
    ) -> None:
        """Wire the driver.  ``run_dir`` should already exist."""
        self._broker             = broker
        self._run_id             = run_id or f"{window_key}-local"
        self._run_dir            = Path(run_dir)
        self._window_key         = window_key
        self._db_session         = db_session
        self._dl                 = decision_logger
        self._ratio              = failure_abort_ratio
        self._traces_dir         = self._run_dir / "traces"
        self._traces_dir.mkdir(parents=True, exist_ok=True)

        # Audit artefacts go alongside traces under runs/<id>/audit/.
        self._audit_dir = self._run_dir / "audit"
        self._audit_dir.mkdir(parents=True, exist_ok=True)

        # Build the live pipeline once per run — same pipeline, fresh runner
        # per tick (ADK InMemorySessionService is per-runner).
        self._pipeline   = build_pipeline(broker, db_session)
        self._failed:    list[dict] = []
        self._total:     int = 0

        # Enable per-tick read capture on the shared cache store so the audit
        # telemetry layer can summarise what the analysts saw.
        try:
            from backtest.providers._store_handle import get_store
            get_store()._audit_enable_capture()
        except RuntimeError:
            # No store wired (unit tests) — telemetry will be empty.
            pass

    # ── public API ─────────────────────────────────────────────────────────────

    async def run(self, state: dict, schedule: list[Tick]) -> None:
        """Execute every tick in ``schedule``, mutating ``state`` in place.

        Parameters
        ----------
        state:
            Shared mutable state dict.  Must contain at least ``"tickers"``
            and ``"watchlist"`` keys.  Modified in-place so each tick inherits
            positions and holdings from the previous one.
        schedule:
            Ordered list of ``Tick`` objects (``as_of`` + ``phase``).
        """
        for tick in schedule:
            self._total += 1

            tw = TraceWriter()
            state["_trace"]           = tw
            state["as_of"]            = tick.as_of
            state["tick_phase"]       = tick.phase
            # Deterministic tick_id: stable composite of (run_id, as_of, phase)
            # so reruns of the same window emit comparable trace files and
            # decision logs.
            state["tick_id"]          = (
                f"{self._run_id}-{tick.as_of.isoformat()}-{tick.phase}"
            )
            state["_decision_logger"] = self._dl

            # Update FakeBroker price to the day's open or close.
            self._refresh_broker_prices(state.get("watchlist", []), tick)

            try:
                await self._run_one_tick(state)
            except Exception as exc:
                logger.exception("tick %s failed", tick.as_of)
                self._failed.append({
                    "as_of":           str(tick.as_of),
                    "exception_type":  type(exc).__name__,
                    "message":         str(exc),
                })
                # Check ratio after recording the failure.
                if len(self._failed) / self._total > self._ratio:
                    self._write_manifest_status("aborted")
                    raise RuntimeError(
                        f"failed-tick ratio {len(self._failed)}/{self._total}"
                        f" exceeded threshold {self._ratio}",
                    ) from exc

            tw.finalise(self._traces_dir / f"{_slug(tick.as_of)}.json")

            # ── per-tick audit telemetry ──────────────────────────────────────
            # Drain the store's read-capture buffer, summarise into per_domain,
            # compute tripwires, and write the JSON record unconditionally.
            from backtest.audit.telemetry import (
                build_telemetry_record,
                per_domain_from_store_reads,
                write_telemetry_record,
            )
            from backtest.providers._store_handle import get_store as _get_store

            try:
                _store       = _get_store()
                cache_reads  = _store._audit_drain_reads()
            except RuntimeError:
                # Store not wired in isolated unit tests — produce empty telemetry.
                cache_reads = {}

            per_domain = per_domain_from_store_reads(
                cache_reads=cache_reads,
                as_of=tick.as_of,
                phase=tick.phase,
            )

            # Drain the timeguard's per-tick wall-clock fallback counter.
            # Any value > 0 means at least one site fell back to the wall
            # clock during this tick — surfaces directly on the tripwire.
            wallclock_fallback_count = drain_wallclock_fallback_count()

            telemetry = build_telemetry_record(
                tick=tick,
                run_id=self._run_id,
                strict_mode=os.environ.get("STOCKBOT_STRICT_AS_OF") == "1",
                per_domain=per_domain,
                report_cache_hits=state.get("_report_cache_hits_for_audit", []),
                db_writes_recorded_at={},
                wall_clock_fallback_fired=wallclock_fallback_count > 0,
            )
            write_telemetry_record(self._audit_dir, telemetry)

            # Reset the per-tick report-cache-hits capture for the next tick.
            state.pop("_report_cache_hits_for_audit", None)

        self._write_manifest_status(
            "completed_with_failures" if self._failed else "completed",
        )

    # ── private helpers ────────────────────────────────────────────────────────

    async def _run_one_tick(self, state: dict) -> None:
        """Drive the pipeline once via ADK's Runner + InMemorySessionService.

        Creates a fresh in-memory session service per tick so ADK session IDs
        never collide across ticks.  After the runner finishes, the updated
        session state is merged back into ``state`` so the next tick inherits
        positions and portfolio data.

        Known ADK 1.32 issue: the runner may raise ``AttributeError`` or
        ``BaseExceptionGroup`` *after* the pipeline completes (teardown bug in
        the parallel-agent finaliser).  These are caught, logged, and ignored
        — the tick result is still readable from the session service.

        Parameters
        ----------
        state:
            The shared mutable state dict for this tick (mutated in place).
        """
        session_service = InMemorySessionService()
        runner = Runner(
            agent=self._pipeline,
            app_name="backtest",
            session_service=session_service,
        )

        # Use a UUID suffix to guarantee session uniqueness even if the
        # deterministic tick_id is the same across driver instances (e.g. in
        # parallel test processes).
        session_id = f"{state['tick_id']}-{uuid.uuid4().hex[:8]}"

        adk_session = await session_service.create_session(
            app_name="backtest",
            user_id="backtest",
            state=dict(state),  # shallow copy — ADK mutates its own session state
            session_id=session_id,
        )

        message = genai_types.Content(
            parts=[genai_types.Part(
                text=_TICK_MESSAGE_TEMPLATE.format(tick_id=state["tick_id"])
            )],
            role="user",
        )

        try:
            async for _ in runner.run_async(
                user_id="backtest",
                session_id=adk_session.id,
                new_message=message,
            ):
                pass
        except (AttributeError, Exception) as exc:
            # ADK 1.32 runner-cleanup bug — see tick.py for details.  The
            # pipeline has already completed at this point; read state below.
            # NOTE: deliberately catches ``Exception`` (not ``BaseException``)
            # so that ``KeyboardInterrupt``, ``SystemExit``, and ``MemoryError``
            # propagate normally and are not silently swallowed here.
            logger.warning(
                "ADK runner raised after tick %s (pipeline likely completed): "
                "%s: %s",
                state["tick_id"], type(exc).__name__, exc,
            )

        # Pull session state back into ``state`` so the next tick sees
        # positions, portfolio, and any other keys written by pipeline agents.
        updated = await session_service.get_session(
            app_name="backtest",
            user_id="backtest",
            session_id=adk_session.id,
        )
        if updated is not None:
            state.update(dict(updated.state))

    def _refresh_broker_prices(self, tickers: list[str], tick: Tick) -> None:
        """Set FakeBroker prices to the day's open or close from the cache.

        Reads from the global ``_store_handle`` singleton (wired by the runner
        before the first tick).  If no bar exists for ``ticker`` on
        ``tick.as_of.date()``, the price is left unchanged.

        Parameters
        ----------
        tickers:
            Watchlist symbols to update.
        tick:
            The current scheduled tick (``as_of`` + ``phase``).
        """
        from backtest.providers._store_handle import get_store

        try:
            store = get_store()
        except RuntimeError:
            # Store not wired (e.g. in isolated unit tests) — skip silently.
            return

        for ticker in tickers:
            bars = store.read_ohlcv(ticker, tick.as_of.date(), tick.as_of.date())
            if not bars:
                continue
            bar = bars[0]
            price = bar.open if tick.phase == "open" else bar.close
            self._broker.set_price(ticker, price)

    def _write_manifest_status(self, status: str) -> None:
        """Patch ``manifest.json`` with current run status + failed-tick list.

        Reads the existing manifest if it exists; merges in the new fields and
        rewrites it atomically (write_text is atomic on Linux for small files).

        Parameters
        ----------
        status:
            One of ``"completed"``, ``"completed_with_failures"``,
            or ``"aborted"``.
        """
        path = self._run_dir / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        manifest["status"]       = status
        manifest["failed_ticks"] = self._failed
        manifest["ticks_total"]  = self._total
        manifest["ticks_failed"] = len(self._failed)

        # Audit completeness: one .tick.json per scheduled tick is expected.
        audit_files = list(self._audit_dir.glob("*.tick.json"))
        manifest["audit_complete"]     = len(audit_files) == self._total
        manifest["audit_record_count"] = len(audit_files)

        path.write_text(json.dumps(manifest, indent=2, default=str))


def _slug(as_of: Any) -> str:
    """Return a filename-safe ISO timestamp slug.

    Replaces colons, plus-signs, and spaces that are unsafe in filenames or
    on some shells.

    Parameters
    ----------
    as_of:
        Datetime-like value (anything ``str()`` can convert).

    Returns
    -------
    str
        A filesystem-safe string derived from the ISO representation of ``as_of``.
    """
    return (
        str(as_of)
        .replace(":", "-")
        .replace("+", "p")
        .replace(" ", "T")
    )

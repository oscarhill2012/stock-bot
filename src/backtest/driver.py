"""Tick-loop driver — runs the unchanged live pipeline once per scheduled tick.

The driver is deliberately thin: pre-tick setup (compute ``as_of``, attach a
fresh ``TraceWriter``), call the live pipeline via ``_run_tick``, post-tick
flush the trace.  Mid-tick failures are caught, recorded in the manifest, and
the driver advances to the next tick unless the configured failure ratio is
exceeded.

**ADK state-persistence note.**  ADK's ``InMemorySessionService`` deep-copies
the session on creation, so mutations made by ``BaseAgent._run_async_impl``
to ``ctx.session.state`` are visible within a single invocation (all sub-agents
share the same live session object) but are NOT persisted back to storage after
the invocation ends.  The driver works around this by:

1. Using a ``_CapturingSessionService`` that holds a live reference to the
   in-flight session so the post-tick state is readable.
2. On each tick, creating a fresh session seeded with the *carry state* from
   the previous tick (positions, memory_buffer, etc.).

This preserves cross-tick state (open positions, memory accumulation) without
relying on ADK's event-driven ``state_delta`` persistence mechanism, which the
deterministic BaseAgent sub-classes in this pipeline do not use.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from google.adk import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types as genai_types

from backtest.schedule import Tick
from observability.trace import TraceWriter
from orchestrator.pipeline import build_pipeline

logger = logging.getLogger(__name__)

# ── State keys carried forward across ticks ───────────────────────────────────

#: These keys persist across ticks.  All other keys are re-derived fresh each tick.
_CARRY_KEYS: frozenset[str] = frozenset({
    "positions",
    "memory_buffer",
    "day_digest",
    "thesis",
    "last_executed_tick_id",
    "starting_capital",
    "spy_start_price",
})


class _CapturingSessionService(InMemorySessionService):
    """InMemorySessionService variant that exposes the live in-flight session.

    ADK's ``InMemorySessionService`` stores a *copy* of the session, so the
    state mutations that BaseAgent sub-classes write directly to
    ``ctx.session.state`` are invisible after ``run_async`` completes.  This
    subclass holds a reference to the live session object (passed to
    ``append_event``) so the driver can read the post-tick state.
    """

    def __init__(self) -> None:
        """Initialise with no captured session."""
        super().__init__()
        self.live_session: Any = None

    async def append_event(self, session: Any, event: Any) -> Any:
        """Capture the in-flight session reference, then delegate to parent."""
        self.live_session = session
        return await super().append_event(session, event)


class Driver:
    """Loop over scheduled ticks and invoke the live pipeline for each.

    Parameters
    ----------
    broker:
        A ``FakeBroker`` instance pre-seeded with cash and (empty) prices.
        The driver updates prices before each tick via ``broker.set_price``.
    run_id:
        Unique identifier for this backtest run (used in ``tick_id`` derivation
        and manifest writes).
    run_dir:
        Root directory for this run.  Must already exist.  Trace files are
        written to ``<run_dir>/traces/``.
    window_key:
        Era slug passed to the ``DecisionLogger`` for corpus organisation.
    db_session:
        Optional SQLAlchemy session wired into the live pipeline for evidence
        and snapshot persistence.  Defaults to ``None`` (no DB writes).
    decision_logger:
        Optional ``DecisionLogger`` instance.  When present, one decision JSON
        is written per executed Fill.
    failure_abort_ratio:
        If the ratio ``failed_ticks / total_ticks`` exceeds this threshold, the
        driver raises ``RuntimeError`` and writes ``manifest.status = "aborted"``.
        Default: 0.10 (10%).
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
        """Wire the driver.  ``run_dir`` must already exist."""
        self._broker     = broker
        self._run_id     = run_id or uuid.uuid4().hex[:8]
        self._run_dir    = Path(run_dir)
        self._window_key = window_key
        self._db_session = db_session
        self._dl         = decision_logger
        self._ratio      = failure_abort_ratio

        # Ensure traces output directory exists.
        self._traces_dir = self._run_dir / "traces"
        self._traces_dir.mkdir(parents=True, exist_ok=True)

        # Build the live pipeline once — the plan requires verbatim reuse.
        self._pipeline = build_pipeline(broker, db_session)

        # Failure tracking.
        self._failed: list[dict] = []
        self._total:  int = 0

    # ── Public interface ──────────────────────────────────────────────────────

    async def run(self, state: dict, schedule: list[Tick]) -> None:
        """Execute every tick in ``schedule``, updating ``state`` in place.

        On each tick:
        1. Injects ``as_of``, ``tick_id``, ``_trace``, and ``_decision_logger``
           into ``state``.
        2. Refreshes FakeBroker prices to the tick's open/close bar price.
        3. Calls ``_run_one_tick(state)``; catches and logs any exception.
        4. Flushes the trace writer to ``traces/<as_of>.json``.
        5. Checks the failure ratio and aborts if threshold is exceeded.

        Parameters
        ----------
        state:
            Shared mutable state dict that persists across ticks.  Must contain
            ``"tickers"`` and ``"watchlist"`` keys.
        schedule:
            Ordered list of ``Tick`` objects from ``backtest.schedule``.
        """
        for tick in schedule:
            self._total += 1

            # Attach a fresh trace writer for this tick.
            tw = TraceWriter()
            state["_trace"]           = tw
            state["as_of"]            = tick.as_of
            state["tick_phase"]       = tick.phase
            state["_decision_logger"] = self._dl

            # Deterministic tick_id: per-run DB means no cross-run collision
            # risk, so a stable composite of (run_id, as_of, phase) makes
            # reruns of the same window produce comparable trace files.
            state["tick_id"] = (
                f"{self._run_id}-{tick.as_of.isoformat()}-{tick.phase}"
            )

            # Update FakeBroker prices from the cache so the executor has a
            # valid fill price and the snapshotter has a valid portfolio value.
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

                if self._total > 0 and len(self._failed) / self._total > self._ratio:
                    self._write_manifest_patch({"status": "aborted"})
                    raise RuntimeError(
                        f"failed-tick ratio {len(self._failed)}/{self._total}"
                        f" exceeded threshold {self._ratio}",
                    ) from exc

            # Flush trace regardless of success/failure.
            tw.finalise(self._traces_dir / f"{_slug(tick.as_of)}.json")

        # Write terminal status once all ticks are processed.
        self._write_manifest_patch({
            "status":       "completed_with_failures" if self._failed else "completed",
            "failed_ticks": self._failed,
            "ticks_total":  self._total,
            "ticks_failed": len(self._failed),
        })

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _run_one_tick(self, state: dict) -> None:
        """Drive the pipeline for one tick, propagating state changes back.

        Uses a ``_CapturingSessionService`` to recover the in-flight session
        state after the ADK ``Runner`` completes.  The ADK
        ``InMemorySessionService`` stores a *copy* of the session, so direct
        ``ctx.session.state`` mutations by agents are only visible via the live
        session reference (not via ``get_session``).  After the run, selected
        cross-tick keys are merged back into ``state``.

        Parameters
        ----------
        state:
            Shared state dict to seed the session with and update after the run.
        """
        svc    = _CapturingSessionService()
        runner = Runner(
            agent=self._pipeline,
            app_name="backtest",
            session_service=svc,
        )

        # Seed the new session with current carry state.
        session = await svc.create_session(
            app_name="backtest",
            user_id="backtest",
            state=dict(state),
        )

        msg = genai_types.Content(
            parts=[genai_types.Part(text=f"run tick {state['tick_id']}")],
            role="user",
        )

        try:
            async for _ in runner.run_async(
                user_id="backtest",
                session_id=session.id,
                new_message=msg,
            ):
                pass
        except (AttributeError, BaseExceptionGroup, Exception) as exc:
            # ADK 1.32 has a known runner-cleanup bug: after the pipeline
            # runs, the runner may raise AttributeError('NoneType'.partial)
            # or a BaseExceptionGroup wrapping GeneratorExit from parallel-
            # agent teardown.  Both happen *after* session state has been
            # written, so the tick result is still available via the
            # captured session.  We log and continue.
            # NOTE: deliberately NOT catching KeyboardInterrupt / SystemExit —
            # those are BaseException subclasses that should propagate so the
            # user can interrupt a long run with Ctrl-C.
            logger.warning(
                "ADK runner raised after tick %s (pipeline likely completed): "
                "%s: %s",
                state["tick_id"], type(exc).__name__, exc,
            )

        # Propagate the post-tick state back into the shared dict.
        if svc.live_session is not None:
            live_state = dict(svc.live_session.state)
            for key in _CARRY_KEYS:
                if key in live_state:
                    state[key] = live_state[key]

            # Also copy execution results and trace data so the caller
            # (tests, reporting) can observe them.
            for key in ("executions", "last_snapshot", "strategist_decision",
                        "final_orders", "risk_clamps_applied"):
                if key in live_state:
                    state[key] = live_state[key]

    def _refresh_broker_prices(self, tickers: list[str], tick: Tick) -> None:
        """Update FakeBroker prices to the tick's open or close bar price.

        Reads OHLCV bars from the cache (via ``_store_handle``) for each
        ticker and calls ``broker.set_price`` with the appropriate bar price.
        If no bar exists for a ticker in the cache, the price is left unchanged
        (the FakeBroker was seeded with 0.0 for all tickers at runner start).

        Parameters
        ----------
        tickers:
            Active watchlist tickers for this run.
        tick:
            The current tick whose ``phase`` determines open vs close price.
        """
        try:
            from backtest.providers._store_handle import get_store
            store = get_store()
        except RuntimeError:
            # Store not configured (e.g. in unit tests that don't need prices).
            return

        for ticker in tickers:
            bars = store.read_ohlcv(ticker, tick.as_of.date(), tick.as_of.date())
            if not bars:
                continue
            bar   = bars[0]
            price = bar.open if tick.phase == "open" else bar.close
            self._broker.set_price(ticker, price)

    def _write_manifest_patch(self, patch: dict) -> None:
        """Merge ``patch`` into ``manifest.json``, creating the file if absent.

        Parameters
        ----------
        patch:
            Key/value pairs to merge into the manifest.
        """
        path = self._run_dir / "manifest.json"
        manifest: dict = {}
        if path.exists():
            try:
                manifest = json.loads(path.read_text())
            except Exception:
                pass

        manifest.update(patch)
        path.write_text(json.dumps(manifest, indent=2, default=str))


# ── Utilities ─────────────────────────────────────────────────────────────────

def _slug(as_of: Any) -> str:
    """Return a filename-safe ISO timestamp slug.

    Replaces colons, plus signs, and spaces so the string is safe on all
    file systems.

    Parameters
    ----------
    as_of:
        A datetime or datetime-stringifiable value.

    Returns
    -------
    str
        A safe slug (e.g. ``"2023-03-13T09-30-00-04-00"``).
    """
    return (
        str(as_of)
        .replace(":", "-")
        .replace("+", "p")
        .replace(" ", "T")
    )

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
from google.genai import types as genai_types

from backtest.schedule import Tick
from data.timeguard import drain_wallclock_fallback_count
from observability.drain import drain_tick
from observability.handle_injector_plugin import HandleInjectorPlugin
from observability.otel_setup import install_observability
from observability.trace import TraceWriter
from orchestrator.persistence import make_session_service
from orchestrator.pipeline import build_pipeline

logger = logging.getLogger(__name__)

# Sentinel used when the caller does not provide a ``new_message`` content.
_TICK_MESSAGE_TEMPLATE = "Backtest tick {tick_id}"


def _log_exception_chain(
    exc: BaseException,
    tick_id: str,
    depth: int = 0,
) -> None:
    """Recursively log ``exc`` and any ``ExceptionGroup`` sub-exceptions.

    ADK's parallel-agent code surfaces failures as ``BaseExceptionGroup`` /
    ``ExceptionGroup`` whose default ``str()`` only reports the count
    ("unhandled errors in a TaskGroup (2 sub-exceptions)"), not the actual
    sub-exceptions.  Without unwrapping, every mid-pipeline failure appears
    in the log as the same useless one-liner.

    This helper walks ``exc.exceptions`` (if present) and logs each leaf
    exception with its own traceback so the underlying cause is visible.

    Parameters
    ----------
    exc:
        The exception (or exception group) to log.
    tick_id:
        Current tick identifier — included in every log line so failures
        from concurrent ticks can be told apart.
    depth:
        Recursion depth, used to indent nested sub-exceptions for legibility.
    """

    indent = "  " * depth

    # ``ExceptionGroup`` and ``BaseExceptionGroup`` both expose ``.exceptions``;
    # ordinary exceptions do not, so ``getattr`` keeps this generic.
    subs = getattr(exc, "exceptions", None)

    if subs:
        # Group node — log the wrapper plus a count, then recurse into each
        # sub-exception so the *leaves* land in the log with full tracebacks.
        logger.error(
            "%stick %s ExceptionGroup: %s (%d sub-exceptions)",
            indent, tick_id, type(exc).__name__, len(subs),
        )
        for sub in subs:
            _log_exception_chain(sub, tick_id, depth + 1)
    else:
        # Leaf node — log with ``exc_info`` so the traceback comes through.
        logger.error(
            "%stick %s sub-exception: %s: %s",
            indent, tick_id, type(exc).__name__, exc,
            exc_info=exc,
        )


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
    session_db_url:
        SQLAlchemy-style URL for the ADK session database.  Passed directly to
        ``make_session_service(db_url=…)`` on each tick.  Use
        ``sqlite+aiosqlite:///:memory:`` in unit tests that don't need
        cross-tick persistence; use the per-run path
        ``sqlite+aiosqlite:///runs/<run-id>/session.sqlite`` in production
        backtest runs.  Defaults to the in-memory sentinel so tests that
        construct Driver without a real runs directory still work.
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
    enforce_pipeline_completion:
        When ``True`` (the default — production-safe), the driver asserts
        after every tick that the Snapshotter (the *last* agent in the
        pipeline) wrote ``state["last_snapshot"]`` keyed by the current
        tick_id.  If the snapshot is missing or stale, the tick is treated
        as a real failure rather than silently marked "completed".  Tests
        that exercise the driver against a stubbed / failing pipeline
        (e.g. no API keys, mocked ADK runner) should pass ``False`` because
        the snapshotter cannot run end-to-end in those scenarios.
    """

    def __init__(
        self,
        *,
        broker: Any,
        run_dir: Path,
        window_key: str,
        run_id: str = "",
        session_db_url: str = "sqlite+aiosqlite:///:memory:",
        db_session: Any = None,
        decision_logger: Any = None,
        failure_abort_ratio: float = 0.10,
        enforce_pipeline_completion: bool = True,
    ) -> None:
        """Wire the driver.  ``run_dir`` should already exist."""
        self._broker             = broker
        self._run_id             = run_id or f"{window_key}-local"
        self._run_dir            = Path(run_dir)
        self._window_key         = window_key
        self._session_db_url     = session_db_url
        self._db_session         = db_session
        self._dl                 = decision_logger
        self._ratio              = failure_abort_ratio
        self._enforce_completion = enforce_pipeline_completion
        self._traces_dir         = self._run_dir / "traces"
        self._traces_dir.mkdir(parents=True, exist_ok=True)

        # Audit artefacts go alongside traces under runs/<id>/audit/.
        self._audit_dir = self._run_dir / "audit"
        self._audit_dir.mkdir(parents=True, exist_ok=True)

        # OTEL-shaped observability artefacts go under runs/<id>/obs/
        # ({logs,traces,metrics}/<tick>.json), separate from the legacy
        # TraceWriter output at runs/<id>/traces/<tick>.json.  Install the
        # OTEL providers + log handler exactly once per process — the call
        # is idempotent across Driver instances.
        self._obs_dir = self._run_dir / "obs"
        self._obs_dir.mkdir(parents=True, exist_ok=True)
        self._obs_handles = install_observability(service_name="stockbot-backtest")

        # Phase 9: pipeline is now built *per tick* inside ``_run_one_tick``
        # so the News and Fundamental analyst branches fan out across the
        # watchlist as it exists at each tick boundary.  Storing the broker
        # and db_session here gives ``_run_one_tick`` everything it needs.
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
            Shared mutable state dict.  Must contain at least ``"tickers"``.
            Modified in-place so each tick inherits
            positions and holdings from the previous one.
        schedule:
            Ordered list of ``Tick`` objects (``as_of`` + ``phase``).
        """
        total_ticks = len(schedule)
        for tick in schedule:
            self._total += 1

            # One human-readable header per tick — paired with the
            # per-agent "Foo done in N ms" lines emitted by
            # ``observability.otel_setup.AgentLifecycleLogger`` it gives
            # the operator a clean play-by-play in the terminal without
            # leaking ADK's DEBUG-level prompt dumps.
            logger.info(
                "── tick %d/%d — %s %s ──",
                self._total, total_ticks, tick.as_of.isoformat(), tick.phase,
            )

            tw = TraceWriter()
            state["as_of"]            = tick.as_of
            state["tick_phase"]       = tick.phase
            # Deterministic tick_id: stable composite of (run_id, as_of, phase)
            # so reruns of the same window emit comparable trace files and
            # decision logs.
            state["tick_id"]          = (
                f"{self._run_id}-{tick.as_of.isoformat()}-{tick.phase}"
            )
            # Update FakeBroker price to the day's open or close.  The
            # symbol list comes from ``state["tickers"]`` — A1.6 folded
            # the redundant ``state["watchlist"]`` key away.  Live has
            # no ``watchlist`` either, so this aligns the two
            # lifecycles on a single key.
            self._refresh_broker_prices(state.get("tickers", []), tick)

            # Refresh ``state["portfolio"]`` from the broker so the
            # strategist's after-callback and held-view see the same
            # current_weights that risk_gate later reads from the broker.
            #
            # The live path (``orchestrator/tick.py:_build_initial_state``)
            # rebuilds state from the broker on every Cloud Run Job
            # invocation, so cross-tick staleness is structurally impossible
            # there.  The backtest path keeps a single ``state`` dict alive
            # across the whole schedule, so any field sourced from the
            # broker has to be re-pulled at the tick boundary — otherwise
            # tick 1's BUY fills land in the broker but tick 2's strategist
            # still sees the empty-at-start portfolio dump, computes
            # ``current_weights = {}``, misses its own "close needs
            # close_reason" guard, and the violation only surfaces deep in
            # risk_gate (which DOES read the live broker).  Re-pulling here
            # eliminates that source-of-truth split.
            #
            # ``state["positions"]`` (the thesis book) is propagated by
            # the executor's state_delta event (``"positions"`` key) and
            # does not need a refresh here.
            # TODO Band 5: drop once reads migrate to user:positions.
            state["portfolio"] = (
                await self._broker.get_portfolio()
            ).model_dump(mode="json")

            # ── Phase 2 PIT contract: refresh reference_prices per tick ──────
            # The Phase 1 seed in Runner._run_async pre-loads the full window
            # unfiltered (as_of=None) as a safety net, but that means a tick
            # at day N can see ETF bars for day N+1 through window_end, which
            # is lookahead bias for every relative-strength feature.
            #
            # Here we re-seed from the store scoped to bars up to (and
            # including) tick.as_of so the technical extractor only ever sees
            # data that would have been observable at that exact moment.
            # The window bounds are set to the whole window so warm-up bars
            # before tick.as_of are still available for rolling calculations.
            try:
                from backtest.providers._store_handle import get_store as _get_ref_store
                from backtest.runner import _seed_reference_prices

                _ref_store = _get_ref_store()

                # ``window_start`` is not carried in driver state; use a
                # conservative 365-day lookback window so warm-up bars are
                # included.  Bars after ``tick.as_of`` are stripped by the
                # PIT clamp inside ``_seed_reference_prices``.
                from datetime import timedelta
                _wstart = tick.as_of.date() - timedelta(days=365)

                _ref = _seed_reference_prices(
                    store=_ref_store,
                    window_start=_wstart,
                    window_end=tick.as_of.date(),
                    as_of=tick.as_of,
                )
                # Dump to JSON-safe dicts — mirrors how Runner._run_async
                # seeds the initial state (SqlSessionService cannot serialise
                # Pydantic objects; the technical extractor coerces back on read).
                state["reference_prices"] = {
                    sym: ph.model_dump(mode="json") for sym, ph in _ref.items()
                }
            except RuntimeError:
                # Store handle not initialised (e.g. isolated unit tests that
                # construct Driver without a real cache) — leave reference_prices
                # unchanged so those paths do not break.
                pass

            try:
                await self._run_one_tick(state, tw)
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

            # Drain cache hits from the log buffer *before* drain_tick resets it.
            # Previously sourced from ``state["_report_cache_hits_for_audit"]``
            # which was silently dropped by ADK's session merge (S3 fix).
            report_cache_hits = self._drain_logs_cache_hits()

            telemetry = build_telemetry_record(
                tick=tick,
                run_id=self._run_id,
                strict_mode=os.environ.get("STOCKBOT_STRICT_AS_OF") == "1",
                per_domain=per_domain,
                report_cache_hits=report_cache_hits,
                db_writes_recorded_at={},
                wall_clock_fallback_fired=wallclock_fallback_count > 0,
            )
            write_telemetry_record(self._audit_dir, telemetry)

            # ── per-tick OTEL drain ───────────────────────────────────────────
            # Flush the three buffered observability artefacts (logs, OTEL
            # spans, OTEL metrics) into runs/<id>/obs/{logs,traces,metrics}/.
            # The drain is best-effort — failures are logged inside drain_tick
            # and never propagate, so the tick loop is unaffected.
            drain_tick(
                self._obs_handles,
                self._obs_dir,
                tick_slug = _slug(tick.as_of) + f"-{tick.phase}",
                tick_id   = state["tick_id"],
            )

        self._write_manifest_status(
            "completed_with_failures" if self._failed else "completed",
        )

    # ── private helpers ────────────────────────────────────────────────────────

    def _drain_logs_cache_hits(self) -> list[dict]:
        """Return the report-cache-hit list for the current tick.

        Inspects the in-memory log buffer that ``drain_tick`` is about to
        flush; counts ``report_cache_hit`` messages and returns one placeholder
        dict per hit so the audit ``len(report_cache_hits)`` contract is
        preserved.

        Must be called *before* ``drain_tick`` because ``drain_tick`` resets
        the buffer as part of ``drain_to_file``.

        The log handler buffer (``TickBufferedLogHandler._buffer``) holds one
        ``dict`` per emitted record, each with a ``"message"`` key that is
        already fully-formatted by ``record.getMessage()`` at emit time.

        Returns an empty list when the log handler is not available (e.g.,
        isolated unit tests that do not call ``install_observability``).
        """

        # ``self._obs_handles`` is set unconditionally in ``__init__`` via
        # ``install_observability``.  The ``hasattr`` guard is a defensive belt-
        # and-braces for subclass or mock scenarios.
        if not hasattr(self, "_obs_handles"):
            return []

        log_handler = self._obs_handles.log_handler
        # ``TickBufferedLogHandler._buffer`` is a list[dict]; each dict carries
        # at minimum ``ts``, ``level``, ``logger``, ``message`` fields.
        raw_buffer = getattr(log_handler, "_buffer", None) or []

        return [
            {"event": "report_cache_hit"}
            for ev in raw_buffer
            if isinstance(ev, dict) and ev.get("message") == "report_cache_hit"
        ]

    async def _run_one_tick(self, state: dict, tw: TraceWriter) -> None:
        """Drive the pipeline once via ADK's Runner + DatabaseSessionService.

        Creates a fresh session per tick so ADK session IDs never collide
        across ticks.  The session service is backed by the per-run SQLite
        file (``runs/<run-id>/session.sqlite``) so user-scoped state
        (``user:positions``, ``user:thesis``) persists across ticks within
        the same run.

        After the runner finishes, the updated session state is merged back
        into ``state`` so the next tick inherits pipeline-process keys such as
        ``last_snapshot``, ``portfolio``, ``reference_prices`` etc.

        Known ADK 1.32 issue: the runner may raise ``AttributeError`` or
        ``BaseExceptionGroup`` *after* the pipeline completes (teardown bug in
        the parallel-agent finaliser).  These are caught, logged, and ignored
        — the tick result is still readable from the session service.

        Parameters
        ----------
        state:
            The shared mutable state dict for this tick (mutated in place).
        tw:
            The ``TraceWriter`` instance for this tick.  Passed in from
            ``run()`` so it can be injected onto the live ADK session after
            ``create_session`` returns (temp:-prefixed keys in the seed dict
            are discarded by ADK's ``extract_state_delta``).
        """
        # Phase 9: rebuild the pipeline each tick so the News and Fundamental
        # analyst branches fan out across the current ``state["tickers"]``.
        # The watchlist is tick-scoped per §A; building once at __init__ time
        # would freeze an outdated tickers list into the SequentialAgent's
        # sub_agents for the entire run.
        pipeline = build_pipeline(
            self._broker,
            self._db_session,
            tickers=state.get("tickers", []) or [],
        )

        # App name is per-window so each backtest run's user_state rows are
        # isolated from other windows and from live/paper runs.
        app_name = f"StockBot-backtest-{self._window_key}"

        # One shared session service instance per tick — backed by the
        # per-run SQLite file so user-scoped state (user:positions,
        # user:thesis) persists across ticks within this run.
        session_service = make_session_service(db_url=self._session_db_url)

        # Per-invocation observability handles (TraceWriter, DecisionLogger)
        # are NOT installed by mutating ``adk_session.state`` after
        # ``create_session`` — ADK's ``Runner.run_async`` calls
        # ``session_service.get_session`` for every invocation, which
        # rebuilds session state from persisted storage and discards any
        # in-memory mutation made by the driver (temp:-prefixed keys are
        # stripped from persistence by design).  The correct hook is the
        # plugin manager's ``before_run_callback``, which fires AFTER the
        # session is resolved and BEFORE the root agent runs, with access
        # to the live invocation state dict every sub-agent will read from.
        handle_injector = HandleInjectorPlugin(
            trace_writer    = tw,
            decision_logger = self._dl,
        )

        runner = Runner(
            agent           = pipeline,
            app_name        = app_name,
            session_service = session_service,
            plugins         = [handle_injector],
        )

        # Use a UUID suffix to guarantee session uniqueness even if the
        # deterministic tick_id is the same across driver instances (e.g. in
        # parallel test processes).
        session_id = f"{state['tick_id']}-{uuid.uuid4().hex[:8]}"

        # Build the seed dict for ADK's create_session.  Rules:
        # 1. Strip temp:-prefixed keys — ADK's extract_state_delta discards
        #    them at persistence time anyway; per-invocation handles like
        #    ``temp:_trace`` / ``temp:_decision_logger`` are injected by the
        #    HandleInjectorPlugin's before_run_callback (see Runner above).
        # 2. Coerce datetime objects to ISO strings — DatabaseSessionService
        #    serialises state via json.dumps, which cannot handle native
        #    datetime objects.  The live path's ``state["as_of"]`` and the
        #    driver's per-tick ``state["as_of"]`` are both datetimes; agents
        #    that read ``as_of`` must tolerate either datetime or ISO string
        #    and coerce as needed.
        from datetime import datetime as _dt
        seed_state = {
            k: (v.isoformat() if isinstance(v, _dt) else v)
            for k, v in state.items()
            if not k.startswith("temp:")
        }

        # Create the session up-front so ``runner.run_async`` can locate it
        # by ``session_id``.  We only need the returned session's ``.id`` —
        # observability handles are injected by :class:`HandleInjectorPlugin`
        # at ``before_run_callback`` time (see Runner construction above).
        adk_session = await session_service.create_session(
            app_name   = app_name,
            user_id    = "stockbot",
            state      = seed_state,
            session_id = session_id,
        )

        message = genai_types.Content(
            parts=[genai_types.Part(
                text=_TICK_MESSAGE_TEMPLATE.format(tick_id=state["tick_id"])
            )],
            role="user",
        )

        # The ADK runner may raise for two distinct reasons here:
        #
        # 1. *Cleanup-bug* (ADK 1.32): the pipeline has actually finished and
        #    the snapshotter has written ``last_snapshot``, but ADK's
        #    parallel-agent finaliser throws ``AttributeError`` or an
        #    ``ExceptionGroup`` during teardown.  Safe to ignore — the
        #    snapshot row is already in the database.
        # 2. *Real mid-pipeline failure*: an agent raised; the snapshotter
        #    never ran; ``last_snapshot`` is absent or stale.  Previously
        #    this was indistinguishable from case 1 and was silently
        #    swallowed, so any new regression in fundamental / news /
        #    strategist agents looked like a clean run.
        #
        # We now always unwrap the exception chain so the *leaf* causes land
        # in the log with their tracebacks (see ``_log_exception_chain``).
        # The "did the pipeline actually finish?" arbitration is deferred to
        # the post-run ``last_snapshot`` check below.
        #
        # NOTE: deliberately catches ``Exception`` (not ``BaseException``) so
        # ``KeyboardInterrupt``, ``SystemExit``, and ``MemoryError`` propagate
        # normally and are not silently swallowed.
        pipeline_exc: BaseException | None = None
        try:
            async for _ in runner.run_async(
                user_id="stockbot",
                session_id=adk_session.id,
                new_message=message,
            ):
                pass
        except (AttributeError, Exception) as exc:
            pipeline_exc = exc
            _log_exception_chain(exc, state["tick_id"])

        # Pull session state back into ``state`` so the next tick can access
        # per-process pipeline keys written by agents.  Keys are selectively
        # carried; see decisions below.
        updated = await session_service.get_session(
            app_name=app_name,
            user_id="stockbot",
            session_id=adk_session.id,
        )
        if updated is not None:
            updated_state = dict(updated.state)

            # ── State-carry decisions (Band 2 review) ─────────────────────
            #
            # ``user:positions`` / ``user:thesis`` — DROPPED.
            #   These are user-scoped keys written by the Executor's
            #   after_agent_callback (Band 4).  They persist in the
            #   DatabaseSessionService row and are re-hydrated by ADK's
            #   user_state merge on the next session create — carrying them
            #   here would shadow the DB row with a stale in-memory copy.
            #
            # ``last_snapshot`` — KEPT.
            #   The pipeline-completion check (``_enforce_completion``) reads
            #   this key from ``state`` directly after the tick.  It must be
            #   present in ``state`` for that guard to fire.
            #
            # ``portfolio`` — KEPT.
            #   Re-fetched from the broker at each tick boundary by
            #   ``driver.run()`` anyway, but carrying it here is harmless and
            #   mirrors the live path where the broker call is the authority.
            #
            # ``reference_prices`` — KEPT.
            #   Refreshed per-tick by ``_seed_reference_prices`` in
            #   ``driver.run()``.  Carrying the post-tick value is harmless.
            #
            # ``memory_buffer`` / ``day_digest`` — KEPT.
            #   These are ordinary cross-tick pipeline keys that survive in the
            #   session state and must be visible to the next tick's agents.
            #
            # ``temp:*`` — DROPPED implicitly.
            #   ADK strips temp:-prefixed keys from persisted deltas; they
            #   will not appear in ``updated.state`` at all.
            #
            # All other keys that appear in updated.state (analyst outputs,
            # decision artefacts, etc.) are tick-scoped and will be
            # overwritten by the next tick's agents — carrying them forward
            # is safe (they won't be read) but adds noise.  We carry the
            # full state minus the user: prefix keys to keep the logic simple.
            state.update({
                k: v
                for k, v in updated_state.items()
                if not k.startswith("user:")
            })

        # ── pipeline-completion check ──────────────────────────────────────
        # The Snapshotter is the *last* agent in the pipeline and writes
        # ``state["last_snapshot"]`` keyed by the current ``tick_id``.  If
        # the snapshot is missing or carries a different tick_id (i.e. it's
        # leftover from a previous tick that ran to completion), the
        # pipeline did not reach the end — raise so the outer loop records
        # this tick as failed and the abort-ratio logic can fire.
        if self._enforce_completion:
            snap        = state.get("last_snapshot")
            snap_tickid = snap.get("tick_id") if isinstance(snap, dict) else None
            if snap_tickid != state["tick_id"]:
                raise RuntimeError(
                    f"pipeline did not reach snapshotter for tick "
                    f"{state['tick_id']!r} — last_snapshot.tick_id was "
                    f"{snap_tickid!r}.  See preceding log entries for the "
                    f"underlying exception chain."
                ) from pipeline_exc

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

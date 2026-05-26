# Source audit — src/observability/

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 7 (`__init__.py`, `trace.py`, `terminal_log.py`, `otel_setup.py`, `exporters.py`, `log_handler.py`, `drain.py`)
**Findings:** 0 P0 · 2 P1 · 3 P2 · 2 P3

## Summary

The subsystem is two coexisting observability layers: (a) `TraceWriter` (manual per-boundary JSON snapshots, ~40 `_trace_maybe` call sites across agents) and (b) the OTEL stack (`otel_setup` + `exporters` + `log_handler` + `drain`) that taps ADK's native span/metric emission per tick. The 7-file split is justified by genuinely distinct jobs — there is no parallel old/new wiring within the subsystem. Rule 8 compliance is good: observability writers only ever read/write `temp:`-prefixed handles (`temp:_trace`, `temp:_decision_logger`, `temp:_obs_<analyst>_calls`, `temp:_llm_start_<…>`), never contract-bearing keys. The OTEL stack is **backtest-only**: `install_observability` is called once in `backtest/driver.py:179` and the live tick entrypoint (`orchestrator/tick.py`) never wires it — that is a legitimate §D-1 additive carve-out, but the subsystem docstring (`__init__.py`) is silent on the asymmetry. Two cross-subsystem dependencies the consolidator should know about: (1) the consumer-side `_drain_logs_cache_hits` in `backtest/driver.py:389` reaches into `TickBufferedLogHandler._buffer` and uses observability records as an audit-telemetry data channel — that coupling reverses the "observability is a sink, not a source" intent of Rule 8 from the consumer side; (2) `scripts/trace_tick.py` seeds the wrong key (`state["_trace"]` instead of `state["temp:_trace"]`), so the manual surface-trace harness has been emitting empty JSON files unnoticed.

## Findings

### P1-01 · C7 doc/code drift · `trace_tick.py` uses `state["_trace"]` but every reader looks up `state["temp:_trace"]`

- **Location:** `src/observability/trace.py:4` (module docstring), `src/observability/trace.py:6` (function docstring), `src/observability/trace.py:127`, `src/observability/trace.py:225` — and the bug it documents lives in `scripts/trace_tick.py:130` (`"_trace": tw`) and `scripts/trace_tick.py:160` (`tw = adk_session.state["_trace"]`).
- **Confidence:** high
- **Description:**
  `trace.py:4` claims: "the ``trace_tick.py`` entrypoint sets ``state["temp:_trace"]`` to a TraceWriter". The actual `scripts/trace_tick.py` seeds the unprefixed `"_trace"` key, and `_trace_maybe(state, ...)` looks up `state.get("temp:_trace")` exclusively (`trace.py:157`). Every one of the ~40 `_trace_maybe` call sites across the agents — fetched, technical, social, smart_money, news, fundamental joiners, executor, risk_gate, strategist enricher, context_shim — therefore short-circuits to no-op when invoked from `trace_tick.py`. The resulting `docs/surface-traces/<tick>.json` will contain only sections written by the explicit `make_llm_trace_callbacks` path (which uses `callback_context.state.get("temp:_trace")` — same key, so also returns `None`), i.e. effectively empty. `scripts/trace_tick.py` is the only entrypoint that creates a `TraceWriter` outside the backtest driver, so this is the entire production-trace user-facing path and it has been silently degraded since the `temp:_trace` rename landed (Spec B). Severity-wise this is a P1 not a P0 because trace_tick is a manual debugging tool — but it is filed against this subsystem because the trace.py docstring is the authoritative claim about what the helper expects, and that claim no longer matches the script. Per RUBRIC §2 C7, a fix to `scripts/trace_tick.py` is out of audit scope (no `src/` edit needed inside observability), but the drift in `trace.py`'s docstrings is in scope.
- **Suggested action:**
  Update `trace.py:4` and `trace.py:127` docstrings to match reality (either by noting that `trace_tick.py` is currently broken and seeds the wrong key, pending a fix in the scripts workstream, or by holding the doc as target-state and flagging the `trace_tick.py` divergence to that workstream). The actual fix — renaming `"_trace"` to `"temp:_trace"` in `scripts/trace_tick.py` — belongs to the scripts audit.

### P1-02 · C5 silent-failure attractor · TraceWriter snapshot failures swallowed; `make_observability_callbacks.after_cb` swallows `usage_metadata` extraction errors

- **Location:** `src/observability/trace.py:168-174`, `src/observability/terminal_log.py:403-410`.
- **Confidence:** medium
- **Description:**
  Two places where the subsystem swallows exceptions to a `_LOGGER.exception` or `pass`:
  (a) `_trace_maybe` (trace.py:168) wraps `tw.snapshot(...)` in `try/except Exception: _LOGGER.exception(...)`. The comment justifies this — "the no-op *production* path is never affected by trace-side failures" — and the rationale is sound for production where tracing is disabled. But the **trace-mode** path is exactly where a serialiser exception would silently drop a section and produce a misleading-but-non-empty trace; the failure mode that lost the tick-1 `03_strategist` section in baseline-2025-09 is restated in the comment as already-fixed, yet the same shape can still drop sections one-at-a-time without any operator-visible signal beyond a buried log line.
  (b) `make_observability_callbacks.after_cb` (terminal_log.py:403-410) wraps the `usage_metadata` extraction in `try/except Exception: pass` — fully silent (no log). A future ADK change that renames `usage_metadata` (or returns it as an unexpected shape) would silently zero out every token-counter row in the analyst summary table with zero diagnostics; the operator would see "0 tok total" rows and assume tokens were free, not that observability broke.
  Per the user-memory `feedback_silent_failures_loud_tests`, this is the repo's recurring bug class. (a) is more defensible than (b) — for (a) tracing is opt-in and a `_LOGGER.exception` line at least shows up; for (b) the swallow is silent. Confidence is `medium` because Rule 8 (observability must not crash the pipeline) is genuinely load-bearing — these except-blocks exist to honour it. Consolidation may decide the right answer is "log loudly, do not raise" rather than "raise".
- **Suggested action:**
  Narrow both excepts to the specific failure modes that have been observed (e.g. `JSONEncodeError` for trace serialisation, `AttributeError` for `usage_metadata` shape drift) and let unexpected exceptions propagate; or at minimum convert the terminal_log.py:408 silent `pass` into a `_LOGGER.warning(...)` so a future shape drift is visible.

### P2-01 · C1 dead code · `emit_analyst_totals` and `emit_analyst_header` have no callers

- **Location:** `src/observability/terminal_log.py:630` (`emit_analyst_totals`) and `src/observability/terminal_log.py:687` (`emit_analyst_header`).
- **Confidence:** high
- **Description:**
  `grep -rn 'emit_analyst_totals\|emit_analyst_header' src/ scripts/ tests/` finds only the two definitions themselves — no callers in `src/`, no callers in `scripts/`, and no test exercising them. The module's own header comment (terminal_log.py:23-29) lists `emit_analyst_summary` as the live function and makes no reference to either dead helper. The `emit_analyst_totals` docstring at line 641 self-describes as "retained for backwards compatibility only" — but there are no callers left to be compatible with. Both functions are pure C1 dead code per the RUBRIC §2 definition (no live callers anywhere in `src/`, `tests/`, or `scripts/`). The legacy comment block on lines 626-628 ("Legacy compatibility shim — kept so existing callers don't break at import") can be deleted with the functions.
- **Suggested action:**
  Delete `emit_analyst_totals` (terminal_log.py:630-684), `emit_analyst_header` (terminal_log.py:687-702), and the surrounding "Legacy compatibility shim" comment header. The `format_tokens` / `format_latency` helpers used by `emit_analyst_totals` are still referenced by `emit_analyst_summary` and stay.

### P2-02 · C3 overabstraction (low confidence) · `AgentLifecycleLogger` is a SpanProcessor whose only output duplicates an INFO log line already supplied by ADK at standard verbosity

- **Location:** `src/observability/otel_setup.py:44-83` (class) and `otel_setup.py:157` (wire-up).
- **Confidence:** low
- **Description:**
  `AgentLifecycleLogger` is a `SpanProcessor` that fires on `on_end` of each `invoke_agent` span and emits one INFO line per closed span to a dedicated `stockbot.lifecycle` logger. The class is wired alongside the buffered span exporter so it gets every span. Its docstring describes the value as "one human-readable line per top-level agent invocation". Two concerns: (1) ADK 1.34 already emits start/end log lines at INFO on the `google_adk` namespace — `terminal_log.py:192` deliberately clamps those to WARNING because they were judged "drown out our structured output with no useful information"; `AgentLifecycleLogger` then turns the equivalent data back on through a different channel. (2) Confidence is `low` because the lifecycle line is on a separate logger (`stockbot.lifecycle`) that the operator can mute independently, and a "done in N ms" derivation IS slightly richer than ADK's bare "agent end" emission, so the indirection buys something — just not much. The class also has the cost of `on_start`, `shutdown`, `force_flush` boilerplate implementing a `SpanProcessor` interface for a one-line emission. Mention rather than file aggressively.
- **Suggested action:**
  Either keep as-is (consolidation may decide the separate-logger mute knob is worth the indirection) or replace with a one-line filter on the existing log handler / terminal log that picks `google_adk` "agent end" records and reformats them. Not urgent.

### P2-03 · C7 doc/code drift · `__init__.py` describes both layers as coexisting but is silent on which lifecycles each runs in

- **Location:** `src/observability/__init__.py:1-17`.
- **Confidence:** high
- **Description:**
  The package docstring lists TraceWriter and the OTEL stack as "two coexisting layers" but makes no statement about *where* each runs. In practice: TraceWriter runs in backtest (driver injects it at `backtest/driver.py:512`) and runs nowhere live (smoke_run.py imports only `setup_terminal_logging`; orchestrator/tick.py imports only `_TICK_LOGGER`); the OTEL stack runs only in backtest (`install_observability` is called only at `backtest/driver.py:179` and never in the live tick path). The `trace.py` docstring at line 3-6 explicitly says "Production runs do not instantiate this" — but the §D-1 additive carve-out from `contract-invariants.md` formally permits the asymmetry, and that fact belongs in the package overview so a reader doesn't conclude the live tick is missing observability by accident. The current shape of the doc reads like "two layers, both available everywhere"; the reality is "two layers, both backtest-only, by §D-1 carve-out".
- **Suggested action:**
  Add one sentence to `__init__.py` noting the §D-1 carve-out and the backtest-only wiring. No code change.

### P3-01 · C1/C7 trivial · `terminal_log.py` `ok_marker` ternary picks `"✓"` either way

- **Location:** `src/observability/terminal_log.py:515`.
- **Confidence:** high
- **Description:**
  `ok_marker = "✓" if not failed else "✓"  # always ✓ for completed ones`. The ternary collapses to the constant `"✓"`. Either the original intent was `"✗"` when failed and the code was incompletely simplified, or the ternary was a leftover from a refactor and should just be a constant. Cosmetic.
- **Suggested action:**
  Either replace with `ok_marker = "✓"` (matching the apparent current intent) or restore the originally-intended `"✗"` for failed rows. Land alongside the next touch to the file.

### P3-02 · C3 (cosmetic) · `TraceWriter._sections` direct write at `trace.py:292-295` bypasses the public `snapshot` API

- **Location:** `src/observability/trace.py:289-296` (in `make_llm_trace_callbacks._after`).
- **Confidence:** high
- **Description:**
  The `_after` callback overwrites the `_in` placeholder by writing directly into `tw._sections[f"{section_name}_out"] = {...}`, with a comment saying "TraceWriter has no public update method for this case yet". This is a private-attribute write from a different module-level function within the same file — harmless, but it puts the "overwrite existing section" mechanic outside the documented `snapshot` API. The natural shape is either to extend `snapshot` to accept an optional `overwrite=True` flag or to add a tiny `replace(label, payload)` method; the current direct dict mutation is fine but mildly leaky.
- **Suggested action:**
  Add a `TraceWriter.replace(label, ...)` method (or accept the direct write as adequate). Cosmetic.

## Notes for the consolidator

- **Cross-subsystem coupling — audit reads observability buffer.** `src/backtest/driver.py:389-422` (`_drain_logs_cache_hits`) reaches into `self._obs_handles.log_handler._buffer` and scans for `report_cache_hit` records to drive audit telemetry. The observability log handler is becoming a data channel for audit, not just a sink. From the observability subsystem's point of view this is fine — the buffer is just public-ish state — but it inverts Rule 8's "observability does not feed back into the contract surface". File the finding against `backtest/` consolidation, not here.
- **OTEL stack is backtest-only by design.** `install_observability` is called once at `backtest/driver.py:179` and nowhere else. `orchestrator/tick.py` (the live tick) does not install OTEL and does not inject `temp:_trace` / `temp:_decision_logger`. This is conformant with §D-1, but consolidation should confirm the live lifecycle has its own observability story planned (production target per `__init__.py` docstring is "GCS"). If the production OTEL wire-up is still future work, that is its own backlog item rather than a finding against the current subsystem.
- **No contract violations found.** The subsystem honours Rule 8: every state read/write is to a `temp:`-prefixed key. The `temp:_obs_<analyst>_calls`, `temp:_llm_start_<analyst>_<ticker>`, `temp:_trace`, `temp:_decision_logger` keys all match the Rule 2 carve-out for invocation-scoped observability handles described at `contract-invariants.md:308-350`.

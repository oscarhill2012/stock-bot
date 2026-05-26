# ops module audit (2026-05-26)

Source scope: `src/observability/`, `src/baselines/`, `src/deploy/`,
`src/config/`. Tests scope: every file under `tests/` that imports any of the
above.

The project is pre-deployment; flag any code that assumes deployment exists
(no live, no paper, no GCS, no Cloud Run, no remote OTEL collector).

---

## F-ops-001
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `scripts/trace_tick.py:118-160` (entrypoint, not under
  `src/`, but it is the only consumer of `TraceWriter` outside the
  backtest driver and was the original "ground-truth" use case for the
  module).
- **Evidence:**
  ```
   118    tw = TraceWriter()
   …
   130        "_trace":        tw,                       # bare key — not "temp:_trace"
   …
   151        state=initial_state,
   152    )
   …
   160    tw = adk_session.state["_trace"]
  ```
  Compare with `src/observability/handle_injector_plugin.py:1-42`
  which documents the exact bug:
  > "The backtest driver historically installed per-invocation
  > observability handles by mutating `adk_session.state` immediately
  > after `session_service.create_session(...)`. That pattern is silently
  > discarded by ADK. … across the entire history of the backtest
  > harness, every run's `traces/<tick>.json` was an empty `{}` …"
- **Intent violated:** §C-Rule 2 "Runtime observability handles ride on
  `temp:`" (contract-invariants.md). `trace_tick.py` uses a bare
  `"_trace"` key (which would not survive `extract_state_delta`) and does
  not use the `HandleInjectorPlugin`. It only works at all because the
  script runs against `InMemorySessionService`; the moment anyone reuses
  this pattern with `DatabaseSessionService` it silently empties.
- **Suggested action:** investigate — does anyone still run
  `scripts/trace_tick.py`? If yes, migrate to `HandleInjectorPlugin`. If
  no, delete the script. Either way it is the last living instance of
  the broken pattern.
- **Notes:** out of formal audit scope (not under `src/`) but called out
  because it is the only remaining consumer of `TraceWriter` outside the
  driver and silently reproduces the bug `handle_injector_plugin.py`
  exists to prevent.

## F-ops-002
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/deploy/` (entire directory).
- **Evidence:** `ls -la src/deploy/` returns only `.` and `..`. No
  `__init__.py`, no source, no tests. `grep -r "from deploy\|import
  deploy" src/ scripts/ tests/` returns zero hits.
- **Intent violated:** intent.md §2.11 says deploy is "Empty (deployment
  scaffolding deferred)" — so intent matches reality, but the empty
  package adds no value.
- **Suggested action:** delete the empty directory; reinstate it
  alongside actual deploy code when Cloud Run scaffolding lands.
  Alternatively leave a placeholder `__init__.py` with a one-line
  comment if the user wants to keep the slot reserved.

## F-ops-003
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/observability/terminal_log.py:663-717`
  (`emit_analyst_totals`) and `:720-734` (`emit_analyst_header`).
- **Evidence:** `grep -rn "emit_analyst_totals\|emit_analyst_header"
  src/ scripts/ tests/` returns only the definitions in
  `terminal_log.py`. Zero call sites. The module's own docstring at
  `:660-661` admits `emit_analyst_totals` is a "Legacy compatibility
  shim — kept so existing callers don't break at import".
- **Intent violated:** n/a — these are pre-Phase-9 helpers. The
  contract says nothing about them.
- **Suggested action:** delete both. The "kept so existing callers
  don't break" rationale is self-falsifying — `grep` proves no
  importers exist.

## F-ops-004
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/observability/otel_setup.py:219-228`
  (`get_handles`).
- **Evidence:** `grep -rn "get_handles" src/ scripts/ tests/` returns
  only the definition. Zero callers. The driver calls
  `install_observability` and stashes the return value on
  `self._obs_handles`.
- **Intent violated:** n/a.
- **Suggested action:** delete `get_handles`. If a future caller needs
  "the installed bundle if any", `install_observability` is already
  idempotent and returns the same object.

## F-ops-005
- **Category:** dead-code
- **Severity:** P2 (compat shim is signposted by name; not actively
  harmful but adds module surface)
- **Location:** `src/baselines/spy.py` — `SPYMetrics` and
  `_metrics_from_series`.
- **Evidence:** `grep -rn "_metrics_from_series\|SPYMetrics" src/
  scripts/` shows zero production callers. Only `tests/unit/test_spy_metrics.py`
  and `tests/unit/baselines/test_spy_metrics_removed.py` import these
  symbols. The module docstring at `src/baselines/spy.py:3-7` admits
  `spy_metrics` was deleted "had zero callers" and that
  `_metrics_from_series` is preserved "for tests and the reporting
  layer" — but `src/backtest/reporting.py` computes its own
  `_annualised_sharpe` at line 545 and never imports from
  `baselines.spy`.
- **Intent violated:** intent.md §2.11 names baselines as "SPY
  buy-and-hold metrics for backtest comparison" — yet the only
  baseline-metric callers are tests of itself.
- **Suggested action:** investigate dedupe with
  `src/backtest/reporting.py` (`_annualised_sharpe` and drawdown logic)
  — either reporting.py should call `_metrics_from_series`, or
  `baselines/spy.py` should be deleted along with its two test files.
  The docstring is presently a lie ("used by … the reporting layer" —
  it isn't).

## F-ops-006
- **Category:** dead-test
- **Severity:** P2
- **Location:** `tests/unit/baselines/test_spy_metrics_removed.py` —
  the whole file.
- **Evidence:** the test exists to assert a Phase 7 deletion stays
  deleted (`assert not hasattr(spy, "spy_metrics")`). Phase 7 is now
  ancient history; the test no longer protects a live concern. If
  F-ops-005 is acted on and the whole `baselines/spy.py` module is
  retired, this regression guard becomes meaningless.
- **Intent violated:** test-policy.md §A.7 "Tests must surface silent
  failures loudly" — this test surfaces nothing; it pins a deletion.
- **Suggested action:** delete once F-ops-005 is resolved.

## F-ops-007
- **Category:** silent-failure
- **Severity:** P2
- **Location:** `src/observability/terminal_log.py:438-445`
  (`make_observability_callbacks._after`).
- **Evidence:**
  ```
   438        try:
   439            meta = getattr(llm_response, "usage_metadata", None)
   440            if meta is not None:
   441                prompt_tokens    = getattr(meta, "prompt_token_count",     None)
   442                candidate_tokens = getattr(meta, "candidates_token_count", None)
   443        except Exception:
   444            # Defensive — never crash the pipeline on observability code.
   445            pass
   ```
  Bare `except Exception: pass` swallows every error from
  usage-metadata extraction. Rule 8 says observability is additive
  (must not crash the pipeline), but a silent swallow with no log
  means a future Gemini SDK change that renames `usage_metadata` would
  produce zero-token rows in every terminal summary for the lifetime
  of the regression.
- **Intent violated:** test-policy.md §A.7 / §G.8 "branch_failed
  warnings are not benign" — same class of silent-swallow problem.
- **Suggested action:** `except Exception: logger.exception(…); pass`
  so the operator sees a one-shot warning. Same fix pattern that
  `observability.trace._trace_maybe` already adopted (see
  `trace.py:170-174`).

## F-ops-008
- **Category:** over-abstraction
- **Severity:** P2
- **Location:** `src/observability/otel_setup.py:187-208` — the
  `captured_namespaces` tuple of ten logger names.
- **Evidence:**
  ```
   187    captured_namespaces = (
   188        "google_adk", "stockbot", "agents", "backtest",
   189        "orchestrator", "observability", "data", "broker",
   190        "contract", "config",
   191    )
  ```
  Plus comment at `:185-186`: "`stockbot` is reserved for any future
  namespace migration but harmless if currently unused." That is two
  log-name systems coexisting (`stockbot.*` aspirational; bare
  package names actual). The handler ends up attached to both for no
  current benefit.
- **Intent violated:** n/a.
- **Suggested action:** investigate — either commit to the
  `stockbot.*` namespace and migrate, or delete the `stockbot` entry
  from the tuple and stop pretending it captures anything.

## F-ops-009
- **Category:** test-gap
- **Severity:** P2
- **Location:** `tests/unit/config/` — only
  `test_analysts_config.py` and `test_strategist_config.py` exist;
  no test files for `config.models`, `config.retry_429`,
  `config.risk_gate`, `config.schedule`. There is
  `tests/unit/test_schedule_config.py` and
  `tests/unit/test_risk_gate_config_loader.py` outside the mirrored
  layout, but `config.models` and `config.retry_429` have no
  dedicated loader tests at all.
- **Evidence:** `grep -rn "load_models_config\|load_retry_429_policy"
  tests/` returns zero matches.
- **Intent violated:** intent.md §2.11 "Centralised JSON config
  loaders … Single source of truth for all tuning knobs" — a missing
  key, a malformed numeric, or a regression in `_reset_cache` would
  pass CI silently.
- **Suggested action:** add minimal loader smoke tests for
  `config.models` and `config.retry_429` (valid payload + missing-key
  ValidationError + `_reset_cache` cycles `lru_cache`).

## F-ops-010
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:**
  - `src/config/analysts.py:187-207` —
    `AnalystsConfig.schema_cap` (integer-math headroom calculation).
  - `src/config/strategist.py:152-174` —
    `StrategistConfig.schema_cap` (identical integer-math headroom
    calculation, identical docstring, identical floating-point
    rationale).
- **Evidence:** both functions are byte-for-byte the same logic:
  `(prompt_cap * (100 + self.slack_percent) + 99) // 100`. Both have
  the same `200 * 1.1` floating-point comment. Two configs
  intentionally carry independent `slack_percent` knobs (per
  `src/config/analysts.py` docstring) so the *method body* sharing
  fits a small mixin / helper.
- **Intent violated:** n/a.
- **Suggested action:** extract a free function
  `apply_slack(prompt_cap: int, slack_percent: int) -> int` in a
  shared module (e.g. `config/_slack.py`) that both configs call.
  The per-config `slack_percent` attribute stays independent. Mild
  cleanup, not urgent.

## F-ops-011
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `config/README.md` (top table at lines 7-18).
- **Evidence:** `config/README.md` lists `data.json` and points the
  loader at `src/data/config.py` (`get_config()`), but the ops module
  audit's source scope only covers `src/config/`. The `src/data/config.py`
  loader is out of scope here — but the README's "one file per concern
  + a loader" pattern is breached for two cases visible from this
  module's vantage point:
  1. `config/watchlist_smoke.json` exists in `config/` but is not
     documented in `config/README.md` at all (search for
     "watchlist_smoke" returns zero hits in the README).
  2. `config/analyst_heuristics.json` is documented but its loader
     `src/agents/analysts/heuristics.py` is also out of `src/config/`'s
     scope — flagged here as a layering observation, not a fault.
- **Intent violated:** project convention (CLAUDE.md) "Each file
  covers one concern. A `README.md` in `config/` describes every
  file" — `watchlist_smoke.json` is undocumented.
- **Suggested action:** add a `watchlist_smoke.json` row to
  `config/README.md` (or delete the file if it is no longer used by
  any smoke script).

## F-ops-012
- **Category:** dead-code
- **Severity:** P3
- **Location:** `src/observability/otel_setup.py:231-241`
  (`_reset_for_tests`).
- **Evidence:** Used only by `tests/unit/observability/test_drain.py`
  and `tests/unit/observability/test_otel_setup.py`. Acceptable as a
  test hook (matches the `_reset_cache` pattern on config loaders),
  flagged P3 only because the docstring carries a heavy warning that
  is mostly worth keeping.
- **Suggested action:** keep; document at module level alongside
  other test hooks if a "test hooks" section ever lands.

## F-ops-013
- **Category:** dead-code
- **Severity:** P3
- **Location:** `src/baselines/__init__.py`.
- **Evidence:** Empty file (0 bytes per `wc -l`). Combined with
  F-ops-005's recommendation, the whole package may go.
- **Suggested action:** absorbed by F-ops-005.

## F-ops-014
- **Category:** dead-test
- **Severity:** P2
- **Location:** `tests/unit/observability/test_terminal_log.py`
  (whole file, ~750 lines).
- **Evidence:** Tests `format_tokens`, `format_latency`,
  `make_observability_callbacks`, `emit_analyst_summary` —
  comprehensive coverage. Not dead. **Cross-reference instead:**
  several test cases use `_TICK_LOGGER` / `_CALLS_LOGGER` names
  internally that this audit would not catch as silent-failure
  attractors. Worth a glance during test-policy review.
- **Suggested action:** keep. Listed here only for the parent
  reviewer's awareness — this is the canonical example of "test the
  observability surface positively". Not a finding.

---

## Cross-cutting observations (not findings, for the parent reviewer)

- The OTEL stack (`exporters.py`, `otel_setup.py`, `drain.py`) is **not**
  noop — it really wires global OTEL providers and buffers in memory.
  Intent says "OTEL setup — noop in pre-deployment state per intent
  drafters" but the code installs `TickBufferedSpanExporter` /
  `TickBufferedMetricExporter` and drains per-tick JSON. No remote
  collector is configured (no OTLP endpoint, no Cloud Trace, no GCS
  sink), so "noop" is true at the *export-to-remote* level only. The
  buffered drain to `runs/<id>/obs/{logs,traces,metrics}/<tick>.json` is
  live functionality, exercised by `src/backtest/driver.py:192,389`.
  Not a finding per se — it depends on whether the audit reviewer
  treats "writes to local FS" as deployment-implying. It does not.

- `HandleInjectorPlugin` is the right fix for the install-after-create
  bug; tests in `tests/unit/backtest/` exercise it through the driver
  rather than directly. No standalone unit test exists for the plugin
  itself (`grep "HandleInjectorPlugin" tests/` shows zero hits). Worth
  considering a one-shot test that asserts
  `before_run_callback` mutates the live `invocation_context.session.state`
  in-place — but that requires fabricating an `InvocationContext` and
  may not be worth the complexity vs. relying on the integration
  driver test.

- All four `src/config/*.py` loaders correctly use `@lru_cache(maxsize=1)`
  except `config/risk_gate.py` which has no `_reset_cache` helper
  (counterpart of every other loader). Not strictly a finding — tests
  that mutate `config/risk_gate.json` would need to call
  `get_risk_gate_config.cache_clear()` directly. Mild inconsistency.

- Drain failures in `src/observability/drain.py:45-48,61-68` use
  `_drain_logger.exception(...)` instead of bare `pass`. Good. No
  finding.

- `terminal_log.py:138-208` `setup_terminal_logging` is well-comment-ed
  and the allowlist filter behaviour is non-trivial — please prioritise
  reading the comments before any refactor.

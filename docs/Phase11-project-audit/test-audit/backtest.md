# Test audit — backtest subsystem (`src/backtest/`)

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/backtest.md` (primary); incidental cross-refs into `docs/Phase11-project-audit/source-audit/data-providers.md` and `docs/Phase11-project-audit/source-audit/orchestrator.md` where flagged.
**Test files in scope:** 50 (full list below)
**Tests collected from those files:** ~155 (counted via `pytest <paths> --collect-only -q` walks during discovery; an exact pytest collect was not re-run for this report because no test files were modified)
**Findings:** 1 P0 · 4 P1 · 5 P2 · 1 P3

## Files in scope

Discovery sweep used `grep -rln "from backtest\|src.backtest\|import backtest" tests/` plus `find tests -iname "*backtest*" -o -iname "*replay*" -o -iname "*cache*" -o -iname "*tripwire*" -o -iname "*leak*"` and a follow-up grep for each `src/backtest/` submodule. Files grouped by current location:

**`tests/backtest/`** (3):
- `tests/backtest/test_cache_hits_audit.py`
- `tests/backtest/test_reference_prices.py`
- `tests/backtest/test_tripwire_advisory_rename.py`

**`tests/backtest/audit/`** (4):
- `tests/backtest/audit/test_auditing_store.py`
- `tests/backtest/audit/test_audit_tick_smoke.py`
- `tests/backtest/audit/test_telemetry_record_shape.py`
- `tests/backtest/audit/test_tripwires.py`

**`tests/backtest/leak_regressions/`** (6):
- `tests/backtest/leak_regressions/test_cache_skip_includes_source_provider.py`
- `tests/backtest/leak_regressions/test_missing_timestamp_marks_row.py`
- `tests/backtest/leak_regressions/test_open_tick_excludes_sameday_bar.py`
- `tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py`
- `tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py`
- `tests/backtest/leak_regressions/test_report_cache_logs_originating_as_of.py`

**`tests/integration/backtest/`** (7):
- `tests/integration/backtest/test_backfill_smoke.py`
- `tests/integration/backtest/test_driver_failure_threshold.py`
- `tests/integration/backtest/test_driver_one_tick.py`
- `tests/integration/backtest/test_end_to_end_smoke.py`
- `tests/integration/backtest/test_fetcher_idempotent.py`
- `tests/integration/backtest/test_fresh_run_starts_clean.py`
- `tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py`

**`tests/unit/backtest/`** (18):
- `tests/unit/backtest/test_cache_providers.py`
- `tests/unit/backtest/test_cache_store.py`
- `tests/unit/backtest/test_decision_logger.py`
- `tests/unit/backtest/test_driver_consumes_tickers.py`
- `tests/unit/backtest/test_driver_keyboard_interrupt.py`
- `tests/unit/backtest/test_driver_per_tick_rebuild.py`
- `tests/unit/backtest/test_driver_portfolio_refresh.py`
- `tests/unit/backtest/test_driver_wallclock_telemetry.py`
- `tests/unit/backtest/test_reporting_forward_return_dates.py`
- `tests/unit/backtest/test_reporting_obs_aggregation.py`
- `tests/unit/backtest/test_reporting.py`
- `tests/unit/backtest/test_runner_initial_prices.py`
- `tests/unit/backtest/test_runner_initial_state_parity.py`
- `tests/unit/backtest/test_runner_sigint.py`
- `tests/unit/backtest/test_schedule.py`
- `tests/unit/backtest/test_settings.py`
- `tests/unit/backtest/test_wall_clock_leakage.py`
- `tests/unit/backtest/test_windows.py`

**`tests/unit/backtest/cache/`** (2):
- `tests/unit/backtest/cache/test_schema_version_mismatch.py`
- `tests/unit/backtest/cache/test_store_skipped_writes_counter.py`

**`tests/unit/`** (loose, 6):
- `tests/unit/test_decision_logger_strict_serialiser.py`
- `tests/unit/test_equity_curve.py`
- `tests/unit/test_plot_equity.py`
- `tests/unit/test_replay_backtest_cli.py`
- `tests/unit/test_reporting_span_names.py`
- `tests/unit/test_spy_metrics.py`

**`tests/unit/baselines/`** (1):
- `tests/unit/baselines/test_spy_metrics_removed.py`

**`tests/integration/`** (1, root-level):
- `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`

## Summary

The backtest suite has two large strengths and one load-bearing gap. Strengths: (i) the end-to-end smoke (`tests/integration/backtest/test_end_to_end_smoke.py`) explicitly asserts the three definitive-leak tripwires do not fire after a full single-tick replay, and (ii) the writer-side wall-clock tests (`tests/unit/backtest/test_wall_clock_leakage.py`) are a model of policy-aligned positive-content assertions. Gap: there is **no test anywhere** that fires the `any_filter_key_after_as_of` tripwire specifically for `notable_holders` — the exact path silently disabled by source-audit `backtest.md` P0-01 (`as_of_date` mapped instead of `filed_at`). The remaining findings are layout sprawl (six directories), a handful of weak completion-only assertions in driver tests, a wide-scope monkeypatch on `Runner` internals, and one borderline §A.3 multi-tick fixture.

## Findings

### P0-01 · T4 missing surfacing test · `notable_holders` leak detection has no firing-test

- **Location(s):** new test needed — extend `tests/backtest/audit/test_tripwires.py` with a `notable_holders` parallel of `test_filter_key_after_as_of_fires`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/backtest.md` P0-01 (`notable_holders` mapped to non-existent `as_of_date` in `src/backtest/audit/telemetry.py:187` and `src/backtest/audit/upstream_verifier.py:101-102`; the canonical model field is `filed_at`).
- **Confidence:** high.
- **Description:**
  `tests/backtest/audit/test_tripwires.py` exercises `any_filter_key_after_as_of` for `news` (line ~30) and `open_tick_sameday_bar_advisory` / its absence for `price_history` (lines ~60 and ~95) but no test in the suite seeds a `notable_holders` row with `filed_at > as_of` and asserts the telemetry records `max_filed_at > as_of`. `tests/integration/backtest/test_end_to_end_smoke.py:718` even explicitly notes the cache has no filing data, so the smoke run never crosses the broken mapping. Because the audit code `getattr(row, "as_of_date", None)` returns `None` for every `NotableHolder` row, the per-domain summary block contains no key, the tripwire condition cannot fire, and the entire leak detector silently disables for that domain. No existing test catches this — the smoke test would pass even if every notable_holders row in cache were dated next year.
- **Suggested action:**
  Add `test_notable_holders_filter_key_after_as_of_fires` next to the news case in `tests/backtest/audit/test_tripwires.py`: seed one row with `filed_at = as_of + 1 day`, drive the audit pipeline, assert `tripwires.any_filter_key_after_as_of is True` and `summary.by_domain["notable_holders"]["max_filed_at"] > as_of`. The test must fail against `HEAD` and pass once the source-audit P0-01 mapping fix lands.

---

### P1-01 · T1 dead helper · `build_telemetry_record_from_logs` test is the only caller

- **Location(s):** `tests/backtest/test_cache_hits_audit.py`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/backtest.md` P2-05 (helper not reached from any non-test path).
- **Confidence:** high.
- **Description:**
  `test_cache_hits_audit.py` imports and exercises `build_telemetry_record_from_logs` (in `src/backtest/audit/telemetry.py`). Source-audit P2-05 records that this is the only call site anywhere in the repository — the production pipeline uses `_audit_record` plus the `AuditingStore` decorator, not the log-replay constructor. Keeping the test green keeps the helper alive and creates a maintenance attractor: any future change to the audit record shape requires updating two construction paths.
- **Suggested action:**
  Delete the test in the same PR that lands source-audit P2-05's deletion of `build_telemetry_record_from_logs`. Conditional disposition — do not delete in isolation, as the helper is currently shipped.

---

### P1-02 · T3 weak completion assertion · `test_driver_one_tick` only checks trace count

- **Location(s):** `tests/integration/backtest/test_driver_one_tick.py`
- **Source-audit cross-ref:** none directly, but adjacent to `docs/Phase11-project-audit/source-audit/backtest.md` P1-02 (driver/runner divergence) — a positive-content assertion here would catch the divergent reference-prices lookback.
- **Confidence:** high.
- **Description:**
  The test runs one tick through `Driver.run` and asserts only `len(traces) == 1`. It does not check for `branch_failed` warnings in caplog, does not assert `state["strategist_decision"].stances` is non-empty, does not check that the trace file on disk has more than zero bytes, does not assert `is_no_data` is false on any analyst verdict, and does not check decision-logger output. Per `test-policy.md` §A.7 and the `feedback_silent_failures_loud_tests` memory, this is the canonical "ran without crashing" anti-pattern: the test would pass even if every analyst silently returned the empty-stub verdict that `data-providers.md` source audit calls out as a silent-failure attractor.
- **Suggested action:**
  Strengthen with: (i) `assert not any("branch_failed" in r.message for r in caplog.records)`, (ii) at least one `assert state["strategist_decision"]["stances"]`, (iii) assert `is_no_data` is `False` for at least one analyst verdict in `state["temp:ticker_evidence_objects"]`. Do not delete — the test is on a load-bearing path.

---

### P1-03 · T6 wide-scope monkeypatch · `test_runner_sigint` patches 8 internal Runner names

- **Location(s):** `tests/unit/backtest/test_runner_sigint.py`
- **Source-audit cross-ref:** indirect (`docs/Phase11-project-audit/source-audit/backtest.md` P1-03 — two parallel cache-read capture mechanisms — gets harder to consolidate while a test pins this many internal seams).
- **Confidence:** high.
- **Description:**
  The test rewires the entire wiring surface of `backtest.runner`: `Driver`, `generate_ticks`, `CachedDataStore`, `_store_handle`, `set_active_provider`, `create_all`, `make_engine`, `DecisionLogger`. Per `test-policy.md` §A.6 ("Tests own their state — narrow `setattr`, not class-level"), this is the wide-scope shape: any refactor that renames or re-routes one of these eight internal symbols will silently make the SIGINT path stop being tested rather than fail the test.
- **Suggested action:**
  Reshape: replace the eight `monkeypatch.setattr` calls with a single seam — either inject a `signal.SIGINT` mid-tick via a fixture and run against a minimal in-memory cache (the same path the wall-clock-leakage tests take), or use `subprocess.run` with `--tick-limit 1` and a `SIGINT` from `os.kill`. Both alternatives test the SIGINT contract without freezing eight private names.

---

### P1-04 · T7 §A.3 multi-tick violation · `test_driver_portfolio_refresh` uses a 2-tick schedule

- **Location(s):** `tests/unit/backtest/test_driver_portfolio_refresh.py`
- **Source-audit cross-ref:** none (policy violation only).
- **Confidence:** high.
- **Description:**
  `test-policy.md` §A.3 requires unit tests run a single tick on the `baseline-2025-09` window. This test schedules two ticks to demonstrate that the portfolio cache is rebuilt per-tick. The behaviour is real and worth testing — the violation is using multi-tick rather than a single tick with a state-mutation assertion between two `driver.tick()` invocations of the same tick spec.
- **Suggested action:**
  Reshape to single-tick: call `driver._tick(...)` twice on the same tick spec with a portfolio mutation between calls, asserting the second call sees the mutation. Keeps the contract (per-tick rebuild) without violating the hard rule.

---

### P2-01 · T8 layout sprawl · backtest tests live across six directories

- **Location(s):** see "Files in scope" above — `tests/backtest/`, `tests/backtest/audit/`, `tests/backtest/leak_regressions/`, `tests/integration/backtest/`, `tests/unit/backtest/`, `tests/unit/backtest/cache/`, plus six loose `tests/unit/test_*.py` files and one root-level `tests/integration/test_multi_tick_...`.
- **Source-audit cross-ref:** none (test layout only).
- **Confidence:** high.
- **Description:**
  Per `test-policy.md` §B, the canonical tree is `tests/unit/<subsystem>/` and `tests/integration/<subsystem>/`. The current layout has *three* parallel roots (`tests/backtest/`, `tests/unit/backtest/`, `tests/integration/backtest/`) plus loose files. Most painful: the leak-regression tests sit under `tests/backtest/leak_regressions/` while the tripwire unit tests sit under `tests/backtest/audit/` — a contributor adding a new tripwire test has to grep first.
- **Suggested action:**
  Consolidate `tests/backtest/` and `tests/backtest/audit/` into `tests/unit/backtest/audit/`; move `tests/backtest/leak_regressions/` to `tests/integration/backtest/leak_regressions/` (these are end-to-end leak tests); move the loose `tests/unit/test_equity_curve.py`, `test_plot_equity.py`, `test_reporting_span_names.py`, `test_decision_logger_strict_serialiser.py`, `test_spy_metrics.py`, `test_replay_backtest_cli.py` into `tests/unit/backtest/`.

---

### P2-02 · T1 contingent · politician_trades tests on a disabled domain

- **Location(s):** `tests/unit/backtest/test_cache_providers.py::test_politician_trades_cache_returns_pydantic_list`; `tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-providers.md` (politician_trades is disabled in `scripts.backtest_fetch._build_provider_fns`); user memory `project_politician_trades_disabled` confirms the domain stays as a registered shell provider, not a deletion candidate.
- **Confidence:** medium.
- **Description:**
  Per memory the politician_trades shell provider stays registered so the config-flip-only swap convention holds. That makes both tests legitimate — they guard the shell against silent regressions. Filed at P2 (not P1) because the disposition is "keep with explanatory docstring", not "delete with the source PR". The current test docstrings do not mention the shell-provider context.
- **Suggested action:**
  Reshape: add a one-line module docstring to each test explaining that politician_trades is a registered-but-stubbed shell domain per `project_politician_trades_disabled` memory, so a future contributor doesn't delete the test thinking the domain is dead.

---

### P2-03 · T7 §A.3 borderline · `test_multi_tick_backtest_produces_diverse_rationale` runs 5 ticks

- **Location(s):** `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`
- **Source-audit cross-ref:** none directly.
- **Confidence:** medium.
- **Description:**
  Five ticks, but the test runs a *shim* (not the full pipeline) so the §A.3 cost — long real LLM/full-pipeline replay — is not paid. The contract being tested (rationale diversity across ticks) genuinely needs more than one tick. Borderline: the hard rule is "single tick on baseline-2025-09 unless explicitly marked"; the test should carry the integration + slow marker pair and a docstring stating the §A.3 exception, neither of which it currently does.
- **Suggested action:**
  Reshape minimally: add `@pytest.mark.integration` and `@pytest.mark.slow` decorators and a one-paragraph docstring justifying the multi-tick necessity per §A.3 exception clauses. No code change needed — purely metadata.

---

### P2-04 · T3 legitimises silent stub · `test_social_cache_returns_empty_model`

- **Location(s):** `tests/unit/backtest/test_cache_providers.py::test_social_cache_returns_empty_model`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/backtest.md` P2-04 (the empty-by-design social cache is a silent-failure attractor — source audit wants this surfaced rather than swallowed).
- **Confidence:** medium.
- **Description:**
  The test asserts that the social cache returns an empty `SocialSignals` model with no rows. That is the present behaviour, but source-audit P2-04 wants this branch surfaced (e.g. raise on empty, or at least log at WARN) rather than silently returning empty payloads — the analyst then degrades to neutral, which the strategist treats as signal-noise. The test currently locks in the silent-empty behaviour as the contract.
- **Suggested action:**
  Conditional reshape: once source-audit P2-04 lands (either raise-on-empty or WARN-on-empty), flip this test to assert the new surfaced behaviour. Filed at P2 because it does not mask anything today; today's behaviour is what ships.

---

### P2-05 · T8 loose-file layout · six backtest-adjacent tests live in `tests/unit/` root

- **Location(s):** `tests/unit/test_replay_backtest_cli.py`, `tests/unit/test_decision_logger_strict_serialiser.py`, `tests/unit/test_reporting_span_names.py`, `tests/unit/test_equity_curve.py`, `tests/unit/test_plot_equity.py`, `tests/unit/test_spy_metrics.py`.
- **Source-audit cross-ref:** none.
- **Confidence:** high.
- **Description:**
  These six files all exercise `src/backtest/...` symbols (decision logger, reporting, replay CLI) but live at `tests/unit/` root rather than under `tests/unit/backtest/`. Pure discoverability issue.
- **Suggested action:**
  Move all six to `tests/unit/backtest/`. Per user memory `project_replay_backtest_manual_tool`, `test_replay_backtest_cli.py` is intentionally kept — the move is cosmetic, not a deletion.

---

### P3-01 · T8 docstring drift · `test_strict_mode_aborts_on_missing_as_of` claims a live-pipeline boot it does not perform

- **Location(s):** `tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py`
- **Source-audit cross-ref:** none.
- **Confidence:** high.
- **Description:**
  The module docstring describes booting the live pipeline through `Runner.run`, but the test actually calls `data.get_price_history` directly with `as_of=None` and asserts the resulting raise. The assertion is correct and the path is real, but the docstring promises a pipeline-level test that isn't there. Cosmetic.
- **Suggested action:**
  Rewrite the docstring to describe the actual call path: a direct provider invocation with `as_of=None` asserting the strict-mode guard fires.

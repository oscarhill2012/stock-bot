# Test audit — `src/config/` and `src/baselines/`

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/config-and-baselines.md` (P1-01, P2-01, P2-02, P2-03, P2-04, P3-01)
**Test files in scope:** 8 primary (+ 6 peripheral noted below)
**Tests collected from those files:** 38 (via `pytest <paths> --collect-only -q`)
**Findings:** 0 P0 · 4 P1 · 5 P2 · 1 P3

## Files in scope

Primary — exercise the config + baselines subsystems directly:

- `tests/unit/config/test_analysts_config.py` — 9 tests, `load_analysts_config` happy-path + validation.
- `tests/unit/config/test_strategist_config.py` — 4 tests, `load_strategist_config` LLM-caps focus.
- `tests/unit/test_schedule_config.py` — 12 tests, mixed live-config + custom-path coverage for `load_schedule_config` / `get_schedule_config`.
- `tests/unit/test_risk_gate_config_loader.py` — 2 tests, `load_risk_gate_config` field mapping + `orchestrator.state` re-export contract.
- `tests/unit/test_analyst_config_rationale_budget.py` — 4 tests, the derived `verdict_rationale_prompt_budget` property.
- `tests/unit/test_spy_metrics.py` — 3 tests, `baselines.spy._metrics_from_series`.
- `tests/unit/test_equity_curve.py` — 3 tests, `baselines.equity_curve.compute_equity_curve`.
- `tests/unit/baselines/test_spy_metrics_removed.py` — 2 tests, regression-anchor that `spy_metrics` did not return.

Peripheral — touch the subsystems but are owned by other audit slots (flagged so the consolidator avoids double-counting):

- `tests/contract/test_lookbacks_sourced_from_config.py`, `tests/contract/test_http_timeout_sourced_from_config.py`, `tests/contract/test_schedule_sourced_from_config.py` — assert call-sites read from `data.config` / `backtest.settings`; the `config/` audit slot here only covers the *loader* modules.
- `tests/unit/agents/test_llm_retry.py` — exercises `agents.llm_retry` and stubs `config.retry_429.get_retry_429_policy`; the stub pattern (lines 286-298) is the only live use of the `retry_429` test surface.
- `tests/conftest.py:14-24`, `tests/integration/conftest.py:16-62` — autouse cache-clear fixtures that own the `get_analysts_config` cache lifecycle; behaviour audited only where directly relevant.

Layout notes:

- The `config/` loader tests are split: two live under `tests/unit/config/` (mirrored to `src/config/`), three sit loose at `tests/unit/test_*.py` root level (`test_schedule_config.py`, `test_risk_gate_config_loader.py`, `test_analyst_config_rationale_budget.py`). That asymmetry is the obvious §B layout finding — see P2-04.
- The baselines tests are split the same way: `tests/unit/baselines/test_spy_metrics_removed.py` is mirrored, but `tests/unit/test_spy_metrics.py` and `tests/unit/test_equity_curve.py` sit loose at root. See P2-04.

## Summary

The config-loader tests are unusually solid for this repo — Pydantic happy-path, validation-rejection, and derived-property cases are well covered with positive-content assertions rather than just "didn't raise". The dominant problems concentrate around the deprecated `StanceCaps.close_reason_max_chars` / `trim_reason_max_chars` pair (source P1-01) which the strategist fixture in `test_strategist_config.py` mirrors as if the fields were live, the live-config-reading tests in `test_schedule_config.py` and `test_risk_gate_config_loader.py` that violate §A.6 by depending on the operator-tunable `config/` tree, and the `_metrics_from_series` SPY test pair (`test_spy_metrics.py` + `test_spy_metrics_removed.py`) that exists solely to anchor a helper with no production callers — a textbook T1 zombie per source P2-03/P2-04.

## Findings

### P1-01 · T1 dead test fixture · strategist test payload populates deprecated `close_reason_max_chars` / `trim_reason_max_chars`

- **Location(s):** `tests/unit/config/test_strategist_config.py:37-38` (inside `_valid_strategist_json`).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/config-and-baselines.md` P1-01.
- **Confidence:** high
- **Description:**
  `_valid_strategist_json` shapes a minimal-valid payload that every test in the file reuses. Lines 37-38 set `close_reason_max_chars: 120` and `trim_reason_max_chars: 120` — the two fields the source-audit P1-01 finding flags as deprecated dead code (`src/config/strategist.py:93-103` carries the explicit `DEPRECATED` docstring; no live caller reads them). The fixture is the only thing keeping these two `StanceCaps` fields required at load time — once they are dropped from the Pydantic model, both keys must come out of this fixture too or the four tests fan-out into Pydantic `extra` errors. No test in this file actually asserts on the two fields' values; they exist only because the schema currently demands them. Filed P1 (not P2) because the deletion PR for source P1-01 needs to land both the field removal and this fixture trim in lockstep — leaving the fixture lines in turns a clean schema rename into a four-test breakage.
- **Suggested action:**
  Remove lines 37-38 from `_valid_strategist_json` in the same PR that drops `close_reason_max_chars` / `trim_reason_max_chars` from `StanceCaps`. No test logic changes — the four tests only exercise the `llm` sub-block and are insensitive to which sibling fields the payload carries.

### P1-02 · T1 dead test · `tests/unit/test_spy_metrics.py` exercises a helper with zero production callers

- **Location(s):** `tests/unit/test_spy_metrics.py` (entire file, 3 tests).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/config-and-baselines.md` P2-03.
- **Confidence:** high
- **Description:**
  `_metrics_from_series` has no production callers — a fresh `grep -rn "_metrics_from_series\|baselines.spy" src/ scripts/` finds the symbol only inside `src/baselines/spy.py` itself. The function survived the Phase 7 sweep that deleted the public `spy_metrics` wrapper specifically because this test file kept it pinned. The three tests are well-shaped (positive content assertions on `cumulative_return`, `max_drawdown`, `sharpe`), but they are testing a private helper whose only consumer is themselves. Per the rubric T1, "tests that mock or stub a function whose live callers all went away" — same shape, one rung higher: a test whose target has no live callers other than the test itself. Filed P1 because the deletion is contingent on source-audit P2-03 landing (the audit recommends tightening the docstring rather than deleting the helper, so the deletion path needs an explicit decision); if the call goes the other way and the helper is kept "as a regression guard", the test stays.
- **Suggested action:**
  Delete `tests/unit/test_spy_metrics.py` *and* `src/baselines/spy.py::_metrics_from_series` together. The matching `tests/unit/baselines/test_spy_metrics_removed.py::test_metrics_from_series_still_exists` regression-anchor at line 23-26 must be dropped in the same PR (see P1-03). If source-audit P2-03 elects to keep the helper, leave both files alone.

### P1-03 · T1 dead test · `test_metrics_from_series_still_exists` anchors the same dead helper from the regression-removal file

- **Location(s):** `tests/unit/baselines/test_spy_metrics_removed.py:23-26`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/config-and-baselines.md` P2-03.
- **Confidence:** high
- **Description:**
  The file's stated purpose is "ensure `spy_metrics` is not silently reintroduced" (`test_spy_metrics_symbol_is_gone` at lines 14-20) — a perfectly legitimate Phase 7 regression anchor. But the second test, `test_metrics_from_series_still_exists`, anchors the *opposite* invariant: that the dead private helper is *kept* alive for the (single) test file that exercises it. This is the test-anchored-zombie shape spelled out verbatim in the rubric — a test whose only purpose is to prevent deletion of code whose only purpose is to satisfy that test. The `assert hasattr(spy, "_metrics_from_series")` line has zero correctness value once P1-02 is acted on. Filed P1 because it is paired with P1-02 — the two land in the same PR.
- **Suggested action:**
  Delete `test_metrics_from_series_still_exists` (lines 23-26) in the same PR that lands P1-02. Keep `test_spy_metrics_symbol_is_gone` — that one defends a real Phase 7 decision and is well-shaped.

### P1-04 · T7 §A.6 / T6 wide-scope live-config read · `test_state_reexports_resolve_by_legacy_name` reads operator-tunable values from the live `config/risk_gate.json`

- **Location(s):** `tests/unit/test_risk_gate_config_loader.py:49-72`.
- **Source-audit cross-ref:** none — this is a test-layer violation of `docs/test-policy.md` §A.6.
- **Confidence:** high
- **Description:**
  The test imports `CASH_FLOOR_WEIGHT`, `MAX_DELTA_PER_TICKER`, `MAX_TOTAL_TURNOVER` from `orchestrator.state`. Per `src/orchestrator/state.py:18-20`, those module-level constants are resolved once at import time from `get_risk_gate_config()`, which reads the live `config/risk_gate.json` via the `lru_cache` singleton. Lines 70-72 then assert exact equality against the *current* values shipped in the JSON (`CASH_FLOOR_WEIGHT == 0.00`, `MAX_DELTA_PER_TICKER == 0.05`, `MAX_TOTAL_TURNOVER == 0.50`). The minute an operator tunes `config/risk_gate.json` — exactly the use-case the file exists for — this test fails with no source-side regression. The test claims to defend a "loader contract" but the assertions actually pin the live config values rather than the import-time wiring. §A.6 says tests must not depend on the live `config/` tree; this test does. Filed P1 because it sits on `tests/integration/` adjacent code (risk-gate wiring) and the false signal it would produce on an operator config edit is potentially confusing — the wrong test fires while the change is correct.
- **Suggested action:**
  Reshape: keep lines 60-67 (the `isinstance(value, float)` assertions on names re-exported), drop lines 70-72 (the exact-value assertions against the live JSON). The `test_loader_maps_each_json_field` test at lines 23-46 already covers value-flow via a `tmp_path` fixture file — that is the correct shape; this second test should defend the *re-export wiring*, not pin the values.

### P2-01 · T6 §A.6 live-config read · `test_schedule_config.py` live-config tests pin the live `config/schedule.json` values

- **Location(s):** `tests/unit/test_schedule_config.py:48-85` (four `test_live_config_*` tests) plus `test_lru_cache_clears_between_tests` at lines 170-186.
- **Source-audit cross-ref:** none — test-policy §A.6.
- **Confidence:** high
- **Description:**
  Five tests in this file call `load_schedule_config()` (no path override) or `get_schedule_config()`, which both resolve against the live `config/schedule.json`. `test_live_config_has_expected_tick_times` at lines 76-85 hard-pins both `09:45` and `16:30` — those are operator-tunable cadence knobs the file's own docstring describes as "headroom to add 12:30 ET". An operator editing the JSON to add a third tick or shift the cadence breaks the test with no source-side regression. The three sibling tests (`test_live_config_parses`, `test_live_config_tick_times_are_valid_hhmm`, `test_live_config_list_length_matches_ticks_per_day`) are weaker but still violate §A.6: they depend on the live file existing in a valid shape. The custom-path tests at lines 92-167 are the correct pattern and don't have this issue. Filed P2 (not P1) because the live-config tests do provide a smoke check that the committed file parses — they need to be *moved*, not *deleted*, and the cost of getting them wrong is a confusing CI failure not a silent regression.
- **Suggested action:**
  Move the four `test_live_config_*` tests into a single `tests/contract/test_schedule_config_live.py` marked `@pytest.mark.contract` — that layer is the right home for "the committed file parses and conforms to the schema" assertions. Drop the exact-value assertions in `test_live_config_has_expected_tick_times` (lines 84-85) or rewrite them to assert structural properties (e.g. "every entry is in the 09:00–17:00 trading-hours band") that survive operator tuning. `test_lru_cache_clears_between_tests` is best dropped — it asserts the autouse fixture works, which is something pytest itself enforces.

### P2-02 · T1 dead test target · `config.models._reset_cache` and `config.retry_429._reset_cache` test hooks have no test callers

- **Location(s):** no test file — gap finding.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/config-and-baselines.md` P2-04.
- **Confidence:** high
- **Description:**
  Source-audit P2-04 records that `src/config/models.py:141-149` and `src/config/retry_429.py:139-148` define `_reset_cache()` helpers explicitly labelled "test fixtures only", but the tests that would import them never do — `tests/unit/agents/test_llm_retry.py:286-298` instead monkeypatches `cfg_mod.get_retry_429_policy` directly, and there is no test file that mutates `config/models.json` at all. The two helpers are therefore dead per source-audit P2-04, and on the test side this is a "dead test target" finding: removing them will not break a single test. Filed P2 because the deletion is a small in-source action — no test changes needed.
- **Suggested action:**
  Delete both `_reset_cache` helpers in the source-audit P2-04 PR. No test file edits required; confirm pre-PR with `grep -rn "models._reset_cache\|retry_429._reset_cache" tests/` (expected: zero hits).

### P2-03 · T3 weak assertion · `test_lru_cache_clears_between_tests` asserts only that pytest's autouse fixture works

- **Location(s):** `tests/unit/test_schedule_config.py:170-186`.
- **Source-audit cross-ref:** none.
- **Confidence:** high
- **Description:**
  The test docstring concedes "this test just confirms the cache is fresh after the autouse fixture clears it — checking the function is callable and returns a ScheduleConfig from the real file". The two assertions are `isinstance(cfg, ScheduleConfig)` and `cfg is cfg2`. The first asserts the live file loads (already covered by `test_live_config_parses`); the second asserts `@lru_cache(maxsize=1)` works (a Python stdlib invariant, not StockBot behaviour). Per rubric T3 / §E "asserting only on completion": this test has no positive failure mode that would catch a real regression. The cache-clearing fixture above it (`_clear_schedule_config_cache` at lines 32-41) is doing the load-bearing work, and its correctness is asserted implicitly by the rest of the file functioning. P2 because the file has stronger sibling tests; T3 because the shape is "did pytest set up the fixture?", not a behavioural assertion.
- **Suggested action:**
  Delete `test_lru_cache_clears_between_tests`. If the cache-clear fixture needs a dedicated test, move it to a `tests/unit/test_conftest_fixtures.py` and assert the load-second-time-returns-different-object invariant by directly poking `_DEFAULT_PATH` — that would defend the fixture's purpose rather than `lru_cache`'s.

### P2-04 · T8 layout · config + baselines tests are split between mirrored and root locations

- **Location(s):** `tests/unit/test_schedule_config.py`, `tests/unit/test_risk_gate_config_loader.py`, `tests/unit/test_analyst_config_rationale_budget.py`, `tests/unit/test_spy_metrics.py`, `tests/unit/test_equity_curve.py` (root) vs `tests/unit/config/test_*.py` and `tests/unit/baselines/test_spy_metrics_removed.py` (mirrored).
- **Source-audit cross-ref:** none.
- **Confidence:** high
- **Description:**
  Per test-policy §B "unit tests live under `tests/unit/<module-mirror>/`", every test exercising `src/config/` should live under `tests/unit/config/` and every test exercising `src/baselines/` should live under `tests/unit/baselines/`. The reality is split: `test_analysts_config.py` and `test_strategist_config.py` are mirrored, but `test_schedule_config.py`, `test_risk_gate_config_loader.py`, and `test_analyst_config_rationale_budget.py` (which is conceptually a deeper analysts-config slice — the derived `verdict_rationale_prompt_budget`) sit loose at root. Same for baselines: `test_spy_metrics_removed.py` is mirrored but its sibling `test_spy_metrics.py` is not, and `test_equity_curve.py` is loose. P2 because the suite still discovers correctly; this is hygiene.
- **Suggested action:**
  Move the five loose files into the mirrored locations:
  - `tests/unit/test_schedule_config.py` → `tests/unit/config/test_schedule_config.py`
  - `tests/unit/test_risk_gate_config_loader.py` → `tests/unit/config/test_risk_gate_config.py` (also rename — `_loader` is redundant)
  - `tests/unit/test_analyst_config_rationale_budget.py` → `tests/unit/config/test_analysts_config_rationale_budget.py`
  - `tests/unit/test_spy_metrics.py` → `tests/unit/baselines/test_spy_metrics.py` (if not deleted per P1-02)
  - `tests/unit/test_equity_curve.py` → `tests/unit/baselines/test_equity_curve.py`

### P2-05 · T3 + §D "Time and money" violation · `test_equity_curve.py:_row` defaults to `datetime.now(tz=UTC)`

- **Location(s):** `tests/unit/test_equity_curve.py:29-43`.
- **Source-audit cross-ref:** none — test-policy §D.
- **Confidence:** high
- **Description:**
  `_row()` builds a snapshot row whose `recorded_at` falls back to `datetime.now(tz=UTC)` when callers omit it. Every test in the file uses that fallback (callers at lines 58, 71-74 pass no `recorded_at`). Test-policy §D ("Time and money") is explicit: "A test that references 'yesterday' is a test that breaks at midnight. Hard-code timestamps". The current shape happens to work because `compute_equity_curve` doesn't care about absolute time — but the three snapshots stamped at `now()` end up identical to second resolution if the test runs in under one second, which it does. The anchor-row ordering invariant in `compute_equity_curve` (anchors on the first `recorded_at`) is therefore *non-deterministically* honoured: two rows with the same timestamp resolve in insertion order today but the contract is "earliest `recorded_at` is the anchor". A future change that resolves ties differently silently breaks the test for one in N runs. P2 (not P0) because no real regression is hiding behind this today; it is a §D hygiene violation with a small latent failure mode.
- **Suggested action:**
  Replace the `datetime.now(tz=UTC)` fallback at line 30 with explicit per-row timestamps — e.g. `_row("init", ..., recorded_at=datetime(2025, 9, 2, 13, 30, tzinfo=UTC))` and increment by one minute per subsequent row. No production-code change needed.

### P3-01 · T8 docstring · `test_lru_cache_clears_between_tests` docstring contradicts itself

- **Location(s):** `tests/unit/test_schedule_config.py:170-178`.
- **Source-audit cross-ref:** none.
- **Confidence:** medium
- **Description:**
  Docstring sentence "wires `get_schedule_config` to read it (via `load_schedule_config` path override is not available on the cached function, so this test just confirms the cache is fresh after the autouse fixture clears it)" reads as a fragment of an earlier intent that was abandoned mid-write. Cosmetic. Subsumed by P2-03 if that finding is acted on (the test gets deleted).
- **Suggested action:**
  Drop together with the test in P2-03; otherwise rewrite to one sentence describing the invariant the test actually checks.

## Cross-subsystem notes

- The deprecated `close_reason_max_chars` / `trim_reason_max_chars` removal (source P1-01) coordinates across three layers in lockstep: `src/config/strategist.py:108-109` schema fields, `config/strategist.json` lines 10-11, `config/README.md:331-332`, and `tests/unit/config/test_strategist_config.py:37-38` fixture lines. One PR; no cross-test fan-out beyond the four-test file owning the fixture.
- The `_reset_cache` removal (source P2-04) has zero test-side fan-out — confirmed by grep. Source-only PR.
- The SPY helper deletion (P1-02 + P1-03 + source P2-03) is the only finding pair where the source audit and test audit *disagree* on disposition: the source audit recommends docstring-tightening (keep helper), the test audit recommends deletion of helper + both anchoring tests. Consolidator should pick one path; the test-audit recommendation is to delete because the helper has no production reach and the regression-anchor test (`test_spy_metrics_symbol_is_gone`) already defends the Phase 7 removal invariant without needing the private helper alive.
- §A hard-rule scan: zero §A.1 (real API keys), zero §A.2 (live-cache writes), zero §A.3 (multi-tick / non-baseline window), zero §A.4 (LLM-without-gate) violations across these eight files. The findings concentrate at §A.6 (live-config tree reads) and §A.7 / §E (weak assertions on cache-clear behaviour).

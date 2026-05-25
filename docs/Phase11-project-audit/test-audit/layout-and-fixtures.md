# Test audit — layout, fixtures, conftests, markers (cross-cutting)

**Auditor:** subagent (meta-level layout audit)
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/SUMMARY.md` (Open Questions §1 SmartMoney, §2 unused data domains), `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md`, `docs/Phase11-project-audit/source-audit/agents-strategist.md`, `docs/Phase11-project-audit/source-audit/contract.md`
**Test files in scope:** 211 `.py` test files across the entire `tests/` tree (count excludes `__init__.py` and `conftest.py`)
**Tests collected from those files:** 1,210 (via `.venv/bin/python -m pytest tests/ --collect-only -q`)
**Findings:** 0 P0 · 3 P1 · 12 P2 · 4 P3

## Files in scope

This audit deliberately covers no source subsystem — its scope is the test tree itself. The 15 per-subsystem reports already filed P2 layout notes inside their reports; this report consolidates the cross-cutting picture and recommends concrete consolidation targets. The directories considered:

- `tests/` (root + `conftest.py` + `__init__.py`)
- `tests/agents/` (+ `agents/analysts/`, `agents/memory/`)
- `tests/analysts/` (+ `analysts/fundamental/`, `analysts/news/`)
- `tests/backtest/` (+ `backtest/audit/`, `backtest/leak_regressions/`)
- `tests/contract/`
- `tests/executor/`
- `tests/fixtures/` (+ `fixtures/contract/`)
- `tests/integration/` (+ `integration/backtest/`, `integration/conftest.py`)
- `tests/orchestrator/`
- `tests/unit/` (+ all nested subtrees and `unit/data/conftest.py`)

Raw counts:

- 65 loose `tests/unit/*.py` files (test-policy §B violation — should mirror source).
- 20 root-level `tests/integration/*.py` files; only 1 carries the `integration` marker, 0 carry `slow`.
- 3 `conftest.py` files (1 root, 1 `integration/`, 1 `unit/data/`).
- 32 `__init__.py` files (all empty — none required for `pytest.ini`'s `pythonpath = . src` to resolve imports).
- 7 fixture files under `tests/fixtures/` (5 used, 2 effectively unused in the way the root conftest envisaged).

## Summary

The test tree carries the archaeological record of every reorg the project has gone through, with **three or four parallel mirror trees per major subsystem** (analysts in four; executor in three; contract in two; strategist split between `tests/unit/agents/strategist/` and three loose `tests/unit/test_strategist_*.py` files; orchestrator in three locations). The dominant single anomaly is the **65-file flat dump in `tests/unit/`** — none of these test modules mirror the source path their test policy §B asks for, and at least 24 of them have a clean canonical home that already exists (e.g. `tests/unit/agents/strategist/`, `tests/unit/agents/memory/` does not exist yet but should). The `integration/` tree is functionally unmarked: 19 of 20 files carry only `@pytest.mark.asyncio` and would be picked up by `pytest -m integration` exactly never. Fixtures are largely fine: the `load_fixture` / `fixture_path` pair declared in the root `conftest.py` is unused by every test in the suite (tests load JSON via `pathlib.Path("tests/fixtures/...")` directly), and the integration conftest's `cache_root` / `make_ctx` are also dead. One autouse fixture (`_clear_analysts_config_cache` in the root conftest) **runs against every test in the suite** including the 1,000+ that never touch the analyst config — this is acceptable hygiene but worth noting.

## Findings

### P1-01 · T8 layout · 65 loose `tests/unit/*.py` files violate the §B mirror-source rule

- **Location(s):** `tests/unit/*.py` (the 65 flat files; full list below)
- **Source-audit cross-ref:** N/A (pure layout)
- **Confidence:** high
- **Description:**
  Test-policy §B is explicit: unit tests "live under `tests/unit/` mirroring the source tree (e.g. `src/agents/news/fetch.py` → `tests/unit/agents/news/test_fetch.py`)." 65 of the loose files in `tests/unit/` ignore this. The cost is twofold. First, discoverability is poor — a contributor looking for "strategist schema tests" has to grep, because `test_strategist_schema.py` lives next to `test_yaml_parses` and `test_fake_broker.py` instead of in `tests/unit/agents/strategist/`. Second, the resulting cohabitation has already produced **29 duplicate test function names** across the suite (finding P2-09 below), several of them between a loose file and the canonical mirror it should have been part of (e.g. `test_extracts_required_keys` appearing in `tests/unit/contract/extractors/test_fundamental.py` AND `tests/unit/test_extract_fundamental_features.py`). Recommended destinations, grouped:

  - **`tests/unit/agents/strategist/` (3 files)** — `test_strategist_schema.py`, `test_strategist_prompt_risk_substitutions.py`, `test_strategist_prompt_worked_examples_ticker.py`. Sibling tree already exists.
  - **`tests/unit/agents/analysts/<analyst>/` (10 prompt files + 9 deterministic files = 19)** — the 10 LLM-prompt files split between `news/` and `fundamental/` (which don't yet exist under `tests/unit/agents/analysts/` — only `analysts/news/` and `analysts/fundamental/` exist *outside* the mirror tree, which is a separate parallel-tree finding); the 9 deterministic files split into `smart_money/`, `social/`, `technical/`. Cross-references P1-02 below (deterministic-analyst parallel trees).
  - **`tests/unit/agents/memory/` (4 files)** — `test_memory_compress.py`, `test_memory_eviction.py`, `test_memory_schema.py`, `test_memory_writer_agent.py`. Mirror dir does not yet exist; create it.
  - **`tests/unit/agents/risk_gate/` or `tests/unit/orchestrator/` (3 files)** — the three loose risk-gate files. The `risk-gate.md` per-subsystem audit notes the canonical location is contested between agent-mirror and orchestrator-mirror; pick one.
  - **`tests/unit/broker/` (3 files)** — `test_fake_broker.py`, `test_portfolio.py`, `test_trading212_request_construction.py`. Mirror dir does not exist; create it.
  - **`tests/unit/observability/` (4 files)** — `test_trace_writer.py`, `test_trace_writer_exception_logging.py`, `test_trace_maybe_noop.py`, `test_llm_trace_callbacks.py`. The mirror dir already has 8 sibling files — these clearly belong there.
  - **`tests/unit/orchestrator/` (2 files)** — `test_tick_entrypoint.py`, `test_tick_state.py`. Already-flagged in the orchestrator-audit report.
  - **`tests/unit/backtest/` or `tests/unit/scripts/` (8 reporting/equity/embedding/snapshot files)** — most are reporting-side concerns; `test_embeddings.py`, `test_buffer_persistence.py`, `test_decision_logger_strict_serialiser.py`, `test_reporting_span_names.py`, `test_plot_equity.py`, `test_equity_curve.py`, `test_spy_metrics.py`, `test_snapshot_persistence.py`.
  - **`tests/unit/data/` or `tests/unit/data/models/` (4 evidence + 2 SEC parsing)** — evidence row/index/dedup/trade-log tests belong with the persistence layer; `test_form4_parser.py` and `test_insider_model_roundtrip.py` belong in `tests/unit/data/providers/` or `data/models/`.
  - **`tests/unit/scripts/` or `tests/unit/lifecycle/` (13 lifecycle/CLI files)** — `test_initialise.py`, `test_initialise_cli.py`, `test_hard_reset.py`, `test_hard_reset_cli.py`, `test_init_db_script.py`, `test_lifecycle_initialise.py`, `test_session_service_factory.py`, `test_smoke_run_cli.py`, `test_replay_backtest_cli.py`, `test_stock_picker.py`, `test_schedule_config.py`, `test_scheduler_yaml.py`, `test_cloudbuild_yaml.py`. The lifecycle-audit report has its own opinion on where these go — defer to that report's recommendation.

- **Suggested action:**
  In a single dedicated PR, `git mv` each of the 65 files to its recommended home, creating new mirror directories where they don't yet exist (memory, broker, scripts, agents/risk_gate). Keep the `__init__.py` placeholders. Do not change file *contents* — only locations — to keep the diff inspectable. Run `pytest --collect-only` before and after to confirm collection count is unchanged.

### P1-02 · T8 layout · Four parallel test trees for `src/agents/analysts/`

- **Location(s):** `tests/agents/analysts/`, `tests/analysts/`, `tests/analysts/fundamental/`, `tests/analysts/news/`, `tests/unit/agents/analysts/`, plus the 19 loose `tests/unit/test_<analyst-thing>.py` files from P1-01
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/SUMMARY.md` §Open Question 1 (delete SmartMoney?) — the layout sprawl makes the SmartMoney delete harder because the relevant tests are scattered across all four trees
- **Confidence:** high
- **Description:**
  Per the analysts-deterministic audit report, tests for `src/agents/analysts/` exercise the same source area from four separate locations:
  - `tests/agents/analysts/test_evidence_callback.py` (1 file, post-callback shape)
  - `tests/analysts/` (5 root files: `test_smart_money.py`, `test_technical.py`, `test_branch_composition.py`, `test_cache_callbacks_per_ticker.py`, `test_per_ticker_branch.py`)
  - `tests/analysts/fundamental/` and `tests/analysts/news/` (3 files each — fetch/joiner/prompts)
  - `tests/unit/agents/analysts/` (6 files — the canonical §B home)

  None of these directories has a documented purpose distinguishing it from the others. The `tests/analysts/news/test_fetch_agent.py` and `tests/analysts/fundamental/test_fetch_agent.py` share an entire test function set with each other (3 of 5 duplicate function names per finding P2-09). Per §B, `tests/unit/agents/analysts/<analyst>/` is the canonical home and the only one that should exist.

- **Suggested action:**
  Consolidate everything analyst-related into `tests/unit/agents/analysts/<analyst>/` plus `tests/integration/` for the wired-pipeline cases (the `test_branch_composition.py` / `test_per_ticker_branch.py` smoke tests in `tests/analysts/`). Move `tests/agents/analysts/test_evidence_callback.py` to `tests/unit/agents/analysts/test_evidence_callback.py` (or under `_common/` if you mirror that subpackage). Delete `tests/agents/`, `tests/analysts/` after migration. Conditional on the SmartMoney delete decision: if SmartMoney is deleted, the smart-money-specific tests in all four trees go with it — line them up for one sweep PR.

### P1-03 · T8 layout · `tests/integration/` ignores its own marker

- **Location(s):** all 20 of `tests/integration/test_*.py`
- **Source-audit cross-ref:** N/A
- **Confidence:** high
- **Description:**
  `pytest.ini` defines `integration: requires real LLM or external API` and `slow: long-running tests excluded from the default run`. Test-policy §C says "Backtest smoke tests almost always need `slow + integration`." But of the 20 files under `tests/integration/`, **only one** (`test_strategist_v2_smoke.py`) applies `@pytest.mark.integration`, and **none** apply `slow`. The other 19 files carry only `@pytest.mark.asyncio`, which means:
  - `pytest -m integration` runs only 1 of 20 integration files — defeating the purpose of the marker.
  - `pytest -m "not slow"` (the default-fast invocation) still runs every one of them, including the multi-tick backtest in `test_multi_tick_backtest_produces_diverse_rationale.py`.

  The result is the inverse of what §F documents: the "default" run is heavier than intended and the "opt-in heavy" run is lighter. Combined with the rule in §A.4 (LLM tests gate on `RUN_LLM_TESTS=1`), there is currently no clean way to "run all integration tests" via marker selection.

- **Suggested action:**
  Add `pytestmark = pytest.mark.integration` to every file in `tests/integration/`, and `pytestmark = [pytest.mark.integration, pytest.mark.slow]` to the two known-slow ones (`test_multi_tick_backtest_produces_diverse_rationale.py`, `test_retry_smoke.py` if it exercises retries with real timers — verify before tagging). For the LLM-touching file, also retain the existing `skipif` on `RUN_LLM_TESTS`. Update `docs/test-policy.md` §F if the marker semantics need clarifying.

### P2-01 · T8 layout · Three parallel test trees for `src/agents/executor/`

- **Location(s):** `tests/executor/test_executor_bookkeeping.py`, `tests/unit/executor/test_open_positions_state.py`, `tests/unit/agents/executor/{test_thesis_writer_callback.py, test_verb_dispatch.py}`, plus `tests/unit/agents/test_executor_decision_hook.py` (a fifth file in the parent dir)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/test-audit/executor.md` P2-04 already flagged this
- **Confidence:** high
- **Description:**
  Per the executor audit, tests for `src/agents/executor/` live in three locations. The canonical §B home (`tests/unit/agents/executor/`) holds 2 of the 5 files. `tests/executor/` and `tests/unit/executor/` are pre-rename siblings that were never collapsed.
- **Suggested action:**
  Consolidate to `tests/unit/agents/executor/` (canonical) and `tests/integration/` (for the FakeBroker-wired test). Move `test_executor_decision_hook.py` into `tests/unit/agents/executor/`. Delete the empty `tests/executor/` and `tests/unit/executor/` dirs.

### P2-02 · T8 layout · Two parallel test trees for `src/contract/`

- **Location(s):** `tests/contract/` (7 files) and `tests/unit/contract/` (9 files + `extractors/` subdir of 6)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/test-audit/contract-package.md` P2-08 already flagged this
- **Confidence:** high
- **Description:**
  Per the contract-package audit, `tests/contract/` originally housed boundary-invariant tests, then `tests/unit/contract/` was added without retiring it. The split is now arbitrary: `tests/contract/test_evidence_schema.py` tests the same Pydantic validator that `tests/unit/contract/test_evidence.py` tests. The `pytest.ini` `contract` marker is *not applied* to any file in either tree, so the layout split provides no behavioural benefit.
- **Suggested action:**
  Defer to the contract-package audit's recommendation (consolidate to one location; their report has specific filename-merge guidance). At minimum, move `tests/contract/test_evidence_schema.py` to `tests/unit/contract/` and delete the `tests/contract/` root if `test_provider_shapes.py` (the live-API contract test) needs special-cased handling.

### P2-03 · T8 layout · `tests/orchestrator/` parallel to `tests/unit/orchestrator/`

- **Location(s):** `tests/orchestrator/test_pipeline_build.py` (1 file) vs `tests/unit/orchestrator/` (10 files)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/test-audit/orchestrator.md` P2-04 already flagged this
- **Confidence:** high
- **Description:**
  One file lives in the wrong location.
- **Suggested action:**
  `git mv tests/orchestrator/test_pipeline_build.py tests/unit/orchestrator/test_pipeline_build.py` and delete the empty `tests/orchestrator/` directory.

### P2-04 · T8 fixtures · `load_fixture` / `fixture_path` declared but never used

- **Location(s):** `tests/conftest.py:27-44`
- **Source-audit cross-ref:** N/A
- **Confidence:** high
- **Description:**
  The root conftest defines a fixture-loader pair documented in `test-policy.md §D` ("JSON fixtures live in `tests/fixtures/` and are loaded via the `load_fixture` fixture"). A grep across the entire suite finds **zero** test files that take `load_fixture` or `fixture_path` as a parameter. The 7 tests that load JSON fixtures (in `tests/unit/contract/extractors/` and `tests/unit/agents/strategist/test_position_thesis.py`) construct `pathlib.Path("tests/fixtures/contract/<name>.json")` strings directly, which couples them to the test-execution cwd (works because `pytest.ini` sets `testpaths = tests` and the rootdir is the repo root, but fragile).
- **Suggested action:**
  Either (a) retire the fixtures and amend §D to drop the documented convention, or (b) port the 7 existing fixture-loading sites to use `load_fixture` so the convention has teeth. Option (b) is the cheaper consistency win — six of seven sites are one-line replacements.

### P2-05 · T8 fixtures · `cache_root` and `make_ctx` in `tests/integration/conftest.py` are dead

- **Location(s):** `tests/integration/conftest.py:16-104`
- **Source-audit cross-ref:** N/A
- **Confidence:** high
- **Description:**
  Grep for `cache_root` across `tests/`: one definition in `tests/integration/conftest.py`, **zero** uses. Grep for `make_ctx` (the module-level factory): one definition, zero uses (every test that needs a context stub defines its own local `_make_ctx`, including four files in `tests/integration/` itself). The fixture body is non-trivial (writes `analysts.json`, patches `_DEFAULT_PATH`, clears `lru_cache`) — leaving it as decoration is a maintenance trap because future authors will assume it's load-bearing.
- **Suggested action:**
  Delete `cache_root` and `make_ctx` from `tests/integration/conftest.py`. If the conftest ends up empty, delete the file (pytest discovers tests fine without it).

### P2-06 · T8 conftest · Root autouse `_clear_analysts_config_cache` runs on every test

- **Location(s):** `tests/conftest.py:14-24`
- **Source-audit cross-ref:** N/A
- **Confidence:** medium
- **Description:**
  The root conftest's `_clear_analysts_config_cache` is `autouse=True`, so it fires before and after every one of the 1,210 collected tests. Most of those tests never load the analyst config — they exercise providers, the cache store, the strategist schema, etc. The cost is small (two `cache_clear()` calls per test, microseconds each), but the *scope* is wrong: an autouse fixture in the root conftest should only carry state that genuinely affects every test. The current placement also means a future "test analyst config is sticky across two calls" test cannot be written without explicitly opting out of autouse, which is awkward.
- **Suggested action:**
  Move the fixture to `tests/unit/agents/analysts/conftest.py` (create if needed) and `tests/integration/conftest.py`, keeping it autouse within those subtrees. Tests outside the analyst path then don't pay the implicit cost. Low priority — current behaviour is correct, just over-scoped.

### P2-07 · T8 layout · `tests/agents/` partially populated, partially under `tests/unit/agents/`

- **Location(s):** `tests/agents/test_isolated_failure.py`, `tests/agents/test_output_caps_per_ticker.py`, `tests/agents/analysts/test_evidence_callback.py`, `tests/agents/memory/test_writer_smart_money_seen.py` vs the canonical `tests/unit/agents/{analysts,executor,strategist}/`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/test-audit/agents-misc.md`
- **Confidence:** high
- **Description:**
  `tests/agents/` is a third parallel mirror tree for `src/agents/` (alongside the loose tests in `tests/unit/` and the canonical `tests/unit/agents/`). It holds 4 files spread across 3 subdirs, none of which has a documented reason to live outside `tests/unit/agents/`.
- **Suggested action:**
  Move all four files into `tests/unit/agents/` (the two top-level files into `tests/unit/agents/`, the analyst evidence-callback into `tests/unit/agents/analysts/`, the memory writer into `tests/unit/agents/memory/` — a directory that does not yet exist and needs creating). Delete `tests/agents/` after.

### P2-08 · T8 layout · `tests/analysts/{news,fundamental}/` duplicate `tests/unit/agents/analysts/`

- **Location(s):** `tests/analysts/news/{test_fetch_agent.py, test_joiner.py, test_prompts.py}` and `tests/analysts/fundamental/{test_fetch_agent.py, test_joiner.py, test_prompts.py}`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/test-audit/analysts-deterministic.md` (notes "this subsystem's tests are spread across four parallel test trees"), `docs/Phase11-project-audit/test-audit/analysts-llm.md`
- **Confidence:** high
- **Description:**
  These six files share nine duplicate test function names with their counterpart in the sibling directory (e.g. `test_fetch_writes_per_ticker_context_keys` exists in both `tests/analysts/fundamental/test_fetch_agent.py` and `tests/analysts/news/test_fetch_agent.py`; same story for `test_joiner_*` and `test_instruction_*`). They are mirror-image scaffolding for the news/fundamental fetch–joiner–prompts triad. The convention is fine; the location is not.
- **Suggested action:**
  Move to `tests/unit/agents/analysts/news/` and `tests/unit/agents/analysts/fundamental/` (mirror dirs that don't yet exist). Conditional on the analysts-llm audit's reshape recommendations — coordinate the move in the same PR that lands its strengthening.

### P2-09 · T8 collection · 29 duplicate test function names across files

- **Location(s):** see python-computed table below
- **Source-audit cross-ref:** N/A
- **Confidence:** high
- **Description:**
  A `pytest --collect-only -q` sweep finds 29 test function names that appear in two or more files. Pytest *does* disambiguate them by file path so collection still works, but the duplicates make:
  - `pytest -k <name>` unreliable (matches the wrong test, or both).
  - CI output ambiguous when a failing test name appears in two files.
  - Refactor risk higher (rename one, forget the other).

  The most common patterns are:
  - **Extractor scaffold** — `test_all_features_are_floats`, `test_extracts_required_keys`, `test_handles_empty_data_gracefully` each appear 4–5 times across `tests/unit/contract/extractors/test_*.py` and the loose `tests/unit/test_extract_*.py` files. Same scaffold, different domain. **Resolve by P1-01:** moving the loose files removes most collisions; renaming the per-domain tests to `test_all_features_are_floats_for_<domain>` removes the rest.
  - **News/fundamental analyst mirror** — `test_fetch_writes_per_ticker_context_keys`, `test_fetch_degrades_on_provider_error`, `test_joiner_builds_canonical_keys_from_per_ticker_state`, `test_joiner_synthesises_no_data_for_missing_key`, `test_joiner_output_consumable_by_strategist_index_evidence`, `test_instruction_addresses_single_ticker`, `test_instruction_describes_single_verdict_output`, `test_instruction_honours_output_caps_from_config`. Same scaffolding for news vs fundamental — **resolved by P2-08** layout move (move both into the mirror tree and accept the same-name behaviour, or prefix with the analyst name).
  - **One-off name collisions** worth fixing individually: `test_drain_creates_parent_dirs` and `test_drain_resets_buffer` (both appear in `tests/unit/observability/test_log_handler.py` AND `tests/unit/observability/test_metric_exporter.py`), `test_explicit_args` (CLI parsers in two scripts), `test_yaml_parses` (two YAML configs), `test_output_always_six_chars` (one file lists it twice — `tests/unit/observability/test_terminal_log.py` has two tests with the same name, which is a genuine bug since the second silently overrides the first).
- **Suggested action:**
  Fix `tests/unit/observability/test_terminal_log.py::test_output_always_six_chars` first — that one is broken in place (two functions, same name, only the second runs). For the rest, the layout PR (P1-01 + P2-08) eliminates most of the noise; for the residual cross-domain collisions, rename per pattern (`test_drain_creates_parent_dirs_for_log_handler`, `_for_metric_exporter`, etc.).

### P2-10 · T8 stray `__init__.py` files contribute nothing under `pythonpath = . src`

- **Location(s):** 32 empty `__init__.py` files throughout `tests/`
- **Source-audit cross-ref:** N/A
- **Confidence:** medium
- **Description:**
  `pytest.ini` sets `pythonpath = . src`, so test modules are resolvable without `__init__.py`. Pytest's `rootdir`-based collection treats `tests/` as a flat namespace; the inits are leftover from a pre-pytest era. They aren't harmful, but each one is a file the consolidator either has to keep in sync or has to think about during moves.
- **Suggested action:**
  Low priority — keep until the P1-01/P2-08 directory moves land, then a single sweep PR can remove them all. Confirm with `pytest --collect-only -q` afterwards that collection count is unchanged.

### P2-11 · T8 layout · `tests/backtest/` and `tests/integration/backtest/` overlap

- **Location(s):** `tests/backtest/` (9 files including `audit/` and `leak_regressions/`) and `tests/integration/backtest/` (7 files)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/test-audit/backtest.md`
- **Confidence:** medium
- **Description:**
  The backtest audit notes that policy §B is honoured here (`tests/backtest/` for cache + audit primitives, `tests/integration/backtest/` for driver/runner smoke). What it does not flag is that the loose `tests/unit/backtest/` exists too (17 files) — making three trees, not two. This is consistent with the analyst pattern and is the most likely consolidation target where §B-mirror-source is the destination.
- **Suggested action:**
  Defer to the backtest audit. If §B applies, `tests/backtest/` should fold into `tests/unit/backtest/` (cache + audit + leak_regressions all moving in). `tests/integration/backtest/` stays as-is.

### P2-12 · T8 fixtures · `tests/fixtures/position_thesis_v1.json` loaded by one test only

- **Location(s):** `tests/fixtures/position_thesis_v1.json`, used only by `tests/unit/agents/strategist/test_position_thesis.py`
- **Source-audit cross-ref:** N/A
- **Confidence:** low
- **Description:**
  Single-use frozen-shape fixtures are fine — they prevent silent schema drift — but this one sits at the top level of `tests/fixtures/` while the analogous contract fixtures sit under `tests/fixtures/contract/`. If the consolidator chooses to move it (e.g. to `tests/fixtures/strategist/`) it should be coordinated with the source-audit `agents-strategist.md` finding on `PositionThesis` (a P1 that flagged the schema as live-but-unstable).
- **Suggested action:**
  Either move to `tests/fixtures/strategist/position_thesis_v1.json` for consistency, or leave it; coordinate with the strategist source-fix PR rather than handling in the layout sweep.

### P3-01 · T8 cosmetic · `pytest.ini` defines `slow` but very few tests carry it

- **Location(s):** `pytest.ini` + every long-running test in the suite
- **Source-audit cross-ref:** N/A
- **Confidence:** medium
- **Description:**
  `slow` is defined as "long-running tests excluded from the default run" but a `grep -l "pytest.mark.slow" tests/` returns zero results. Combined with P1-03, this means `pytest -m slow` and `pytest -m "not slow"` are currently no-ops. The biggest behavioural impact is on the multi-tick backtest test (`test_multi_tick_backtest_produces_diverse_rationale.py`) — it runs by default but is not separable.
- **Suggested action:**
  Tag the known-heavy tests after P1-03 lands (every file >0.5 s wall-clock in the per-test report) with `pytest.mark.slow`. Update `docs/test-policy.md §F` to spell out the expected invocations (default = fast; `-m "slow or integration"` = full).

### P3-02 · T8 cosmetic · `tests/fixtures/contract/__init__.py` makes the JSON dir a package

- **Location(s):** `tests/fixtures/contract/__init__.py`
- **Source-audit cross-ref:** N/A
- **Confidence:** low
- **Description:**
  The fixture directory has an `__init__.py` even though it contains only JSON files. Harmless, but suggests the author tried to import fixtures as modules at some point. Future authors might be misled.
- **Suggested action:**
  Delete in the same sweep PR as the other empty `__init__.py` files (P2-10).

### P3-03 · T8 cosmetic · `unit/data/conftest.py`'s `registry_isolation` is one of only two narrowly-scoped fixtures

- **Location(s):** `tests/unit/data/conftest.py`
- **Source-audit cross-ref:** N/A
- **Confidence:** low
- **Description:**
  The fixture is correctly scoped, takes a snapshot of `_REGISTRY` and `_LIMITERS`, and is used by 2 tests (`test_active_pacing.py`, `test_registry.py`). It's a good model for how subtree conftests should look. Worth referencing in `docs/test-policy.md §D` as the canonical pattern.
- **Suggested action:**
  Cite this fixture in §D as the "narrow-scope conftest" exemplar when the policy doc is next updated.

### P3-04 · T8 cosmetic · No conftest under `tests/unit/agents/`, `tests/unit/backtest/`, `tests/unit/contract/`

- **Location(s):** N/A (absence)
- **Confidence:** low
- **Description:**
  Several large subtrees have shared boilerplate that's currently inlined (e.g. every test in `tests/unit/backtest/` sets up a `CachedDataStore` against `tmp_path`). A subtree conftest with a `_wire_store(tmp_path)` fixture (similar to `tests/unit/backtest/test_driver_wallclock_telemetry.py:17-23` but lifted into a conftest) would remove ~10 copies of the same setup. Same story for the report-cache and clock fixtures in the analyst tests.
- **Suggested action:**
  Out of scope for the consolidation sweep — file as a separate cleanup task once the moves land. Reference this finding when reshaping individual tests per the per-subsystem reports.

---

## Appendix A — Files to move (consolidator's worklist)

In the recommended order:

1. **Marker pass (P1-03)** — one line added to each of 19 `tests/integration/*.py`.
2. **Dead-code removal (P2-04, P2-05, P2-06)** — delete unused fixtures from `conftest.py` files.
3. **Layout moves**:
   - `tests/orchestrator/` → `tests/unit/orchestrator/` (1 file).
   - `tests/contract/test_evidence_schema.py` → `tests/unit/contract/` (and decide on `test_provider_shapes.py`).
   - `tests/executor/`, `tests/unit/executor/` → `tests/unit/agents/executor/` (2 files moved + 1 file via `tests/unit/agents/test_executor_decision_hook.py` shifted inwards).
   - `tests/agents/` → `tests/unit/agents/` (4 files, creates `tests/unit/agents/memory/`).
   - `tests/analysts/{news,fundamental}/` → `tests/unit/agents/analysts/{news,fundamental}/` (6 files).
   - `tests/analysts/test_*.py` (5 files) → `tests/unit/agents/analysts/` (sort by target analyst).
   - `tests/unit/*.py` (65 loose files) → mirror dirs per P1-01 table.
   - `tests/backtest/` → `tests/unit/backtest/` (subject to backtest-audit confirmation).
4. **Duplicate-name fixes (P2-09)** — rename the residual collisions; fix the one true bug in `tests/unit/observability/test_terminal_log.py`.
5. **Init sweep (P2-10, P3-02)** — delete empty `__init__.py` files.

Each step is a separable PR; the order matters only insofar as the marker pass and dead-fixture removal should land before the moves so the diffs are inspectable.

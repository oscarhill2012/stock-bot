# Test audit — src/orchestrator/

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/orchestrator.md` (primary). Adjacent files referenced where their tests target orchestrator-owned surface: `docs/Phase11-project-audit/source-audit/backtest.md` (for the driver-parity tests under `tests/unit/backtest/`) and `docs/Phase11-project-audit/source-audit/agents-*` (for the persistence writes invoked from pipeline agents covered by §C-Rule 7).
**Test files in scope:** 14 (full list below)
**Tests collected from those files:** 41 (via `pytest <paths> --collect-only -q`)
**Findings:** 4 P0 · 5 P1 · 4 P2 · 2 P3

## Files in scope

Grouped by location — note the orchestrator suite is sprawled across four
top-level locations, which is itself a T8 finding (P2-04).

- `tests/orchestrator/` — 1 file
  - `test_pipeline_build.py`
- `tests/unit/orchestrator/` — 10 files
  - `test_persistence.py`
  - `test_persistence_ticker_stance.py`
  - `test_pipeline_sequential_branches.py`
  - `test_pipeline_wiring_v2.py`
  - `test_risk_gate.py` *(also in scope of `docs/Phase11-project-audit/test-audit/risk-gate.md`; orchestrator covers it only as a pipeline-state consumer of `MAX_DELTA_PER_TICKER` / `MAX_POSITION_WEIGHT` imported from `orchestrator.state`)*
  - `test_temp_prefix_keys.py`
  - `test_tick_as_of_phase.py`
  - `test_tick_initial_state.py`
  - `test_tick_reference_prices.py`
  - `test_trade_log_tick_id_fks.py`
- `tests/unit/` (root level) — 4 files
  - `test_tick_entrypoint.py`
  - `test_tick_state.py`
  - `test_stock_picker.py`
  - `test_session_service_factory.py`
- `tests/integration/` — 4 files
  - `test_pipeline_composition.py`
  - `test_phase2_hydration_from_db_only.py`
  - `test_namespace_partitioning.py`
  - `test_state_delta_user_prefix_end_to_end.py`

`test_session_service_factory.py` and `test_persistence.py` cover the same
three branches of `make_session_service` (explicit URL, env-var fallback,
both-missing-raises). Filed as P2-03 redundancy.

## Summary

The orchestrator suite is strong on the Phase-2 builder seams (`as_of`,
`tick_phase`, reference-prices JSON safety, portfolio dump, persistence
round-trips) and on the pipeline topology contract (8 stages, Phase-9
ticker fan-out, parallel-pool shape). It is weak in exactly the places
the source audit flagged as P0: there is **no test exercising
`run_once`'s exception-swallow clause**, **no test seeding `as_of` as a
raw `datetime` against the real `DatabaseSessionService`** to prove the
JSON-encode path, and **no test asserting cross-tick survival of
`memory_buffer` / `day_digest`** — every existing assertion is on the
*empty seed* itself, not on persistence. The audit's most surprising
secondary finding is that `TickState` is alive only because two stale
tests in `tests/unit/test_tick_state.py` still import it (P1-03 in the
source audit, T1 here).

## Findings

### P0-01 · T4 missing surfacing test · `run_once`'s `except (AttributeError, BaseException)` clause has no regression test

- **Location(s):** new test needed — natural home is `tests/unit/orchestrator/test_tick_run_once_exception_handling.py` (the closest existing file is `tests/unit/test_tick_entrypoint.py:1-13`, which asserts only that the module imports and that `run_once` is a coroutine — i.e. zero coverage of the swallow path).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P0-02 (`except (AttributeError, BaseException)` swallows every exception including `KeyboardInterrupt` / `SystemExit`).
- **Confidence:** high
- **Description:**
  Source P0-02 is the canonical silent-failure attractor named in `feedback_silent_failures_loud_tests`. The clause at `src/orchestrator/tick.py:260-270` will swallow a real pipeline blow-up (TypeError, KeyError, even SystemExit) and return `updated.state` from the session service regardless. The whole orchestrator unit suite has nothing here: `test_tick_entrypoint.py` asserts `hasattr(module, "run_once")` and that the function is a coroutine, full stop. There is no test that (a) raises a non-ADK exception inside the runner's event loop and asserts it propagates, (b) raises the *known* ADK 1.32 teardown `AttributeError('NoneType'.partial)` and asserts the warning is logged via `caplog` and the function still returns the session state, or (c) asserts that after a swallow, the returned state must include `last_snapshot` (the Rule-8 handshake key the backtest driver already gates on at `src/backtest/driver.py:393-401`). Without this test, the source-audit fix for P0-02 cannot land safely — any narrowing of the except clause risks regressing the ADK-1.32 accommodation, and any widening will not be caught.
- **Suggested action:**
  Add a new file `tests/unit/orchestrator/test_tick_run_once_exception_handling.py` with three scenarios. (1) `test_run_once_propagates_non_adk_exceptions` — monkeypatch `Runner.run_async` to be an async generator that raises `RuntimeError("synthetic")`; assert `run_once` re-raises (it currently swallows). (2) `test_run_once_swallows_known_adk_teardown_bug` — raise `AttributeError("'NoneType' object has no attribute 'partial'")` from the generator, use `caplog` to assert the warning fires, and assert the function returns a dict containing `last_snapshot` (the Rule-8 success handshake). (3) `test_run_once_asserts_pipeline_reached_snapshotter` — raise the teardown bug *but* return a session whose state lacks `last_snapshot`; assert the function still re-raises rather than masking a genuine pipeline mid-run failure. These three together fix what the source P0-02 narrowing is supposed to fix and act as the regression contract for whatever shape the narrowing takes.

### P0-02 · T4 missing surfacing test · raw-`datetime` `as_of` against real `DatabaseSessionService` (live-path JSON-encode)

- **Location(s):** new test needed — sister of `tests/unit/orchestrator/test_tick_as_of_phase.py`. The closest existing test patches `_fetch_reference_prices` and only asserts `as_of` is timezone-aware UTC (`test_tick_as_of_phase.py:21-62`); it does **not** create an ADK `DatabaseSessionService` and feed the dict through `create_session`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P0-03 (live path writes a `datetime` into `create_session(state=...)` that `DatabaseSessionService` cannot JSON-serialise).
- **Confidence:** high
- **Description:**
  Source P0-03 is a latent P0: `_build_initial_state` returns `"as_of": datetime.now(tz=UTC)` and `run_once` passes the dict into `session_service.create_session(state=initial_state)` at `tick.py:242-247`. The backtest driver coerces every datetime to ISO at `src/backtest/driver.py:494-499` because the JSON serialiser cannot round-trip a `datetime`; the live path was never given the same fix. `test_tick_as_of_phase.py` validates the value's type but not the contract: it never instantiates a `DatabaseSessionService` and never calls `create_session(state=<the builder output>)`. The `test_phase2_hydration_from_db_only.py` test does use a real `DatabaseSessionService` but seeds an ISO-string `as_of` (line 38 — `state={"tick_id": "t-1"}`, no `as_of` at all), so the failure mode never surfaces. As the source audit notes, this is exactly the case the memory entry `feedback_as_of_boundary_coercion` calls out as mandatory. Because live isn't deployed yet, no production tick has exercised this path — the source P0-03 will fire on the first live tick unless the test exists.
- **Suggested action:**
  Add `tests/unit/orchestrator/test_tick_initial_state_json_safe.py` with two assertions: (1) `json.dumps(_build_initial_state(...))` succeeds (i.e. the dict is JSON-safe in the same sense `reference_prices` already is — see `test_tick_reference_prices.py:44-77`). (2) Round-trip via a real `DatabaseSessionService` with an in-memory sqlite URL: `await svc.create_session(app_name="StockBot-test", user_id="stockbot", state=initial_state)` must not raise `TypeError: Object of type datetime is not JSON serializable`. The test should pass once `_build_initial_state` ISO-stringifies `as_of` per source-audit P0-03's suggested fix.

### P0-03 · T3 + T4 · `_build_initial_state` happy-path test asserts the empty-seed contract violation rather than against it

- **Location(s):** `tests/unit/orchestrator/test_tick_initial_state.py:24-49` (`test_initial_state_retains_required_keys`).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P0-01 (`memory_buffer` / `day_digest` seeded empty at Phase 2 instead of hydrated from persistence).
- **Confidence:** high
- **Description:**
  The test asserts `"memory_buffer" in state` and `"day_digest" in state` (line 36) — but those two keys are §A cross-tick fields whose source of truth is the persistence layer (§E), and the source audit's P0-01 names this exact failure mode: "treating a cross-tick field as tick-scoped — seeding it with an empty value at Phase 2 instead of reading from persistence. This is a Phase 2 violation." The test pins down the wrong shape: it ratifies the empty-seed at line 151-152 of `tick.py` as the contract, so any fix that hydrates the values from persistence (the resolution path Spec C will land) will *break* this test. Per the rubric T3 entry, this is "an `assert key in state` test where the key's correct shape would be a value loaded from the persistence layer, not an empty default." There is also no T4 cross-tick survival test anywhere in the suite — no test runs two ticks back-to-back and asserts `memory_buffer` from tick N is visible to tick N+1. Filed as P0 because it actively masks a contract violation that the source audit has identified for fix.
- **Suggested action:**
  When Spec C lands and source P0-01 is fixed, strengthen this assertion to: `assert state["memory_buffer"] == <value previously written to persistence>` and `assert state["day_digest"] == <value previously written to persistence>`. Until then, mark the empty-key assertion with an explicit "violates contract-invariants §B Phase 2 — see source-audit P0-01" comment so it cannot be cited as evidence that the current seed shape is correct. Add a separate new test `test_memory_buffer_survives_across_ticks` (T4) that runs two `_build_initial_state` calls bracketing a fake MemoryWriter persistence write and asserts the value survives — this is the contract assertion Spec C will need to land safely.

### P0-04 · T3 · `test_state_delta_user_prefix_end_to_end.py` does not assert against `branch_failed` / silent-fail paths

- **Location(s):** `tests/integration/test_state_delta_user_prefix_end_to_end.py:96-136`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P0-02 (silent-failure attractor in `run_once`), `feedback_silent_failures_loud_tests` (memory), test-policy §G.8 ("`branch_failed` warnings are not benign").
- **Confidence:** high
- **Description:**
  This is the most load-bearing orchestrator integration test — it proves the Executor `after_agent_callback` → ADK auto-yield → `DatabaseSessionService.append_event` chain that Spec B depends on. The current assertions verify positive output (lines 116-134 — `user:positions` contains AAPL, `opened_price ≈ 200.0`, `user:thesis` matches the decision thesis), which is good. What's missing: no `caplog.set_level(WARNING)` + `caplog` assertion that no `branch_failed` record was emitted (test-policy §G.8 is explicit about this requirement for pipeline-level tests), and the consumed `async for _ in runner.run_async(...)` loop swallows nothing but also does not assert the runner *produced* any events — a runner that yielded zero events because the callback was never invoked would still let the AAPL assertion pass *only because the existing user_state was inherited from the test's own seed* (lines 60-87 seed `"user:positions": {}` then expect the callback to populate it). The comment at lines 96-99 is right to call out the no-try/except design, but the test does not back it up with a `caplog` assertion that the callback log line actually fired. Filed P0 because this is the canonical Spec B integration test and a regression that silently disabled the after_agent_callback would still pass.
- **Suggested action:**
  Add `caplog.set_level(logging.WARNING)` and assert no `branch_failed` record was emitted (mirror what test-policy §G.8 requires). Add a positive log assertion that the executor callback log line (whatever it is — `_executor_thesis_writer_callback` writes at INFO; check `src/agents/executor/agent.py`) appears in `caplog.records`. Add an event-count guard: collect the events from `runner.run_async` and assert at least one carries a `state_delta` with a `user:positions` key, so a regression that silently disabled the auto-yield path fails the test instead of relying on the seed-then-reload coincidence.

### P1-01 · T1 dead test (contingent) · `tests/unit/test_tick_state.py` is the sole live consumer of `TickState`

- **Location(s):** `tests/unit/test_tick_state.py:1-15` (both tests).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P1-03 and P2-01 (`TickState` is referenced by exactly one test and not used in `src/` at all).
- **Confidence:** high
- **Description:**
  Per the source audit, `TickState` is a documentation artefact masquerading as a schema — no code in `src/` constructs, validates, or reads it. Grep across `src/` and `scripts/` confirms only the class definition (`src/orchestrator/state.py:60-101`) and this one test file reference it. The test asserts default values (`tick_id == ""`, `tickers == []`, `memory_buffer == []`, `last_executed_tick_id is None`) which is the very shape the source audit calls "an orphan state at lines 82-91 [that] is misleading". Worse, the test ratifies the orphan `last_executed_tick_id: str | None = None` shape; per `contract-invariants.md` §A the field is now **tick-scoped** (written via `state_delta`), so the `TickState` field grouping it under "Persistent across ticks" is wrong, and the test pins down the wrong shape. Source-audit P1-03 leaves the disposition open ((a) make `TickState` a live Phase-2 contract or (b) delete both); whichever the source PR picks, the existing test is dead in its current form.
- **Suggested action:**
  Delete contingent on the source-audit P1-03 fix PR. If option (b) (delete the class), delete this whole file. If option (a) (turn `TickState` into a live Phase-2 contract validator at the `_build_initial_state` boundary), rewrite both tests to assert validation behaviour (a missing required field raises `ValidationError`, an unexpected type raises) — the default-value assertions go either way.

### P1-02 · T6 inappropriate state ownership · `test_stock_picker.py` reads the live `config/watchlist.json`

- **Location(s):** `tests/unit/test_stock_picker.py:1-13` (both tests).
- **Source-audit cross-ref:** none direct — this is a test-policy §A.6 finding. Adjacent: `src/orchestrator/stock_picker.py:1-16` (the function reads `config/watchlist.json` via a hard-coded path).
- **Confidence:** high
- **Description:**
  `get_watchlist()` opens `config/watchlist.json` from the live project tree (via `Path(__file__).resolve().parents[2] / "config" / "watchlist.json"`). `test_get_watchlist_contains_expected_tickers` asserts `"AAPL" in tickers` and `"MSFT" in tickers` — this is a test of the live config file contents, not of `get_watchlist`'s logic. Test-policy §A.6 ("Tests own their state") forbids reads of the live `config/` tree. If a user re-edits `config/watchlist.json` to drop AAPL or MSFT (legitimate watchlist edit), this test breaks. The user-memory entry `feedback_provider_switching_must_be_one_line` reinforces: config edits should not require code or test changes. Borderline P1/P2; filed P1 because the failure mode is "test breaks on a routine config edit", which is the exact kind of friction the policy is designed to avoid.
- **Suggested action:**
  Reshape both tests to use `monkeypatch.setattr(stock_picker, "_WATCHLIST_PATH", tmp_path / "watchlist.json")` and seed the file with a synthetic JSON payload. The test then asserts `get_watchlist()` returns the seeded list — which exercises the function's actual logic (file open + JSON parse + key extract) rather than the live watchlist's contents.

### P1-03 · T8 layout · orchestrator tests live in four parallel directories

- **Location(s):** `tests/orchestrator/`, `tests/unit/orchestrator/`, `tests/unit/test_tick_*.py`, `tests/integration/test_pipeline_*.py` / `test_phase2_*.py` / `test_namespace_*.py` / `test_state_delta_*.py`.
- **Source-audit cross-ref:** test-policy §B (taxonomy and location rules).
- **Confidence:** high
- **Description:**
  Per test-policy §B, unit tests for a module should live under `tests/unit/<module-mirror>/`. The orchestrator suite is the most fragmented in the repo. Examples: `tests/orchestrator/test_pipeline_build.py` belongs under `tests/unit/orchestrator/` (it's structural, no I/O); `tests/unit/test_tick_entrypoint.py`, `test_tick_state.py`, `test_stock_picker.py`, `test_session_service_factory.py` should all be under `tests/unit/orchestrator/`. Three of these root-level files also overlap with files already in `tests/unit/orchestrator/` (`test_session_service_factory.py` ≡ `test_persistence.py`'s three branches — see P2-03). The integration tests' location is policy-compliant on its own, but the existence of three orchestrator-specific test files at four different depths makes the suite hard to navigate by `pytest tests/unit/orchestrator/` alone.
- **Suggested action:**
  Move `tests/orchestrator/test_pipeline_build.py` and the four root-level orchestrator tests into `tests/unit/orchestrator/`. Drop `tests/orchestrator/` once empty. Defer the integration-tree consolidation — those four files are correctly in `tests/integration/` per §B but would benefit from a `tests/integration/orchestrator/` sub-directory for discoverability (P2 layout polish).

### P1-04 · T4 missing surfacing test · `_dispatch_app_name` fallback demotes unknown broker modes to PAPER

- **Location(s):** new test needed.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P1-04 (`_dispatch_app_name` silently re-classifies unknown broker modes as PAPER).
- **Confidence:** high
- **Description:**
  Source P1-04 names a silent-failure attractor on the most consequential routing decision the bot makes: a broker constructed with `mode="livee"` (typo) would route to `StockBot-paper`. There is no test of `_dispatch_app_name` at all, and there is no test of the fallback logic at `tick.py:225-227`. The `_dispatch_app_name` function (line 25-54) is fully importable and testable in isolation — `BrokerMode(BrokerMode.LIVE)` returns `"StockBot-live"`, `BrokerMode.PAPER` returns `"StockBot-paper"`, and the `case _` branch raises `ValueError`. The bug lives in the *call site*, not the function itself, which is why this gap exists — no one has thought to test the call site's silent demotion.
- **Suggested action:**
  Add `tests/unit/orchestrator/test_tick_broker_mode_routing.py` with: (1) `test_paper_broker_routes_to_paper_app_name` (FakeBroker has no `.mode` attribute — assert the fallback lands in `StockBot-paper`). (2) `test_live_broker_routes_to_live_app_name`. (3) `test_typo_mode_raises_loudly` — set `broker.mode = "livee"` and assert `run_once` raises `ValueError` rather than silently routing to paper. Test (3) is the regression for source P1-04's suggested fix.

### P1-05 · T8 missing markers · slow/integration markers not applied to four-file integration cluster

- **Location(s):** `tests/integration/test_pipeline_composition.py`, `tests/integration/test_phase2_hydration_from_db_only.py`, `tests/integration/test_namespace_partitioning.py`, `tests/integration/test_state_delta_user_prefix_end_to_end.py`.
- **Source-audit cross-ref:** none direct — test-policy §C ("Pytest markers").
- **Confidence:** medium
- **Description:**
  Test-policy §C says integration tests should carry `integration`; backtest smoke usually needs `slow + integration`. None of these four files carry either marker. `test_state_delta_user_prefix_end_to_end.py` runs a real ADK Runner with `DatabaseSessionService` (sqlite in-memory) plus a full Executor agent — this is unambiguously `integration`. `test_phase2_hydration_from_db_only.py` builds two `DatabaseSessionService` instances against a real sqlite file — `integration`. `test_namespace_partitioning.py` same shape. `test_pipeline_composition.py` is structural-only (no I/O) so it's debatable, but it builds the full pipeline tree which transitively imports ADK, Google GenAI, and SQLAlchemy — at minimum it should carry `integration`. Filed P1 because the policy is explicit.
- **Suggested action:**
  Add `pytestmark = pytest.mark.integration` at the module level of all four files. `test_state_delta_user_prefix_end_to_end.py` should additionally carry `slow` (it spins up a real ADK Runner).

### P2-01 · T3 · `test_pipeline_composition.py::test_pipeline_has_eight_stages` asserts count but not content

- **Location(s):** `tests/integration/test_pipeline_composition.py:20-24`.
- **Source-audit cross-ref:** test-policy §E ("Asserting only on counts, never on content").
- **Confidence:** medium
- **Description:**
  `assert len(pipeline.sub_agents) == 8` — a stage being replaced by a no-op `BaseAgent` of the wrong type would still pass. The companion `test_pipeline_stage_names` (lines 27-48) does check names by index, which is the right shape. The count-only test is redundant with the name test and is what `test-policy.md §E` calls out by name. P2 because the sibling test already does the work.
- **Suggested action:**
  Delete `test_pipeline_has_eight_stages` (covered by `test_pipeline_stage_names` which is strictly stronger). Or fold the length assertion into the names test as a sanity check at the top.

### P2-02 · T3 · `test_tick_entrypoint.py` asserts only importability and coroutine-ness

- **Location(s):** `tests/unit/test_tick_entrypoint.py:1-13`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P0-02 (silent-failure attractor in `run_once` — see P0-01 above for the missing surfacing test).
- **Confidence:** high
- **Description:**
  Two tests, total. (1) `hasattr(orchestrator.tick, "run_once")` — passes if the module imports. (2) `inspect.iscoroutinefunction(run_once)` — passes if the def has `async`. Combined, they verify that the file was not deleted and that someone did not change `async def` to `def`. They verify nothing about the function's behaviour. This is the cleanest example in the repo of test-policy §E's "It didn't raise, therefore it works" anti-pattern — except even weaker because they do not run the function. P2 because they are not actively masking a bug (the gap is P0-01); they are decorative.
- **Suggested action:**
  Delete both tests. Replace with the three regression tests sketched in P0-01 (exception handling) and P0-04 (broker-mode routing). The "module imports" assertion is implicit in any test that imports `run_once`; the "is coroutine" assertion is implicit in any `await run_once(...)` call site.

### P2-03 · T8 redundant · `test_persistence.py` and `test_session_service_factory.py` cover the same three branches

- **Location(s):** `tests/unit/orchestrator/test_persistence.py:14-47` and `tests/unit/test_session_service_factory.py:14-42`.
- **Source-audit cross-ref:** none.
- **Confidence:** high
- **Description:**
  Both files test `make_session_service` with the same three scenarios: explicit `db_url` wins, env-var fallback, both-missing-raises. The assertion shapes are near-identical (class name + `hasattr(svc, "db_engine")`). `test_persistence.py` matches against `RuntimeError(match="make_session_service")`; `test_session_service_factory.py` matches against `RuntimeError(match="DATABASE_URL")`. One was clearly copied from the other during the Band-2 migration (the docstrings reference it explicitly). P2 because the duplication does no active harm — both pass — but the two files will drift if anyone updates only one.
- **Suggested action:**
  Delete `tests/unit/test_session_service_factory.py` (the root-level one; per P1-03 layout, the canonical location is `tests/unit/orchestrator/test_persistence.py`). If the match-string in the canonical test should also include `DATABASE_URL`, fold that in during deletion.

### P2-04 · T6 wide-scope monkeypatch · `test_tick_reference_prices.py` patches `_fetch_reference_prices` at module scope

- **Location(s):** `tests/unit/orchestrator/test_tick_reference_prices.py:33` and line 60.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/orchestrator.md` P1-02 (live `_fetch_reference_prices` bypasses the provider registry).
- **Confidence:** medium
- **Description:**
  `monkeypatch.setattr(mod, "_fetch_reference_prices", fake_fetch)` patches the function `_build_initial_state` calls into. This is a single-function patch, which is the §A.5 / §E correct level — but it patches a *seam that the source audit recommends removing*. Source P1-02 says `_fetch_reference_prices` should be replaced with a call through `data.providers.registry.get_stats_provider().bulk_download(...)`. Once that fix lands, this monkeypatch target disappears. Filed P2 (not P1) because the patch level is currently policy-compliant — but consolidator should be aware that fixing source P1-02 will break these two tests until the patch is re-targeted at the registry leaf.
- **Suggested action:**
  Contingent on source P1-02 landing: re-target the monkeypatch at `data.providers.stats.yfinance._bulk_download` (the leaf seam) or at the registered stats provider's `bulk_download` method, depending on which form P1-02's fix takes. Until then, leave as-is — the tests do their job at the current seam.

### P3-01 · T8 cosmetic · `test_temp_prefix_keys.py` is a source-text scanner, not a behaviour test

- **Location(s):** `tests/unit/orchestrator/test_temp_prefix_keys.py:28-35`.
- **Source-audit cross-ref:** none.
- **Confidence:** medium
- **Description:**
  The test scans seven source files for forbidden bare key strings (`{held_positions_view}`, `"technical_data"`, etc.). It is a regex guard, not a test of orchestrator behaviour — it would be more at home under `tests/contract/` per test-policy §B (contract tests "assert on a signature or schema rather than runtime values"). The test does what it says it does and is useful; the finding is purely about location. P3 cosmetic.
- **Suggested action:**
  Consider moving to `tests/contract/test_no_bare_invocation_keys.py` in a future pass. Not urgent.

### P3-02 · T8 cosmetic · `test_pipeline_sequential_branches.py` is named for the pre-Phase-9 topology

- **Location(s):** `tests/unit/orchestrator/test_pipeline_sequential_branches.py` (entire file).
- **Source-audit cross-ref:** none direct.
- **Confidence:** low
- **Description:**
  The file is named for the pre-Phase-9 sequential branch arrangement ("AnalystPool must be Parallel→Sequential→Sequential"), but the file's docstring explicitly says Phase 9 retired that guard, and the body now tests the parallel-pool shape. The test is correct; the filename is stale. Cosmetic.
- **Suggested action:**
  Rename to `test_analyst_pool_topology.py` or similar in a future cleanup pass.

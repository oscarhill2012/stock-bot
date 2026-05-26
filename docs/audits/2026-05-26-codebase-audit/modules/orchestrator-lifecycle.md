# Module audit — orchestrator + lifecycle

Scope:
- `src/orchestrator/` (`__init__.py`, `pipeline.py`, `persistence.py`, `state.py`, `stock_picker.py`, `tick.py`)
- `src/lifecycle/` (`__init__.py`, `initialise.py`, `hard_reset.py`, `scheduler.py`)
- Tests under `tests/unit/orchestrator/`, `tests/orchestrator/`, `tests/unit/test_initialise*.py`, `tests/unit/test_hard_reset*.py`, `tests/unit/test_session_service_factory.py`, `tests/unit/test_init_db_script.py`, `tests/unit/test_lifecycle_initialise.py`, `tests/unit/test_stock_picker.py`, `tests/unit/test_tick_state.py`, `tests/unit/test_tick_entrypoint.py`.

---

## F-orch-001
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `src/orchestrator/tick.py:148`, `_build_initial_state` returns `state["as_of"]` as a `datetime` object.
- **Evidence:**
  ```python
  "as_of":      datetime.now(tz=UTC),
  ...
  initial_state = await _build_initial_state(broker, tick_id, tickers)
  adk_session = await session_service.create_session(
      app_name=_app_name,
      user_id="stockbot",
      state=initial_state,
  )
  ```
  Live `run_once` now uses `make_session_service()` (a `DatabaseSessionService` — see `src/orchestrator/persistence.py:456-466`). The backtest driver explicitly coerces `as_of` to ISO before `create_session` (`src/backtest/driver.py:545-550`) because `DatabaseSessionService` serialises state via `json.dumps`. Live does not — the raw `datetime` is passed through and will either fail JSON encoding or be silently dropped depending on ADK's serialiser path.
- **Intent violated:** memory rule "every datetime write to state must ISO-stringify first (backtest's DatabaseSessionService can't hold datetime)"; contract §A `as_of` row.
- **Suggested action:** investigate — coerce `as_of` to ISO in `_build_initial_state` (or at the same point the driver does in backtest). Confirm against `DatabaseSessionService` behaviour.
- **Notes:** asymmetry between live and backtest. The two ISO-vs-datetime paths are exactly the silent-failure attractor the audit is hunting; live tick has presumably never been exercised end-to-end against a real DB.

## F-orch-002
- **Category:** policy-mismatch
- **Severity:** P0
- **Location:** `src/orchestrator/tick.py:229-247` (live `run_once`); contrast `src/backtest/driver.py:517-527` and `src/observability/handle_injector_plugin.py`.
- **Evidence:** Backtest builds a `Runner(..., plugins=[HandleInjectorPlugin(...)])` because direct mutation of `adk_session.state["temp:_*"]` after `create_session` is silently discarded (the plugin's docstring explicitly cites this bug class). Live `run_once` does neither — it never installs `temp:_trace` or `temp:_decision_logger`. If live ever wires a `TraceWriter` or `DecisionLogger` and tries the same direct-mutation pattern (e.g. between `create_session` and `runner.run_async`), the handles will be silently dropped and observability writes will become empty files — exactly the dead-observability symptom the plugin's docstring describes.
- **Intent violated:** memory rule "ADK temp: handle install must use BasePlugin.before_run_callback. Never mutate adk_session.state["temp:_*"] after create_session — the runner rehydrates and discards it."; intent §2.9 "observability handles (...) injected as temp:_trace / temp:_decision_logger by direct adk_session.state mutation after create_session(...)" — that description is now stale (post-plugin) and contradicts the plugin module's docstring.
- **Suggested action:** investigate — either route live observability through the same `HandleInjectorPlugin` or document that live currently has no observability handles and the contract description in §2.9 of the intent is stale.
- **Notes:** there is a latent trap here: any future contributor reading intent §2.9 will reach for the direct-mutation pattern and ship a silent-failure bug.

## F-orch-003
- **Category:** dedupe-candidate
- **Severity:** P1
- **Location:** Phase 2 state seeding split across `src/orchestrator/tick.py:105-161` (`_build_initial_state`) and `src/backtest/runner.py:530-579` plus `src/backtest/driver.py:238-319`.
- **Evidence:** Both lifecycles seed the same keys (`tickers`, `portfolio`, `memory_buffer`, `day_digest`, `reference_prices`, `tick_phase`, `as_of`) and both dump `reference_prices` to `model_dump(mode="json")`. The two paths have already drifted:
  - live writes `as_of` as `datetime`, backtest writes ISO string (`driver.py:545-550`).
  - live seeds `memory_buffer=[]` and `day_digest=""` (`tick.py:151-152`); backtest seeds the same (`runner.py:569-570`).
  - live fetches `reference_prices` via `yfinance` bulk (`tick.py:99-102`); backtest reads from cache (`runner.py:544-548`) and refreshes per-tick (`driver.py:290-319`).
  - live re-fetches portfolio per process invocation (Cloud Run cold-start); backtest re-fetches per tick (`driver.py:275-277`).

  Contract §B Phase 2 demands "both lifecycles see identical keys at identical phases". They do today, but only by coincidence — there is no shared helper to keep them in step.
- **Intent violated:** §2.9 "Phase 2 populates all §A row fields from their Source of Truth; cross-tick fields hydrated from persistence (never seeded empty)" + the live-≡-backtest topology invariant.
- **Suggested action:** consolidate — extract a shared Phase 2 seeder that both lifecycles call, parametrised on broker / reference-source / `as_of`-supplier. Failing that, add a contract test that asserts both seeders produce the same key set (the existing `tests/unit/backtest/test_runner_initial_state_parity.py` does some of this — confirm coverage).
- **Notes:** Not currently diverging in shape (which is why this is P1, not P0), but the `as_of`-type divergence (F-orch-001) is the first symptom.

## F-orch-004
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `src/lifecycle/initialise.py:21` and `src/lifecycle/hard_reset.py:17` — `_STOCKBOT_TABLES = ("buffer_entries", "trade_log", "portfolio_snapshots")`.
- **Evidence:** `src/orchestrator/persistence.py` declares six ORM tables: `buffer_entries`, `trade_log`, `ticker_stances`, `portfolio_snapshots`, `analyst_evidence`, `ticker_evidence`. The lifecycle list covers only three. Consequences:
  - `initialise._check_live_tables_empty` silently passes when `ticker_stances`, `analyst_evidence`, or `ticker_evidence` still hold rows from a previous run — operator believes the DB is fresh, it isn't.
  - `hard_reset._row_counts` / `_archive_sqlite` (covers the whole DB) / `_archive_postgres` (per-table) / `_truncate_live` (per-table) all skip three tables, so a hard reset leaves stale evidence/stance rows behind on Postgres; on SQLite the archive is whole-DB so archival is fine but the truncate is incomplete.
- **Intent violated:** §2.9 "lifecycle/ runs ... hard-reset path"; §B Phase 1 "Persistence layer (§E) ready — DB connection open, schema verified".
- **Suggested action:** investigate — derive `_STOCKBOT_TABLES` from `Base.metadata.tables.keys()` so the list cannot drift, then audit any tests that assume the legacy three-table list.
- **Notes:** scripts/init_db creates all six (via `create_all`); the asymmetry is purely in the reset / preflight code path. P0 because a stale `ticker_stances` row carrying a different schema version would silently corrupt analytics for the new run.

## F-orch-005
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/orchestrator/state.py:61-103`, class `TickState`.
- **Evidence:**
  ```
  $ grep -rn "TickState\b" src/ scripts/
  src/orchestrator/state.py:61:class TickState(BaseModel):
  ```
  ```
  $ grep -rn "TickState\b" tests/
  tests/unit/test_tick_state.py:1:from orchestrator.state import TickState
  tests/unit/test_tick_state.py:4:def test_tick_state_defaults():
  tests/unit/test_tick_state.py:13:def test_tick_state_serializes():
  ```
  `TickState` is referenced by exactly one test file (which only verifies its own defaults round-trip). No agent, lifecycle, or backtest module instantiates or validates it. The state dict the pipeline actually uses is an unstructured dict built in `_build_initial_state` / `Runner._run_async`. Comments in the class itself (lines 73-89) treat field names as advisory ("`temp:` prefix at runtime", "`positions` migrated to user-scoped, reads are a type error by design") yet no type check enforces any of this.
- **Intent violated:** n/a (TickState predates the current contract).
- **Suggested action:** investigate — either delete `TickState` and `test_tick_state.py`, or wire it as a Pydantic validator at Phase 2 boundary so live ≡ backtest field shape is enforceable.
- **Notes:** the risk-gate constants in the same module (`MIN_HELD_WEIGHT` etc., lines 14-21) are widely used and must stay. Only the `TickState` model is unused.

## F-orch-006
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/orchestrator/pipeline.py:121-124`, `_build_memory_writer`.
- **Evidence:** `_build_memory_writer` is a one-line delegate that the pipeline calls inline (`pipeline.py:166`). Unlike `_build_strategist`, the docstring gives no test-mocking rationale. `grep -rn "_build_memory_writer" src/ tests/` returns only the two sites in `pipeline.py` itself.
- **Intent violated:** n/a.
- **Suggested action:** investigate — inline `MemoryWriter()` at the call site or document why the indirection exists (parity with `_build_strategist`'s test seam).
- **Notes:** trivial nit; only worth changing if `_build_strategist`'s pattern is being normalised.

## F-orch-007
- **Category:** dead-test
- **Severity:** P2
- **Location:** `tests/unit/test_tick_entrypoint.py` (whole file, 13 lines).
- **Evidence:**
  ```python
  def test_tick_module_importable():
      import orchestrator.tick
      assert hasattr(orchestrator.tick, "run_once")

  def test_run_once_is_coroutine():
      ...
      assert inspect.iscoroutinefunction(run_once)
  ```
  These assert "module imports" and "function is async". Neither asserts on any tick-time behaviour, state-dict shape, broker dispatch, or persistence side-effect. Per test-policy §A.7 / §E "It didn't raise, therefore it works", they are decorative.
- **Intent violated:** test-policy §A.7.
- **Suggested action:** delete (or replace with a real tick-shape assertion against `_build_initial_state` — already covered by `tests/unit/orchestrator/test_tick_initial_state.py`).

## F-orch-008
- **Category:** dedupe-candidate (test-consolidation)
- **Severity:** P2
- **Location:** `tests/unit/test_session_service_factory.py` and `tests/unit/orchestrator/test_persistence.py`.
- **Evidence:** Both files contain the same three tests for `make_session_service` (explicit URL wins / env-var fallback / both-missing raises). `test_persistence.py:1-48` and `test_session_service_factory.py:1-43` are functionally identical with cosmetic name changes.
- **Intent violated:** test-policy §B (one test per scenario per layer).
- **Suggested action:** consolidate — keep the `tests/unit/orchestrator/test_persistence.py` copy (location matches source tree per §B "mirroring the source tree"), delete `tests/unit/test_session_service_factory.py`.

## F-orch-009
- **Category:** test-gap
- **Severity:** P1
- **Location:** Live `as_of` ISO coercion (F-orch-001) and the Phase 2 seeder asymmetry (F-orch-003).
- **Evidence:** `tests/unit/orchestrator/test_tick_as_of_phase.py:48-50` asserts `isinstance(as_of, datetime)` — i.e. it locks in the broken-on-DB-backend datetime behaviour rather than the ISO-string fix. No test asserts that `_build_initial_state`'s `as_of` value survives `DatabaseSessionService` round-trip. Memory rule: "every datetime write to state must ISO-stringify first."
- **Intent violated:** policy memory + intent §2.9.
- **Suggested action:** add a test that takes the dict from `_build_initial_state`, passes it through `make_session_service().create_session(...)`, reads it back, and asserts the round-tripped `as_of` value resolves via `resolve_as_of` without falling back to wall-clock.
- **Notes:** ties directly to F-orch-001.

## F-orch-010
- **Category:** test-gap
- **Severity:** P1
- **Location:** No test exercises the hard-reset table list against the actual ORM metadata (relates to F-orch-004).
- **Evidence:** `tests/unit/test_hard_reset.py` only seeds and asserts on `portfolio_snapshots`. `tests/unit/test_init_db_script.py:10` hard-codes `EXPECTED_TABLES = {"buffer_entries", "trade_log", "portfolio_snapshots"}` — the same stale set. A test that derived "expected tables" from `Base.metadata.tables.keys()` and asserted them all created / cleared would have caught F-orch-004.
- **Intent violated:** test-policy §A.7 (assert positive output state).
- **Suggested action:** add a contract test that compares `_STOCKBOT_TABLES` (or its replacement) against `Base.metadata.tables.keys()`.

## F-orch-011
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `src/orchestrator/tick.py:259-270` — `except (AttributeError, BaseException) as exc:`.
- **Evidence:**
  ```python
  except (AttributeError, BaseException) as exc:
      ...
      logger.warning(...)
  ```
  `BaseException` swallows `KeyboardInterrupt`, `SystemExit`, `MemoryError`. The backtest driver explicitly avoids this footgun (`src/backtest/driver.py:588-590`: "NOTE: deliberately catches Exception (not BaseException) so KeyboardInterrupt, SystemExit, and MemoryError propagate normally"). Also, the catch logs the failure but does NOT enforce pipeline-completion afterwards — backtest does (`driver.py:663-672`), live does not. A mid-pipeline failure would log a warning, silently return whatever partial state survived, and the caller would treat the tick as successful.
- **Intent violated:** test-policy §A.7 + the symmetry implied by intent §2.9 ("Both lifecycles see identical state-dict shape at identical phases").
- **Suggested action:** investigate — narrow the catch to `(AttributeError, BaseExceptionGroup, Exception)` (mirroring backtest's logic) and add a `last_snapshot.tick_id` check before returning. Reuse `_log_exception_chain` from the driver.
- **Notes:** the "silent degradation in the live path" is exactly the class memory rule "Silent failures are the recurring bug class" warns against.

## F-orch-012
- **Category:** dead-code
- **Severity:** P3
- **Location:** `src/orchestrator/tick.py:226` — `_broker_mode = BrokerMode(_raw_mode) if _raw_mode in BrokerMode._value2member_map_ else BrokerMode.PAPER`.
- **Evidence:** Uses the undocumented private `_value2member_map_` to avoid the `ValueError` from `BrokerMode("paper")`. `BrokerMode(_raw_mode)` would do the same with a public API and a try/except. Reaches into Python private state for no benefit. Also: the comment "FakeBroker does not expose .mode; default to PAPER so test runs land in the paper namespace rather than raising" — confirmed; FakeBroker has no `mode` attr. The `getattr(broker, "mode", "paper")` fallback is the line that handles it; the `_value2member_map_` check is redundant given that fallback.
- **Intent violated:** n/a.
- **Suggested action:** refactor to `_broker_mode = BrokerMode(_raw_mode)` with the fallback in the `getattr` call.

## F-orch-013
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `src/lifecycle/initialise.py:74-89` `_check_live_tables_empty` — depends on `_STOCKBOT_TABLES` (see F-orch-004) AND on every Postgres database hosting StockBot tables in the `public` schema (hard_reset hard-codes `public."{t}"` at lines 67-69).
- **Evidence:** No test exercises the Postgres path; the `public.` assumption is undocumented and would fail silently against any deployment that uses a different schema (e.g. multi-tenant).
- **Intent violated:** n/a (deployment is pre-deployment, so this is latent).
- **Suggested action:** investigate — either remove the Postgres branch (pre-deployment, SQLite-only today) or read the schema from the SQLAlchemy URL.

## F-orch-014
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/lifecycle/scheduler.py:1-21` — `pause_job` / `resume_job` shell out to `gcloud scheduler jobs pause/resume`.
- **Evidence:** Project state per `.claude/CLAUDE.md` is "pre-deployment — the bot is in active development and is not yet deployed anywhere. No paper or live instance is running." No Cloud Scheduler job exists. The functions are called by `initialise.initialise(scheduler_job=...)` and `hard_reset.hard_reset(scheduler_job=...)` only when the caller supplies a job name; no production call site does. Tests monkeypatch both functions in every relevant test.
- **Intent violated:** n/a (pre-deployment).
- **Suggested action:** investigate — keep if the live-deployment plan still includes Cloud Scheduler; delete if the scheduling mechanism has shifted (e.g. Cloud Run Jobs cron). Either way the comment "No-op shim under tests" is misleading: real execution would shell out unconditionally.

## F-orch-015
- **Category:** dead-test
- **Severity:** P3
- **Location:** `tests/unit/test_tick_state.py` (whole file).
- **Evidence:** Tests `TickState()` defaults and `model_dump`. As established in F-orch-005, `TickState` itself is unused by any pipeline code. The test asserts only that an unused Pydantic class round-trips its own defaults.
- **Intent violated:** test-policy §A.7.
- **Suggested action:** delete with `TickState` per F-orch-005; or, if `TickState` is retained as an audit aid, leave the test in place.

## F-orch-016
- **Category:** over-abstraction
- **Severity:** P3
- **Location:** `src/orchestrator/tick.py:25-54` `_dispatch_app_name` + `BrokerMode` enum.
- **Evidence:** The enum has two values; the helper maps each to a hard-coded string. A two-line dict literal would do. The backtest path constructs its own app_name string inline at `src/backtest/driver.py:500` (`f"StockBot-backtest-{self._window_key}"`). No symmetry.
- **Intent violated:** n/a.
- **Suggested action:** refactor — collapse to a module-level dict, or accept the asymmetry with backtest and stop pretending it's a dispatch.
- **Notes:** trivial.

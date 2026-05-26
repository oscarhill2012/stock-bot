# Test strategy review

Audit date: 2026-05-26. Reviewer: cross-cutting test-strategy pass over
the 11 module audits in
`docs/audits/2026-05-26-codebase-audit/modules/`.  Ground truth:
`docs/test-policy.md`, `docs/contract-invariants.md`,
`docs/audits/2026-05-26-codebase-audit/intent.md` (§7 authoritative).

This file is **findings only** — no source or test edits proposed in
code, only T-NNN findings the maintainer can act on.

---

## 1. Executive summary

**Why current tests miss silent failures (the recurring bug class):**

- **"Did it raise?" is the dominant test shape.** Across the 11 module
  audits, at least 14 findings cite tests that assert only on
  completion, count, or class identity (F-broker-008, F-executor-002,
  F-executor-010, F-orch-007, F-orch-015, F-agents-misc-007,
  F-agents-misc-010, F-contract-008, F-contract-009, F-analysts-013,
  F-risk_gate-011, F-data-013, F-data-014, F-backtest-006).
  Test-policy §A.7 / §E forbid this exact shape; the suite predates
  §A.7 and was never retro-fitted.
- **Mock shapes encode bugs.** F-broker-003 (`await resp.json()` —
  cemented by `AsyncMock` in F-broker-008) and F-agents-misc-007 (patch
  target is `yfinance.Ticker`, production calls `data.get_price_history`)
  are the canonical instances: a green test only because the mock
  matches the buggy call, not the real one.  Stubbing above the leaf
  HTTP boundary (test-policy §A.5) — never tripped because nothing
  forces leaf-level patching.
- **No happy-path "no degradation" assertions.** Every analyst fetch,
  the social-sentiment provider (F-data-004), the snapshotter SPY
  fetch (F-agents-misc-006), the BlackHole `except RuntimeError: pass`
  quartet in the backtest driver (F-backtest-002), the
  `_seed_initial_prices` 0.0 default (F-backtest-004), and the
  `_strategist_validation_callback` (F-strategist-001) all degrade
  silently.  Tests are written around the degradation path returning,
  not around the happy path producing real signal — so a regression
  from "real signal" → "neutral fallback" is invisible.
- **`is_no_data=True` is the universal silent-failure attractor.**
  Joiners synthesise neutral verdicts for missing per-ticker keys
  (F-contract-005), digest neutral-fills missing analysts
  (F-contract-005), strategist context-shim falls back to empty,
  the executor swallows `AssertionError` in the after-callback
  (F-executor-001), risk-gate bare-returns on missing decision
  (F-risk_gate-001).  Test-policy §G.7 calls this out explicitly;
  approximately zero tests pair length / completion assertions
  with `is_no_data=False`.
- **Live ≠ backtest divergence has no symmetry tests.** F-orch-001
  (live writes raw `datetime`, backtest writes ISO), F-orch-002 (only
  backtest installs `HandleInjectorPlugin`), F-orch-003 (two
  hand-maintained Phase 2 seeders), F-broker-001 (production reaches
  into `FakeBroker._prices`), F-risk_gate-002 (risk-gate prices only
  from `_prices`) — every one is an asymmetric path with no contract
  test asserting "both lifecycles produce identical state shape at
  identical phases" (the §B Phase 2 invariant in
  `contract-invariants.md`).  Live tick has never been exercised
  end-to-end against `DatabaseSessionService`.

**Top consolidation opportunities:**

- **Directory-layout schism.** `tests/<module>/` (executor, analysts,
  orchestrator, agents, contract, backtest) coexists with
  `tests/unit/<module>/` (agents, executor, orchestrator, contract,
  data, backtest, baselines, config, observability) — same modules,
  two trees, ad-hoc placement decisions.  Pick one.
  (F-executor-011, F-orch-008, F-data-017, F-risk_gate-010 all
  symptom-flag this.)
- **Strategist legacy-callback test cluster.**  Five test files
  (F-strategist-002) exercise `_strategist_validation_callback`, which
  intent §7.2 confirms is dead in production and survives only as a
  delegate for these tests.  Delete-or-port.
- **Smart-money test cluster.**  Five test files (F-analysts-003) test
  smart-money code paths that the live pipeline never invokes
  (per F-analysts-001).  Delete once the human resolves the F-001
  fork.
- **Phase-3 unused provider tests.**  ~975 LOC of unit tests
  (F-data-016) cover the four `earnings` / `analyst_consensus` /
  `short_interest` / `options` providers that have zero consumers
  (F-data-001).
- **`test_end_to_end_smoke.py` mega-file** (756 LoC, F-backtest-013):
  split per concern; until split, no shared fixture for "construct a
  realistic tick state" — see T-005 below.

**Test-policy compliance score (impressionistic):**

- §A.1 / §A.4 (no real keys / LLM opt-in) — **fully followed**.
- §A.2 / §A.3 (cache & one-tick rules) — **followed** by the smoke
  layer.
- §A.5 (leaf-HTTP stubbing) — **partially**; some tests stub above the
  leaf (e.g. F-agents-misc-007 patches `yfinance.Ticker` instead of
  `data.get_price_history`).
- §A.6 (tests own their state) — **followed**.
- §A.7 (surface silent failures loudly) — **widely violated**.
  This is the single highest-leverage gap.
- §B (layer taxonomy / location) — **violated by layout schism**
  (above).
- §E ("counts not content") — **widely violated**.
- §G.7 (`is_no_data=True` attractor) — **violated**; few tests assert
  `is_no_data=False` on happy paths.
- §G.8 (`branch_failed` warnings not benign) — **violated**; no
  pipeline-level `caplog`-based test asserts absence of
  `branch_failed`.

Overall: the structural rules (no live keys, one-tick smoke, cache
discipline) are honoured; the behavioural rule (§A.7) is the gap.

---

## 2. Silent failures by test-coverage status

Every silent-failure finding from the module audits, mapped to the
nearest existing test and why it doesn't catch the bug.  **Cheapest
fix shape** is one-test-one-bug; many overlap with the shared
"loud-failure" fixtures proposed in §6.

| Finding | Description | Nearest existing test | Why it doesn't catch | Cheapest fix shape |
|---|---|---|---|---|
| **F-broker-001** | Production `risk_gate/agent.py:101` reaches into `FakeBroker._prices`; T212 has no such attr — live silently loses price fallback. | None directly; `tests/unit/agents/risk_gate/test_agent.py` uses `FakeBroker` only. | All risk-gate tests use `FakeBroker`, so the `hasattr(_prices)` branch is always taken. | Parameterise risk-gate tests over `FakeBroker` AND a stub `Trading212Broker` with `position_size`-only surface; assert prices come from `state["reference_prices"]`. |
| **F-broker-002** | T212 `get_portfolio` silently drops unknown instruments. | None — F-broker-009 confirms zero coverage of `get_portfolio`. | No test exists. | One unit test that constructs a T212 response with an unmapped instrument and asserts a `WARNING` log + raises (or surfaces) the drop. |
| **F-broker-003** | `await resp.json()` is a never-tripped runtime bug; `httpx.Response.json()` is sync. | `tests/unit/test_trading212_request_construction.py` (F-broker-008). | Test uses `AsyncMock` for `.json`, so `await dict` doesn't fire. Mock encodes the bug. | Switch mock to `MagicMock(return_value={...})` — test will fail until the source is fixed. |
| **F-data-004** | Finnhub social-sentiment soft-fails to empty `SocialSentiment(snapshots=[], aggregate_score=0.0)` on every API exception. | `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py` (22 LoC). | Test only checks `as_of` is accepted; does not exercise the exception path. | One test that raises `FinnhubAPIException` from the leaf fetch and asserts either an exception OR a structured-log warning record. |
| **F-data-005** | News/Tiingo, politician/Quiver, politician/FMP return `[]` on missing API key. | `tests/unit/data/providers/test_news_tiingo.py` etc. (F-data-014) — assert `out == []`. | Tests **encode the bug as expected behaviour**. | Flip to `pytest.raises(SecretMissingError)`; will fail until provider raises. |
| **F-data-006** | News providers silently `return []` on reversed-window inputs. | None. | No test exercises malformed-window inputs. | One test per provider: pass `start > end`, assert `ValueError`. |
| **F-data-008** | `timeguard` wall-clock fallback bumps a counter live never reads. | None. | No live-mode counter assertion. | Either delete the counter or add a test that asserts the `WARNING` log fires when fallback is used in live mode. |
| **F-data-019** | Bare `except Exception` in EDGAR / pit_composite leaf parsers. | None. | No test asserts the warning-on-swallow shape. | Add a "malformed row" test that asserts a structured warning is logged. |
| **F-analysts-012** | Every analyst fetch swallows exceptions and returns `[]` / `None`. | `tests/integration/test_analyst_pool.py` stubs leaf fetches; never raises. | No `caplog`-based assertion that the warning record fires (F-analysts-013). | One pool-level test that raises from one analyst's leaf fetch and asserts the per-ticker warning + IsolatedFailureWrapper branch_failed log fires. |
| **F-contract-001** | Deterministic extractors fabricate synthetic `AnalystReport`s because of the `_report_required_when_data_present` validator. | None. | No test asserts deterministic verdicts carry `report=None`. | Schema-level test asserting deterministic analyst verdicts pass with `report=None`; OR a contract test pinning the synthetic-report shape and policy. |
| **F-contract-005** | `_fill_missing` silently neutral-fills a missing analyst slot. | `tests/unit/contract/test_digest.py` tests `_fill_missing` positively; no assertion that the fill is logged. | Tests verify the fill works, not that it warns. | `caplog`-based assertion that fill emits a structured warning. |
| **F-risk_gate-001** | Missing `strategist_decision` triggers a bare `return` — no `final_orders`, no log, no event. | None (F-risk_gate-011 confirms). | No test passes a state without `strategist_decision`. | One test asserts that an empty / missing decision either raises OR yields `final_orders=[]` + a warning log. |
| **F-risk_gate-002** | Risk-gate cannot price an unheld BUY because it never reads `state["reference_prices"]`. | None. | All tests preload `_prices` into the FakeBroker, masking the bug. | One test with a stance for a ticker not in `portfolio.positions` and `_prices` empty; assert the order is produced using `reference_prices`. |
| **F-executor-001** | `_executor_thesis_writer_callback` swallows `AssertionError` via `print(file=sys.stderr)` (not `logger`). | None. | No `caplog` assertion on the BUY-with-no-fill-price path. | Force-construct an `executions` list where a BUY row has `actual_price=None`; assert a `logger.error` record fires (will fail until `print` becomes `logger.error`). |
| **F-executor-006** | `fill_prices[ticker] = None` when row is `status="rejected"` then trips the BUY assertion. | `tests/integration/test_executor_with_fake_broker.py::test_executor_rejection_continues` (F-executor-010). | Only asserts `status == "rejected"`; never combines a rejected row with a filled BUY in the same tick. | One regression test that mixes one rejected row + one filled BUY in `executions`; assert no `None`-fill assertion fires. |
| **F-executor-008** | Idempotency guard at `_run_async_impl` doesn't cover the after-callback — re-running clobbers `user:positions`. | `test_executor_idempotent` asserts `"executions" not in state`. | No assertion the after-callback didn't fire; no full Runner-lifecycle test. | One Runner-driven test invoking executor twice with same `tick_id`; assert `user:positions` is identical after both. |
| **F-orch-001** | Live `_build_initial_state` writes raw `datetime` `as_of`; backtest coerces to ISO. | `tests/unit/orchestrator/test_tick_as_of_phase.py:48-50` (F-orch-009). | Test asserts `isinstance(as_of, datetime)` — **encodes the broken behaviour**. | Replace with a round-trip test through `DatabaseSessionService.create_session` and assert `resolve_as_of` succeeds. |
| **F-orch-002** | Live `run_once` never installs `HandleInjectorPlugin`; future trace writers will be silently dropped. | None. | No live-tick observability handle test exists. | Contract test asserting Runner constructed in `run_once` has `plugins=[HandleInjectorPlugin(...)]` if observability handles are configured. |
| **F-orch-004** | Lifecycle `_STOCKBOT_TABLES` lists only 3 of 6 ORM tables — hard reset / preflight skips 3. | `tests/unit/test_init_db_script.py` hard-codes the **same stale set** of 3 (F-orch-010). | Test cements the bug. | Replace hard-coded list with `set(Base.metadata.tables.keys())` and assert preflight covers every table. |
| **F-orch-011** | Live `tick.run_once` catches `BaseException` (swallows `KeyboardInterrupt` etc.) and doesn't enforce pipeline-completion. | None. | No test exercises mid-pipeline failure in live path. | Two tests: (a) raise `KeyboardInterrupt` mid-pipeline, assert it propagates; (b) raise `Exception` mid-pipeline, assert `last_snapshot` absence triggers the abort. |
| **F-agents-misc-006** | Snapshotter swallows every SPY-fetch exception and substitutes `spy_price=0.0` with no log. | `tests/integration/test_snapshotter.py` (F-agents-misc-007). | Patch target is `yfinance.Ticker`; production calls `data.get_price_history`. Patch is a no-op and tests don't assert `spy_price`. | Repoint patch to `data.get_price_history`; assert `spy_price == 470.0` on happy path AND assert structured warning on raise. |
| **F-agents-misc-015** | `_is_schema_error` silently returns `False` on `ImportError` of pydantic (hard dep). | None. | No test forces ImportError. | Either delete the guard or test that ImportError raises a configuration error. |
| **F-analysts-005** | Smart-money analyst writes `state["smart_money_verdicts"]` directly without `state_delta` (Rule 1 violation). | None (smart_money is shelved; F-analysts-001). | No live pipeline test exercises smart_money. | Moot if smart_money stays shelved. Test would assert the joiner yields a single Event with `state_delta` carrying the key. |
| **F-backtest-002** | Four `except RuntimeError: pass` swallows in driver loop. | `tests/integration/backtest/test_end_to_end_smoke.py` runs with store wired, never trips the guards. | Production path never trips the guard, so silent skip is invisible. | One driver-construction test with no store wired; assert each entry-point either raises OR logs a structured warning, not silently no-ops. |
| **F-backtest-003** | `except (AttributeError, Exception)` redundant tuple — dead defensive. | Smoke covers happy path. | Redundancy is not behavioural; tests cannot see it. | Lint/code change only — not a test gap. |
| **F-backtest-004** | `_seed_initial_prices` silently sets price=0.0 for tickers with no OHLCV. | None. | No test seeds a watchlist ticker without OHLCV bars. | One runner-level test with a watchlist ticker absent from the cache; assert raise OR `metrics.md` flags the zero. |
| **F-backtest-005** | `decision_logger.py:339` reads bare `state["positions"]`; will silently return `None` when context-shim write is removed. | None. | No test asserts `held_view_at_decision` is non-null on the happy path. | Read `state["user:positions"]` instead; add positive assertion on `held_view_at_decision` content. |
| **F-backtest-010** | `_verify_filing` / `_verify_news` hard-code `agreement_with_cache=True` — a tripwire that can never fire. | None. | The verifier is invoked but its result is hard-coded green. | Make the placeholders return `None` ("unknown"); update SUMMARY rendering and tests to expect neutral, not green. |
| **F-ops-001** | `scripts/trace_tick.py` uses bare-key `"_trace"` (not `temp:_trace`) — silently empty traces if anyone reruns. | None. | The script's a one-off; no test runs it. | Migrate or delete the script. |
| **F-ops-007** | `make_observability_callbacks._after` bare `except Exception: pass` for usage-metadata extraction. | `tests/unit/observability/test_terminal_log.py` covers the happy path. | No test forces the swallow. | Add a test with a malformed `usage_metadata`; assert a `logger.exception` record fires. |

**Pattern across the table:** the cheapest fix in almost every row is
"add one `caplog`-based assertion that the structured warning fires
on the failure path" or "assert positive output value (non-zero,
non-empty) on the happy path".  Both shapes are 5-10 line tests.

---

## 3. Test-policy compliance findings

### T-001 — §A.7 widely violated: "Did it raise?" tests
- **Category:** test-policy violation
- **Severity:** P0
- **Location:** at least 14 module audits cite this shape, including:
  `tests/unit/test_tick_entrypoint.py` (F-orch-007),
  `tests/unit/test_memory_writer_agent.py` (F-agents-misc-010),
  `tests/unit/test_tick_state.py` (F-orch-015),
  `tests/integration/test_executor_with_fake_broker.py::test_executor_rejection_continues` (F-executor-010),
  `tests/integration/test_snapshotter.py` (F-agents-misc-007),
  `tests/unit/contract/test_evidence.py` (F-contract-008).
- **Evidence:** All assert only "imports", "is async", "doesn't
  raise", `len(...) == N`, or class identity.  None assert positive
  output content.
- **Suggested action:** for each, add at minimum one content
  assertion (e.g. `assert verdicts[0].direction == "bullish"`,
  `assert not verdicts[0].is_no_data`, `assert snap["spy_price"] >
  0`) or delete if the test exercises nothing else.

### T-002 — §G.7 `is_no_data=True` attractor: no happy-path assertions
- **Category:** test-policy violation
- **Severity:** P0
- **Location:** every analyst joiner test, every digest test, every
  pipeline-level smoke.  No `assert not verdict.is_no_data` on happy
  path anywhere.
- **Evidence:** Test-policy §G.7 is explicit: *"Always assert
  `is_no_data=False` on happy-path verdicts so this trap fails the
  test instead of hiding inside it."*  Grep across `tests/` for
  `is_no_data` returns only schema tests and a handful of negative
  assertions.
- **Suggested action:** add a shared fixture
  `assert_no_silent_degradation(state)` that walks every
  `{domain}_verdicts` list and asserts no entry has `is_no_data=True`
  unless the test is deliberately exercising a degraded branch.
  Invoke from every happy-path pipeline test.

### T-003 — §G.8 `branch_failed` not asserted absent on happy path
- **Category:** test-policy violation
- **Severity:** P1
- **Location:** all pipeline-level / smoke tests
  (`tests/integration/test_analyst_pool.py`,
  `tests/integration/backtest/test_end_to_end_smoke.py`).
- **Evidence:** `grep -rn "branch_failed" tests/` returns no
  `caplog`-based absence-assertion.  Test-policy §G.8 is explicit.
- **Suggested action:** add `caplog.set_level(WARNING)` +
  `assert not any("branch_failed" in r.message for r in caplog.records)`
  to every happy-path pipeline test.  One-line addition; high
  leverage.

### T-004 — §A.5 leaf-HTTP-boundary rule violated
- **Category:** test-policy violation
- **Severity:** P1
- **Location:** `tests/integration/test_snapshotter.py:26-44`
  (F-agents-misc-007) patches `yfinance.Ticker`; production calls
  `data.get_price_history`.  `tests/unit/test_trading212_request_construction.py`
  patches at `client.post.return_value.json = AsyncMock(...)` —
  matches the buggy `await resp.json()` shape, not the real `httpx`
  shape.
- **Suggested action:** repoint patches to the leaf seam
  (`data.get_price_history`, `httpx.AsyncClient.post`); add a CI lint
  check that flags `patch("yfinance.*")` or any patch above the
  documented leaf-seam list.

### T-005 — Tests encode bugs (mock-shape regression cement)
- **Category:** test-policy violation
- **Severity:** P0
- **Location:** F-broker-008 (`AsyncMock` cements `await resp.json()`),
  F-data-014 (`out == []` cements the missing-key silent return),
  F-orch-009 (`isinstance(as_of, datetime)` cements live's broken
  raw-datetime write), F-orch-010 (hard-coded stale 3-table list),
  F-risk_gate-009 (`"hold" in _NO_RISK_GATE_INTENTS` cements stale
  verb).
- **Evidence:** Each test would need to flip its assertion to a fix
  shape (`raises`, `IsoString`, four-verb vocab) when the
  underlying bug is fixed.
- **Suggested action:** when fixing each underlying finding, rewrite
  the cementing test in the same patch (the test should fail
  pre-fix and pass post-fix).

### T-006 — Live/backtest symmetry not asserted
- **Category:** test-policy gap
- **Severity:** P1
- **Location:** No test compares `_build_initial_state` (live) with
  `Runner._seed_state` (backtest) for key-shape parity.  F-orch-003
  flags `tests/unit/backtest/test_runner_initial_state_parity.py`
  exists — but it only checks the backtest side.
- **Evidence:** §B Phase 2 of the contract requires both lifecycles
  end with identical key sets; no test asserts this.
- **Suggested action:** one contract test:
  `assert set(_build_initial_state(...).keys()) ==
  set(runner._seed_state(...).keys())` (with each side called via
  its real entry point).

### T-007 — Cache-confined rule honoured (no finding)
- **Category:** test-policy compliance — confirmed
- **Severity:** n/a
- **Location:** all cache-touching tests use `tmp_path/store.sqlite`.
- **Evidence:** No test writes to `backtests/` per audit.  Confirmed
  compliant.

### T-008 — `as_of` mandatory rule mostly honoured
- **Category:** test-policy compliance — confirmed with one gap
- **Severity:** P2
- **Location:** Extractor tests under
  `tests/unit/contract/extractors/test_extractor_as_of.py` pin the
  signature.  Provider tests pass `as_of`.  Gap: F-orch-009 — live
  `_build_initial_state` test asserts `as_of` is a `datetime`,
  contradicting "every datetime write to state must ISO-stringify
  first" (user memory).
- **Suggested action:** rewrite the live test to assert ISO-string +
  `resolve_as_of` round-trip.

---

## 4. Consolidation opportunities

### T-101 — Directory-layout schism between `tests/<module>/` and `tests/unit/<module>/`
- **Category:** test-consolidation
- **Severity:** P0
- **Location:** `tests/analysts/` vs `tests/unit/agents/analysts/`;
  `tests/executor/` vs `tests/unit/executor/` vs
  `tests/unit/agents/executor/`; `tests/orchestrator/` vs
  `tests/unit/orchestrator/`; `tests/agents/memory/` vs
  `tests/unit/agents/`.
- **Evidence:** Test-policy §B mandates `tests/unit/<module-mirror>/`
  for unit tests and `tests/integration/` for integration.  The
  top-level `tests/analysts/`, `tests/executor/`,
  `tests/orchestrator/`, `tests/agents/` directories are non-
  taxonomic; F-executor-011, F-orch-008, F-risk_gate-010, F-data-017
  each independently flag overlapping tests across the two layouts.
- **Suggested action:** consolidate every test under the taxonomy in
  test-policy §B:
  - `tests/unit/<module-mirror>/` for unit tests
  - `tests/integration/` for cross-module integration
  - `tests/contract/` for boundary invariants
  - `tests/backtest/` for cache + audit primitives
  Migrate `tests/analysts/`, `tests/executor/`, `tests/orchestrator/`,
  `tests/agents/` into the canonical locations; resolve overlaps by
  keeping the higher-content variant.

### T-102 — No shared "build a realistic tick state" fixture
- **Category:** missing shared fixture
- **Severity:** P1
- **Location:** `tests/conftest.py`, nested conftests.
- **Evidence:** Every pipeline-touching test (smoke, executor,
  risk-gate, strategist enricher, snapshotter) hand-rolls a state
  dict.  F-risk_gate-010 calls out three copies of `_make_ctx` in
  risk_gate tests alone.  F-backtest-013 (756-line smoke) inlines
  the same state-assembly work.
- **Suggested action:** add `tests/conftest.py::tick_state()`
  fixture that builds a contract-compliant state dict (all §A keys
  populated, including `temp:_trace`, `temp:_decision_logger`,
  `reference_prices`, `user:positions`, etc.) — parametrised by
  watchlist + held positions.  Every pipeline test composes from
  this.

### T-103 — Strategist legacy-callback test cluster
- **Category:** test-consolidation / dead-test
- **Severity:** P1
- **Location:** five files per F-strategist-002.  Per intent §7.2
  the callback is dead in production.
- **Suggested action:** delete-or-port: delete the pure callback
  tests (`test_validation_callback.py`,
  `test_strategist_minimal_schema_no_retry.py`); port useful
  assertions to `test_enricher.py` against the live
  `StrategistEnricher` path.

### T-104 — Smart-money test cluster
- **Category:** test-consolidation / dead-test
- **Severity:** P1 (conditional on F-analysts-001 fork)
- **Location:** five files per F-analysts-003.
- **Suggested action:** delete if smart-money stays shelved; rewrite
  to assert canonical no-data shape if re-enabled.

### T-105 — Phase-3 unused-provider test cluster
- **Category:** test-consolidation / dead-test
- **Severity:** P1
- **Location:** ~975 LoC across `test_analyst_consensus_yfinance.py`,
  `test_earnings_finnhub_as_of.py`,
  `test_short_interest_finra_as_of.py`,
  `test_options_yfinance_shell.py` (F-data-016).
- **Suggested action:** delete alongside F-data-001.

### T-106 — `test_end_to_end_smoke.py` mega-file (756 LoC)
- **Category:** test-consolidation
- **Severity:** P2
- **Location:** `tests/integration/backtest/test_end_to_end_smoke.py`
  (F-backtest-013).
- **Suggested action:** split into per-concern smokes
  (`test_smoke_pipeline_completes.py`,
  `test_smoke_telemetry_written.py`,
  `test_smoke_decision_logger_writes.py`,
  `test_smoke_state_shape.py`) sharing a conftest fixture (T-102).

### T-107 — Data-registry tests scattered across six files
- **Category:** test-consolidation
- **Severity:** P2
- **Location:** F-data-017: six files under `tests/unit/data/`
  re-import providers and poke at `_REGISTRY` from slightly
  different angles.
- **Suggested action:** consolidate into one
  `tests/unit/data/test_registry.py` covering registration,
  dispatch, swap, pacing, and `as_of` plumbing.

### T-108 — Risk-gate tests duplicated across four files
- **Category:** test-consolidation
- **Severity:** P2
- **Location:** F-risk_gate-010.
- **Suggested action:** consolidate `_make_ctx` to
  `tests/unit/agents/risk_gate/conftest.py`; merge the integration
  variants into one parameterised file.

### T-109 — `make_session_service` duplicated across two files
- **Category:** test-consolidation
- **Severity:** P2
- **Location:** F-orch-008 —
  `tests/unit/test_session_service_factory.py` and
  `tests/unit/orchestrator/test_persistence.py` are functionally
  identical.
- **Suggested action:** keep the mirrored-layout copy in
  `tests/unit/orchestrator/`; delete the other.

---

## 5. Dead-test inventory

Aggregated and deduped from all 11 module audits.  Grouped by reason.

### Group A — Tests for shelved smart-money (5 files)
*Dependency: F-analysts-001 resolution.*
- `tests/analysts/test_smart_money.py`
- `tests/unit/test_smart_money_fetch.py`
- `tests/unit/test_smart_money_gate.py`
- `tests/unit/test_derive_smart_money_verdict.py`
- `tests/agents/memory/test_writer_smart_money_seen.py`

### Group B — Tests for dead `_strategist_validation_callback` (5 files)
*Dependency: F-strategist-001 deletion.*
- `tests/integration/test_strategist_minimal_schema_no_retry.py`
- `tests/integration/backtest/test_end_to_end_smoke.py:390-408` (partial)
- `tests/integration/backtest/test_fresh_run_starts_clean.py:161-190,261` (partial)
- `tests/unit/agents/strategist/test_validation_callback.py`
- `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`

### Group C — Tests for dead `evidence_view.py` (3 files)
*Dependency: F-strategist-004 deletion.*
- `tests/unit/agents/strategist/test_evidence_view.py`
- `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py`
- `tests/unit/agents/strategist/test_evidence_view_missing_report.py`

### Group D — Tests for Phase-3 unused providers (4 files, ~975 LoC)
*Dependency: F-data-001 deletion.*
- `tests/unit/data/providers/test_analyst_consensus_yfinance.py`
- `tests/unit/data/providers/test_earnings_finnhub_as_of.py`
- `tests/unit/data/providers/test_short_interest_finra_as_of.py`
- `tests/unit/data/providers/test_options_yfinance_shell.py`

### Group E — Tests pinning deleted contract fields
- `tests/unit/contract/test_evidence_raw_text.py` — pins
  `raw_text` field (F-contract-002 dead).
- `tests/unit/contract/test_invariants_doc_carveout.py` — pins
  Phase8 doc presence + callback carve-out clause (F-contract-007;
  intent §7.2).
- `tests/unit/orchestrator/test_risk_gate.py::test_no_risk_gate_intents_constant_contains_hold_and_update`
  — pins stale `hold` verb (F-risk_gate-009; cements F-risk_gate-003).
- `tests/integration/test_risk_gate_agent.py` +
  `tests/integration/test_risk_gate_state_delta.py` — fixtures use
  deleted `thesis` / `close_reasons` fields (F-risk_gate-012).
- `tests/executor/test_executor_bookkeeping.py:40-52`,
  `tests/unit/executor/test_open_positions_state.py:161-170,205-212`,
  `tests/unit/agents/test_executor_decision_hook.py:163-170` —
  `_THESIS` fixtures carry deleted `horizon` / `target_price` /
  `stop_price` / `last_review_note` keys (F-executor-003);
  `test_executor_decision_hook.py:78-92` uses deleted
  `intent="open"` (F-executor-002).

### Group F — Smoke / tautological tests
- `tests/unit/test_tick_entrypoint.py` — "module imports" /
  "function is async" only (F-orch-007).
- `tests/unit/test_tick_state.py` — tests unused `TickState`
  Pydantic class (F-orch-015; conditional on F-orch-005).
- `tests/unit/test_memory_writer_agent.py` — `issubclass` /
  `name ==` only (F-agents-misc-010).
- `tests/unit/baselines/test_spy_metrics_removed.py` — asserts a
  Phase-7 deletion stays deleted (F-ops-006).
- `tests/unit/test_buffer_persistence.py` — exercises unwired
  `BufferEntryRow` CRUD (F-agents-misc-009, conditional on
  F-agents-misc-005).
- `tests/unit/test_memory_schema.py:35-47` —
  `test_memory_projection_*` for unused `MemoryProjection`
  (F-agents-misc-008, conditional on F-agents-misc-003).

### Group G — Test cements buggy behaviour
*(Listed in §3 / T-005; repeated here for the dead-test
inventory's completeness.)*
- `tests/unit/test_trading212_request_construction.py` (F-broker-008
  — `AsyncMock` shape cements `await resp.json()` bug).
- `tests/unit/data/providers/test_news_tiingo.py`,
  `test_politician_trades_quiver_as_of.py`,
  `test_politician_trades_fmp.py` (F-data-014 — `out == []` cements
  missing-key silent return).
- `tests/unit/orchestrator/test_tick_as_of_phase.py:48-50` (F-orch-009
  — `isinstance(as_of, datetime)` cements live raw-datetime write).
- `tests/unit/test_init_db_script.py` (F-orch-010 — hard-codes stale
  3-table list).

### Group H — Single-call / unused-fixture orphan
- `tests/backtest/test_cache_hits_audit.py` — sole caller of
  `build_telemetry_record_from_logs`, which is itself dead
  (F-backtest-006).

**Inventory totals:** ~25 deletable files outright (Groups A–F);
~6 files requiring partial cleanup (Group E partials); ~7 files
needing rewriting in lockstep with bug-fix patches (Group G);
1 orphan (Group H).

---

## 6. Suggested invariant tests (gap-fillers)

Ten high-leverage tests that would catch a whole class of
silent-failure bug.  Each one is small (~10-30 lines) and
fixture-shareable.

### T-201 — `assert_no_silent_degradation(state)` fixture + happy-path invocation
- **What it asserts:** For every `{domain}_verdicts` key in `state`,
  no entry has `is_no_data=True` unless the test marks the branch as
  intentionally degraded.  Same for `{domain}_evidence` rows.
- **Catches:** F-contract-005, F-analysts-013, F-data-004,
  F-agents-misc-006 (Snapshotter SPY=0 baseline), and every future
  "neutral fallback masked the bug" regression.
- **Where:** `tests/conftest.py`; invoked by every happy-path
  pipeline test.

### T-202 — Pipeline-level `branch_failed` absence assertion
- **What it asserts:** `caplog.set_level(WARNING)` in every smoke +
  integration pipeline test; assert no `branch_failed`,
  `branch_*_failed`, `fetch failed`, `snapshot_spy_fetch_failed`, or
  `usage_metadata_error` records on the happy path.
- **Catches:** F-analysts-013, F-analysts-012, F-agents-misc-006,
  F-ops-007.
- **Where:** add to `tests/integration/test_analyst_pool.py`,
  `tests/integration/backtest/test_end_to_end_smoke.py`, and the
  per-domain integration tests.

### T-203 — Live ≡ backtest Phase 2 state-shape contract test
- **What it asserts:**
  `set(_build_initial_state(...).keys()) ==
  set(Runner._seed_state(...).keys())`.  Both sides invoked via
  their real entry point against a shared mock broker.
- **Catches:** F-orch-001 (would have caught the `as_of` type
  drift), F-orch-003 (the seeder schism), any future Phase 2
  divergence.
- **Where:** `tests/contract/test_phase2_state_parity.py` (new).

### T-204 — `_STOCKBOT_TABLES` ≡ `Base.metadata.tables.keys()`
- **What it asserts:** Lifecycle's hard-coded table list matches the
  ORM metadata exactly.
- **Catches:** F-orch-004 (would have caught the stale 3-table list
  the moment `ticker_stances` / `analyst_evidence` /
  `ticker_evidence` were added).
- **Where:** `tests/contract/test_lifecycle_tables_match_orm.py`
  (new).

### T-205 — Broker protocol parity test
- **What it asserts:** `FakeBroker` and `Trading212Broker` expose
  the same public surface (every method on the `Broker` protocol;
  no `_prices`-style private channel reachable from production
  code).
- **Catches:** F-broker-001 (would have caught the
  `hasattr(broker, "_prices")` leak in `risk_gate/agent.py:101`),
  F-broker-005 (`position_size` unused on both), F-broker-006
  (`set_price` un-protocoled).
- **Where:** `tests/contract/test_broker_protocol_parity.py` (new).

### T-206 — Risk-gate-prices-from-reference-prices test
- **What it asserts:** A stance to BUY an unheld ticker produces an
  order priced from `state["reference_prices"]`, not from broker
  state.
- **Catches:** F-risk_gate-002 (would have prevented the live-
  blocking ValueError on first new BUY), reinforces T-205.
- **Where:** `tests/unit/agents/risk_gate/test_unheld_buy_pricing.py`
  (new).

### T-207 — Idempotency-guard coverage extending to after-callback
- **What it asserts:** Running the Executor twice with the same
  `tick_id` leaves `user:positions` identical (after-callback
  doesn't re-fire and clobber).
- **Catches:** F-executor-008.
- **Where:** extend
  `tests/integration/test_executor_with_fake_broker.py::test_executor_idempotent`.

### T-208 — Provider missing-key behaviour test
- **What it asserts:** Every provider raises `SecretMissingError`
  (not silently returns `[]`) when its API-key env var is unset.
- **Catches:** F-data-005, F-data-014 (cementing tests would flip).
- **Where:** `tests/contract/test_provider_secret_missing.py` (new),
  parametrised over all providers.

### T-209 — Live observability handle installation test
- **What it asserts:** Live `run_once`, when constructed with
  observability handles, installs `HandleInjectorPlugin` on the
  Runner.  Asserting on Runner construction, not behaviour.
- **Catches:** F-orch-002 (the latent silent-empty-traces trap if
  anyone reaches for direct mutation in live).
- **Where:** `tests/contract/test_observability_install_pattern.py`
  (new).

### T-210 — Joiner verdict/evidence consistency test
- **What it asserts:** For every analyst joiner output,
  `set(verdicts ticker keys) == set(evidence ticker keys)`, and the
  per-ticker verdict in `evidence[t].verdict` equals the
  corresponding entry in `verdicts[t]`.
- **Catches:** F-analysts-016 (drift between `{domain}_verdicts`
  and `{domain}_evidence`); makes the F-007 joiner dedupe safe.
- **Where:** `tests/unit/agents/analysts/news/test_joiner.py`,
  `tests/unit/agents/analysts/fundamental/test_joiner.py`.

---

## 7. Test-policy edits proposed

The policy is sound and well-grounded.  Three small additions are
warranted; no rule is wrong.

### T-301 — Add positive-assertion fixture to §D Conventions
- **Rule:** §D Conventions → fixtures section.
- **Current text:** lists `conftest.py` and `load_fixture` but no
  canonical "silent-failure detector".
- **Proposed change:** add a paragraph:
  > "A repository-wide
  > `assert_no_silent_degradation(state)` fixture lives in
  > `tests/conftest.py`.  Every happy-path pipeline test invokes
  > it after the tick completes — it asserts no `{domain}_verdicts`
  > entry has `is_no_data=True` and the structured-log record set
  > contains no `branch_failed` / `*fetch failed` warnings.  Tests
  > deliberately exercising a degraded branch override this with
  > `@pytest.mark.allow_degradation('news')` (or similar)."
- **Why:** institutionalises §A.7 in a way that is hard to forget;
  catches F-contract-005, F-analysts-012, F-data-004, F-agents-misc-006
  and every future instance of the same class.

### T-302 — Add `tests/unit/` ≡ source-tree-mirror enforcement
- **Rule:** §B Test taxonomy table.
- **Current text:** says unit tests live in `tests/unit/<module-mirror>/`.
- **Proposed change:** add to §E Anti-patterns:
  > "**Top-level module folders under `tests/` (e.g.
  > `tests/analysts/`, `tests/executor/`, `tests/orchestrator/`,
  > `tests/agents/`) are forbidden.**  Every unit test goes under
  > `tests/unit/<module-mirror>/`; every integration test under
  > `tests/integration/`.  Putting unit-style tests at the top level
  > makes layer-taxonomy enforcement (markers, CI selection, fixture
  > inheritance) impossible."
- **Why:** the directory schism flagged in T-101 keeps recurring;
  the policy is silent on it today.  Codify the layout.

### T-303 — Cementing-tests anti-pattern
- **Rule:** §E Anti-patterns.
- **Current text:** lists "It didn't raise, therefore it works",
  "asserting only on counts", etc.
- **Proposed change:** add:
  > "**Tests that encode buggy behaviour as the expected result.**
  > If a test asserts `out == []` on a provider whose missing-key
  > path should raise, or asserts `isinstance(x, datetime)` on a
  > state field that must be ISO-stringified, or asserts a stale
  > vocabulary item is present in a constant, the test is
  > cementing the bug.  When fixing such a bug, rewrite the test
  > in the same patch so it would fail pre-fix and pass post-fix —
  > do not silently update assertions to match new behaviour."
- **Why:** five distinct findings (F-broker-008, F-data-014,
  F-orch-009, F-orch-010, F-risk_gate-009) hit this exact shape and
  the policy doesn't name it.

---

*End of test-strategy review.*

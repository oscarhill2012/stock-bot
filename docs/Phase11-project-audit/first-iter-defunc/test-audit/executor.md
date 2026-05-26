# Test audit — src/agents/executor

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/agents-executor.md` (primary); `docs/Phase11-project-audit/source-audit/agents-strategist.md` (peripherally — `StrategistDecision.thesis` shape)
**Test files in scope:** 8 (full list below)
**Tests collected from those files:** 41 (via `pytest <paths> --collect-only -q`)
**Findings:** 3 P0 · 6 P1 · 4 P2 · 1 P3

## Files in scope

The executor's tests are scattered across three parallel mirror trees plus
two integration files plus a shared writer-side suite. The split is itself
a layout finding (see P2-04).

- `tests/executor/` — **1 file** (root-level test tree)
  - `tests/executor/test_executor_bookkeeping.py` — 2 tests (trim vs full exit)
- `tests/unit/executor/` — **1 file** (mirror tree #2)
  - `tests/unit/executor/test_open_positions_state.py` — 3 tests (BUY/SELL state mutations)
- `tests/unit/agents/executor/` — **2 files** (canonical mirror tree per test-policy §B)
  - `tests/unit/agents/executor/test_thesis_writer_callback.py` — 8 tests (after-callback unit tests)
  - `tests/unit/agents/executor/test_verb_dispatch.py` — 11 tests (pure-helper tests)
- `tests/unit/agents/` — **1 file** (executor-specific, not in the executor subdir)
  - `tests/unit/agents/test_executor_decision_hook.py` — 4 tests (decision-logger hook)
- `tests/unit/backtest/` — **1 file** (writer-side leak suite, cross-cutting)
  - `tests/unit/backtest/test_wall_clock_leakage.py::test_executor_closed_at_uses_as_of` — 1 executor-relevant test
- `tests/integration/` — **2 files**
  - `tests/integration/test_executor_with_fake_broker.py` — 5 tests (executor + FakeBroker + DB)
  - `tests/integration/test_state_delta_user_prefix_end_to_end.py` — 1 test (full ADK Runner + DatabaseSessionService)

## Summary

The executor suite is unusually thorough on the *callback* and *verb-dispatch*
seams — `test_thesis_writer_callback.py` and `test_verb_dispatch.py` are model
unit-test suites with strong positive content assertions and a clear 1:1 to
Spec B acceptance bullets. The dominant weakness is structural: every single
`_run_async_impl`-driving test (six tests across three parallel directories)
hard-codes the bare-key `"positions"` bridge as the assertion target — which
means the entire suite *defends* the C2 parallel old/new branch flagged in
source-audit P1-01 rather than the canonical `user:positions`, and would
therefore actively block the source PR that drops the bare key. The second
gap is silent-failure surfacing for source-audit P1-02 and P1-03 (BUY-without-
matching-stance and dead-key fallback in `fill_prices`) — no test asserts
either fires loudly. Cross-subsystem note for the consolidator: the
`test_wall_clock_leakage.py::test_executor_closed_at_uses_as_of` test lives
under `tests/unit/backtest/` for thematic reasons but is really an
executor-closure test; it should probably be moved or duplicated under
`tests/unit/agents/executor/` so the executor subsystem owns its own
determinism guards.

## Findings

### P0-01 · T4 missing surfacing test · BUY-without-matching-open-stance silently degrades

- **Location(s):** new test needed under `tests/unit/agents/executor/`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` P1-02
- **Confidence:** high
- **Description:**
  Source-audit P1-02 names `agent.py:124-179` as a silent-degradation
  attractor: if a BUY order arrives whose ticker has no matching
  `intent="open"` stance in `strategist_decision.stances`, the executor
  produces a successful broker fill but never assembles a thesis and
  never adds the ticker to either `state["positions"]` or
  `state["user:positions"]`. No test covers this. Three BUY-path tests
  exist (`test_executor_buy_fills`, `test_executor_stamps_opened_price_on_buy`,
  `test_buy_writes_thesis_to_state_positions`) and all three feed a
  matching open-stance — none drives the "BUY with no stance at all"
  branch. Per the user memory `feedback_silent_failures_loud_tests`,
  this is exactly the recurring bug class.
- **Suggested action:**
  Add `test_buy_without_matching_open_stance_surfaces_loudly` to
  `tests/unit/agents/executor/test_open_positions_state.py` (after
  the source-fix lands). Drive a BUY order whose ticker is absent
  from `strategist_decision.stances` and assert the executor either
  raises or emits a `logger.warning` line via `caplog` — the source
  audit's recommended shape.

### P0-02 · T4 missing surfacing test · fill-price lookup dead-key fallback hides Execution shape drift

- **Location(s):** new test needed under `tests/unit/agents/executor/test_thesis_writer_callback.py`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` P1-03
- **Confidence:** high
- **Description:**
  Source-audit P1-03 flags the `(row["order"]["ticker"] or
  row["stance"]["ticker"]) or ""` / `(row["fill_price"] or
  row["actual_price"])` OR-chain in
  `_executor_thesis_writer_callback` (lines 397-408). The
  `Execution` Pydantic model carries only `order` + `actual_price`,
  so the alternative keys exist for dead producers — and when an
  upstream change ever omits `order`, the lookup silently falls back
  to `""` (ticker) and `None` (price), and `apply_stance_to_thesis`
  is called with `fill_price=None` which the surrounding `except
  (AssertionError, ValueError)` catch at lines 449-465 swallows with
  only a stderr print. No test in `test_thesis_writer_callback.py`
  asserts that this catch never fires on the happy path, and no test
  drives the dead-key branch deliberately. The happy-path tests
  produce `{"order": ..., "actual_price": ...}` so the OR-chain's
  short-circuit always selects the first alternative.
- **Suggested action:**
  Add two tests: (a) `test_callback_swallow_branch_does_not_fire_on_happy_path`
  using `capsys` to assert the stderr print is absent on every
  happy-path scenario; (b) `test_callback_execution_missing_order_key_fails_loudly`
  after the source-fix tightens the lookup — should raise rather
  than coerce to `fill_price=None`. Pair with source-fix PR for P1-03.

### P0-03 · T3 only-asserts-completion masks idempotency regression on a load-bearing path

- **Location(s):** `tests/integration/test_executor_with_fake_broker.py:51-64` (`test_executor_idempotent`)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` (no direct finding — but this is the test for the idempotency guard at `agent.py:79-81`)
- **Confidence:** high
- **Description:**
  The idempotency test asserts only `assert "executions" not in
  state` after running the executor against a state where
  `last_executed_tick_id == tick_id`. That confirms the early
  `return` fired, but it never asserts that *no broker call was
  made* — a future refactor that moved the broker call above the
  idempotency guard would leave `executions` unset (because the
  return statement still fires before the executions list is
  written into state) yet the broker would now have been called
  twice on retry. Critically, the test also never asserts on
  `state_delta["last_executed_tick_id"]` — the cross-tick
  propagation key that the source audit's P1-01 collapse will
  reshape. Per the user-memory `feedback_silent_failures_loud_tests`
  and source-audit recurring pattern, "did the executor short-
  circuit" is precisely the kind of behaviour where a positive
  broker assertion (e.g. `broker._orders` is empty, or the FakeBroker
  exposes a call counter) is the only honest check.
- **Suggested action:**
  Strengthen: assert `len(broker._orders) == 0` (or the equivalent
  on FakeBroker) and assert that no event was yielded — `events =
  [ev async for ev in agent._run_async_impl(ctx)]; assert events ==
  []`. Then the test catches both a regression in the guard
  position and the silent yield-an-empty-delta case.

### P1-01 · T2 parallel old/new branches · the entire `_run_async_impl` test set defends the bare-key `"positions"` side

- **Location(s):** every `_run_async_impl`-driving test —
  `tests/executor/test_executor_bookkeeping.py:130, 133, 176, 179`,
  `tests/integration/test_executor_with_fake_broker.py:121, 129, 248-257, 327-332`,
  `tests/unit/executor/test_open_positions_state.py:125, 136, 184-188`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` P1-01 (bare-key shadow)
- **Confidence:** high
- **Description:**
  Source-audit P1-01 proposes either dropping the bare key from the
  executor's state_delta or removing the bare-key write end-to-end.
  Either resolution invalidates every existing test that asserts
  positively on `delta["positions"]` (BUY → contains the assembled
  thesis; SELL → does not contain the ticker) and every negative
  assertion `"user:positions" not in delta` (this assertion is
  *correct today* for `_run_async_impl` but is structured as a
  permanent guard rather than a temporary one). Six tests across
  three parallel directories — `test_trim_preserves_position_thesis`,
  `test_full_exit_writes_one_trade_log_row_and_deletes`,
  `test_executor_stamps_opened_price_on_buy`,
  `test_cross_tick_buy_then_sell_produces_trade_log_row`,
  `test_buy_writes_thesis_to_state_positions`,
  `test_sell_removes_ticker_from_state_positions` — are written so
  tightly to the bare-key bridge that they cannot survive option
  (b) of the P1-01 fix and would have to be partially re-written
  even under option (a). This is the load-bearing T2 cluster for
  the executor subsystem.
- **Suggested action:**
  Once source-fix P1-01 lands, reshape every one of these tests to
  assert on `state["user:positions"]` (after wiring through a real
  ADK Runner or a stub that fires the after-callback) and delete the
  now-meaningless `"user:positions" not in delta` negative
  assertions. If the source fix picks option (b) (drop bare key
  entirely), the simpler ones — `test_buy_writes_thesis_to_state_positions`,
  `test_sell_removes_ticker_from_state_positions` — should move
  into the after-callback test file rather than keep their own
  `_run_async_impl` driving harness.

### P1-02 · T1 dead tests of `resolve_broker_call` if the source-audit P2-01 deletion path is taken

- **Location(s):** `tests/unit/agents/executor/test_verb_dispatch.py:76-131, 313-330` (5 tests)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` P2-01
- **Confidence:** high
- **Description:**
  Source-audit P2-01 documents that `resolve_broker_call` and
  `_NO_TRADE_INTENTS` have no live callers; the executor's run loop
  iterates `final_orders` directly and never invokes the helper. The
  source audit explicitly notes the existing tests at
  `test_verb_dispatch.py` are fossil-style — they exercise the
  function's intrinsic logic, not its use anywhere. If the source
  consolidation pass takes the deletion option, these five tests
  (`test_resolve_broker_call_open_returns_buy_to_weight`,
  `test_resolve_broker_call_close_returns_sell_all`,
  `test_resolve_broker_call_hold_returns_none`,
  `test_resolve_broker_call_update_returns_none`,
  `test_resolve_broker_call_trim_returns_sell_call`) must be
  deleted in the same PR — otherwise the import at line 22 will
  fail. The remaining `apply_stance_to_thesis` tests in the same
  file (8 tests) are unaffected; they exercise a helper with live
  callers in both `_run_async_impl` and the after-callback.
- **Suggested action:**
  Conditional delete: if P2-01 disposes via deletion, drop the five
  `resolve_broker_call_*` tests in the same PR; if P2-01 disposes
  by wiring the helper into `_run_async_impl`, the tests stay and
  should be supplemented with an integration test verifying the
  run loop consults the helper.

### P1-03 · T2 parallel-branch defender · `test_executor_stamps_opened_price_on_buy` doubles up `test_buy_writes_thesis_to_state_positions`

- **Location(s):** `tests/integration/test_executor_with_fake_broker.py:67-138` and `tests/unit/executor/test_open_positions_state.py:73-138`
- **Source-audit cross-ref:** (drift surfaced by P1-01)
- **Confidence:** medium
- **Description:**
  These two tests are near-duplicates: both build a FakeBroker with
  a fixed price, fire a BUY with an open-intent stance, and assert
  the bare-key bridge contains the resulting thesis with
  `opened_price=fill_price`. The only differences are filler values
  (`AAPL` at $215.50 in one, AAPL at $200.00 in the other) and
  fixture style (one uses `_make_ctx`, the other uses a `_StubCtx`
  class). The presence of both is a layout artefact of the parallel
  `tests/unit/executor/` and `tests/integration/` trees, not a
  deliberate redundancy. Either test alone would catch the same
  regression.
- **Suggested action:**
  When P1-01 is resolved and the bare-key assertions are reshaped
  to target `user:positions`, collapse to a single canonical test
  under `tests/unit/agents/executor/` and delete the duplicate.

### P1-04 · T5 mock-at-wrong-level · `test_cross_tick_buy_then_sell_produces_trade_log_row` calls `_run_async_impl` directly to simulate a full Runner round-trip

- **Location(s):** `tests/integration/test_executor_with_fake_broker.py:163-334`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` P1-01 (the writer-of-record split this test maps around)
- **Confidence:** medium
- **Description:**
  The test claims to verify a cross-tick BUY→SELL DatabaseSessionService
  round-trip, but it does this by manually calling
  `executor._run_async_impl(ctx)` (bypassing the ADK Runner and
  therefore the `after_agent_callback`), then hand-feeding the
  yielded `Event` to `DatabaseSessionService.append_event`. The
  result is a test that exercises the bare-key bridge persistence
  but never exercises `user:positions` cross-tick. Per test-policy
  §A.5 ("stub at the leaf, not above it"), the analogue here is
  "drive the full Runner; do not bypass the lifecycle". The proper
  shape exists already in `test_state_delta_user_prefix_end_to_end.py`
  (which uses `Runner.run_async`), but that test does not exercise
  the cross-tick *round-trip* — only the within-tick callback
  write. The combination of "we have a true cross-tick test that
  bypasses the callback" + "we have a true callback test that does
  not round-trip" leaves the *interaction* of the two uncovered.
- **Suggested action:**
  Reshape `test_cross_tick_buy_then_sell_produces_trade_log_row` to
  drive `Runner.run_async` for both ticks, sharing the same
  `DatabaseSessionService`. Assert that the SELL tick's
  `apply_stance_to_thesis` sees the prior `user:positions` row
  populated by the BUY tick's after-callback. Until source-fix
  P1-01 lands, the test can keep the bare-key assertion as a
  belt-and-braces; once P1-01 lands, drop the bare-key part.

### P1-05 · T3 only-asserts-completion · `test_executor_logger_exception_does_not_abort_tick` no longer matches the source's loud-log behaviour

- **Location(s):** `tests/unit/agents/test_executor_decision_hook.py:107-136`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` P2-02
- **Confidence:** high
- **Description:**
  The source audit (P2-02) flagged the previous
  `contextlib.suppress(Exception)` as a silent-suppression hazard;
  the source has since been tightened to `try / except Exception:
  logger.warning(..., exc_info=True)` (see `agent.py:303-318` —
  "We now log loudly with the full traceback"). The test still
  only asserts (a) the tick did not raise and (b)
  `state["executions"][0]["status"] == "filled"`. It does not
  assert the warning is emitted via `caplog`, which is exactly the
  shape test-policy §A.7 third bullet requires ("verify the logs
  the code claims to emit actually fire"). Without that assertion,
  a future regression returning to `contextlib.suppress` would
  pass this test green.
- **Suggested action:**
  Strengthen: add `caplog.set_level(logging.WARNING,
  logger="agents.executor.agent")` and `assert any("decision_logger"
  in r.message for r in caplog.records)`. Net result: regression
  to silent suppression fails the test.

### P1-06 · T3 only-asserts-completion · `test_executor_calls_decision_logger_on_fill` asserts call but not state-shape contract

- **Location(s):** `tests/unit/agents/test_executor_decision_hook.py:57-105`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-executor.md` P2-02 (peripheral)
- **Confidence:** medium
- **Description:**
  The test asserts the logger was called once and the call argument
  is a dict containing `"executions"`. It does not assert that the
  snapshot contains the *position thesis* the executor just
  assembled (`"positions"["AAPL"]`), the strategist decision, or
  the `as_of`. Since the DecisionLogger snapshot is the seed corpus
  for RAG when paper/live deploys, a regression where the snapshot
  drops a critical field would pass this test green. Per test-policy
  §E ("Asserting only on counts, never on content"), this is a
  textbook content-assertion gap.
- **Suggested action:**
  Strengthen: assert `"AAPL" in call_arg["positions"]`,
  `call_arg["strategist_decision"]` is non-empty, and
  `call_arg["tick_id"] == "t1"`. Net: a future change that drops
  any of those from the snapshot fails the test.

### P2-01 · T6 wide-scope monkeypatch · `test_snapshotter_uses_as_of` mutates `sys.modules["yfinance"]` with no fixture teardown

- **Location(s):** `tests/unit/backtest/test_wall_clock_leakage.py:128-135`
- **Source-audit cross-ref:** (no direct executor finding — flagged for cross-subsystem layout)
- **Confidence:** medium
- **Description:**
  This is a `test_wall_clock_leakage.py` test, not strictly an
  executor test, but the file shares a `_StubCtx` helper with the
  executor's wall-clock test (`test_executor_closed_at_uses_as_of`)
  and they sit in the same suite. The snapshotter test rebinds
  `sys.modules["yfinance"]` to a fake module with no teardown —
  any subsequent test that imports `yfinance` (including the
  snapshotter's own subsequent runs in the same pytest session)
  inherits the fake. test-policy §A.6 forbids module-level global
  mutation of this shape. I flag this here only because the file
  is in the executor's audit scope; the actual fix belongs to the
  snapshotter audit.
- **Suggested action:**
  Use `monkeypatch.setitem(sys.modules, "yfinance", fake_yf)` so
  pytest restores the original module on teardown. Route to the
  snapshotter audit when filed.

### P2-02 · T3 redundant happy-path assertion · `test_callback_returns_none_no_reprompt` duplicates implicit ADK contract

- **Location(s):** `tests/unit/agents/executor/test_thesis_writer_callback.py:150-157`
- **Source-audit cross-ref:** (none — this is a hygiene observation)
- **Confidence:** low
- **Description:**
  The test asserts the callback returns `None`, which is "Rule 3
  conformance" per the comment. ADK's after_agent_callback
  signature returns `Optional[Content]`; returning `None` is the
  default behaviour of a function whose last statement is a side
  effect. The test will pass unless someone explicitly types
  `return <Content>(...)` — a change that would be picked up by
  the integration test anyway. Filed P2 only because it is hygiene
  noise; if removed the suite loses nothing. The other seven tests
  in the file are all load-bearing.
- **Suggested action:**
  Optional deletion. Not blocking.

### P2-03 · T8 layout / discoverability · parallel executor test directories violate test-policy §B

- **Location(s):** `tests/executor/`, `tests/unit/executor/`, `tests/unit/agents/executor/`, `tests/unit/agents/test_executor_decision_hook.py`
- **Source-audit cross-ref:** (none — pure layout)
- **Confidence:** high
- **Description:**
  Test-policy §B says unit tests live "under `tests/unit/`
  mirroring the source tree" — and gives `tests/unit/agents/news/`
  as the example for `src/agents/news/`. The executor therefore
  has one canonical home: `tests/unit/agents/executor/`. In
  practice the suite is split four ways: (a) `tests/executor/`
  (root-level — predates the policy); (b) `tests/unit/executor/`
  (a second mirror missing the `agents/` segment); (c)
  `tests/unit/agents/executor/` (canonical); (d)
  `tests/unit/agents/test_executor_decision_hook.py` (an executor-
  specific file in the parent `agents/` directory). The four
  parallel locations make discovery slow and create the duplicate
  in P1-03.
- **Suggested action:**
  Consolidate into `tests/unit/agents/executor/`. Specifically:
  move `test_executor_bookkeeping.py` from `tests/executor/`,
  move `test_open_positions_state.py` from `tests/unit/executor/`,
  and move `test_executor_decision_hook.py` from
  `tests/unit/agents/` — then delete the now-empty
  `tests/executor/` and `tests/unit/executor/` directories. The
  integration tests stay where they are.

### P2-04 · T8 layout · `test_executor_closed_at_uses_as_of` is filed under `tests/unit/backtest/` not `tests/unit/agents/executor/`

- **Location(s):** `tests/unit/backtest/test_wall_clock_leakage.py:202-247`
- **Source-audit cross-ref:** (none — layout only)
- **Confidence:** medium
- **Description:**
  The test exercises `ExecutorAgent._run_async_impl` and asserts on
  `TradeLogRow.closed_at` and `holding_period_hours`. It is grouped
  with other writer-side wall-clock tests for thematic reasons but
  the executor subsystem is the actual subject. Discoverability
  suffers: the file does not match any of the `grep -rln
  "ExecutorAgent"` discovery shapes someone auditing the executor
  would run.
- **Suggested action:**
  Either move this single test into
  `tests/unit/agents/executor/test_wall_clock_determinism.py`
  (with a back-link comment to the writer-side suite), or leave it
  and document in the executor subsystem's own conftest that
  cross-cutting determinism tests live in
  `tests/unit/backtest/test_wall_clock_leakage.py`. The
  consolidation pass should pick the simpler option.

### P3-01 · T8 docstring inconsistency · `test_executor_idempotent` has no docstring while every other test in the file does

- **Location(s):** `tests/integration/test_executor_with_fake_broker.py:50-64`
- **Source-audit cross-ref:** (none — cosmetic)
- **Confidence:** high
- **Description:**
  Per the user-global `CLAUDE.md` rule "Every function gets a
  docstring/header comment describing its purpose, parameters, and
  return value" and test-policy §D ("Function docstrings are
  mandatory"), this test should carry a docstring. The neighbouring
  `test_executor_stamps_opened_price_on_buy` and
  `test_cross_tick_buy_then_sell_produces_trade_log_row` have
  multi-paragraph docstrings; this one is bare. Minor.
- **Suggested action:**
  Add a one-paragraph docstring describing the idempotency guard.
  Folded into the P0-03 strengthening pass.

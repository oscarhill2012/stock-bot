# executor module audit — 2026-05-26

Scope: `src/agents/executor/agent.py`, `src/agents/executor/_verb_dispatch.py`,
plus tests under `tests/executor/`, `tests/unit/agents/executor/`,
`tests/unit/executor/`, `tests/unit/agents/test_executor_decision_hook.py`,
`tests/integration/test_executor_with_fake_broker.py`.

Per intent §7.3: bare-key `state["positions"]` inside `_run_async_impl`
is contractually load-bearing for in-tick BUY → SELL ordering. The
executor's own use is NOT flagged. External readers and stale tests
are.

---

## F-executor-001
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `src/agents/executor/agent.py:510-526`
- **Evidence:**
  ```python
  except AssertionError:
      import sys
      import traceback as _tb
      print(
          f"[executor/_executor_thesis_writer_callback] "
          f"apply_stance_to_thesis raised for {ticker!r} "
          ...
          file=sys.stderr,
      )
      continue
  ```
  The after-callback swallows `AssertionError` (e.g. `buy` reaching the
  dispatcher with no fill price — `_verb_dispatch.py:219`), prints to
  stderr via `print()` (not `logger`), and continues the loop. The
  comment says "log loudly with the full traceback" but the call site
  uses `print(file=sys.stderr)`, which bypasses the structured logger,
  is unconditional (won't go through any `caplog`-based test), and
  produces no log record the reporting layer can aggregate (cf. the
  `hallucinated_stance` log key picked up by
  `backtest/reporting.py:1033`).
- **Intent violated:** test-policy §A.7 "Tests must surface silent
  failures loudly"; intent.md §3.2 "silent failures are the recurring
  bug class" memo.
- **Suggested action:** investigate — replace `print` with
  `logger.error(..., exc_info=True)`, and add a `caplog` test that
  asserts the record fires when the BUY thesis-writer path is hit
  with `fill_price=None`.
- **Notes:** A genuine wiring bug (e.g. `executions` payload missing
  `actual_price` so `fill_prices[ticker]` is `None`) would silently
  drop the per-ticker thesis update from `user:positions` while the
  tick reports success.

## F-executor-002
- **Category:** dead-test
- **Severity:** P0
- **Location:** `tests/unit/agents/test_executor_decision_hook.py:78-92`
- **Evidence:**
  ```python
  "stances": [
      {
          "ticker":       "AAPL",
          "intent":       "open",         # invalid verb under four-verb schema
          "weight":       0.10,
          "horizon":      "swing",        # deleted by iter-3
          "rationale":    "test",
          "target_price": 170.0,          # deleted by iter-3
          "stop_price":   130.0,          # deleted by iter-3
          "catalyst":     "test catalyst",# deleted by iter-3
      },
  ],
  ```
  `TickerStance` validators (`stance_schema.py:69,88-90`) declare
  `extra="forbid"` and reject `intent="open"`. The test stance is
  never validated in this test because the executor only deserialises
  stances at the BUY branch via `TickerStance.model_validate(...)` —
  the dispatch only fires on `order.action == "BUY"`, and this test
  expects `BUY` so the validation *would* fire. But the stance lookup
  filters on `intent == "buy"`, so a stance with `intent="open"` is
  silently skipped and the test still passes via the BUY broker-fill
  path. Coverage of the intended "open assembles thesis" assertion is
  zero.
- **Intent violated:** intent §2.3 four-verb vocabulary; test-policy
  §A.7 / §E "Asserting only on counts, never on content".
- **Suggested action:** investigate / delete-and-rewrite — the test
  asserts on `fake_logger.on_executions.assert_called_once()` only;
  it does not check the assembled `state["positions"]["AAPL"]` thesis,
  so the legacy stance shape never trips a failure.
- **Notes:** Pair with F-executor-003 — same legacy shape leaks
  through several executor tests.

## F-executor-003
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/executor/test_executor_bookkeeping.py:40-52`;
  `tests/unit/executor/test_open_positions_state.py:161-170,205-212`;
  `tests/unit/agents/test_executor_decision_hook.py:163-170`
- **Evidence:**
  ```python
  _THESIS: dict = {
      ...
      "horizon":          "swing",
      "target_price":     120.0,
      "stop_price":       90.0,
      ...
      "last_review_note": "",   # field renamed in iter-3
  }
  ```
  These thesis fixtures still carry `horizon`, `target_price`,
  `stop_price`, and `last_review_note` keys. `PositionThesis` now
  forbids those fields (`position_thesis.py:82-91`). The SELL path in
  the executor reads `thesis.get("opened_at")` / `opened_price` /
  `opened_tag` / `rationale` only (`agent.py:221-269`), so the extra
  keys are silently ignored — the tests pass but pretend to exercise
  a schema shape that no longer exists.
- **Intent violated:** §3.1 glossary (`PositionThesis` is the canonical
  shape); test-policy §G evolution rule (delete obsolete fixtures
  alongside code changes).
- **Suggested action:** delete the legacy keys from the fixtures.
- **Notes:** Test bodies themselves assert `"horizon" not in stored`
  etc., so part of the file is iter-3-aware while the local
  `_THESIS` / `existing_thesis` fixtures aren't.

## F-executor-004
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/strategist/context_shim.py:153,229`;
  `src/backtest/decision_logger.py:336-340`
- **Evidence:**
  ```python
  # context_shim.py:153
  positions = state.get("user:positions") or state.get("positions") or {}
  # decision_logger.py:339
  "held_view_at_decision": _coerce(
      (state.get("positions") or {}).get(ticker)
  ),
  ```
  Per intent §7.3 (authoritative): "External readers, however, do NOT
  need the bare key". ContextShim and `decision_logger` run *after*
  the executor's `after_agent_callback` fires, so `user:positions`
  is already authoritative. The fallback chain
  `user:positions or positions` is dead defensive code, and the
  decision logger reads ONLY the bare key — wrong source by
  construction (it should read `user:positions`).
- **Intent violated:** §7.3 (bare key is executor-internal); §3.2
  synonym candidate #3 (bare vs `user:` positions).
- **Suggested action:** consolidate-with-`user:positions` — drop the
  bare-key fallback from `context_shim` and switch
  `decision_logger.py:339` to `state.get("user:positions")`. Leave the
  executor's own use untouched.
- **Notes:** This is the §7.3 follow-up the audit was asked to spot.

## F-executor-005
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `docs/contract-invariants.md §A`
- **Evidence:** §A schema table lists `state["user:positions"]` but no
  row for the bare key. Code labels itself `# Band 4 bare-key BUY→SELL
  bridge` (`executor/agent.py:99,320,384`), and intent §7.3 confirms
  the key is contractual inside the executor.
- **Intent violated:** §7.3 "P2 doc-fix: `docs/contract-invariants.md
  §A` should add a row documenting `state["positions"]` as an
  executor-internal tick-scoped working copy of `user:positions`."
- **Suggested action:** investigate — add §A row: field
  `state["positions"]`, owner `Executor (_run_async_impl)`, lifetime
  `tick-scoped` (working copy seeded from `user:positions` at Phase 2),
  notes "BUY → SELL intra-tick bridge; executor-internal".
- **Notes:** Audit-only finding; the doc owner does the edit.

## F-executor-006
- **Category:** silent-failure
- **Severity:** P2
- **Location:** `src/agents/executor/agent.py:447-457`
- **Evidence:**
  ```python
  for row in state.get("executions", []):
      if not row:
          continue
      ticker = (
          (row.get("order") or {}).get("ticker") or
          (row.get("stance") or {}).get("ticker") or
          ""
      )
      if ticker:
          fill_prices[ticker] = row.get("fill_price") or row.get("actual_price")
  ```
  Two redundant lookup paths: `row["stance"]["ticker"]` is never
  written by `_run_async_impl` (Executions only carry `order`, not
  `stance` — see `state.py` Execution shape). And the read pair
  `row.get("fill_price") or row.get("actual_price")` accepts both
  spellings; current writer only emits `actual_price` (line 109). A
  `status="rejected"` row has no `actual_price` and silently lands in
  `fill_prices[ticker] = None`, which then trips the BUY assertion
  in `_verb_dispatch.py:219` via the AssertionError swallow noted in
  F-executor-001.
- **Intent violated:** §3.1 glossary (`Execution` shape); test-policy
  §A.7.
- **Suggested action:** investigate — narrow to the one writer shape
  (`row["order"]["ticker"]`, `row["actual_price"]`), filter
  rejected/no-price rows out of `fill_prices` rather than mapping them
  to `None`. Add a regression test forcing one rejected + one filled
  in the same `executions` list.
- **Notes:** Couples directly to F-executor-001.

## F-executor-007
- **Category:** over-abstraction
- **Severity:** P2
- **Location:** `src/agents/executor/_verb_dispatch.py:84-141`
  (`resolve_broker_call`)
- **Evidence:** `resolve_broker_call` is defined and tested
  (`test_verb_dispatch.py:75-128`), and its docstring says
  "this helper exists primarily to gate whether a broker call is
  needed". But no production code path calls it — `_run_async_impl`
  reads `state["final_orders"]` directly (already gated by the risk
  gate), and the after-callback dispatches via `apply_stance_to_thesis`.
  ```bash
  $ grep -rn 'resolve_broker_call' src/ | grep -v _verb_dispatch.py
  src/agents/risk_gate/agent.py:72:        # skip broker dispatch for them (``resolve_broker_call`` returns
  ```
  Sole hit is a comment in `risk_gate`. Zero call sites.
- **Intent violated:** n/a.
- **Suggested action:** delete `resolve_broker_call` and the four
  tests in `test_verb_dispatch.py::test_resolve_broker_call_*`.
- **Notes:** If the verb-gating logic is retained elsewhere it should
  be a private helper on the risk gate, not in the executor package.

## F-executor-008
- **Category:** test-gap
- **Severity:** P2
- **Location:** `tests/integration/test_executor_with_fake_broker.py::test_executor_idempotent`
- **Evidence:** The idempotency guard at `agent.py:79` is exercised by
  exactly one test (`test_executor_idempotent`) which asserts
  `"executions" not in state`. There is no assertion that the
  `state_delta` event is suppressed, that `state["positions"]` is
  unchanged, or that the `after_agent_callback` is not re-firing —
  re-running the after-callback against the prior tick's state could
  silently double-write `user:positions`.
- **Intent violated:** §2.3 idempotency invariant; test-policy §A.7.
- **Suggested action:** investigate — add: (1) a test exercising the
  full ADK runner lifecycle to confirm the after-callback is also
  guarded (it currently isn't — only `_run_async_impl` checks
  `last_executed_tick_id`); (2) an assertion that no
  `state_delta` event is yielded on the second invocation.
- **Notes:** The guard at line 79 returns *before* yielding, but the
  after-callback `_executor_thesis_writer_callback` has no such
  guard. On an ADK retry, the callback would re-run against the prior
  tick's `strategist_decision` and clobber `user:positions`.

## F-executor-009
- **Category:** dedupe-candidate
- **Severity:** P3
- **Location:** `src/agents/executor/agent.py:317-393`
- **Evidence:** The executor writes the same trio to state twice:
  ```python
  # lines 319-321: direct dict assignment for same-tick consumers
  state["executions"]             = executions
  state["positions"]              = positions
  state["last_executed_tick_id"]  = tick_id
  # lines 381-393: state_delta event for cross-tick persistence
  delta = {
      "executions":            executions,
      "last_executed_tick_id": tick_id,
      "positions":             positions,
  }
  yield Event(..., actions=EventActions(state_delta=delta))
  ```
  The direct writes are documented as "visible to any later agent in
  *this* tick (same object reference)" but `executions` and
  `last_executed_tick_id` are tick-scoped per §A — no in-tick consumer
  needs them before the event lands. Direct mutation of `state["positions"]`
  is the only one that matters for the BUY→SELL bridge (already
  documented at line 320).
- **Intent violated:** §5.3 (acknowledged `last_executed_tick_id` paired
  direct write is "defensive belt-and-braces"); intent §3.2 synonym
  candidate #11.
- **Suggested action:** investigate — drop the direct writes for
  `executions` and `last_executed_tick_id` once §5.3 is actioned;
  retain the `state["positions"]` direct write (bridge requirement).
- **Notes:** P3 because self-documented technical debt.

## F-executor-010
- **Category:** dead-test
- **Severity:** P3
- **Location:** `tests/integration/test_executor_with_fake_broker.py:138-153`
  (`test_executor_rejection_continues`)
- **Evidence:** Test asserts only `executions[0]["status"] == "rejected"`.
  No assertion that the `error` field is populated, no `caplog` check
  that the rejection is logged, no assertion on
  `state["positions"]` being unchanged. The whole BrokerRejection
  exception branch is `except BrokerRejection as e: ... Execution(...,
  status="rejected", error=str(e))` (`agent.py:308-313`) — a typo or
  swallow of the error message would not be caught.
- **Intent violated:** test-policy §A.7 and §E "Asserting only on
  counts, never on content".
- **Suggested action:** investigate — augment assertions to include
  `error` content and that the thesis book is untouched.

## F-executor-011
- **Category:** dedupe-candidate
- **Severity:** P3
- **Location:** `tests/executor/test_executor_bookkeeping.py` vs
  `tests/unit/executor/test_open_positions_state.py`
- **Evidence:** Both files cover the BUY-stamps-bridge / SELL-removes-
  bridge / trade-log-row-on-close territory using the same `_StubCtx`
  / `_run` pattern, the same `FakeBroker(_TICKER, _OPEN_PRICE)`
  fixtures, and the same `state["positions"]` bare-key assertions. Of
  note:
  - `test_buy_stance_populates_bare_key_bridge` (bookkeeping)
  - `test_buy_writes_thesis_to_state_positions` (open_positions_state)
  - `test_full_exit_writes_one_trade_log_row_and_deletes` (bookkeeping)
  - `test_sell_writes_tick_id_fks_to_trade_log` (open_positions_state)
- **Intent violated:** test-policy §B taxonomy / locations.
- **Suggested action:** consolidate-with the `tests/unit/executor/`
  layout (or whichever is canonical) — `tests/executor/` is a non-
  taxonomic top-level directory.
- **Notes:** Two side-by-side directories for the same component is a
  structural smell. Pick one and migrate.

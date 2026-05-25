# T-F12 — Completion-only assertion rewrites

**Wave:** 4 (parallel)
**Pairs source-audit fix:** none directly (this is the test-rewrite
half of Theme E — "It didn't raise, therefore it works"). The source
sides that lock in the new contracts are owned by sibling specs
(T-F01 for the surfacing primitives the new caplog asserts target,
T-F03 / T-F06 for the lifecycle/executor source changes the
strengthened CLI tests pin).
**Branch:** `fix/T-F12-completion-only-rewrites`
**Depends on:** T-F01a (the surfacing primitive — strengthened
end-to-end tests assert against it via `caplog`); T-F03 (lifecycle
ADK-tables coverage); T-F06 (executor `"positions"` → `user:positions`).
**Estimated diff size:** small / medium

## Scope

Theme E in the test-audit catalogues every test that asserts only on
*completion* — `rc == 0`, "didn't raise", `len(...) == 1`, key
presence — without checking positive output content. Per
`test-policy.md` §A.7 and §E and the user memory
`feedback_silent_failures_loud_tests`, this is the most-recurring
test pathology in the project. Most Theme-E rewrites are absorbed
into the source-fix PRs that touch the same files (T-F01, T-F03,
T-F05, T-F06). This spec sweeps up the residual five sites where
the rewrite is **not** load-bearing for any source change but is
still policy-required, and which are large enough or attractor-shaped
enough to deserve their own PR.

### In scope

The five residual sites:

1. **Risk-gate surfacing tests (paired with T-F03 / sibling).** Two
   undefended P0s per test-audit `risk-gate.md`:
   - P0-01 — no test asserts RiskGate raises (or surfaces) when
     `strategist_decision` is falsy / missing. Add
     `tests/unit/agents/risk_gate/test_agent.py::test_risk_gate_raises_when_strategist_decision_missing`
     and a sibling `..._when_strategist_decision_empty_dict`. Drive
     `_run_async_impl` with `state = {}`, `state =
     {"strategist_decision": None}`, `state = {"strategist_decision":
     {}}`. Depending on which side the source-fix lands (raise vs
     `branch_failed` + empty `final_orders`), assert either
     `pytest.raises(StrategistContractViolation)` or
     `caplog.records` carrying the warning + a one-event yield with
     `final_orders=[]`. Pair every variant with
     `caplog.set_level(WARNING)`.
   - P0-02 — no test asserts the closing-without-`close_reason`
     lifecycle raise at `agent.py:101-108`. Add a unit test:
     construct a `FakeBroker` holding an open AAPL position above
     `MIN_HELD_WEIGHT`, feed a `StrategistDecision` with
     `target_weights={"AAPL": 0.0}` and `close_reasons={}`, drive
     `_run_async_impl`, assert
     `pytest.raises(StrategistContractViolation, match="Closing")`.
     Pair with a happy-path counterpart that supplies
     `close_reasons={"AAPL": "stop_loss"}`.

   Note: the source-side for P0-01 (the raise / surface change at
   `src/agents/risk_gate/agent.py:45-47`) is **owned by this spec**
   (it does not have a dedicated source-fix sibling). Treat as a
   self-contained source+test pair: write the test against the
   intended new contract, land the source change, watch the test
   flip from red to green.

2. **Orchestrator end-to-end `branch_failed` caplog guard
   strengthening** — already specified in T-F02 (per test-audit
   `orchestrator.md` P0-04). **Cross-reference only**: T-F02 owns it
   to keep this spec from duplicating; not closed here.

3. **Lifecycle CLI happy-path strengthening** per test-audit
   `lifecycle.md` P0-03 and P0-04:
   - `tests/unit/test_initialise_cli.py:37`
     (`test_main_calls_initialise`) — add positive assertions after
     `rc == 0`: assert the anchor row exists with `tick_id == "init"`,
     assert captured stdout contains the "Wrote anchor snapshot" line
     via `capsys`, and add a sibling test that gives the CLI a
     malformed watchlist JSON and asserts `rc == 1` with the
     appropriate stderr message.
   - `tests/unit/test_hard_reset_cli.py:35`
     (`test_yes_flag_skips_prompt`) — after `cli.main([...])`,
     re-open the live DB and assert every table the CLI claims to
     truncate is empty; assert `(archive_dir / "*.db").exists()`;
     assert the meta JSON exists and parses. Reuse the assertions
     from `test_archive_creates_file_and_truncates_live`.

   **Coordinate with T-F03.** If T-F03 has already strengthened
   these (because they sit next door to the ADK-tables coverage
   work), drop them here. Otherwise own them here. Decide by reading
   `docs/Phase11-project-audit/fix-plan/T-F03-lifecycle-adk-tables.md` at implementation
   time.

4. **Executor idempotency test strengthening** per test-audit
   `executor.md` P0-03:
   - `tests/integration/test_executor_with_fake_broker.py:51-64`
     (`test_executor_idempotent`) — strengthen the assertion from
     `assert "executions" not in state` to:
     - `assert len(broker._orders) == 0` (or the equivalent
       FakeBroker counter — confirm the attribute name at
       implementation time).
     - `events = [ev async for ev in agent._run_async_impl(ctx)];
       assert events == []`.
     - Add a one-paragraph docstring per test-audit `executor.md`
       P3-01.

   **Coordinate with T-F06.** If T-F06 has already strengthened
   this test as part of the `"positions"` → `user:positions`
   rewrite, drop it here. Otherwise own.

5. **Backtest driver completion-only weak spots** per test-audit
   `backtest.md` P1-02:
   - `tests/integration/backtest/test_driver_one_tick.py` — add
     `assert not any("branch_failed" in r.message for r in caplog.records)`,
     `assert state["strategist_decision"]["stances"]`, and
     `assert is_no_data is False` for at least one analyst verdict
     in `state["temp:ticker_evidence_objects"]`. This is a load-
     bearing single-tick test today asserted only on
     `len(traces) == 1`.

### Out of scope

- Every Theme-E rewrite already owned by another wave-4 spec:
  - The T-F02 end-to-end caplog guard (`orchestrator.md` test P0-04).
  - The T-F05 strategist v2 smoke strengthening
    (`strategist.md` test P0-03).
  - The T-F05 `test_no_op_without_db_session` `events == []`
    addition (`strategist.md` test P2-01).
- The T-F01 silent-failure inverts (whole spec).
- Layout sweep test relocations — T-F10.
- Marker discipline retrofits — T-F11.
- Wide-scope monkeypatch reshapes (test-audit `backtest.md` P1-03
  / `strategist.md` P2-03 etc.) — not Theme-E shaped; defer.
- The four extractor `test_extracts_required_keys` content-guard
  strengthening — owned by T-F09.
- Risk-gate weak-assertion clamp strengthening (`risk-gate.md` P2-01,
  P2-02, P2-03) — defer as P2 hygiene.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `risk-gate.md` test P0-01 + matching source change | `src/agents/risk_gate/agent.py:45-47`; `tests/unit/agents/risk_gate/test_agent.py` (new) | Surface falsy `strategist_decision` |
| `risk-gate.md` test P0-02 | `tests/unit/agents/risk_gate/test_agent.py` (new) | Closing-without-`close_reason` raise regression test |
| `lifecycle.md` test P0-03 | `tests/unit/test_initialise_cli.py:37` | Strengthen beyond `rc == 0` |
| `lifecycle.md` test P0-04 | `tests/unit/test_hard_reset_cli.py:35` | Strengthen beyond stdout substring |
| `executor.md` test P0-03 | `tests/integration/test_executor_with_fake_broker.py:51-64` | Strengthen idempotency to assert no broker call |
| `executor.md` test P3-01 | same file | Add docstring |
| `backtest.md` test P1-02 | `tests/integration/backtest/test_driver_one_tick.py` | Strengthen beyond `len(traces) == 1` |

(`lifecycle.md` test P0-03 / P0-04 and `executor.md` test P0-03 are
*contingent* — drop from this spec if T-F03 / T-F06 already
incorporated them.)

## Implementation steps

1. **Pre-flight: read sibling specs.** Open
   `docs/Phase11-project-audit/fix-plan/T-F03-lifecycle-adk-tables.md` and
   `docs/Phase11-project-audit/fix-plan/T-F06-executor-state-keys.md` (or its actual
   filename when drafted). Strike from this spec any rewrite the
   sibling already covers. Commit the spec edit before starting
   implementation if scope shrinks.
2. **Risk-gate (paired source + test).** This is the biggest chunk
   and the only true source+test pair in this spec:
   - Source: flip `src/agents/risk_gate/agent.py:45-47` from silent
     return to either raise (`StrategistContractViolation`) or
     `emit_branch_failed` + single-event yield with `final_orders=[]`.
     Decision criterion: if a legitimate path exists where RiskGate
     receives an empty `strategist_decision` (e.g. seeded cold-start
     state where the strategist branch has not yet emitted), pick
     the `branch_failed` + empty path; otherwise raise. Pick raise
     by default — the test-audit's framing ("strategist branch
     ALWAYS runs in production") supports raise.
   - Test: write the two new unit tests in
     `tests/unit/agents/risk_gate/test_agent.py` (the file is moved
     into this folder by T-F10).
3. **Lifecycle CLI strengthening.** Source-side untouched (these
   are pure test strengthenings on the existing CLI behaviour).
   - Add the anchor-row + stdout assertions to
     `test_main_calls_initialise`.
   - Add the sibling malformed-watchlist test.
   - Add the post-`cli.main` table-empty + archive-file + meta-JSON
     assertions to `test_yes_flag_skips_prompt`.
4. **Executor idempotency strengthening.** Confirm the FakeBroker
   call-counter attribute name (`_orders` per the test-audit; verify
   at the file). Add the `len(broker._orders) == 0` + `events == []`
   assertions and the docstring.
5. **Backtest driver one-tick strengthening.** Add the three new
   assertions (`branch_failed` absence, `stances` non-empty,
   `is_no_data is False` on at least one verdict).
6. **Run full `pytest tests/`**. Update
   `graphify-out/graph_delta.md` with the new
   `tests/unit/agents/risk_gate/test_agent.py` file.

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in
  commit body) — minus any that turned out to be in T-F03 / T-F06's
  scope (record this in the commit body).
- [ ] The risk-gate source change passes the new tests; if the
  disposition was "raise", both tests assert `pytest.raises`; if
  "branch_failed", both assert on `caplog` + the one-event yield.
- [ ] Graphify delta entry appended.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
```

## Risks and rollbacks

- **Risk: the risk-gate raise breaks the backtest smoke** if the
  baseline window has any tick where `strategist_decision` is
  legitimately absent (e.g. before the strategist branch wires in).
  Mitigation: empirical check via the backtest smoke command
  before committing; if the smoke fails, switch the disposition to
  `branch_failed` + empty-orders yield and rewrite the tests.
- **Risk: the lifecycle CLI strengthening duplicates work T-F03 did
  in a different shape.** Mitigation: the pre-flight read in step 1
  catches this; the commit body records the de-duplication.
- **Risk: the executor `_orders` attribute name is wrong** (test-audit
  cited it, but the source file is the source of truth). Mitigation:
  read `src/broker/fake.py` at implementation time and use the
  actual attribute.
- **Rollback:** feature branch discardable. The five sub-changes
  are independent; revert any one without disturbing the others.

## Subagent dispatch prompt sketch

> Implement T-F12 (completion-only assertion rewrites) per
> `docs/Phase11-project-audit/fix-plan/T-F12-completion-only-rewrites.md`. Context:
> `docs/Phase11-project-audit/test-audit/risk-gate.md` (P0-01, P0-02),
> `docs/Phase11-project-audit/test-audit/lifecycle.md` (P0-03, P0-04),
> `docs/Phase11-project-audit/test-audit/executor.md` (P0-03, P3-01),
> `docs/Phase11-project-audit/test-audit/backtest.md` (P1-02);
> `docs/test-policy.md` §A.7 + §E;
> sibling specs
> `docs/Phase11-project-audit/fix-plan/T-F03-lifecycle-adk-tables.md`,
> `docs/Phase11-project-audit/fix-plan/T-F06-executor-state-keys.md`
> for the pre-flight scope strike. The risk-gate source+test pair is
> the load-bearing chunk; the other four are test-only
> strengthenings. British English throughout.

# T-F06 — Executor `"positions"` → canonical `user:positions`

**Wave:** 3
**Pairs source-audit fix:** F8
**Branch:** `fix/T-F06-executor-positions-key`
**Depends on:** T-F10 (layout sweep — provides
`tests/unit/agents/executor/` consolidation)
**Estimated diff size:** medium

## Scope

The executor maintains two parallel position books inside the same tick:
the canonical `user:positions` (written exclusively by
`_executor_thesis_writer_callback` per `contract-invariants.md`
§A row + §C-Rule 1 Spec B clarification) and a bare-key `"positions"`
shadow (written directly into session state and propagated via
`state_delta`, so it is durable in `DatabaseSessionService` — not
merely an in-tick handoff). Two external readers consume the bare
key (`src/agents/strategist/context_shim.py:125` and
`src/backtest/decision_logger.py:335`), so the divergence is
load-bearing for cross-tick correctness — one swallowed exception
between writer and reader and the two books drift. This PR removes
the bare-key write end-to-end, switches the two external readers to
`user:positions`, and reshapes the six tests across three parallel
directories that currently assert positively on the bare key.

Also bundled in this PR are two adjacent silent-degradation
surfacings flagged by the executor source audit (P1-02 BUY-without-
matching-stance, P1-03 fill-price OR-chain dead-key fallback) plus
the dead-`resolve_broker_call` decision (P2-01). The bundling is
deliberate: the same executor file is touched, the same test files
are reshaped, and splitting would force two PRs through the same
merge-conflict surface.

### In scope

- **Source — `src/agents/executor/agent.py` (P1-01 bare-key removal):**
  - Drop the bare-key `"positions"` write path entirely. Affected
    sites: `:94` (read), `:179` (BUY write into bridge), `:259`
    (SELL del from bridge), `:276` (state mutation),
    `:317-324` (state_delta propagation). After the change, the
    executor must write `user:positions` **exclusively** via the
    auto-yielded delta-tracked pattern in
    `_executor_thesis_writer_callback` (per `contract-invariants.md`
    §C-Rule 1's Spec B clarification — the canonical mechanism for
    cross-tick state).
  - Within-tick BUY→SELL coordination (the original justification
    for the bare-key bridge — see comment at `:86-93` and `:317`):
    if any in-tick coordination is still needed, write to a
    `temp:positions_pending` scratch key (Rule-8 observability prefix
    — not durable, not in §A) instead of the bare `positions` key.
    But first verify the coordination is actually still needed —
    the audit notes the after-callback iterates `decision.stances`
    rather than `state["final_orders"]`, so the BUY→SELL bridge may
    be vestigial. **Recommend: try removing the bridge entirely
    first; reinstate as `temp:positions_pending` only if a test
    fails that exercises real same-tick BUY+SELL on the same
    ticker.**
  - Clean up the Band-history comment cluster at `:86-93, 116-123,
    165-168, 279-287, 309-317` per source P2-03 (consolidate to a
    single block at the top of `_run_async_impl`). This is incidental
    to the bare-key removal — same lines, same diff hunk.
- **Source — `src/agents/strategist/context_shim.py:121-125` (P1-01 reader 1):**
  - Drop the fall-back read of bare `state["positions"]`. Read
    `state["user:positions"]` exclusively. Remove the stale comment
    claiming `state["positions"]` is "never written post-Plan-1"
    (also flagged as P3-01 cross-subsystem drift).
- **Source — `src/backtest/decision_logger.py:335` (P1-01 reader 2):**
  - Replace the bare-key read with `state["user:positions"]`. The
    decision logger is a Rule-8 observability writer; switching to
    the canonical key has no contract-bearing side effects.
- **Source — `src/agents/executor/agent.py` (P1-02 BUY-without-stance surfacing):**
  - Lines `:124-179` (BUY branch). When the search for the unique
    `intent="open"` stance returns `open_stance is None`, raise (or
    `logger.warning`) loudly. The case "stance verb is `add`" is
    legitimate and reaches the after-callback fine; only "no stance
    at all for this BUY ticker" should be loud. **Recommend: raise**
    — this is a contract violation upstream of the executor.
- **Source — `src/agents/executor/agent.py` (P1-03 fill-price OR-chain):**
  - Lines `:379-390` (`_executor_thesis_writer_callback`). Drop the
    dead-key fallbacks: read solely `row["order"]["ticker"]` and
    `row["actual_price"]`. The `Execution` Pydantic model
    (`src/orchestrator/state.py:46-55`) doesn't carry `stance` or
    `fill_price` keys — they're for no live producer. If the
    `Execution` shape drifts, fail loudly at the lookup site
    (`KeyError`) rather than coerce to `""` / `None` and trigger
    the swallowed `AssertionError` further down at `:431-447`.
  - Pair the narrowed lookup with a narrowed `except` at `:431-447`:
    the surrounding `except (AssertionError, ValueError)` catches
    everything from `apply_stance_to_thesis`; once the dead-key
    fallback is gone, the catch can be removed or narrowed to a
    specific drift-shape (subagent decides — but err on the side
    of "raise" given the silent-failures policy).
- **Source — `src/agents/executor/_verb_dispatch.py` (P2-01 dead helper):**
  - Default: **delete** `resolve_broker_call` and `_NO_TRADE_INTENTS`
    (lines `:23, 26-84`). The executor's run loop iterates
    `final_orders` already pre-filtered by risk_gate's
    `_NO_RISK_GATE_INTENTS` parallel constant. The function is
    fossil code — its only callers are the
    `tests/unit/agents/executor/test_verb_dispatch.py` tests of its
    intrinsic logic (test P1-02 — five tests to delete).
  - Cross-subsystem follow-up: remove stale comment references in
    `src/agents/risk_gate/agent.py:19, 59` mentioning
    `resolve_broker_call` (executor source P3-01 item 4).
  - **Override path:** if the subagent surfaces a real caller during
    implementation (e.g. a script that's not in graphify), file an
    inline note and leave the helper in place — but bias hard toward
    deletion.
- **Source — global grep for bare-`"positions"` readers:**
  - After landing the two known readers above, grep `src/` for any
    other bare-key reader that might exist:
    `grep -rn '"positions"' src/ --include="*.py" | grep -v 'user:positions'`.
    Patch any holdouts in this same PR. (The audit identifies only
    the two; verify by greppage in case the source audit missed
    one.)
- **Tests — reshape six tests across three parallel directories
  (executor test P1-01):**
  - The post-T-F10 canonical home is `tests/unit/agents/executor/`.
    The six tests currently sit at:
    `tests/executor/test_executor_bookkeeping.py:130, 133, 176, 179`
    (`test_trim_preserves_position_thesis`,
    `test_full_exit_writes_one_trade_log_row_and_deletes`);
    `tests/integration/test_executor_with_fake_broker.py:121, 129,
    248-257, 327-332`
    (`test_executor_stamps_opened_price_on_buy`,
    `test_cross_tick_buy_then_sell_produces_trade_log_row`);
    `tests/unit/executor/test_open_positions_state.py:125, 136,
    184-188`
    (`test_buy_writes_thesis_to_state_positions`,
    `test_sell_removes_ticker_from_state_positions`).
  - For each: replace positive bare-key assertions
    (`delta["positions"]["AAPL"] == ...`) with assertions on
    `state["user:positions"]` after wiring through a real ADK
    Runner (or a stub that fires the after-callback). Delete the
    negative `"user:positions" not in delta` assertions — they
    were correct under the bare-key regime and meaningless after
    the source change.
  - Where two tests are near-duplicates (e.g.
    `test_executor_stamps_opened_price_on_buy` ⇄
    `test_buy_writes_thesis_to_state_positions`, executor test P1-03
    duplicate-pair), collapse to a single canonical test under
    `tests/unit/agents/executor/` and delete the duplicate.
- **Tests — delete five `resolve_broker_call_*` tests (P1-02):**
  - If source P2-01 deletes the helpers (default path), delete
    these five tests from
    `tests/unit/agents/executor/test_verb_dispatch.py:76-131, 313-330`:
    `test_resolve_broker_call_open_returns_buy_to_weight`,
    `test_resolve_broker_call_close_returns_sell_all`,
    `test_resolve_broker_call_hold_returns_none`,
    `test_resolve_broker_call_update_returns_none`,
    `test_resolve_broker_call_trim_returns_sell_call`.
  - The remaining `apply_stance_to_thesis` tests in the same file
    (8 tests) are unaffected — leave them.
- **Tests — new — BUY-without-matching-stance surfacing (executor test P0-01):**
  - Add `test_buy_without_matching_open_stance_surfaces_loudly` to
    `tests/unit/agents/executor/test_open_positions_state.py` (post-
    T-F10 consolidation). Drive a BUY order whose ticker is absent
    from `strategist_decision.stances`. Assert the executor either
    raises (preferred per the source-fix shape) or emits a
    `logger.warning` line via `caplog`. Closes test P0-01.
- **Tests — new — fill-price OR-chain dead-key surfacing (executor test P0-02):**
  - Add two tests to
    `tests/unit/agents/executor/test_thesis_writer_callback.py`:
    (a) `test_callback_swallow_branch_does_not_fire_on_happy_path` —
    use `capsys` to assert the stderr print in the swallowed
    `AssertionError` catch is absent on every happy-path scenario.
    (b) `test_callback_execution_missing_order_key_fails_loudly` —
    construct an Execution row that omits the `order` key entirely;
    assert the callback raises (post-source-fix), rather than
    coercing to `fill_price=None`.
- **Tests — strengthen executor idempotency (executor test P0-03):**
  - Edit `tests/integration/test_executor_with_fake_broker.py:51-64`
    (`test_executor_idempotent`). Beyond `assert "executions" not in
    state`, assert `len(broker._orders) == 0` (or the equivalent
    FakeBroker call counter) and assert the event stream is empty:
    `events = [ev async for ev in agent._run_async_impl(ctx)];
    assert events == []`. Add the missing docstring (executor test
    P3-01).

### Out of scope

- Layout consolidation of the four parallel executor test
  directories (executor test P2-03) — owned by T-F10. This PR
  assumes T-F10 has landed and writes against the consolidated
  `tests/unit/agents/executor/` layout.
- Executor source P2-02 (`contextlib.suppress(Exception)` around
  `decision_logger.on_executions`) — already tightened to
  `logger.warning(..., exc_info=True)` per the audit cross-ref;
  the test-side strengthening (executor test P1-05 — assert
  `caplog` warning fires) is light enough to fold in here if the
  subagent has cycles, but otherwise defer.
- Executor test P1-04 (`test_cross_tick_buy_then_sell_produces_trade_log_row`
  bypasses `Runner.run_async`) — refactor to drive the Runner is
  larger scope; defer unless the bare-key removal forces it.
- Executor test P1-06 (`test_executor_calls_decision_logger_on_fill`
  content-assertion gap) — defer.
- Executor source P3-01 cross-subsystem references —
  (1) `docs/contract-invariants.md` `thesis_revision` ⇄ `thesis`
  drift,
  (2) `src/agents/strategist/schema.py:138-139` MemoryWriter
  ownership claim,
  (3) `src/agents/strategist/context_shim.py:122-124` "never
  written post-Plan-1" comment (the comment that becomes false
  this PR — patch it in-pass per the user memory
  `feedback_co_planned_specs_trust_each_other`),
  (4) `src/agents/risk_gate/agent.py:19, 59` stale
  `resolve_broker_call` reference (patch in-pass per the same
  memory). Items 1 and 2 belong to the contract-doc patch PR and a
  strategist PR respectively.
- Executor source P3-02 (`build_executor` factory inline-or-keep
  decision) — defer.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `agents-executor.md` source P1-01 | `src/agents/executor/agent.py:94,179,259,276,317-324`; `src/agents/strategist/context_shim.py:125`; `src/backtest/decision_logger.py:335` | Remove bare-key `"positions"` write path; switch readers to `user:positions`; consolidate Band-history comments. |
| `agents-executor.md` source P1-02 | `src/agents/executor/agent.py:124-179` | Raise (or loud-log) on BUY-without-matching-open-stance. |
| `agents-executor.md` source P1-03 | `src/agents/executor/agent.py:379-390,431-447` | Drop fill-price OR-chain dead-key fallbacks; narrow swallow catch. |
| `agents-executor.md` source P2-01 | `src/agents/executor/_verb_dispatch.py:23,26-84` | Delete `resolve_broker_call` + `_NO_TRADE_INTENTS`; patch stale risk_gate comments. |
| `executor.md` test P0-01 | `tests/unit/agents/executor/test_open_positions_state.py` | Add BUY-without-matching-stance surfacing test. |
| `executor.md` test P0-02 | `tests/unit/agents/executor/test_thesis_writer_callback.py` | Add happy-path-no-swallow + missing-order-key surfacing tests. |
| `executor.md` test P0-03 | `tests/integration/test_executor_with_fake_broker.py:51-64` | Strengthen idempotency assertion past completion-only. |
| `executor.md` test P1-01 | 6 tests across `tests/executor/`, `tests/unit/executor/`, `tests/integration/` | Reshape bare-key `"positions"` assertions to `state["user:positions"]`. |
| `executor.md` test P1-02 | `tests/unit/agents/executor/test_verb_dispatch.py:76-131,313-330` | Delete 5 `resolve_broker_call_*` tests in lock-step with source P2-01 deletion. |
| `executor.md` test P1-03 | `tests/integration/test_executor_with_fake_broker.py` ⇄ `tests/unit/executor/test_open_positions_state.py` | Collapse near-duplicate BUY-thesis test pair to a single canonical home. |
| `executor.md` test P1-04 | (cross-tick BUY→SELL via Runner) | Out of scope; flagged in spec. |
| `executor.md` test P1-05 | `tests/unit/agents/test_executor_decision_hook.py:107-136` | Optional: add `caplog` WARNING assertion if cycles permit. |
| `executor.md` test P1-06 | (decision logger content assertions) | Out of scope; flagged in spec. |

## Implementation steps

1. **Pre-flight (read-only):** confirm T-F10 has landed and
   `tests/unit/agents/executor/` is the consolidated home for the
   six bare-key-asserting tests. If T-F10 hasn't merged, abort and
   flag to the dispatcher.
2. **Read the two audit reports in full:**
   `docs/Phase11-project-audit/source-audit/agents-executor.md` and
   `docs/Phase11-project-audit/test-audit/executor.md`.
3. **Grep for all bare-`"positions"` reader sites:**
   `grep -rn '"positions"' src/ --include="*.py" | grep -v
   'user:positions'`. Confirm the audit's two known readers
   (`context_shim.py:125`, `decision_logger.py:335`) plus the
   executor itself are the entire set. List any extras inline and
   either patch in-pass or escalate.
4. **Source — drop the bare-key write path in the executor.**
   - Edit `src/agents/executor/agent.py` at lines `:94, :179, :259,
     :276, :317-324`. Remove all bare-key `"positions"` writes from
     the state_delta payload and from direct state mutation. Verify
     the after-callback `_executor_thesis_writer_callback` still
     writes `user:positions` via the auto-yielded delta-tracked
     pattern — that path is the canonical writer-of-record and is
     unchanged.
   - Same edit: verify if any same-tick BUY→SELL coordination
     actually breaks. Run the tests; if a real same-tick BUY+SELL
     scenario surfaces (likely
     `test_cross_tick_buy_then_sell_produces_trade_log_row` or
     similar), reinstate the in-tick coordination via
     `temp:positions_pending` (Rule-8 observability prefix), not
     `positions`. Document the decision in the executor docstring.
   - Same edit: consolidate the five Band-history comment clusters
     into a single block at the top of `_run_async_impl`.
5. **Source — switch the two external readers.**
   - `src/agents/strategist/context_shim.py:121-125`: read
     `state["user:positions"]` exclusively. Update the surrounding
     comment block (the "never written post-Plan-1" line — also
     stale per executor source P3-01).
   - `src/backtest/decision_logger.py:335`: read
     `state["user:positions"]`.
6. **Source — BUY-without-matching-stance surfacing.**
   - `src/agents/executor/agent.py:124-179`: when `open_stance is
     None` for a BUY order, raise `RuntimeError` (preferred) with a
     message naming the offending ticker. Alternative: `logger.warning`
     plus a counter. Recommend raise.
7. **Source — fill-price OR-chain narrowing.**
   - `src/agents/executor/agent.py:379-390`: read solely
     `row["order"]["ticker"]` and `row["actual_price"]`. Drop the
     `or row["stance"]["ticker"]` / `or row["fill_price"]`
     fallbacks.
   - `:431-447`: narrow or remove the `except (AssertionError,
     ValueError)` swallow. The recommended shape: let the exception
     propagate; the upstream pipeline guard handles mid-tick blow-ups.
8. **Source — delete `resolve_broker_call` (default path).**
   - Edit `src/agents/executor/_verb_dispatch.py`: delete
     `resolve_broker_call`, `_NO_TRADE_INTENTS`, and any helpers used
     only by them. Re-export list / `__all__` update if applicable.
   - Edit `src/agents/risk_gate/agent.py:19, 59`: remove the stale
     comments referencing `resolve_broker_call`.
9. **Tests — reshape the six bare-key tests** to assert on
   `state["user:positions"]` instead.
   - For each test in the post-T-F10 location, drive the executor
     through a real ADK Runner (or a stub that fires the
     after-callback) so the canonical write happens. If
     `_run_async_impl` is called directly without the Runner, the
     after-callback won't fire — the test must invoke the callback
     explicitly or switch to the Runner harness.
   - Delete `"user:positions" not in delta` negative assertions.
   - Where two tests are near-duplicates, collapse to one (executor
     test P1-03) and delete the duplicate.
10. **Tests — delete the five `resolve_broker_call_*` tests** from
    `tests/unit/agents/executor/test_verb_dispatch.py:76-131,
    313-330`. Confirm the remaining `apply_stance_to_thesis` tests
    in the same file (8 tests) still pass.
11. **Tests — add the two new surfacing tests** for BUY-without-
    stance (test P0-01) and fill-price-missing-order (test P0-02).
12. **Tests — strengthen idempotency** (test P0-03). Add
    `len(broker._orders) == 0` assertion + empty-events assertion
    + docstring.
13. **Run the full suite** and verify green. Run
    `.venv/bin/python -m ruff check src/` clean.
14. **Self-audit against the rubric.** Particularly check:
    - No new C5 silent-failure attractors introduced by the
      narrowed swallow / removed fallbacks.
    - No new T3 completion-only assertions in the rewritten tests.
    - The after-callback is genuinely the only writer of
      `user:positions` after the change — grep
      `state["user:positions"] =` and `state_delta = {"user:positions"`
      across `src/` to confirm.
15. **Append graphify delta entry** noting the deleted helpers
    (`resolve_broker_call`, `_NO_TRADE_INTENTS`), the changed
    reader signatures in `context_shim.py` / `decision_logger.py`,
    and the deleted/collapsed tests.

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in
  commit body).
- [ ] Bare-key `"positions"` is **not written** anywhere in `src/`
  after the PR — verify by
  `grep -rn '"positions"' src/ --include="*.py" | grep -v
  'user:positions' | grep -v 'temp:positions'` returning zero
  state-write hits (read-only string-literal matches in docstrings
  or comments are fine).
- [ ] Six previously bare-key-asserting tests now assert on
  `state["user:positions"]`.
- [ ] Five `resolve_broker_call_*` tests deleted; the
  `apply_stance_to_thesis` tests in the same file still pass.
- [ ] Two new surfacing tests (BUY-without-stance, fill-price OR-chain)
  fail when the corresponding source fix is reverted on a scratch
  branch (sanity check — optional but recommended).
- [ ] `test_executor_idempotent` asserts positively on broker call
  count, not just completion.
- [ ] Graphify delta entry appended.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
.venv/bin/python -m pytest tests/unit/agents/executor/ tests/integration/test_executor_with_fake_broker.py -v
grep -rn '"positions"' src/ --include="*.py" | grep -v 'user:positions' | grep -v 'temp:positions'
```

## Risks and rollbacks

- **Risk — same-tick BUY+SELL coordination genuinely broken by
  bare-key removal:** if a test exists that exercises real
  same-tick BUY+SELL on the same ticker, removing the bridge
  outright breaks it. Mitigation: run the tests first; if a real
  coordination need surfaces, reinstate via `temp:positions_pending`
  (Rule-8 prefix) rather than the bare key. Document the decision
  in the executor docstring.
- **Risk — the after-callback isn't actually firing in unit tests
  that bypass the Runner:** the six rewrites need either a Runner
  harness or an explicit callback invocation. If most of the
  affected tests call `_run_async_impl` directly, switching to
  Runner-driven harnesses is a larger refactor than the spec
  suggests. Mitigation: prefer explicit callback invocation
  (`agent.after_agent_callback(ctx)` or equivalent) inside the
  test where simpler, fall back to Runner only where the unit
  shape genuinely requires it.
- **Risk — `resolve_broker_call` has a hidden caller** outside
  graphify's view (e.g. a script or a sibling repository). Grep
  `src/`, `scripts/`, and `tests/` before deletion. If any
  non-test caller surfaces, leave the helper in place and downgrade
  the source fix to "add `_deprecated` warning".
- **Risk — the `context_shim.py` / `decision_logger.py` readers
  expect a different shape from `user:positions` than from the
  bare key.** Verify the value shapes are identical (the audit
  implies they are — same dict from the same source — but confirm
  in the diff).
- **Rollback:** feature branch discardable; no `main` impact until
  merge. The bare-key removal is reversible by reverting the
  executor diff (the readers' switches must be reverted in
  lock-step).

## Subagent dispatch prompt sketch

> Implement T-F06 from `docs/Phase11-project-audit/fix-plan/T-F06-executor-positions-key.md`.
> Read `docs/Phase11-project-audit/source-audit/agents-executor.md` and
> `docs/Phase11-project-audit/test-audit/executor.md` in full first.
>
> Source-side changes:
> (1) Drop the bare-key `"positions"` write path in
> `src/agents/executor/agent.py` (5 sites) — the executor's
> `after_agent_callback` writes `user:positions` exclusively via
> the auto-yielded delta-tracked pattern.
> (2) Switch the two external readers (`context_shim.py:125`,
> `decision_logger.py:335`) to `user:positions`. Grep `src/` for
> any other bare-key holdouts and patch in-pass.
> (3) Add loud surfacing on BUY-without-matching-stance
> (`agent.py:124-179`) — raise preferred.
> (4) Drop the fill-price OR-chain dead-key fallbacks
> (`agent.py:379-390`) and narrow the `except (AssertionError,
> ValueError)` swallow at `:431-447`.
> (5) Default-delete `resolve_broker_call` + `_NO_TRADE_INTENTS`
> from `_verb_dispatch.py` (override path: leave in place if a
> real caller surfaces). Remove the stale risk_gate comments at
> `agent.py:19, 59`.
>
> Test-side changes (in lock-step):
> Reshape the six tests that currently assert positively on the
> bare key `"positions"` to assert on `state["user:positions"]`.
> Delete the five `resolve_broker_call_*` tests. Add the two new
> surfacing tests (BUY-without-stance, missing-order-key). Strengthen
> the idempotency test past completion-only.
>
> Layout context: T-F10 must have landed first — the canonical
> executor test home is `tests/unit/agents/executor/`. Confirm
> before starting.
>
> Full `.venv/bin/python -m pytest tests/` must pass green before
> commit. Shell convention: never prepend
> `cd ".../StockBot" && ...` to bash commands.

# Source audit — src/agents/executor

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 2 (`agent.py`, `_verb_dispatch.py`; `__init__.py` is empty)
**Findings:** 0 P0 · 3 P1 · 3 P2 · 2 P3

## Summary

The Executor subsystem owns broker dispatch (`_run_async_impl`) and the
writer-of-record path for the cross-tick `user:positions` and
`user:thesis` keys (`_executor_thesis_writer_callback`). The two
themes that dominate the findings are (a) a sister bare-key
`positions` bridge that has outlived its in-tick purpose and now
shadow-writes alongside the canonical `user:positions` (parallel
old/new state), and (b) a small cluster of silent-degradation
attractors in the BUY thesis-assembly path and the after-callback's
fill-price lookup. Cross-subsystem touch points to flag for
consolidation: the contract doc's `thesis_revision` field name does
not exist in `StrategistDecision` (it is `thesis`), the
`strategist/schema.py` docstring still attributes thesis writes to
`MemoryWriter`, the `strategist/context_shim.py` comment claims
`state["positions"]` is "never written post-Plan-1" (the executor
writes it every tick), and `risk_gate/agent.py` comments still
reference `resolve_broker_call` as if it were live.

## Findings

### P1-01 · C2 parallel old/new branches · bare-key `positions` shadows canonical `user:positions`

- **Location:** `src/agents/executor/agent.py:94, 179, 259, 276, 317-324` (write sites); readers at `src/agents/strategist/context_shim.py:125`, `src/backtest/decision_logger.py:335`.
- **Confidence:** high
- **Description:**
  The executor maintains two parallel position books inside the same
  tick. The canonical `user:positions` is written exclusively by
  `_executor_thesis_writer_callback` (see contract §A row, §C-Rule 1
  Spec B clarification). A second copy under the bare key
  `"positions"` is written directly into session state (line 276) and
  also propagated cross-tick via the executor's `state_delta` event
  (line 324) — meaning it is durable in `DatabaseSessionService`, not
  merely an in-tick handoff. The original justification (see comment
  at lines 86–93 and 317) is the "Band 4 BUY→SELL bridge" — the
  same-tick channel that lets the SELL branch (line 190 onward) read
  the newly-assembled thesis before the after-callback runs. That is
  a real in-tick need, but two readers outside the executor consume
  the bare key (`context_shim.py:125` falls back to it; the backtest
  `decision_logger.py:335` reads only the bare key). Because the bare
  key is persisted, the two stores can drift across ticks (e.g. a
  close stance whose after-callback path raises and is swallowed at
  agent.py:431, leaving `user:positions` unchanged but the bare key
  having had the ticker `del`-ed at line 259). The bare key is also
  not present in the §A schema and has no documented owner. This is
  the classic "parallel old/new branch one bad merge from divergence"
  shape.
- **Suggested action:**
  Either (a) drop the bare key from the executor's `state_delta`
  payload so it lives strictly as an in-tick scratch via direct
  mutation (and update the two external readers to use
  `user:positions`), or (b) demote both readers to `user:positions`
  and delete the bare-key write end-to-end now that the
  writer-of-record split exists. The TODO at `backtest/driver.py:261`
  already signals the second option as the intended terminus.

### P1-02 · C5 silent-failure attractor · BUY-without-matching-open-stance silently skips thesis assembly

- **Location:** `src/agents/executor/agent.py:124-179`
- **Confidence:** medium
- **Description:**
  In the BUY branch, the executor searches `strategist_decision.stances`
  for the unique stance whose `ticker == order.ticker` and
  `intent == "open"`. If no such stance is found (`open_stance is
  None`, line 139), the position is silently not added to the
  bare-key bridge — no log, no raise, no telemetry. Because the
  after-callback (`_executor_thesis_writer_callback`) iterates
  `decision.stances` rather than `state["final_orders"]`, an
  `add`/`trim`/`hold`/`update` stance with a matching order would
  hit the correct branch in the callback — but a genuine BUY order
  with no corresponding stance verb at all is a contract violation
  upstream (risk_gate would only emit it if the proposed weight
  jumped and stances were misaligned) and the executor's silent
  coercion to "no thesis" hides it. Per the
  `feedback_silent_failures_loud_tests` policy, this is the
  recurring bug class.
- **Suggested action:**
  Raise (or at minimum emit a loud warning to stderr matching the
  style at lines 440–446) when a BUY order has no matching open
  stance. The case "stance verb is `add`" is legitimate and reaches
  the after-callback fine; only "no stance at all for this BUY
  ticker" should be loud.

### P1-03 · C5 silent-failure attractor · fill-price lookup tolerates two dead alternative key shapes

- **Location:** `src/agents/executor/agent.py:379-390`
- **Confidence:** medium
- **Description:**
  The callback builds `fill_prices` from `state["executions"]` with
  three OR-fallback expressions: ticker from `row["order"]["ticker"]`
  OR `row["stance"]["ticker"]`, price from `row["fill_price"]` OR
  `row["actual_price"]`. The `Execution` Pydantic model
  (`src/orchestrator/state.py:46-55`) carries only `order`,
  `actual_price`, `actual_quantity`, `slippage_bps`,
  `broker_order_id`, `error`, `status`. Neither `stance` nor
  `fill_price` is ever populated by this codebase — the test fixtures
  at `tests/unit/agents/executor/test_thesis_writer_callback.py:99-106`
  emit only `{"order": ..., "actual_price": ..., "status": ...}`. The
  alternative keys exist for no live producer, so when an Execution
  shape drifts (e.g. a future change forgets `order`), the lookup
  silently falls back to `""` for the ticker and `None` for the
  price, and `apply_stance_to_thesis` is called with
  `fill_price=None` — which the `open` branch turns into an
  AssertionError that the surrounding `except (AssertionError,
  ValueError)` at lines 431-447 swallows with only a stderr print.
  Loss of any thesis update for a successful BUY is exactly the
  shape that motivated the "silent failures are the recurring bug
  class" policy.
- **Suggested action:**
  Drop the two dead-key fallbacks; read solely `row["order"]["ticker"]`
  and `row["actual_price"]`. If the shape ever drifts, fail loudly
  at the lookup site rather than coercing the result downstream into
  a swallowed assertion in the catch block.

### P2-01 · C1 dead code · `resolve_broker_call` and `_NO_TRADE_INTENTS` have no live callers

- **Location:** `src/agents/executor/_verb_dispatch.py:23, 26-84`
- **Confidence:** high
- **Description:**
  `grep -rn "resolve_broker_call" src/ scripts/` returns only the
  definition itself plus two stale-comment references in
  `src/agents/risk_gate/agent.py:19` and `:59` ("the executor's
  `_run_async_impl` skips broker dispatch for them
  (`resolve_broker_call` returns None)"). The actual executor run
  loop never calls `resolve_broker_call`; it iterates `final_orders`
  emitted by risk_gate, which already pre-filters on `hold` /
  `update` via the parallel constant `_NO_RISK_GATE_INTENTS` at
  `risk_gate/agent.py:20`. The set `_NO_TRADE_INTENTS` is only
  referenced inside `resolve_broker_call` and is therefore
  transitively dead. Tests at
  `tests/unit/agents/executor/test_verb_dispatch.py` exercise the
  function, but per the rubric "if a test exercises it, it is not
  dead — note the test reference in the finding and downgrade or
  drop." Those tests assert the function's intrinsic logic, not its
  use anywhere — they would survive a deletion only as a fossil. I
  am leaving this filed as P2 dead code with a pointer to the test;
  the consolidation pass can decide whether to retire the function
  or wire it into the run loop.
- **Suggested action:**
  Either delete `resolve_broker_call` + `_NO_TRADE_INTENTS` + the
  test module + the stale comments in `risk_gate/agent.py`, or
  reinstate it as the gate that produces broker dispatch decisions
  inside `_run_async_impl` (replacing the implicit assumption that
  `final_orders` is already pre-filtered). The latter is the
  shape the docstring at `_verb_dispatch.py:42-54` already
  anticipates.

### P2-02 · C5 silent-failure attractor · catch-all `Exception` suppression around `decision_logger.on_executions`

- **Location:** `src/agents/executor/agent.py:296-300`
- **Confidence:** medium
- **Description:**
  `contextlib.suppress(Exception)` wraps the `dl.on_executions(...)`
  call with the rationale "a logger failure must never abort the
  tick". Observability is Rule 8 (contract-neutral, additive), and a
  catch-all here is defensible by that rule. The hazard is calibration:
  the suppression is broad enough to hide bugs in the logger itself
  (e.g. a serialisation failure on a new state shape) without any
  signal — no stderr print, no metric, no narrowing of the exception
  set. The §A.7 silent-failure-attractor pattern is "logged-but-not-
  propagated warnings on the happy path"; this is worse — not even
  logged.
- **Suggested action:**
  Narrow the suppression to specific exception types the logger is
  documented to raise on legitimately-bad inputs, and at minimum emit
  a stderr line on swallow (same shape as lines 440–446). The
  observability invariance is preserved; the bug-class is reintroduced
  to detection.

### P2-03 · C7 doc/code drift · stale BUY-path comments reference removed `new_positions` and outdated bands

- **Location:** `src/agents/executor/agent.py:86-93, 116-123, 165-168, 279-287, 309-317`
- **Confidence:** high
- **Description:**
  The file's comment thread carries five overlapping references to
  removed or transitional code:
  (a) line 88-92 references "Band 4 bare-key bridge" and "the BUY→SELL
  in-tick channel; `new_positions` (the strategist's pre-computed
  thesis) was the only thing removed in Band 6" — both Band labels
  predate the current code shape and assume the reader already knows
  the band history;
  (b) line 116-119 ("BUY: assemble the thesis from the open-intent
  stance + fill price… the after_agent_callback writes the new-model
  `user:positions`") restates the band history;
  (c) line 165-168 ("This mirrors the old `new_positions` behaviour
  where the strategist's `opened_tag` came from `decision_tag`")
  references the deleted shape;
  (d) line 279-287 ("`user:positions` is intentionally NOT mutated
  here…") is mostly accurate but reiterates the Band 4 framing one
  more time;
  (e) line 309-317 also restates the same.
  None of these match a finding category alone, but together they
  form a doc/code-drift cluster that obscures the file rather than
  documenting it.
- **Suggested action:**
  Consolidate the five overlapping comments into a single block at
  the top of `_run_async_impl` that names the two writer-of-record
  responsibilities (bare-key bridge for in-tick `BUY→SELL`,
  after-callback for cross-tick `user:positions`) without the band
  history. Drop band-numbered references entirely; they are noise to
  a reader without the migration log.

### P3-01 · C7 doc/code drift · cross-subsystem references to be flagged for `contract-invariants.md` and sibling subsystems

- **Location:** `docs/contract-invariants.md` §A footnote (line 89-98) and §A `state["user:thesis"]` row text; `src/agents/strategist/schema.py:135-145`; `src/agents/strategist/context_shim.py:121-125`; `src/agents/risk_gate/agent.py:19, 59`.
- **Confidence:** high
- **Description:**
  Four cross-subsystem drift items surfaced during the executor audit
  but the changes themselves do not belong in `src/agents/executor/`:
  (1) `contract-invariants.md` §A row for `user:thesis` and the
  footnote at line 89-98 refer to "Strategist's optional
  `thesis_revision`"; the actual field on `StrategistLLMDecision` and
  `StrategistDecision` is `thesis` (see
  `src/agents/strategist/schema.py:106, 135`). No code references
  `thesis_revision`. File against `docs/contract-invariants` per
  rubric §C7.
  (2) `src/agents/strategist/schema.py:138-139` docstring still says
  "When non-null, MemoryWriter writes the new text to
  state['thesis']" — but the Spec B path is Executor's
  after-callback writing `user:thesis`; MemoryWriter no longer owns
  this write.
  (3) `src/agents/strategist/context_shim.py:122-124` comment claims
  '`state["positions"]` is never written post-Plan-1' — the executor
  writes it every tick (see P1-01 above).
  (4) `src/agents/risk_gate/agent.py:19, 59` reference
  `resolve_broker_call` as if it were the gate the executor uses;
  it is not (see P2-01).
- **Suggested action:**
  Hand items (1)→`docs/contract-invariants`,
  (2)→`src/agents/strategist`, (3)→`src/agents/strategist`,
  (4)→`src/agents/risk_gate` for the consolidation pass to route to
  the relevant subsystem audits.

### P3-02 · C3 overabstraction (low confidence) · `build_executor` factory wraps a one-arg constructor

- **Location:** `src/agents/executor/agent.py:476-484`
- **Confidence:** low
- **Description:**
  `build_executor(broker, db_session=None)` returns
  `ExecutorAgent(broker=broker, db_session=db_session)` and does
  nothing else. Used by `src/orchestrator/pipeline.py:152` and three
  test modules. The rubric C3 exception for "Rule 7 architectural
  seams" likely covers this — the factory is the
  pipeline-vs-lifecycle composition point — so I file with `low`
  confidence and defer to the consolidation pass. If the seam is
  intentional symmetry with other `build_*` factories in
  `orchestrator/pipeline.py`, this is not overabstraction; if it is
  a stub left from when the constructor was richer, inline it.
- **Suggested action:**
  Consolidation pass decides: either fold into `ExecutorAgent`'s
  constructor at the call sites (delete the factory), or leave as-is
  if it parallels sibling `build_*` factories that share a
  pipeline-composition signature.

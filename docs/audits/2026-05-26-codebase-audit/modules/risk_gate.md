# Module audit — `risk_gate`

Scope: `src/agents/risk_gate/{agent,constraints,orders}.py` + tests.

Intent reference: `docs/audits/2026-05-26-codebase-audit/intent.md` §2.4 and
§7 (authoritative). Contract: `docs/contract-invariants.md` §A row
`final_orders` / `risk_clamps_applied` (implicit — RiskGate is the writer-of-
record).

---

## F-risk_gate-001
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `src/agents/risk_gate/agent.py:49-51`
- **Evidence:**
  ```python
  decision_raw = state.get("strategist_decision")
  if not decision_raw:
      return
  ```
  Bare `return` from an `async def _run_async_impl` yields zero events. No
  `final_orders` key is written to state, no clamp telemetry is recorded,
  no trace is emitted, no log line fires.
- **Intent violated:** §2.4 ("Outputs … `state["final_orders"]`,
  `state["risk_clamps_applied"]`, defensive write to
  `state["last_risk_gate_decision"]`"). Intent says the agent emits these
  keys; it does not contemplate an early-exit silent skip.
- **Suggested action:** investigate. Either (a) raise on a missing /
  empty `strategist_decision` (it is upstream contract that the strategist
  always emits one — silent skip masks a real pipeline break), or (b) at
  minimum yield an Event with `final_orders=[]` + a structured-log
  warning so the executor sees a deterministic empty payload rather than
  an absent key. The current `if not decision_raw` also treats a decision
  with empty `stances` / empty `target_weights` as "no decision" because
  truthy-check on a model is identity-true but a dict cast might surface
  empty; verify the truthiness semantics under the dict / model branches.
- **Notes:** matches the recurring silent-failure class flagged in the
  user-global MEMORY (`feedback_silent_failures_loud_tests.md`). No test
  exercises this branch.

## F-risk_gate-002
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `src/agents/risk_gate/orders.py:31-32`,
  `src/agents/risk_gate/agent.py:100-104`
- **Evidence:**
  ```python
  # orders.py
  if ticker not in prices:
      raise ValueError(f"no price for {ticker}")
  # agent.py
  prices = {t: pos.last_price for t, pos in portfolio.positions.items()}
  if hasattr(self.broker, "_prices"):
      for t, p in self.broker._prices.items():
          if t not in prices:
              prices[t] = p
  ```
  The price map is built from **currently-held positions only**, with a
  test-only fallback to `FakeBroker._prices`. `Trading212Broker` does not
  expose `_prices`. Any **new-position BUY** (a ticker the strategist
  wants to open that is not already held) therefore has no price in the
  map and `weights_to_orders` raises `ValueError("no price for X")`,
  crashing the entire tick.
  Note: `state["reference_prices"]` (the bulk yfinance pull described in
  contract §A) is never read by the risk gate, even though it is the
  canonical per-tick price source for unheld tickers.
- **Intent violated:** §2.4 ("Convert clamped weights into broker
  orders"); contract §A `reference_prices` row (Tick bootstrap supplies
  this for exactly this purpose).
- **Suggested action:** investigate. Either (a) fall back to
  `state["reference_prices"]` for any ticker missing from
  `portfolio.positions`, or (b) document that BUYs of unheld tickers go
  through a different broker call that does not need an est_price. The
  current code only works because `FakeBroker` injects all watchlist
  prices into `_prices`; live will block on the first new buy.
- **Notes:** loud failure but live-blocking and currently undetectable in
  unit tests (which all use `FakeBroker` with pre-loaded `_prices`).

## F-risk_gate-003
- **Category:** policy-mismatch
- **Severity:** P1
- **Location:** `src/agents/risk_gate/agent.py:21,75-86`
- **Evidence:**
  ```python
  _NO_RISK_GATE_INTENTS: Final[frozenset[str]] = frozenset({"hold", "update"})
  ...
  for _ticker, _intent in list(_stance_intents.items()):
      if _intent in _NO_RISK_GATE_INTENTS:
          proposed.pop(_ticker, None)
  ```
  Intent §2.4: "`update` and `no_action` stances are stripped from the
  weight dict before clamping (they carry no weight change)." Code strips
  `update` and the legacy `hold` — but **not `no_action`**.
  `derivation.py:300-304` writes `target_weights[stance.ticker] = current`
  for every `no_action` ticker, so those tickers survive into `proposed`
  and then into `apply_constraints`. If the held weight happens to exceed
  `MAX_POSITION_WEIGHT` (or the per-delta clamp triggers on rounding), the
  gate produces a SELL order against a stance the strategist explicitly
  said is "considered, no change". The lifecycle check
  (`agent.py:141-148`) will also fire its `sell_reason` requirement
  against `no_action` exits.
- **Intent violated:** §2.4 bullet "`update` and `no_action` stances are
  stripped".
- **Suggested action:** add `"no_action"` to `_NO_RISK_GATE_INTENTS` and
  drop the legacy `"hold"` (the canonical verb set is
  `buy/sell/update/no_action` per `stance_schema.py:98`).
- **Notes:** the existing test `test_no_risk_gate_intents_constant_contains_hold_and_update`
  (`tests/unit/orchestrator/test_risk_gate.py:77`) actively encodes the
  stale verb — see F-risk_gate-009.

## F-risk_gate-004
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `src/agents/risk_gate/constraints.py:170-176`
- **Evidence:**
  ```python
  _clamp_negatives(proposed, clamps)
  _clamp_max_position(proposed, clamps)
  _clamp_cash_floor(proposed, clamps)
  _clamp_max_delta(proposed, current, clamps)
  _clamp_max_turnover(proposed, current, clamps)
  ```
  Intent §2.4: "Clamps applied in order: buy-delta-per-trade → concentration
  cap → cash floor → per-ticker delta → total turnover → no-short rule."
  Source order: no-short FIRST, no-short rule no longer at the end.
  Buy-delta clamp lives in a separate function called from the agent
  (`agent.py:66`) before `apply_constraints`, which matches intent
  ordering for stage 1 only.
- **Intent violated:** §2.4 clamp-order bullet.
- **Suggested action:** investigate which ordering is authoritative
  (the intent doc flagged that two drafters disagreed on ordering). Source
  is currently authoritative per the task brief — update intent.md to
  match source, or move `_clamp_negatives` to the end of
  `apply_constraints` if intent is right.
- **Notes:** no behavioural impact today (a `no_short` clamp zeroes a
  negative weight, after which the other clamps simply pass through 0.0),
  but the divergence is a documentation trap.

## F-risk_gate-005
- **Category:** silent-failure
- **Severity:** P1
- **Location:** `src/agents/risk_gate/agent.py:119-135`
- **Evidence:**
  ```python
  _close_tickers = {
      s.ticker
      for s in (decision.stances or [])
      if s.intent == "sell" and s.weight is None
  }
  ...
  weight_clamps = apply_constraints(proposed, current_weights)
  clamps = _stance_clamps + weight_clamps
  for _t in _close_tickers:
      proposed[_t] = 0.0
  ```
  The restoration overwrites `proposed[_t] = 0.0` **after** clamps were
  computed against the (potentially capped) value. The clamp telemetry
  still records the rewrites (`max_delta` / `max_turnover` records remain
  in `clamps`) even though the final weight ignores them. Downstream
  audit consumers see "clamp fired on AAPL" while the actual `final_orders`
  shows a full close — divergent narratives.
- **Intent violated:** §2.4 "Every clamp is recorded for audit." —
  records a clamp that did not actually constrain the output.
- **Suggested action:** investigate. Either suppress clamp records for
  tickers in `_close_tickers`, or document that clamp records are
  "rules that *would have* fired" rather than "rules whose output
  shipped".
- **Notes:** also: full-close override does not re-run `apply_constraints`
  to check whether the *other* tickers' turnover budget changed when AAPL
  jumped from `current - max_delta` to `0.0`. A large held position
  forced to full close could push total turnover above `MAX_TOTAL_TURNOVER`
  without a re-clamp.

## F-risk_gate-006
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/agents/risk_gate/agent.py:186`
- **Evidence:**
  ```python
  # Module-level singleton — pipeline uses RiskGateAgent(broker=...) factory instead.
  risk_gate_agent = RiskGateAgent()
  ```
  ```
  $ grep -rn "risk_gate_agent\b" --include="*.py" .
  src/agents/risk_gate/agent.py:186:risk_gate_agent = RiskGateAgent()
  ```
  Zero external references. The comment confirms the pipeline uses the
  factory.
- **Intent violated:** n/a.
- **Suggested action:** delete.

## F-risk_gate-007
- **Category:** dead-code
- **Severity:** P3
- **Location:** `src/agents/risk_gate/agent.py:11`
- **Evidence:**
  ```python
  from observability.trace import _trace_maybe
  ```
  Importing a `_`-prefixed private helper from another package; legitimate
  use but the underscore convention suggests it should be a public
  re-export. (Not strictly dead — it is called on lines 92 and 163. The
  finding is the leading-underscore-cross-package import; not a deletion
  candidate, more a P3 nit.)
- **Intent violated:** n/a.
- **Suggested action:** investigate — rename `_trace_maybe` to
  `trace_maybe` in `observability` and update the call sites if cross-
  package use is intended.

## F-risk_gate-008
- **Category:** over-abstraction
- **Severity:** P3
- **Location:** `src/agents/risk_gate/constraints.py:32-80`
- **Evidence:** `apply_buy_delta_clamp` is called from exactly one place
  (`agent.py:66`); its only consumer is `RiskGateAgent._run_async_impl`.
  It mutates `stance.weight` in-place **and** returns clamp records. The
  in-place mutation contract is non-obvious; combined with the merged
  `_stance_clamps + weight_clamps` ordering, the two-call structure is
  load-bearing only because `apply_constraints` operates on a `dict[str,
  float]` and cannot see stance objects.
- **Intent violated:** n/a.
- **Suggested action:** investigate consolidating into `apply_constraints`
  by passing the stances list. Removes the "in-place mutation + return
  records" split.

## F-risk_gate-009
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/unit/orchestrator/test_risk_gate.py:77-86`
- **Evidence:**
  ```python
  def test_no_risk_gate_intents_constant_contains_hold_and_update():
      assert "hold"   in _NO_RISK_GATE_INTENTS
      assert "update" in _NO_RISK_GATE_INTENTS
      assert "open"  not in _NO_RISK_GATE_INTENTS
      assert "close" not in _NO_RISK_GATE_INTENTS
      assert "add"   not in _NO_RISK_GATE_INTENTS
      assert "trim"  not in _NO_RISK_GATE_INTENTS
  ```
  Tests stale verbs (`hold`, `open`, `close`, `add`, `trim`). The
  canonical verb set is `buy / sell / update / no_action`
  (`stance_schema.py:98`). The test actively **enforces the bug** in
  F-risk_gate-003 — when the constant is corrected to include `no_action`,
  this test will need a rewrite.
- **Intent violated:** intent §7-style (canonical verbs are buy/sell/
  update/no_action; no `hold`).
- **Suggested action:** delete or rewrite to assert `no_action` and
  `update` are stripped, and `buy` / `sell` are not.

## F-risk_gate-010
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `tests/unit/orchestrator/test_risk_gate.py` vs
  `tests/unit/agents/risk_gate/test_agent.py` vs
  `tests/integration/test_risk_gate_agent.py` vs
  `tests/integration/test_risk_gate_state_delta.py`
- **Evidence:** four files all build `RiskGateAgent` + a `_make_ctx`
  MagicMock InvocationContext stub + drive `_run_async_impl`. The
  `_make_ctx` helper is duplicated verbatim in three of them. The
  state-delta conformance test (`test_risk_gate_state_delta.py`)
  duplicates the FakeBroker + state setup of
  `test_risk_gate_agent.py`; the only delta is "assert there is exactly
  one Event".
- **Intent violated:** n/a (test-policy §D: shared fixtures live in
  `conftest.py`).
- **Suggested action:** consolidate. Move `_make_ctx` to a
  `tests/agents/risk_gate/conftest.py`; merge the integration tests into
  one parameterised file.

## F-risk_gate-011
- **Category:** test-gap
- **Severity:** P0
- **Location:** `tests/` — no test covers
  - the early-return silent-skip (F-risk_gate-001),
  - the missing-price ValueError for an unheld BUY (F-risk_gate-002),
  - a `no_action` stance on a held ticker (F-risk_gate-003),
  - the `_close_tickers` restoration after clamping (F-risk_gate-005).
- **Evidence:**
  ```
  $ grep -rn "strategist_decision.*None\|reference_prices" tests/unit/agents/risk_gate/ tests/unit/orchestrator/test_risk_gate.py tests/integration/test_risk_gate_*.py
  (no output)
  ```
  No test passes `state` without `strategist_decision`. No test passes a
  watchlist BUY whose ticker is not in the FakeBroker `_prices`. No test
  drives a `no_action` stance. No test asserts post-clamp behaviour for
  the full-close restoration path.
- **Intent violated:** test-policy §A.7 ("Tests must surface silent
  failures loudly").
- **Suggested action:** add the four missing tests; flag F-risk_gate-001
  and -002 to the human before fixing.

## F-risk_gate-012
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `tests/integration/test_risk_gate_agent.py:24-32` vs
  `tests/integration/test_risk_gate_state_delta.py:55-65`
- **Evidence:** both decisions carry `"thesis": "ok"` and
  `"close_reasons": {}` — fields that **no longer exist** on
  `StrategistDecision` (commits `742f38e`, `ba8555b` collapsed the schema;
  `close_reasons` was replaced by `sell_reasons` per
  `agent.py:140`'s comment). The dicts are still accepted because
  `StrategistDecision.model_validate` ignores unknown keys for backward
  compatibility, but the test fixtures are now misleading.
- **Intent violated:** §2.6 (the schema collapse commits) — tests
  reference dead field names.
- **Suggested action:** rewrite fixtures to use `sell_reasons` /
  `update_reasons` and drop `thesis`.

## F-risk_gate-013
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/agents/risk_gate/agent.py:21` comment ("Hold has been
  replaced by the three-verb schema")
- **Evidence:** comment says "three-verb schema (buy / sell / update)" —
  the canonical set is the four-verb schema (`buy / sell / update /
  no_action`, per `stance_schema.py:98`). Stale comment from an earlier
  refactor.
- **Intent violated:** §2.6, §3.1 (`stance` row: four-verb vocabulary).
- **Suggested action:** update comment.

---

## Top three for human attention

1. **F-risk_gate-002 (P0, silent-failure):** live broker cannot price a
   new-position BUY because the agent never reads `state["reference_prices"]`.
   This blocks live trading the moment the strategist opens a position not
   already held.
2. **F-risk_gate-003 + F-risk_gate-009 (P1, policy-mismatch + dead-test):**
   `_NO_RISK_GATE_INTENTS` is `{"hold", "update"}` not
   `{"update", "no_action"}`; the unit test actively pins the wrong set.
   `no_action` stances on held tickers slip through the strip and can
   produce surprise SELL orders.
3. **F-risk_gate-001 (P0, silent-failure):** missing `strategist_decision`
   triggers a bare `return` with zero events — no log, no telemetry, no
   `final_orders` key. Classic silent-failure attractor; no test covers
   it.

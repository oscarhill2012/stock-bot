# Source audit — src/agents/risk_gate/

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 4 (`__init__.py`, `agent.py`, `constraints.py`, `orders.py`)
**Findings:** 1 P0 · 0 P1 · 3 P2 · 1 P3

## Summary

RiskGate is the deterministic, no-LLM agent sitting between Strategist and
Executor (§A `strategist_decision` row). It strips no-trade stances,
clamps the strategist's `target_weights` through five hard rules in fixed
order, validates the closing-without-`close_reason` lifecycle invariant,
converts the clamped weights into `Order` objects, and yields a single
`state_delta` carrying `final_orders` + `risk_clamps_applied`. The code is
small, focused, and contract-correct on the Rule 1 / Rule 4 axes. The two
substantive concerns are: (1) a silent early-return when
`state["strategist_decision"]` is falsy that lets the pipeline continue
to Executor with no orders and no surfaced error; (2) a module-level
`RiskGateAgent()` singleton at agent.py:146 that has no callers — the
pipeline constructs `RiskGateAgent(broker=...)` afresh. Cross-subsystem
note for consolidation: RiskGate reads `decision.target_weights`,
`decision.stances`, `decision.close_reasons` — fields owned by the
Strategist's enricher; any rename there must be coordinated.

## Findings

### P0-01 · C5 silent-failure attractor · empty/missing `strategist_decision` is a silent skip

- **Location:** `src/agents/risk_gate/agent.py:45-47`
- **Confidence:** medium
- **Description:**
  When `state.get("strategist_decision")` is `None`, `{}`, or any other
  falsy value, `_run_async_impl` returns without yielding any event.
  No `final_orders` key is written (the §A row is tick-scoped and
  populated by `TickState`'s default `Field(default_factory=list)`),
  no `risk_clamps_applied` is logged, and Executor at
  `src/agents/executor/agent.py:78` reads `state.get("final_orders", [])`
  → empty list → loops over nothing → completes "successfully". From
  outside the pipeline the tick looks like it ran with a "no-trade"
  outcome, but the actual cause is a missing strategist output. Per
  `test-policy.md` §A.7 / `feedback_silent_failures_loud_tests` this
  is the repo's recurring bug class — a degradation path that
  upstream Strategist failures (LLM returning empty JSON, the
  enricher's no-op short-circuit, a missing
  `StrategistDecisionWriter`) can silently steer the whole tick into.
  The early-return guard predates the Strategist enricher work (graph
  delta 2026-05-25) which now guarantees a valid
  `StrategistDecision` whenever the LLM produced one; the fallback
  no longer corresponds to a benign state.
- **Suggested action:**
  Replace `if not decision_raw: return` with a raise — RiskGate's
  contract presupposes a `StrategistDecision` upstream; the absence
  of one is a contract violation, not a no-op. If a benign "no
  decision yet" case actually exists (e.g. a warm-up tick), make it
  explicit by branching on a documented sentinel and surface a
  WARNING log + write an empty `final_orders` via `state_delta` so
  the trace shows RiskGate ran.

### P2-01 · C1 dead code · module-level `risk_gate_agent` singleton

- **Location:** `src/agents/risk_gate/agent.py:145-146`
- **Confidence:** high
- **Description:**
  `risk_gate_agent = RiskGateAgent()` is constructed at import time
  with no broker. The pipeline at `src/orchestrator/pipeline.py:164`
  builds its own `RiskGateAgent(broker=broker)` inside
  `build_pipeline`. `grep -rn "risk_gate_agent" src/ tests/ scripts/`
  finds zero callers of the singleton — the only other hit is a
  comment match in `tests/integration/test_risk_gate_state_delta.py`
  that mentions the module path, not the singleton. The accompanying
  comment ("Module-level singleton — pipeline uses
  RiskGateAgent(broker=...) factory instead.") acknowledges the
  shape; the singleton itself is leftover.
- **Suggested action:**
  Delete lines 145-146. No callers, and constructing a broker-less
  RiskGate at import time has no purpose.

### P2-02 · C3 overabstraction · `self.broker` `hasattr(_prices)` access

- **Location:** `src/agents/risk_gate/agent.py:88-91`
- **Confidence:** medium
- **Description:**
  The price-map construction reaches into `self.broker._prices` — a
  private attribute that exists only on `FakeBroker`
  (`src/broker/fake.py:21`). `Trading212Broker` does not expose
  `_prices`; in live the `hasattr` check is false and the map is
  built solely from `portfolio.positions[t].last_price`. The
  fallback is reasonable but the broker-protocol leakage (RiskGate
  reaching past the broker interface into a test-only implementation
  detail) is a small architectural smell — and it means RiskGate has
  a different ordering-source code path in backtest vs live, which
  Rule 7 (cross-tick persistence is the lifecycle's job, not the
  pipeline's) does not strictly forbid but the spirit of Rule 8
  (additive carve-outs must not change pipeline outputs) makes
  uncomfortable. Today the values agree because the FakeBroker's
  `_prices` and the `Position.last_price` it builds are kept in sync
  on every `set_price` (`src/broker/fake.py:27-28`), but a future
  test that sets a price for a ticker with no position would expose
  the asymmetry.
- **Suggested action:**
  Promote a "give me a price for this ticker" method to the broker
  `Protocol` (e.g. `async def current_price(ticker) -> float`), have
  both broker implementations satisfy it, and drop the `hasattr`
  branch. Or — if RiskGate only needs prices for positions the
  portfolio already holds, drop the `_prices` augmentation entirely.

### P2-03 · C7 doc/code drift · "Strategist callback" comment refers to retired callback location

- **Location:** `src/agents/risk_gate/agent.py:99-100`
- **Confidence:** medium
- **Description:**
  The lifecycle-check comment says "New-open validation is handled
  earlier by the Strategist callback." Per `graph_delta.md`
  2026-05-25, the Strategist `after_agent_callback` for derivation /
  validation was lifted out of `LlmAgent` into the
  `StrategistEnricher` BaseAgent (`src/agents/strategist/enricher.py`).
  The old `_strategist_validation_callback` is now a thin legacy
  shim. The phrase "Strategist callback" still loosely fits (the
  enricher delegates to `validate_and_enrich`, which is shared with
  the shim) but reads as out-of-date for anyone tracing the wiring.
- **Suggested action:**
  Rewrite the comment to "New-open validation is handled earlier by
  the StrategistEnricher (`agents/strategist/enricher.py`)."

### P3-01 · C7 doc/code drift · stale "Phase 9" / Plan C history in nearby commentary

- **Location:** `src/agents/risk_gate/agent.py:129-134`
- **Confidence:** low
- **Description:**
  The block comment above the `yield Event(...)` cites "Contract Rule
  1" and `docs/contract-invariants.md` §C-Rule 1 — accurate today.
  The mention of "RiskGate's output handshake to the Executor
  (final_orders) and to observability (risk_clamps_applied) is one
  logical step" frames `risk_clamps_applied` as observability, but
  per §A `risk_clamps_applied` is not in the field schema at all
  (it's pipeline-internal working state). This is not a contract
  violation — §A explicitly scopes itself to "contract-bearing
  fields" — but the comment's "to observability" wording lightly
  implies it lives on the contract surface. A casual reader might
  misread. Cosmetic.
- **Suggested action:**
  Tighten the wording to "to observability/telemetry consumers" or
  drop the qualifier. Low priority; only worth a touch if the file
  is already being edited.

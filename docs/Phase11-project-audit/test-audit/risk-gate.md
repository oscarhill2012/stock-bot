# Test audit — src/agents/risk_gate/

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md`
**Test files in scope:** 6 (full list below)
**Tests collected from those files:** 26 (via `pytest <paths> --collect-only -q`)
**Findings:** 2 P0 · 0 P1 · 6 P2 · 1 P3

## Files in scope

Discovery sweep (`grep -rln "risk_gate\|RiskGate\|risk_gate_agent\|risk_gate_orders\|risk_gate_constraints" tests/` plus `find tests -iname "*risk_gate*" -o -iname "*riskgate*"`) returned a six-file core plus a handful of files that only mention RiskGate in passing (pipeline-composition smoke / decision-logger schema / enricher docstring). The core six are:

- `tests/integration/` — 2 files
  - `tests/integration/test_risk_gate_agent.py` (1 test)
  - `tests/integration/test_risk_gate_state_delta.py` (1 test)
- `tests/unit/orchestrator/` — 1 file (layout outlier — see P2-04)
  - `tests/unit/orchestrator/test_risk_gate.py` (5 tests)
- `tests/unit/` (root-level, no module-mirror folder) — 3 files (layout outlier — see P2-04)
  - `tests/unit/test_risk_gate_constraints.py` (12 tests)
  - `tests/unit/test_risk_gate_orders.py` (4 tests)
  - `tests/unit/test_risk_gate_config_loader.py` (2 tests)

Files that merely *mention* RiskGate (out of scope for this audit, covered by adjacent subsystems):
`tests/integration/backtest/test_end_to_end_smoke.py`,
`tests/integration/test_pipeline_composition.py`,
`tests/unit/agents/strategist/test_enricher.py`,
`tests/unit/backtest/test_decision_logger.py`,
`tests/unit/orchestrator/test_pipeline_wiring_v2.py`,
`tests/unit/test_strategist_prompt_risk_substitutions.py`.

## Summary

The constraints + orders units (`tests/unit/test_risk_gate_{constraints,orders}.py`) are content-rich and contract-correct: per-clamp before/after assertions, rule-name assertions, algorithm-order verification. The agent-level coverage is the weak side — both integration tests assert only structural key-presence (`"final_orders" in state`, `isinstance(..., list)`) without ever asserting that the orders list is non-empty or contains the expected ticker/action, and there is *no* test of source-audit P0-01 (silent return on falsy `strategist_decision`) or of the lifecycle invariant raise at `agent.py:104-108` (closing without `close_reason`). These are the load-bearing surfacing paths and they are entirely unguarded. Layout-wise the file split is also incoherent: `tests/unit/test_risk_gate_*.py` sit at the unit root next to a fifth file that lives at `tests/unit/orchestrator/test_risk_gate.py`, with no `tests/unit/agents/risk_gate/` folder mirroring the source.

## Findings

### P0-01 · T4 missing surfacing test · no test asserts RiskGate raises (or surfaces) when `strategist_decision` is falsy/missing

- **Location:** new test needed (currently zero coverage across all six in-scope files)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md` P0-01 (silent early-return at `agent.py:45-47`)
- **Confidence:** high
- **Description:**
  Source P0-01 calls out that `if not decision_raw: return` lets the tick continue to Executor with no orders, no log, no `state_delta` — a textbook silent-degradation attractor per `test-policy.md` §A.7 and the user memory `feedback_silent_failures_loud_tests`. The test suite contains zero coverage of this path: a grep for `strategist_decision.*None`, `strategist_decision.*{}`, and "missing strategist" against the six in-scope files returns no matches. Neither does the suite *document the current (broken) silent-skip* (which would itself be a tell-tale T3 — "didn't raise" anchoring buggy behaviour), nor does it assert the fix-state (raise / explicit warning + empty `final_orders` in `state_delta`). Without this test the source fix can land and silently regress later.
- **Suggested action:**
  Add a new test (e.g. `tests/unit/agents/risk_gate/test_agent.py::test_risk_gate_raises_when_strategist_decision_missing` and a sibling `..._when_strategist_decision_empty_dict`) that drives `_run_async_impl` with `state = {}` and `state = {"strategist_decision": None}` and `state = {"strategist_decision": {}}`, and — depending on the source-fix shape chosen — asserts either (a) the agent raises a `StrategistContractViolation` (or analogous typed error), or (b) it yields exactly one `Event` whose `state_delta` carries `final_orders=[]` plus a WARNING log line captured via `caplog`. Pair every variant with `caplog.set_level(WARNING)` and assert *positively* on the surfacing primitive — do not write "didn't raise" tests on this path.

### P0-02 · T4 missing surfacing test · no test asserts the closing-without-`close_reason` lifecycle raise

- **Location:** new test needed
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md` (lifecycle check at `agent.py:99-108` is a contract-bearing branch; closely related to P0-01 in the silent-failure-attractor inventory)
- **Confidence:** high
- **Description:**
  `agent.py:101-108` raises `StrategistContractViolation` when a position transitions from `current_weights >= MIN_HELD_WEIGHT` to a post-clamp weight below the threshold without a matching entry in `decision.close_reasons`. This is the only contract invariant RiskGate is responsible for enforcing on its own (everything else is hand-offs). Grepping `close_reason` plus `Closing.*without` plus `StrategistContractViolation` against the in-scope files returns *no* test that exercises the raise; the only hits are the empty `"close_reasons": {}` literal in two integration tests' happy-path payloads. A future refactor (e.g. demoting the raise to a log, or silently coercing the absent reason to a default string) would not fail any test. Per the source-audit silent-failure-attractor theme this branch needs a positive assertion.
- **Suggested action:**
  Add a unit test in the same new `tests/unit/agents/risk_gate/test_agent.py` (or alongside the lifecycle code) that constructs a `FakeBroker` holding an open AAPL position above `MIN_HELD_WEIGHT`, feeds a `StrategistDecision` with `target_weights={"AAPL": 0.0}` and `close_reasons={}` (deliberately empty), drives `_run_async_impl`, and asserts `pytest.raises(StrategistContractViolation, match="Closing")`. Pair it with a happy-path counterpart that supplies `close_reasons={"AAPL": "stop_loss"}` and asserts the agent yields its normal `state_delta` without raising.

### P2-01 · T3 weak assertion · `test_risk_gate_applies_constraints_and_sets_orders` only checks key presence

- **Location:** `tests/integration/test_risk_gate_agent.py:39-40`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md` (general — load-bearing happy-path agent test)
- **Confidence:** high
- **Description:**
  The single assertion block is `assert "final_orders" in state; assert "risk_clamps_applied" in state`. Per `test-policy.md` §A.7 / §E "Asserting only on counts, never on content" this verifies nothing beyond "the agent ran". `final_orders` could legitimately be `[]` (if upstream went wrong and the clamp loop produced no movement, or if the AAPL/MSFT prices were mis-wired so `weights_to_orders` skipped them under `ORDER_EPSILON`) and the test would still pass. The test name promises "applies constraints and sets orders" but does not assert a BUY order for AAPL, does not check the order's action / quantity / est_price, and does not assert that `risk_clamps_applied` is empty for an under-cap input (the proposed weights are 0.05 / 0.0 — well below `MAX_POSITION_WEIGHT`, so no clamps should fire).
- **Suggested action:**
  Strengthen: assert `len(state["final_orders"]) == 1`, `state["final_orders"][0]["ticker"] == "AAPL"`, `state["final_orders"][0]["action"] == "BUY"`, `pytest.approx(state["final_orders"][0]["quantity"]) == 2.5` (0.05 × 10_000 / 200), and `state["risk_clamps_applied"] == []`.

### P2-02 · T3 weak assertion · `test_risk_gate_yields_state_delta_with_orders_and_clamps` asserts only types and event count

- **Location:** `tests/integration/test_risk_gate_state_delta.py:72-80`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md` (Rule 1 conformance — the test's stated purpose)
- **Confidence:** high
- **Description:**
  The test exists to lock in Contract Rule 1 (single co-emitted Event for both writes). It does assert `len(events) == 1`, which is meaningful. But the payload assertions are `"final_orders" in delta; "risk_clamps_applied" in delta; isinstance(delta["final_orders"], list); isinstance(delta["risk_clamps_applied"], list)` — both lists can be empty and the assertions still pass. Same input as P2-01 so the same content assertions apply. The test docstring even says "AAPL has a positive target weight so an order is generated" but that promise is never checked.
- **Suggested action:**
  Strengthen: assert `len(delta["final_orders"]) >= 1`, `delta["final_orders"][0]["ticker"] == "AAPL"`, `delta["final_orders"][0]["action"] == "BUY"`, and `delta["risk_clamps_applied"] == []` (since 0.05 is under-cap). Keep the `len(events) == 1` assertion — that one is doing real work.

### P2-03 · T3 weak assertion · cap tests assert clamp-record presence but not the clamped value or final order content

- **Location:** `tests/unit/orchestrator/test_risk_gate.py:188-199` (`test_risk_gate_caps_open_at_max_position_weight`) and `tests/unit/orchestrator/test_risk_gate.py:202-252` (`test_risk_gate_caps_add_at_max_delta_per_ticker`)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md` (general — these are the most thorough agent-level tests; their content assertions should match the strength of the constraint unit tests)
- **Confidence:** medium
- **Description:**
  Both tests assert `len(clamp_records_for_ticker) > 0` and nothing else. The unit-level constraint tests (`tests/unit/test_risk_gate_constraints.py`) already assert `clamps[0].rule == "max_position"`, `clamps[0].before == 0.50`, `clamps[0].after == 0.20` — that level of specificity is exactly what's missing one layer up. As a bonus the `caps_open` test passes `broker=None`, which means `orders = []` *always* in the agent path (see `agent.py:110`); the test therefore cannot ever assert on `final_orders`, which is half the point of testing at the agent layer rather than the constraint layer. The "caps add" test uses a `MagicMock` broker that would let it assert order content, but doesn't.
- **Suggested action:**
  Strengthen `test_risk_gate_caps_open_at_max_position_weight` to assert `aapl_clamps[0]["rule"] == "max_position"`, `aapl_clamps[0]["before"] == overweight`, `aapl_clamps[0]["after"] == MAX_POSITION_WEIGHT`. Strengthen `test_risk_gate_caps_add_at_max_delta_per_ticker` analogously and additionally assert `len(delta["final_orders"]) == 1` and `delta["final_orders"][0]["action"] == "BUY"` with quantity matching the clamped delta. Consider providing a non-`None` broker to the `caps_open` test so the order half of the contract is exercised.

### P2-04 · T8 layout · risk-gate tests scattered across three directories with no `tests/unit/agents/risk_gate/` mirror

- **Location:** the six file paths listed in "Files in scope"
- **Source-audit cross-ref:** —
- **Confidence:** high
- **Description:**
  Per `test-policy.md` §B "Unit tests live under `tests/unit/` mirroring the source tree", `src/agents/risk_gate/{agent,constraints,orders}.py` should map to `tests/unit/agents/risk_gate/test_{agent,constraints,orders}.py`. Today the constraint and orders unit tests sit at the **unit root** (`tests/unit/test_risk_gate_constraints.py`, `tests/unit/test_risk_gate_orders.py`), the verb-aware agent unit test sits under **orchestrator** (`tests/unit/orchestrator/test_risk_gate.py`) — which is wrong: risk_gate is not part of `src/orchestrator/` — and the two BaseAgent integration tests sit at the integration root with no `tests/integration/agents/` grouping. The config loader test also lives at unit root rather than under `tests/unit/config/`. A new author looking for "risk gate tests" has to grep, not navigate.
- **Suggested action:**
  Consolidate under `tests/unit/agents/risk_gate/test_{agent,constraints,orders}.py` (move the orchestrator-mislocated `test_risk_gate.py` into the new folder; rename the two root-level files); move `test_risk_gate_config_loader.py` to `tests/unit/config/test_risk_gate.py`. Integration files can stay where they are or move to `tests/integration/agents/risk_gate/` if that pattern is adopted repo-wide.

### P2-05 · T6 wide-scope monkeypatch · `MagicMock(spec=Position)` substitutes the real `Position` model in the agent-level `caps_add` test

- **Location:** `tests/unit/orchestrator/test_risk_gate.py:218-231`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md` P2-02 (FakeBroker `_prices` peek — the same broker-protocol surface)
- **Confidence:** medium
- **Description:**
  The test builds a `MagicMock(spec=Position)` and a free-form `MagicMock()` broker rather than using the real `Position` / `Portfolio` / `FakeBroker` from `src/broker/`. Per `test-policy.md` §A.5 / §E "Mocking `CachedDataStore` or `data.providers.registry`" the same logic applies here: the real `FakeBroker(starting_cash=..., prices={...})` is cheap, in-process, and exposes exactly the surface RiskGate uses. Substituting a mock at the `get_portfolio` level bypasses the `portfolio.current_weights()` calculation (it returns whatever the mock is told), the `Position.last_price` accessor, and — crucially — the `FakeBroker._prices` peek branch that source-audit P2-02 flagged. This is a missed opportunity to defend the peek path *and* a §A.5-level mocking-above-the-seam concern.
- **Suggested action:**
  Reshape using `FakeBroker(starting_cash=10_000.0, prices={"TSLA": 250.0})` and a pre-seeded TSLA position (either via direct `Portfolio.positions={"TSLA": Position(...)}` injection on a `FakeBroker` test seam, or via a small helper). This also gives a natural place to add a defending assertion for the `_prices` peek (P2-06 below).

### P2-06 · T4 missing test · no test defends the FakeBroker `_prices` peek behaviour

- **Location:** new test needed
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-risk-gate.md` P2-02 (the `hasattr(self.broker, "_prices")` branch at `agent.py:88-91`)
- **Confidence:** medium
- **Description:**
  Source P2-02 calls the `_prices` peek a "small architectural smell" and notes that the asymmetry is currently invisible because `FakeBroker` keeps `_prices` and `Position.last_price` in sync. The audit also notes "a future test that sets a price for a ticker with no position would expose the asymmetry" — but no such test exists, and no test exists that *pins the current behaviour* either. If source-audit P2-02's preferred fix lands (promote `current_price(ticker)` to the broker `Protocol`), the absence of a test means there is no oracle for "the migrated code still produces the same orders". File this as a forward-looking test gap.
- **Suggested action:**
  Add a unit test that constructs `FakeBroker(prices={"AAPL": 200.0, "MSFT": 300.0})` with `Portfolio` holding only AAPL, runs `_run_async_impl` against a decision touching both AAPL and MSFT, and asserts MSFT orders use the FakeBroker `_prices` value (i.e. the order's `est_price == 300.0`). This pins the current behaviour today *and* becomes the regression oracle for the source-audit P2-02 fix.

### P3-01 · T8 cosmetic · `test_risk_gate.py` docstring still says "Band 4 tests" / "plan §4.5"

- **Location:** `tests/unit/orchestrator/test_risk_gate.py:1-13`
- **Source-audit cross-ref:** —
- **Confidence:** low
- **Description:**
  The module docstring references the dispatch plan that delivered the verb-aware skip rule ("Band 4 tests", "plan §4.5"). That plan landed; the reference is now historical. Low-priority cosmetic per the test-audit severity bands.
- **Suggested action:**
  When this file moves under P2-04, rewrite the docstring to describe the file's scope ("Unit tests for `RiskGateAgent._run_async_impl` — verb-aware skip rule, position-cap clamping, delta-cap clamping") rather than its provenance.

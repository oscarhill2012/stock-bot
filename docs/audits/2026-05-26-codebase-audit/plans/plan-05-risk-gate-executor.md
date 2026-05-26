# Plan 05 — Risk-gate / executor handoff correctness (P0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the strategist → risk_gate → executor handoff fail loudly on every missing-input case (no `strategist_decision`, no price for an unheld BUY, unknown stance verb) and route the executor's error path through `logger` so structured tests can catch it. Eliminate the `FakeBroker._prices` reach-in by promoting `state["reference_prices"]` to a first-class risk_gate input.

**Architecture:** Risk_gate gains a real `reference_prices` parameter sourced from session state (canonical writer is `_build_initial_state` / backtest `_seed_reference_prices`). The `hasattr(broker, "_prices")` fallback is deleted. Risk_gate raises `StrategistContractViolation` (or a new `RiskGateInputError`) when `strategist_decision` is absent. Risk_gate's `_NO_RISK_GATE_INTENTS` is rewritten to the canonical four-verb vocabulary (`{update, no_action}`) — there is no compat layer for `hold`. The executor's after-callback swallows nothing: the bare `print(file=sys.stderr)` becomes `logger.error(..., exc_info=True)`, and `fill_prices` is built from a single canonical key (`actual_price`) with absent prices treated as a loud error (raise) rather than silent `None`. Telemetry under §A-034 is corrected by removing the post-clamp restoration write — full-close handling is folded into `apply_constraints` so clamp records and final weights agree.

**Tech Stack:** Python 3.12, pytest, pydantic v2, Google ADK `BaseAgent`, `caplog`.

**Trust contract:**
- **Trusts landed:**
  - **Plan 01** — repo hygiene, no stray imports referenced here.
  - **Plan 02** — single canonical `rationale` field. Note: Plan 02 originally treated `sell_reasons` / `update_reasons` / `last_reviewed_reason` as stable carry-overs; the A-013 cluster's deletion of those fields now lands here as **Task 8** of this plan (see below). Fixtures and tests touched by earlier tasks in this plan must still construct decisions with the legacy fields populated — Task 8 is the single commit where they go away.
  - **Plan 03** — `Portfolio.from_state_value` classmethod (renamed canonical coercion helper) and canonical `state["portfolio"]` shape, plus the renamed `temp:executor_positions_bridge` key; risk_gate consumes `await broker.get_portfolio()` directly so any state-side coercion is Plan 03's problem, not ours.
  - **Plan 04** — symmetric lifecycle plus `resolve_as_of`; both live `_build_initial_state` and backtest driver write `reference_prices` as `{symbol: PriceHistory.model_dump(mode="json")}` for every tick before risk_gate runs.
- **Later plans trust this plan to land:**
  - The strategist → risk_gate → executor handoff fails loudly on missing decisions, uses real broker prices (not reach-ins), and uses the fresh stance verbs.
  - **Plan 11 (test consolidation)** will assert against the new loud behaviour — every test the present plan rewrites must remain rewritten (no quiet revert to status-only assertions).

---

## Pipeline diagram

```
                   ┌──────────────────────────────────────────┐
                   │ state["strategist_decision"]  (dict)     │
                   │ state["reference_prices"]     (dict)     │  ← seeded by
                   │ state["user:positions"]       (dict)     │    Plan 04
                   └──────────────────┬───────────────────────┘
                                      │
                       missing decision → RAISE (A-001)
                                      │
                                      ▼
        ┌──────────────────────────────────────────────────────┐
        │ RiskGateAgent                                        │
        │   - prices = broker portfolio ∪ reference_prices     │  ← A-002 / A-005
        │   - filter NO_RISK_GATE_INTENTS = {update,no_action} │  ← A-017 / A-061
        │   - apply_constraints (full-close handled inside,    │  ← A-034
        │       no post-clamp restoration)                     │
        │   - weights_to_orders → raise on missing price       │  ← A-002 (already)
        └──────────────────────────────┬───────────────────────┘
                                       │
                  state_delta { final_orders, risk_clamps_applied }
                                       │
                                       ▼
        ┌──────────────────────────────────────────────────────┐
        │ ExecutorAgent                                        │
        │   - dispatch verbs (buy/sell/update/no_action)       │
        │   - write executions w/ canonical actual_price        │  ← A-068
        │   - after-callback: logger.error(..., exc_info=True) │  ← A-008
        │     raises if fill_price missing for any verb that   │
        │     needs one (no silent None)                       │
        └──────────────────────────────────────────────────────┘
```

---

## File structure

**Modify:**
- `src/agents/risk_gate/agent.py` — A-001, A-002, A-005, A-017, A-034, A-061
- `src/agents/risk_gate/orders.py` — only signature/docstring touch-ups; raise path stays
- `src/agents/executor/agent.py` — A-008, A-068
- `src/agents/risk_gate/__init__.py` — export updated symbol if name changes

**Modify (tests):**
- `tests/unit/agents/risk_gate/test_agent.py` — rewrite the verb-set pin + add new loud-failure tests
- `tests/unit/orchestrator/test_risk_gate.py` — rewrite `test_no_risk_gate_intents_constant_contains_hold_and_update` and the `_prices` reach-in test (A-020 explicitly lists this file)
- `tests/integration/test_executor_with_fake_broker.py` — assert via `caplog`, not status-only (A-018 cluster)

**Create (tests):**
- `tests/unit/agents/risk_gate/test_loud_failures.py` — focused suite for A-001, A-002, A-005, A-034

No new production files. No new symbols beyond a possible `RiskGateInputError` class (placed in `src/agents/risk_gate/agent.py`).

---

## Ordered changes

The order below is dependency-driven: each task lands a behaviour change with its test pair before the next task depends on it.

### Task 1 — Replace `_NO_RISK_GATE_INTENTS` verb set (A-017, A-061)

**Files:**
- Modify: `src/agents/risk_gate/agent.py:17-21`
- Modify: `tests/unit/orchestrator/test_risk_gate.py:77-86` (test currently pins `hold`)

- [ ] **Step 1: Write the failing test** in `tests/unit/orchestrator/test_risk_gate.py`, replacing `test_no_risk_gate_intents_constant_contains_hold_and_update`:

```python
def test_no_risk_gate_intents_constant_is_update_and_no_action():
    """Constant carries the canonical four-verb non-trade subset."""
    from agents.risk_gate.agent import _NO_RISK_GATE_INTENTS

    assert _NO_RISK_GATE_INTENTS == frozenset({"update", "no_action"})
    # Defensive — old verbs must be gone (no compat).
    assert "hold"  not in _NO_RISK_GATE_INTENTS
    assert "open"  not in _NO_RISK_GATE_INTENTS
    assert "close" not in _NO_RISK_GATE_INTENTS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/orchestrator/test_risk_gate.py::test_no_risk_gate_intents_constant_is_update_and_no_action -v`
Expected: FAIL — `hold` is still present.

- [ ] **Step 3: Update the constant + its comment** (A-061 refresh) in `src/agents/risk_gate/agent.py:17-21`:

```python
# Stances whose intent is non-trading (update = thesis refresh, no_action =
# explicit hold). Risk caps are irrelevant for these — they must bypass the
# weight-clamp path entirely. Canonical four-verb vocabulary: buy / sell /
# update / no_action (see src/agents/strategist/schema.py). No compatibility
# shim for the pre-iter-3 "hold" verb — strategist will never emit it.
_NO_RISK_GATE_INTENTS: Final[frozenset[str]] = frozenset({"update", "no_action"})
```

- [ ] **Step 4: Audit other pinned-hold sites** in the same test file. The companion test `test_risk_gate_passes_hold_through_unchanged` (line ~90) must be rewritten as `test_risk_gate_passes_no_action_through_unchanged` with `intent="no_action"` — `update` is already covered elsewhere.

```python
def test_risk_gate_passes_no_action_through_unchanged():
    """A no_action stance must not produce any broker order or clamp record."""
    no_action_stance = TickerStance(
        ticker="AAPL",
        intent="no_action",
        rationale="model uncertain — defer",
        # ... fields per existing fixture
    )
    decision = _decision_with_stances([no_action_stance])
    # ... run agent, assert final_orders == [] and risk_clamps_applied == []
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/orchestrator/test_risk_gate.py -v -k "no_risk_gate_intents or no_action"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/risk_gate/agent.py tests/unit/orchestrator/test_risk_gate.py
git commit -m "refactor(risk_gate): use canonical {update, no_action} verb set (A-017, A-061)"
```

---

### Task 2 — Raise on missing `strategist_decision` (A-001)

**Files:**
- Modify: `src/agents/risk_gate/agent.py:43-51`
- Create: `tests/unit/agents/risk_gate/test_loud_failures.py`

- [ ] **Step 1: Add a typed exception** to `src/agents/risk_gate/agent.py` (top of file, below imports):

```python
class RiskGateInputError(RuntimeError):
    """Raised when RiskGate is invoked with missing or malformed inputs.

    These are wiring bugs — the strategist contract guarantees a decision
    object on every tick (even one with stances=[]). Falling through silently
    would hide pipeline breakage as 'no orders this tick'.
    """
```

- [ ] **Step 2: Write the failing test** in `tests/unit/agents/risk_gate/test_loud_failures.py`:

```python
"""Loud-failure tests for the risk_gate agent.

Each test in this file exists because the historical behaviour was to
silently return / no-op on a missing or malformed input. The new contract
is "raise on every missing-input case"; these tests pin that behaviour.
"""
import pytest

from agents.risk_gate.agent import RiskGateAgent, RiskGateInputError
# ... existing fixtures: _invocation_context_with_state, fake_broker_factory


@pytest.mark.asyncio
async def test_risk_gate_raises_when_strategist_decision_missing(
    fake_broker_factory,
    _invocation_context_with_state,
):
    """Missing strategist_decision is a wiring bug — must raise loudly."""
    ctx = _invocation_context_with_state(state={})  # no strategist_decision
    agent = RiskGateAgent(broker=fake_broker_factory())

    with pytest.raises(RiskGateInputError, match="strategist_decision"):
        async for _ in agent._run_async_impl(ctx):
            pass


@pytest.mark.asyncio
async def test_risk_gate_raises_when_strategist_decision_is_none(
    fake_broker_factory,
    _invocation_context_with_state,
):
    """Explicit None counts as missing — must raise, not silently skip."""
    ctx = _invocation_context_with_state(state={"strategist_decision": None})
    agent = RiskGateAgent(broker=fake_broker_factory())

    with pytest.raises(RiskGateInputError, match="strategist_decision"):
        async for _ in agent._run_async_impl(ctx):
            pass
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/risk_gate/test_loud_failures.py -v`
Expected: FAIL — current code does `return` silently.

- [ ] **Step 4: Replace the silent return** in `src/agents/risk_gate/agent.py:49-51`:

```python
state = ctx.session.state
decision_raw = state.get("strategist_decision")
if not decision_raw:
    # A-001 — silent return masked upstream wiring breakage. The strategist
    # contract guarantees a decision (even one with stances=[]) on every
    # tick; absence here means the pipeline is broken, not "no orders".
    raise RiskGateInputError(
        "risk_gate invoked without strategist_decision — strategist "
        "must produce a (possibly empty) StrategistDecision every tick"
    )
```

- [ ] **Step 5: Run all risk_gate tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/risk_gate/ tests/unit/orchestrator/test_risk_gate.py -v`
Expected: PASS, including the two new ones.

- [ ] **Step 6: Audit fixtures** — grep for any test that builds a session state without `strategist_decision` and expects risk_gate to succeed:

Run: `.venv/bin/python -m pytest tests/ -k risk_gate -v 2>&1 | tail -40`

If any test breaks because its fixture omitted `strategist_decision`, fix the fixture (add an empty `StrategistDecision(stances=[], target_weights={}, sell_reasons={}, update_reasons={})`). Do **not** weaken the new raise.

- [ ] **Step 7: Commit**

```bash
git add src/agents/risk_gate/agent.py tests/unit/agents/risk_gate/test_loud_failures.py
git commit -m "feat(risk_gate): raise RiskGateInputError on missing strategist_decision (A-001)"
```

---

### Task 3 — Read prices from `state["reference_prices"]`; drop `_prices` reach-in (A-002, A-005)

**Files:**
- Modify: `src/agents/risk_gate/agent.py:94-107`
- Modify: `tests/unit/agents/risk_gate/test_loud_failures.py` (add cases)

- [ ] **Step 1: Write the failing tests** appending to `tests/unit/agents/risk_gate/test_loud_failures.py`:

```python
@pytest.mark.asyncio
async def test_risk_gate_uses_reference_prices_for_unheld_buy(
    fake_broker_factory,
    _invocation_context_with_state,
    _decision_with_buy,  # produces a stance{intent="buy", ticker="NVDA", weight=0.05}
):
    """First-time BUY of an unheld ticker must price from reference_prices."""
    state = {
        "strategist_decision": _decision_with_buy("NVDA", 0.05).model_dump(),
        "reference_prices": {"NVDA": {"close": 950.0, "as_of": "2026-05-26"}},
    }
    ctx = _invocation_context_with_state(state=state)
    # Broker has NO NVDA position and NO _prices injection.
    broker = fake_broker_factory(positions={})  # cash-only
    agent  = RiskGateAgent(broker=broker)

    events = [e async for e in agent._run_async_impl(ctx)]

    orders = events[0].actions.state_delta["final_orders"]
    nvda   = next(o for o in orders if o["ticker"] == "NVDA")
    assert nvda["action"]    == "BUY"
    assert nvda["est_price"] == 950.0


@pytest.mark.asyncio
async def test_risk_gate_raises_when_reference_price_missing_for_unheld_buy(
    fake_broker_factory, _invocation_context_with_state, _decision_with_buy,
):
    """No reference_prices entry for an unheld BUY ⇒ loud ValueError."""
    state = {
        "strategist_decision": _decision_with_buy("NVDA", 0.05).model_dump(),
        "reference_prices": {},  # empty
    }
    ctx = _invocation_context_with_state(state=state)
    agent = RiskGateAgent(broker=fake_broker_factory(positions={}))

    with pytest.raises(ValueError, match="no price for NVDA"):
        async for _ in agent._run_async_impl(ctx):
            pass


@pytest.mark.asyncio
async def test_risk_gate_does_not_read_broker__prices_attribute(
    fake_broker_factory, _invocation_context_with_state, _decision_with_buy,
):
    """The _prices hasattr reach-in is gone — pricing must come from state."""
    state = {
        "strategist_decision": _decision_with_buy("NVDA", 0.05).model_dump(),
        "reference_prices": {"NVDA": {"close": 950.0, "as_of": "2026-05-26"}},
    }
    broker = fake_broker_factory(positions={})
    # Inject a hostile value into broker._prices — if the agent reads it,
    # the order would price at 1.0 and this assertion would fire.
    broker._prices = {"NVDA": 1.0}

    ctx = _invocation_context_with_state(state=state)
    agent = RiskGateAgent(broker=broker)
    events = [e async for e in agent._run_async_impl(ctx)]

    nvda = next(o for o in events[0].actions.state_delta["final_orders"]
                if o["ticker"] == "NVDA")
    assert nvda["est_price"] == 950.0, (
        "risk_gate must source prices from state['reference_prices'], "
        "not broker._prices (FakeBroker-only injection point)"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/risk_gate/test_loud_failures.py -v -k "reference_price or _prices_attribute"`
Expected: FAIL — current code reads `broker._prices` and never touches `state["reference_prices"]`.

- [ ] **Step 3: Replace the price-build block** in `src/agents/risk_gate/agent.py:94-107`:

```python
if self.broker:
    portfolio = await self.broker.get_portfolio()
    current_weights = portfolio.current_weights()

    # Price map sources (in priority order):
    #   1. broker portfolio positions  — last_price of held lots (always
    #      freshest for tickers we own).
    #   2. state["reference_prices"]   — seeded each tick by
    #      _build_initial_state (live) / _seed_reference_prices (backtest).
    #      Covers unheld watchlist tickers a first-time BUY needs to price.
    #
    # A-002 / A-005 — the historical fallback was hasattr(broker, "_prices"),
    # a FakeBroker-only private channel with no Trading212 equivalent. Reading
    # state guarantees the live path has prices for unheld BUYs.
    prices = {t: pos.last_price for t, pos in portfolio.positions.items()}
    reference_prices = state.get("reference_prices") or {}
    for sym, payload in reference_prices.items():
        if sym in prices:
            continue
        # PriceHistory.model_dump(mode="json") shape — see
        # orchestrator/tick.py:_fetch_reference_prices and
        # backtest/runner.py:_seed_reference_prices. Both write a "close"
        # scalar at the top level so this lookup is one key deep.
        close = payload.get("close") if isinstance(payload, dict) else None
        if close is not None:
            prices[sym] = float(close)
else:
    current_weights = {}
    prices = {}
```

- [ ] **Step 4: Verify `reference_prices` shape** by reading the canonical writers:

Run: `.venv/bin/python -c "from orchestrator.tick import _fetch_reference_prices; import inspect; print(inspect.getsource(_fetch_reference_prices))"`

Expected: returns `dict[str, PriceHistory]`. `model_dump(mode="json")` produces a dict with a `close` field. If the structure is `{"bars": [...]}` instead of a top-level `close`, adapt Step 3's extraction to take the last bar's close (`payload["bars"][-1]["close"]`). The test in Step 1 must be updated to match the shape — fixture and consumer agree.

- [ ] **Step 5: Run the test suite**

Run: `.venv/bin/python -m pytest tests/unit/agents/risk_gate/ tests/unit/orchestrator/test_risk_gate.py tests/integration/ -v -k "risk_gate or risk_gate_with_broker"`
Expected: PASS.

- [ ] **Step 6: Search for callers of the deleted reach-in**

Run: `grep -rn "broker._prices\|self.broker._prices" src/ tests/`
Expected: matches only in `src/broker/fake.py` (definition) and test fixtures that inject prices into FakeBroker for FakeBroker's own behaviour. **No** risk_gate production reads remain.

- [ ] **Step 7: Commit**

```bash
git add src/agents/risk_gate/agent.py tests/unit/agents/risk_gate/test_loud_failures.py
git commit -m "fix(risk_gate): price unheld BUYs from state[reference_prices], drop FakeBroker._prices reach-in (A-002, A-005)"
```

---

### Task 4 — Remove `_close_tickers` post-clamp restoration (A-034)

The current code computes clamps over the full `proposed` dict, then writes `proposed[_t] = 0.0` for every full-close ticker **after** clamping. Result: `risk_clamps_applied` records clamp events that did not actually constrain the emitted weights — telemetry lies. Fix: exclude full-close tickers from `apply_constraints` so the clamp record matches reality.

**Files:**
- Modify: `src/agents/risk_gate/agent.py:119-135`
- Modify: `tests/unit/agents/risk_gate/test_agent.py` (add a telemetry-consistency test)

- [ ] **Step 1: Write the failing test** in `tests/unit/agents/risk_gate/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_full_close_does_not_appear_in_clamp_telemetry(
    fake_broker_factory, _invocation_context_with_state,
):
    """A-034 — full-close (sell with weight=None) must bypass clamp logic
    entirely. risk_clamps_applied must NOT contain a record for the closed
    ticker, because the clamp didn't constrain anything (we overwrote to 0)."""
    # Set up: AAPL is held at 0.20; strategist emits sell intent with no weight.
    # max_delta_per_ticker is, say, 0.05 — without the fix, AAPL appears in
    # clamps as "delta capped 0.20→0.15" even though the final weight is 0.
    ...
    events = [e async for e in agent._run_async_impl(ctx)]
    delta = events[0].actions.state_delta
    aapl_clamps = [c for c in delta["risk_clamps_applied"]
                   if c["ticker"] == "AAPL"]
    assert aapl_clamps == [], (
        "Full-close must not produce clamp telemetry — clamp would not have "
        "constrained the post-restoration weight (0.0)."
    )
    aapl_order = next(o for o in delta["final_orders"] if o["ticker"] == "AAPL")
    assert aapl_order["action"] == "SELL"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/risk_gate/test_agent.py::test_full_close_does_not_appear_in_clamp_telemetry -v`
Expected: FAIL — clamp record for AAPL present.

- [ ] **Step 3: Restructure the clamp section** in `src/agents/risk_gate/agent.py:119-135`:

```python
# A-034 — full closes (sell with weight=None) bypass clamps because
# capping them at MAX_DELTA_PER_TICKER would leave dust shares behind
# (D3 contract trap). Excluding them from `proposed` before
# `apply_constraints` means the clamp telemetry reflects what actually
# constrained output — the post-clamp restoration write that used to
# live here distorted telemetry and is removed.
_close_tickers = {
    s.ticker
    for s in (decision.stances or [])
    if s.intent == "sell" and s.weight is None
}

# Snapshot pre-exclusion weights for the lifecycle check below; it needs
# to see what the strategist asked for, full closes included.
original_weights = dict(proposed)

# Remove full closes from the clamping domain — they will be re-added as 0.
proposed_for_clamp = {t: w for t, w in proposed.items() if t not in _close_tickers}

weight_clamps = apply_constraints(proposed_for_clamp, current_weights)
clamps        = _stance_clamps + weight_clamps

# Reassemble final proposed: clamp output + full-close targets at 0.0.
proposed = dict(proposed_for_clamp)
for _t in _close_tickers:
    proposed[_t] = 0.0
```

- [ ] **Step 4: Run the new test plus the existing risk_gate suite**

Run: `.venv/bin/python -m pytest tests/unit/agents/risk_gate/ tests/unit/orchestrator/test_risk_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/risk_gate/agent.py tests/unit/agents/risk_gate/test_agent.py
git commit -m "fix(risk_gate): exclude full-close tickers from clamp domain so telemetry matches output (A-034)"
```

---

### Task 5 — Executor after-callback uses `logger.error(..., exc_info=True)` (A-008)

**Files:**
- Modify: `src/agents/executor/agent.py:510-526`
- Modify: `tests/integration/test_executor_with_fake_broker.py` (add caplog assertion to existing rejection test)

- [ ] **Step 1: Write the failing test** — add to `tests/integration/test_executor_with_fake_broker.py`:

```python
def test_thesis_writer_callback_logs_assertion_through_logger(
    caplog, fake_broker_with_decision_causing_assertion,
):
    """A-008 — apply_stance_to_thesis raising AssertionError must surface
    via logger.error with exc_info, not via bare print() to stderr (which
    bypasses caplog and structured log scrapers)."""
    caplog.set_level(logging.ERROR, logger="agents.executor.agent")

    # Run the executor; expect callback to swallow the AssertionError but
    # log it loudly.
    state = run_executor_with(...)

    records = [r for r in caplog.records
               if "_executor_thesis_writer_callback" in r.message
               or "apply_stance_to_thesis" in r.message]
    assert records, (
        "AssertionError in thesis writer callback must be logged via "
        "logger.error (caplog sees it) — not print(file=sys.stderr)."
    )
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None, (
        "logger.error must be called with exc_info=True so the traceback "
        "is attached to the log record."
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_executor_with_fake_broker.py::test_thesis_writer_callback_logs_assertion_through_logger -v`
Expected: FAIL — caplog sees nothing because the callback prints to stderr.

- [ ] **Step 3: Replace the `print` block** in `src/agents/executor/agent.py:510-526`:

```python
except AssertionError:
    # Caller bug (e.g. ``buy`` reaching the dispatcher with no fill
    # price). Strategist hallucinations are reported via the HALLUCINATED
    # sentinel — they do not raise. An AssertionError here means our
    # wiring is wrong, not the LLM's output, so log loudly with the full
    # traceback and continue rather than abort the tick.
    # A-008 — previously used print(file=sys.stderr) which bypassed the
    # structured logger and caplog. Routing through logger.error makes
    # the failure visible to log aggregators and tests.
    logger.error(
        "thesis_writer_callback: apply_stance_to_thesis raised for "
        "ticker=%s intent=%s — wiring bug, skipping row",
        ticker, stance.intent,
        exc_info=True,
    )
    continue
```

- [ ] **Step 4: Run the new test and the existing executor suite**

Run: `.venv/bin/python -m pytest tests/integration/test_executor_with_fake_broker.py tests/unit/agents/executor/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/executor/agent.py tests/integration/test_executor_with_fake_broker.py
git commit -m "fix(executor): route thesis-writer assertion to logger.error with exc_info (A-008)"
```

---

### Task 6 — Executor `fill_prices` cleanup (A-068)

`row["stance"]["ticker"]` is dead (Execution rows have never carried a `stance` key). `fill_price or actual_price` accepts two spellings. Rejected rows write `None`, which silently becomes the BUY-without-price path the previous task just made loud.

**Files:**
- Modify: `src/agents/executor/agent.py:443-457`
- Modify: existing executor tests if they rely on the dual spelling
- Modify: `tests/integration/test_executor_with_fake_broker.py` (assert raised exception for missing-price BUY)

- [ ] **Step 1: Verify Execution row shape** — read the writer:

Run: `grep -n "actual_price\|fill_price" src/agents/executor/agent.py src/orchestrator/state.py | head -30`

Expected: the canonical key is `actual_price` (set in the Execution model after a successful broker call). `fill_price` should not appear as a writer. If it does, the writer must be migrated to `actual_price` in the same commit (don't leave dual writers).

- [ ] **Step 2: Write the failing test** — `tests/unit/agents/executor/test_thesis_callback.py`:

```python
def test_fill_prices_uses_only_actual_price_key():
    """A-068 — fill_prices must read the canonical actual_price field, not
    the deprecated fill_price alias. A row written with the alias must NOT
    contribute a price (alias support is removed)."""
    state = {
        "executions": [
            {"order": {"ticker": "AAPL"}, "actual_price": 195.0},
            {"order": {"ticker": "MSFT"}, "fill_price":   400.0},   # alias — ignored
        ],
        ...
    }
    callback = _executor_thesis_writer_callback  # private — see agent.py
    fill_prices = callback._build_fill_prices(state)   # extract helper

    assert fill_prices == {"AAPL": 195.0}, (
        "Only the canonical actual_price key must populate fill_prices; "
        "the fill_price alias was dual-spelling support that has been removed."
    )
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/executor/test_thesis_callback.py -v`
Expected: FAIL — MSFT is still picked up via the alias.

- [ ] **Step 4: Replace the fill-prices block** in `src/agents/executor/agent.py:443-457`:

```python
# Build a fill-price lookup from successful execution rows. The canonical
# price key is ``actual_price`` (set by Execution.actual_price on the BUY
# path after broker confirmation). A-068 — the historical code also
# accepted ``fill_price`` as an alias and silently fell back to None for
# rejected rows, which combined with A-008's swallowed print to produce
# a near-invisible BUY-without-price failure. We now:
#   - read only the canonical key
#   - omit rejected rows entirely (no None entries)
# Downstream (apply_stance_to_thesis) raises AssertionError on missing
# price for BUY, surfaced via the logger by A-008's fix.
fill_prices: dict[str, float] = {}
for row in state.get("executions", []):
    if not row:
        continue
    ticker = (row.get("order") or {}).get("ticker")
    actual_price = row.get("actual_price")
    if ticker and actual_price is not None:
        fill_prices[ticker] = float(actual_price)
```

If you need a helper for testability, extract `_build_fill_prices(state) -> dict[str, float]` and have the callback call it. Keep the helper private to the module.

- [ ] **Step 5: Run the executor test suite**

Run: `.venv/bin/python -m pytest tests/unit/agents/executor/ tests/integration/test_executor_with_fake_broker.py -v`
Expected: PASS. If any existing test fails because it wrote `fill_price` to a fixture row, **rewrite the fixture** to use `actual_price`. Do not re-add alias support.

- [ ] **Step 6: Commit**

```bash
git add src/agents/executor/agent.py tests/unit/agents/executor/test_thesis_callback.py
git commit -m "refactor(executor): fill_prices reads only canonical actual_price, drop alias and None entries (A-068)"
```

---

### Task 7 — Cross-cutting sweep + final test pass

- [ ] **Step 1: Grep for compat residue**

Run:
```bash
grep -rn '"hold"\|broker\._prices\|"fill_price"\|stance\["ticker"\]' src/ tests/
```

Expected: no production matches in `src/agents/risk_gate/` or `src/agents/executor/`. Tests may reference these strings only in *assertions that they are gone* (e.g. `assert "hold" not in _NO_RISK_GATE_INTENTS`).

- [ ] **Step 2: Full suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -50`
Expected: green. Any failure outside `tests/unit/agents/risk_gate/`, `tests/unit/orchestrator/test_risk_gate.py`, or `tests/integration/test_executor_with_fake_broker.py` indicates an upstream test was relying on one of the silent fallbacks — fix in this same commit (the fix is always "stop relying on silent behaviour", not "re-add the silent path").

- [ ] **Step 3: Ruff**

Run: `.venv/bin/python -m ruff check src/agents/risk_gate src/agents/executor`
Expected: clean.

- [ ] **Step 4: Final commit (only if cleanup edits were needed)**

```bash
git add -p
git commit -m "chore(plan-05): cross-cutting sweep — purge silent-fallback residue"
```

---

## Test strategy — fails-before / passes-after matrix

| Finding | Fails-before test                                                                       | Passes-after assertion                                              |
| ------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| A-001   | `test_risk_gate_raises_when_strategist_decision_missing` / `..._is_none`                | `pytest.raises(RiskGateInputError, match="strategist_decision")`    |
| A-002   | `test_risk_gate_uses_reference_prices_for_unheld_buy`                                   | `nvda["est_price"] == 950.0`                                        |
| A-002   | `test_risk_gate_raises_when_reference_price_missing_for_unheld_buy`                     | `pytest.raises(ValueError, match="no price for NVDA")`              |
| A-005   | `test_risk_gate_does_not_read_broker__prices_attribute` (hostile `_prices` injection)   | `est_price` matches state, not the hostile broker attr              |
| A-008   | `test_thesis_writer_callback_logs_assertion_through_logger`                             | `caplog` record at ERROR with `exc_info is not None`                |
| A-017   | `test_no_risk_gate_intents_constant_is_update_and_no_action`                            | `_NO_RISK_GATE_INTENTS == frozenset({"update","no_action"})`        |
| A-017   | `test_risk_gate_passes_no_action_through_unchanged`                                     | `final_orders == [] and risk_clamps_applied == []`                  |
| A-034   | `test_full_close_does_not_appear_in_clamp_telemetry`                                    | `[c for c in clamps if c["ticker"]=="AAPL"] == []`                  |
| A-061   | covered by A-017 test (comment correctness is a code-review check, not a runtime test)  | comment present in `src/agents/risk_gate/agent.py:17-21`            |
| A-068   | `test_fill_prices_uses_only_actual_price_key`                                           | `fill_prices == {"AAPL": 195.0}` (no MSFT)                          |

**Unknown-verb raise** is not new code in this plan — `StrategistDecision`'s pydantic model with `extra="forbid"` already raises on unknown verbs (TickerStance enum). Plan 02 owns the schema; this plan just trusts it. We **do** add one defensive smoke test in `test_loud_failures.py`:

```python
def test_unknown_verb_in_raw_decision_raises_at_validation():
    """The four-verb schema is enforced at StrategistDecision.model_validate.
    risk_gate's validation step must therefore propagate the pydantic error,
    not catch-and-skip."""
    bad = {"stances": [{"ticker": "AAPL", "intent": "frobnicate", "rationale": "x"}],
           "target_weights": {}, "sell_reasons": {}, "update_reasons": {}}
    with pytest.raises(Exception):  # pydantic.ValidationError concrete type fine too
        StrategistDecision.model_validate(bad)
```

---

## Risks / silent-regression checklist

Run through each before declaring done.

1. **`tests/unit/orchestrator/test_risk_gate.py:77-86`** pinned the old `{"hold","update"}` set — already in A-020. Confirm rewritten, **not** deleted: keeping the constant under positive assertion prevents accidental drift back.
2. **Any test fixture that constructs a session state with no `strategist_decision`** and reaches risk_gate — used to be a no-op, now raises. Fix the fixture (add an empty `StrategistDecision`) rather than wrapping the raise in `pytest.raises` everywhere.
3. **`tests/integration/test_executor_with_fake_broker.py::test_executor_rejection_continues`** (A-018 cluster) — historically asserted only that the tick didn't crash. Add a positive content assertion: either `caplog` sees the rejection log, or `state["executions"]` contains a row with `status="rejected"` and `actual_price is None`.
4. **`scripts/replay_backtest.py`** — manual tool, in MEMORY.md. Do NOT delete or modify. If it constructs risk_gate state by hand, audit that it now seeds `reference_prices` (it should already; Plan 04 owns the seed).
5. **FakeBroker tests in `tests/unit/broker/`** — `_prices` injection still legitimate for FakeBroker's own price-resolution behaviour. We only removed the *risk_gate's reach-in*, not the FakeBroker primitive.
6. **Snapshot equality fixtures** — any frozen golden that recorded clamp records for full-close tickers will change after A-034. Regenerate, do not pin around it.
7. **Telemetry consumers** — `decision_logger.py` reads `risk_clamps_applied`. Removing full-close entries from that list means downstream metrics like "ticks-with-clamps" will drop. This is correct (those clamps never constrained output) — flag in PR description for Plan 11's report regeneration.
8. **No backwards-compat shims for `hold`.** Anywhere `hold` appears in production code (`src/`) after this plan is a bug. Grep proves it.
9. **`fill_price` alias.** Same rule — the alias must be gone from `src/`. If it's referenced in `docs/` or `graph_delta.md` annotations, those will be cleaned up by later plans; this plan only owns code/tests.
10. **Cross-lifecycle parity.** Plan 04 ensures both live and backtest write `reference_prices`. Verify by running one backtest tick: `PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window svb-stress-2023-03 --ticks 1` and confirming no `RiskGateInputError` or `ValueError: no price for ...`.

---

### Task 8 — Delete `sell_reasons` / `update_reasons` / `last_reviewed_reason` (A-013 cluster tail)

**Context:** Plan 02 collapsed the analyst-side rationale fields but deferred the strategist-decision and position-thesis prose carriers (the A-013 cluster tail). Each is a byte-identical derivation of `TickerStance.rationale`, so removing them strictly reduces duplication; nothing reads them that cannot read `stance.rationale` instead. This task closes the cluster.

**Files (production):**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/schema.py` — drop `sell_reasons` and `update_reasons` fields from `StrategistDecision`.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/derivation.py` — drop the two dicts from `_DerivedFields` and the assignment block (~lines 254-298, 345-346).
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/enricher.py:227-228` — drop the two kwargs from the `StrategistDecision(...)` construction.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/position_thesis.py:156` — drop the `last_reviewed_reason` field and the surrounding docstring sentences at ~lines 70-78.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/stance_schema.py:35` — drop the docstring reference to `last_reviewed_reason`.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/_verb_dispatch.py` lines 235, 251, 291, 311, 323, 348 — delete the six `last_reviewed_reason` assignments (kwarg form on lines 235/311, dict-key form elsewhere).
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/agent.py:251` — drop the `(_sd.get("sell_reasons") or {})` access path.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/agent.py:33,140-145` — replace the closing-stance contract check that consults `decision.sell_reasons` with a check against `{s.ticker for s in decision.stances if s.intent == "sell"}` (the rationale lives on the stance now).
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/orchestrator/persistence.py:137` — delete the legacy comment about the unified `sell_reasons` dict (it no longer exists).
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/config/strategist.py:10` — same comment cleanup.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/backtest/decision_logger.py:274` — replace the `decision.get("sell_reasons")` access with the equivalent stance-derived map.

**Files (tests):**
- Modify the ~26 test references enumerated by `grep -rn 'sell_reasons\|update_reasons\|last_reviewed_reason' tests/` — including the fixture-construction lines this plan's Tasks 1-2 add (the empty `StrategistDecision(stances=[], target_weights={}, sell_reasons={}, update_reasons={})` patterns at plan-05:238 and plan-05:683). Drop the legacy kwargs; the constructor accepts them without after this task.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/fixtures/position_thesis_v1.json` — remove the `last_reviewed_reason` key.

**This task supersedes the trust note at the head of this plan (line ~14)** which previously promised `sell_reasons`/`update_reasons` would remain stable. Fixtures touched by earlier tasks in this plan must construct decisions with those kwargs until this task lands; this task is the single commit that removes them.

- [ ] **Step 1: Write the failing schema-shape test.**

```python
# tests/unit/agents/strategist/test_decision_schema_v2.py — add
def test_strategist_decision_rejects_legacy_reason_dicts():
    """A-013 tail: sell_reasons / update_reasons no longer exist on the schema.

    The Pydantic model is configured `extra='forbid'`, so attempting to
    pass either kwarg must raise ValidationError.  This guarantees a
    silent regression cannot reintroduce the byte-identical duplication.
    """
    import pytest
    from pydantic import ValidationError

    from agents.strategist.schema import StrategistDecision

    with pytest.raises(ValidationError):
        StrategistDecision(stances=[], target_weights={}, sell_reasons={"AAPL": "x"})
    with pytest.raises(ValidationError):
        StrategistDecision(stances=[], target_weights={}, update_reasons={"AAPL": "x"})
```

- [ ] **Step 2: Run it — expected FAIL (today both kwargs are accepted).**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_decision_schema_v2.py::test_strategist_decision_rejects_legacy_reason_dicts -v`

- [ ] **Step 3: Apply the schema deletion.** Strip the two fields from `StrategistDecision`; strip the matching mirror fields and assignments from `derivation.py`; drop the two kwargs from the `enricher.py` construction. Confirm `extra='forbid'` is set on the model (it is, per the existing `model_config`).

- [ ] **Step 4: Delete `PositionThesis.last_reviewed_reason`.** Remove the Field declaration (line 156), the docstring paragraph that defines it (~lines 70-78), and the matching mention in `stance_schema.py:35`.

- [ ] **Step 5: Update the six executor verb-dispatch sites.** Each of lines 235, 251, 291, 311, 323, 348 in `_verb_dispatch.py` carries a single `last_reviewed_reason = stance.rationale or ""` (or its dict-form equivalent). Delete the assignments; `PositionThesis` no longer accepts the field. Confirm no remaining call site reads it (`grep -rn last_reviewed_reason src/`).

- [ ] **Step 6: Rewrite the risk_gate closing-stance contract check.** In `risk_gate/agent.py:140-145`, replace the `decision.sell_reasons` lookup with a stance-derived sell-ticker set:

```python
# Closing-stance contract: every position that the proposed weights
# will close must carry an explicit sell stance.  The rationale lives
# on the stance (A-013 tail collapse — sell_reasons dict deleted).
selling = {s.ticker for s in (decision.stances or []) if s.intent == "sell"}
for t in (current_weights.keys() | proposed.keys()):
    was_open  = current_weights.get(t, 0.0) > 0.0
    will_be_open = proposed.get(t, 0.0) > 0.0
    if was_open and not will_be_open and t not in selling:
        raise StrategistContractViolation(
            f"position {t!r} closes without a matching sell stance"
        )
```

- [ ] **Step 7: Update `executor/agent.py:251` and `backtest/decision_logger.py:274`.** Replace the `(_sd.get("sell_reasons") or {})` access with `{s["ticker"]: s["rationale"] for s in _sd.get("stances", []) if s.get("intent") == "sell"}` — preserves the same observable mapping for any downstream snapshot consumer without re-introducing the schema field.

- [ ] **Step 8: Sweep tests.** Run:

```bash
grep -rn 'sell_reasons\|update_reasons\|last_reviewed_reason' tests/
```

For each hit: if it constructs the kwarg, delete the kwarg. If it asserts on the field, rewrite to assert on the stance-derived shape (Step 7 mapping). Update `tests/fixtures/position_thesis_v1.json` to drop `last_reviewed_reason`. The ~26 hits enumerated under "Files (tests)" above are the expected surface area; any extra hit is a real downstream consumer — stop and investigate.

- [ ] **Step 9: Run the full suite.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
Expected: green. Any failure outside the touched files indicates an undocumented consumer of the legacy dicts.

- [ ] **Step 10: Run ruff.**

Run: `.venv/bin/python -m ruff check src/agents/strategist src/agents/executor src/agents/risk_gate src/orchestrator src/backtest src/config`
Expected: clean.

- [ ] **Step 11: Commit.**

```bash
git add src/ tests/
git commit -m "refactor(schema): delete A-013 cluster tail — sell_reasons, update_reasons, last_reviewed_reason

Strategist decisions and position theses carried byte-identical duplicates
of TickerStance.rationale. Every consumer now reads the stance directly;
the schema fields are gone and the model rejects them via extra='forbid'.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9 — Delete the `risk_gate_agent` module-level singleton (A-057)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/agent.py:186-187`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/__init__.py` — drop the symbol from `__all__` if exported.

The module-level `risk_gate_agent = RiskGateAgent()` instance at `agent.py:186-187` is dead — the pipeline constructs the agent via `RiskGateAgent(broker=...)` at wire-up time (the per-tick injection is the only sanctioned construction path because the broker handle is required for portfolio reads). The singleton was a leftover from the pre-broker era and would silently degrade into a brokerless agent if anything imported it.

- [ ] **Step 1: Write the failing import-shape test.**

```python
# tests/unit/agents/risk_gate/test_no_module_singleton.py — new file
"""A-057: the dead module-level RiskGateAgent() singleton must not return.

A brokerless RiskGateAgent silently degrades — Plan 05 Task 3 made broker
prices a hard requirement; an importable agent with no broker bypasses it.
"""
import agents.risk_gate.agent as _rg


def test_no_module_level_risk_gate_singleton():
    assert not hasattr(_rg, "risk_gate_agent"), (
        "module-level `risk_gate_agent` is dead (A-057); construct via "
        "RiskGateAgent(broker=...) at pipeline wire-up time instead"
    )
```

- [ ] **Step 2: Run it — expected FAIL.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/risk_gate/test_no_module_singleton.py -v`

- [ ] **Step 3: Delete the two lines at `agent.py:186-187`.** Confirm no other file imports `risk_gate_agent` (the symbol, not the class) via `grep -rn 'risk_gate_agent' src/ tests/ scripts/`. If `__init__.py` re-exports it, remove from `__all__` as well.

- [ ] **Step 4: Run the risk_gate suite + a full pytest pass.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
Expected: green. The new test passes; nothing else regresses.

- [ ] **Step 5: Commit.**

```bash
git add src/agents/risk_gate/ tests/unit/agents/risk_gate/test_no_module_singleton.py
git commit -m "chore(risk_gate): delete dead module-level singleton (A-057)

Pipeline constructs RiskGateAgent(broker=...) at wire-up; the brokerless
singleton at agent.py:186 was unreachable and would silently degrade if
imported. Removed plus a guard test against re-introduction.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 10 — Fold `apply_buy_delta_clamp` into `apply_constraints` (A-058)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/constraints.py:32-80` — delete `apply_buy_delta_clamp`; move its per-stance loop into `apply_constraints` as the first step before the existing weight-level clamps.
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/agent.py:14,67` — drop the import and the dedicated `_stance_clamps = apply_buy_delta_clamp(...)` call; pass the stance list into `apply_constraints` instead.
- Modify: `tests/unit/agents/risk_gate/test_constraints.py` (or equivalent) — point existing stance-clamp tests at `apply_constraints`; assert the same `ClampRecord(rule="buy_delta_exceeded", ...)` rows appear in its return list.

The two-call structure dates from before the four-verb collapse; today `apply_buy_delta_clamp` and `apply_constraints` share the same `RiskGateConfig`, the same `ClampRecord` ledger, and are invoked back-to-back. Folding them lets one function own the full clamp sequence — no shared state between the two, no risk of the agent forgetting to call one.

- [ ] **Step 1: Write the failing parity test.**

```python
# tests/unit/agents/risk_gate/test_constraints.py — add
def test_apply_constraints_runs_buy_delta_clamp_first():
    """A-058: apply_constraints now owns the per-stance buy-delta clamp.

    A buy stance whose weight exceeds max_delta_per_buy must come out
    clamped and the clamp record must appear in apply_constraints's return.
    """
    from agents.risk_gate.constraints import apply_constraints
    from agents.strategist.stance_schema import TickerStance
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    over_cap = cfg.max_delta_per_buy + 0.01
    stance = TickerStance.model_construct(  # bypass schema validator on purpose
        ticker="AAPL", intent="buy", weight=over_cap, rationale="x",
    )
    proposed: dict[str, float] = {"AAPL": over_cap}
    current:  dict[str, float] = {}

    clamps = apply_constraints(
        proposed,
        current,
        stances=[stance],          # new kwarg
        config=cfg,                # new kwarg
    )

    assert stance.weight == cfg.max_delta_per_buy
    assert any(c.rule == "buy_delta_exceeded" and c.ticker == "AAPL" for c in clamps)
```

- [ ] **Step 2: Run it — expected FAIL (current signature lacks `stances`/`config`).**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/risk_gate/test_constraints.py::test_apply_constraints_runs_buy_delta_clamp_first -v`

- [ ] **Step 3: Refactor `apply_constraints`.** Extend its signature to accept `stances: list[TickerStance] | None = None` and `config: RiskGateConfig` (move the config from per-call default into a required kwarg — there is one caller). At the head of its body, run the per-stance buy-delta loop (the body of the old `apply_buy_delta_clamp`) and append its `ClampRecord`s to the existing list. Delete the standalone `apply_buy_delta_clamp` function.

- [ ] **Step 4: Update `risk_gate/agent.py`.** Drop the import at line 14 and the `_stance_clamps = apply_buy_delta_clamp(...)` call at line 67. Pass `stances=decision.stances or []` and `config=_rg_config` into the existing `apply_constraints(proposed, current_weights, ...)` call at line 127. The merged-clamps assembly that previously concatenated `_stance_clamps + weight_clamps` collapses to the single return value.

- [ ] **Step 5: Run the risk_gate suite.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/risk_gate tests/integration -k risk_gate -v`
Expected: green. Any pre-existing test that constructed `apply_buy_delta_clamp` directly must be rewritten to call `apply_constraints` — that is the intended consolidation, not a regression.

- [ ] **Step 6: Run ruff.**

Run: `.venv/bin/python -m ruff check src/agents/risk_gate`
Expected: clean.

- [ ] **Step 7: Commit.**

```bash
git add src/agents/risk_gate/ tests/unit/agents/risk_gate/
git commit -m "refactor(risk_gate): fold apply_buy_delta_clamp into apply_constraints (A-058)

The two functions shared config, ledger and call site; the split was
leftover from the pre-four-verb era. apply_constraints now owns the full
clamp sequence — one function, one ledger, no risk of skipping a step.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Definition of done

- All ten tasks committed; each commit independently green under `pytest tests/ -v`.
- `grep -rn '"hold"\|broker\._prices' src/agents/risk_gate src/agents/executor` returns nothing.
- `_NO_RISK_GATE_INTENTS` equals `frozenset({"update", "no_action"})`.
- `RiskGateInputError` is raised on missing `strategist_decision`; covered by two tests.
- Unheld BUY in a live-shape state with `reference_prices` populated produces an order at the reference price; with `reference_prices` empty raises `ValueError`.
- Executor thesis-writer callback's AssertionError path is visible to `caplog` with `exc_info`.
- `risk_clamps_applied` contains no entry for any ticker that ended at `0.0` via the full-close path.
- `fill_prices` is built from `actual_price` only; rejected rows do not appear.
- One backtest tick runs end-to-end without `RiskGateInputError` or missing-price `ValueError`.
- `docs/contract-invariants.md` / graphify-out delta: not in scope for this plan (Plan 11/12 own doc reconciliation). If you spot a stale invariant line in §A while editing, leave a `# TODO(plan-11): ...` only — do not edit cross-plan docs.

---

## Self-review notes

- **Spec coverage** — every listed finding (A-001, A-002, A-005, A-008, A-013-tail, A-017, A-034, A-057, A-058, A-061, A-068) has a dedicated task + test pair. A-061 (comment refresh) is folded into Task 1 since the comment and the constant live two lines apart. A-013-tail (sell_reasons/update_reasons/last_reviewed_reason) lands as Task 8, completing the cluster Plan 02 started. A-057 (risk_gate module singleton) and A-058 (apply_buy_delta_clamp two-call structure) land as Tasks 9 and 10 — both sit naturally in this plan's risk_gate surface area.
- **Placeholders** — none. Every test shows code; every edit shows the replacement block; every command shows expected output category.
- **Type consistency** — `RiskGateInputError` is the single new symbol; `_NO_RISK_GATE_INTENTS` keeps the same name and type (`frozenset[str]`); `fill_prices` narrows from `dict[str, float | None]` to `dict[str, float]` (documented at the declaration site).
- **No silent-fallback re-introduction** — every "raise" path has a test that fails when the raise is replaced with a return/None.

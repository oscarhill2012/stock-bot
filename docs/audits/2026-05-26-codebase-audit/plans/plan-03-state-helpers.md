# Plan 03 — State-key & Portfolio-helper Consolidation

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal.** Collapse the ad-hoc portfolio/positions/thesis state-access vocabulary
into one canonical helper, one canonical state shape, and zero bare-key
fallbacks — so later plans can read `state["portfolio"]` / `state["user:positions"]`
without defensive shape-checking.

**Architecture.** Introduce a single private helper `_coerce_portfolio` on
`broker.portfolio.Portfolio` (classmethod `from_state_value`) and a tiny
`agents._state_access` reader module that exposes `read_positions(state)` and
`read_thesis(state)`. Every consumer migrates onto these two doors. The bare
keys (`state["positions"]`, `state["cash"]`, `state["thesis"]`,
`state["user:thesis"]`) and their `or`-chain fallbacks are deleted. The
in-tick BUY → SELL bridge stays — but moves to a clearly-named
`temp:executor_positions_bridge` key (executor-internal, not consumed by
external readers).

**Tech stack.** Python 3.12, Pydantic v2, Google ADK session state, pytest.

---

## Trust contract

**Trusts (from Plans 01–02 already landed):**
- Rationale/verdict vocabulary is canonical (no `reason` / `catalyst` /
  `summary` aliases left).
- `PositionThesis` carries only `rationale`/`last_reviewed_reason` (already
  collapsed in commit `742f38e`).

**This plan promises to later plans:**
- Exactly **one** `_coerce_portfolio` implementation, exposed as
  `Portfolio.from_state_value(value)`.
- Exactly **one** canonical state shape: `state["portfolio"]` is the
  `Portfolio.model_dump(mode="json")` working copy refreshed at Phase 2;
  `state["user:positions"]` is the persistent thesis-book (dict[ticker →
  PositionThesis dump]); `state["user:thesis"]` is the standing-thesis string.
- **Zero** bare-key fallbacks for `positions`, `cash`, `thesis` in any
  consumer outside the executor's in-tick bridge.
- Helpers **raise** on missing/malformed `state["portfolio"]` — they do not
  paper over with empty defaults. Cold-start callers are responsible for
  seeding `state["portfolio"]` explicitly.
- Plans 04 (as_of boundary), 05, and 10 may assume the above without
  defensive shape-checking.

---

## 1 — Canonical state shape

| Key                                | Type                                          | Writer-of-record                                                            | Readers (post-plan)                                                                                  |
|------------------------------------|-----------------------------------------------|-----------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| `state["portfolio"]`               | `Portfolio.model_dump(mode="json")` dict      | Live: `orchestrator/tick.py::build_initial_tick_state` (Phase 2 seed). Backtest: `backtest/driver.py` (per-tick refresh). | strategist context-shim, strategist enricher, risk_gate, snapshotter, decision_logger, reporting. **Always present.** |
| `state["user:positions"]`          | `dict[ticker → PositionThesis dump]`          | `_executor_thesis_writer_callback` (after_agent_callback).                  | strategist context-shim, decision_logger, reporting, persistence.                                    |
| `state["user:thesis"]`             | `str` (standing thesis)                       | `_executor_thesis_writer_callback`.                                         | strategist context-shim, persistence.                                                                |
| `state["temp:executor_positions_bridge"]` | `dict[ticker → PositionThesis dump]` (in-tick) | Executor `_run_async_impl` (intra-tick BUY → SELL).                       | Executor `_run_async_impl` **only** — single-file scope.                                             |

**Deleted keys / fallbacks:**
- `state["positions"]` (bare key) — replaced by the temp-namespaced bridge
  for in-tick use, and `user:positions` everywhere else.
- `state["cash"]` (bare key) — was never written, but the audit found
  fallback reads. Deleted.
- `state["thesis"]` (bare key) — A-086 residue. Deleted.
- All `state.get("user:X") or state.get("X")` `or`-chain expressions.

**Ownership rule (single-writer).** The four canonical keys each have one
writer-of-record listed above. Plans 04+ may not introduce additional
writers without amending this table.

**Mid-tick `broker.get_portfolio()` rule (A-072).** Only **two** call sites
remain after this plan:
1. Phase 2 seed (`orchestrator/tick.py::build_initial_tick_state` and
   `backtest/driver.py`'s per-tick refresh).
2. Executor SELL-close confirmation in
   `agents/executor/agent.py:205` — the broker is the only honest source
   for post-fill remaining-quantity, see the comment at agent.py:196-201.
   This call is **retained** (the audit explicitly flagged it as legitimate;
   only the duplicate calls in risk_gate and snapshotter are removed).

Risk_gate and snapshotter migrate to reading `state["portfolio"]` via
`Portfolio.from_state_value`.

---

## 2 — Ordered changes

The order is **strict**: helper first (additive), then call-site migration
(behaviour-preserving), then deletion (the actual cleanup). Each task is
self-contained and committable.

---

### Task 1 — Add `Portfolio.from_state_value` classmethod

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/broker/portfolio.py`
- Test:   `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/broker/test_portfolio_from_state_value.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/broker/test_portfolio_from_state_value.py
"""Tests for the canonical Portfolio.from_state_value coercion helper.

This helper is the *only* sanctioned way to coerce a state["portfolio"]
value into a Portfolio instance — see plan-03-state-helpers.md §1.
"""
from __future__ import annotations

import pytest

from broker.portfolio import Portfolio, Position


def test_from_state_value_passes_through_instance() -> None:
    """A live Portfolio object round-trips untouched."""
    p = Portfolio(cash=100.0)
    assert Portfolio.from_state_value(p) is p


def test_from_state_value_validates_dict_dump() -> None:
    """A model_dump(mode='json') dict is validated back into a Portfolio."""
    src = Portfolio(
        cash      = 250.0,
        positions = {"AAPL": Position(ticker="AAPL", quantity=3.0, last_price=42.0)},
    )
    dump = src.model_dump(mode="json")

    out = Portfolio.from_state_value(dump)

    assert isinstance(out, Portfolio)
    assert out.cash == 250.0
    assert out.positions["AAPL"].quantity == 3.0


def test_from_state_value_raises_on_none() -> None:
    """Missing portfolio is a contract violation — never silently empty."""
    with pytest.raises(ValueError, match="state\\[.portfolio.\\] missing"):
        Portfolio.from_state_value(None)


def test_from_state_value_raises_on_malformed_dict() -> None:
    """Malformed dict raises rather than silently swallowing the bug."""
    with pytest.raises(ValueError, match="state\\[.portfolio.\\] malformed"):
        Portfolio.from_state_value({"cash": "not-a-number", "positions": []})


def test_from_state_value_raises_on_wrong_type() -> None:
    """Unrecognised types raise — no `or {}` papering-over."""
    with pytest.raises(TypeError, match="state\\[.portfolio.\\] unexpected type"):
        Portfolio.from_state_value(42)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/broker/test_portfolio_from_state_value.py -v`
Expected: FAIL with `AttributeError: type object 'Portfolio' has no attribute 'from_state_value'`.

- [ ] **Step 3: Implement the classmethod**

Append to `src/broker/portfolio.py` inside the `Portfolio` class body:

```python
    @classmethod
    def from_state_value(cls, value: "Portfolio | dict | None") -> "Portfolio":
        """Coerce a session-state value into a Portfolio — the canonical door.

        The single sanctioned way to read ``state["portfolio"]`` across the
        codebase.  Raises on missing or malformed input rather than
        silently producing an empty portfolio — silent empties were the
        source of the "tick T+1 strategist sees no holdings" class of
        bugs catalogued in audit finding A-014 / A-071.

        Args:
            value: Either a live ``Portfolio`` instance, a
                ``Portfolio.model_dump(mode="json")`` dict (the
                cross-tick storage shape), or ``None``.

        Returns:
            A ``Portfolio`` instance.

        Raises:
            ValueError: If ``value`` is ``None`` or a malformed dict.
            TypeError:  If ``value`` is any other type.
        """
        # Pass-through for already-validated instances — the hot path
        # inside a single tick where the dict has been coerced once and
        # stashed back as the object.
        if isinstance(value, cls):
            return value

        # Missing portfolio is a contract violation, not a cold-start
        # fall-back.  Cold start must seed state["portfolio"] explicitly
        # via Portfolio(cash=starting_capital).model_dump(mode="json").
        if value is None:
            raise ValueError(
                "state['portfolio'] missing — every tick must seed it at "
                "Phase 2 (live: orchestrator/tick.py; backtest: driver.py)."
            )

        if isinstance(value, dict):
            try:
                return cls.model_validate(value)
            except Exception as exc:  # noqa: BLE001 — re-raised below with context
                raise ValueError(
                    f"state['portfolio'] malformed: {exc}"
                ) from exc

        raise TypeError(
            f"state['portfolio'] unexpected type: {type(value).__name__}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/broker/test_portfolio_from_state_value.py -v`
Expected: PASS (5/5).

- [ ] **Step 5: Commit**

```bash
git add src/broker/portfolio.py tests/unit/broker/test_portfolio_from_state_value.py
git commit -m "feat(portfolio): add from_state_value classmethod as canonical state-coercion door

Single sanctioned coercion helper for state['portfolio']. Raises on
missing or malformed input rather than silently producing an empty
portfolio (audit findings A-014, A-071).
"
```

---

### Task 2 — Migrate `strategist/context_shim.py` onto the canonical helper

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/context_shim.py` (lines 55-72, 153, 159, 229, 249, 304)
- Test:   existing `tests/unit/strategist/test_context_shim.py` plus new behavioural assertion below

- [ ] **Step 1: Add a failing test that the shim refuses the bare `positions` key**

Append to `tests/unit/strategist/test_context_shim.py`:

```python
def test_context_shim_ignores_bare_positions_key() -> None:
    """The shim must read user:positions exclusively — no bare-key fallback.

    Audit finding A-014: external readers used to silently fall back to
    state['positions'] (the executor's in-tick bridge), which would
    persist stale BUY→SELL intermediate state across ticks.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Portfolio

    shim = StrategistContextShim()
    state = {
        "user:positions":            {},
        "positions":                 {"AAPL": {"rationale": "bridge-leak"}},
        "portfolio":                 Portfolio(cash=1.0).model_dump(mode="json"),
        "user:active_stances_initialised": True,
    }

    out = shim.render(state)

    # The bridge value must NOT appear in the rendered held-view.
    assert "bridge-leak" not in out["temp:held_positions_view"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/strategist/test_context_shim.py::test_context_shim_ignores_bare_positions_key -v`
Expected: FAIL — the current shim's `or state.get("positions")` fallback leaks the bridge value.

- [ ] **Step 3: Delete the local `_coerce_portfolio` and rewrite all reads**

In `src/agents/strategist/context_shim.py`:

a) **Delete** lines 55-72 (the local `_coerce_portfolio` helper).

b) Replace the import block to add `Portfolio.from_state_value` usage by
   keeping the existing `from broker.portfolio import Portfolio` line — no
   new import needed; the classmethod is reached via `Portfolio.from_state_value`.

c) Replace line 153:

```python
        positions = state.get("user:positions") or state.get("positions") or {}
```

with:

```python
        # A-014: read only the canonical user-namespaced key.  The
        # executor's bridge (temp:executor_positions_bridge) is
        # executor-internal and must never leak into the strategist's
        # held-view.
        positions = state.get("user:positions") or {}
```

d) Replace line 159:

```python
        portfolio = _coerce_portfolio(state.get("portfolio"))
```

with:

```python
        portfolio = Portfolio.from_state_value(state.get("portfolio"))
```

e) Apply the same two substitutions to lines 229 and 249 inside
   `_run_async_impl` (identical patterns).

f) **Delete** line 304's bare-key thesis fallback. Replace:

```python
        thesis: str = state.get("user:thesis") or ""
```

with (kept as-is — `user:thesis` is the canonical key; the issue at
line 304 was already correct, but re-verify there is no `or state.get("thesis")`
tail; if there is, strip it).

- [ ] **Step 4: Run the full strategist test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/strategist/ -v`
Expected: PASS — including the new bridge-leak assertion.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/context_shim.py tests/unit/strategist/test_context_shim.py
git commit -m "refactor(strategist): drop bare-key positions fallback in context shim

Reads only state['user:positions'] now; the executor's in-tick bridge
must never leak into the strategist's held-view (audit A-014).
Local _coerce_portfolio replaced by Portfolio.from_state_value.
"
```

---

### Task 3 — Migrate `strategist/enricher.py` onto the canonical helper

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/enricher.py` (lines 73-89, 174)

- [ ] **Step 1: Delete the second `_coerce_portfolio` copy**

In `src/agents/strategist/enricher.py`:

a) **Delete** lines 70-89 (the `# ── pure helper ───` block plus the
   `_coerce_portfolio` function body).

b) Replace line 174:

```python
    portfolio = _coerce_portfolio(state.get("portfolio"))
```

with:

```python
    portfolio = Portfolio.from_state_value(state.get("portfolio"))
```

- [ ] **Step 2: Run the strategist test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/strategist/ -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/agents/strategist/enricher.py
git commit -m "refactor(strategist): replace local _coerce_portfolio with canonical helper

Deletes the second of two duplicate _coerce_portfolio implementations
(audit A-071). Enricher now uses Portfolio.from_state_value.
"
```

---

### Task 4 — Eliminate redundant mid-tick `broker.get_portfolio()` in risk_gate

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/agent.py` (lines 94-107)
- Test:   `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/risk_gate/test_risk_gate_reads_state_portfolio.py` (new)

- [ ] **Step 1: Write a failing test that risk_gate reads `state["portfolio"]`, not the broker**

```python
# tests/unit/risk_gate/test_risk_gate_reads_state_portfolio.py
"""Verify risk_gate consumes state['portfolio'] instead of broker.get_portfolio.

Audit finding A-072 — Phase 2 already refreshes state['portfolio'] at the
tick boundary; calling broker.get_portfolio mid-tick was a duplicate.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.risk_gate.agent import RiskGateAgent  # adjust import if differs
from broker.portfolio import Portfolio, Position


@pytest.mark.asyncio
async def test_risk_gate_uses_state_portfolio_not_broker() -> None:
    """The clamp loop must read current_weights from state['portfolio']."""
    state_portfolio = Portfolio(
        cash      = 50.0,
        positions = {"AAPL": Position(ticker="AAPL", quantity=1.0, last_price=100.0)},
    )

    # Broker.get_portfolio is wired to a sentinel that would diverge from
    # state — if risk_gate calls it, the assertion below will catch it.
    diverging_broker_portfolio = Portfolio(cash=0.0)
    broker = AsyncMock()
    broker.get_portfolio = AsyncMock(return_value=diverging_broker_portfolio)

    agent = RiskGateAgent(broker=broker)

    # Minimal state — only the keys risk_gate actually consumes.
    state = {
        "strategist_decision": {"stances": []},
        "portfolio":           state_portfolio.model_dump(mode="json"),
    }

    # Direct-call the pure clamp helper (or the smallest entry point that
    # exercises the read).  See risk_gate/agent.py for the actual API.
    _ = await agent._compute_clamps(state)  # name depends on the existing surface

    broker.get_portfolio.assert_not_called()
```

> **Note for the implementer:** if `RiskGateAgent` has no pure-helper
> entry point that exposes the clamp computation, the smallest viable
> alternative is to drive `_run_async_impl` via a lightweight
> `InvocationContext` stub — pattern already used in
> `tests/unit/risk_gate/test_risk_gate_*.py`. Re-use that stub rather
> than inventing a new one.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/risk_gate/test_risk_gate_reads_state_portfolio.py -v`
Expected: FAIL — `broker.get_portfolio` is currently called at line 95.

- [ ] **Step 3: Replace the broker call with the state read**

In `src/agents/risk_gate/agent.py`, replace lines 94-107 with:

```python
        # A-072: consume state['portfolio'] (refreshed at Phase 2) rather
        # than re-pulling from the broker mid-tick.  The broker remains
        # the source of truth, but the Phase 2 refresh already canonicalised
        # it into state for every downstream agent.
        portfolio_value = state.get("portfolio")
        if portfolio_value is None:
            # Cold-start carve-out: no portfolio seeded yet → no
            # historical weights to clamp against.  Raise rather than
            # silently allow unbounded weights — cold-start callers
            # should seed state['portfolio'] explicitly.
            raise RuntimeError(
                "risk_gate: state['portfolio'] missing — Phase 2 seed "
                "did not run."
            )

        portfolio = Portfolio.from_state_value(portfolio_value)
        current_weights = portfolio.current_weights()

        # Build a price map from portfolio positions, then fill any gaps
        # from FakeBroker's injected _prices (used in tests).  The broker
        # reference is still kept on the agent so test fakes can publish
        # synthetic prices; we just no longer re-pull the full portfolio.
        prices = {t: pos.last_price for t, pos in portfolio.positions.items()}
        if self.broker is not None and hasattr(self.broker, "_prices"):
            for t, p in self.broker._prices.items():
                if t not in prices:
                    prices[t] = p
```

Add the `from broker.portfolio import Portfolio` import if not already present.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/risk_gate/ tests/integration/test_risk_gate*.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/risk_gate/agent.py tests/unit/risk_gate/test_risk_gate_reads_state_portfolio.py
git commit -m "refactor(risk_gate): read state['portfolio'] instead of broker.get_portfolio

The Phase 2 tick seed already canonicalises broker state into
state['portfolio']; the mid-tick broker re-pull was a duplicate
(audit A-072).
"
```

---

### Task 5 — Eliminate redundant mid-tick `broker.get_portfolio()` in snapshotter

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/snapshot/agent.py` (line 38)

- [ ] **Step 1: Add a failing test**

Add to `tests/unit/test_snapshotter.py` (or create if absent):

```python
@pytest.mark.asyncio
async def test_snapshotter_uses_state_portfolio_not_broker() -> None:
    """Snapshotter reads state['portfolio'], does not call broker (A-072)."""
    from unittest.mock import AsyncMock
    from agents.snapshot.agent import SnapshotterAgent
    from broker.portfolio import Portfolio

    p = Portfolio(cash=100.0)
    broker = AsyncMock()
    broker.get_portfolio = AsyncMock(return_value=Portfolio(cash=999.0))

    # Drive via the smallest existing invocation-context stub.
    state = {
        "tick_id":   "t-1",
        "portfolio": p.model_dump(mode="json"),
    }

    # Re-use existing snapshotter test harness — see
    # tests/unit/test_snapshotter.py for the InvocationContext stub.
    await _drive_snapshotter(SnapshotterAgent(broker=broker), state)

    broker.get_portfolio.assert_not_called()
    assert state["last_snapshot"]["bot_cash"] == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_snapshotter.py::test_snapshotter_uses_state_portfolio_not_broker -v`
Expected: FAIL.

- [ ] **Step 3: Replace the broker call**

In `src/agents/snapshot/agent.py`, replace line 38:

```python
        portfolio = await self.broker.get_portfolio()
```

with:

```python
        # A-072: read the Phase 2 canonical snapshot rather than re-pulling
        # from the broker mid-tick.  Same rationale as risk_gate — the
        # broker remains source-of-truth, but Phase 2 already published it.
        portfolio = Portfolio.from_state_value(state.get("portfolio"))
```

Add `from broker.portfolio import Portfolio` to the imports at the top.

- [ ] **Step 4: Run snapshotter tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_snapshotter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/snapshot/agent.py tests/unit/test_snapshotter.py
git commit -m "refactor(snapshot): read state['portfolio'] instead of broker.get_portfolio

Mirrors the risk_gate change in Task 4 — Phase 2 already canonicalised
the portfolio (audit A-072).  Two of the three duplicated calls are now
gone; the executor SELL-close confirmation remains by design.
"
```

---

### Task 6 — Rename the executor's in-tick bridge key and remove bare-key leakage

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/agent.py` (lines 99, 320, 384)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/backtest/driver.py` (lines 271-273, the explanatory comment block — text only)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/backtest/decision_logger.py` (lines 336-340)

- [ ] **Step 1: Add a failing test that decision_logger reads `user:positions`**

```python
# tests/unit/backtest/test_decision_logger_held_view.py (extend or create)
def test_decision_logger_held_view_reads_user_positions() -> None:
    """held_view_at_decision must come from user:positions, not bare key (A-014)."""
    # ... minimal state with both keys diverging:
    state = {
        "user:positions":          {"AAPL": {"rationale": "real"}},
        "positions":               {"AAPL": {"rationale": "bridge-leak"}},
        # ... plus the minimum decision_logger requires
    }
    snapshot = _logger.build_snapshot_for_ticker(state, "AAPL")
    assert snapshot["strategist_view"]["held_view_at_decision"]["rationale"] == "real"
```

- [ ] **Step 2: Run it to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_decision_logger_held_view.py -v`
Expected: FAIL — currently reads `state.get("positions")`.

- [ ] **Step 3: Rename the bridge in the executor**

In `src/agents/executor/agent.py`:

a) Line 99 — change:

```python
        positions: dict = dict(state.get("positions", {}))
```

to:

```python
        # In-tick BUY → SELL bridge.  Strictly executor-internal:
        # the temp: namespace prevents external readers from picking it up
        # by accident (audit A-014).  The persistent thesis-book is
        # state['user:positions'], written by _executor_thesis_writer_callback
        # after this method completes.
        positions: dict = dict(state.get("temp:executor_positions_bridge", {}))
```

b) Lines 320 + 384 — change every `state["positions"]` write to
   `state["temp:executor_positions_bridge"]`, and the `"positions"` key in
   the `delta` dict to `"temp:executor_positions_bridge"`.

c) Update the surrounding comments to refer to the new key by name.

- [ ] **Step 4: Update `backtest/decision_logger.py`**

Replace lines 336-340:

```python
                "held_view_at_decision": _coerce(
                    (state.get("positions") or {}).get(ticker)
                ),
```

with:

```python
                # A-014: read the persistent thesis-book directly.  The
                # executor's in-tick bridge lives under
                # temp:executor_positions_bridge and is *not* the same as
                # the cross-tick book — using the bridge here would give
                # stale-by-one-tick held views.
                "held_view_at_decision": _coerce(
                    (state.get("user:positions") or {}).get(ticker)
                ),
```

- [ ] **Step 5: Update the `backtest/driver.py` comment**

Replace the stale comment block at driver.py:271-274 (text only — no
behavioural change here, the driver does not read the bridge):

```python
            # state['user:positions'] (thesis book) and
            # state['temp:executor_positions_bridge'] (executor in-tick
            # bridge) are both managed by the executor (after-callback and
            # _run_async_impl respectively) and do not need a refresh here.
```

- [ ] **Step 6: Run executor + backtest tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/executor/ tests/integration/test_executor_with_fake_broker.py tests/unit/backtest/test_decision_logger_held_view.py -v`
Expected: PASS (after Task 9 also updates the integration test fixture, which
this task's integration tests may temporarily depend on — if so, re-stage
and proceed; the integration assertions are tightened in Task 9).

- [ ] **Step 7: Commit**

```bash
git add src/agents/executor/agent.py src/backtest/decision_logger.py src/backtest/driver.py tests/unit/backtest/test_decision_logger_held_view.py
git commit -m "refactor(executor): rename in-tick bridge to temp:executor_positions_bridge

Moves the BUY->SELL bridge under the temp: namespace so external readers
can't accidentally consume it.  decision_logger now reads user:positions
exclusively (audit A-014).
"
```

---

### Task 7 — Collapse the executor's triple direct-write + state_delta (A-073)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/agent.py` (lines 319-322, 380-393)

The executor currently writes the same three keys *twice*: once via
direct `state[k] = v` mutations (lines 319-322) and once via the
`state_delta=delta` Event payload (lines 381-392). Per audit A-073 the
direct writes are visible in-tick via the shared session reference, but
only the `state_delta` Event propagates them to storage. The
paired-write pattern is brittle (easy to drift) and the audit calls for
a single `write_durable` helper.

- [ ] **Step 1: Add a failing test that the paired keys cannot drift**

```python
# tests/unit/executor/test_executor_state_delta_parity.py
"""Audit A-073: every in-tick state mutation must appear in state_delta."""
import pytest

@pytest.mark.asyncio
async def test_executor_in_tick_writes_match_state_delta() -> None:
    """In-tick state and emitted Event.state_delta carry identical keys/values."""
    # Drive the executor end-to-end with a FakeBroker and at least one
    # BUY + one SELL.  Capture the yielded Event and assert that for each
    # key in DURABLE_KEYS, the in-tick state value equals the delta value.
    ...
```

- [ ] **Step 2: Introduce a `write_durable` helper inside `executor/agent.py`**

Add near the top of `agents/executor/agent.py` (module-scope):

```python
# Keys whose mutations must propagate to cross-tick storage.  See
# audit A-073 — paired direct-write + state_delta was the existing
# pattern; this helper collapses it into one call.
_DURABLE_EXECUTOR_KEYS = (
    "executions",
    "last_executed_tick_id",
    "temp:executor_positions_bridge",
)


def _write_durable(state, delta_accumulator: dict, key: str, value) -> None:
    """Write ``value`` to ``state[key]`` and record it for the state_delta Event.

    Single sanctioned door for executor in-tick writes that need to
    cross the tick boundary.  Reading either ``state[key]`` (in-tick) or
    the yielded Event's ``state_delta[key]`` (cross-tick) will see the
    same value by construction.
    """
    state[key] = value
    delta_accumulator[key] = value
```

- [ ] **Step 3: Refactor `_run_async_impl` to use the helper**

Replace the in-tick block (lines ~319-322) and the delta-building block
(lines ~380-393) with a single accumulator loop:

```python
        # Single-write pattern — populate the durable-keys accumulator
        # once, then mirror it to both in-tick state and the yielded
        # Event's state_delta via _write_durable.
        durable: dict = {}
        _write_durable(state, durable, "executions",            executions)
        _write_durable(state, durable, "last_executed_tick_id", tick_id)
        _write_durable(state, durable, "temp:executor_positions_bridge", positions)

        # Conditional: only include the closed-trades log if this tick
        # actually mutated it.
        if "user:closed_trades_log" in state:
            durable["user:closed_trades_log"] = state["user:closed_trades_log"]

        # ... (trace + decision_logger calls unchanged) ...

        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta=durable),
        )
```

- [ ] **Step 4: Run executor tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/executor/ tests/integration/test_executor_with_fake_broker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/executor/agent.py tests/unit/executor/test_executor_state_delta_parity.py
git commit -m "refactor(executor): collapse paired in-tick + state_delta writes via write_durable

Single helper now handles both the in-tick state mutation and the
cross-tick state_delta Event payload — eliminates the drift risk
flagged by audit A-073.
"
```

---

### Task 8 — Remove `state["thesis"]` / paired `user:thesis` residue (A-086)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/agent.py` (lines 560-573)

The thesis-carry-forward at executor agent.py:560-573 currently reads
`state.get("user:thesis", "")` (correct) but the audit (A-086) flags
that older callers still write `state["thesis"]` somewhere. Sweep and
delete.

- [ ] **Step 1: Search for any remaining bare `state["thesis"]` writes/reads**

Run: `grep -rn 'state\[.thesis.\]\|state\.get(.thesis.' src/ scripts/ tests/`
Expected output: enumerate every match — they must all become
`state["user:thesis"]` (or be deleted as dead code).

- [ ] **Step 2: Add a failing assertion**

```python
# tests/unit/test_no_bare_thesis_keys.py
"""Audit A-086: state['thesis'] must not appear anywhere in src/."""
from pathlib import Path
import re

def test_no_bare_thesis_state_key_in_src() -> None:
    """Bare state['thesis'] reads/writes were deleted (A-086)."""
    pattern = re.compile(r"""state\[\s*["']thesis["']\s*\]|state\.get\(\s*["']thesis["']""")
    offenders = []
    for path in Path("src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, "Bare state['thesis'] still present:\n" + "\n".join(offenders)
```

- [ ] **Step 3: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_no_bare_thesis_keys.py -v`
Expected: PASS if grep returned no matches; FAIL with the file:line list otherwise.

- [ ] **Step 4: Delete each offender**

For each match: change `state["thesis"]` → `state["user:thesis"]`, or
delete the line entirely if dead. Re-run the test until green.

- [ ] **Step 5: Mirror the assertion for bare `positions` / `cash`**

Extend the same test file:

```python
def test_no_bare_positions_or_cash_state_keys_in_src() -> None:
    """Audit A-014: external readers must use user:positions only.

    The only sanctioned write to state['temp:executor_positions_bridge']
    lives in agents/executor/agent.py — every other reference is a regression.
    """
    pattern = re.compile(
        r"""state\[\s*["'](positions|cash)["']\s*\]|state\.get\(\s*["'](positions|cash)["']"""
    )
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, "Bare state['positions'|'cash'] still present:\n" + "\n".join(offenders)
```

- [ ] **Step 6: Run both assertions, fix any remaining offenders, commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_no_bare_thesis_keys.py -v`
Expected: PASS.

```bash
git add src/ tests/unit/test_no_bare_thesis_keys.py
git commit -m "chore(state-keys): delete state['thesis']/'positions'/'cash' residue

Sweeps every remaining bare-key access in src/.  Static-content test
guards against regression (audit A-014, A-086).
"
```

---

### Task 9 — Sweep test fixtures (the silent-regression vector)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/integration/test_executor_with_fake_broker.py` (line 274)
- Modify: any other test fixture that constructs the bare-key shape (enumerate via grep)

Tests are the most likely place for the bare-key shape to outlive the
src/ migration — fixtures get copy-pasted across files and the helpers
above only catch src/ violations.

- [ ] **Step 1: Enumerate every test that still references the deleted keys**

Run: `grep -rn 'state\(.\get\)\?\[\?\s*["'\'']\(positions\|cash\|thesis\)["'\'']' tests/`

For each hit, classify:
- **External read** (asserting on `state.get("positions")` etc.) — rewrite to use the canonical key.
- **Fixture write** (`state = {"positions": ..., ...}`) — rewrite as `state = {"user:positions": ..., "portfolio": Portfolio(...).model_dump(mode="json"), ...}`.
- **Legitimate executor-internal test** of the bridge — rewrite to use
  the new `temp:executor_positions_bridge` key name.

- [ ] **Step 2: Fix `tests/integration/test_executor_with_fake_broker.py:274`**

```python
    reloaded_positions = reloaded.state.get("positions", {})
```

becomes:

```python
    # A-014: cross-tick persisted book is user:positions, not the bare key.
    reloaded_positions = reloaded.state.get("user:positions", {})
```

If the test was asserting the bridge specifically (check the surrounding
assertion semantics), use `temp:executor_positions_bridge` instead.

- [ ] **Step 3: Walk every other hit from Step 1 and apply the matching rewrite**

Do not batch-rewrite — each fixture needs the per-call classification
from Step 1.

- [ ] **Step 4: Add a static-content test that mirrors Task 8's to `tests/`**

```python
# tests/unit/test_no_bare_state_keys_in_fixtures.py
"""Audit A-014/A-086: test fixtures must use the canonical state shape too.

Carve-outs:
- tests/unit/executor/* may reference temp:executor_positions_bridge
  (the executor's legitimate in-tick bridge — internal scope).
- tests under tests/contract/snapshots/ may contain JSON fixtures with
  legacy keys for backwards-compat assertions; allowlist them here.
"""
from pathlib import Path
import re

# Carve-outs intentionally narrow — extend only with a documented reason.
_ALLOWLIST = {
    # add paths here if a legitimate test needs the bare key
}

_PATTERN = re.compile(
    r"""state\[\s*["'](positions|cash|thesis)["']\s*\]|state\.get\(\s*["'](positions|cash|thesis)["']"""
)

def test_no_bare_state_keys_in_test_fixtures() -> None:
    offenders: list[str] = []
    for path in Path("tests").rglob("*.py"):
        if str(path) in _ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _PATTERN.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Bare state['positions'|'cash'|'thesis'] in test fixtures:\n"
        + "\n".join(offenders)
    )
```

- [ ] **Step 5: Run the full test suite to catch any unexpected fallout**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x -q`
Expected: PASS — including the two new static-content guards.

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test: migrate fixtures off bare positions/cash/thesis state keys

Sweeps every test that constructed the old bare-key shape; adds a
static-content guard to keep new fixtures honest (audit A-014, A-086).
"
```

---

### Task 10 — Drop executor direct in-tick writes for `executions` / `last_executed_tick_id` (A-069)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/agent.py:317-393`
- Modify: `tests/unit/executor/test_executor_state_delta_parity.py` (extend the Task 7 test)

**Context:** Task 7 introduced `_write_durable(state, delta_accumulator, key, value)` for the three durable keys (`executions`, `last_executed_tick_id`, `temp:executor_positions_bridge`) — the helper mirrors every write to both in-tick state and the yielded Event's `state_delta`. Audit A-069 narrows that: only `temp:executor_positions_bridge` actually needs in-tick visibility (the BUY → SELL bridge inside the same tick). `executions` and `last_executed_tick_id` are read only on the *next* tick, so writing them to in-tick state is dead bookkeeping that doubles the mutation surface and invites drift.

This task narrows the `_write_durable` surface and shifts the two non-bridge keys to be `state_delta`-only writes. The bridge stays paired because the in-tick BUY → SELL flow depends on it.

- [ ] **Step 1: Extend the parity test to assert in-tick absence.**

```python
# tests/unit/executor/test_executor_state_delta_parity.py — add a second test
@pytest.mark.asyncio
async def test_executor_writes_executions_only_to_state_delta() -> None:
    """A-069: `executions` and `last_executed_tick_id` must NOT appear in
    in-tick state; only the BUY→SELL bridge needs in-tick visibility.

    Drives the executor end-to-end with a FakeBroker over one BUY tick,
    captures the in-tick state snapshot before the yielded Event, and
    asserts the two keys are absent in-tick but present in state_delta.
    """
    ...
    # Pre-yield in-tick state
    assert "executions"             not in pre_yield_state
    assert "last_executed_tick_id"  not in pre_yield_state

    # state_delta carries them
    assert event.actions.state_delta["executions"]            == executions
    assert event.actions.state_delta["last_executed_tick_id"] == tick_id

    # Bridge is paired (Task 7 contract — unchanged)
    assert "temp:executor_positions_bridge" in pre_yield_state
    assert event.actions.state_delta["temp:executor_positions_bridge"] \
        == pre_yield_state["temp:executor_positions_bridge"]
```

- [ ] **Step 2: Run it — expected FAIL (Task 7 wrote both keys in-tick via `_write_durable`).**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/executor/test_executor_state_delta_parity.py -v`

- [ ] **Step 3: Narrow the `_DURABLE_EXECUTOR_KEYS` constant.**

In `src/agents/executor/agent.py`, narrow the tuple introduced by Task 7:

```python
# Only the BUY→SELL bridge needs in-tick visibility (A-069).
# executions and last_executed_tick_id are next-tick reads — they live
# in state_delta only to keep the in-tick mutation surface honest.
_DURABLE_EXECUTOR_KEYS = ("temp:executor_positions_bridge",)
```

The constant is now informational (the helper no longer reads it for dispatch); leave it as a code-review aid — a one-line `assert key in _DURABLE_EXECUTOR_KEYS or key.startswith("user:")` inside `_write_durable` is acceptable but optional.

- [ ] **Step 4: Split the executor's write site.** Replace the unified accumulator loop introduced by Task 7 with one paired bridge write and two delta-only writes:

```python
        durable: dict = {}

        # Bridge — paired write (in-tick visibility required for the
        # same-tick BUY → SELL flow that reads state[bridge_key]).
        _write_durable(state, durable, "temp:executor_positions_bridge", positions)

        # Cross-tick-only writes — readers (the next tick's run_once and the
        # snapshotter) consume these from persisted session state, never
        # from the current tick's in-memory dict.
        durable["executions"]            = executions
        durable["last_executed_tick_id"] = tick_id

        if "user:closed_trades_log" in state:
            durable["user:closed_trades_log"] = state["user:closed_trades_log"]
```

- [ ] **Step 5: Audit in-tick readers.** Run:

```bash
grep -rn '\bstate\[\s*"executions"\s*\]\|\bstate\[\s*"last_executed_tick_id"\s*\]\|state\.get(\s*"executions"\|state\.get(\s*"last_executed_tick_id"' src/
```

Any hit inside `src/agents/executor/agent.py` after this task is a contradiction — fix it. Any hit elsewhere in `src/` reads from persisted session state on a later tick, which is unaffected (ADK rehydrates from storage between ticks).

- [ ] **Step 6: Run the full suite.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
Expected: green. Any test that asserted on in-tick `state["executions"]` must be rewritten to assert on `event.actions.state_delta["executions"]` — that is the intended narrowing, not a regression.

- [ ] **Step 7: Commit.**

```bash
git add src/agents/executor/agent.py tests/unit/executor/test_executor_state_delta_parity.py
git commit -m "refactor(executor): keep bridge paired, narrow executions to state_delta only (A-069)

Audit A-069: only temp:executor_positions_bridge needs in-tick visibility
(the BUY → SELL flow reads it within the same tick). executions and
last_executed_tick_id are next-tick reads; writing them to in-tick state
was dead bookkeeping that doubled the mutation surface.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 3 — Test strategy

The plan's test layer has three jobs, in increasing strictness:

1. **Helper contract.** `tests/unit/broker/test_portfolio_from_state_value.py`
   (Task 1) pins the canonical helper's behaviour — including that it
   **raises** on missing/malformed input rather than yielding an empty
   portfolio. This is the load-bearing assertion of the whole plan: if
   the helper ever quietly returns `Portfolio(cash=0.0)` on `None`, the
   silent-degradation class that the audit was written to kill comes
   straight back.

2. **Consumer behaviour.** Tasks 2/4/5 each add one targeted assertion
   that the migrated agent reads only the canonical key — by populating
   the *deprecated* bare key with a sentinel value the test would notice
   if it leaked through. This converts "consumers were updated" from a
   review-only claim into a runnable check.

3. **Static-content guards.** Tasks 8 and 9 add two regex-based tests
   (`tests/unit/test_no_bare_thesis_keys.py` and
   `tests/unit/test_no_bare_state_keys_in_fixtures.py`) that fail the
   build if any new bare-key reference appears in `src/` or `tests/`.
   These are the contract enforcement layer Plans 04+ rely on.

**Why no mocking-policy exception.** All consumer tests drive the
real agent against either a real or `AsyncMock`'d broker — there's no
helper-internal stub. The `AsyncMock` in Tasks 4/5 is a *probe* (asserting
the broker was **not** called), not a behavioural substitute.

**Coverage target.** Every line removed/changed in Tasks 2-9 must be
covered by at least one of the three test layers above. Reject the task
during review if a hot path was touched without a corresponding test.

---

## 4 — Risks / silent-regression checklist

| Risk                                                                       | Why it matters                                                                                              | Mitigation                                                                                              |
|----------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| Test fixtures still constructing `{"positions": {...}}` shape.             | Tests would pass against the old shape even after src/ migration → false-green coverage.                    | Task 9 static-content guard catches this.                                                               |
| External script under `scripts/` reading `state["positions"]`.             | `replay_backtest.py` and `trace_tick.py` are manual tools — easy to miss.                                   | Task 8's grep includes `scripts/`; sweep covers it.                                                     |
| `decision_logger` snapshot consumers (audit JSONs in `runs/`).             | Existing snapshots on disk reference the old key shape in their schema.                                     | Out of scope — snapshots are write-only artefacts; reading code lives in `reporting.py` (Task 6 covers).|
| The executor's BUY → SELL in-tick bridge breaking.                         | Renaming the key is a refactor risk — same-tick BUY then SELL must still see the bridge value.              | Task 6 integration test exercises BUY-then-SELL end-to-end on FakeBroker.                               |
| Cold-start ticks failing the new "raise on missing portfolio" rule.        | Plan 04 (as_of boundary) may want to run with no portfolio seeded in unit tests.                            | Helper raises with a clear message; Plan 04 fixtures must seed `state["portfolio"]` — documented above. |
| ADK `State` proxy semantics — `state["k"] = v` triggers delta tracking only inside `_run_async_impl` / callback contexts, not in test stubs. | Task 7's `_write_durable` could silently no-op in some test contexts.                                       | The helper writes to both `state[k]` and the explicit `delta_accumulator` dict — tests assert on the latter directly. |
| `risk_gate` integration tests inject a custom broker portfolio.            | Task 4 swap means injected-broker tests no longer drive the clamp loop unless they also set `state["portfolio"]`. | Tests must be updated to seed `state["portfolio"]`; Task 4 Step 4 runs the integration suite to catch this. |

**Memory-aware note.** Per `~/.claude/projects/.../MEMORY.md`:
"silent failures are the recurring bug class". This plan's central
mechanism — making the helper **raise** — is deliberately aligned with
that. Do not weaken it during implementation review.

---

## 5 — Definition of done

Every box below must be tickable from a clean checkout at HEAD-of-Plan-03:

- [ ] `Portfolio.from_state_value` exists, is the **only** coercion helper
      in `src/`, and `grep -rn "_coerce_portfolio" src/` returns **zero
      hits**.
- [ ] `grep -rn 'state\.get(.\(positions\|cash\|thesis\).' src/ scripts/`
      returns **zero hits** (excluding `temp:executor_positions_bridge`
      which is a different key).
- [ ] `grep -rn 'state\[.\(positions\|cash\|thesis\).\]' src/ scripts/`
      returns **zero hits** (same carve-out).
- [ ] `broker.get_portfolio()` is called from **exactly** these sites:
      `orchestrator/tick.py::build_initial_tick_state` (live Phase 2),
      `backtest/driver.py` (backtest Phase 2 refresh),
      `lifecycle/initialise.py` (start-of-day seed),
      `agents/executor/agent.py:~205` (SELL post-fill confirmation).
      Verified by `grep -rn 'get_portfolio()' src/`.
- [ ] `state["temp:executor_positions_bridge"]` is read and written
      **only** inside `src/agents/executor/agent.py`. Verified by grep.
- [ ] `state["user:positions"]` is written **only** by
      `_executor_thesis_writer_callback`. Verified by grep.
- [ ] `state["user:thesis"]` is written **only** by
      `_executor_thesis_writer_callback`. Verified by grep.
- [ ] Task 7's `_write_durable` is the only path for executor in-tick
      durable writes; the prior paired-write pattern is gone.
- [ ] The two static-content tests (`test_no_bare_thesis_keys.py`,
      `test_no_bare_state_keys_in_fixtures.py`) pass and run in CI.
- [ ] `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q` is green
      end-to-end with no skips/xfails introduced by this plan.
- [ ] `graphify-out/graph_delta.md` has a dated entry summarising the
      key-namespace changes (per the project convention in CLAUDE.md).
- [ ] FINDINGS.md entries A-014, A-071, A-072, A-073, A-086 are linked
      back to this plan's commits in the audit's `progress.md` (or the
      equivalent ledger Plan 12 will introduce).

# Contract conformance — A1 (mechanical) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the mechanical, no-open-questions slice of contract-conformance work — convert three direct-mutation Rule 1 deviations to yielded `state_delta` events, wire `as_of` / `tick_phase` into the live tick builder, resolve the singular/plural verdict naming drift in the spec, and document four currently-undecided state keys in §A. Pre-deployment, single focused PR.

**Architecture:** Each Rule 1 conversion replaces a direct `state[k] = v` write inside `_run_async_impl` with a single yielded `Event(actions=EventActions(state_delta={...}))` — mirroring the canonical shape already used by MemoryWriter / Executor / Snapshotter. The live tick builder gains two new keys to match the backtest driver's existing seeds. Spec edits in `docs/contract-invariants.md` and `contract-audit.md` realign the documents with reality. No persistence-subsystem work, no callback restructures, no `temp:` prefixing.

**Tech Stack:** Python 3.14, Google ADK 1.34, pytest (+ pytest-asyncio), ruff. Existing project conventions per `.claude/CLAUDE.md` (British English, comment-heavy code, function docstrings).

---

## What changed and why

The contract spec at `docs/contract-invariants.md` (ratified 2026-05-15) and the audit at `docs/Phase8-contract-audit-fixes/contract-audit.md` together identify a set of mechanical contract deviations that can be closed without touching callbacks, persistence, or any open design question. This plan addresses six such items:

- **A1.1 / A1.2 / A1.3** — three `BaseAgent` subclasses (TechnicalAnalyst, SocialAnalyst, RiskGateAgent) currently mutate session state directly inside `_run_async_impl` and rely on the `return / yield` no-op generator trick. None of them already yield a `state_delta` (unlike Executor / MemoryWriter / Snapshotter, which are the defensive double-write set and are intentionally out of scope here). The fix is purely additive in terms of behaviour — the yielded event makes the same writes durable through ADK's `SessionService.append_event` path.
- **A1.4** — the live tick builder in `src/orchestrator/tick.py` never sets `as_of` or `tick_phase`, while the backtest driver sets both at `src/backtest/driver.py:194-195`. Downstream writers (`EvidenceWriter`, `StrategistDecisionWriter`, `MemoryWriter`, technical extractor) read `state["as_of"]`. Live currently relies on the `resolve_as_of(..., allow_wallclock=True)` fallback at every consumer. Seeding `as_of` from wall-clock once, at Phase 2 (tick-start), makes live conformant with the contract's "lifecycle owns Phase 2 hydration" rule.
- **A1.5** — the spec's §A schema uses singular keys (`technical_verdict`, `fundamental_verdict`, `news_verdict`, `social_verdict`) but the shipping code uses plural (`*_verdicts`). Decision baked in by the brainstorm: update the spec to match the code.
- **A1.6** — four keys (`tick_phase`, `last_executed_tick_id`, `last_snapshot`, `watchlist`) are present in code but unmodelled in §A. Three become new §A rows (documenting current writers and ownership); `watchlist` folds into `tickers` since backtest seeds both with identical content.

This plan does **not** touch persistence (the four high-severity cross-tick deviations `positions` / `memory_buffer` / `day_digest` / `thesis` are deferred to todo-fixes 2.5.3), does **not** restructure any callbacks (deferred to plan A2), and does **not** add `temp:` prefixes (gated on A2). See the "Deferred to A2" appendix at the end for the explicit boundary.

---

## File map

The plan touches the following files. Each is mentioned in exactly one task; nothing is edited twice across tasks.

**Modified — source:**

- `src/agents/analysts/technical/agent.py` — Task 1 (A1.1).
- `src/agents/analysts/social/agent.py` — Task 2 (A1.2).
- `src/agents/risk_gate/agent.py` — Task 3 (A1.3).
- `src/orchestrator/tick.py` — Task 4 (A1.4).
- `src/backtest/runner.py` — Task 7 (A1.6, watchlist fold).
- `src/backtest/driver.py` — Task 7 (A1.6, watchlist fold consumer).

**Modified — spec / audit:**

- `docs/contract-invariants.md` — Task 5 (A1.5 rename) + Task 6 (A1.6 new §A rows).
- `docs/Phase8-contract-audit-fixes/contract-audit.md` — Task 5 (A1.5 rename, prose alignment).

**Created — tests:**

- `tests/unit/agents/analysts/test_technical_state_delta.py` — Task 1.
- `tests/unit/agents/analysts/test_social_state_delta.py` — Task 2.
- `tests/integration/test_risk_gate_state_delta.py` — Task 3.
- `tests/unit/orchestrator/test_tick_as_of_phase.py` — Task 4.
- `tests/unit/backtest/test_driver_consumes_tickers.py` — Task 7.

**Final task touches:** repo-wide pytest + ruff + the slow smoke test.

---

## Task 1: Convert TechnicalAnalyst to yield `state_delta`

**A1.1** — Rule 1 conversion for `src/agents/analysts/technical/agent.py:129`.

**Files:**
- Modify: `src/agents/analysts/technical/agent.py:82-136`
- Create: `tests/unit/agents/analysts/test_technical_state_delta.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/analysts/test_technical_state_delta.py` with the following exact contents:

```python
"""Rule 1 conformance test for ``TechnicalAnalyst``.

Asserts that the analyst yields a single ``Event`` whose
``actions.state_delta`` contains ``technical_verdicts``.  The previous
implementation wrote directly to ``ctx.session.state`` and used the
``return / yield`` no-op generator trick — that pattern is forbidden by
contract Rule 1 because ADK's ``SessionService.append_event`` only
persists state when the event carries a non-empty ``state_delta``.

See ``docs/contract-invariants.md`` §C-Rule 1.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.analysts.heuristics import TechnicalHeuristics, load_heuristics
from agents.analysts.technical.agent import TechnicalAnalyst


def _make_heuristics() -> TechnicalHeuristics:
    """Return the cached ``TechnicalHeuristics`` config section."""

    # Use the project-default heuristics — exercising the production config
    # avoids drift between the test and what the live analyst sees.
    return load_heuristics().technical


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK ``InvocationContext`` double.

    Mirrors the established pattern in ``tests/unit/test_social_analyst_run.py``
    — the analyst only touches ``ctx.session.state`` and
    ``ctx.invocation_id``, so a ``MagicMock`` carrying those two attributes
    is sufficient.
    """

    ctx = MagicMock()
    ctx.session.state = state
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_technical_yields_state_delta_with_verdicts() -> None:
    """``_run_async_impl`` must yield exactly one ``Event`` carrying
    ``technical_verdicts`` in ``actions.state_delta``.

    The verdict list shape is exercised elsewhere; this test only locks in
    the Rule 1 wiring.
    """

    analyst = TechnicalAnalyst(heuristics=_make_heuristics())

    # Empty ``technical_data`` is enough — the analyst still iterates the
    # ticker list and emits an empty list of verdicts.  Rule 1 fires
    # regardless of payload size.
    state: dict = {"tickers": ["AAPL"], "technical_data": {}}
    ctx = _make_ctx(state)

    events: list = []
    async for event in analyst._run_async_impl(ctx):
        events.append(event)

    # Exactly one Event must be yielded — the state_delta carrier.
    assert len(events) == 1, (
        f"expected exactly one yielded Event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    assert "technical_verdicts" in delta, (
        "state_delta must carry the 'technical_verdicts' key per Rule 1"
    )
    assert isinstance(delta["technical_verdicts"], list)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_technical_state_delta.py -v`

Expected: the test FAILs with `assert 0 == 1` (zero events yielded) or `AssertionError: expected exactly one yielded Event; got 0`.

- [ ] **Step 3: Apply the minimal change to `src/agents/analysts/technical/agent.py`**

Edit the file at lines 1-37 to add the `EventActions` import alongside the existing `Event` import. Change the import block so it reads:

```python
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
```

Then replace lines 128-136 of `src/agents/analysts/technical/agent.py` — currently:

```python
        # Write the verdict list so the after_agent_callback can read it.
        state["technical_verdicts"] = verdicts

        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        _trace_maybe(ctx.session.state, "02_technical_verdict", verdicts)

        # No events emitted — pure state mutation, same as SocialAnalyst.
        return
        yield  # required to make this an async generator
```

— with:

```python
        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        # Run before the yield so the trace records the same payload the
        # state_delta carries.
        _trace_maybe(ctx.session.state, "02_technical_verdict", verdicts)

        # Contract Rule 1 — every state write rides on an Event whose
        # ``actions.state_delta`` carries it.  ADK's SessionService only
        # persists state via ``append_event``; a direct ``state[k] = v``
        # would be lost on any non-in-memory session backend.  See
        # ``docs/contract-invariants.md`` §C-Rule 1.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={"technical_verdicts": verdicts}),
        )
```

Also update the `_run_async_impl` docstring's `Yields:` block at lines 98-100 — currently:

```python
        Yields:
            Nothing — state mutation is written directly, matching the pattern
            used by SocialAnalyst, MemoryWriter, and RiskGateAgent.
```

— so it reads:

```python
        Yields:
            One ``Event`` whose ``actions.state_delta`` carries the
            ``technical_verdicts`` list.  Conforms to contract Rule 1; no
            direct ``state[k] = v`` write is performed.
```

And update the class-level docstring at lines 41-49 — replace "and writes ``state["technical_verdicts"]``" with "and yields an ``Event`` whose ``state_delta`` carries ``technical_verdicts``" (single occurrence; preserve surrounding text). Likewise update the module docstring at lines 10-14 — replace the "writes ``state["technical_verdicts"]`` directly to session state" wording with "yields an Event whose ``state_delta`` carries ``technical_verdicts``".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_technical_state_delta.py -v`

Expected: the test PASSes.

- [ ] **Step 5: Re-run pre-existing technical tests to catch regressions**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_technical.py tests/unit/test_analyst_fetchers.py -v`

Expected: all pre-existing tests PASS. The `make_evidence_callback` after-callback reads from `ctx.state` (via `cb_ctx.state`) — that pathway is unaffected because in-tick consumers see the same `ctx.session.state` reference; the ADK Runner merges yielded `state_delta`s before the next agent in the SequentialAgent runs.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/agents/analysts/technical/agent.py tests/unit/agents/analysts/test_technical_state_delta.py`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/technical/agent.py tests/unit/agents/analysts/test_technical_state_delta.py
git commit -m "$(cat <<'EOF'
fix(analysts/technical): yield state_delta instead of direct state mutation

TechnicalAnalyst._run_async_impl previously wrote
state["technical_verdicts"] directly and used the return/yield no-op
generator trick. Contract Rule 1 (docs/contract-invariants.md §C-Rule 1)
requires every state write to ride on an Event whose
actions.state_delta carries it — the direct write is not durable
through ADK's SessionService.append_event. Replace with a single
yielded Event mirroring the shape used by MemoryWriter and Executor.

Part of A1 mechanical contract-conformance work.
EOF
)"
```

---

## Task 2: Convert SocialAnalyst to yield `state_delta`

**A1.2** — Rule 1 conversion for `src/agents/analysts/social/agent.py:120`. Mirror of Task 1.

**Files:**
- Modify: `src/agents/analysts/social/agent.py:81-127`
- Create: `tests/unit/agents/analysts/test_social_state_delta.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/analysts/test_social_state_delta.py` with these exact contents:

```python
"""Rule 1 conformance test for ``SocialAnalyst``.

Asserts that the analyst yields a single ``Event`` whose
``actions.state_delta`` carries ``social_verdicts``.  See the technical
analyst counterpart and ``docs/contract-invariants.md``
§C-Rule 1 for the contract rationale.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.analysts.heuristics import SocialHeuristics
from agents.analysts.social.agent import SocialAnalyst


def _make_heuristics() -> SocialHeuristics:
    """Return a canonical ``SocialHeuristics`` fixture.

    The values mirror ``tests/unit/test_social_analyst_run.py`` so the
    two test files agree on what "default-ish" looks like for the social
    analyst.
    """

    return SocialHeuristics(
        score_neutral_band=0.05,
        score_to_magnitude_scale=2.0,
        high_volume_mentions=200,
        high_volume_magnitude_boost=0.15,
        confidence_volume_floor=30,
        platform_disagreement_threshold=0.3,
        confidence_base=0.4,
        confidence_boost_step=0.2,
        confidence_penalty_step=0.2,
        magnitude_cap=1.0,
    )


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK ``InvocationContext`` double with mutable state."""

    ctx = MagicMock()
    ctx.session.state = state
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_social_yields_state_delta_with_verdicts() -> None:
    """``_run_async_impl`` must yield one ``Event`` whose ``state_delta``
    carries ``social_verdicts``."""

    analyst = SocialAnalyst(heuristics=_make_heuristics())

    # Empty payload — the agent emits an empty verdict list, but the
    # yielded Event must still appear (Rule 1 is shape-not-size).
    state: dict = {"social_data": {}}
    ctx = _make_ctx(state)

    events: list = []
    async for event in analyst._run_async_impl(ctx):
        events.append(event)

    assert len(events) == 1, (
        f"expected exactly one yielded Event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    assert "social_verdicts" in delta
    assert isinstance(delta["social_verdicts"], list)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_social_state_delta.py -v`

Expected: the test FAILs with `AssertionError: expected exactly one yielded Event; got 0`.

- [ ] **Step 3: Apply the minimal change to `src/agents/analysts/social/agent.py`**

Update the import at line 30 — currently:

```python
from google.adk.events import Event
```

— to:

```python
from google.adk.events import Event, EventActions
```

Replace lines 119-127 — currently:

```python
        # Write the verdict list so the after_agent_callback can read it.
        state["social_verdicts"] = verdicts

        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        _trace_maybe(ctx.session.state, "02_social_verdict", verdicts)

        # No events emitted — pure state mutation, same as RiskGateAgent.
        return
        yield  # required to make this an async generator
```

— with:

```python
        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        # Trace before the yield so the recorded payload matches the
        # state_delta value.
        _trace_maybe(ctx.session.state, "02_social_verdict", verdicts)

        # Contract Rule 1 — yield the state_delta so the write survives
        # ADK's SessionService.append_event boundary.  Direct dict
        # mutation alone is lost on persistent session backends.  See
        # ``docs/contract-invariants.md`` §C-Rule 1.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={"social_verdicts": verdicts}),
        )
```

Update the `_run_async_impl` docstring's `Yields:` block at lines 96-98 — currently:

```python
        Yields:
            Nothing — state mutation is written directly, matching the pattern
            used by MemoryWriter, RiskGateAgent, and EvidenceWriter.
```

— so it reads:

```python
        Yields:
            One ``Event`` whose ``actions.state_delta`` carries the
            ``social_verdicts`` list.  Conforms to contract Rule 1; no
            direct ``state[k] = v`` write is performed.
```

And update the class docstring at lines 41-48 — replace "and writes ``state["social_verdicts"]``" with "and yields an ``Event`` whose ``state_delta`` carries ``social_verdicts``". Likewise update the module docstring at lines 10-13 — replace "writes ``state["social_verdicts"]`` directly to session state" with "yields an Event whose ``state_delta`` carries ``social_verdicts``".

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_social_state_delta.py -v`

Expected: the test PASSes.

- [ ] **Step 5: Re-run pre-existing social tests to catch regressions**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_social_analyst_run.py tests/unit/test_social_fetch.py -v`

Expected: all pre-existing tests PASS. The existing tests in `test_social_analyst_run.py` consume the async generator with `async for _ in analyst._run_async_impl(ctx): pass` and then read `state["social_verdicts"]` directly — the test now reads the post-yield state, but the existing tests' direct read still works because they mutate the dict via `state.update` is **not** done by the test, so the existing assertions on `state["social_verdicts"]` after consuming the generator would fail. Verify carefully: the existing tests' direct read **breaks** unless either (a) the agent keeps its in-tick direct write, or (b) the tests merge the yielded delta themselves.

If `tests/unit/test_social_analyst_run.py` fails because the test reads `state["social_verdicts"]` directly without merging the yielded delta, edit `tests/unit/test_social_analyst_run.py` to merge the delta in each test. Specifically, replace each occurrence of:

```python
    async for _ in analyst._run_async_impl(ctx):
        pass
```

— with the four-line pattern:

```python
    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in analyst._run_async_impl(ctx):
        state.update(_event.actions.state_delta)
```

There are five such occurrences in `tests/unit/test_social_analyst_run.py` (one per `@pytest.mark.asyncio` test). Update all five.

Re-run after that edit:

```
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_social_analyst_run.py tests/unit/test_social_fetch.py -v
```

Expected: all PASS.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/agents/analysts/social/agent.py tests/unit/agents/analysts/test_social_state_delta.py tests/unit/test_social_analyst_run.py`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/social/agent.py tests/unit/agents/analysts/test_social_state_delta.py tests/unit/test_social_analyst_run.py
git commit -m "$(cat <<'EOF'
fix(analysts/social): yield state_delta instead of direct state mutation

SocialAnalyst._run_async_impl previously wrote
state["social_verdicts"] directly and used the return/yield no-op
generator trick. Contract Rule 1 (docs/contract-invariants.md §C-Rule 1)
requires every state write to ride on an Event whose
actions.state_delta carries it. Replace with a single yielded Event
mirroring the shape used by MemoryWriter / Executor / TechnicalAnalyst.
Update the pre-existing analyst tests to merge the yielded delta before
reading state.

Part of A1 mechanical contract-conformance work.
EOF
)"
```

---

## Task 3: Convert RiskGateAgent to yield `state_delta`

**A1.3** — Rule 1 conversion for `src/agents/risk_gate/agent.py:88-89`. Slight twist: the trace call at lines 92-96 reads `state["final_orders"]` and `state["risk_clamps_applied"]` — those reads must be re-pointed at local variables so the trace records the same payload the yield carries.

**Files:**
- Modify: `src/agents/risk_gate/agent.py:34-99`
- Create: `tests/integration/test_risk_gate_state_delta.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_risk_gate_state_delta.py` with these exact contents:

```python
"""Rule 1 conformance test for ``RiskGateAgent``.

RiskGate previously wrote ``final_orders`` and ``risk_clamps_applied``
directly to ``ctx.session.state`` and used the ``return / yield`` no-op
generator trick.  Contract Rule 1 (``docs/contract-invariants.md``
§C-Rule 1) demands a yielded ``Event`` whose ``actions.state_delta``
carries the writes.  This test locks the new shape in.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.risk_gate.agent import RiskGateAgent
from broker.fake import FakeBroker


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK ``InvocationContext`` double for RiskGate.

    RiskGate reads ``ctx.session.state`` and uses ``ctx.invocation_id`` in
    the yielded ``Event``; the broker is injected through the agent's
    ``broker`` field, not via the context.
    """

    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_risk_gate_yields_state_delta_with_orders_and_clamps() -> None:
    """``_run_async_impl`` must yield one ``Event`` whose ``state_delta``
    carries both ``final_orders`` and ``risk_clamps_applied`` in a single
    payload.

    Why a single Event: the two writes are part of the same logical
    boundary (RiskGate's output handshake to Executor) and the contract
    treats co-emitted writes as one atomic update.
    """

    broker = FakeBroker(
        starting_cash=10_000.0,
        prices={"AAPL": 200.0, "MSFT": 300.0},
    )
    agent = RiskGateAgent(broker=broker)

    # The decision shape matches the existing integration test in
    # ``tests/integration/test_risk_gate_agent.py``.  AAPL has a positive
    # target weight so an order is generated; MSFT is left at zero.
    state: dict = {
        "strategist_decision": {
            "target_weights": {"AAPL": 0.05, "MSFT": 0.0},
            "decision_tag":   "test",
            "reasoning":      "ok",
            "updated_thesis": "ok",
            "confidence":     0.7,
            "new_positions":  {},
            "close_reasons":  {},
        },
        "positions": {},
    }
    ctx = _make_ctx(state)

    events: list = []
    async for event in agent._run_async_impl(ctx):
        events.append(event)

    assert len(events) == 1, (
        f"expected exactly one yielded Event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    assert "final_orders" in delta
    assert "risk_clamps_applied" in delta
    assert isinstance(delta["final_orders"], list)
    assert isinstance(delta["risk_clamps_applied"], list)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_risk_gate_state_delta.py -v`

Expected: the test FAILS with `AssertionError: expected exactly one yielded Event; got 0`.

- [ ] **Step 3: Apply the minimal change to `src/agents/risk_gate/agent.py`**

Update the import at line 9 — currently:

```python
from google.adk.events import Event
```

— to:

```python
from google.adk.events import Event, EventActions
```

Then replace lines 86-99 — currently:

```python
        orders = weights_to_orders(proposed, portfolio, prices) if self.broker else []

        state["final_orders"] = [o.model_dump() for o in orders]
        state["risk_clamps_applied"] = [c.model_dump() for c in clamps]

        # Surface trace — record clamped weights and generated orders.
        _trace_maybe(state, "06_risk_gate_out", {
            "clamped_weights": proposed,
            "orders": state["final_orders"],
            "clamps": state["risk_clamps_applied"],
        })

        return
        yield  # required to make this an async generator
```

— with:

```python
        orders = weights_to_orders(proposed, portfolio, prices) if self.broker else []

        # Snapshot the JSON-friendly payloads into local variables so the
        # trace (below) and the yielded ``state_delta`` (further below)
        # both reference the same in-memory list rather than reading
        # back through ``state`` (which, post-Rule-1, the agent no longer
        # writes to directly).
        final_orders        = [o.model_dump() for o in orders]
        risk_clamps_applied = [c.model_dump() for c in clamps]

        # Surface trace — record clamped weights and generated orders.
        # Reads from the local variables, not from ``state``, because the
        # state_delta has not been merged yet at this point.
        _trace_maybe(state, "06_risk_gate_out", {
            "clamped_weights": proposed,
            "orders":          final_orders,
            "clamps":          risk_clamps_applied,
        })

        # Contract Rule 1 — yield a single Event whose state_delta
        # carries both writes.  RiskGate's output handshake to the
        # Executor (final_orders) and to observability
        # (risk_clamps_applied) is one logical step; co-emitting keeps
        # the merge atomic on the SessionService.  See
        # ``docs/contract-invariants.md`` §C-Rule 1.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "final_orders":        final_orders,
                "risk_clamps_applied": risk_clamps_applied,
            }),
        )
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_risk_gate_state_delta.py -v`

Expected: the test PASSes.

- [ ] **Step 5: Re-run the pre-existing RiskGate integration test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_risk_gate_agent.py -v`

Expected: the test in `test_risk_gate_agent.py` previously asserted `"final_orders" in state` and `"risk_clamps_applied" in state` after consuming the generator with `async for _ in agent._run_async_impl(ctx): pass`. Post-change, those keys live in the yielded delta, not in `state`. Edit `tests/integration/test_risk_gate_agent.py` lines 35-39 — currently:

```python
    ctx = _make_ctx(state)
    async for _ in agent._run_async_impl(ctx):
        pass
    assert "final_orders" in state
    assert "risk_clamps_applied" in state
```

— so it reads:

```python
    ctx = _make_ctx(state)
    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in agent._run_async_impl(ctx):
        state.update(_event.actions.state_delta)
    assert "final_orders" in state
    assert "risk_clamps_applied" in state
```

The mock `_make_ctx` in this file lacks `invocation_id`; either rely on MagicMock's attribute autogeneration (the Event Pydantic model coerces it to a string via its model validation — verify: if this fails, set `ctx.invocation_id = "test-invocation"` explicitly in `_make_ctx`).

Re-run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_risk_gate_agent.py -v`

Expected: PASS. If `Event` rejects the MagicMock-derived `invocation_id`, set `ctx.invocation_id = "test-invocation"` in `_make_ctx` at line 12 (insert one line `ctx.invocation_id = "test-invocation"` before `return ctx`). Re-run; PASS.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/agents/risk_gate/agent.py tests/integration/test_risk_gate_state_delta.py tests/integration/test_risk_gate_agent.py`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/agents/risk_gate/agent.py tests/integration/test_risk_gate_state_delta.py tests/integration/test_risk_gate_agent.py
git commit -m "$(cat <<'EOF'
fix(risk_gate): yield state_delta with final_orders + risk_clamps_applied

RiskGateAgent._run_async_impl previously wrote both keys directly to
ctx.session.state and used the return/yield no-op generator trick.
Contract Rule 1 (docs/contract-invariants.md
§C-Rule 1) requires both writes to ride on a yielded Event whose
actions.state_delta carries them in a single atomic payload. The
trace call now reads the JSON-friendly payloads from local variables
so it records the same data the state_delta yields. Update the
pre-existing integration test to merge the yielded delta before
reading state.

Part of A1 mechanical contract-conformance work.
EOF
)"
```

---

## Task 4: Seed `as_of` and `tick_phase` in the live tick builder

**A1.4** — `src/orchestrator/tick.py:_build_initial_state` currently omits `as_of` and `tick_phase`. Backtest sets both at `src/backtest/driver.py:194-195`. Add wall-clock `as_of` and `"live"` `tick_phase` to the live builder.

Critically: the `STOCKBOT_STRICT_AS_OF=1` env var (in `src/data/timeguard.py:resolve_as_of`) is set during backtest runs and turns the wall-clock fallback into a veto. Live must **not** set that env var; the test must assert that.

**Files:**
- Modify: `src/orchestrator/tick.py:60-100`
- Create: `tests/unit/orchestrator/test_tick_as_of_phase.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/orchestrator/test_tick_as_of_phase.py` with these exact contents:

```python
"""Contract test: live ``_build_initial_state`` must seed ``as_of`` +
``tick_phase``.

Backtest already seeds both at ``src/backtest/driver.py:194-195``.  Live
historically omitted them and relied on the
``resolve_as_of(..., allow_wallclock=True)`` fallback at every consumer.
Contract Rule 7 ("lifecycle owns Phase 2 hydration") demands a single
authoritative writer; seeding once in the builder closes the asymmetry.

"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.tick import _build_initial_state


@pytest.mark.asyncio
async def test_build_initial_state_seeds_as_of_and_tick_phase() -> None:
    """``_build_initial_state`` must populate both ``as_of`` (timezone-aware
    UTC datetime near wall-clock) and ``tick_phase`` (the literal string
    ``"live"``) in the returned state dict."""

    # Mock broker: ``get_portfolio`` returns a portfolio whose ``model_dump``
    # produces a serialisable dict.  ``MagicMock`` proxies cover the rest.
    broker = MagicMock()
    portfolio = MagicMock()
    portfolio.model_dump.return_value = {"cash": 0.0, "positions": {}}
    broker.get_portfolio = AsyncMock(return_value=portfolio)

    # Patch the reference-price fetch so the test doesn't touch yfinance.
    with patch(
        "orchestrator.tick._fetch_reference_prices",
        new=AsyncMock(return_value={}),
    ):
        before = datetime.now(tz=UTC)
        state = await _build_initial_state(
            broker, tick_id="tick-test-001", tickers=["AAPL"],
        )
        after = datetime.now(tz=UTC)

    # ``as_of`` must be present and timezone-aware UTC.
    assert "as_of" in state, "live builder must seed state['as_of']"
    as_of = state["as_of"]
    assert isinstance(as_of, datetime)
    assert as_of.tzinfo is not None, "as_of must be timezone-aware"
    assert as_of.utcoffset() == timedelta(0), "as_of must be in UTC"

    # ``as_of`` must lie within the wall-clock window the test captured.
    # A 5-second slack window covers any reasonable in-process clock drift.
    assert before - timedelta(seconds=5) <= as_of <= after + timedelta(seconds=5), (
        f"as_of {as_of} must be within wall-clock window "
        f"[{before}, {after}]"
    )

    # ``tick_phase`` must be the literal string ``"live"``.
    assert state.get("tick_phase") == "live", (
        f"live builder must seed tick_phase='live'; got {state.get('tick_phase')!r}"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_tick_as_of_phase.py -v`

Expected: the test FAILS with `AssertionError: live builder must seed state['as_of']`.

- [ ] **Step 3: Apply the minimal change to `src/orchestrator/tick.py`**

The file already imports `from datetime import UTC, date, datetime` (line 7) and `_build_initial_state` already calls `datetime.now(tz=UTC)` elsewhere in this module (line 117). No new imports are needed.

Replace lines 85-100 — currently:

```python
    return {
        "tick_id": tick_id,
        "tickers": tickers,
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "",
        "positions": {},
        "portfolio": portfolio.model_dump(mode="json"),
        # Dump each PriceHistory to a JSON-safe dict so the ADK SqlSessionService
        # (which serialises state via plain json.dumps) doesn't choke on Pydantic
        # objects.  The technical extractor coerces dicts back to PriceHistory
        # on the consumer side — see src/contract/extractors/technical.py.
        "reference_prices": {
            sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
        },
    }
```

— with:

```python
    return {
        "tick_id": tick_id,
        # Phase 2 lifecycle handshake — the live builder is the single
        # authoritative writer of ``as_of`` and ``tick_phase``.  Backtest
        # sets the equivalents in ``src/backtest/driver.py``.  These
        # keys are documented in ``docs/contract-invariants.md`` §A.
        # Note: ``STOCKBOT_STRICT_AS_OF=1`` is
        # set by backtest runs to veto wall-clock fallback at consumers
        # like ``data.timeguard.resolve_as_of``; live must NOT set that
        # env var, so this wall-clock seed lands cleanly.
        "as_of":      datetime.now(tz=UTC),
        "tick_phase": "live",
        "tickers": tickers,
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "",
        "positions": {},
        "portfolio": portfolio.model_dump(mode="json"),
        # Dump each PriceHistory to a JSON-safe dict so the ADK SqlSessionService
        # (which serialises state via plain json.dumps) doesn't choke on Pydantic
        # objects.  The technical extractor coerces dicts back to PriceHistory
        # on the consumer side — see src/contract/extractors/technical.py.
        "reference_prices": {
            sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
        },
    }
```

Also extend the function's docstring at lines 61-75 — replace the existing docstring text with:

```python
    """Build the initial pipeline state for one live tick.

    Reads the live portfolio from the broker, fetches reference prices,
    and seeds the Phase 2 lifecycle keys (``tick_id``, ``as_of``,
    ``tick_phase``) plus the cross-tick fields the pipeline expects.
    The cross-tick fields (``memory_buffer``, ``day_digest``, ``thesis``,
    ``positions``) are seeded empty here — true persistence-backed
    rehydration is tracked in ``docs/todo-fixes.md`` item 2.5.3 and is
    out of scope for A1.

    Args:
        broker: Any broker implementing ``get_portfolio() -> Portfolio``.
        tick_id: The unique identifier string for this tick.
        tickers: The list of watchlist ticker symbols for this tick.

    Returns:
        A dict containing all keys the pipeline expects at startup,
        including a JSON-serialisable portfolio snapshot under
        ``"portfolio"`` and a wall-clock UTC ``as_of`` datetime under
        ``"as_of"`` (tick_phase is the literal string ``"live"``).
    """
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_tick_as_of_phase.py -v`

Expected: the test PASSes.

- [ ] **Step 5: Re-run the pre-existing tick tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_tick_entrypoint.py tests/unit/test_tick_state.py -v`

Expected: PASS. These tests don't touch `_build_initial_state` directly so they should be unaffected.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/orchestrator/tick.py tests/unit/orchestrator/test_tick_as_of_phase.py`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/tick.py tests/unit/orchestrator/test_tick_as_of_phase.py
git commit -m "$(cat <<'EOF'
fix(orchestrator/tick): seed as_of (wall clock) + tick_phase in live builder

The live _build_initial_state previously omitted as_of and tick_phase,
relying on resolve_as_of(..., allow_wallclock=True) fallback at every
consumer. Backtest already seeds both at driver.py:194-195. Set
as_of=datetime.now(tz=UTC) and tick_phase="live" once in the builder
so the live path matches backtest's Phase 2 handshake. STOCKBOT_STRICT
_AS_OF=1 (backtest's wall-clock veto) must remain unset in live; a
test pins that contract.

Part of A1 mechanical contract-conformance work.
EOF
)"
```

---

## Task 5: Resolve singular/plural verdict naming drift in the spec

**A1.5** — decision (baked in): update the spec to match the code's plural form. No code changes.

**Files:**
- Modify: `docs/contract-invariants.md`
- Modify: `docs/Phase8-contract-audit-fixes/contract-audit.md`

- [ ] **Step 1: Verify the current spec uses singular forms**

Run: `grep -n "technical_verdict\|fundamental_verdict\|news_verdict\|social_verdict" docs/contract-invariants.md docs/Phase8-contract-audit-fixes/contract-audit.md`

Expected: many matches in both files, in §A schema rows and in prose.

- [ ] **Step 2: Edit `docs/contract-invariants.md`**

In `docs/contract-invariants.md`:

Update lines 75-78 of the §A schema table — currently:

```
| `technical_verdict` | TechnicalAnalyst (`output_key`) | tick-scoped | TechnicalAnalyst LLM call | Phase 3 | n/a | Unique key — see §C-Rule 4. |
| `fundamental_verdict` | FundamentalAnalyst (`output_key`) | tick-scoped | FundamentalAnalyst LLM call | Phase 3 | n/a | Unique key — see §C-Rule 4. |
| `news_verdict` | NewsAnalyst (`output_key`) | tick-scoped | NewsAnalyst LLM call | Phase 3 | n/a | Unique key — see §C-Rule 4. |
| `social_verdict` | SocialAnalyst (`output_key`) | tick-scoped | SocialAnalyst LLM call | Phase 3 | n/a | Unique key — see §C-Rule 4. |
```

— so they read:

```
| `technical_verdicts` | TechnicalAnalyst (`state_delta`) | tick-scoped | TechnicalAnalyst deterministic extractor | Phase 3 | n/a | Unique key — see §C-Rule 4. Yielded as a list of per-ticker verdict dicts; written via `state_delta` (Rule 1) — TechnicalAnalyst is a BaseAgent, not an LlmAgent, so no `output_key`. |
| `fundamental_verdicts` | FundamentalAnalyst (`output_key`) | tick-scoped | FundamentalAnalyst LLM call | Phase 3 | n/a | Unique key — see §C-Rule 4. |
| `news_verdicts` | NewsAnalyst (`output_key`) | tick-scoped | NewsAnalyst LLM call | Phase 3 | n/a | Unique key — see §C-Rule 4. |
| `social_verdicts` | SocialAnalyst (`state_delta`) | tick-scoped | SocialAnalyst deterministic extractor | Phase 3 | n/a | Unique key — see §C-Rule 4. Yielded as a list of per-ticker verdict dicts; written via `state_delta` (Rule 1) — SocialAnalyst is a BaseAgent, not an LlmAgent, so no `output_key`. |
```

Then update the four singular references in the prose at lines 244-245 — currently:

```
**Implication:** the AnalystPool's four analysts must each have a unique
`output_key`. The §A table records the four current keys
(`technical_verdict`, `fundamental_verdict`, `news_verdict`,
`social_verdict`) explicitly to prevent future drift.
```

— so it reads:

```
**Implication:** the AnalystPool's four analysts must each have a unique
output key. The §A table records the four current keys
(`technical_verdicts`, `fundamental_verdicts`, `news_verdicts`,
`social_verdicts`) explicitly to prevent future drift. Two analysts
(FundamentalAnalyst, NewsAnalyst) use ADK's `output_key` mechanism;
the other two (TechnicalAnalyst, SocialAnalyst) are BaseAgent
subclasses and yield their writes via `state_delta` (Rule 1) — the
uniqueness requirement of Rule 4 is satisfied regardless of mechanism.
```

- [ ] **Step 3: Edit `docs/Phase8-contract-audit-fixes/contract-audit.md`**

In `docs/Phase8-contract-audit-fixes/contract-audit.md`:

Update the summary-table row at line 43 — currently:

```
| §A | `technical_verdict` / `fundamental_verdict` / `news_verdict` / `social_verdict` | deviation | medium | Code uses plural keys (`*_verdicts`); spec uses singular. Naming drift. Also: technical + social use direct mutation, not `output_key`. |
```

— so it reads:

```
| §A | `technical_verdicts` / `fundamental_verdicts` / `news_verdicts` / `social_verdicts` | resolved (A1.5) | — | Spec aligned to the code's plural form 2026-05-20. Technical + social still write via `state_delta` (Rule 1), not `output_key` (resolved by A1.1 / A1.2). |
```

Update the heading at line 183 — currently:

```
### `technical_verdict` / `fundamental_verdict` / `news_verdict` / `social_verdict` — DEVIATION (medium)
```

— so it reads:

```
### `technical_verdicts` / `fundamental_verdicts` / `news_verdicts` / `social_verdicts` — RESOLVED (A1.5 + A1.1/A1.2)
```

Update the "Naming drift" prose at lines 187-200 — currently:

```
**Naming drift.** Spec uses singular (`technical_verdict`); code uses
plural (`technical_verdicts`). Verified:

- Fundamental: `src/agents/analysts/fundamental/agent.py:164` —
  `output_key="fundamental_verdicts"`.
- News: `src/agents/analysts/news/agent.py:119` —
  `output_key="news_verdicts"`.
- Technical: `src/agents/analysts/technical/agent.py:129` —
  `state["technical_verdicts"] = verdicts` (no `output_key`; see below).
- Social: `src/agents/analysts/social/agent.py:120` —
  `state["social_verdicts"] = verdicts` (no `output_key`; see below).

Pick one form (plural is what's wired) and align the spec, or rename
the four code sites to match the spec.
```

— so it reads:

```
**Naming drift — RESOLVED (A1.5, 2026-05-20).** The spec has been
updated to match the code's plural form. The four keys as wired in
code:

- Fundamental: `src/agents/analysts/fundamental/agent.py:164` —
  `output_key="fundamental_verdicts"`.
- News: `src/agents/analysts/news/agent.py:119` —
  `output_key="news_verdicts"`.
- Technical: `src/agents/analysts/technical/agent.py` — yields
  `state_delta={"technical_verdicts": ...}` (resolved by A1.1).
- Social: `src/agents/analysts/social/agent.py` — yields
  `state_delta={"social_verdicts": ...}` (resolved by A1.2).
```

Update the closing sentence in this section at lines 217-219 — currently:

```
**Why it hasn't bitten:** in-memory session services keep mutations
visible; the contract failure surfaces only with a persistence-backed
session service.
```

— so it reads:

```
**Why the previous Rule 1 deviation hadn't bitten before A1.1/A1.2:**
in-memory session services keep direct mutations visible across the
SequentialAgent boundary; the contract failure would surface only on a
persistence-backed session service. A1.1 / A1.2 close the gap.
```

Update the "Cross-cutting structural notes — Naming drift" section at lines 663-676 — currently:

```
### Naming drift between spec and code

The audit found one naming drift (singular vs plural verdict keys; see
§A `*_verdict` finding). Recommendation: standardise on whichever form
is cheapest to change — likely the spec, since the plural-keyed code is
shipping. If choosing to fix the code instead, the refactor touches:

- `src/agents/analysts/technical/agent.py:129`
- `src/agents/analysts/fundamental/agent.py:164`
- `src/agents/analysts/news/agent.py:119`
- `src/agents/analysts/social/agent.py:120`
- every prompt and consumer that reads `{technical,fundamental,news,
  social}_verdicts` (grep target).
```

— so it reads:

```
### Naming drift between spec and code — RESOLVED (A1.5, 2026-05-20)

The audit's one naming drift (singular vs plural verdict keys) was
resolved by updating the spec to match the shipping plural form. No
code changes were needed. Historical context retained here so future
audits can trace the decision.
```

- [ ] **Step 4: Verify no singular references remain (other than historical context)**

Run: `grep -n "technical_verdict[^s]\|fundamental_verdict[^s]\|news_verdict[^s]\|social_verdict[^s]" docs/contract-invariants.md docs/Phase8-contract-audit-fixes/contract-audit.md || echo "OK no singular forms"`

Expected: `OK no singular forms`. If any singular form remains in either file (other than the deliberate historical-context phrasing in `contract-audit.md`), fix it.

- [ ] **Step 5: Commit**

```bash
git add docs/contract-invariants.md docs/Phase8-contract-audit-fixes/contract-audit.md
git commit -m "$(cat <<'EOF'
docs(specs): align verdict key names to code's plural form

The contract spec at docs/contract-invariants.md
§A used singular keys (technical_verdict, fundamental_verdict,
news_verdict, social_verdict); the shipping code uses plural
(*_verdicts). Decision baked in by the 2026-05-20 contract-conformance
brainstorm: update the spec to match the code. No code changes. Update
the §A schema rows, the Rule 4 prose, and the audit's findings to
reflect the resolved form.

Part of A1 mechanical contract-conformance work.
EOF
)"
```

---

## Task 6: Promote `tick_phase` / `last_executed_tick_id` / `last_snapshot` to §A

**A1.6 spec edit** — add three new §A rows documenting the contract for keys already written by the code.

**Files:**
- Modify: `docs/contract-invariants.md`

- [ ] **Step 1: Edit `docs/contract-invariants.md`**

In `docs/contract-invariants.md`, add three new rows to the §A schema table. After the (now updated by Task 5) `social_verdicts` row, append three rows so the table tail reads:

```
| `social_verdicts` | SocialAnalyst (`state_delta`) | tick-scoped | SocialAnalyst deterministic extractor | Phase 3 | n/a | Unique key — see §C-Rule 4. Yielded as a list of per-ticker verdict dicts; written via `state_delta` (Rule 1) — SocialAnalyst is a BaseAgent, not an LlmAgent, so no `output_key`. |
| `tick_phase` | Tick bootstrap | tick-scoped | Lifecycle wrapper | Phase 2 | n/a | Literal string — live sets `"live"`; backtest sets the schedule's `tick.phase` (`"open"` / `"close"`). Decorative for the pipeline today; consumed by observability/tracing surfaces. Documented in §A so future agents that branch on phase have a contractual hook. |
| `last_executed_tick_id` | Executor (`state_delta`) | tick-scoped | Executor's idempotency handshake | Phase 3 | n/a | Set to the current `tick_id` after the Executor finishes its run. Read by the Executor itself at the top of the next invocation as an idempotency guard. Written via `state_delta` (Rule 1); a paired direct write is currently retained as defensive belt-and-braces (out of A1 scope — see todo-fixes 2.5.x). |
| `last_snapshot` | Snapshotter (`state_delta`) | tick-scoped | Snapshotter's pipeline-completion handshake | Phase 3 | n/a | Set at the end of the tick. Read by the backtest driver's per-tick assertion (`src/backtest/driver.py:393-401`) to confirm the pipeline reached the Snapshotter. Written via `state_delta`; the paired direct write is defensive (out of A1 scope). |
```

Then immediately below the table (currently lines 80-84) — currently:

```
The four cross-tick rows (`positions`, `memory_buffer`, `day_digest`,
`thesis`) all depend on the persistence subsystem described in §E.
Until that subsystem exists, those rows describe target-state and any
lifecycle that ships without true persistence for them violates the
contract.
```

— extend the paragraph by appending a sibling paragraph immediately after, so the section reads:

```
The four cross-tick rows (`positions`, `memory_buffer`, `day_digest`,
`thesis`) all depend on the persistence subsystem described in §E.
Until that subsystem exists, those rows describe target-state and any
lifecycle that ships without true persistence for them violates the
contract.

Two of the tick-scoped rows (`last_executed_tick_id`, `last_snapshot`)
exist as **in-tick handshake keys** — written and read inside a single
tick, with no cross-tick contract. They are listed in §A only because
their owners and refresh points are stable enough to lock down; they
do not require persistence and the lifecycle wrapper does not touch
them.
```

- [ ] **Step 2: Verify the three new rows are present**

Run: `grep -E '^\| `(tick_phase|last_executed_tick_id|last_snapshot)`' docs/contract-invariants.md`

Expected: three lines matching, one per new row.

- [ ] **Step 3: Commit**

```bash
git add docs/contract-invariants.md
git commit -m "$(cat <<'EOF'
docs(specs): add tick_phase / last_executed_tick_id / last_snapshot to §A

Three keys that the running code already writes were absent from the
contract's §A field schema: tick_phase (lifecycle handshake set by
Phase 2 in both lifecycles), last_executed_tick_id (Executor's
idempotency guard), and last_snapshot (Snapshotter's pipeline-
completion handshake). All three are tick-scoped; the latter two are
in-tick-only handshakes between specific agents and their consumers.
Document the contract so future audits don't re-flag them as
unmodelled.

Part of A1 mechanical contract-conformance work.
EOF
)"
```

---

## Task 7: Fold `watchlist` into `tickers`

**A1.6 code-aligner** — backtest seeds both `watchlist` and `tickers` with identical content; the driver reads `state.get("watchlist", [])` at two sites. Drop the separate seed and re-point the consumers at `tickers`. Live has no `watchlist` reference, so no change there.

**Files:**
- Modify: `src/backtest/runner.py:475-491`
- Modify: `src/backtest/driver.py:205, 283`
- Create: `tests/unit/backtest/test_driver_consumes_tickers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/backtest/test_driver_consumes_tickers.py` with these exact contents:

```python
"""Contract test: backtest driver must read ``state["tickers"]`` (not
``state["watchlist"]``) for the per-tick broker price refresh.

A1.6 folds the two-key duplication into a single key. The driver's
``_refresh_broker_prices`` call inside ``Driver.run`` is the canonical
consumer; after A1.6 it must source the watchlist from
``state["tickers"]`` so live (which has no ``watchlist`` key) and
backtest agree on the same field.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backtest.driver import Driver
from backtest.schedule import Tick
from broker.fake import FakeBroker


def _make_driver(tmp_path: Path, broker: FakeBroker) -> Driver:
    """Minimal Driver fixture — mirrors the pattern in
    ``tests/unit/backtest/test_driver_portfolio_refresh.py``."""

    (tmp_path / "manifest.json").write_text("{}")
    return Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="test-window",
        failure_abort_ratio=0.99,
        enforce_pipeline_completion=False,
    )


@pytest.mark.asyncio
async def test_driver_uses_tickers_for_price_refresh(tmp_path: Path) -> None:
    """The driver's per-tick price refresh must source its symbol list
    from ``state["tickers"]``. Confirmed by deliberately omitting
    ``state["watchlist"]`` — the refresh must still see the tickers.
    """

    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    driver = _make_driver(tmp_path, broker)

    # ``state`` carries only "tickers", not "watchlist". Pre-A1.6 the
    # driver would read state.get("watchlist", []) and pass [] to the
    # refresh helper.
    state: dict = {
        "tickers":   ["AAPL"],
        "portfolio": (await broker.get_portfolio()).model_dump(mode="json"),
        "positions": {},
    }

    # Capture the symbol list the driver hands to _refresh_broker_prices.
    captured: list[list[str]] = []
    original_refresh = driver._refresh_broker_prices

    def _spy_refresh(watchlist, tick):
        """Record the symbol list the driver passes to the refresh hook."""

        captured.append(list(watchlist))
        original_refresh(watchlist, tick)

    async def _noop_runner(*args, **kwargs):
        """ADK Runner stand-in — yields nothing."""

        if False:                          # pragma: no cover
            yield None

    schedule = [Tick(as_of=datetime(2025, 9, 2, 13, 30, tzinfo=UTC), phase="open")]

    with patch.object(driver, "_refresh_broker_prices", _spy_refresh), \
         patch(
             "backtest.driver.Runner",
             return_value=MagicMock(run_async=_noop_runner),
         ):
        await driver.run(state, schedule)

    # The driver must have passed the AAPL list — proving it read
    # ``state["tickers"]`` rather than the missing ``state["watchlist"]``.
    assert captured == [["AAPL"]], (
        f"driver must source the refresh symbol list from state['tickers']; "
        f"captured: {captured!r}"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_driver_consumes_tickers.py -v`

Expected: FAIL with `AssertionError: ... captured: [[]]` — the driver currently reads `state.get("watchlist", [])`, the test's state has no `watchlist` key, so the captured list is the empty list default.

- [ ] **Step 3: Update `src/backtest/driver.py`**

Find the two consumer sites:

Run: `grep -n 'state.get("watchlist"' src/backtest/driver.py`

Expected: two lines (around lines 205 and 283 per the audit, exact numbers may have drifted by ±5).

Edit each occurrence — replace:

```python
state.get("watchlist", [])
```

— with:

```python
state.get("tickers", [])
```

Use the Edit tool with `replace_all=true` for `state.get("watchlist", [])` -> `state.get("tickers", [])`.

Then update the surrounding comment at line 204-206 — currently:

```python
            # Update FakeBroker price to the day's open or close.
            self._refresh_broker_prices(state.get("watchlist", []), tick)
```

— so it reads:

```python
            # Update FakeBroker price to the day's open or close.  The
            # symbol list comes from ``state["tickers"]`` — A1.6 folded
            # the redundant ``state["watchlist"]`` key away.  Live has
            # no ``watchlist`` either, so this aligns the two
            # lifecycles on a single key.
            self._refresh_broker_prices(state.get("tickers", []), tick)
```

(The second occurrence — around line 283 — gets only the `watchlist` -> `tickers` swap and no comment change; that call site has its own surrounding context.)

- [ ] **Step 4: Update `src/backtest/runner.py`**

Edit `src/backtest/runner.py` lines 475-491 — currently:

```python
            state: dict = {
                "tickers":          wl_filtered,
                "watchlist":        wl_filtered,
                "portfolio":        portfolio.model_dump(mode="json"),
                "positions":        {},
                "memory_buffer":    [],
                "day_digest":       "",
                "thesis":           "",
                # Dump each PriceHistory to a JSON-safe dict so the ADK
                # SqlSessionService (plain json.dumps under the hood) doesn't
                # choke on Pydantic objects.  Mirrors orchestrator.tick.  The
                # technical extractor coerces dicts back to PriceHistory on
                # read — see src/contract/extractors/technical.py.
                "reference_prices": {
                    sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
                },
            }
```

— so it reads:

```python
            state: dict = {
                # A1.6 — ``tickers`` is the single canonical watchlist
                # key.  The previous duplicate ``watchlist`` seed has
                # been dropped; the driver now sources its per-tick
                # price refresh from ``state["tickers"]`` so live and
                # backtest agree on the same field.
                "tickers":          wl_filtered,
                "portfolio":        portfolio.model_dump(mode="json"),
                "positions":        {},
                "memory_buffer":    [],
                "day_digest":       "",
                "thesis":           "",
                # Dump each PriceHistory to a JSON-safe dict so the ADK
                # SqlSessionService (plain json.dumps under the hood) doesn't
                # choke on Pydantic objects.  Mirrors orchestrator.tick.  The
                # technical extractor coerces dicts back to PriceHistory on
                # read — see src/contract/extractors/technical.py.
                "reference_prices": {
                    sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
                },
            }
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_driver_consumes_tickers.py -v`

Expected: PASS.

- [ ] **Step 6: Run pre-existing driver tests to catch regressions**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/ -v -m "not slow"`

Expected: all PASS. In particular, `tests/unit/backtest/test_driver_portfolio_refresh.py` constructs state dicts that include both `tickers` and `watchlist` — those tests are unaffected because they include both keys.

- [ ] **Step 7: Verify no stray `state["watchlist"]` reader survives in `src/backtest/`**

Run: `grep -rn 'watchlist' src/backtest/ | grep -v 'wl_filtered\|watchlist_'`

Expected: no remaining references that read `state["watchlist"]` or `state.get("watchlist"`. (Variable names like `wl_filtered` or `watchlist_` prefixes are fine — those refer to local Python variables, not the state key.) If a stray reference remains, re-point it to `tickers`.

- [ ] **Step 8: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/backtest/driver.py src/backtest/runner.py tests/unit/backtest/test_driver_consumes_tickers.py`

Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/backtest/driver.py src/backtest/runner.py tests/unit/backtest/test_driver_consumes_tickers.py
git commit -m "$(cat <<'EOF'
fix(backtest): fold state["watchlist"] into state["tickers"]

Backtest historically seeded both state["watchlist"] and state["tickers"]
with identical content; the driver read state.get("watchlist", []) at
two sites. Live has no watchlist key at all. A1.6 folds the duplicate
away — runner.py drops the watchlist seed, driver.py reads
state.get("tickers", []) at both refresh sites. Single canonical key,
both lifecycles agree.

Part of A1 mechanical contract-conformance work.
EOF
)"
```

---

## Task 8: Full test run + smoke test gate

End-to-end verification that nothing in A1 broke the broader suite or the slow integration test.

**Files:** none modified.

- [ ] **Step 1: Run the full fast suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`

Expected: all PASS. If anything fails, the failure is almost certainly an existing test that read `state["technical_verdicts"]` / `state["social_verdicts"]` / `state["final_orders"]` / `state["risk_clamps_applied"]` after consuming the agent's generator without merging the yielded delta. Apply the same merge pattern from Tasks 2 and 3 — replace `async for _ in <agent>._run_async_impl(ctx): pass` with `async for _event in <agent>._run_async_impl(ctx): state.update(_event.actions.state_delta)`.

If a test fails for an unrelated reason — stop and investigate; do not paper over with retries.

- [ ] **Step 2: Run the integration suite (non-slow)**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/ -m "not slow" -v`

Expected: PASS.

- [ ] **Step 3: Run the slow end-to-end smoke test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`

Expected: PASS. This is the integration gate — it exercises the full pipeline through the backtest driver against a frozen cache, including TechnicalAnalyst, SocialAnalyst, and RiskGate. Any post-A1 incompatibility surfaces here.

Two likely failure modes if it fails:

  1. **The smoke test reads `state["watchlist"]` somewhere.** Re-point to `state["tickers"]`.

  2. **A downstream consumer (e.g. `make_evidence_callback`) reads `state["technical_verdicts"]` / `state["social_verdicts"]` synchronously between the analyst's yield and the next agent.** The ADK Runner merges yielded `state_delta`s between agents in a `SequentialAgent` — but inside a `ParallelAgent`, the four analyst branches run concurrently and the merge happens at the parallel barrier. Verify by inspecting the actual failure: if the after-callback fires inside the same parallel branch as `_run_async_impl`, it reads `cb_ctx.state` (the same dict reference); the post-yield merge should propagate the delta into that dict before the after-callback runs. If it doesn't, the merge timing is the failure cause and **the analyst conversions need a paired direct write after all** — at which point, stop and consult the A1 scope owner. (The plan deliberately did **not** keep direct writes for A1.1/A1.2 per spec; if this fallback proves necessary, treat it as a discovered A1 ambiguity and document it.)

- [ ] **Step 4: Full ruff sweep**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`

Expected: no errors.

- [ ] **Step 5: Append a graphify delta entry**

The technical/social analyst and risk_gate conversions change the structural relationship between these agents and the ADK runtime (yielded Event flow rather than direct state mutation), so per `.claude/CLAUDE.md` this is a non-trivial structural change that warrants a graphify delta.

Append the following dated section to the end of `graphify-out/graph_delta.md` (the file is gitignored — do **not** `git add` it):

```
## 2026-05-20 — A1 mechanical contract-conformance

Three BaseAgent subclasses converted from direct `state[k]=v` writes to
yielded `Event(state_delta=...)` per contract Rule 1. Live tick builder
now seeds `as_of` + `tick_phase`. Backtest `watchlist` key folded into
`tickers`. Spec/audit aligned to plural verdict keys.

- New/changed nodes: none (no new files).
- New/changed edges:
  - `TechnicalAnalyst._run_async_impl` now yields `Event` -> ADK
    SessionService (was: direct dict mutation).
  - `SocialAnalyst._run_async_impl` now yields `Event` -> ADK
    SessionService (was: direct dict mutation).
  - `RiskGateAgent._run_async_impl` now yields `Event` carrying
    `final_orders` + `risk_clamps_applied` (was: direct dict mutation).
  - `orchestrator.tick._build_initial_state` now writes `as_of`
    (wall-clock UTC) + `tick_phase="live"`.
  - `backtest.driver.Driver.run` now reads `state["tickers"]` for the
    per-tick broker price refresh (was: `state["watchlist"]`).
  - `backtest.runner.run_window` no longer seeds `state["watchlist"]`.
- Removed: none.
```

If `graphify-out/graph_delta.md` does not exist on this machine, skip this step — graphify is per-developer.

- [ ] **Step 6: Verify the working tree is clean and the branch is ready**

Run: `git status`

Expected: working tree clean (all commits made). No untracked files other than possibly `graphify-out/graph_delta.md` (which is gitignored).

- [ ] **Step 7: Final commit gate**

Do **not** create an additional commit at this step — every prior task ended with its own focused commit. If the diff between the merge-base and HEAD looks right (`git log --oneline main..HEAD` should show six or seven commits, one per task plus any post-hoc test fixups), A1 is ready for review/PR.

---

## Self-review

**Spec coverage** — every A1 sub-item maps to a task:

- A1.1 TechnicalAnalyst Rule 1 conversion → Task 1.
- A1.2 SocialAnalyst Rule 1 conversion → Task 2.
- A1.3 RiskGate Rule 1 conversion → Task 3.
- A1.4 `as_of` + `tick_phase` in live → Task 4.
- A1.5 Naming-drift spec rename → Task 5.
- A1.6 §A row additions → Task 6.
- A1.6 `watchlist` → `tickers` fold → Task 7.
- End-to-end smoke gate → Task 8.

**Placeholder scan** — every step has exact code, exact paths, exact commands, and exact expected output. No TBDs, no "implement appropriately", no "similar to Task N".

**Type / shape consistency** — the yielded Event shape used in Tasks 1/2/3 is consistent: `Event(author=self.name, invocation_id=ctx.invocation_id, actions=EventActions(state_delta={...}))`. The merge pattern in the test fixups is consistent: `async for _event in <agent>._run_async_impl(ctx): state.update(_event.actions.state_delta)`. The contract reference (`docs/contract-invariants.md` §C-Rule 1) is cited consistently.

**Known ambiguity** — Task 8 Step 3 flags one potential issue: the ParallelAgent merge timing for the analyst-pool branches. If the post-yield state_delta does not propagate into `cb_ctx.state` before the analyst's own `after_agent_callback` runs (`make_evidence_callback`), the after-callback will see an empty/missing verdict list and fail. The spec brief explicitly forbids adding a paired direct write to A1.1/A1.2 — so if this fails in the smoke test, treat as A1 scope ambiguity and consult the spec owner before proceeding. (Empirically: in `tests/unit/test_social_analyst_run.py:test_after_callback_fires_and_writes_evidence`, the after-callback reads `state["social_verdicts"]` from the *same dict reference* that `_run_async_impl` writes; that test already passes today because of the direct write. After A1.2 removes the direct write, the test will need the merge pattern from Task 2 Step 5 — and the after-callback itself must continue to read from `cb_ctx.state` post-merge. The plan handles the test side; if the production after-callback fails for the same reason at smoke-test time, the plan's Task 8 Step 3 is the discovery point.)

---

## Deferred to A2 (and other follow-up workstreams)

The following items were explicitly excluded from A1 by the contract-conformance brainstorm. They are recorded here so a future executor knows what comes next and doesn't get tempted to extend A1.

- **Cross-tick persistence subsystem** — the four high-severity §A deviations (`positions`, `memory_buffer`, `day_digest`, `thesis`). Tracked in `docs/todo-fixes.md` item 2.5.3 and audit-findings F1. Blocked on store-choice decisions (Chroma vs Vertex AI vs SQL-only).

- **Strategist callback restructure** — `_held_view_before_callback` (`src/agents/strategist/agent.py:70`), `_evidence_view_before_callback` (`:176-177`), and `_strategist_validation_callback` (`:388`). These are direct-mutation Rule 1 deviations inside callbacks, which (per Rule 3) cannot yield events. Resolution requires lifting the writes into BaseAgent shims that wrap the Strategist — A2's territory.

- **Analyst fetch + after callbacks** (4 fetch + 4 after across `src/agents/analysts/{technical,fundamental,news,social}/{fetch.py,agent.py}`) — same callback-can't-yield problem; A2.

- **`temp:` prefix application** on `held_positions_view`, `ticker_evidence`, `ticker_evidence_objects`, `{analyst}_data` — Rule 2 conformance, gated on the callback restructures above (the writes need to ride on a yielded state_delta first).

- **AST walker / contract test** that statically enforces Rule 1 across `src/agents/` — `docs/todo-fixes.md` item 2.5.2. Decision point: strict vs pragmatic-with-escape-hatch policy. The strict-policy option only becomes viable after A2 lands.

- **Lift pipeline-internal DB writers** (`EvidenceWriter`, `StrategistDecisionWriter`, Executor trade log, Snapshotter) into Phase 4 hooks — `docs/todo-fixes.md` item 2.5.4. Blocked on 2.5.3 fixing the lifecycle-wrapper shape.

- **Drop defensive double writes** in Executor / MemoryWriter / Snapshotter — these three retain a paired `state[k] = v` direct write alongside their `state_delta` yield as belt-and-braces against in-tick stale reads. Dropping them is safe only after the AST walker (2.5.2) and the persistence subsystem (2.5.3) are both landed.

---

## Sequencing relative to A2 and todo-fixes 2.5.x

A1 ships first. A1 is fully self-contained — it makes no assumption about persistence, callback restructures, or contract enforcement, so it can land in a single focused PR against `main` without coordination. A2 follows once design questions around Strategist `output_key` placement and callback-to-shim restructures are settled; A2 builds on A1's foundation (every analyst that A2 touches has already moved its direct write to a `state_delta` yield via the analyst pool conversions A1.1 / A1.2 set the pattern for). The `todo-fixes.md` items 2.5.2 (AST walker), 2.5.3 (persistence subsystem), and 2.5.4 (lift mid-pipeline DB writers) form the longer-running follow-up. 2.5.3 unblocks the four high-severity §A cross-tick deviations and is the largest of the three; 2.5.4 depends on 2.5.3 settling the lifecycle-wrapper concept; 2.5.2 depends on the strict-vs-pragmatic policy decision recorded in todo-fixes itself. Once A1 + A2 + 2.5.2 + 2.5.3 + 2.5.4 are all in, the defensive double writes in Executor / MemoryWriter / Snapshotter can be removed and the contract becomes uniformly enforced across the codebase.

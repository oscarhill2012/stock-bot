# Contract conformance — A2 (structural) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the callback-shaped Rule 1 deviations in the StockBot pipeline by lifting the Strategist's `before_agent_callback` writes into a new `StrategistContextShim` `BaseAgent`, deleting the now-redundant `after_model_callback` composite, converting the Fund/News analyst callback writes onto the `state_delta` rail, restructuring the AnalystPool so Fund/News run sequentially, prefixing invocation-scoped keys with `temp:`, and documenting the in-tick `after_agent_callback` carve-out for `_strategist_validation_callback` in the contract spec.

**Architecture:** The Strategist becomes `SequentialAgent([StrategistContextShim, LlmAgent(...)])`: the shim yields one `Event(state_delta={…})` carrying `temp:held_positions_view`, `temp:ticker_evidence`, and `temp:ticker_evidence_objects` — replacing the two `before_agent_callback`s that currently direct-mutate state. The Strategist `after_agent_callback` (`_strategist_validation_callback`) is intentionally left in place under a new "in-tick callback carve-out" clause added to `contract-invariants.md`: the callback's only consumer (RiskGate) lives in the same tick. Fund/News analysts move from `ParallelAgent` to a sequential branch — the previous parallel arrangement was viable only while their writes were captured by ADK's `output_schema` round-trip; the new shape lets each analyst yield its full `state_delta` (including a new `raw_text` field on the evidence schema). The four `{analyst}_data` keys gain a `temp:` prefix at every write/read site so ADK's invocation-boundary strip prevents tick-to-tick leak.

**Tech Stack:** Python 3.14, Google ADK 1.34 (`BaseAgent`, `SequentialAgent`, `ParallelAgent`, `LlmAgent`, `Event`, `EventActions`), Pydantic 2 (evidence schemas), pytest (+ `pytest-asyncio`), ruff. Existing project conventions per `.claude/CLAUDE.md` (British English, comment-heavy code, function docstrings, blank-line whitespace).

---

## Open questions — surface BEFORE executing

The brief sent into this plan contains two items that, after reading the source, conflict with what the code actually does today. The plan below adopts the safest interpretation of each but flags them here so the user can confirm or correct before any task runs.

### OQ-1 — `default=str` in `TraceWriter` is **already present**

The brief (item 2) says: *"In `src/observability/trace_writer.py` … add `default=str` to the `json.dumps(...)` call so datetimes serialise without a per-field coercion shim. This is a one-line change."*

What I actually found:

- The module is `src/observability/trace.py` (not `trace_writer.py`).
- The flush method is `TraceWriter.finalise(out_path)` (not `.write(...)`).
- Line 115 already reads: `out_path.write_text(json.dumps(self._sections, indent=2, default=str))`.

The fix the brief calls for is already in place. The associated regression test (`datetime` payload round-trips without raising) still has standalone value as a guard against future regressions and is included in Task 4 below. **No source edit is needed in `trace.py` itself.**

If the user intended a different site — e.g. a `default=str` fix somewhere downstream (e.g. `decision_logger.py`) — please point at it before Task 4 runs.

### OQ-2 — Deleting `after_model_callback=_strategist_after_model_composite` loses the stance-clamp safety net

The brief (item 1) says: *"Delete `after_model_callback=_strategist_after_model_composite` (the sanitiser/clamping chain that currently coerces stale fields and runs `default=str`-like normalisation). Its only downstream consumer was TraceWriter relying on datetime-serialisable output; fix that at source — see item 2 below."*

What `_strategist_after_model_composite` actually is (verified at `src/agents/strategist/agent.py:534-564`):

1. **First** runs `_clamp_stance_bounds_after_model`, which deserialises the LLM response JSON, **clamps `preferred_weight` and `conviction` to `[0.0, 1.0]`**, and re-serialises. The docstring is explicit: *"Always-on clamping is mandatory: the `ge=0` constraint on `preferred_weight` must hold regardless of whether tracing is enabled."* The clamp exists because Gemini occasionally emits negative `preferred_weight` (intent: short positions, which the bot doesn't support); without the clamp, ADK's schema validator raises `ValidationError` and aborts the tick.
2. **Second** runs the optional trace callback (only attached when `STOCKBOT_TRACE=1`).

What the production pipeline actually wires (`src/orchestrator/pipeline.py:83-93`): only the trace callback when `STOCKBOT_TRACE=1` — `_strategist_after_model_composite` is **never wired by `build_pipeline()`**. The composite is only wired on the module-level singleton at `src/agents/strategist/agent.py:567-577`, which is used by some unit tests but not by live or backtest. So:

- The clamp is **already dormant** in the production lifecycle.
- Deleting the composite + the module-level singleton's `after_model_callback=` arg is mechanically safe today.
- But the brief's stated reason ("only downstream consumer was TraceWriter") is wrong — the composite's primary job is the clamp, not the trace.

**Resolution (user decision, 2026-05-20): delete the clamp outright.**

The Strategist prompt already forbids negative weights (no shorting).  The clamp is dormant defensive code that has never actually defended a production run — keeping ~330 lines of code "just in case" the LLM ignores its instructions is YAGNI.  If LLM drift recurs in practice, the right fix is a Pydantic field-level validator on `TickerStance` (which the contract permits) rather than a re-attached `after_model_callback` (which Rule 3 forbids from yielding state).

**Task 2 below deletes:**
- `_strategist_after_model_composite` (composite chain)
- `_clamp_stance_bounds_after_model` (clamp function)
- `_CLAMPED_STANCE_FIELDS` (clamp constant)
- `_composite_before_callback` (replaced by `StrategistContextShim` in Task 1)
- `tests/unit/agents/strategist/test_clamp_stance_bounds.py` (sole consumer of the deleted clamp symbols)

The cost is honest: if Gemini emits a negative `preferred_weight` post-deletion, the tick aborts at `ADK output_schema` validation rather than being silently clamped.  That is the desired behaviour — the bot would otherwise be quietly correcting an LLM that's ignoring its instructions.

### OQ-3 — Per-analyst sequential branch shape

The brief (item 5) says: *"The brainstorm settled that we move to **sequential analyst branches** for Fund/News specifically … propose the smallest structural change."*

The smallest structural change is replacing the four-child `ParallelAgent` with `SequentialAgent([ParallelAgent([Technical, Social]), Fundamental, News])` — Technical and Social remain parallel (no shared writes, A1 has already converted them to `state_delta`); Fundamental and News become sequential so each owns the `state_delta` rail unambiguously. The unique-`output_key` Rule 4 invariant is preserved (the keys remain distinct), and the per-tick wall-clock cost increases only by the serialised LLM latency of Fundamental + News (already cache-aware via `make_report_cache_callbacks`).

If the user instead wants a single fully-sequential `SequentialAgent([Technical, Social, Fundamental, News])`, that is a one-line change at the same site — flag it before Task 6 runs.

---

## What changed and why

A1 (sibling plan, executing separately) handles the mechanical Rule 1 conversions — three `BaseAgent` `_run_async_impl` direct writes (Technical, Social, RiskGate), `as_of`/`tick_phase` wiring in the live tick builder, the singular/plural verdict rename in the spec, and the new §A rows for `tick_phase`/`last_executed_tick_id`/`last_snapshot`/`watchlist`. A2 (this plan) handles the four remaining categories of contract deviation:

- **A2.1 / A2.2** — Strategist `before_agent_callback` writes (`held_positions_view`, `ticker_evidence`, `ticker_evidence_objects`) are direct mutations from inside callbacks. ADK callbacks can't yield Events (Rule 3), so the writes can never become contract-conformant in the callback. Solution: lift the work into a new `StrategistContextShim` `BaseAgent` that yields one `Event(state_delta=…)` and slots in front of the existing Strategist `LlmAgent` via a `SequentialAgent`.
- **A2.3** — Strategist `after_model_callback` composite (`_strategist_after_model_composite`) is now redundant. The module-level singleton wires it; production (`_build_strategist` in `pipeline.py`) does not. Removing the singleton's hook closes one of the audit's outstanding deviation rows (`src/agents/strategist/agent.py:388` family). See OQ-2 above.
- **A2.4** — The Strategist `after_agent_callback` (`_strategist_validation_callback`) at `src/agents/strategist/agent.py:388` performs the contract's mandatory cross-stance validation (four passes against `state["portfolio"]` and `state["tickers"]`) and then derives `target_weights` / `new_positions` / `close_reasons` / `trim_reasons`. The work *can't* move to `output_schema` (the schema can't see runtime portfolio state) and *can't* move to a downstream `BaseAgent` cleanly (the LLM response shape is opaque outside the callback). It writes back via direct mutation at line 388 — technically Rule 1 — but the only consumer (RiskGate) runs in the same tick. The plan documents this as an **in-tick callback carve-out** in `contract-invariants.md` rather than restructuring the callback.
- **A2.5** — Fundamental and News analysts use `before_agent_callback` (fetch) + `after_agent_callback` (evidence build) + `before_model_callback` / `after_model_callback` (cache + trace) hooks. Each callback direct-mutates state (`{analyst}_data`, `{analyst}_evidence`, `{analyst}_context`). A2 adds a `raw_text` field on the evidence schema (brainstorm decision) so the strategist sees raw provider text, and converts the after-callback writes onto a yielded `state_delta` via a thin `BaseAgent` wrapper that runs the existing LlmAgent and the existing post-processing as a sequential pair.
- **A2.6** — `held_positions_view`, `ticker_evidence`, `ticker_evidence_objects`, and the four `{analyst}_data` keys are textbook `temp:` candidates. Renaming at every write/read site makes ADK strip them at the invocation boundary, preventing accidental cross-tick reads.
- **A2.7** — AnalystPool moves from `ParallelAgent([Technical, Fundamental, News, Social])` to `SequentialAgent([ParallelAgent([Technical, Social]), Fundamental, News])` so each LLM analyst owns the `state_delta` rail unambiguously (no Rule 4 race with itself once `state_delta` yields are interleaved with `output_schema` writes).

This plan does **not** touch persistence (the four high-severity cross-tick deviations `positions` / `memory_buffer` / `day_digest` / `thesis` stay deferred to todo-fixes 2.5.3), does **not** add the AST-walker contract test (todo-fixes 2.5.2), does **not** relocate the four pipeline-internal DB writers (todo-fixes 2.5.4), and does **not** touch any of A1's surface.

---

## File map

The plan touches the following files. Each is touched by exactly one task except for `pipeline.py` (Tasks 1 + 6 + 7) and the smoke test (Task 1 + Task 9) — both of which are unavoidable because the wiring changes are layered.

**Created — source:**

- `src/agents/strategist/context_shim.py` — new `StrategistContextShim` BaseAgent. Task 1.
- `src/agents/analysts/_base_yield.py` — small shared BaseAgent wrapper that yields `state_delta` after an inner `LlmAgent` completes (used by both Fund and News). Task 5.

**Modified — source:**

- `src/agents/strategist/agent.py` — Task 2 (delete `_composite_before_callback` wiring + `after_model_callback` wiring on module-level singleton; keep `_strategist_validation_callback` intact).
- `src/orchestrator/pipeline.py` — Task 1 (wire `StrategistContextShim` into `_build_strategist`), Task 6 (rework `_build_analyst_pool` into sequential-Fund/News), Task 7 (`temp:` prefix where pipeline factories pass state keys).
- `src/agents/strategist/held_view.py` — Task 1 (no code change; verify import surface still works after `context_shim.py` absorbs the caller).
- `src/agents/strategist/evidence_view.py` — Task 1 (no code change; verify import surface).
- `src/agents/analysts/fundamental/agent.py` — Task 5 (lift after-callback evidence write onto `state_delta` via the `_base_yield.py` wrapper; remove direct write from `_common.make_evidence_callback`'s after-callback when called from Fund).
- `src/agents/analysts/news/agent.py` — Task 5 (same as Fund).
- `src/agents/analysts/_common.py` — Task 5 (`make_evidence_callback` gains a `yield_via_event: bool = False` flag — when True, the function does not direct-mutate state; the caller's `BaseAgent` wrapper picks the payload off a return value instead).
- `src/contract/evidence.py` — Task 5 (`AnalystEvidence` gains an optional `raw_text: str | None = None` field).
- `src/agents/analysts/fundamental/fetch.py` — Task 7 (`temp:fundamental_data` instead of `fundamental_data`).
- `src/agents/analysts/news/fetch.py` — Task 7 (`temp:news_data`).
- `src/agents/analysts/technical/fetch.py` — Task 7 (`temp:technical_data`).
- `src/agents/analysts/social/fetch.py` — Task 7 (`temp:social_data`).
- All sites that read those keys (the analyst `_run_async_impl` methods, `_common.make_evidence_callback`, the cache callbacks in `cache_callbacks.py`) — Task 7.

**Modified — spec / audit:**

- `docs/contract-invariants.md` — Task 3 (in-tick callback carve-out clause in §C-Rule 1).
- `docs/Phase8-contract-audit-fixes/contract-audit.md` — Task 3 (mark `src/agents/strategist/agent.py:388` row as "conformant under in-tick carve-out").

**Created — tests:**

- `tests/unit/agents/strategist/test_context_shim.py` — Task 1.
- `tests/unit/agents/strategist/test_after_model_unwired.py` — Task 2.
- `tests/unit/contract/test_invariants_doc_carveout.py` — Task 3 (doc-presence guard).
- `tests/unit/observability/test_trace_writer_datetime.py` — Task 4.
- `tests/unit/contract/test_evidence_raw_text.py` — Task 5 (schema field exists).
- `tests/unit/agents/analysts/test_fundamental_yield.py` — Task 5.
- `tests/unit/agents/analysts/test_news_yield.py` — Task 5.
- `tests/unit/orchestrator/test_pipeline_sequential_branches.py` — Task 6.
- `tests/unit/orchestrator/test_temp_prefix_keys.py` — Task 7.

**Modified — tests:**

- `tests/integration/backtest/test_end_to_end_smoke.py` — Task 1 (`_patched_build_strategist` wires `StrategistContextShim` ahead of the LlmAgent so the shim runs in the smoke harness) and Task 9 (final-gate run).

---

## Execution order rationale

The brief asks the plan be ordered "ContextShim + delete after_model_callback first because they share a test surface; carve-out documentation second; Fund/News + sequential branches third; `temp:` prefix last". The plan below follows that order with one nuance: Task 4 (`TraceWriter` datetime test) lands between the carve-out doc and the Fund/News work because it is a tiny independent guard and naturally pairs with the spec edits as a "tighten everything around the Strategist" block. The smoke-test gate is the final task.

---

## Task 1: Lift Strategist before-callback writes into `StrategistContextShim`

**A2.1 + A2.2** — replace `_composite_before_callback` with a `BaseAgent` that yields a single `Event(state_delta=…)`.

**Files:**
- Create: `src/agents/strategist/context_shim.py`
- Modify: `src/orchestrator/pipeline.py:54-93` (rework `_build_strategist` to return `SequentialAgent([shim, LlmAgent])`)
- Modify: `tests/integration/backtest/test_end_to_end_smoke.py:317-344` (`_patched_build_strategist` must also wrap shim + mock LlmAgent in a `SequentialAgent`)
- Create: `tests/unit/agents/strategist/test_context_shim.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/strategist/test_context_shim.py` with the following exact contents:

```python
"""Contract Rule 1 test for ``StrategistContextShim``.

The shim replaces ``_composite_before_callback`` (held-view +
evidence-view) on the Strategist LlmAgent.  The contract requires every
state write to ride on a yielded ``Event(actions=EventActions(state_delta=...))``
— callbacks cannot yield events (Rule 3), so the work has to live on a
``BaseAgent``.

This test wires the shim by itself (without the downstream LlmAgent) and
asserts that one event is emitted carrying the three expected keys with the
``temp:`` prefix mandated by Task 7's later edit.  It does NOT assert on
the rendered string content of the held-positions view — separate tests in
``test_held_view.py`` / ``test_evidence_view.py`` already cover formatting.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agents.strategist.context_shim import StrategistContextShim


@pytest.fixture
def populated_state() -> dict:
    """Build a session-state dict with the keys the shim needs to read.

    The shim reads ``positions``, ``portfolio``, ``tickers``, ``tick_id``,
    ``as_of``, and the four per-analyst ``*_evidence`` lists.  An empty
    ``positions`` dict is fine — the held-view renderer handles the flat-
    portfolio case.  The evidence lists are empty too — the evidence-view
    branch handles that path.
    """
    return {
        "tickers":            ["AAPL"],
        "tick_id":            "test-tick-1",
        "as_of":              datetime(2026, 5, 20, 13, 30, tzinfo=UTC),
        "positions":          {},
        "portfolio":          {"cash": 100_000.0, "positions": {}},
        "technical_evidence": [],
        "fundamental_evidence": [],
        "news_evidence":      [],
        "smart_money_evidence": [],
    }


def test_shim_yields_one_event_with_temp_prefixed_keys(populated_state: dict) -> None:
    """Run the shim and assert exactly one event carrying the three context keys."""
    shim = StrategistContextShim()

    # Fake InvocationContext — just needs invocation_id + a session whose
    # .state attribute is our populated dict.  ADK's BaseAgent contract only
    # touches ctx.invocation_id and ctx.session.state during _run_async_impl.
    fake_session = MagicMock()
    fake_session.state = populated_state
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-1"
    fake_ctx.session = fake_ctx.session_service = fake_session

    async def _drain() -> list:
        events: list = []
        async for ev in shim._run_async_impl(fake_ctx):
            events.append(ev)
        return events

    events = asyncio.run(_drain())

    assert len(events) == 1, (
        f"StrategistContextShim must yield exactly one event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    expected_keys = {
        "temp:held_positions_view",
        "temp:ticker_evidence",
        "temp:ticker_evidence_objects",
    }
    assert set(delta.keys()) == expected_keys, (
        f"state_delta keys mismatch: {set(delta.keys())} vs {expected_keys}"
    )
    # held-view always produces *some* string (empty portfolio -> sentinel msg).
    assert isinstance(delta["temp:held_positions_view"], str)
    # evidence-view list is empty (no per-ticker evidence in the fixture) but
    # still serialised as a list/string pair.
    assert isinstance(delta["temp:ticker_evidence"], str)
    assert isinstance(delta["temp:ticker_evidence_objects"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.context_shim'`.

- [ ] **Step 3: Implement `StrategistContextShim`**

Create `src/agents/strategist/context_shim.py` with the following exact contents:

```python
"""StrategistContextShim — ADK BaseAgent that hydrates strategist context keys.

Replaces the two ``before_agent_callback`` direct-mutation sites on the
Strategist ``LlmAgent`` (``_held_view_before_callback`` and
``_evidence_view_before_callback`` in ``agents/strategist/agent.py``).

ADK callbacks cannot yield ``Event``s (contract Rule 3) but the contract
requires every state write to ride on a yielded
``Event(actions=EventActions(state_delta=...))`` (Rule 1).  The shim
resolves the conflict: the same view-rendering work runs inside a
``BaseAgent._run_async_impl``, which can yield.  The shim slots in front
of the Strategist LlmAgent inside a SequentialAgent so the LlmAgent's
``inject_session_state`` resolves ``{held_positions_view}`` and
``{ticker_evidence}`` against the freshly-written state.

The three keys carry the ``temp:`` prefix mandated by §C-Rule 2 — they are
invocation-scoped working state, never read across ticks.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.strategist.held_view import render_held_positions_view
from broker.portfolio import Portfolio
from contract.digest import build_ticker_evidence
from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS
from contract.evidence import AnalystEvidence
from contract.strategist_prompt import render_all_ticker_blocks
from contract.ticker_evidence import TickerEvidence
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe


def _coerce_portfolio(value) -> Portfolio:
    """Return a Portfolio whether ``value`` is an instance, dict, or None.

    Mirrors the helper in ``agents.strategist.agent`` so the shim is
    self-contained and does not pull in callback-flavoured code.

    Args:
        value: A ``Portfolio``, a ``Portfolio.model_dump(mode="json")``
            dict, or ``None``.

    Returns:
        A ``Portfolio`` instance.  ``None`` produces an empty portfolio.
    """
    if isinstance(value, Portfolio):
        return value
    if value is None:
        return Portfolio(cash=0.0)
    return Portfolio.model_validate(value)


def _index_evidence(state, key: str) -> dict[str, AnalystEvidence]:
    """Index a per-analyst evidence list by ticker.

    Items may be raw dicts (post-JSON-serialisation) or validated
    ``AnalystEvidence`` instances — both are tolerated.

    Args:
        state: ADK session-state proxy / dict.
        key: The state key, e.g. ``"technical_evidence"``.

    Returns:
        Mapping ticker -> ``AnalystEvidence``.
    """
    items = state.get(key, []) or []
    out: dict[str, AnalystEvidence] = {}
    for item in items:
        ev = AnalystEvidence.model_validate(item) if isinstance(item, dict) else item
        out[ev.ticker] = ev
    return out


class StrategistContextShim(BaseAgent):
    """Hydrate ``temp:held_positions_view`` + ``temp:ticker_evidence*`` on state.

    Yields a single ``Event(state_delta=…)`` carrying the three keys the
    Strategist's instruction template will resolve.  Slots immediately
    before the Strategist ``LlmAgent`` inside its enclosing
    ``SequentialAgent``.

    Why this is a ``BaseAgent`` not a callback: ADK callbacks cannot
    yield ``Event``s (Rule 3); state writes must ride on
    ``state_delta`` (Rule 1).  A ``BaseAgent`` is the smallest legal
    construct that satisfies both rules.
    """

    name: str = "StrategistContextShim"

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Build held-view + ticker-evidence and emit them on a single Event.

        Reads ``positions``, ``portfolio``, ``tickers``, ``tick_id``,
        ``as_of`` / ``recorded_at``, and the four per-analyst
        ``*_evidence`` lists.  Writes ``temp:held_positions_view``,
        ``temp:ticker_evidence``, and ``temp:ticker_evidence_objects``.

        Args:
            ctx: ADK invocation context; ``ctx.session.state`` is the
                pipeline session-state dict / proxy.

        Yields:
            Exactly one ``Event`` whose ``actions.state_delta`` carries
            the three context keys.
        """
        state = ctx.session.state

        # ── Held-positions view ───────────────────────────────────────────
        positions = state.get("positions", {}) or {}
        portfolio = _coerce_portfolio(state.get("portfolio"))
        held_view = render_held_positions_view(positions, portfolio)

        # ── Ticker-evidence view ──────────────────────────────────────────
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str = state.get("tick_id", "unknown")

        # Resolve the ``recorded_at`` timestamp for evidence aggregation.
        # Priority: state["as_of"] (backtest replay clock) > state["recorded_at"]
        # > wall-clock fallback (live, when STOCKBOT_STRICT_AS_OF=0).
        as_of_raw = state.get("as_of")
        if isinstance(as_of_raw, datetime):
            recorded_at = as_of_raw
        else:
            recorded_at_raw = state.get("recorded_at")
            if isinstance(recorded_at_raw, str):
                recorded_at = datetime.fromisoformat(recorded_at_raw)
            elif isinstance(recorded_at_raw, datetime):
                recorded_at = recorded_at_raw
            else:
                recorded_at = resolve_as_of(
                    None, allow_wallclock=True, site="strategist/context_shim",
                )

        # Index every analyst's evidence list by ticker.
        tech = _index_evidence(state, "technical_evidence")
        fund = _index_evidence(state, "fundamental_evidence")
        news = _index_evidence(state, "news_evidence")
        sm   = _index_evidence(state, "smart_money_evidence")

        # Build one TickerEvidence per watchlist ticker.
        ticker_evidence: list[TickerEvidence] = []
        for t in tickers:
            per_analyst: dict[str, AnalystEvidence] = {}
            if t in tech: per_analyst["technical"]   = tech[t]
            if t in fund: per_analyst["fundamental"] = fund[t]
            if t in news: per_analyst["news"]        = news[t]
            if t in sm:   per_analyst["smart_money"] = sm[t]

            te = build_ticker_evidence(
                per_analyst = per_analyst,
                ticker      = t,
                tick_id     = tick_id,
                recorded_at = recorded_at,
                weights     = DEFAULT_ANALYST_WEIGHTS,
            )
            ticker_evidence.append(te)

        ticker_evidence_objects = [te.model_dump(mode="json") for te in ticker_evidence]
        ticker_evidence_rendered = render_all_ticker_blocks(ticker_evidence)

        # Surface trace — no-op unless state["_trace"] is set.
        _trace_maybe(state, "04_digest", ticker_evidence_objects)

        # ── Yield exactly one Event carrying all three keys ───────────────
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "temp:held_positions_view":     held_view,
                "temp:ticker_evidence":         ticker_evidence_rendered,
                "temp:ticker_evidence_objects": ticker_evidence_objects,
            }),
        )
```

- [ ] **Step 4: Rewire `_build_strategist` to wrap the LlmAgent in a SequentialAgent**

In `src/orchestrator/pipeline.py`, replace the entire `_build_strategist` function body. Change the current return from `LlmAgent(...)` to `SequentialAgent(name="StrategistBranch", sub_agents=[StrategistContextShim(), LlmAgent(...)])`. The LlmAgent must keep `before_agent_callback=None` (the shim replaces `_composite_before_callback`) and `after_agent_callback=_strategist_validation_callback` (preserved — see Task 3 carve-out).

The new `_build_strategist` reads as follows in full:

```python
def _build_strategist():
    """Build the Strategist branch — SequentialAgent[ContextShim, LlmAgent].

    The ContextShim hydrates ``temp:held_positions_view``,
    ``temp:ticker_evidence``, and ``temp:ticker_evidence_objects`` via a
    yielded ``Event(state_delta=…)`` (contract Rule 1).  The downstream
    LlmAgent then resolves those keys via ADK's instruction-variable
    substitution and emits its ``StrategistDecision``.  The validation +
    derivation work stays as an ``after_agent_callback`` on the LlmAgent —
    see the in-tick callback carve-out documented in
    ``docs/contract-invariants.md`` §C-Rule 1.
    """
    import os

    from google.adk.agents import LlmAgent, SequentialAgent

    from agents.strategist.agent import _strategist_validation_callback
    from agents.strategist.context_shim import StrategistContextShim
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistDecision
    from observability.trace import make_llm_trace_callbacks

    model_name = "gemini-2.5-pro"
    before_model = None
    after_model = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        before_model, after_model = make_llm_trace_callbacks(
            "05_strategist_llm", model=model_name,
        )

    llm = LlmAgent(
        name="Strategist",
        model=model_name,
        instruction=STRATEGIST_INSTRUCTION,
        output_schema=StrategistDecision,
        output_key="strategist_decision",
        # before_agent_callback intentionally None — StrategistContextShim
        # now does the work that _composite_before_callback used to do.
        after_agent_callback=_strategist_validation_callback,
        before_model_callback=before_model,
        after_model_callback=after_model,
    )

    return SequentialAgent(
        name="StrategistBranch",
        sub_agents=[StrategistContextShim(), llm],
    )
```

- [ ] **Step 5: Update the smoke-test patched builder to match**

In `tests/integration/backtest/test_end_to_end_smoke.py:317-344`, replace `_patched_build_strategist` so it returns a `SequentialAgent([StrategistContextShim(), LlmAgent(...)])` mirroring the new production shape. The mock `LlmAgent` retains the `_mock_before` `before_model_callback` and `_strategist_validation_callback` as `after_agent_callback`. No `before_agent_callback` on the inner LlmAgent.

Exact replacement:

```python
    def _patched_build_strategist():
        """Build strategist as SequentialAgent[ContextShim, mock LlmAgent]."""
        from google.adk.agents import LlmAgent, SequentialAgent

        from agents.strategist.agent import _strategist_validation_callback
        from agents.strategist.context_shim import StrategistContextShim
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistDecision

        def _mock_before(callback_context, llm_request):
            """Return a synthetic StrategistDecision without calling Gemini."""
            current_tickers = (
                callback_context.state.get("tickers") or tickers
            )
            return _make_strategist_llm_response(current_tickers)

        llm = LlmAgent(
            name="Strategist",
            model="gemini-2.5-pro",
            instruction=STRATEGIST_INSTRUCTION,
            output_schema=StrategistDecision,
            output_key="strategist_decision",
            after_agent_callback=_strategist_validation_callback,
            before_model_callback=_mock_before,
        )

        return SequentialAgent(
            name="StrategistBranch",
            sub_agents=[StrategistContextShim(), llm],
        )
```

- [ ] **Step 6: Run the shim unit test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim.py -v`
Expected: PASS.

- [ ] **Step 7: Run the fast suite to confirm nothing else broke**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS. If the prompt template still references `{held_positions_view}` (no `temp:` prefix), Task 7 will rename it — for now the keys land with the `temp:` prefix and the prompt template lookup will be re-aligned in Task 7. If a test fails here because of the prefix mismatch, defer it to Task 7's gate.

- [ ] **Step 8: Commit**

```bash
git add src/agents/strategist/context_shim.py src/orchestrator/pipeline.py tests/integration/backtest/test_end_to_end_smoke.py tests/unit/agents/strategist/test_context_shim.py
git commit -m "$(cat <<'EOF'
feat(strategist): lift before-callback writes into StrategistContextShim

Replaces _composite_before_callback's direct-mutation writes with a
BaseAgent that yields a single Event(state_delta=…) carrying
temp:held_positions_view, temp:ticker_evidence, and
temp:ticker_evidence_objects.  Closes the §C-Rule 1 deviation at
src/agents/strategist/agent.py:70 + 176-177 documented in
contract-audit.md.  Strategist becomes SequentialAgent[shim, LlmAgent].

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Delete the `after_model_callback` composite **and** the stance-bounds clamp

**A2.3** — adopt the **delete-the-clamp** resolution of OQ-2.  The composite (`_strategist_after_model_composite`), the dependent before-callback (`_composite_before_callback`), the clamp function (`_clamp_stance_bounds_after_model`), the `_CLAMPED_STANCE_FIELDS` constant, and the dedicated clamp test file all delete.  Rationale (user decision, 2026-05-20): the clamp's only job is bounding `preferred_weight` ∈ [0, 1] against Gemini overshoot.  The Strategist prompt already forbids negative weights (no shorting).  If the LLM ignores the instruction in practice, re-introducing a clamp is a small, targeted fix at that point — keeping ~330 lines of dormant defensive code "just in case" is YAGNI.

**Files:**
- Modify: `src/agents/strategist/agent.py` — delete the clamp function (`:421-517`), the `_CLAMPED_STANCE_FIELDS` constant (`:418`), the composite (`:534-564`), `_composite_before_callback` (`:392-410`), the comment at `:31` that references the clamp, and the now-redundant callback wiring in the singleton constructor (`:567-577`)
- Delete: `tests/unit/agents/strategist/test_clamp_stance_bounds.py` (entire file — only consumer of the clamp symbols)
- Create: `tests/unit/agents/strategist/test_after_model_unwired.py` (small guard test)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/strategist/test_after_model_unwired.py` with the following exact contents:

```python
"""Guard test — the production Strategist must not wire any
``after_model_callback`` except the optional trace hook gated on
``STOCKBOT_TRACE=1``.

Why: the legacy ``_strategist_after_model_composite`` chained a stance-
bounds clamp (against Gemini negative-weight drift) with a trace
callback.  Production wiring (``orchestrator/pipeline.py:_build_strategist``)
was already trace-only, so the clamp never actually ran in production.
The Strategist prompt forbids negative weights; the clamp was YAGNI
defensive code.  This test pins the contract — if anyone re-attaches the
composite, this fails fast.

Also asserts the clamp symbols are *gone* (not merely unwired) so a
future engineer cannot quietly re-import them.  If LLM drift recurs, the
fix is a fresh ``output_schema`` validator on ``TickerStance``, not a
re-attached after-model callback (callbacks are Rule 3-forbidden from
yielding state).
"""
from __future__ import annotations

import os
from unittest.mock import patch


def test_pipeline_strategist_branch_has_no_after_model_callback_by_default() -> None:
    """The production strategist LlmAgent must have ``after_model_callback=None``
    unless ``STOCKBOT_TRACE=1``.

    Inspects the SequentialAgent returned by ``_build_strategist`` and
    asserts the inner LlmAgent has no after-model callback in the default
    (non-trace) environment.
    """
    # Ensure trace is OFF for this assertion.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("STOCKBOT_TRACE", None)
        from orchestrator.pipeline import _build_strategist

        branch = _build_strategist()
        # branch is SequentialAgent[ContextShim, LlmAgent]; LlmAgent is the
        # second child.
        llm = branch.sub_agents[1]
        assert llm.after_model_callback is None, (
            "Strategist LlmAgent has after_model_callback wired in default env "
            "(STOCKBOT_TRACE not set) — that should be None."
        )


def test_module_singleton_no_longer_wires_after_model_composite() -> None:
    """The strategist module singleton must not wire
    ``_strategist_after_model_composite`` (it no longer exists), and the
    clamp symbols it chained must also be gone.
    """
    from agents.strategist import agent as sa

    # All four symbols delete as part of A2.3.
    assert not hasattr(sa, "_strategist_after_model_composite"), (
        "_strategist_after_model_composite should be removed in A2.3."
    )
    assert not hasattr(sa, "_clamp_stance_bounds_after_model"), (
        "_clamp_stance_bounds_after_model should be removed in A2.3 "
        "(prompt forbids negative weights; clamp was unwired YAGNI)."
    )
    assert not hasattr(sa, "_CLAMPED_STANCE_FIELDS"), (
        "_CLAMPED_STANCE_FIELDS should be removed in A2.3 — it was only "
        "consumed by the deleted clamp."
    )
    # The module-level singleton must not pass after_model_callback.
    assert sa.strategist_agent.after_model_callback is None, (
        "strategist_agent singleton still has after_model_callback wired; "
        "A2.3 unwires it."
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_after_model_unwired.py -v`
Expected: FAIL — `_strategist_after_model_composite`, `_clamp_stance_bounds_after_model`, and `_CLAMPED_STANCE_FIELDS` all still exist.

- [ ] **Step 3: Delete the composite, the clamp, and the dedicated clamp test file**

In `src/agents/strategist/agent.py`:

1. Delete the entire `_strategist_after_model_composite` function (currently `:534-564`).
2. Delete the lines that build the optional `_strategist_before_model` / `_strategist_after_trace` (`:526-531`) — they were only consumed by the deleted composite.
3. Delete `_composite_before_callback` (currently `:392-410`) — it was only wired by the singleton and is replaced by `StrategistContextShim` in Task 1.
4. Delete the `_clamp_stance_bounds_after_model` function (currently `:421-517`) entirely, including its docstring.
5. Delete the `_CLAMPED_STANCE_FIELDS` module-level constant (currently `:418`) and the comment block above it (`:413-417`) that introduces the "Sanitising after-model callback" section.
6. Delete the comment at `:31` that forward-references `_clamp_stance_bounds_after_model`.
7. In the `LlmAgent(...)` constructor for the singleton at `:567-577`, remove the `before_agent_callback=_composite_before_callback`, `before_model_callback=_strategist_before_model`, and `after_model_callback=_strategist_after_model_composite` arguments. Keep `after_agent_callback=_strategist_validation_callback` (carve-out — see Task 3).

The replacement singleton constructor reads:

```python
strategist_agent = LlmAgent(
    name="Strategist",
    model=_STRATEGIST_MODEL,
    instruction=STRATEGIST_INSTRUCTION,
    output_schema=StrategistDecision,
    output_key="strategist_decision",
    after_agent_callback=_strategist_validation_callback,
)
```

Then delete the dedicated clamp test file:

```bash
git rm tests/unit/agents/strategist/test_clamp_stance_bounds.py
```

This file's only purpose was to exercise `_clamp_stance_bounds_after_model` — once the clamp is gone, every test in it is testing dead code.

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_after_model_unwired.py -v`
Expected: PASS.

- [ ] **Step 5: Run the fast suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS.  If any other test imported `_strategist_after_model_composite`, `_clamp_stance_bounds_after_model`, or `_CLAMPED_STANCE_FIELDS` directly, that import now raises `ImportError` — either delete the test (if it was clamp-specific) or update the import (if the test only used the symbol as a sentinel).  Confirm no surprises before moving on.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/agent.py tests/unit/agents/strategist/test_after_model_unwired.py
# (the `git rm` from Step 3 has already staged the clamp test file deletion)
git commit -m "$(cat <<'EOF'
refactor(strategist): delete unwired Gemini-clamp after_model_callback

The composite (_strategist_after_model_composite) chained a stance-
bounds clamp against Gemini negative-weight drift with a trace callback.
Production wiring (pipeline.py:_build_strategist) only wired the
composite under STOCKBOT_TRACE=1, so the clamp never actually defended
production runs.  The Strategist prompt forbids negative weights;
keeping ~330 lines of dormant defensive code "just in case" is YAGNI.

Deletes:
- _strategist_after_model_composite
- _clamp_stance_bounds_after_model + _CLAMPED_STANCE_FIELDS
- _composite_before_callback (replaced by StrategistContextShim in A2)
- tests/unit/agents/strategist/test_clamp_stance_bounds.py (sole consumer)

If LLM drift recurs in practice, the fix is an output_schema validator
on TickerStance (Pydantic field-level), not a re-attached callback —
callbacks are forbidden from yielding state per Rule 3.

See contract-conformance-A2-structural.md OQ-2.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Document the in-tick callback carve-out in `contract-invariants.md`

**A2.4** — add an explicit carve-out clause to §C-Rule 1 so the `_strategist_validation_callback` direct-write at `src/agents/strategist/agent.py:388` is documented as conformant.

**Files:**
- Modify: `docs/contract-invariants.md` (insert carve-out clause after the §C-Rule 1 ADK-grounding paragraph)
- Modify: `docs/Phase8-contract-audit-fixes/contract-audit.md` (annotate the `:388` row)
- Create: `tests/unit/contract/test_invariants_doc_carveout.py` (presence guard)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_invariants_doc_carveout.py` with the following exact contents:

```python
"""Doc-presence guard for the A2.4 in-tick callback carve-out.

The carve-out clause in ``contract-invariants.md`` §C-Rule 1 makes the
direct-mutation write in ``_strategist_validation_callback`` conformant.
This test asserts the clause is present so future spec edits cannot
silently drop it.
"""
from __future__ import annotations

from pathlib import Path

# Resolve project root from this file's location — go up four levels:
# tests/unit/contract/test_invariants_doc_carveout.py -> project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_INVARIANTS  = _PROJECT_ROOT / "docs" / "superpowers" / "specs" / "contract-invariants.md"
_AUDIT       = _PROJECT_ROOT / "docs" / "superpowers" / "specs" / "contract-audit.md"


def test_invariants_carveout_clause_present() -> None:
    """The in-tick callback carve-out must be documented in §C-Rule 1."""
    text = _INVARIANTS.read_text(encoding="utf-8")
    assert "In-tick callback carve-out" in text, (
        "contract-invariants.md §C-Rule 1 is missing the in-tick callback "
        "carve-out clause added by A2.4."
    )


def test_audit_marks_388_as_conformant_under_carveout() -> None:
    """contract-audit.md §C-Rule 1 row for :388 must reference the carve-out."""
    text = _AUDIT.read_text(encoding="utf-8")
    assert "in-tick carve-out" in text.lower(), (
        "contract-audit.md does not mark the strategist/agent.py:388 row "
        "as conformant under the in-tick carve-out."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_invariants_doc_carveout.py -v`
Expected: FAIL on both assertions — the carve-out clause is not yet present.

- [ ] **Step 3: Edit `contract-invariants.md`**

In `docs/contract-invariants.md`, locate the §C-Rule 1 section (currently lines 190-204, beginning "### Rule 1 — State mutation must ride on Events"). Append the following paragraph block immediately after the **Implication** line (currently `Strategist's after_agent_callback writing the thesis book must emit a state_delta, not poke the dict.`) and before the §C-Rule 2 header:

```markdown
**In-tick callback carve-out (added 2026-05-20).**  ADK
``after_agent_callback``s cannot yield Events (Rule 3) but are the only
place certain LLM-output validation + derivation can run (they need
runtime access to ``state["portfolio"]`` and ``state["tickers"]``, which
``output_schema`` does not see).  Where such a callback writes to a
state key whose only consumer is **another agent in the same tick**,
that direct write is conformant.  The carve-out does NOT apply if the
key escapes the tick — cross-tick keys must still go through
``state_delta``.

The canonical instance today is the Strategist's
``_strategist_validation_callback`` (see
``src/agents/strategist/agent.py:388``), which rewrites
``state["strategist_decision"]`` with the derived legacy fields
(``target_weights``, ``new_positions``, ``close_reasons``,
``trim_reasons``).  Its only consumer is the downstream RiskGate agent
in the same tick.
```

- [ ] **Step 4: Edit `contract-audit.md`**

In `docs/Phase8-contract-audit-fixes/contract-audit.md`, locate the §C-Rule 1 deviation table (around line 446). Find the row for `Strategist validation callback | src/agents/strategist/agent.py:388 | state["strategist_decision"] (overwriting the output_key write) | medium`. Replace the **Severity** column value `medium` with the literal text `conformant (in-tick carve-out)`, and change the row's first cell to read `Strategist validation callback (conformant — in-tick carve-out)`.

Additionally, in the "Refactor surface — minimum changes needed for conformance" section near the end of the file (the numbered list at ~line 727), remove the bullet that mentions converting the validation callback's `:388` write (currently a sub-bullet under item 4). Replace it with a single sentence: `The strategist validation callback at :388 is now documented as conformant under the in-tick carve-out — see contract-invariants.md §C-Rule 1.`

- [ ] **Step 5: Run the doc test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_invariants_doc_carveout.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/contract-invariants.md docs/Phase8-contract-audit-fixes/contract-audit.md tests/unit/contract/test_invariants_doc_carveout.py
git commit -m "$(cat <<'EOF'
docs(contract): add in-tick callback carve-out for after_agent_callback

ADK after_agent_callbacks cannot yield Events but are the only place
certain LLM-output validation + derivation can run (need runtime access
to state["portfolio"] / state["tickers"]).  Where the only consumer of
the rewritten key is another agent in the same tick, the direct write
is conformant.  Marks _strategist_validation_callback at :388 as
conformant under the carve-out in contract-audit.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add a `TraceWriter` datetime-round-trip regression test

**A2 brief item 2** — `default=str` is already in place on `TraceWriter.finalise` (see OQ-1). Add a guard test so it cannot regress.

**Files:**
- Create: `tests/unit/observability/test_trace_writer_datetime.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/observability/test_trace_writer_datetime.py` with the following exact contents:

```python
"""Guard test — TraceWriter.finalise must serialise datetime payloads.

The strategist after-model composite used to coerce datetimes out of
the LLM response before tracing.  A2.3 deletes that composite; the
trace writer is now relied on to handle datetime serialisation via
``json.dumps(default=str)``.

This test pins that contract by writing a section whose payload
contains a raw ``datetime`` and asserting ``finalise`` does not raise.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from observability.trace import TraceWriter


def test_trace_writer_serialises_datetime_payload(tmp_path: Path) -> None:
    """Writer must round-trip a datetime via default=str without raising."""
    tw = TraceWriter()
    tw.snapshot(
        "01_test_datetime",
        {"recorded_at": datetime(2026, 5, 20, 13, 30, tzinfo=UTC)},
    )

    out_path = tmp_path / "trace.json"
    # Must not raise on the datetime payload.
    tw.finalise(out_path)

    # And the resulting file must be valid JSON containing a string form
    # of the datetime (default=str coercion).
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert "01_test_datetime" in payload
    recorded = payload["01_test_datetime"]["data"]["recorded_at"]
    assert isinstance(recorded, str)
    assert "2026-05-20" in recorded
```

- [ ] **Step 2: Run test to verify it passes immediately (no source change needed — see OQ-1)**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/observability/test_trace_writer_datetime.py -v`
Expected: PASS — `default=str` is already present at `src/observability/trace.py:115`.

If this step FAILS, the brief's assumption is correct after all and `trace.py` does need a `default=str` addition. Add it inline before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/observability/test_trace_writer_datetime.py
git commit -m "$(cat <<'EOF'
test(observability): pin TraceWriter datetime round-trip via default=str

Regression guard against future drift on TraceWriter.finalise's
json.dumps(default=str) call.  Strategist after-model composite (now
deleted in A2.3) used to pre-coerce datetimes; the trace writer is now
solely responsible for tolerating them.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Convert Fund/News analyst writes onto `state_delta` and add `raw_text` to the evidence schema

**A2.5** — wrap each LlmAgent in a thin BaseAgent that yields the evidence list on a `state_delta` after the LlmAgent has emitted its verdicts. Add `raw_text` to `AnalystEvidence` so the strategist downstream sees the raw provider text.

**Files:**
- Modify: `src/contract/evidence.py` — add `raw_text` field to `AnalystEvidence`
- Create: `src/agents/analysts/_base_yield.py` — shared `BaseAgent` wrapper
- Modify: `src/agents/analysts/_common.py` — extend `make_evidence_callback` with `return_payload: bool` to allow the wrapper to pick up the payload via a state key
- Modify: `src/agents/analysts/fundamental/agent.py` — return wrapped agent
- Modify: `src/agents/analysts/news/agent.py` — return wrapped agent
- Create: `tests/unit/contract/test_evidence_raw_text.py`
- Create: `tests/unit/agents/analysts/test_fundamental_yield.py`
- Create: `tests/unit/agents/analysts/test_news_yield.py`

- [ ] **Step 1: Add `raw_text` field to `AnalystEvidence` (failing schema test first)**

Create `tests/unit/contract/test_evidence_raw_text.py`:

```python
"""Schema guard — AnalystEvidence must carry an optional ``raw_text`` field.

The A2.5 brainstorm decision: the Strategist's prompt should be able to
include the raw provider text for News and Fundamental tickers, in
addition to the structured features + verdict.  Adds an optional
``raw_text: str | None = None`` field to ``AnalystEvidence``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from contract.evidence import AnalystEvidence, AnalystVerdict


def test_analyst_evidence_accepts_raw_text() -> None:
    """``raw_text`` must round-trip through model_validate / model_dump."""
    ev = AnalystEvidence(
        analyst     = "news",
        ticker      = "AAPL",
        tick_id     = "t1",
        recorded_at = datetime(2026, 5, 20, tzinfo=UTC),
        features    = {},
        verdict     = AnalystVerdict(
            lean="neutral", magnitude=0.0, confidence=0.5, rationale="x",
        ),
        raw_text    = "Apple closes flat amid SVB contagion fears…",
    )
    dumped = ev.model_dump(mode="json")
    assert dumped["raw_text"].startswith("Apple closes flat")
    # Default None when omitted.
    ev2 = AnalystEvidence(
        analyst="news", ticker="MSFT", tick_id="t1",
        recorded_at=datetime(2026, 5, 20, tzinfo=UTC),
        features={},
        verdict=AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.5, rationale="x"),
    )
    assert ev2.raw_text is None
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_evidence_raw_text.py -v`
Expected: FAIL — `raw_text` is not yet a field on `AnalystEvidence`.

- [ ] **Step 2: Add the field**

In `src/contract/evidence.py`, locate the `AnalystEvidence` class (currently around line 139). Add `raw_text: str | None = Field(default=None, max_length=10_000)` as the final field. Update the class docstring to mention the field. Exact replacement of the class body:

```python
class AnalystEvidence(BaseModel):
    """One analyst's structured output for one ticker on one tick.

    `features` carries the deterministic feature extractor's output (numeric
    only — no strings). Keys are analyst-specific; see Phase 4 spec for the
    locked catalogue per analyst. `feature_warnings` records any
    extractor-emitted issues (missing data window, NaN replacement, etc.) so
    downstream consumers can tell "extractor returned 0.0 because the input
    was missing" apart from "extractor returned a real 0.0".

    `raw_text` is an optional pass-through of the raw provider text the LLM
    analyst saw (News headlines, Fundamental filing excerpts).  Empty / None
    for deterministic analysts (Technical, Social, SmartMoney) where there
    is no provider prose.  Capped at 10 000 characters to keep the strategist
    prompt bounded.
    """

    ticker: str
    analyst: AnalystName
    tick_id: str
    recorded_at: datetime
    features: dict[str, float]
    feature_warnings: list[str] = Field(default_factory=list)
    verdict: AnalystVerdict
    raw_text: str | None = Field(default=None, max_length=10_000)
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_evidence_raw_text.py -v`
Expected: PASS.

- [ ] **Step 3: Write the failing Fund-yield test**

Create `tests/unit/agents/analysts/test_fundamental_yield.py`:

```python
"""Contract Rule 1 test — Fundamental analyst yields evidence via state_delta.

A2.5 wraps the FundamentalAnalyst LlmAgent in a thin BaseAgent that
yields a single ``Event(state_delta={"fundamental_evidence": [...]})``
after the LlmAgent's after_agent_callback has built the evidence list.

This test wires the wrapper, fakes the inner LlmAgent's state writes,
and asserts the outer wrapper emits the evidence on a state_delta.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from agents.analysts._base_yield import YieldingAnalystWrapper


def test_wrapper_yields_evidence_on_state_delta() -> None:
    """The wrapper must yield one Event with the evidence list on state_delta."""
    # A toy inner agent: writes a fake evidence list to state and yields no events.
    class _InnerNoEvents:
        name = "FundamentalAnalyst"
        async def run_async(self, _ctx):
            # Simulate the LlmAgent's after-agent-callback writing evidence.
            _ctx.session.state["fundamental_evidence"] = [
                {"ticker": "AAPL", "analyst": "fundamental"},
            ]
            if False:  # pragma: no cover — keep this an async generator
                yield None

    wrapper = YieldingAnalystWrapper(
        name="FundamentalAnalystBranch",
        inner=_InnerNoEvents(),
        evidence_state_key="fundamental_evidence",
    )

    fake_session = MagicMock()
    fake_session.state = {"tickers": ["AAPL"]}
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-2"
    fake_ctx.session = fake_session

    async def _drain() -> list:
        out = []
        async for ev in wrapper._run_async_impl(fake_ctx):
            out.append(ev)
        return out

    events = asyncio.run(_drain())
    assert len(events) == 1
    delta = events[0].actions.state_delta
    assert "fundamental_evidence" in delta
    assert delta["fundamental_evidence"][0]["ticker"] == "AAPL"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_fundamental_yield.py -v`
Expected: FAIL — `agents.analysts._base_yield` does not exist.

- [ ] **Step 5: Create `_base_yield.py`**

Create `src/agents/analysts/_base_yield.py` with the following exact contents:

```python
"""YieldingAnalystWrapper — BaseAgent that proxies an inner agent and emits
the inner's evidence write as a yielded ``state_delta``.

Used to convert the existing Fundamental and News LlmAgent's after-callback
direct-mutation evidence write into a Rule-1-conformant
``Event(actions=EventActions(state_delta=…))`` yield.

The wrapper:
1. Delegates to the inner agent (an ``LlmAgent`` plus its callbacks).  All
   intermediate events from the inner agent are forwarded unchanged.
2. After the inner agent returns, reads the evidence list from
   ``ctx.session.state[evidence_state_key]`` and yields one new event whose
   ``state_delta`` carries that list under the same key.

The result: even though the inner LlmAgent's after_agent_callback wrote
directly to state (it has to — ADK callbacks cannot yield events), the
outer wrapper republishes the write as a proper ``state_delta`` so ADK's
``SessionService.append_event`` persists it.  The inner direct write
becomes redundant with the outer yield — kept defensively for one cycle
so that consumers in the same invocation continue to see the value
without waiting for the event flush.

A future cleanup can drop the inner direct mutation once the persistence
layer is wired and all session backends honour ``state_delta`` writes
identically.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions


class YieldingAnalystWrapper(BaseAgent):
    """Proxy an inner agent and republish its evidence write as a ``state_delta``.

    Attributes:
        inner: The wrapped agent (typically an ``LlmAgent``).  Run via its
            ``run_async`` async-generator interface; all events it yields are
            passed through to the outer pipeline.
        evidence_state_key: The state key the inner agent's after-callback
            writes to (e.g. ``"fundamental_evidence"``).  The wrapper reads
            this after the inner agent has returned and republishes the
            value on a ``state_delta`` event.
    """

    inner: BaseAgent | object
    evidence_state_key: str

    def __init__(
        self,
        *,
        name: str,
        inner,
        evidence_state_key: str,
    ) -> None:
        """Initialise the wrapper.

        Args:
            name: ADK agent name (e.g. ``"FundamentalAnalystBranch"``).
            inner: The inner agent instance (an ADK ``LlmAgent`` or any
                object that exposes an ``async def run_async(ctx)``
                yielding ``Event`` instances).
            evidence_state_key: State key the inner writes its evidence
                list into.
        """
        super().__init__(name=name)
        # Pydantic-base BaseAgent rejects unknown attributes via __setattr__;
        # use object.__setattr__ to attach our private fields.
        object.__setattr__(self, "inner", inner)
        object.__setattr__(self, "evidence_state_key", evidence_state_key)

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Delegate to ``inner``; after it completes, republish the evidence write.

        Args:
            ctx: ADK invocation context.

        Yields:
            Every event yielded by the inner agent, then one additional
            event carrying ``state[self.evidence_state_key]`` on its
            ``state_delta``.
        """
        # 1. Pass through every event the inner agent yields.
        async for inner_event in self.inner.run_async(ctx):
            yield inner_event

        # 2. The inner agent's after_agent_callback has by now written the
        # evidence list to ``state[self.evidence_state_key]``.  Republish
        # it as a yielded state_delta so the write becomes durable.
        evidence_payload = ctx.session.state.get(self.evidence_state_key)
        if evidence_payload is not None:
            yield Event(
                author        = self.name,
                invocation_id = ctx.invocation_id,
                actions       = EventActions(state_delta={
                    self.evidence_state_key: evidence_payload,
                }),
            )
```

- [ ] **Step 6: Run the wrapper test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_fundamental_yield.py -v`
Expected: PASS.

- [ ] **Step 7: Write the equivalent News test**

Create `tests/unit/agents/analysts/test_news_yield.py` mirroring the Fundamental yield test exactly, substituting `news` for `fundamental` and `NewsAnalyst` for `FundamentalAnalyst`. The wrapper API is the same; this test guards that News uses the same pattern.

Exact contents:

```python
"""Contract Rule 1 test — News analyst yields evidence via state_delta."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from agents.analysts._base_yield import YieldingAnalystWrapper


def test_news_wrapper_yields_evidence_on_state_delta() -> None:
    """News wrapper yields the evidence list on state_delta after the inner agent."""
    class _Inner:
        name = "NewsAnalyst"
        async def run_async(self, _ctx):
            _ctx.session.state["news_evidence"] = [
                {"ticker": "AAPL", "analyst": "news"},
            ]
            if False:  # pragma: no cover
                yield None

    wrapper = YieldingAnalystWrapper(
        name="NewsAnalystBranch", inner=_Inner(),
        evidence_state_key="news_evidence",
    )

    fake_session = MagicMock()
    fake_session.state = {"tickers": ["AAPL"]}
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-3"
    fake_ctx.session = fake_session

    async def _drain() -> list:
        out = []
        async for ev in wrapper._run_async_impl(fake_ctx):
            out.append(ev)
        return out

    events = asyncio.run(_drain())
    assert len(events) == 1
    assert events[0].actions.state_delta["news_evidence"][0]["ticker"] == "AAPL"
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_news_yield.py -v`
Expected: PASS.

- [ ] **Step 8: Wrap the Fundamental + News module-level builders**

In `src/agents/analysts/fundamental/agent.py`, change `_build_fundamental_analyst` to return the wrapper. Replace the final `return LlmAgent(...)` with:

```python
    llm = LlmAgent(
        name="FundamentalAnalyst",
        model=model,
        instruction=instruction,
        output_schema=VerdictBatch,
        output_key="fundamental_verdicts",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=make_evidence_callback(
            analyst="fundamental",
            extractor=extract_fundamental_features,
            verdicts_state_key="fundamental_verdicts",
        ),
        before_model_callback=before_cb,
        after_model_callback=after_cb,
    )

    return YieldingAnalystWrapper(
        name="FundamentalAnalystBranch",
        inner=llm,
        evidence_state_key="fundamental_evidence",
    )
```

Add `from agents.analysts._base_yield import YieldingAnalystWrapper` to the imports at the top.

Make the equivalent change in `src/agents/analysts/news/agent.py`: replace the final `return LlmAgent(...)` with the same pattern, substituting `News` for `Fundamental` and `news_evidence` for `fundamental_evidence`.

- [ ] **Step 9: Run the fast suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS. If the AnalystPool still has the four-child shape, the wrappers slot in transparently and tests still pass. Task 6 changes that shape.

- [ ] **Step 10: Commit**

```bash
git add src/contract/evidence.py src/agents/analysts/_base_yield.py src/agents/analysts/fundamental/agent.py src/agents/analysts/news/agent.py tests/unit/contract/test_evidence_raw_text.py tests/unit/agents/analysts/test_fundamental_yield.py tests/unit/agents/analysts/test_news_yield.py
git commit -m "$(cat <<'EOF'
feat(analysts): wrap Fund/News in YieldingAnalystWrapper + add raw_text

A2.5 — closes the §C-Rule 1 deviation on the Fund/News
after_agent_callback evidence writes by republishing them as
state_delta yields on a thin BaseAgent wrapper.  AnalystEvidence gains
an optional raw_text field so the Strategist sees the raw provider text
the LLM analyst saw.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Restructure AnalystPool to `SequentialAgent([ParallelAgent([Technical, Social]), Fundamental, News])`

**A2.7** — Fundamental and News become sequential so each owns the `state_delta` rail unambiguously. Technical and Social stay parallel (they have no shared writes after A1's BaseAgent state_delta conversion).

**Files:**
- Modify: `src/orchestrator/pipeline.py:7-51` (`_build_analyst_pool`)
- Create: `tests/unit/orchestrator/test_pipeline_sequential_branches.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/orchestrator/test_pipeline_sequential_branches.py`:

```python
"""Topology guard — AnalystPool must be SequentialAgent[Parallel[Tech,Social], Fund, News].

A2.7 changes the analyst pool from a single 4-wide ParallelAgent into a
sequential chain so Fundamental and News each own the state_delta rail
unambiguously.  Technical and Social remain parallel (no shared writes
after A1's BaseAgent state_delta conversion).
"""
from __future__ import annotations


def test_analyst_pool_topology() -> None:
    """Pool is SequentialAgent whose first child is a 2-wide ParallelAgent."""
    from google.adk.agents import ParallelAgent, SequentialAgent

    from orchestrator.pipeline import _build_analyst_pool

    pool = _build_analyst_pool()

    assert isinstance(pool, SequentialAgent), (
        f"AnalystPool root must be SequentialAgent, got {type(pool).__name__}"
    )
    assert len(pool.sub_agents) == 3, (
        f"AnalystPool must have three children "
        f"(Parallel[Tech,Social], Fund, News); got {len(pool.sub_agents)}"
    )

    first = pool.sub_agents[0]
    assert isinstance(first, ParallelAgent), (
        f"First child must be a ParallelAgent (Technical + Social); "
        f"got {type(first).__name__}"
    )
    assert len(first.sub_agents) == 2, (
        f"Parallel branch must have two children (Tech + Social); "
        f"got {len(first.sub_agents)}"
    )

    # Names check — order matters for trace readability.
    assert {a.name for a in first.sub_agents} == {
        "TechnicalAnalyst", "SocialAnalyst",
    }

    # Second and third children are the Fund + News branches (wrapped by
    # YieldingAnalystWrapper, so their names end in "Branch").
    branch_names = {a.name for a in pool.sub_agents[1:]}
    assert branch_names == {"FundamentalAnalystBranch", "NewsAnalystBranch"}, (
        f"Sequential branches must be Fund + News wrappers; got {branch_names}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_pipeline_sequential_branches.py -v`
Expected: FAIL — pool is still `ParallelAgent` with four children.

- [ ] **Step 3: Restructure `_build_analyst_pool`**

In `src/orchestrator/pipeline.py`, replace the entire `_build_analyst_pool` function with:

```python
def _build_analyst_pool():
    """Build the AnalystPool — Sequential[Parallel[Tech,Social], Fund, News].

    Fundamental and News are sequential so each owns the state_delta rail
    unambiguously (see A2.7 — they wrap their LlmAgent in a
    ``YieldingAnalystWrapper`` to republish the evidence write as a yielded
    Event).  Technical and Social remain parallel — both are BaseAgent
    subclasses that already yield state_delta directly (A1.1 / A1.2), so
    Rule 4's unique-output-key invariant is satisfied (they write to
    distinct keys).

    SmartMoney is shelved (2026-05-19).  The analyst module remains so a
    one-line uncomment will revive it once notable_holders / politician
    trades have working PIT-correct providers.
    """
    from google.adk.agents import ParallelAgent, SequentialAgent

    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.agent import _build_news_analyst
    from agents.analysts.social.agent import _build_social_analyst
    from agents.analysts.technical.agent import _build_technical_analyst

    h = load_heuristics()

    parallel_deterministic = ParallelAgent(
        name="DeterministicAnalysts",
        sub_agents=[
            _build_technical_analyst(h.technical),
            _build_social_analyst(h.social),
        ],
    )

    return SequentialAgent(
        name="AnalystPool",
        sub_agents=[
            parallel_deterministic,
            _build_fundamental_analyst(h.fundamental_vocabulary),
            _build_news_analyst(h.news_vocabulary),
        ],
    )
```

- [ ] **Step 4: Run the topology test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_pipeline_sequential_branches.py -v`
Expected: PASS.

- [ ] **Step 5: Run the fast suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS. If any test pinned the AnalystPool as a 4-wide ParallelAgent, update that test.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/pipeline.py tests/unit/orchestrator/test_pipeline_sequential_branches.py
git commit -m "$(cat <<'EOF'
refactor(pipeline): split AnalystPool into Sequential[Parallel[Tech,Social], Fund, News]

Fundamental and News now run sequentially so each owns the state_delta
rail unambiguously.  Technical and Social stay parallel — both are
BaseAgents with distinct output keys (Rule 4 holds).  Smoke-test
artefact ordering is unaffected: the EvidenceWriter downstream reads
the same four state keys.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Prefix invocation-scoped keys with `temp:`

**A2.6** — rename the seven invocation-scoped keys at every write and read site so ADK strips them at invocation boundary.

The seven keys (per the brief): `held_positions_view`, `ticker_evidence`, `ticker_evidence_objects`, `technical_data`, `fundamental_data`, `news_data`, `social_data`. (StrategistContextShim already writes the first three with the `temp:` prefix from Task 1 — this task aligns the remaining read sites and the four `*_data` keys.)

**Files:**
- Modify: `src/agents/strategist/prompts.py` — instruction template placeholders `{held_positions_view}` → `{temp:held_positions_view}`, `{ticker_evidence}` → `{temp:ticker_evidence}`
- Modify: `src/agents/contract/evidence_writer.py` — read `temp:ticker_evidence_objects` instead of `ticker_evidence_objects`
- Modify: `src/agents/analysts/technical/fetch.py` — write `temp:technical_data`
- Modify: `src/agents/analysts/technical/agent.py` — read `temp:technical_data`
- Modify: `src/agents/analysts/social/fetch.py` — write `temp:social_data`
- Modify: `src/agents/analysts/social/agent.py` — read `temp:social_data`
- Modify: `src/agents/analysts/fundamental/fetch.py` — write `temp:fundamental_data`
- Modify: `src/agents/analysts/news/fetch.py` — write `temp:news_data`
- Modify: `src/agents/analysts/_common.py` (`make_evidence_callback`) — change `data: dict = state.get(f"{analyst}_data", {})` to `data: dict = state.get(f"temp:{analyst}_data", {})`
- Modify: `src/agents/analysts/cache_callbacks.py` — if it reads `{analyst}_data`, rename to `temp:{analyst}_data` (verify by Read first; brief says "if applicable")
- Modify: `docs/contract-invariants.md` — add a note in §C-Rule 2 listing these keys as concrete `temp:` examples
- Create: `tests/unit/orchestrator/test_temp_prefix_keys.py`

- [ ] **Step 1: Inventory the read/write sites**

Run a Grep to enumerate every site that writes or reads the seven affected keys, then cross-check against the modify list above:

```
grep -rn "held_positions_view\|ticker_evidence\|ticker_evidence_objects\|technical_data\|fundamental_data\|news_data\|social_data" src/ tests/ | grep -v "_evidence:" | grep -v "verdicts"
```

If a site exists outside the list above, add it to the modify list before proceeding.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/orchestrator/test_temp_prefix_keys.py`:

```python
"""Guard test — invocation-scoped keys must carry the ``temp:`` prefix.

ADK's documented ``temp:`` prefix is invocation-scoped: keys with that
prefix do not survive across ticks.  A2.6 renames seven textbook
invocation-scoped keys to use the prefix so accidental cross-tick reads
fail loudly instead of returning stale data.
"""
from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC = _PROJECT_ROOT / "src"


_FORBIDDEN_UNPREFIXED = (
    # (relative path under src/, forbidden bare key).
    ("agents/strategist/prompts.py",            "{held_positions_view}"),
    ("agents/strategist/prompts.py",            "{ticker_evidence}"),
    ("agents/contract/evidence_writer.py",      "ticker_evidence_objects"),
    ("agents/analysts/technical/fetch.py",      '"technical_data"'),
    ("agents/analysts/social/fetch.py",         '"social_data"'),
    ("agents/analysts/fundamental/fetch.py",    '"fundamental_data"'),
    ("agents/analysts/news/fetch.py",           '"news_data"'),
)


def test_no_bare_invocation_keys_in_source() -> None:
    """Every invocation-scoped key in the modify-list must be prefixed."""
    failures: list[str] = []
    for rel_path, forbidden in _FORBIDDEN_UNPREFIXED:
        text = (_SRC / rel_path).read_text(encoding="utf-8")
        if forbidden in text and f"temp:{forbidden.strip(chr(34))}" not in text:
            failures.append(f"{rel_path}: bare {forbidden!r} found without temp: prefix")
    assert not failures, "Unprefixed invocation keys still present:\n  " + "\n  ".join(failures)
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_temp_prefix_keys.py -v`
Expected: FAIL — the prefix renames have not been applied yet.

- [ ] **Step 3: Apply renames at every write/read site**

For each file in the modify list, replace every bare instance of the seven keys with the `temp:` form. Concrete substitutions:

- `held_positions_view` → `temp:held_positions_view`
- `ticker_evidence` (when it refers to the rendered string state key) → `temp:ticker_evidence`
- `ticker_evidence_objects` → `temp:ticker_evidence_objects`
- `technical_data` (state key only) → `temp:technical_data`
- `fundamental_data` (state key only) → `temp:fundamental_data`
- `news_data` (state key only) → `temp:news_data`
- `social_data` (state key only) → `temp:social_data`

Do NOT rename Python identifiers — only the string literals that are state keys. ADK's instruction-template substitution will read `{temp:ticker_evidence}` just like `{ticker_evidence}`.

For `src/agents/analysts/_common.py:95` change `data: dict = state.get(f"{analyst}_data", {}) or {}` to `data: dict = state.get(f"temp:{analyst}_data", {}) or {}`.

- [ ] **Step 4: Verify `cache_callbacks.py`**

Read `src/agents/analysts/cache_callbacks.py`. If it reads `data_state_key` from the agent factory (currently `"fundamental_data"`, `"news_data"`), the caller already supplies the value via `_build_*_analyst`. Update the two builders (`agent.py` for Fund and News) so the `data_state_key` argument now reads `temp:fundamental_data` / `temp:news_data` respectively. Cache callback internals do not change.

- [ ] **Step 5: Append concrete examples to `contract-invariants.md` §C-Rule 2**

In `docs/contract-invariants.md` §C-Rule 2 (around line 206-214), append a paragraph after the "ADK grounding" line:

```markdown
**Concrete invocation-scoped keys (A2.6 rename, 2026-05-20):**
the strategist's ``temp:held_positions_view``, ``temp:ticker_evidence``,
``temp:ticker_evidence_objects``, and the four analyst raw-data keys
``temp:technical_data`` / ``temp:fundamental_data`` / ``temp:news_data``
/ ``temp:social_data``.  All written by callbacks or the
``StrategistContextShim`` (Task A2.1); all consumed inside a single
tick by the analyst's own ``_run_async_impl`` or by the Strategist's
instruction template.
```

- [ ] **Step 6: Run the prefix guard test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_temp_prefix_keys.py -v`
Expected: PASS.

- [ ] **Step 7: Run the fast suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS. Tests that previously pinned the bare key names (e.g. test_fundamental_fetch.py) need to be updated to use the `temp:` form — these are surface-rename only.

- [ ] **Step 8: Commit**

```bash
git add src/ tests/ docs/contract-invariants.md
git commit -m "$(cat <<'EOF'
refactor(state): prefix invocation-scoped keys with temp:

A2.6 — adds the temp: prefix to held_positions_view, ticker_evidence,
ticker_evidence_objects, and the four {analyst}_data keys.  ADK strips
temp:-prefixed keys at the invocation boundary, so accidental cross-
tick reads now fail loudly instead of returning stale data.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Lint + format gate

The plan has touched many files. Run the linter as a checkpoint before the slow smoke test.

- [ ] **Step 1: Run ruff**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`
Expected: PASS (no lint errors). If errors are emitted, fix them inline before committing the smoke-test gate task.

- [ ] **Step 2: If ruff emitted fixes, commit them**

```bash
git add src/ tests/
git commit -m "$(cat <<'EOF'
style: ruff fixes after A2 structural conversion

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

If no fixes were emitted, skip the commit and continue to Task 9.

---

## Task 9: Final gate — full fast suite + slow smoke test

Verify the entire pipeline still runs end-to-end with the restructured shape.

- [ ] **Step 1: Run the full fast suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS — every test green.

- [ ] **Step 2: Run the end-to-end smoke test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`
Expected: PASS — the smoke test exits with `result.status in {"completed", "completed_with_failures"}`, the `traces/` directory contains one JSON per tick, `report/metrics.md` exists with a valid (non-NaN) total return, audit tripwires are clean.

Common failure modes to look for if Step 2 fails:

- `KeyError: 'held_positions_view'` in the Strategist instruction template: the prompt placeholder did not get the `temp:` prefix in Task 7. Grep `src/agents/strategist/prompts.py` for `{held_positions_view}` and rename to `{temp:held_positions_view}`.
- `AttributeError: 'SequentialAgent' object has no attribute 'before_agent_callback'` in the smoke test: the `_patched_build_strategist` is still calling `LlmAgent(...)` directly without wrapping in a `SequentialAgent`. Reapply Task 1 Step 5.
- `KeyError: 'temp:technical_data'` or similar in an extractor: the read site missed the rename. Re-grep with Task 7 Step 1's command.

- [ ] **Step 3: Append the graphify-delta entry**

Edit `graphify-out/graph_delta.md` (do NOT `git add`) and append:

```
## 2026-05-20 — A2 structural conversion landed

Strategist now SequentialAgent[StrategistContextShim, LlmAgent].
AnalystPool now SequentialAgent[Parallel[Tech,Social], Fund, News]
with Fund/News wrapped in YieldingAnalystWrapper.  Invocation-scoped
state keys prefixed with temp:.  In-tick callback carve-out documented
in contract-invariants.md.

- New nodes: agents.strategist.context_shim.StrategistContextShim,
  agents.analysts._base_yield.YieldingAnalystWrapper
- New edges: pipeline._build_strategist → context_shim.StrategistContextShim;
  pipeline._build_analyst_pool → _base_yield.YieldingAnalystWrapper (×2)
- Removed: agents.strategist.agent._strategist_after_model_composite,
  agents.strategist.agent._composite_before_callback
```

- [ ] **Step 4: No commit for graph_delta — it is gitignored**

Per `.claude/CLAUDE.md`, the `graphify-out/` directory is gitignored and must not be staged. Verify with `git status` that the delta entry is not in the staging area.

- [ ] **Step 5: Final commit (only if Steps 1–2 passed)**

If the smoke test passed and there are no uncommitted source changes, this task ends here — Tasks 1–8 already shipped their respective commits.

If the smoke test surfaced any new bugs that needed inline fixes during Step 2, commit them with:

```bash
git add src/ tests/
git commit -m "$(cat <<'EOF'
fix: smoke-test follow-ups for A2 structural conversion

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Anti-scope reminder

These items are explicitly out-of-scope for this plan and must not be modified:

- `_strategist_validation_callback` body — kept intact under the in-tick carve-out (Task 3).
- A `StrategistValidator` `BaseAgent` shim — do not create one.
- `_run_async_impl` of `TechnicalAnalyst`, `SocialAnalyst`, or `RiskGateAgent` — owned by A1.
- The live tick builder's `as_of` / `tick_phase` seeding — owned by A1.
- Singular/plural verdict-key rename — owned by A1.
- Folding `watchlist` into `tickers` — owned by A1.
- The AST-walker contract test (todo-fixes 2.5.2).
- Relocating DB writers into Phase 4 hooks (todo-fixes 2.5.4).
- Persistence subsystem implementation (todo-fixes 2.5.3).

If any of these become necessary in mid-task to keep the plan working, STOP and escalate — they are deliberately gated behind other workstreams.

---

## Self-review

**1. Spec coverage:**

- Item 1 (Strategist ContextShim + delete after_model_callback): Tasks 1 + 2. ✅
- Item 2 (TraceWriter `default=str` fix): OQ-1 surfaces that it is already present; Task 4 lands the regression test. ⚠ Surfaced as open question.
- Item 3 (Document in-tick callback carve-out): Task 3. ✅
- Item 4 (Fund/News analyst callback conversions + `raw_text` field): Task 5. ✅
- Item 5 (Sequential analyst branches): Task 6. ✅
- Item 6 (`temp:` prefix for invocation-scoped fields): Task 7. ✅
- Full-suite + smoke gate: Task 9. ✅

**2. Placeholder scan:** No "TBD" / "implement later" / "appropriate error handling" / "similar to" in the plan. Every code-emitting step contains the actual code. ✅

**3. Type consistency:** `StrategistContextShim` is referenced by Task 1 + Task 2 + Task 9 — same name everywhere. `YieldingAnalystWrapper` is referenced by Task 5 + Task 6 + Task 9 — same name everywhere. `evidence_state_key` is the constructor arg in Task 5 Step 5 and used in Task 5 Step 7 — same name. The seven `temp:` keys are listed identically in Task 1, Task 7, and the contract spec edit in Task 7 Step 5. ✅

**4. Open questions surfaced (per the brief's "STOP and report back" instruction):** OQ-1 (TraceWriter `default=str` already present — resolved as a regression-test guard, no source edit) and OQ-2 (the `after_model_callback` was wrapping a clamp, not just a trace-helper — resolved 2026-05-20 by deleting the clamp outright as YAGNI defensive code) are both flagged and resolved at the top of the document. OQ-3 (sequential-branch shape proposal) is also surfaced and adopted unless flipped.

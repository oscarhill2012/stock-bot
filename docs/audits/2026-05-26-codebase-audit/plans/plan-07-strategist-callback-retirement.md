# Plan 07 — Strategist Legacy-Callback + `evidence_view` Retirement

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the legacy `_strategist_validation_callback` shim and the dead
`agents/strategist/evidence_view.py` module from the live tree, migrate the
three integration tests that still wire their own LlmAgent to call the
sequenced `StrategistEnricher` BaseAgent path instead, and remove the
invariants-doc carve-out test that pins documentation text for code that
will no longer exist.

**Architecture:** The production strategist branch is already a
`SequentialAgent([StrategistContextShim, RetryingAgentWrapper[LlmAgent],
StrategistEnricher])` and the prompt-render path is already
`contract.strategist_prompt.render_all_ticker_blocks`. The legacy
`_strategist_validation_callback` and `evidence_view.py` survive only as
test-side delegates. This plan removes both symbols from `src/`, deletes
the `build_strategist_enricher` factory (single-caller — its own factory),
deletes the invariants-doc guard test, and migrates the three live-LlmAgent
integration tests to a small in-test helper that wraps the enricher.

**Tech Stack:** Python 3.12, Google ADK (`google-adk`), pytest, Pydantic
v2. No new dependencies.

---

## Trust contract

- **Trusts Plan 01 (pure deletions)** to have deleted the five strategist
  legacy-callback test files and the three `evidence_view` test files
  outright (FINDINGS A-024, A-026). If Plan 01 has NOT yet landed when
  this plan runs, the deletions in Task 7 below cover the gap (it is
  idempotent — `rm -f` on already-absent files is a no-op).
- **Trusts Plan 02 (rationale dedupe)** to have collapsed
  `reason`/`rationale` so the enricher's normalisation is the single
  surviving rationale path. The behavioural surface that the legacy
  callback used to test (off-watchlist rejection, bad rationale,
  exhaustiveness) is now covered by `tests/unit/agents/strategist/test_enricher.py`.
- **Later plans trust this plan to land:**
  - `src/agents/strategist/agent.py` no longer exports
    `_strategist_validation_callback`. Any import of that name fails
    loudly at import time.
  - `src/agents/strategist/evidence_view.py` does not exist.
  - `src/agents/strategist/enricher.py` no longer exports
    `build_strategist_enricher`.
  - `docs/contract-invariants.md` no longer contains the
    `_strategist_validation_callback` carve-out clause (it is replaced
    by a single line stating the strategist branch performs enrichment
    via a sequenced BaseAgent that yields `state_delta`).
  - **Plan 11 (test consolidation)** will delete
    `tests/integration/test_strategist_minimal_schema_no_retry.py` and
    the two backtest smoke files' legacy-callback fixtures as part of
    its tree consolidation — Plan 11 trusts the migrations below to
    have moved any load-bearing coverage into `test_enricher.py` and
    the new `tests/integration/test_strategist_enricher_smoke.py`.

---

## Callback retirement map

| Legacy entrypoint | Consumes today (test-side) | Migrates to |
|---|---|---|
| `agents.strategist.agent._strategist_validation_callback` | `tests/integration/test_strategist_minimal_schema_no_retry.py` (direct call) | New `tests/integration/test_strategist_enricher_smoke.py` driving `StrategistEnricher` via a `SequentialAgent[stub-LlmAgent, StrategistEnricher]` and asserting `state["strategist_decision"]` after a real Runner tick. |
| `agents.strategist.agent._strategist_validation_callback` | `tests/integration/backtest/test_end_to_end_smoke.py:390-408` (wired into hand-built LlmAgent as `after_agent_callback`) | Replaced by `_patched_build_strategist` that builds `SequentialAgent[StrategistContextShim, stub-LlmAgent, StrategistEnricher]` — the live shape, minus the retry wrapper. |
| `agents.strategist.agent._strategist_validation_callback` | `tests/integration/backtest/test_fresh_run_starts_clean.py:161-190,261` (same pattern) | Same migration as above. |
| `agents.strategist.agent._strategist_validation_callback` | `tests/unit/agents/strategist/test_validation_callback.py` (full file) | **Delete** — semantics are covered by `test_enricher.py`. If Plan 01 deleted it already, this is a no-op. |
| `agents.strategist.agent._strategist_validation_callback` | `tests/unit/agents/strategist/test_strategist_callbacks_v2.py` (full file) | **Delete** — same reasoning. |
| `agents.strategist.evidence_view.render_ticker_evidence` | `tests/unit/agents/strategist/test_evidence_view.py` | **Delete** module + test. Live renderer is `contract.strategist_prompt.render_all_ticker_blocks` (called from `context_shim.py:290`). |
| `agents.strategist.evidence_view._format_per_analyst` | `test_evidence_view_drops_dead_social.py`, `test_evidence_view_missing_report.py` | **Delete** — both tests are about a dead renderer. |
| `agents.strategist.enricher.build_strategist_enricher` | nothing — factory is a single-caller of `StrategistEnricher()` with no DI surface | **Delete** the function (4 lines). |
| `tests/unit/contract/test_invariants_doc_carveout.py` | asserts that `docs/contract-invariants.md` contains the literal `"In-tick callback carve-out"` and that `docs/Phase8-contract-audit-fixes/contract-audit.md` mentions the strategist row as "in-tick carve-out" | **Delete** test; also update `docs/contract-invariants.md` to drop the now-stale clause and the `_strategist_validation_callback` reference. |

After this plan: the only path that converts `StrategistLLMDecision` →
`StrategistDecision` is `StrategistEnricher` (a `BaseAgent` running inside
`SequentialAgent`), which yields a single `Event(state_delta=…)` per
contract Rule 1. There is no callback shim, no parallel implementation,
no carve-out to document.

---

## File structure

**Source modifications (live tree):**

- Modify: `src/agents/strategist/agent.py` — delete
  `_strategist_validation_callback` (lines 51-90) and shorten the module
  docstring (drop the legacy-shim paragraph at lines 30-37).
- Delete: `src/agents/strategist/evidence_view.py` (whole file, ~170 LoC).
- Modify: `src/agents/strategist/enricher.py` — delete
  `build_strategist_enricher` (lines 357-362) and a stale docstring
  sentence at line 14 referencing the legacy callback.
- Modify: `src/agents/strategist/context_shim.py:5` — drop the dead
  parenthetical reference to `_evidence_view_before_callback`.

**Docs:**

- Modify: `docs/contract-invariants.md` — delete the carve-out clause
  for `_strategist_validation_callback` (around lines 234-285, see
  Task 6) and any prose referencing the callback. Replace with one
  sentence stating the strategist runs `StrategistEnricher` as a
  sequenced BaseAgent that yields `state_delta` (no carve-out
  required).

**Test changes:**

- Delete: `tests/unit/agents/strategist/test_validation_callback.py`
- Delete: `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`
- Delete: `tests/unit/agents/strategist/test_evidence_view.py`
- Delete: `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py`
- Delete: `tests/unit/agents/strategist/test_evidence_view_missing_report.py`
- Delete: `tests/unit/contract/test_invariants_doc_carveout.py`
- Delete: `tests/integration/test_strategist_minimal_schema_no_retry.py`
  (replaced by the new enricher smoke).
- Modify: `tests/integration/backtest/test_end_to_end_smoke.py` —
  rewrite `_patched_build_strategist` (lines 386-415) to sequence
  `StrategistEnricher` instead of wiring `after_agent_callback`.
- Modify: `tests/integration/backtest/test_fresh_run_starts_clean.py` —
  same rewrite for `_patched_build_strategist` (lines 161-190).
- Create: `tests/integration/test_strategist_enricher_smoke.py` — new
  test that drives `StrategistEnricher` end-to-end with a stub LlmAgent.
- Create: `tests/unit/agents/strategist/test_legacy_symbols_gone.py` —
  one-shot guard that `_strategist_validation_callback`,
  `build_strategist_enricher`, and `agents.strategist.evidence_view`
  cannot be imported (i.e. the retired path is genuinely unreachable).

---

## Ordered changes

Order matters: migrate the live tests first (so they keep passing as
the source symbols disappear), then delete the source symbols, then
delete the dead tests, then the doc-pin test, then the doc itself,
then add the import-guard test.

---

### Task 1: Add the import-guard test (TDD anchor — should currently FAIL)

**Files:**
- Create: `tests/unit/agents/strategist/test_legacy_symbols_gone.py`

- [ ] **Step 1: Write the import-guard test**

```python
"""Guard that the retired legacy strategist surface is genuinely unreachable.

Plan 07 retires three symbols from the live tree:

  - ``agents.strategist.agent._strategist_validation_callback``
  - ``agents.strategist.enricher.build_strategist_enricher``
  - ``agents.strategist.evidence_view`` (entire module)

Any future re-introduction (whether as a fresh definition or as a
re-export) would silently revive a parallel code path next to the
sequenced ``StrategistEnricher``.  This test fails loudly the moment any
of those names becomes importable again — no silent revival.

We assert via ``importlib`` so a typo in the symbol name surfaces here
rather than as a false-green PASS.
"""
from __future__ import annotations

import importlib

import pytest


def test_strategist_validation_callback_is_gone() -> None:
    """The legacy after_agent_callback shim must not exist on the agent module."""

    agent_module = importlib.import_module("agents.strategist.agent")
    assert not hasattr(agent_module, "_strategist_validation_callback"), (
        "agents.strategist.agent._strategist_validation_callback was retired "
        "in Plan 07 — production uses the sequenced StrategistEnricher.  "
        "Reintroducing this symbol revives a parallel enrichment path."
    )


def test_build_strategist_enricher_factory_is_gone() -> None:
    """The single-caller factory must not exist on the enricher module."""

    enricher_module = importlib.import_module("agents.strategist.enricher")
    assert not hasattr(enricher_module, "build_strategist_enricher"), (
        "build_strategist_enricher was a single-caller factory with no DI "
        "surface — retired in Plan 07.  Construct StrategistEnricher() "
        "directly."
    )


def test_evidence_view_module_is_gone() -> None:
    """The dead evidence_view renderer module must not be importable."""

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agents.strategist.evidence_view")
```

- [ ] **Step 2: Run it to confirm it FAILS (red phase)**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_legacy_symbols_gone.py -v`
Expected: all three tests FAIL — symbols still exist.

- [ ] **Step 3: Commit (red-phase snapshot)**

```bash
git add tests/unit/agents/strategist/test_legacy_symbols_gone.py
git commit -m "test(strategist): add import-guard for retired legacy surface (red)"
```

---

### Task 2: Build the new enricher smoke test (replacement for `test_strategist_minimal_schema_no_retry.py`)

**Files:**
- Create: `tests/integration/test_strategist_enricher_smoke.py`

- [ ] **Step 1: Write the new smoke test**

The replacement exercises the same surface (validation + derivation
runs once on a clean intent-form decision) but through the
production-shape sequenced enricher rather than via a direct callback
call.

```python
"""Strategist enricher smoke — replaces the legacy-callback minimal-schema test.

The original ``test_strategist_minimal_schema_no_retry.py`` proved that a
clean intent-form ``StrategistLLMDecision`` flows through
``_strategist_validation_callback`` unchanged.  Plan 07 retired that
callback; the same invariant is now expressed by driving the production
``StrategistEnricher`` BaseAgent with a stub LlmAgent.

We assert:
  1. The stub LLM emits its payload via ``output_key`` exactly once.
  2. The sequenced ``StrategistEnricher`` rewrites
     ``state["strategist_decision"]`` to the enriched dump.
  3. ``derive_decision_fields`` populates ``target_weights`` and
     ``close_reasons`` as expected from the input stances.

No live API.  No retry wrapper (the retry layer is its own concern and
is covered separately).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from agents.strategist.enricher import StrategistEnricher
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance


class _StubLlmAgent(BaseAgent):
    """Yields a single Event whose ``state_delta`` writes a clean narrow decision.

    Replaces the real LlmAgent so the test never touches Vertex.  Mirrors
    what ``LlmAgent`` does with ``output_key`` on a successful call.
    """

    name: str = "Strategist"

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # Build the narrow LLM-shape decision.  Two stances exercise the
        # buy and update verbs; weights stay inside the 5 % buy-delta cap.
        decision = StrategistDecision(
            stances=[
                TickerStance(
                    ticker    = "AAPL",
                    intent    = "buy",
                    weight    = 0.04,
                    rationale = "Strong earnings momentum and AI tailwind.",
                ),
                TickerStance(
                    ticker    = "MSFT",
                    intent    = "update",
                    rationale = "Prose-only update — no trade this tick.",
                ),
            ],
        ).model_dump(mode="json")

        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "strategist_decision": decision,
            }),
        )


@pytest.mark.asyncio
async def test_enricher_rewrites_decision_to_enriched_dump() -> None:
    """A clean intent-form decision flows through enricher to enriched shape."""

    branch = SequentialAgent(
        name       = "StrategistBranch",
        sub_agents = [_StubLlmAgent(), StrategistEnricher()],
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name = "test",
        user_id  = "u",
        state    = {
            # Seed the keys the enricher reads — same shape as production.
            "tickers": ["AAPL", "MSFT"],
            "watchlist": ["AAPL", "MSFT"],
            "tick_id": "tick-001",
            "user:active_stances": {},
            "user:active_stances_initialised": False,
        },
    )

    runner = Runner(agent=branch, app_name="test", session_service=session_service)

    # Drive the runner; we don't need user input — the stub yields proactively.
    async for _ in runner.run_async(
        user_id     = "u",
        session_id  = session.id,
        new_message = genai_types.Content(parts=[genai_types.Part.from_text(text="")]),
    ):
        pass

    final = await session_service.get_session(
        app_name = "test", user_id = "u", session_id = session.id,
    )
    enriched = final.state["strategist_decision"]

    # Enriched dump carries the derived fields the LLM doesn't emit directly.
    assert "target_weights" in enriched, (
        "StrategistEnricher should populate target_weights from stances"
    )
    assert enriched["target_weights"].get("AAPL") == pytest.approx(0.04)
    # MSFT has no weight — derivation should leave it out or set 0.0.
    assert enriched["target_weights"].get("MSFT", 0.0) == pytest.approx(0.0)

    # The user:active_stances_initialised flip is the enricher's one-shot.
    assert final.state["user:active_stances_initialised"] is True
```

- [ ] **Step 2: Run the new smoke test — should PASS today**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_strategist_enricher_smoke.py -v`
Expected: PASS — the enricher already exists and behaves this way today.

If it fails, do NOT proceed — debug the test against the current enricher
before changing source.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_strategist_enricher_smoke.py
git commit -m "test(strategist): add enricher-driven smoke replacing legacy callback test"
```

---

### Task 3: Migrate `test_end_to_end_smoke.py` `_patched_build_strategist`

**Files:**
- Modify: `tests/integration/backtest/test_end_to_end_smoke.py:380-415`

- [ ] **Step 1: Rewrite `_patched_build_strategist` to use the enricher**

Replace the current function body so it builds the same sequenced shape
as the live `build_strategist()` (minus retry wrapper). Replace the
existing lines 386-415 with:

```python
    def _patched_build_strategist():
        """Build strategist as SequentialAgent[ContextShim, stub LlmAgent, Enricher].

        Mirrors the live shape (minus RetryingAgentWrapper) so the test
        exercises the production enrichment path rather than the retired
        ``_strategist_validation_callback`` shim.
        """
        from google.adk.agents import LlmAgent, SequentialAgent

        from agents.strategist.context_shim import StrategistContextShim
        from agents.strategist.enricher import StrategistEnricher
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistLLMDecision

        def _mock_before(callback_context, llm_request):
            """Return a synthetic StrategistLLMDecision without calling Gemini."""
            current_tickers = (
                callback_context.state.get("tickers") or tickers
            )
            return _make_strategist_llm_response(current_tickers)

        llm = LlmAgent(
            name                  = "Strategist",
            model                 = "gemini-2.5-pro",
            instruction           = STRATEGIST_INSTRUCTION,
            output_schema         = StrategistLLMDecision,
            output_key            = "strategist_decision",
            before_model_callback = _mock_before,
        )

        return SequentialAgent(
            name       = "StrategistBranch",
            sub_agents = [StrategistContextShim(), llm, StrategistEnricher()],
        )
```

Key edits versus the original:
- Drops the `from agents.strategist.agent import _strategist_validation_callback` import.
- Drops `after_agent_callback=_strategist_validation_callback` from the
  `LlmAgent` constructor.
- Adds `StrategistEnricher()` as the third sub-agent.
- Switches `output_schema` from `StrategistDecision` to the narrow
  `StrategistLLMDecision` (the enricher widens it). If
  `_make_strategist_llm_response` returns a payload that fits the
  narrow schema today (it should — the legacy callback validated the
  narrow shape), no further edits needed. If the helper produces
  enriched fields, run the test and reduce to the narrow shape.

- [ ] **Step 2: Run the modified smoke test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v`
Expected: PASS.

If `_make_strategist_llm_response` emits an enriched payload that fails
`StrategistLLMDecision` validation, edit the helper to emit the narrow
shape (drop `target_weights`, `close_reasons`, etc.) and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/backtest/test_end_to_end_smoke.py
git commit -m "test(backtest): migrate end_to_end_smoke off legacy strategist callback"
```

---

### Task 4: Migrate `test_fresh_run_starts_clean.py` `_patched_build_strategist`

**Files:**
- Modify: `tests/integration/backtest/test_fresh_run_starts_clean.py:155-190` (and the import at line 27 if applicable)

- [ ] **Step 1: Rewrite the helper to use the enricher**

Replace lines 161-190 with:

```python
    def _patched_build_strategist():
        """Build strategist as SequentialAgent[ContextShim, stub LlmAgent, Enricher].

        See ``test_end_to_end_smoke.py`` for the migration rationale —
        the legacy ``_strategist_validation_callback`` was retired in
        Plan 07 and this test now exercises the same path as production.
        """
        from google.adk.agents import LlmAgent, SequentialAgent
        from google.adk.models import LlmResponse
        from google.genai import types as genai_types

        from agents.strategist.context_shim import StrategistContextShim
        from agents.strategist.enricher import StrategistEnricher
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistLLMDecision

        # Narrow-shape stub payload — the enricher widens it.
        stances = [
            {"ticker": t, "intent": "hold", "rationale": "fresh-test stub"}
            for t in tickers
        ]
        decision = {"stances": stances}

        def _mock_before(ctx, req):
            return LlmResponse(content=genai_types.Content(
                parts=[genai_types.Part.from_text(text=json.dumps(decision))]
            ))

        llm = LlmAgent(
            name                  = "Strategist",
            model                 = "gemini-2.5-pro",
            instruction           = STRATEGIST_INSTRUCTION,
            output_schema         = StrategistLLMDecision,
            output_key            = "strategist_decision",
            before_model_callback = _mock_before,
        )
        return SequentialAgent(
            name       = "StrategistBranch",
            sub_agents = [StrategistContextShim(), llm, StrategistEnricher()],
        )
```

Key edits versus the original:
- Imports `StrategistLLMDecision` (narrow) and `StrategistEnricher`
  instead of `StrategistDecision` and `_strategist_validation_callback`.
- Drops the `intent="hold"` stub's wide-shape fields (`target_weights`,
  `decision_tag`, `reasoning`, `thesis`, `confidence`) — the narrow
  schema does not carry them, and the enricher derives them.
- Renames `reason` → `rationale` in the stance dict to match the
  post-Plan-02 vocabulary.
- Adds `StrategistEnricher()` as the third sub-agent.

- [ ] **Step 2: Run the modified test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_fresh_run_starts_clean.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/backtest/test_fresh_run_starts_clean.py
git commit -m "test(backtest): migrate fresh_run_starts_clean off legacy strategist callback"
```

---

### Task 5: Delete `_strategist_validation_callback` from source (live-tree retirement begins)

**Files:**
- Modify: `src/agents/strategist/agent.py` — delete lines 30-37 (legacy-shim paragraph in module docstring) and lines 51-90 (the shim function and its preceding section comment).

- [ ] **Step 1: Edit the module docstring**

Open `src/agents/strategist/agent.py`. Delete the paragraph that begins
`"The legacy \`\`_strategist_validation_callback\`\` function still exists"`
and ends `"... no parallel implementation to drift."` (the closing
paragraph of the module docstring). Keep the preceding paragraph that
explains why the enricher is a BaseAgent rather than a callback —
that's still load-bearing context.

- [ ] **Step 2: Delete the section comment and the function**

Delete the section header `# ── Legacy callback shim ──...` (line 51)
and the entire `_strategist_validation_callback` function (lines 54-90).

- [ ] **Step 3: Run the legacy-symbol guard test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_legacy_symbols_gone.py::test_strategist_validation_callback_is_gone -v`
Expected: PASS (the first of the three guard tests now passes).

- [ ] **Step 4: Run the full strategist test directory + the two migrated backtest smokes**

Run:
```
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/agents/strategist/ \
  tests/integration/test_strategist_enricher_smoke.py \
  tests/integration/backtest/test_end_to_end_smoke.py \
  tests/integration/backtest/test_fresh_run_starts_clean.py \
  -v
```

Expected: every test PASSES except the soon-to-be-deleted
`test_validation_callback.py` and `test_strategist_callbacks_v2.py`,
which now fail with `ImportError`. That failure is the correct signal
to proceed to Task 7. **Do not** patch those tests — they are slated
for deletion.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/agent.py
git commit -m "refactor(strategist): delete _strategist_validation_callback shim"
```

---

### Task 6: Delete `evidence_view.py` and `build_strategist_enricher`

**Files:**
- Delete: `src/agents/strategist/evidence_view.py`
- Modify: `src/agents/strategist/enricher.py:357-362` (delete factory) and `:14` (docstring nit)
- Modify: `src/agents/strategist/context_shim.py:5` (drop stale parenthetical)

- [ ] **Step 1: Delete `evidence_view.py`**

```bash
git rm src/agents/strategist/evidence_view.py
```

- [ ] **Step 2: Delete `build_strategist_enricher` from `enricher.py`**

In `src/agents/strategist/enricher.py`, delete lines 357-362 (the
`build_strategist_enricher` function and its preceding blank line).

Also fix the docstring at line 14:

```
Originally the enrichment lived inside ``_strategist_validation_callback``
```

→ replace with:

```
Originally the enrichment lived inside an ``after_agent_callback`` on the
LlmAgent
```

(Removes the stale symbol name so grep for `_strategist_validation_callback`
returns zero hits across `src/`.)

- [ ] **Step 3: Fix `context_shim.py:5` dead reference**

Open `src/agents/strategist/context_shim.py`. Line 5 currently reads
something like:

```
``_evidence_view_before_callback`` in ``agents/strategist/agent.py``).
```

Replace the parenthetical with a reference to the live renderer:

```
``render_all_ticker_blocks`` in ``contract/strategist_prompt.py``).
```

- [ ] **Step 4: Run the import-guard test — the remaining two should now PASS**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_legacy_symbols_gone.py -v`
Expected: all three guard tests PASS.

- [ ] **Step 5: Sanity grep for any remaining live-tree refs**

Run:
```
grep -rn "_strategist_validation_callback\|build_strategist_enricher\|evidence_view" src/
```

Expected output: empty (or only matches in fixed docstrings that
mention the historical concept without naming the symbol).

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/enricher.py src/agents/strategist/context_shim.py
git commit -m "refactor(strategist): delete evidence_view module + build_strategist_enricher factory"
```

---

### Task 7: Delete the dead test files

**Files (deletions — idempotent if Plan 01 already removed them):**
- `tests/unit/agents/strategist/test_validation_callback.py`
- `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`
- `tests/unit/agents/strategist/test_evidence_view.py`
- `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py`
- `tests/unit/agents/strategist/test_evidence_view_missing_report.py`
- `tests/integration/test_strategist_minimal_schema_no_retry.py`

- [ ] **Step 1: Delete the files**

```bash
git rm -f \
  tests/unit/agents/strategist/test_validation_callback.py \
  tests/unit/agents/strategist/test_strategist_callbacks_v2.py \
  tests/unit/agents/strategist/test_evidence_view.py \
  tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py \
  tests/unit/agents/strategist/test_evidence_view_missing_report.py \
  tests/integration/test_strategist_minimal_schema_no_retry.py
```

If `git rm -f` reports "did not match any files" for any path, that
means Plan 01 already removed it — continue with the rest.

- [ ] **Step 2: Run the strategist test directory**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/ tests/integration/test_strategist_enricher_smoke.py -v`
Expected: PASS — no `ImportError`s, no broken collection.

- [ ] **Step 3: Commit**

```bash
git commit -m "test(strategist): delete legacy-callback + evidence_view test cluster"
```

---

### Task 8: Retire the invariants-doc carve-out (doc + guard test)

**Files:**
- Delete: `tests/unit/contract/test_invariants_doc_carveout.py`
- Modify: `docs/contract-invariants.md` (remove the `_strategist_validation_callback` carve-out clause around lines 234-285)

- [ ] **Step 1: Delete the doc-pinning test**

```bash
git rm tests/unit/contract/test_invariants_doc_carveout.py
```

- [ ] **Step 2: Edit `docs/contract-invariants.md`**

Open the file. Locate the "In-tick callback carve-out" clause (search
for the literal string `In-tick callback carve-out`). Delete the entire
clause and its clarification paragraphs (the section that mentions
`_strategist_validation_callback`, roughly lines 234-285 — verify
boundaries against the live file).

Replace it with a single sentence in the same location:

```
**Strategist enrichment.**  The strategist branch performs validation
and derivation in a sequenced ``StrategistEnricher`` BaseAgent that
yields a single ``Event(state_delta=…)`` per tick — fully conformant
with Rule 1, no carve-out required.
```

Also scan for any remaining mentions of `_strategist_validation_callback`
in the file (line 245, 269 per the grep) and delete those sentences
or rewrite them to reference `StrategistEnricher` instead.

- [ ] **Step 3: Sanity grep across docs/ for orphan references**

Run:
```
grep -n "_strategist_validation_callback" docs/contract-invariants.md
```

Expected: empty.

Wider sweep (informational — older phase docs may still mention the
symbol historically and that is fine; only `docs/contract-invariants.md`
must be clean):

```
grep -rn "_strategist_validation_callback" docs/ | grep -v "Phase4\|Phase5\|Phase8\|todo-fixes\|post-phase4"
```

Expected: empty.

- [ ] **Step 4: Commit**

```bash
git add docs/contract-invariants.md
git commit -m "docs(invariants): retire strategist callback carve-out — enricher is Rule 1 conformant"
```

---

### Task 9: Full test-suite verification

- [ ] **Step 1: Run the full suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x --tb=short`
Expected: PASS in entirety. Any failure must be traced to either:
  (a) a missed migration site → fix in-pass; or
  (b) a test that depended on the legacy symbol but was not in the
      audit list → investigate and report before deleting.

- [ ] **Step 2: Run ruff to confirm no lint regressions**

Run: `.venv/bin/python -m ruff check src/agents/strategist/ tests/integration/ tests/unit/agents/strategist/`
Expected: clean.

- [ ] **Step 3: Final orphan-reference sweep across the whole repo**

```
grep -rn "_strategist_validation_callback\|build_strategist_enricher" src/ tests/ scripts/
```

Expected: empty.

```
grep -rn "agents.strategist.evidence_view" src/ tests/ scripts/
```

Expected: empty.

- [ ] **Step 4: Commit any cleanup (only if step 1-3 surfaced anything)**

Otherwise no commit required at this step.

---

## Test strategy

| Test file | Disposition | Reason |
|---|---|---|
| `tests/unit/agents/strategist/test_validation_callback.py` | **Delete** | Whole file imports and exercises `_strategist_validation_callback`. Coverage already exists in `test_enricher.py`. |
| `tests/unit/agents/strategist/test_strategist_callbacks_v2.py` | **Delete** | Same — exercises the retired callback. |
| `tests/unit/agents/strategist/test_evidence_view.py` | **Delete** | Tests the dead renderer. |
| `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py` | **Delete** | Same. |
| `tests/unit/agents/strategist/test_evidence_view_missing_report.py` | **Delete** | Same. |
| `tests/unit/contract/test_invariants_doc_carveout.py` | **Delete** | Asserts on doc text for the retired carve-out. |
| `tests/integration/test_strategist_minimal_schema_no_retry.py` | **Delete** | Replaced by `tests/integration/test_strategist_enricher_smoke.py`. |
| `tests/integration/backtest/test_end_to_end_smoke.py` | **Rewrite** `_patched_build_strategist` | Now sequences the enricher; same coverage, production-shape pipeline. |
| `tests/integration/backtest/test_fresh_run_starts_clean.py` | **Rewrite** `_patched_build_strategist` | Same. |
| `tests/integration/test_strategist_enricher_smoke.py` | **New** | Drives `StrategistEnricher` end-to-end via a stub LlmAgent — replacement for the deleted minimal-schema test. |
| `tests/unit/agents/strategist/test_legacy_symbols_gone.py` | **New** | Import-guard proving the retired surface is genuinely unreachable. Prevents silent revival. |

**Coverage delta:** what previously was "the callback ran on a fake LLM
output and produced the enriched decision" is now "the enricher
BaseAgent ran on a fake LLM output (yielded via `state_delta`) and
rewrote the decision to the enriched dump". Same invariant, exercised
through the production code path.

---

## Risks / silent-regression checklist

- **Orchestrator wiring.** Confirm nothing in `src/orchestrator/`
  imports `_strategist_validation_callback`. Verified during research:
  the only `src/` reference was the agent module itself plus the dead
  factory in `enricher.py`. Re-grep at Task 6 Step 5.
- **ADK plugin references.** No ADK plugin in `src/` registers the
  callback (verified — `HandleInjectorPlugin` and friends do not touch
  strategist callbacks). Still: at Task 9 Step 3, the cross-repo grep
  will catch any plugin-side import that slipped through.
- **Hidden re-exports.** Some packages re-export symbols via `__init__.py`.
  Inspect `src/agents/strategist/__init__.py` during Task 5 — if it
  re-exports `_strategist_validation_callback` or `build_strategist_enricher`,
  delete the re-export line in the same commit.
- **Loud-failure preservation.** Task 1's import-guard test is the
  affirmative proof that any future re-introduction fails loudly. Do
  not soften it to a `try/except` — it must `pytest.raises` /
  `assert not hasattr` so a silent revival of the symbol becomes a
  CI red.
- **Backtest smoke shape drift.** Tasks 3 and 4 switch the stub
  LlmAgent's `output_schema` from `StrategistDecision` (wide) to
  `StrategistLLMDecision` (narrow). If the existing stub helpers
  emit wide-shape fields, narrow them in the same commit — do not
  add a compatibility shim in the schema.
- **Doc clean-up trust contract.** Task 8 deletes
  `tests/unit/contract/test_invariants_doc_carveout.py` and edits
  `docs/contract-invariants.md` in the same commit. The doc edit
  must land — leaving the test deleted while the doc still names
  the retired callback creates a "stale doc, no guard" condition
  that Plan 11 would not catch.
- **`pytest -k evidence_view` should be empty after Task 7.** Quick
  sanity check before Task 8:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k evidence_view --collect-only`
  Expected: collected 0 items.

---

## Definition of done

All of the following must hold:

- [ ] `grep -rn "_strategist_validation_callback" src/ tests/ scripts/` returns empty.
- [ ] `grep -rn "build_strategist_enricher" src/ tests/ scripts/` returns empty.
- [ ] `grep -rn "agents.strategist.evidence_view" src/ tests/ scripts/` returns empty.
- [ ] `src/agents/strategist/evidence_view.py` does not exist.
- [ ] `docs/contract-invariants.md` contains no mention of
      `_strategist_validation_callback` and no "In-tick callback
      carve-out" clause.
- [ ] `tests/unit/agents/strategist/test_legacy_symbols_gone.py` passes
      (all three import-guard tests green).
- [ ] `tests/integration/test_strategist_enricher_smoke.py` passes.
- [ ] `tests/integration/backtest/test_end_to_end_smoke.py` and
      `tests/integration/backtest/test_fresh_run_starts_clean.py`
      both pass with the migrated `_patched_build_strategist`.
- [ ] Full suite passes: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x`.
- [ ] Ruff clean on `src/agents/strategist/` and the touched test
      directories.
- [ ] All six audit findings ticked off:
      A-023 (callback dead), A-024 (callback test cluster),
      A-025 (evidence_view module), A-026 (evidence_view test cluster),
      A-027 (build_strategist_enricher factory),
      A-052 (invariants-doc carve-out test).
- [ ] Commits are small, sequenced as in Tasks 1-9, and never bundle
      a source deletion with the test deletion that depended on it
      in the wrong order (test migration → source delete → dead-test
      delete → doc retire).

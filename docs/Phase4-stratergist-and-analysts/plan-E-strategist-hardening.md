# Plan E — Strategist Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is self-contained: a fresh subagent should be able to pick it up with only this plan file + the current repo state.

**Goal:** Sharpen the strategist's validation contract and prove its end-to-end behaviour with a real BUY → SELL round-trip integration test. Plan E is **not** a rewrite — it is a focused hardening pass on `src/agents/strategist/agent.py` plus one new integration test.

**Architecture:** Five small, independent edits clustered around `_strategist_validation_callback` and the module-level `strategist_agent` singleton, followed by one new integration test under `tests/integration/`. No new packages, no new ORM rows, no schema changes.

**Tech Stack:** Google ADK `LlmAgent`, Pydantic v2, pytest, ruff. SQLAlchemy 2 only for the integration test fixtures.

**Pre-deployment context:** No live or paper bot is running. Validation tightening can land directly — no migration, no flag.

**Predecessor plans:** Plans A / B / C MUST be merged. Plan D MAY be merged but is not required — none of Plan E's edits collide with Plan D's scope (Plan D touches `src/agents/analysts/*`, the `attribution/` package, and persistence rows; Plan E touches `src/agents/strategist/` and one new integration test).

**Source:** Five follow-ups consolidated from the Phase 4 chunk audits — `FU-01` through `FU-05` in `post-phase4-backlog.md`. They were flagged repeatedly across Chunks 2, 3, and 4 audits as Important but non-blocking; this plan retires that cluster in one pass.

---

## File Structure

**New files (1):**
- `tests/integration/test_strategist_executor_roundtrip.py` — full BUY → SELL trade-log attribution test

**Modified files (3):**
- `src/agents/strategist/agent.py` — validation tightening (FU-02, FU-03), singleton decision (FU-04)
- `src/agents/risk_gate/lifecycle.py` — remove orphaned `validate_lifecycle_contract` (FU-01)
- `src/agents/risk_gate/agent.py` — drop unused import if any references remain (FU-01 fallout)

**Deleted symbols:** `validate_lifecycle_contract` from `risk_gate/lifecycle.py`.

---

## Task E1: Remove orphaned `validate_lifecycle_contract` (FU-01)

**Files:**
- Modify: `src/agents/risk_gate/lifecycle.py`
- Modify: `src/agents/risk_gate/agent.py` *(only if it imports the removed function)*

- [ ] **Step 1: Confirm the function is truly orphaned**

```bash
grep -rn "validate_lifecycle_contract" src/ tests/
```

Expected hits: the definition in `risk_gate/lifecycle.py`, possibly an unused import in `risk_gate/agent.py`, and nothing else. If a real call site appears, **stop** — the function isn't orphaned and this task needs replanning.

- [ ] **Step 2: Delete the definition and any dead import**

Remove `validate_lifecycle_contract` from `src/agents/risk_gate/lifecycle.py`. Any helper functions used *only* by it go too (run the same grep before deleting each helper). Then drop the import line in `risk_gate/agent.py` if one exists.

- [ ] **Step 3: Verify**

```
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check src/agents/risk_gate/
```

Expected: green, clean.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(risk-gate): remove orphaned validate_lifecycle_contract"
```

---

## Task E2: Loud `tick_id` fallback in `_strategist_validation_callback` (FU-02)

**Files:**
- Modify: `src/agents/strategist/agent.py`
- Modify: `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`

**Background:** `_strategist_validation_callback` currently reads `state["tick_id"]` with a silent fallback chain (`state.get("tick_id") or state.get("recorded_at", "unknown")`). When the tick-id seed is missing, the resulting `PositionThesis.opened_tick_id` becomes a timestamp string or the literal `"unknown"`, which then flows through `executor.BUY` → `TradeLogRow.opening_tick_id`. The downstream attribution chain becomes meaningless, but nothing fails. This is a misconfiguration mask — fail loudly instead.

- [ ] **Step 1: Write the failing test**

In `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`, add a test that builds a callback context whose `state` omits both `"tick_id"` and `"recorded_at"`, then asserts the validation callback raises `KeyError` (or a more specific custom exception — pick one and document it).

- [ ] **Step 2: Make the test pass**

In `src/agents/strategist/agent.py`, replace the silent fallback:

```python
# Before
tick_id: str = state.get("tick_id") or state.get("recorded_at", "unknown")

# After
tick_id = state.get("tick_id")
if not tick_id:
    raise KeyError(
        "state['tick_id'] missing — the orchestrator must seed it before "
        "running the strategist (see orchestrator.tick._build_initial_state)."
    )
```

Keep the local variable typed: `tick_id: str = state["tick_id"]` once the guard has run.

- [ ] **Step 3: Verify**

```
.venv/bin/python -m pytest tests/unit/agents/strategist/ -q
```

Expected: green. The orchestrator's `tick.py:_build_initial_state` always seeds `tick_id`, so production paths are unaffected.

- [ ] **Step 4: Commit**

```bash
git commit -m "fix(strategist): raise KeyError on missing tick_id instead of masking with timestamp fallback"
```

---

## Task E3: Duplicate-ticker stance guard (FU-03)

**Files:**
- Modify: `src/agents/strategist/agent.py`
- Modify: `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`

**Background:** The "no off-watchlist tickers" check inside `_strategist_validation_callback` uses a set comprehension over `decision.stances`:

```python
emitted = {s.ticker for s in decision.stances}
extras = [t for t in emitted if t not in tickers]
```

A model that emits two stances for the same ticker passes silently (the set dedupes them). The legacy-field derivation later picks an arbitrary one, which is a real correctness bug.

- [ ] **Step 1: Write the failing test**

Add a test in `test_strategist_callbacks_v2.py` that constructs a `StrategistDecision` with two `TickerStance` entries for the same ticker (both for an in-watchlist symbol) and asserts the validation callback re-prompts the LLM with a clear duplicate-ticker message.

- [ ] **Step 2: Add the guard**

In `_strategist_validation_callback`, insert this check immediately after the existing "missing watchlist tickers" pass and before the "off-watchlist" pass:

```python
# Duplicate-ticker guard: the set comprehension below would mask duplicates.
emitted_list = [s.ticker for s in decision.stances]
duplicates = sorted({t for t in emitted_list if emitted_list.count(t) > 1})
if duplicates:
    return _reprompt(
        f"You emitted multiple stances for the same ticker(s): {duplicates}. "
        f"Emit exactly one TickerStance per watchlist ticker."
    )
```

- [ ] **Step 3: Verify**

```
.venv/bin/python -m pytest tests/unit/agents/strategist/ -q
.venv/bin/python -m ruff check src/agents/strategist/
```

- [ ] **Step 4: Commit**

```bash
git commit -m "fix(strategist): re-prompt on duplicate-ticker stances instead of silently deduping"
```

---

## Task E4: Decide the fate of `strategist_agent` module-level singleton (FU-04)

**Files:**
- Modify: `src/agents/strategist/agent.py`
- Modify: `src/agents/strategist/__init__.py` *(if D9 hasn't already touched it)*
- Modify: `tests/integration/test_strategist_v2_smoke.py` *(may need an import update)*

**Background:** The module-level `strategist_agent = LlmAgent(...)` singleton at `src/agents/strategist/agent.py:302` was preserved through Chunk 3's pipeline rewrite (which now builds the `LlmAgent` inline inside `_build_strategist`). The only thing still keeping the singleton alive is `tests/integration/test_strategist_v2_smoke.py`, which imports it for a real-ADK Runner invocation. Three of the four Phase-4 chunk audits flagged this as ambiguous: leave it, document it, or delete it?

- [ ] **Step 1: Make the call**

Pick one of:

- **(A) Document and keep** — add a module docstring section to `agent.py` explaining the singleton is a public convenience handle for tests and ad-hoc REPL use; the orchestrator does not use it. Lock the model name and callback wiring as the canonical reference.
- **(B) Delete and rewire the smoke** — remove the singleton; have `test_strategist_v2_smoke.py` build its own `LlmAgent` using the same factory the pipeline uses (`_build_strategist` from `src/orchestrator/pipeline.py`). Cleaner separation, more typing.

Choose **(A)** unless a code-quality review of the smoke test post-rewire shows (B) is materially cleaner. (A) is reversible at any time; (B) is the one-way door.

- [ ] **Step 2: Apply the chosen path**

Option A: write the docstring. Option B: extract a `build_strategist_agent(tools=None)` factory from the existing `_build_strategist` so the smoke can call it directly, delete the module-level singleton, update the smoke's import.

- [ ] **Step 3: Verify**

```
.venv/bin/python -m pytest tests/ -q
RUN_LLM_TESTS=1 .venv/bin/python -m pytest tests/integration/test_strategist_v2_smoke.py -v
```
The gated smoke must still collect cleanly (it will skip without `RUN_LLM_TESTS=1`, and fail at credentials with the env var unless creds are wired — neither outcome blocks Plan E).

- [ ] **Step 4: Commit**

```bash
# Option A
git commit -m "docs(strategist): document strategist_agent singleton as public convenience handle"
# Option B
git commit -m "refactor(strategist): extract build_strategist_agent factory; remove module-level singleton"
```

---

## Task E5: BUY → SELL round-trip integration test (FU-05)

**Files:**
- Create: `tests/integration/test_strategist_executor_roundtrip.py`

**Background:** Plan C's per-task unit tests cover each seam individually — `StrategistDecisionWriter` writes `TickerStanceRow`; executor BUY writes `PositionThesis` into state; executor SELL writes `TradeLogRow` with `opening_tick_id` / `closing_tick_id`. Nothing currently exercises the full chain in one run, so a regression in the `opening_tick_id` flow between two real ticks would not be caught.

This test uses the `FakeBroker` (no LLM) and a stub strategist decision injected directly into state, so it is deterministic and runs in CI without gating.

- [ ] **Step 1: Sketch the test shape**

```python
# tests/integration/test_strategist_executor_roundtrip.py
"""End-to-end BUY → SELL trade-log attribution test.

Runs two synthetic ticks against the in-memory pipeline (sans LLM):

Tick 1: stub strategist_decision opens AAPL; assert PositionThesis lands in
        state["positions"] with opened_tick_id="tick_1".
Tick 2: stub strategist_decision closes AAPL; assert TradeLogRow.opening_tick_id
        == "tick_1" and closing_tick_id == "tick_2".

LLM is not invoked — the test injects a pre-built StrategistDecision dict
directly into state, bypassing the strategist LlmAgent.
"""
```

- [ ] **Step 2: Write the test**

Build the two-tick run using:
- `broker.fakes.FakeBroker` with starting cash and AAPL price.
- An `InMemorySessionService` shared across both ticks.
- A pipeline assembled *without* the strategist `LlmAgent` — replace it with a `BaseAgent` shim that writes a pre-built `state["strategist_decision"]` dict.
- `StrategistDecisionWriter` + `RiskGate` + `executor` (real, not stubbed) + `MemoryWriter` + `Snapshotter` — the rest of the chain runs as-is.

Tick 1 decision: one `TickerStance` for AAPL with `preferred_weight=0.5`, `horizon="swing"`, `target_price`, `stop_price`. Executor BUYs.
Tick 2 decision: one `TickerStance` for AAPL with `preferred_weight=0.0`, `close_reason="hit_target"`. Executor SELLs.

Assertions:
1. After tick 1: `state["positions"]["AAPL"]["opened_tick_id"] == "tick_1"`.
2. After tick 2: a `TradeLogRow` exists in the SQLite session with `opening_tick_id == "tick_1"` and `closing_tick_id == "tick_2"`.
3. After tick 2: `state["positions"]` no longer contains AAPL.

- [ ] **Step 3: Verify**

```
.venv/bin/python -m pytest tests/integration/test_strategist_executor_roundtrip.py -v
```

- [ ] **Step 4: Commit**

```bash
git commit -m "test(strategist+executor): add BUY→SELL round-trip with opening/closing tick attribution"
```

---

## Done

After Plan E lands:

- [ ] `validate_lifecycle_contract` no longer exists anywhere in `src/`.
- [ ] `_strategist_validation_callback` raises `KeyError` on missing `tick_id` rather than silently substituting a timestamp.
- [ ] Duplicate-ticker stances trigger an LLM re-prompt instead of being silently deduped.
- [ ] The `strategist_agent` module-level singleton is either explicitly documented (Option A) or removed in favour of a `build_strategist_agent` factory (Option B).
- [ ] `tests/integration/test_strategist_executor_roundtrip.py` exists and exercises the full BUY → SELL chain across two ticks deterministically (no LLM).
- [ ] `pytest tests/` and `ruff check src/ tests/` both pass.

**Next:** The remaining polish-grade items live in `post-phase4-backlog.md`; pick them off opportunistically.

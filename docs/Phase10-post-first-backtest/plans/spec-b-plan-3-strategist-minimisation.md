# Spec B Plan 3 — Strategist Surface Minimisation

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task.  Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the strategist's per-stance output down to the minimum vocabulary needed to drive the executor — `intent` + `weight` + per-position thesis composite — and delete the legacy `preferred_weight` / `conviction` / `close_reason` / `trim_reason` surface that has been living alongside it since Spec B Band 3.  The dual-form schema is the root cause of the schema-retry exhaustion seen on the 2026-05-24 backtest (MSFT / AVGO / LMT bouncing between forms across three retries) — collapsing to a single form removes the ambiguity that produced the failure rather than papering over it with prompt tightening or smart-retry feedback.

**Architecture:** Four bands threaded in dependency order — (1) **derivation rewrite** so the downstream pipeline reads `intent` + `weight` instead of `preferred_weight`, (2) **prompt rewrite** so the LLM is taught a single vocabulary, (3) **schema cut** that deletes the legacy fields and the dual validator now that nothing depends on them, (4) **test migration** mechanically updating fixtures and assertions.  Each band leaves the tree green; you can pause between bands, run a backtest, and confirm before continuing.  No second Pydantic class, no after-callback conversion layer — `TickerStance` itself shrinks down to the canonical shape.

**Tech Stack:** Python 3.14, Pydantic v2, Google ADK `LlmAgent` (the `output_schema` surface), pytest.  All commands run from project root with `PYTHONPATH=src .venv/bin/python …` per `.claude/CLAUDE.md`.

---

## Why this lands now (design rationale)

Three observations from the live system make this refactor cheap and the cost of *not* doing it expensive:

1. **The per-position thesis already exists end-to-end.**  `PositionThesis` at `src/agents/strategist/position_thesis.py` stores the entry commitments (rationale, target_price, stop_price, catalyst, horizon, opened_at, opened_price); the executor's `apply_stance_to_thesis` mutates it on add / trim / hold / update; `render_held_positions_view` (src/agents/strategist/held_view.py) feeds it back into the next-tick prompt.  So `target_price` / `stop_price` / `horizon` / `catalyst` / `rationale` are NOT legacy — they ARE the structured thesis composite the strategist promised on entry.  This plan keeps every one of them.

2. **`preferred_weight` is the only load-bearing legacy field, and its replacement is trivial.**  It is read by `derive_lifecycle_action` (lifecycle.py:32-88) and `derive_legacy_fields` (derivation.py:156-300, lines 232 / 236 / 244).  Both call sites can read `stance.weight` directly once intent becomes the action source — and `derive_lifecycle_action` itself can be deleted, since `stance.intent` *is* the action.  No risk-gate change: the gate operates on the derived `target_weights` dict and never sees the underlying field name.

3. **The dual schema is actively breaking production.**  The 2026-05-24 backtest exhausted three schema retries on a single strategist call because the LLM bounced between forms — first emitting `intent='open'` with `weight=null` (intent-validator failure), then retrying with `preferred_weight=0.05, intent=null, horizon=null` (legacy-validator failure).  Tightening the prompt + smart-retry would mask this; collapsing the schema fixes it.  Cost of waiting: every backtest tick has a non-zero chance of paying the dual-form tax.

The explorer's option (c) — "keep both forms, enforce schema-level exclusivity" — was rejected because it forbids mixing without removing the choice; the LLM still sees both forms in the ADK-generated JSON schema and still has to pick.  Choice is the bug.

---

## End-state contract

After this plan ships, `TickerStance` has exactly these fields:

| Field           | Type                                              | Required when                  |
|-----------------|---------------------------------------------------|--------------------------------|
| `ticker`        | `str`                                             | always                         |
| `intent`        | `Literal["open","add","trim","close","hold","update"]` | always (no longer optional)    |
| `weight`        | `float \| None` ∈ [0,1]                           | open / add / trim              |
| `rationale`     | `str \| None`                                     | open (FROZEN per Invariant 3) |
| `reason`        | `str \| None`                                     | trim / close / hold / update   |
| `horizon`       | `Literal["intraday","swing","long_term"] \| None` | open (+ optional on update)    |
| `target_price`  | `float \| None`                                   | open (+ optional on update)    |
| `stop_price`    | `float \| None`                                   | open (+ optional on update)    |
| `catalyst`      | `str \| None`                                     | optional on open / update      |

Removed: `preferred_weight`, `conviction`, `close_reason`, `trim_reason`.

The `_require_lifecycle_hints_on_nonzero` validator is deleted; `_require_intent_fields` is simplified to enforce only the verb-conditional rules in the table above and to reject `intent is None`.

`derive_lifecycle_action` is deleted entirely; callers read `stance.intent`.  `derive_legacy_fields` is renamed to `derive_decision_fields` and rewritten to read `stance.weight` for `target_weights`, and to populate `close_reasons` / `trim_reasons` from `stance.reason` when `intent in {"close", "trim"}` respectively.  The `DerivedFields` shape on the consumer side is unchanged — risk_gate, executor, and memory_writer still see the same dicts.

---

## Prerequisites

This plan lands AFTER Spec B Plan 1 (memory backbone) and Plan 2 (strategist surface).  At start-of-plan:

- `TickerStance.intent` already accepts the full verb set (Plan 1).
- `state["user:positions"]` is populated and rendered via `held_view.py` (Plan 2).
- `apply_stance_to_thesis` (executor/_verb_dispatch.py) uses `stance.intent` + `stance.weight` as its dispatch keys (already verified by the explorer's survey).
- The dual validators (`_require_lifecycle_hints_on_nonzero` + `_require_intent_fields`) coexist on `TickerStance` — this plan removes the first and simplifies the second.

If any of these are not in place, **stop and verify Plans 1 / 2 have merged**.

Per the auto-memory `feedback_co_planned_specs_trust_each_other`: this plan assumes its siblings have landed and does not add defensive shims for the legacy fields.

---

## File Map

### Modified

| Path                                                  | Why |
|-------------------------------------------------------|-----|
| `src/agents/strategist/stance_schema.py`              | **Band 3** — delete `preferred_weight`, `conviction`, `close_reason`, `trim_reason`; make `intent` non-optional; delete `_require_lifecycle_hints_on_nonzero`; simplify `_require_intent_fields` to enforce only verb-conditional rules from the end-state table; update module docstring |
| `src/agents/strategist/derivation.py`                 | **Band 1** — rewrite `derive_legacy_fields` (rename to `derive_decision_fields`) to read `stance.weight` + `stance.intent` + verb-conditional `stance.reason`; remove the `derive_lifecycle_action` import |
| `src/agents/strategist/lifecycle.py`                  | **Band 1** — delete the file (`derive_lifecycle_action`, `OPEN_EPSILON`, `SIZE_CHANGE_EPSILON`, `LifecycleAction`).  `OPEN_EPSILON` is unused outside lifecycle.py per Band 1 Task 1 verification; if it has surviving consumers they read `ORDER_EPSILON` from `orchestrator.state` instead |
| `src/agents/strategist/prompts.py`                    | **Band 2** — rewrite `_RAW_INSTRUCTION` to teach the LLM one vocabulary: intent + weight + verb-conditional fields per the end-state table.  Drop the `preferred_weight` / `conviction` language entirely.  Drop the "REJECTED — DO NOT EMIT" anti-example and the "NULL DISCIPLINE" section — both exist to defend against the dual-form ambiguity that this plan removes |
| `src/agents/strategist/schema.py`                     | **Band 1** — `StrategistDecision.target_weights` / `close_reasons` / `trim_reasons` keep their derived-field role; the after-callback wires `derive_decision_fields` output through unchanged.  Update the docstring to reference the new derivation function name |
| `src/agents/strategist/agent.py`                      | **Band 1** — line ~187: replace any direct `preferred_weight` reads with the derived `target_weights` lookup (verify on read; the explorer flagged this site).  Update the after-callback's call to use `derive_decision_fields` |
| `src/agents/strategist/decision_writer.py`            | **Band 1** — line 98: rename the column read (`preferred_weight` → `weight`) and write the `intent` enum to the row.  Schema migration handled in Band 1 Task 4 (DB column rename) |
| `src/agents/strategist/risk_gate.py` (or wherever `risk_gate/agent.py` lives) | **Band 1 verification only** — confirm the gate reads `decision.target_weights`, not `stance.preferred_weight`.  No code change expected per the explorer's finding (risk_gate/agent.py:39-80) |
| `src/observability/decision_logger.py`                | **Band 1** — line 272: rewrite the audit-row schema to log `intent` + `weight` + `reason` rather than `close_reason` / `trim_reason` / `preferred_weight`.  Preserves the audit trail in the new vocabulary |
| `tests/unit/agents/strategist/test_stance_schema.py`  | **Band 4** — delete the `_require_lifecycle_hints_on_nonzero` test class; rewrite `_require_intent_fields` tests against the simplified rules; remove all `preferred_weight` / `conviction` / `close_reason` / `trim_reason` references |
| `tests/unit/agents/strategist/test_derivation.py`     | **Band 4** — rename to match `derive_decision_fields`; update fixtures to use intent+weight; assert `close_reasons` populated from `reason` on `intent=="close"` |
| `tests/unit/agents/strategist/test_lifecycle.py`      | **Band 4** — delete the file (the function is gone) |
| `tests/**/*.py`                                       | **Band 4** — mechanical sweep (~22 files, ~80-100 edits) to update fixtures from `preferred_weight=X, conviction=Y, ...` to `intent="open", weight=X, ...` per the end-state table.  Inventory at the bottom of this plan |

### Created

| Path                                                              | Responsibility |
|-------------------------------------------------------------------|----------------|
| `tests/unit/agents/strategist/test_derivation_intent_path.py`     | Band 1 unit tests for the rewritten derivation — verifies `target_weights` reads `stance.weight`, `close_reasons` populates from `stance.reason` when `intent=="close"`, `trim_reasons` populates from `stance.reason` when `intent=="trim"`, and the held-coverage invariant (Plan 2 / D3) still raises |
| `tests/integration/test_strategist_minimal_schema_no_retry.py`    | Band 4 cross-cutting integration test — runs the strategist against a stub LLM that returns a single intent-form decision; asserts zero schema retries on `llm_retry` instrumentation and that the executor's `verb_dispatch` accepts the output unmodified |

### Deleted

| Path                                                      | Why |
|-----------------------------------------------------------|-----|
| `src/agents/strategist/lifecycle.py`                      | Band 1 — `derive_lifecycle_action` is dead code once intent is canonical |
| `tests/unit/agents/strategist/test_lifecycle.py`          | Band 4 — file under test is gone |

---

## Implementation Order

Four bands, each leaves the tree green and the backtest runnable:

- **Band 1 — Derivation rewrite** (Tasks 1–4): teach the downstream pipeline to consume intent+weight while the legacy fields remain on the model.  The LLM is still emitting both forms at end-of-band; `derive_decision_fields` reads only `intent` + `weight`, falling back to NotImplemented (raise) if `intent is None` so silent drift is impossible.  Backtest at end-of-band confirms the live LLM is naturally emitting intent on every stance (Spec B's existing prompt already biases toward it, even though it documents both forms).
- **Band 2 — Prompt rewrite** (Task 5): switch the prompt vocabulary to intent-only.  After this band the LLM is no longer being asked to emit `preferred_weight` / `conviction` / `close_reason` / `trim_reason` — but the schema still accepts them, so a stray emission does not fail.  Backtest at end-of-band confirms zero legacy-field emissions over 20+ ticks.
- **Band 3 — Schema cut** (Tasks 6–7): delete the legacy fields, the dual validator, and the dead `lifecycle.py`.  Make `intent` non-optional.  Simplify `_require_intent_fields`.  This is the breaking change — anything still reading the legacy fields will explode on import.  Band 1's audit ensures no such caller survives.
- **Band 4 — Test migration** (Tasks 8–10): mechanical sweep of test fixtures and assertions; add the new derivation tests and the no-retry integration test; delete the lifecycle tests.

Bands 1 and 2 are independent of each other; either order works, but Band 1 first lets us prove the derivation rewrite is correct against the live LLM before touching the prompt.  Bands 3 and 4 must follow Bands 1 and 2 in that order.

---

## Band 1 — Derivation rewrite

### Task 1 — Audit and delete `lifecycle.py`

**Files:**
- Modify: `src/agents/strategist/derivation.py`
- Delete:  `src/agents/strategist/lifecycle.py`

- [ ] **Step 1: Inventory `derive_lifecycle_action` call sites.**

```bash
grep -rn "derive_lifecycle_action\|OPEN_EPSILON\|SIZE_CHANGE_EPSILON\|LifecycleAction" src/ tests/ scripts/
```

Expected sites (from the explorer survey): `src/agents/strategist/derivation.py:28` (import), `:236` (call).  If anything else surfaces — especially in the executor or risk_gate — STOP and update this plan before proceeding.

- [ ] **Step 2: Remove the import and the call from `derivation.py`.**

Replace the body of `derive_legacy_fields`'s per-stance loop (lines ~227-260) so that:

- `target_weights[stance.ticker] = stance.weight` (NOT `stance.preferred_weight`).
- `action = stance.intent` (NOT a derived value).
- Add an explicit `if action is None: raise StrategistContractViolation(...)` guard at the top of the loop — silent failure is the recurring bug class (per the auto-memory `feedback_silent_failures_loud_tests`).
- `if action == "close": close_reasons[stance.ticker] = stance.reason` (with a non-empty check).
- `elif action == "trim": trim_reasons[stance.ticker] = stance.reason` (with a non-empty check).
- `decision_tags[stance.ticker] = derive_decision_tag(prior=current, new=stance.weight or 0.0)` — the `or 0.0` handles `weight=None` for close/hold/update.

Keep `derive_decision_tag` as-is — it operates on a (prior, new) weight pair and remains useful.

- [ ] **Step 3: Delete `src/agents/strategist/lifecycle.py`.**

```bash
git rm src/agents/strategist/lifecycle.py
```

- [ ] **Step 4: Verify nothing imports the deleted module.**

```bash
.venv/bin/python -c "import agents.strategist.derivation; import agents.strategist.agent; import agents.strategist.decision_writer"
.venv/bin/python -m ruff check src/agents/strategist/
```

Both must pass clean.

### Task 2 — Rewrite `derive_legacy_fields` → `derive_decision_fields`

**Files:**
- Modify: `src/agents/strategist/derivation.py`
- Create: `tests/unit/agents/strategist/test_derivation_intent_path.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/agents/strategist/test_derivation_intent_path.py` covering:

```python
"""Band 1 — derive_decision_fields reads intent+weight, not preferred_weight."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.strategist.derivation import (
    StrategistContractViolation,
    TickContext,
    derive_decision_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(current_weights=None, watchlist=("AAPL", "MSFT")) -> TickContext:
    """Build a minimal TickContext for derivation tests."""
    return TickContext(
        tick_id="tick_001",
        decision_tag="test",
        now=datetime(2026, 5, 24, 14, 0, tzinfo=UTC),
        current_weights=current_weights or {},
        watchlist=list(watchlist),
    )


class TestTargetWeightsReadIntentPath:
    """target_weights must populate from stance.weight, never preferred_weight."""

    def test_open_stance_populates_target_weight_from_weight_field(self):
        # AVGO is flat; strategist opens at 5%.
        stances = [TickerStance(
            ticker="AVGO", intent="open", weight=0.05,
            rationale="Strong setup", horizon="swing",
            target_price=2100.0, stop_price=1800.0,
        )]
        ctx = _ctx(watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == 0.05

    def test_close_stance_populates_target_weight_zero(self):
        stances = [TickerStance(ticker="AVGO", intent="close", reason="thesis broke")]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == 0.0


class TestCloseReasonFromIntent:
    """close_reasons populates from stance.reason when intent=='close'."""

    def test_close_with_reason_populates_close_reasons(self):
        stances = [TickerStance(
            ticker="AVGO", intent="close", reason="guidance cut invalidates thesis",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.close_reasons["AVGO"] == "guidance cut invalidates thesis"

    def test_close_without_reason_raises(self):
        # Silent failures are the recurring bug class — raise loud.
        stances = [TickerStance(ticker="AVGO", intent="close", reason=None)]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        with pytest.raises(StrategistContractViolation, match="reason"):
            derive_decision_fields(stances, ctx)


class TestTrimReasonFromIntent:
    """trim_reasons populates from stance.reason when intent=='trim'."""

    def test_trim_with_reason_populates_trim_reasons(self):
        stances = [TickerStance(
            ticker="AVGO", intent="trim", weight=0.02,
            reason="taking partial profits at 50% to target",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.trim_reasons["AVGO"] == "taking partial profits at 50% to target"


class TestIntentNonNullEnforced:
    """A stance with intent=None must raise — no silent legacy-path fallback."""

    def test_intent_none_raises_contract_violation(self):
        stances = [TickerStance(ticker="AVGO", intent=None, weight=0.05)]
        ctx = _ctx(watchlist=("AVGO",))
        with pytest.raises(StrategistContractViolation, match="intent"):
            derive_decision_fields(stances, ctx)


class TestHeldCoverageInvariantPreserved:
    """The Plan 2 / D3 invariant — held tickers MUST have a stance — still raises."""

    def test_uncovered_held_ticker_raises(self):
        stances = []  # Strategist returned nothing
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        with pytest.raises(StrategistContractViolation, match="AVGO"):
            derive_decision_fields(stances, ctx)
```

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_derivation_intent_path.py -v
```

All seven tests must FAIL on the un-rewritten derivation (RED).

- [ ] **Step 2: Rewrite `derive_legacy_fields` per the contract.**

In `src/agents/strategist/derivation.py`:

1. Rename `derive_legacy_fields` → `derive_decision_fields` (update all imports — `git grep -l derive_legacy_fields` and patch each site).
2. Rewrite the Pass-1 loop per Task 1 Step 2.
3. Add the `if stance.intent is None: raise` guard at the top of the loop.
4. Add the `if intent == "close" and not stance.reason: raise` guard inline.
5. Update the docstring to describe the new contract (intent-driven, reason carries the close/trim narrative).
6. Update the module docstring header — `derive_legacy_fields` is no longer the name; the function is now part of the canonical pipeline, not a legacy compat shim.

Run the failing test from Step 1 — all seven must now PASS (GREEN).

- [ ] **Step 3: Run the full strategist test suite.**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/ -v
```

Expect some failures — Band 4 will mop them up.  Confirm the failures are all in the form "stance fixtures use `preferred_weight`" (i.e. fixture churn, not logic regressions).  If any failure looks like a logic regression, STOP and diagnose.

### Task 3 — Update direct readers of legacy fields in `src/`

**Files:**
- Modify: `src/agents/strategist/agent.py`
- Modify: `src/agents/strategist/decision_writer.py`
- Modify: `src/observability/decision_logger.py`
- Modify: anywhere else the audit from Task 1 Step 1 surfaced

- [ ] **Step 1: Audit remaining `src/` readers.**

```bash
grep -rn "preferred_weight\|\.close_reason\|\.trim_reason\|\.conviction" src/
```

For each hit, classify:
- **Stance attribute access** (`stance.preferred_weight`, `stance.close_reason`, etc.) — must be replaced.
- **Decision attribute access** (`decision.target_weights`, `decision.close_reasons`, etc.) — derivation output; unchanged.
- **DB column / row class** (`TickerStanceRow.preferred_weight`) — rename the column; schema migration in Step 3 below.

- [ ] **Step 2: Patch each src/ reader.**

For each stance-attribute reader:
- `stance.preferred_weight` → `stance.weight or 0.0` (the `or 0.0` handles close/hold/update where weight is None).
- `stance.close_reason` → `stance.reason if stance.intent == "close" else None`.
- `stance.trim_reason`  → `stance.reason if stance.intent == "trim" else None`.
- `stance.conviction`   → delete the read; if it was being logged, replace with the decision-level `confidence` field.

Add a one-line comment at each replacement site explaining the field move, per the user-global "comment the code" preference.

- [ ] **Step 3: Database column rename.**

`TickerStanceRow` in `decision_writer.py` has a `preferred_weight` column.  Rename it to `weight` and add an `intent` TEXT column.  Per `feedback_destructive_ops_require_explicit_go`: this requires explicit user go-ahead before running migration.  **Generate the migration SQL, show the diff to the user, and wait for "go" before applying.**

The backtest cache databases will need wiping or migrating — flag this to the user, do not auto-delete.

- [ ] **Step 4: Verify.**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ -v -k "strategist or decision"
```

Strategist-suite failures down to "test fixture uses legacy field" (Band 4 territory) — no logic regressions.

### Task 4 — End-of-band live-backtest verification

- [ ] **Step 1: Run a short backtest.**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window <small-window>
```

Pick the smallest window available (one or two ticks).  Watch the strategist's stance output in the decision-log JSON for:

- Every stance has `intent` populated (not None).
- `weight` is set on every open / add / trim.
- `reason` is set on every close / trim / hold / update.

If `intent is None` on any stance: STOP.  The prompt is still steering the LLM toward the legacy form; do not proceed to Band 2 yet — investigate why.

- [ ] **Step 2: Confirm zero schema retries.**

`grep llm_retry_attempt logs/` (or wherever the backtest writes logs) on the strategist agent for the run.  Zero attempts = clean signal.  Any retry = investigate the validation error; it points to a gap in the prompt or the derivation.

---

## Band 2 — Prompt rewrite

### Task 5 — Rewrite `prompts.py` for the intent-only vocabulary

**Files:**
- Modify: `src/agents/strategist/prompts.py`

- [ ] **Step 1: Replace the OUTPUT CONTRACT section.**

The current `_RAW_INSTRUCTION` carries a fork in its documentation: the action table at lines 137-144 describes the dual `preferred_weight` / `intent` derivation; the NULL DISCIPLINE section at 146-167 defends against the dual-form ambiguity; the REJECTED — DO NOT EMIT anti-example at 209-218 calls out the most common decision-killer.  All three are scaffolding for a problem this plan removes.

Rewrite the section so the LLM sees a single contract:

```
## OUTPUT CONTRACT — every rule is enforced; violations abort the tick

Each stance carries an `intent` verb and the fields required for that verb.
The table below is the single source of truth.

| Intent  | What it means                          | Required fields                                                          |
|---------|----------------------------------------|--------------------------------------------------------------------------|
| open    | enter a flat ticker (current weight 0) | weight, rationale, horizon, target_price, stop_price (catalyst optional) |
| add     | grow an existing position              | weight, reason (+ optional horizon/target_price/stop_price/catalyst update) |
| trim    | reduce an existing position (not to 0) | weight, reason                                                           |
| close   | exit an existing position completely   | reason                                                                   |
| hold    | no trade — review only                 | reason (what has changed since open)                                     |
| update  | no trade — revise the thesis           | reason + at least one of target_price / stop_price / horizon / catalyst  |

Schema-level rules (failing these means ADK rejects your response):
- weight: float in [0, 1].  Long-only — 0.0 not permitted (use intent="close" instead).
  The risk gate clamps single-ticker weight at {{MAX_POSITION_PCT}}%, per-tick delta
  at {{MAX_DELTA_PCT}}%, and total turnover at {{MAX_TURNOVER_PCT}}%.  Propose
  values that already respect these.
  {{CASH_FLOOR_STANZA}}
- horizon: one of "intraday", "swing", "long_term".
- rationale: ≤{{STANCE_RATIONALE_MAX}} chars.  FROZEN at open — you cannot change it later.
- reason / catalyst: ≤{{STANCE_RATIONALE_MAX}} chars each.
- Off-watchlist tickers are rejected.

## Two worked examples

OPEN (currently flat, opening at 0.05):
{{"ticker": "XYZ", "intent": "open", "weight": 0.05,
"rationale": "Strong fundamentals, bullish technical setup",
"horizon": "swing", "target_price": 215.0, "stop_price": 180.0,
"catalyst": "earnings beat expected next week"}}

CLOSE (held at 0.05, exiting):
{{"ticker": "XYZ", "intent": "close",
"reason": "guidance cut invalidates thesis"}}
```

Delete the REJECTED — DO NOT EMIT block.  Delete the NULL DISCIPLINE block.  Both exist to defend against an ambiguity the new schema does not have.

- [ ] **Step 2: Delete the `decision-level thesis` line if it was carrying the dual-form vocabulary.**

Read lines 188-189 currently:

```
- thesis (decision-level, optional — null carries the prior thesis forward): ≤{{DECISION_THESIS_MAX}} chars.
```

Keep this line — the decision-level thesis is independent of the per-stance simplification.

- [ ] **Step 3: Verify the build-time substitution still works.**

```bash
PYTHONPATH=src .venv/bin/python -c "from agents.strategist.prompts import STRATEGIST_INSTRUCTION; print(STRATEGIST_INSTRUCTION[:500])"
```

The output should start with the new contract section; the `{{NAME}}` markers (MAX_POSITION_PCT etc.) should be resolved; the runtime `{portfolio}` / `{tickers}` placeholders should survive untouched.

- [ ] **Step 4: End-of-band backtest.**

Same as Task 4 — run a short backtest, confirm intent populated on every stance, confirm zero schema retries.

---

## Band 3 — Schema cut

### Task 6 — Delete legacy fields from `TickerStance`

**Files:**
- Modify: `src/agents/strategist/stance_schema.py`

- [ ] **Step 1: Update the failing tests first.**

In `tests/unit/agents/strategist/test_stance_schema.py`:

1. Delete every test in the `_require_lifecycle_hints_on_nonzero` test class.
2. Rewrite `_require_intent_fields` tests against the simplified rules — see Band 4 Task 9.
3. Add a test asserting `TickerStance(ticker="X", intent=None)` raises a `ValidationError` (intent is now non-optional).
4. Add a test asserting `TickerStance(...)` rejects `preferred_weight=`, `conviction=`, `close_reason=`, `trim_reason=` keyword args (`extra=forbid` or model-validator).

Run; expect new tests to FAIL.

- [ ] **Step 2: Cut the legacy fields.**

In `stance_schema.py`:

1. Delete `preferred_weight: float = Field(...)` (line 109).
2. Delete `conviction: float = Field(...)` (line 112).
3. Delete `close_reason: str | None = Field(...)` (line 159).
4. Delete `trim_reason:  str | None = Field(...)` (line 160).
5. Change `intent: Literal[...] | None = Field(None, ...)` to `intent: Literal[...]` (no default — now required).
6. Delete the `_require_lifecycle_hints_on_nonzero` validator entirely (lines 171-229).
7. Simplify `_require_intent_fields` (lines 231-359):
   - Remove every conditional that checks `self.intent is None`.
   - Remove every check that references `self.preferred_weight`, `self.conviction`, `self.close_reason`, `self.trim_reason`.
   - Reduce to the verb-conditional rules from the end-state table:
     - `open` → require `weight, rationale, horizon, target_price, stop_price`.
     - `add` → require `weight, reason`.
     - `trim` → require `weight, reason`.
     - `close` → require `reason`; forbid `weight` (or accept and ignore — pick one and write a comment).
     - `hold` → require `reason`; forbid `weight`.
     - `update` → require `reason` AND at least one of `target_price / stop_price / horizon / catalyst`; forbid `weight`.
8. Update the module docstring to describe the single canonical form.
9. Add `model_config = ConfigDict(extra="forbid")` if not already present — guarantees the test from Step 1.4 passes.

Run the test suite from Step 1; all must now PASS.

### Task 7 — Strategist contract integration test

**Files:**
- Modify: any contract test that asserts the dual-form is accepted (find with `grep -rn "preferred_weight" tests/contract/`)
- Create: integration coverage in Band 4 Task 10

- [ ] **Step 1: Find dual-form contract tests and rewrite them.**

```bash
grep -rln "preferred_weight\|conviction\|close_reason\|trim_reason" tests/contract/ 2>/dev/null
```

For each file, rewrite the fixture to the intent form; delete tests that exist solely to test the legacy path.

- [ ] **Step 2: Verify schema rejection on stray legacy field.**

Add to `tests/unit/agents/strategist/test_stance_schema.py`:

```python
def test_legacy_preferred_weight_kwarg_rejected():
    """Band 3 — the legacy field is gone; passing it must raise."""
    with pytest.raises(ValidationError):
        TickerStance(ticker="X", intent="open", weight=0.05, preferred_weight=0.05)
```

(Plus equivalents for `conviction`, `close_reason`, `trim_reason`.)

---

## Band 4 — Test migration

### Task 8 — Mechanical fixture sweep

**Files:**
- Modify: ~22 test files across `tests/`.  Inventory below.

- [ ] **Step 1: Generate the per-file diff.**

```bash
grep -rln "preferred_weight\|conviction\|close_reason\|trim_reason" tests/
```

Cross-check against the explorer's estimate (~22 files, ~80-100 references).  If the count is materially higher, STOP and reassess — there may be readers in `src/` that Band 1 missed.

- [ ] **Step 2: Per-file rewrite.**

For each fixture that constructs a `TickerStance(...)`:

| Old kwarg                              | New kwarg                                          |
|----------------------------------------|----------------------------------------------------|
| `preferred_weight=0.05, conviction=0.7` | `intent="open", weight=0.05` (+ rationale/horizon/target/stop on open) |
| `preferred_weight=0.0` (close)         | `intent="close", reason="..."`                     |
| `preferred_weight=X, close_reason="Y"` | `intent="close", reason="Y"`                       |
| `preferred_weight=X, trim_reason="Y"`  | `intent="trim", weight=X, reason="Y"`              |

For fixtures that assert on `decision.target_weights` / `decision.close_reasons` / `decision.trim_reasons` — these shapes are unchanged; no edit needed.

- [ ] **Step 3: Full suite green.**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/ scripts/
```

Both must pass clean.  Per `feedback_silent_failures_loud_tests`: a test that passes by skipping is not a passing test — verify the count of `collected` items has not dropped vs the pre-band baseline.

### Task 9 — Update `test_stance_schema.py` for the simplified validator

**Files:**
- Modify: `tests/unit/agents/strategist/test_stance_schema.py`

(See Band 3 Task 6 Step 1 — most of this is already specified there.)  This task is the catch-up sweep for anything that was missed.

### Task 10 — End-to-end "no schema retries" integration test

**Files:**
- Create: `tests/integration/test_strategist_minimal_schema_no_retry.py`

- [ ] **Step 1: Write the test.**

```python
"""Band 4 — end-to-end: the simplified schema fixes the dual-form retry storm.

The 2026-05-24 backtest exhausted three schema retries on MSFT / AVGO / LMT
because the LLM bounced between legacy and intent forms.  This test runs the
strategist against a stub LLM that emits a single clean intent-form decision
and asserts:
  1. Zero schema retries on the llm_retry counter.
  2. The decision passes through verb_dispatch unmodified.
  3. derive_decision_fields produces the expected target_weights / close_reasons.
"""
```

Pattern after `tests/integration/test_strategist_retry_smoke.py` (the existing schema-retry smoke test) — use the same stub LLM scaffolding, just feed it an intent-form payload and assert zero retries.

- [ ] **Step 2: Run.**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_strategist_minimal_schema_no_retry.py -v
```

Must pass.

---

## Acceptance criteria

This plan is done when ALL of the following are true:

1. `grep -rn "preferred_weight\|\.conviction\|\.close_reason\|\.trim_reason" src/ tests/ scripts/` returns zero matches (excluding the migration SQL and this plan file itself).
2. `src/agents/strategist/lifecycle.py` does not exist.
3. `TickerStance` has the field set listed in the End-state contract section above, with `intent` non-optional.
4. `_require_lifecycle_hints_on_nonzero` does not exist; `_require_intent_fields` enforces only the verb-conditional rules from the End-state contract.
5. `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` passes with at least the same number of `collected` items as before the plan started (i.e. no silent skips).
6. `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/ scripts/` is clean.
7. A live backtest of ≥20 ticks completes with **zero `llm_retry_attempt class=schema` events** on the strategist agent.
8. Decision-log JSONs for that backtest show every stance with `intent` populated and (where applicable) `weight` / `reason` / `rationale` / `horizon` / `target_price` / `stop_price` populated per the End-state contract.
9. The graphify delta is updated per `.claude/CLAUDE.md` — append a dated entry to `graphify-out/graph_delta.md` describing the symbol deletions and the `derive_legacy_fields` → `derive_decision_fields` rename.

---

## Out of scope (followups)

These ideas surfaced during the design conversation but are deferred:

- **Smart-retry with ValidationError injection** — the user approved injecting validation errors into the next prompt during the conversation that produced this plan.  That work is now lower-priority because the dual-form ambiguity (the actual cause of the retry storm) is gone, but the smart-retry would still be a quality improvement for other LLM agents (analysts) and for any future schema-evolution friction.  Propose as a separate plan under Phase 10 when bandwidth allows.
- **Free-text per-stance thesis field** — the user considered this during the conversation but chose to keep the structured composite (rationale + target + stop + horizon + catalyst) because it feeds the held_view evolution rendering ("you said target $X, current is $Y").  Revisit only if the structured form proves too rigid in live trading.
- **`weight=0.0` semantics on close** — the simplified validator on `close` rejects `weight`.  An alternative is to accept `weight=0.0` as a redundant-but-consistent declaration.  Pick one in Band 3 Task 6 Step 2; document the choice; don't churn back and forth.

---

## Test-file inventory (Band 4 reference)

The explorer found ~22 test files referencing legacy fields.  Run this command to regenerate the list before starting Band 4 — names may have shifted between plan-drafting and plan-execution:

```bash
grep -rl "preferred_weight\|\.conviction\b\|\.close_reason\b\|\.trim_reason\b" tests/ | sort
```

Mechanical edits dominate.  If any file requires non-trivial test-logic rewrites (i.e. a test exists to verify the dual-form path specifically), STOP and add a task to this plan describing what it covers and how the new contract preserves the underlying behaviour.

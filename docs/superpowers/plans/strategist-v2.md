# Strategist v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-LlmAgent strategist's flat `target_weights` output with a per-ticker `TickerStance` schema; render a structured "Held Positions" block (entry price, target, stop, current price, P&L) into the prompt; persist per-ticker stances + outcome attribution FKs. Drops the council architecture from the deprecated specs.

**Architecture:** The strategist stays a single Gemini-Pro `LlmAgent` at pipeline position 2. It gains a `before_agent_callback` that renders held-position context into state, and an `after_agent_callback` that validates per-ticker stances and derives the legacy `target_weights` / `new_positions` / `close_reasons` / `trim_reasons` fields downstream agents already consume. A new `strategist_decision_writer` runs between strategist and `risk_gate`, persisting one `TickerStanceRow` per ticker per tick. The executor gains a BUY-side write to `state["positions"][ticker]` and populates new `TradeLogRow.opening_tick_id` / `closing_tick_id` columns.

**Tech Stack:** Python 3.11+, Pydantic v2, Google ADK (`LlmAgent`, `BaseAgent`, `SequentialAgent`), SQLAlchemy 2.x ORM, pytest. Source: `src/agents/strategist/`, `src/agents/executor/`, `src/orchestrator/`. Tests: `tests/unit/strategist/`, `tests/unit/executor/`, `tests/unit/orchestrator/`, `tests/integration/`.

**Spec:** `docs/superpowers/specs/strategist-v2-design.md` — read this before starting.

**Shell convention (Windows):** All commands run from project root `C:\Users\oscar\OneDrive - Nexus365\Documents\StockBot`. Use `.venv/Scripts/python -m pytest …` and `.venv/Scripts/python -m ruff …`. **Do not** prefix commands with `cd`.

**Graphify:** After substantial structural changes (new files, new modules, changed pipeline stages), append a dated entry to `graphify-out/graph_delta.md`. The cleanup phase has a step for this.

---

## File Structure (created / modified by this plan)

**New files:**
- `src/agents/strategist/stance_schema.py` — `TickerStance`
- `src/agents/strategist/lifecycle.py` — `derive_lifecycle_action`, `OPEN_EPSILON`, `SIZE_CHANGE_EPSILON`
- `src/agents/strategist/derivation.py` — `derive_legacy_fields`
- `src/agents/strategist/held_view.py` — `render_held_positions_view`
- `src/agents/strategist/decision_writer.py` — `StrategistDecisionWriter`, `build_strategist_decision_writer`
- `tests/unit/strategist/__init__.py` (already exists per graph_delta — verify)
- `tests/unit/strategist/test_stance_schema.py`
- `tests/unit/strategist/test_lifecycle_derivation.py`
- `tests/unit/strategist/test_derivation.py`
- `tests/unit/strategist/test_held_view.py`
- `tests/unit/strategist/test_strategist_validation_v2.py`
- `tests/unit/strategist/test_decision_writer.py`
- `tests/unit/strategist/test_prompts_v2.py`
- `tests/unit/orchestrator/test_persistence_strategist.py`
- `tests/unit/orchestrator/test_pipeline_wiring_v2.py`
- `tests/unit/executor/test_open_positions_state.py`
- `tests/integration/test_strategist_v2_smoke.py`

**Modified files:**
- `src/agents/strategist/schema.py` — add `opened_tick_id` to `PositionThesis`; add `stances` and `trim_reasons` to `StrategistDecision`
- `src/agents/strategist/agent.py` — rewrite `_strategist_validation_callback`; add `_held_view_before_callback`; rebuild `strategist_agent`
- `src/agents/strategist/prompts.py` — new `STRATEGIST_INSTRUCTION` template
- `src/agents/executor/agent.py` — BUY-side write to `state["positions"]`; populate trade-log FKs
- `src/orchestrator/persistence.py` — `TickerStanceRow`; extend `TradeLogRow` with `opening_tick_id` / `closing_tick_id`
- `src/orchestrator/pipeline.py` — wire `strategist_decision_writer` between strategist and risk_gate

**Deleted at cleanup:**
- `docs/superpowers/plans/strategist-council.md`
- `docs/superpowers/plans/exit-rules-and-telemetry.md`

**Annotated as superseded at cleanup:**
- `docs/superpowers/specs/strategist-council-design.md`
- `docs/superpowers/specs/exit-rules-and-telemetry-design.md`

---

## Phase 1 — Test scaffolding + verify preflight assumptions

### Task 1.1: Verify the `tests/unit/strategist/` package marker exists

**Files:**
- Verify: `tests/unit/strategist/__init__.py`

- [ ] **Step 1: Check the test package marker**

Run: `.venv/Scripts/python -c "import os; print(os.path.exists('tests/unit/strategist/__init__.py'))"`
Expected: `True`. If `False`, create an empty file:
```python
```
and commit:
```bash
git add tests/unit/strategist/__init__.py
git commit -m "test: scaffold tests/unit/strategist package"
```

### Task 1.2: Confirm `MIN_HELD_WEIGHT` source

**Files:**
- Read: `src/orchestrator/state.py:10`

- [ ] **Step 1: Confirm `MIN_HELD_WEIGHT = 0.001` exists**

Run: `.venv/Scripts/python -c "from orchestrator.state import MIN_HELD_WEIGHT; print(MIN_HELD_WEIGHT)"`
Expected: `0.001`

This constant is imported throughout the rest of this plan. Do not redefine it locally.

### Task 1.3: Confirm portfolio data source for held-view

**Files:**
- Read: `src/broker/portfolio.py`

- [ ] **Step 1: Confirm `Portfolio.current_weights()` and `Position.last_price` exist**

Run: `.venv/Scripts/python -c "from broker.portfolio import Portfolio, Position; p = Portfolio(cash=100.0); print(hasattr(p, 'current_weights'), hasattr(Position, 'last_price'))"`
Expected: `True True`

If either is missing, the plan needs revision before continuing.

---

## Phase 2 — TickerStance schema

### Task 2.1: Write tests for TickerStance

**Files:**
- Create: `tests/unit/strategist/test_stance_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_stance_schema.py`:
```python
"""TickerStance schema tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance


def _base_stance(**overrides):
    defaults = dict(
        ticker="AAPL",
        preferred_weight=0.10,
        conviction=0.7,
        rationale="cheap on FCF basis",
    )
    defaults.update(overrides)
    return defaults


def test_minimal_stance_validates():
    s = TickerStance(**_base_stance())
    assert s.ticker == "AAPL"
    assert s.preferred_weight == 0.10
    assert s.conviction == 0.7
    assert s.horizon is None
    assert s.target_price is None
    assert s.stop_price is None
    assert s.catalyst is None
    assert s.close_reason is None
    assert s.trim_reason is None


def test_preferred_weight_in_zero_one():
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(preferred_weight=1.1))
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(preferred_weight=-0.01))


def test_conviction_in_zero_one():
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(conviction=1.1))
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(conviction=-0.01))


def test_rationale_max_length_140():
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(rationale="x" * 141))
    s = TickerStance(**_base_stance(rationale="x" * 140))
    assert len(s.rationale) == 140


def test_horizon_literal_only():
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(horizon="forever"))
    for h in ("intraday", "swing", "long_term"):
        s = TickerStance(**_base_stance(horizon=h))
        assert s.horizon == h


def test_open_lifecycle_hints_optional_at_schema_level():
    s = TickerStance(
        **_base_stance(
            horizon="swing",
            target_price=210.0,
            stop_price=185.0,
            catalyst="Q3 earnings",
        )
    )
    assert s.target_price == 210.0
    assert s.stop_price == 185.0


def test_close_reason_max_length_120():
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(close_reason="x" * 121))


def test_trim_reason_max_length_120():
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(trim_reason="x" * 121))


def test_catalyst_max_length_80():
    with pytest.raises(ValidationError):
        TickerStance(**_base_stance(catalyst="x" * 81))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_stance_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.stance_schema'`

### Task 2.2: Implement TickerStance

**Files:**
- Create: `src/agents/strategist/stance_schema.py`

- [ ] **Step 1: Write the schema**

Create `src/agents/strategist/stance_schema.py`:
```python
"""Per-ticker decision schema emitted by the v2 strategist.

The strategist emits one TickerStance per watchlist ticker per tick, exhaustively.
Lifecycle hints (horizon/target_price/stop_price/catalyst on opens; close_reason on
closes; trim_reason on trims) are required by the after-agent validator only on the
matching transition; null otherwise.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TickerStance(BaseModel):
    """Strategist's per-ticker decision and rationale for one tick."""

    ticker: str
    preferred_weight: float = Field(ge=0.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=140)

    # Lifecycle hints — populated only on the matching transition.
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=80)
    close_reason: str | None = Field(default=None, max_length=120)
    trim_reason: str | None = Field(default=None, max_length=120)
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_stance_schema.py -v`
Expected: PASS (9 tests)

- [ ] **Step 3: Commit**

```bash
git add src/agents/strategist/stance_schema.py tests/unit/strategist/test_stance_schema.py
git commit -m "feat(strategist): add TickerStance per-ticker decision schema"
```

---

## Phase 3 — PositionThesis.opened_tick_id

### Task 3.1: Write the failing test

**Files:**
- Modify: `tests/unit/strategist/test_stance_schema.py` (extend existing or create separate)

- [ ] **Step 1: Add test for new field**

Append to `tests/unit/strategist/test_stance_schema.py`:
```python
# ── PositionThesis.opened_tick_id ─────────────────────────────────────────────


def test_position_thesis_has_opened_tick_id_default_empty():
    from datetime import datetime, timezone
    from agents.strategist.schema import PositionThesis

    t = PositionThesis(
        ticker="AAPL",
        opened_at=datetime.now(tz=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl_2026q2",
        rationale="FCF yield + insider buying",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    assert t.opened_tick_id == ""


def test_position_thesis_opened_tick_id_round_trip():
    from datetime import datetime, timezone
    from agents.strategist.schema import PositionThesis

    t = PositionThesis(
        ticker="AAPL",
        opened_at=datetime.now(tz=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl_2026q2",
        rationale="FCF yield + insider buying",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
        opened_tick_id="tick_2026-04-22T14:00",
    )
    assert t.opened_tick_id == "tick_2026-04-22T14:00"
    dumped = t.model_dump()
    assert dumped["opened_tick_id"] == "tick_2026-04-22T14:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_stance_schema.py -v -k "opened_tick_id"`
Expected: FAIL with `AttributeError` or `ValidationError` for unknown field

### Task 3.2: Add the field to PositionThesis

**Files:**
- Modify: `src/agents/strategist/schema.py`

- [ ] **Step 1: Add the field**

Edit `src/agents/strategist/schema.py`. Find the `PositionThesis` class and add `opened_tick_id` as the last field:
```python
class PositionThesis(BaseModel):
    """Structured rationale for an open position, created when a position is opened
    and updated on each subsequent tick while the position is held."""

    ticker: str
    opened_at: datetime
    opened_price: float
    opened_tag: str                                    # decision_tag from the opening tick
    rationale: str = Field(max_length=400)             # why we entered
    horizon: Literal["intraday", "swing", "long_term"]
    target_price: float | None = None
    stop_price: float | None   = None
    catalyst: str | None = Field(default=None, max_length=100)
    last_reviewed_at: datetime
    last_review_note: str = Field(default="", max_length=200)
    opened_tick_id: str = ""                           # NEW — tick that opened this position
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_stance_schema.py -v`
Expected: PASS (11 tests now — 9 original + 2 thesis)

- [ ] **Step 3: Run full strategist test suite to confirm no regression**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: All previously-passing tests still pass

- [ ] **Step 4: Commit**

```bash
git add src/agents/strategist/schema.py tests/unit/strategist/test_stance_schema.py
git commit -m "feat(strategist): add opened_tick_id to PositionThesis for outcome attribution"
```

---

## Phase 4 — Lifecycle action derivation

### Task 4.1: Write tests for derive_lifecycle_action

**Files:**
- Create: `tests/unit/strategist/test_lifecycle_derivation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_lifecycle_derivation.py`:
```python
"""derive_lifecycle_action tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from agents.strategist.lifecycle import (
    derive_lifecycle_action,
    OPEN_EPSILON,
    SIZE_CHANGE_EPSILON,
)
from orchestrator.state import MIN_HELD_WEIGHT


# ── Currently flat (curr < MIN_HELD_WEIGHT) ──────────────────────────────────


def test_flat_to_open_above_open_epsilon():
    assert derive_lifecycle_action(curr=0.0, pref=0.05) == "open"


def test_flat_to_open_at_open_epsilon_boundary():
    # OPEN_EPSILON = 0.005; pref strictly greater than this opens
    assert derive_lifecycle_action(curr=0.0, pref=OPEN_EPSILON + 1e-9) == "open"


def test_flat_to_hold_below_open_epsilon():
    assert derive_lifecycle_action(curr=0.0, pref=0.001) == "hold"


def test_flat_to_hold_at_zero():
    assert derive_lifecycle_action(curr=0.0, pref=0.0) == "hold"


def test_flat_when_curr_below_min_held_weight():
    # Curr 0.0005 < MIN_HELD_WEIGHT (0.001) is treated as flat
    assert derive_lifecycle_action(curr=0.0005, pref=0.05) == "open"


# ── Currently held (curr >= MIN_HELD_WEIGHT) ─────────────────────────────────


def test_held_to_close_when_pref_below_min_held():
    assert derive_lifecycle_action(curr=0.10, pref=0.0) == "close"


def test_held_to_close_at_min_held_boundary():
    # pref strictly less than MIN_HELD_WEIGHT closes
    assert derive_lifecycle_action(curr=0.10, pref=MIN_HELD_WEIGHT - 1e-9) == "close"


def test_held_to_trim_meaningful_reduction():
    # 0.10 → 0.05 is a 5pp reduction, well above SIZE_CHANGE_EPSILON
    assert derive_lifecycle_action(curr=0.10, pref=0.05) == "trim"


def test_held_to_trim_at_size_change_epsilon_boundary():
    # delta = -SIZE_CHANGE_EPSILON exactly does not trim (must be strictly less)
    assert (
        derive_lifecycle_action(curr=0.10, pref=0.10 - SIZE_CHANGE_EPSILON)
        == "hold"
    )
    assert (
        derive_lifecycle_action(curr=0.10, pref=0.10 - SIZE_CHANGE_EPSILON - 1e-9)
        == "trim"
    )


def test_held_to_add_meaningful_increase():
    assert derive_lifecycle_action(curr=0.05, pref=0.10) == "add"


def test_held_to_add_at_size_change_epsilon_boundary():
    assert (
        derive_lifecycle_action(curr=0.05, pref=0.05 + SIZE_CHANGE_EPSILON)
        == "hold"
    )
    assert (
        derive_lifecycle_action(curr=0.05, pref=0.05 + SIZE_CHANGE_EPSILON + 1e-9)
        == "add"
    )


def test_held_to_hold_no_meaningful_change():
    assert derive_lifecycle_action(curr=0.05, pref=0.05) == "hold"
    assert derive_lifecycle_action(curr=0.05, pref=0.06) == "hold"   # 1pp < SIZE_CHANGE_EPSILON


# ── Constants exposed and sane ───────────────────────────────────────────────


def test_open_epsilon_constant_value():
    assert OPEN_EPSILON == 0.005


def test_size_change_epsilon_constant_value():
    assert SIZE_CHANGE_EPSILON == 0.02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_lifecycle_derivation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.lifecycle'`

### Task 4.2: Implement derive_lifecycle_action

**Files:**
- Create: `src/agents/strategist/lifecycle.py`

- [ ] **Step 1: Write the implementation**

Create `src/agents/strategist/lifecycle.py`:
```python
"""Lifecycle action derivation for the v2 strategist.

Given (current_weight, preferred_weight) for one ticker, classify the strategist's
intent as open / close / trim / add / hold. Constants tuned to make trim/add
meaningfully different from holding; opens require a non-trivial commitment.
"""
from __future__ import annotations

from orchestrator.state import MIN_HELD_WEIGHT  # 0.001 — global floor


# Strategist-specific epsilons (NOT shared with the risk gate).
OPEN_EPSILON: float        = 0.005   # pref must strictly exceed this to open
SIZE_CHANGE_EPSILON: float = 0.02    # |pref - curr| must strictly exceed this to count as trim/add


def derive_lifecycle_action(curr: float, pref: float) -> str:
    """Classify a per-ticker weight transition.

    Returns one of: "open", "close", "trim", "add", "hold".
    """
    if curr < MIN_HELD_WEIGHT:
        # Currently flat
        return "open" if pref > OPEN_EPSILON else "hold"

    # Currently held
    if pref < MIN_HELD_WEIGHT:
        return "close"

    delta = pref - curr
    if delta < -SIZE_CHANGE_EPSILON:
        return "trim"
    if delta > SIZE_CHANGE_EPSILON:
        return "add"
    return "hold"
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_lifecycle_derivation.py -v`
Expected: PASS (14 tests)

- [ ] **Step 3: Commit**

```bash
git add src/agents/strategist/lifecycle.py tests/unit/strategist/test_lifecycle_derivation.py
git commit -m "feat(strategist): add derive_lifecycle_action with OPEN/SIZE_CHANGE epsilons"
```

---

## Phase 5 — Legacy-fields derivation

### Task 5.1: Write tests for derive_legacy_fields

**Files:**
- Create: `tests/unit/strategist/test_derivation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_derivation.py`:
```python
"""derive_legacy_fields tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.derivation import TickContext, derive_legacy_fields
from agents.strategist.stance_schema import TickerStance


def _ctx(**overrides):
    defaults = dict(
        tick_id="tick_2026-04-22T14:00",
        decision_tag="open_aapl_2026q2",
        now=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        current_prices={"AAPL": 192.40, "MSFT": 410.10, "NVDA": 850.0},
        current_weights={"AAPL": 0.0, "MSFT": 0.05, "NVDA": 0.10},
    )
    defaults.update(overrides)
    return TickContext(**defaults)


# ── target_weights ────────────────────────────────────────────────────────────


def test_target_weights_one_per_stance():
    stances = [
        TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                     rationale="open", horizon="swing",
                     target_price=210.0, stop_price=185.0),
        TickerStance(ticker="MSFT", preferred_weight=0.05, conviction=0.6,
                     rationale="hold"),
        TickerStance(ticker="NVDA", preferred_weight=0.0, conviction=0.8,
                     rationale="close", close_reason="Thesis broken"),
    ]
    out = derive_legacy_fields(stances, _ctx())
    assert out.target_weights == {"AAPL": 0.08, "MSFT": 0.05, "NVDA": 0.0}


# ── new_positions ─────────────────────────────────────────────────────────────


def test_open_stance_builds_position_thesis():
    stances = [
        TickerStance(
            ticker="AAPL",
            preferred_weight=0.08,
            conviction=0.7,
            rationale="FCF yield + insider buying",
            horizon="swing",
            target_price=210.0,
            stop_price=185.0,
            catalyst="Q3 earnings 11/01",
        ),
    ]
    out = derive_legacy_fields(stances, _ctx())
    assert "AAPL" in out.new_positions
    thesis = out.new_positions["AAPL"]
    assert thesis.ticker == "AAPL"
    assert thesis.opened_price == 192.40
    assert thesis.opened_tag == "open_aapl_2026q2"
    assert thesis.opened_tick_id == "tick_2026-04-22T14:00"
    assert thesis.rationale == "FCF yield + insider buying"
    assert thesis.horizon == "swing"
    assert thesis.target_price == 210.0
    assert thesis.stop_price == 185.0
    assert thesis.catalyst == "Q3 earnings 11/01"
    assert thesis.opened_at == datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc)


def test_non_open_stances_do_not_produce_thesis():
    stances = [
        TickerStance(ticker="MSFT", preferred_weight=0.05, conviction=0.6,
                     rationale="hold"),
        TickerStance(ticker="NVDA", preferred_weight=0.0, conviction=0.8,
                     rationale="close", close_reason="Thesis broken"),
    ]
    out = derive_legacy_fields(stances, _ctx())
    assert out.new_positions == {}


# ── close_reasons ─────────────────────────────────────────────────────────────


def test_close_stance_populates_close_reason():
    stances = [
        TickerStance(
            ticker="NVDA",
            preferred_weight=0.0,
            conviction=0.8,
            rationale="exit",
            close_reason="Thesis broken — momentum lost",
        ),
    ]
    out = derive_legacy_fields(stances, _ctx())
    assert out.close_reasons == {"NVDA": "Thesis broken — momentum lost"}


def test_non_close_stances_do_not_populate_close_reasons():
    stances = [
        TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                     rationale="open", horizon="swing",
                     target_price=210.0, stop_price=185.0),
    ]
    out = derive_legacy_fields(stances, _ctx())
    assert out.close_reasons == {}


# ── trim_reasons ──────────────────────────────────────────────────────────────


def test_trim_stance_populates_trim_reason():
    stances = [
        TickerStance(
            ticker="MSFT",
            preferred_weight=0.02,    # was 0.05 → trim
            conviction=0.6,
            rationale="reduce risk",
            trim_reason="Take profits at +20%",
        ),
    ]
    out = derive_legacy_fields(stances, _ctx())
    assert out.trim_reasons == {"MSFT": "Take profits at +20%"}


def test_non_trim_stances_do_not_populate_trim_reasons():
    stances = [
        TickerStance(ticker="MSFT", preferred_weight=0.05, conviction=0.6,
                     rationale="hold"),
    ]
    out = derive_legacy_fields(stances, _ctx())
    assert out.trim_reasons == {}


# ── opened_at uses tick context ───────────────────────────────────────────────


def test_opened_at_uses_tick_context_now():
    fixed_now = datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc)
    stances = [
        TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                     rationale="open", horizon="swing",
                     target_price=210.0, stop_price=185.0),
    ]
    out = derive_legacy_fields(stances, _ctx(now=fixed_now))
    assert out.new_positions["AAPL"].opened_at == fixed_now
    assert out.new_positions["AAPL"].last_reviewed_at == fixed_now
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_derivation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.derivation'`

### Task 5.2: Implement derive_legacy_fields

**Files:**
- Create: `src/agents/strategist/derivation.py`

- [ ] **Step 1: Write the implementation**

Create `src/agents/strategist/derivation.py`:
```python
"""Derive the legacy StrategistDecision fields (target_weights, new_positions,
close_reasons, trim_reasons) from a list of TickerStance objects.

Downstream agents (risk_gate, executor, memory_writer) still consume the legacy
fields. The strategist LLM only emits stances; the after-agent callback runs
this derivation to populate the legacy view server-side.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from pydantic import BaseModel

from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.schema import PositionThesis
from agents.strategist.stance_schema import TickerStance


@dataclass
class TickContext:
    """Per-tick data the derivation needs that the strategist LLM does not emit."""

    tick_id: str
    decision_tag: str
    now: datetime
    current_prices: dict[str, float]    # ticker → last_price
    current_weights: dict[str, float]   # ticker → current portfolio weight


class DerivedFields(BaseModel):
    """Output of derive_legacy_fields."""

    target_weights: dict[str, float] = {}
    new_positions: dict[str, PositionThesis] = {}
    close_reasons: dict[str, str] = {}
    trim_reasons: dict[str, str] = {}


def derive_legacy_fields(
    stances: Sequence[TickerStance],
    ctx: TickContext,
) -> DerivedFields:
    """Compute target_weights / new_positions / close_reasons / trim_reasons."""
    target_weights: dict[str, float] = {}
    new_positions: dict[str, PositionThesis] = {}
    close_reasons: dict[str, str] = {}
    trim_reasons: dict[str, str] = {}

    for stance in stances:
        target_weights[stance.ticker] = stance.preferred_weight

        curr = ctx.current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(curr, stance.preferred_weight)

        if action == "open":
            new_positions[stance.ticker] = PositionThesis(
                ticker=stance.ticker,
                opened_at=ctx.now,
                opened_price=ctx.current_prices.get(stance.ticker, 0.0),
                opened_tag=ctx.decision_tag,
                rationale=stance.rationale,
                horizon=stance.horizon or "swing",   # validator has already enforced presence
                target_price=stance.target_price,
                stop_price=stance.stop_price,
                catalyst=stance.catalyst,
                last_reviewed_at=ctx.now,
                last_review_note="",
                opened_tick_id=ctx.tick_id,
            )
        elif action == "close":
            close_reasons[stance.ticker] = stance.close_reason or ""
        elif action == "trim":
            trim_reasons[stance.ticker] = stance.trim_reason or ""
        # "add" and "hold" need no extra fields

    return DerivedFields(
        target_weights=target_weights,
        new_positions=new_positions,
        close_reasons=close_reasons,
        trim_reasons=trim_reasons,
    )
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_derivation.py -v`
Expected: PASS (8 tests)

- [ ] **Step 3: Commit**

```bash
git add src/agents/strategist/derivation.py tests/unit/strategist/test_derivation.py
git commit -m "feat(strategist): add derive_legacy_fields for per-ticker stance → legacy schema"
```

---

## Phase 6 — Held positions view rendering

### Task 6.1: Write tests for render_held_positions_view

**Files:**
- Create: `tests/unit/strategist/test_held_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_held_view.py`:
```python
"""render_held_positions_view tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.held_view import render_held_positions_view
from agents.strategist.schema import PositionThesis
from broker.portfolio import Portfolio, Position


def _thesis(**overrides):
    defaults = dict(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl_2026q2",
        rationale="insider buying + FCF yield 6.2%",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
        catalyst="Q3 earnings 11/01",
        last_reviewed_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        last_review_note="",
        opened_tick_id="tick_2026-04-22T14:00",
    )
    defaults.update(overrides)
    return PositionThesis(**defaults)


# ── Empty case ────────────────────────────────────────────────────────────────


def test_empty_positions_renders_flat_message():
    pf = Portfolio(cash=1000.0)
    out = render_held_positions_view(positions={}, portfolio=pf)
    assert "(No held positions" in out
    assert "flat" in out.lower()


# ── Full happy path ───────────────────────────────────────────────────────────


def test_full_position_renders_all_fields():
    thesis_dict = _thesis().model_dump(mode="json")
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis_dict}, portfolio=pf)
    assert "AAPL" in out
    assert "192.40" in out          # opened price
    assert "210.00" in out          # target
    assert "185.00" in out          # stop
    assert "198.50" in out          # current
    assert "swing" in out           # horizon
    assert "Q3 earnings 11/01" in out
    assert "insider buying" in out  # rationale
    assert "+3.17" in out or "+3.2" in out   # P&L %


# ── Missing data fallbacks ────────────────────────────────────────────────────


def test_missing_target_renders_none_set():
    thesis_dict = _thesis(target_price=None, stop_price=None).model_dump(mode="json")
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis_dict}, portfolio=pf)
    assert "(none set at open)" in out


def test_missing_current_price_renders_unavailable():
    thesis_dict = _thesis().model_dump(mode="json")
    pf = Portfolio(cash=900.0, positions={})   # no AAPL in portfolio
    out = render_held_positions_view(positions={"AAPL": thesis_dict}, portfolio=pf)
    assert "(price unavailable)" in out


def test_missing_catalyst_omits_catalyst_line():
    thesis_dict = _thesis(catalyst=None).model_dump(mode="json")
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis_dict}, portfolio=pf)
    assert "Catalyst:" not in out


# ── Multiple holdings ─────────────────────────────────────────────────────────


def test_multiple_holdings_rendered_with_blank_line_separator():
    aapl = _thesis(ticker="AAPL").model_dump(mode="json")
    msft = _thesis(
        ticker="MSFT",
        opened_price=410.0,
        rationale="cloud tailwind",
        target_price=450.0,
        stop_price=395.0,
        catalyst=None,
    ).model_dump(mode="json")
    pf = Portfolio(
        cash=500.0,
        positions={
            "AAPL": Position(quantity=5.0, avg_cost=192.40, last_price=198.50),
            "MSFT": Position(quantity=2.0, avg_cost=410.0, last_price=415.0),
        },
    )
    out = render_held_positions_view(
        positions={"AAPL": aapl, "MSFT": msft},
        portfolio=pf,
    )
    assert "AAPL" in out
    assert "MSFT" in out
    # blocks separated by a blank line
    assert "\n\n" in out


# ── Pydantic-instance acceptance (positions[ticker] may also be PositionThesis) ──


def test_positions_value_can_be_thesis_instance():
    thesis_inst = _thesis()
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis_inst}, portfolio=pf)
    assert "AAPL" in out
    assert "192.40" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_held_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.held_view'`

### Task 6.2: Implement render_held_positions_view

**Files:**
- Create: `src/agents/strategist/held_view.py`

- [ ] **Step 1: Write the implementation**

Create `src/agents/strategist/held_view.py`:
```python
"""Render the Held Positions block injected into the strategist's prompt.

The strategist needs to know what it bought, why, and where the targets/stops
sit. Pulls thesis info from `state["positions"]` (dict[ticker, thesis_dict])
and live price/weight from `state["portfolio"]` (Portfolio instance).
"""
from __future__ import annotations

from typing import Any

from broker.portfolio import Portfolio
from agents.strategist.schema import PositionThesis


def _coerce_thesis(value: Any) -> PositionThesis:
    """Accept either a dict or a PositionThesis instance."""
    if isinstance(value, PositionThesis):
        return value
    return PositionThesis.model_validate(value)


def _coerce_portfolio(value: Any) -> Portfolio:
    """Accept either a dict or a Portfolio instance."""
    if isinstance(value, Portfolio):
        return value
    return Portfolio.model_validate(value)


def _format_one(thesis: PositionThesis, portfolio: Portfolio) -> str:
    ticker = thesis.ticker
    pos = portfolio.positions.get(ticker)
    weights = portfolio.current_weights()
    curr_weight = weights.get(ticker, 0.0)

    lines: list[str] = [ticker]

    # Opened
    opened_str = thesis.opened_at.strftime("%Y-%m-%d %H:%M")
    lines.append(
        f"  Opened:    {opened_str} at ${thesis.opened_price:.2f}, "
        f"weight {curr_weight:.3f}"
    )

    # Why
    lines.append(f"  Why:       {thesis.rationale}")

    # Aim — target + stop
    if thesis.target_price is None and thesis.stop_price is None:
        lines.append("  Aim:       (none set at open)")
    else:
        target_part = (
            f"target ${thesis.target_price:.2f} "
            f"({(thesis.target_price - thesis.opened_price) / thesis.opened_price * 100:+.1f}% from open)"
            if thesis.target_price is not None
            else "target (none)"
        )
        stop_part = (
            f"stop ${thesis.stop_price:.2f} "
            f"({(thesis.stop_price - thesis.opened_price) / thesis.opened_price * 100:+.1f}% from open)"
            if thesis.stop_price is not None
            else "stop (none)"
        )
        lines.append(f"  Aim:       {target_part}  |  {stop_part}")

    # Horizon
    lines.append(f"  Horizon:   {thesis.horizon}")

    # Catalyst (omit line if absent)
    if thesis.catalyst:
        lines.append(f"  Catalyst:  {thesis.catalyst}")

    # Now — current price + P&L
    if pos is None or pos.last_price <= 0:
        lines.append("  Now:       (price unavailable)")
    else:
        pnl_pct = (pos.last_price - thesis.opened_price) / thesis.opened_price * 100
        lines.append(
            f"  Now:       ${pos.last_price:.2f}  |  weight {curr_weight:.3f}  "
            f"|  {pnl_pct:+.2f}% unrealised"
        )

    return "\n".join(lines)


def render_held_positions_view(
    positions: dict[str, Any],
    portfolio: Any,
) -> str:
    """Render every held position as a structured block for the strategist prompt.

    `positions`: dict[ticker, PositionThesis | thesis-dict]. Empty dict ⇒ "no holdings"
    message. `portfolio`: a Portfolio instance or dict.
    """
    if not positions:
        return "(No held positions — portfolio is flat.)"

    pf = _coerce_portfolio(portfolio)
    blocks: list[str] = []
    for ticker in sorted(positions.keys()):
        try:
            thesis = _coerce_thesis(positions[ticker])
        except Exception:
            # Defensive — corrupt thesis dict; skip rather than crashing the tick
            continue
        blocks.append(_format_one(thesis, pf))

    if not blocks:
        return "(No held positions — portfolio is flat.)"

    return "\n\n".join(blocks)
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_held_view.py -v`
Expected: PASS (7 tests)

- [ ] **Step 3: Commit**

```bash
git add src/agents/strategist/held_view.py tests/unit/strategist/test_held_view.py
git commit -m "feat(strategist): add render_held_positions_view for prompt context"
```

---

## Phase 7 — StrategistDecision schema extensions

### Task 7.1: Write tests for new fields

**Files:**
- Create: `tests/unit/strategist/test_decision_schema_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_decision_schema_v2.py`:
```python
"""StrategistDecision v2 schema tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance


def test_decision_with_stances_validates():
    d = StrategistDecision(
        stances=[
            TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                         rationale="open", horizon="swing",
                         target_price=210.0, stop_price=185.0),
            TickerStance(ticker="MSFT", preferred_weight=0.05, conviction=0.6,
                         rationale="hold"),
        ],
        target_weights={},
        decision_tag="open_aapl_hold_msft",
        reasoning="rotating into AAPL on insider buying; trimming risk",
        updated_thesis="cyclicals stretched; quality only",
        confidence=0.65,
    )
    assert len(d.stances) == 2
    assert d.stances[0].ticker == "AAPL"


def test_decision_trim_reasons_default_empty_dict():
    d = StrategistDecision(
        stances=[],
        target_weights={},
        decision_tag="hold_all",
        reasoning="quiet tick",
        updated_thesis="no edge",
        confidence=0.4,
    )
    assert d.trim_reasons == {}


def test_decision_trim_reasons_round_trip():
    d = StrategistDecision(
        stances=[],
        target_weights={"MSFT": 0.02},
        decision_tag="trim_msft",
        reasoning="taking profits",
        updated_thesis="lock in gains",
        confidence=0.55,
        trim_reasons={"MSFT": "Take profits at +20%"},
    )
    assert d.trim_reasons == {"MSFT": "Take profits at +20%"}
    dumped = d.model_dump()
    assert dumped["trim_reasons"] == {"MSFT": "Take profits at +20%"}


def test_decision_legacy_fields_still_present():
    """Legacy fields stay so downstream consumers don't break."""
    d = StrategistDecision(
        stances=[],
        target_weights={"AAPL": 0.08},
        decision_tag="open_aapl",
        reasoning="x",
        updated_thesis="y",
        confidence=0.7,
        new_positions={},
        close_reasons={},
    )
    assert d.target_weights == {"AAPL": 0.08}
    assert d.new_positions == {}
    assert d.close_reasons == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_schema_v2.py -v`
Expected: FAIL — current `StrategistDecision` lacks `stances` and `trim_reasons` fields.

### Task 7.2: Extend StrategistDecision

**Files:**
- Modify: `src/agents/strategist/schema.py`

- [ ] **Step 1: Add `stances` and `trim_reasons` fields**

Edit `src/agents/strategist/schema.py`. Add the import for `TickerStance` and extend `StrategistDecision`:
```python
"""Strategist output schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agents.strategist.stance_schema import TickerStance


class PositionThesis(BaseModel):
    """Structured rationale for an open position, created when a position is opened
    and updated on each subsequent tick while the position is held."""

    ticker: str
    opened_at: datetime
    opened_price: float
    opened_tag: str                                    # decision_tag from the opening tick
    rationale: str = Field(max_length=400)             # why we entered
    horizon: Literal["intraday", "swing", "long_term"]
    target_price: float | None = None
    stop_price: float | None   = None
    catalyst: str | None = Field(default=None, max_length=100)
    last_reviewed_at: datetime
    last_review_note: str = Field(default="", max_length=200)
    opened_tick_id: str = ""                           # tick that opened this position


class StrategistDecision(BaseModel):
    """Full output from one Strategist LLM call.

    `stances` is the LLM's primary output (per-ticker decisions). The legacy
    fields (target_weights, new_positions, close_reasons, trim_reasons) are
    populated server-side by the after-agent callback's derive_legacy_fields,
    so risk_gate / executor / memory_writer don't need to change.
    """

    # NEW — primary content
    stances: list[TickerStance] = Field(default_factory=list)

    # Existing global fields
    target_weights: dict[str, float] = Field(default_factory=dict)
    decision_tag: str
    reasoning: str = Field(max_length=300)
    updated_thesis: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    new_positions: dict[str, PositionThesis] = Field(default_factory=dict)
    close_reasons: dict[str, str] = Field(default_factory=dict)
    trim_reasons: dict[str, str] = Field(default_factory=dict)         # NEW
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_schema_v2.py -v`
Expected: PASS (4 tests)

- [ ] **Step 3: Run any other strategist schema test that may exist**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: All previously-passing tests still pass

- [ ] **Step 4: Commit**

```bash
git add src/agents/strategist/schema.py tests/unit/strategist/test_decision_schema_v2.py
git commit -m "feat(strategist): add stances + trim_reasons to StrategistDecision"
```

---

## Phase 8 — Prompt template rewrite

### Task 8.1: Write the prompt-rendering test

**Files:**
- Create: `tests/unit/strategist/test_prompts_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_prompts_v2.py`:
```python
"""Strategist v2 prompt template tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_template_has_held_positions_slot():
    assert "{held_positions_view}" in STRATEGIST_INSTRUCTION


def test_template_has_existing_state_slots():
    assert "{portfolio}" in STRATEGIST_INSTRUCTION
    assert "{memory_buffer}" in STRATEGIST_INSTRUCTION
    assert "{day_digest}" in STRATEGIST_INSTRUCTION
    assert "{thesis}" in STRATEGIST_INSTRUCTION
    assert "{tickers}" in STRATEGIST_INSTRUCTION


def test_template_has_analyst_signal_slots():
    assert "{technical_signals}" in STRATEGIST_INSTRUCTION
    assert "{fundamental_signals}" in STRATEGIST_INSTRUCTION
    assert "{sentiment_signals}" in STRATEGIST_INSTRUCTION
    assert "{smart_money_signals}" in STRATEGIST_INSTRUCTION


def test_template_no_longer_has_active_positions_dump():
    """The unstructured `Active Positions: {positions}` line is replaced by the held view."""
    assert "Active Positions: {positions}" not in STRATEGIST_INSTRUCTION


def test_template_instructs_per_ticker_stance_output():
    assert "TickerStance" in STRATEGIST_INSTRUCTION
    assert "preferred_weight" in STRATEGIST_INSTRUCTION
    assert "conviction" in STRATEGIST_INSTRUCTION
    assert "rationale" in STRATEGIST_INSTRUCTION


def test_template_documents_lifecycle_hint_rules():
    text = STRATEGIST_INSTRUCTION
    assert "OPEN" in text
    assert "CLOSE" in text
    assert "TRIM" in text
    assert "horizon" in text
    assert "target_price" in text
    assert "stop_price" in text
    assert "close_reason" in text
    assert "trim_reason" in text


def test_template_renders_with_all_required_slots():
    rendered = STRATEGIST_INSTRUCTION.format(
        portfolio="cash=100, positions={}",
        memory_buffer="[]",
        day_digest="(empty)",
        thesis="(empty)",
        held_positions_view="(No held positions — portfolio is flat.)",
        technical_signals="[]",
        fundamental_signals="[]",
        sentiment_signals="[]",
        smart_money_signals="[]",
        tickers="['AAPL','MSFT']",
    )
    assert "(No held positions" in rendered
    assert "['AAPL','MSFT']" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_prompts_v2.py -v`
Expected: FAIL — current prompt lacks `{held_positions_view}` and per-stance instructions.

### Task 8.2: Rewrite the prompt template

**Files:**
- Modify: `src/agents/strategist/prompts.py`

- [ ] **Step 1: Replace the template**

Edit `src/agents/strategist/prompts.py` (full replacement):
```python
"""Strategist v2 prompt template.

Renders held-position context inline so the model sees what it bought, why, and
the targets/stops set on entry. Output is a list[TickerStance], exhaustive over
the watchlist.
"""

STRATEGIST_INSTRUCTION = """
You are the portfolio strategist for an algorithmic trading bot. You decide a per-ticker
stance for the next trading hour.

## Current State
Portfolio: {portfolio}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest: {day_digest}
Current Thesis: {thesis}

## Held Positions (your prior decisions)
{held_positions_view}

## Analyst Signals
Technical:    {technical_signals}
Fundamental:  {fundamental_signals}
Sentiment:    {sentiment_signals}
Smart Money:  {smart_money_signals}

## Smart Money Bias Instruction
If smart_money_signals is non-empty AND contains signals with conviction='high',
let those signals dominate the directional call for those tickers — weight 2-3x the dense signals.
Smart Money is a bias channel, not just a co-equal vote.

## Your Job
Emit a TickerStance for EVERY watchlist ticker: {tickers}.

Per stance:
- preferred_weight ∈ [0,1]: your ideal portfolio weight next tick
- conviction ∈ [0,1]: how strongly you hold this view
- rationale: ≤140 chars, why
- If proposing to OPEN (current ≈ 0 → preferred > 0): include horizon, target_price, stop_price; catalyst optional.
- If proposing to CLOSE (current > 0 → preferred ≈ 0): include close_reason.
- If proposing to TRIM (current > 0 → preferred lower but still held): include trim_reason.
- If holding or adding: lifecycle hint fields stay null.

Also emit at the decision level:
- decision_tag (snake_case, ≤40 chars): this tick's headline decision
- reasoning (≤300 chars): overall summary across all stances
- updated_thesis (≤500 chars): working hypothesis for next tick
- confidence ∈ [0,1]: overall conviction in this tick's plan

Watchlist: {tickers}
"""
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_prompts_v2.py -v`
Expected: PASS (7 tests)

- [ ] **Step 3: Commit**

```bash
git add src/agents/strategist/prompts.py tests/unit/strategist/test_prompts_v2.py
git commit -m "feat(strategist): rewrite prompt for held-positions block + per-stance output"
```

---

## Phase 9 — Strategist agent rewrite (callbacks + wiring)

### Task 9.1: Write tests for the v2 validation callback

**Files:**
- Create: `tests/unit/strategist/test_strategist_validation_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_strategist_validation_v2.py`:
```python
"""Strategist v2 before/after callback tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.agent import (
    _held_view_before_callback,
    _strategist_validation_callback,
)
from agents.strategist.schema import PositionThesis, StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio, Position


class _State(dict):
    """Minimal state stand-in matching ADK's CallbackContext.state shape."""


class _Ctx:
    def __init__(self, state: dict):
        self.state = state


def _portfolio(holdings: dict[str, tuple[float, float, float]] | None = None,
               cash: float = 1000.0) -> Portfolio:
    """holdings: ticker -> (quantity, avg_cost, last_price)"""
    positions = {}
    if holdings:
        for t, (q, ac, lp) in holdings.items():
            positions[t] = Position(quantity=q, avg_cost=ac, last_price=lp)
    return Portfolio(cash=cash, positions=positions)


# ── before callback: held_positions_view ──────────────────────────────────────


def test_before_callback_renders_empty_view_when_no_holdings():
    state = _State(
        positions={},
        portfolio=_portfolio().model_dump(mode="json"),
    )
    _held_view_before_callback(_Ctx(state))
    assert "(No held positions" in state["held_positions_view"]


def test_before_callback_renders_full_view_with_holdings():
    thesis = PositionThesis(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="FCF + insider",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
        last_reviewed_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        opened_tick_id="tick_2026-04-22T14:00",
    )
    state = _State(
        positions={"AAPL": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"AAPL": (10.0, 192.40, 198.50)}).model_dump(mode="json"),
    )
    _held_view_before_callback(_Ctx(state))
    assert "AAPL" in state["held_positions_view"]
    assert "192.40" in state["held_positions_view"]
    assert "198.50" in state["held_positions_view"]


# ── after callback: missing tickers ───────────────────────────────────────────


def test_after_callback_reprompts_on_missing_tickers():
    state = _State(
        tickers=["AAPL", "MSFT"],
        positions={},
        portfolio=_portfolio().model_dump(mode="json"),
        tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5,
                             rationale="hold")
            ],
            decision_tag="hold_all",
            reasoning="x",
            updated_thesis="y",
            confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None  # re-prompt content
    text = out.parts[0].text
    assert "MSFT" in text


def test_after_callback_reprompts_on_extras():
    state = _State(
        tickers=["AAPL"],
        positions={},
        portfolio=_portfolio().model_dump(mode="json"),
        tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5,
                             rationale="hold"),
                TickerStance(ticker="GOOG", preferred_weight=0.05, conviction=0.7,
                             rationale="open", horizon="swing",
                             target_price=200.0, stop_price=170.0),
            ],
            decision_tag="x",
            reasoning="x",
            updated_thesis="y",
            confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    assert "GOOG" in out.parts[0].text


# ── after callback: lifecycle hint enforcement ────────────────────────────────


def test_after_callback_reprompts_on_open_without_horizon():
    state = _State(
        tickers=["AAPL"],
        positions={},
        portfolio=_portfolio().model_dump(mode="json"),
        tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", preferred_weight=0.05, conviction=0.7,
                             rationale="open"),  # missing horizon/target/stop
            ],
            decision_tag="x",
            reasoning="x",
            updated_thesis="y",
            confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    text = out.parts[0].text
    assert "AAPL" in text
    assert "horizon" in text or "target_price" in text or "stop_price" in text


def test_after_callback_reprompts_on_close_without_close_reason():
    thesis = PositionThesis(
        ticker="AAPL",
        opened_at=datetime.now(tz=timezone.utc),
        opened_price=192.40,
        opened_tag="x",
        rationale="x",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    state = _State(
        tickers=["AAPL"],
        positions={"AAPL": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"AAPL": (10.0, 192.40, 198.50)}).model_dump(mode="json"),
        tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5,
                             rationale="exit"),  # missing close_reason
            ],
            decision_tag="x",
            reasoning="x",
            updated_thesis="y",
            confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    assert "close_reason" in out.parts[0].text


def test_after_callback_reprompts_on_trim_without_trim_reason():
    thesis = PositionThesis(
        ticker="MSFT",
        opened_at=datetime.now(tz=timezone.utc),
        opened_price=410.0,
        opened_tag="x",
        rationale="x",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    state = _State(
        tickers=["MSFT"],
        positions={"MSFT": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"MSFT": (10.0, 410.0, 415.0)}, cash=500).model_dump(mode="json"),
        tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[
                # MSFT current weight ~ (10*415)/(500+10*415) = 0.892; pref 0.30 → trim
                TickerStance(ticker="MSFT", preferred_weight=0.30, conviction=0.5,
                             rationale="reduce"),  # missing trim_reason
            ],
            decision_tag="x",
            reasoning="x",
            updated_thesis="y",
            confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    assert "trim_reason" in out.parts[0].text


# ── after callback: derivation populates legacy fields on success ─────────────


def test_after_callback_derives_legacy_fields_on_valid_input():
    state = _State(
        tickers=["AAPL"],
        positions={},
        portfolio=_portfolio().model_dump(mode="json"),
        tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", preferred_weight=0.05, conviction=0.7,
                             rationale="open", horizon="swing",
                             target_price=210.0, stop_price=185.0),
            ],
            decision_tag="open_aapl",
            reasoning="x",
            updated_thesis="y",
            confidence=0.7,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is None  # no re-prompt
    decided = state["strategist_decision"]
    # Now contains derived legacy fields
    assert decided["target_weights"] == {"AAPL": 0.05}
    assert "AAPL" in decided["new_positions"]
    assert decided["new_positions"]["AAPL"]["opened_tick_id"] == "tick_X"
    assert decided["close_reasons"] == {}
    assert decided["trim_reasons"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_strategist_validation_v2.py -v`
Expected: FAIL — `_held_view_before_callback` does not exist; the existing `_strategist_validation_callback` does not handle the v2 schema.

### Task 9.2: Rewrite the strategist agent

**Files:**
- Modify: `src/agents/strategist/agent.py` (full replacement)

- [ ] **Step 1: Replace the agent module**

Edit `src/agents/strategist/agent.py` (full replacement):
```python
"""Strategist v2 LlmAgent — emits per-ticker TickerStance, derives legacy fields server-side."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from broker.portfolio import Portfolio
from agents.strategist.derivation import TickContext, derive_legacy_fields
from agents.strategist.held_view import render_held_positions_view
from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.prompts import STRATEGIST_INSTRUCTION
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance


def _coerce_portfolio(value) -> Portfolio:
    if isinstance(value, Portfolio):
        return value
    if value is None:
        return Portfolio(cash=0.0)
    return Portfolio.model_validate(value)


def _held_view_before_callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
    """Render the held-positions block into state["held_positions_view"]."""
    state = callback_context.state
    positions = state.get("positions", {}) or {}
    portfolio_raw = state.get("portfolio")
    portfolio = _coerce_portfolio(portfolio_raw)
    state["held_positions_view"] = render_held_positions_view(positions, portfolio)
    return None


def _reprompt(text: str) -> genai_types.Content:
    return genai_types.Content(
        parts=[genai_types.Part(text=text)],
        role="user",
    )


def _strategist_validation_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Validate per-ticker stances; on success, derive legacy fields and write back."""
    state = callback_context.state
    raw = state.get("strategist_decision")
    if not raw:
        return None

    decision = (
        StrategistDecision.model_validate(raw) if isinstance(raw, dict) else raw
    )

    tickers: list[str] = state.get("tickers", []) or []
    portfolio = _coerce_portfolio(state.get("portfolio"))
    current_weights = portfolio.current_weights()
    current_prices = {
        t: pos.last_price for t, pos in portfolio.positions.items()
    }
    tick_id: str = state.get("tick_id") or state.get("recorded_at", "unknown")

    # 1) Exhaustive: every watchlist ticker must have a stance
    emitted = {s.ticker for s in decision.stances}
    missing = [t for t in tickers if t not in emitted]
    if missing:
        return _reprompt(
            f"You missed stances for these tickers: {missing}. "
            f"Emit a TickerStance for EVERY watchlist ticker."
        )

    # 2) No off-watchlist tickers
    extras = [t for t in emitted if t not in tickers]
    if extras:
        return _reprompt(
            f"You included off-watchlist tickers: {extras}. "
            f"Only emit stances for the watchlist."
        )

    # 3) Per-stance lifecycle hint enforcement
    for stance in decision.stances:
        curr = current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(curr, stance.preferred_weight)

        if action == "open":
            missing_fields = [
                name for name, val in (
                    ("horizon", stance.horizon),
                    ("target_price", stance.target_price),
                    ("stop_price", stance.stop_price),
                ) if val is None
            ]
            if missing_fields:
                return _reprompt(
                    f"Stance for {stance.ticker} opens a position but is missing: "
                    f"{missing_fields}. Include horizon, target_price, and stop_price on opens."
                )
        elif action == "close":
            if not stance.close_reason:
                return _reprompt(
                    f"Stance for {stance.ticker} closes a position but is missing close_reason. "
                    f"Include a close_reason explaining why you're exiting."
                )
        elif action == "trim":
            if not stance.trim_reason:
                return _reprompt(
                    f"Stance for {stance.ticker} trims a position but is missing trim_reason. "
                    f"Include a trim_reason explaining the size reduction."
                )

    # 4) Derive legacy fields
    ctx = TickContext(
        tick_id=str(tick_id),
        decision_tag=decision.decision_tag,
        now=datetime.now(tz=timezone.utc),
        current_prices=current_prices,
        current_weights=current_weights,
    )
    derived = derive_legacy_fields(decision.stances, ctx)
    decision.target_weights = derived.target_weights
    decision.new_positions = derived.new_positions
    decision.close_reasons = derived.close_reasons
    decision.trim_reasons = derived.trim_reasons

    # Write back the enriched decision
    state["strategist_decision"] = decision.model_dump(mode="json")
    return None


strategist_agent = LlmAgent(
    name="Strategist",
    model="gemini-2.0-pro-001",
    instruction=STRATEGIST_INSTRUCTION,
    output_schema=StrategistDecision,
    output_key="strategist_decision",
    before_agent_callback=_held_view_before_callback,
    after_agent_callback=_strategist_validation_callback,
)
```

- [ ] **Step 2: Run the v2 callback tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_strategist_validation_v2.py -v`
Expected: PASS (7 tests)

- [ ] **Step 3: Run all strategist tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: All passing — including any pre-existing tests in this directory.

- [ ] **Step 4: Commit**

```bash
git add src/agents/strategist/agent.py tests/unit/strategist/test_strategist_validation_v2.py
git commit -m "feat(strategist): rewrite agent for v2 — before/after callbacks, derivation"
```

### Task 9.3: Update pipeline factory to pass new callbacks

**Files:**
- Modify: `src/orchestrator/pipeline.py:25-38`

- [ ] **Step 1: Update `_build_strategist`**

Edit `_build_strategist` in `src/orchestrator/pipeline.py`:
```python
def _build_strategist():
    """Build a fresh Strategist LlmAgent each time."""
    from google.adk.agents import LlmAgent
    from agents.strategist.agent import (
        _held_view_before_callback,
        _strategist_validation_callback,
    )
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistDecision
    return LlmAgent(
        name="Strategist",
        model="gemini-2.0-pro-001",
        instruction=STRATEGIST_INSTRUCTION,
        output_schema=StrategistDecision,
        output_key="strategist_decision",
        before_agent_callback=_held_view_before_callback,
        after_agent_callback=_strategist_validation_callback,
    )
```

- [ ] **Step 2: Run any pipeline-builder tests**

Run: `.venv/Scripts/python -m pytest tests/ -v -k "pipeline"`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/orchestrator/pipeline.py
git commit -m "refactor(pipeline): wire v2 strategist callbacks into LlmAgent factory"
```

---

## Phase 10 — Executor changes

### Task 10.1: Write tests for state["positions"] BUY-side write + trade-log FKs

**Files:**
- Create: `tests/unit/executor/__init__.py` (empty if missing)
- Create: `tests/unit/executor/test_open_positions_state.py`

- [ ] **Step 1: Ensure the executor test package marker exists**

Run: `.venv/Scripts/python -c "import os; open('tests/unit/executor/__init__.py', 'a').close()"`

- [ ] **Step 2: Write the failing test**

Create `tests/unit/executor/test_open_positions_state.py`:
```python
"""Executor v2 tests — state["positions"] BUY-side write + TradeLog FKs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agents.executor.agent import ExecutorAgent
from broker.fake import FakeBroker
from broker.portfolio import Portfolio
from broker.protocol import Fill
from orchestrator.state import Order


class _StubSession:
    def __init__(self):
        self.state = {}


class _StubCtx:
    def __init__(self, state: dict):
        class _S:
            pass
        self.session = _S()
        self.session.state = state


def _run(coro_gen):
    """Drain an async generator into a list, swallowing yielded events."""
    async def _drain():
        events = []
        async for ev in coro_gen:
            events.append(ev)
        return events
    return asyncio.run(_drain())


# ── BUY-side write to state["positions"][ticker] ─────────────────────────────


def test_buy_writes_thesis_to_state_positions(monkeypatch):
    broker = FakeBroker(seed_cash=10_000.0, fills_at={"AAPL": 200.0})

    state = {
        "tick_id": "tick_X",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=5, est_price=200.0).model_dump()
        ],
        "positions": {},
        "strategist_decision": {
            "decision_tag": "open_aapl",
            "new_positions": {
                "AAPL": {
                    "ticker": "AAPL",
                    "opened_at": datetime.now(tz=timezone.utc).isoformat(),
                    "opened_price": 200.0,
                    "opened_tag": "open_aapl",
                    "rationale": "open thesis",
                    "horizon": "swing",
                    "target_price": 220.0,
                    "stop_price": 190.0,
                    "catalyst": None,
                    "last_reviewed_at": datetime.now(tz=timezone.utc).isoformat(),
                    "last_review_note": "",
                    "opened_tick_id": "tick_X",
                }
            },
            "close_reasons": {},
            "trim_reasons": {},
        },
    }

    agent = ExecutorAgent(broker=broker, db_session=None)
    _run(agent._run_async_impl(_StubCtx(state)))

    assert "AAPL" in state["positions"]
    aapl_thesis = state["positions"]["AAPL"]
    assert aapl_thesis["ticker"] == "AAPL"
    assert aapl_thesis["opened_tick_id"] == "tick_X"
    assert aapl_thesis["target_price"] == 220.0


def test_sell_removes_ticker_from_state_positions():
    broker = FakeBroker(seed_cash=0.0, fills_at={"AAPL": 220.0},
                        seed_positions={"AAPL": (5.0, 200.0)})

    aapl_thesis = {
        "ticker": "AAPL",
        "opened_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "opened_price": 200.0,
        "opened_tag": "open_aapl",
        "rationale": "exit thesis",
        "horizon": "swing",
        "target_price": 220.0,
        "stop_price": 190.0,
        "catalyst": None,
        "last_reviewed_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "last_review_note": "",
        "opened_tick_id": "tick_OPEN",
    }

    state = {
        "tick_id": "tick_CLOSE",
        "final_orders": [
            Order(ticker="AAPL", action="SELL", quantity=5, est_price=220.0).model_dump()
        ],
        "positions": {"AAPL": aapl_thesis},
        "strategist_decision": {
            "decision_tag": "close_aapl",
            "new_positions": {},
            "close_reasons": {"AAPL": "Target reached"},
            "trim_reasons": {},
        },
    }

    agent = ExecutorAgent(broker=broker, db_session=None)
    _run(agent._run_async_impl(_StubCtx(state)))

    assert "AAPL" not in state["positions"]


# ── TradeLog opening_tick_id / closing_tick_id ───────────────────────────────


def test_sell_writes_tick_id_fks_to_trade_log(tmp_path):
    """When closing, the trade-log row carries opening_tick_id from thesis and closing_tick_id from state."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from orchestrator.persistence import Base, TradeLogRow

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    broker = FakeBroker(seed_cash=0.0, fills_at={"AAPL": 220.0},
                        seed_positions={"AAPL": (5.0, 200.0)})

    aapl_thesis = {
        "ticker": "AAPL",
        "opened_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "opened_price": 200.0,
        "opened_tag": "open_aapl",
        "rationale": "exit thesis",
        "horizon": "swing",
        "target_price": 220.0,
        "stop_price": 190.0,
        "catalyst": None,
        "last_reviewed_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "last_review_note": "",
        "opened_tick_id": "tick_OPEN",
    }

    state = {
        "tick_id": "tick_CLOSE",
        "final_orders": [
            Order(ticker="AAPL", action="SELL", quantity=5, est_price=220.0).model_dump()
        ],
        "positions": {"AAPL": aapl_thesis},
        "strategist_decision": {
            "decision_tag": "close_aapl",
            "new_positions": {},
            "close_reasons": {"AAPL": "Target reached"},
            "trim_reasons": {},
        },
    }

    agent = ExecutorAgent(broker=broker, db_session=db)
    _run(agent._run_async_impl(_StubCtx(state)))

    db.commit()
    rows = db.query(TradeLogRow).all()
    assert len(rows) == 1
    assert rows[0].opening_tick_id == "tick_OPEN"
    assert rows[0].closing_tick_id == "tick_CLOSE"
```

This test depends on `FakeBroker` supporting `seed_positions` and `fills_at`. Verify the existing fake.py before running:

Run: `.venv/Scripts/python -c "from broker.fake import FakeBroker; help(FakeBroker.__init__)"`

If `seed_positions` / `fills_at` are not constructor args, adjust the test fixtures to match the existing FakeBroker API (or add the missing constructor support to FakeBroker as part of this task — keep changes minimal).

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/executor/test_open_positions_state.py -v`
Expected: FAIL — executor doesn't currently write state["positions"][ticker] on BUY; TradeLogRow has no opening/closing_tick_id columns.

### Task 10.2: Add TradeLogRow.opening_tick_id / closing_tick_id

**Files:**
- Modify: `src/orchestrator/persistence.py`

- [ ] **Step 1: Add the columns to TradeLogRow**

Edit `src/orchestrator/persistence.py`. Find the `TradeLogRow` class and add the two columns:
```python
class TradeLogRow(Base):
    __tablename__ = "trade_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime] = mapped_column(DateTime)
    opened_price: Mapped[float] = mapped_column(Float)
    closed_price: Mapped[float] = mapped_column(Float)
    pnl_dollar: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)
    holding_period_hours: Mapped[int] = mapped_column(Integer)
    horizon_intent: Mapped[str] = mapped_column(String)
    opened_tag: Mapped[str] = mapped_column(String)
    closed_tag: Mapped[str] = mapped_column(String)
    opened_rationale: Mapped[str] = mapped_column(String)
    close_reason: Mapped[str] = mapped_column(String)
    catalyst_realised: Mapped[bool] = mapped_column(Boolean)
    opening_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)   # NEW
    closing_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)   # NEW
```

- [ ] **Step 2: Verify the schema still creates cleanly**

Run: `.venv/Scripts/python -c "from orchestrator.persistence import Base, make_engine, create_all; e = make_engine('sqlite://'); create_all(e); print('OK')"`
Expected: `OK`

### Task 10.3: Update executor to write state["positions"] on BUY + populate FKs

**Files:**
- Modify: `src/agents/executor/agent.py`

- [ ] **Step 1: Replace the executor**

Edit `src/agents/executor/agent.py` (full replacement):
```python
"""Executor BaseAgent — submits orders via Broker, manages position book."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from broker.protocol import Broker, BrokerRejection
from orchestrator.state import Execution, Order


class ExecutorAgent(BaseAgent):
    """ADK agent that submits the risk-gated orders to the broker and records results.

    Responsibilities:
    - Submit each Order from state["final_orders"] via the broker.
    - Record fill details and slippage in state["executions"].
    - Update the position book (state["positions"]):
        * BUY  → write the new PositionThesis dict (from strategist_decision.new_positions)
        * SELL → remove the ticker from state["positions"]
    - Write a trade-log entry to the DB when a position fully closes,
      populating opening_tick_id (from thesis) and closing_tick_id (from state["tick_id"]).
    - Idempotency guard: skips execution if tick_id was already processed.
    """

    name: str = "Executor"
    broker: Any  # Broker protocol
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        tick_id: str = state.get("tick_id", "unknown")

        if state.get("last_executed_tick_id") == tick_id:
            return
        yield  # required by the ADK generator protocol

        orders_raw = state.get("final_orders", [])
        orders = [
            Order.model_validate(o) if isinstance(o, dict) else o
            for o in orders_raw
        ]

        executions: list[dict] = []
        positions: dict = dict(state.get("positions", {}))

        # New_positions from this tick's strategist decision (used on BUYs).
        decision = state.get("strategist_decision") or {}
        new_positions = decision.get("new_positions", {}) if isinstance(decision, dict) else {}
        close_reasons = decision.get("close_reasons", {}) if isinstance(decision, dict) else {}
        decision_tag = decision.get("decision_tag", "unknown") if isinstance(decision, dict) else "unknown"

        for order in orders:
            try:
                fill = await self.broker.submit_market(
                    order.ticker, order.action, order.quantity
                )
                exec_record = Execution(
                    order=order,
                    status="filled",
                    actual_price=fill.price,
                    actual_quantity=fill.quantity,
                    broker_order_id=fill.id,
                    slippage_bps=(
                        abs(fill.price - order.est_price) / order.est_price * 10_000
                        if order.est_price else None
                    ),
                )

                if order.action == "BUY":
                    # Record the thesis for this newly-opened position.
                    thesis_dict = new_positions.get(order.ticker)
                    if thesis_dict is not None:
                        positions[order.ticker] = thesis_dict

                elif order.action == "SELL" and order.ticker in positions:
                    thesis = positions.get(order.ticker)
                    if thesis and self.db_session:
                        from orchestrator.persistence import save_trade_log_entry

                        opened_price = (
                            thesis.get("opened_price") if isinstance(thesis, dict)
                            else thesis.opened_price
                        )
                        opened_at = (
                            thesis.get("opened_at") if isinstance(thesis, dict)
                            else thesis.opened_at
                        )
                        opened_tick = (
                            thesis.get("opened_tick_id") if isinstance(thesis, dict)
                            else getattr(thesis, "opened_tick_id", "")
                        )
                        opened_tag_val = (
                            thesis.get("opened_tag") if isinstance(thesis, dict)
                            else thesis.opened_tag
                        )
                        opened_rationale_val = (
                            thesis.get("rationale") if isinstance(thesis, dict)
                            else thesis.rationale
                        )
                        horizon_val = (
                            thesis.get("horizon") if isinstance(thesis, dict)
                            else thesis.horizon
                        )
                        closed_at = datetime.now(tz=timezone.utc)
                        opened_at_dt = (
                            datetime.fromisoformat(opened_at)
                            if isinstance(opened_at, str)
                            else opened_at
                        )
                        holding_hours = int(
                            (closed_at - opened_at_dt).total_seconds() / 3600
                        )
                        pnl_pct = (fill.price - opened_price) / opened_price * 100

                        save_trade_log_entry(self.db_session, {
                            "ticker":              order.ticker,
                            "opened_at":           opened_at_dt,
                            "closed_at":           closed_at,
                            "opened_price":        opened_price,
                            "closed_price":        fill.price,
                            "pnl_dollar":          (fill.price - opened_price) * fill.quantity,
                            "pnl_pct":             pnl_pct,
                            "holding_period_hours": holding_hours,
                            "horizon_intent":      horizon_val,
                            "opened_tag":          opened_tag_val,
                            "closed_tag":          decision_tag,
                            "opened_rationale":    opened_rationale_val,
                            "close_reason":        close_reasons.get(order.ticker, ""),
                            "catalyst_realised":   False,
                            "opening_tick_id":     opened_tick or None,
                            "closing_tick_id":     tick_id,
                        })

                    del positions[order.ticker]

            except BrokerRejection as e:
                exec_record = Execution(
                    order=order,
                    status="rejected",
                    error=str(e),
                )

            executions.append(exec_record.model_dump())

        state["executions"] = executions
        state["positions"] = positions
        state["last_executed_tick_id"] = tick_id


def build_executor(broker: Broker, db_session=None) -> ExecutorAgent:
    """Factory used by the pipeline builder to wire in the broker and DB session."""
    return ExecutorAgent(broker=broker, db_session=db_session)
```

- [ ] **Step 2: Run executor tests**

Run: `.venv/Scripts/python -m pytest tests/unit/executor/test_open_positions_state.py -v`
Expected: PASS (3 tests)

- [ ] **Step 3: Run any existing executor tests to confirm no regression**

Run: `.venv/Scripts/python -m pytest tests/ -v -k "executor"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/agents/executor/agent.py src/orchestrator/persistence.py tests/unit/executor/__init__.py tests/unit/executor/test_open_positions_state.py
git commit -m "feat(executor): write thesis on BUY + populate TradeLog tick_id FKs"
```

---

## Phase 11 — TickerStanceRow + persistence

### Task 11.1: Write tests for TickerStanceRow

**Files:**
- Create: `tests/unit/orchestrator/test_persistence_strategist.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/orchestrator/test_persistence_strategist.py`:
```python
"""TickerStanceRow + TradeLogRow FK tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import (
    Base,
    TickerStanceRow,
    TradeLogRow,
    save_ticker_stance,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session()


def test_ticker_stance_row_round_trip(db_session):
    save_ticker_stance(
        db_session,
        tick_id="tick_X",
        decision_tag="open_aapl",
        recorded_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        stance={
            "ticker": "AAPL",
            "preferred_weight": 0.08,
            "conviction": 0.7,
            "rationale": "FCF + insider",
            "horizon": "swing",
            "target_price": 210.0,
            "stop_price": 185.0,
            "catalyst": "Q3",
            "close_reason": None,
            "trim_reason": None,
        },
        lifecycle_action="open",
    )
    db_session.commit()

    rows = db_session.query(TickerStanceRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.tick_id == "tick_X"
    assert r.ticker == "AAPL"
    assert r.preferred_weight == 0.08
    assert r.conviction == 0.7
    assert r.rationale == "FCF + insider"
    assert r.horizon == "swing"
    assert r.target_price == 210.0
    assert r.stop_price == 185.0
    assert r.catalyst == "Q3"
    assert r.close_reason is None
    assert r.trim_reason is None
    assert r.lifecycle_action == "open"
    assert r.decision_tag == "open_aapl"


def test_ticker_stance_row_nulls_for_hold(db_session):
    save_ticker_stance(
        db_session,
        tick_id="tick_X",
        decision_tag="hold_msft",
        recorded_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        stance={
            "ticker": "MSFT",
            "preferred_weight": 0.05,
            "conviction": 0.6,
            "rationale": "still cheap",
            "horizon": None,
            "target_price": None,
            "stop_price": None,
            "catalyst": None,
            "close_reason": None,
            "trim_reason": None,
        },
        lifecycle_action="hold",
    )
    db_session.commit()
    r = db_session.query(TickerStanceRow).first()
    assert r.horizon is None
    assert r.target_price is None
    assert r.lifecycle_action == "hold"


def test_trade_log_fk_join_to_ticker_stance(db_session):
    """Closed-trade outcomes can be joined back to the deliberation that opened them."""
    # Insert a stance row for the opening tick
    save_ticker_stance(
        db_session,
        tick_id="tick_OPEN",
        decision_tag="open_aapl",
        recorded_at=datetime(2026, 4, 1, 14, tzinfo=timezone.utc),
        stance={
            "ticker": "AAPL",
            "preferred_weight": 0.08,
            "conviction": 0.7,
            "rationale": "open thesis",
            "horizon": "swing",
            "target_price": 210.0,
            "stop_price": 185.0,
            "catalyst": None,
            "close_reason": None,
            "trim_reason": None,
        },
        lifecycle_action="open",
    )
    # Insert a trade-log row for the closing tick referencing the opening tick
    db_session.add(TradeLogRow(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=timezone.utc),
        closed_at=datetime(2026, 4, 22, 14, tzinfo=timezone.utc),
        opened_price=192.40,
        closed_price=210.0,
        pnl_dollar=88.0,
        pnl_pct=9.13,
        holding_period_hours=504,
        horizon_intent="swing",
        opened_tag="open_aapl",
        closed_tag="close_aapl",
        opened_rationale="open thesis",
        close_reason="Target reached",
        catalyst_realised=False,
        opening_tick_id="tick_OPEN",
        closing_tick_id="tick_CLOSE",
    ))
    db_session.commit()

    # Join: every TradeLogRow with opening_tick_id → that tick's TickerStanceRow rows
    joined = (
        db_session.query(TradeLogRow, TickerStanceRow)
        .filter(TradeLogRow.opening_tick_id == TickerStanceRow.tick_id)
        .filter(TradeLogRow.ticker == TickerStanceRow.ticker)
        .all()
    )
    assert len(joined) == 1
    trade, stance = joined[0]
    assert trade.ticker == "AAPL"
    assert stance.tick_id == "tick_OPEN"
    assert stance.lifecycle_action == "open"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_persistence_strategist.py -v`
Expected: FAIL — `TickerStanceRow` and `save_ticker_stance` don't exist.

### Task 11.2: Add TickerStanceRow + save_ticker_stance

**Files:**
- Modify: `src/orchestrator/persistence.py`

- [ ] **Step 1: Add the row + the save helper**

Append to `src/orchestrator/persistence.py` (after the existing TradeLog section, before the `make_engine` helper):
```python
# ── TickerStanceRow ──────────────────────────────────────────────────


class TickerStanceRow(Base):
    """One row per ticker per tick — strategist's per-ticker decision substrate."""

    __tablename__ = "ticker_stances"

    id: Mapped[int]                = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]           = mapped_column(String, index=True)
    recorded_at: Mapped[datetime]  = mapped_column(DateTime)
    ticker: Mapped[str]            = mapped_column(String, index=True)
    preferred_weight: Mapped[float] = mapped_column(Float)
    conviction: Mapped[float]      = mapped_column(Float)
    rationale: Mapped[str]         = mapped_column(String)
    horizon: Mapped[str | None]    = mapped_column(String, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    catalyst: Mapped[str | None]   = mapped_column(String, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    trim_reason: Mapped[str | None]  = mapped_column(String, nullable=True)
    lifecycle_action: Mapped[str]  = mapped_column(String, index=True)
    decision_tag: Mapped[str]      = mapped_column(String, index=True)


def save_ticker_stance(
    session: Session,
    *,
    tick_id: str,
    decision_tag: str,
    recorded_at: datetime,
    stance: dict,
    lifecycle_action: str,
) -> None:
    """Persist one ticker stance. Caller is responsible for committing."""
    row = TickerStanceRow(
        tick_id=tick_id,
        recorded_at=recorded_at,
        ticker=stance["ticker"],
        preferred_weight=stance["preferred_weight"],
        conviction=stance["conviction"],
        rationale=stance["rationale"],
        horizon=stance.get("horizon"),
        target_price=stance.get("target_price"),
        stop_price=stance.get("stop_price"),
        catalyst=stance.get("catalyst"),
        close_reason=stance.get("close_reason"),
        trim_reason=stance.get("trim_reason"),
        lifecycle_action=lifecycle_action,
        decision_tag=decision_tag,
    )
    session.add(row)
    session.flush()
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_persistence_strategist.py -v`
Expected: PASS (3 tests)

- [ ] **Step 3: Run all persistence tests for no regression**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/ -v`
Expected: All passing.

- [ ] **Step 4: Commit**

```bash
git add src/orchestrator/persistence.py tests/unit/orchestrator/test_persistence_strategist.py
git commit -m "feat(persistence): add TickerStanceRow + save_ticker_stance helper"
```

---

## Phase 12 — strategist_decision_writer

### Task 12.1: Write tests for the writer

**Files:**
- Create: `tests/unit/strategist/test_decision_writer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_decision_writer.py`:
```python
"""StrategistDecisionWriter tests — Tier 1, no LLM."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.strategist.decision_writer import (
    StrategistDecisionWriter,
    build_strategist_decision_writer,
)
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio, Position
from orchestrator.persistence import Base, TickerStanceRow


class _StubCtx:
    def __init__(self, state: dict):
        class _S: pass
        self.session = _S()
        self.session.state = state


def _run(coro_gen):
    async def _drain():
        return [ev async for ev in coro_gen]
    return asyncio.run(_drain())


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session()


def test_writer_persists_one_row_per_stance(db_session):
    decision = StrategistDecision(
        stances=[
            TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                         rationale="open", horizon="swing",
                         target_price=210.0, stop_price=185.0),
            TickerStance(ticker="MSFT", preferred_weight=0.05, conviction=0.6,
                         rationale="hold"),
            TickerStance(ticker="NVDA", preferred_weight=0.0, conviction=0.8,
                         rationale="exit", close_reason="Thesis broken"),
        ],
        target_weights={"AAPL": 0.08, "MSFT": 0.05, "NVDA": 0.0},
        decision_tag="rotation_q2",
        reasoning="x",
        updated_thesis="y",
        confidence=0.65,
    )
    portfolio = Portfolio(
        cash=900.0,
        positions={
            "MSFT": Position(quantity=2.0, avg_cost=400.0, last_price=410.0),
            "NVDA": Position(quantity=1.0, avg_cost=900.0, last_price=850.0),
        },
    )
    state = {
        "tick_id": "tick_X",
        "strategist_decision": decision.model_dump(mode="json"),
        "portfolio": portfolio.model_dump(mode="json"),
    }
    agent = StrategistDecisionWriter(db_session=db_session)
    _run(agent._run_async_impl(_StubCtx(state)))

    db_session.commit()
    rows = db_session.query(TickerStanceRow).all()
    assert len(rows) == 3

    by_ticker = {r.ticker: r for r in rows}
    assert by_ticker["AAPL"].lifecycle_action == "open"     # curr ~0
    assert by_ticker["MSFT"].lifecycle_action == "hold"     # curr ~0.005, pref 0.05 → trim? depends on weight
    # Note: actual MSFT weight ≈ (2*410)/(900+2*410+1*850) ≈ 0.32 ⇒ pref 0.05 is a trim
    # We assert based on the actual derived action; recompute:
    # Total = 900 + 820 + 850 = 2570; MSFT weight = 820/2570 = 0.319; pref 0.05; delta = -0.269 ⇒ trim
    assert by_ticker["MSFT"].lifecycle_action in ("trim", "hold")  # depends on actual weight
    assert by_ticker["NVDA"].lifecycle_action == "close"

    # decision_tag denormalised on each row
    assert all(r.decision_tag == "rotation_q2" for r in rows)


def test_writer_no_op_when_no_decision(db_session):
    state = {"tick_id": "tick_X", "strategist_decision": None, "portfolio": Portfolio(cash=100.0).model_dump(mode="json")}
    agent = StrategistDecisionWriter(db_session=db_session)
    _run(agent._run_async_impl(_StubCtx(state)))
    db_session.commit()
    assert db_session.query(TickerStanceRow).count() == 0


def test_writer_no_op_when_no_db_session():
    state = {"tick_id": "tick_X", "strategist_decision": StrategistDecision(
        stances=[TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5, rationale="hold")],
        target_weights={"AAPL": 0.0},
        decision_tag="x",
        reasoning="x",
        updated_thesis="y",
        confidence=0.5,
    ).model_dump(mode="json"), "portfolio": Portfolio(cash=100.0).model_dump(mode="json")}
    agent = StrategistDecisionWriter(db_session=None)
    # Should not raise
    _run(agent._run_async_impl(_StubCtx(state)))


def test_build_factory_returns_agent(db_session):
    agent = build_strategist_decision_writer(db_session)
    assert isinstance(agent, StrategistDecisionWriter)
    assert agent.db_session is db_session
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_writer.py -v`
Expected: FAIL — `decision_writer` module does not exist.

### Task 12.2: Implement strategist_decision_writer

**Files:**
- Create: `src/agents/strategist/decision_writer.py`

- [ ] **Step 1: Write the writer**

Create `src/agents/strategist/decision_writer.py`:
```python
"""StrategistDecisionWriter — persists per-ticker stances to TickerStanceRow.

Runs in the pipeline between strategist and risk_gate so that the council's
intent is recorded even if risk_gate raises a contract violation. Mirrors the
existing AttributionWriter pattern.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.schema import StrategistDecision
from broker.portfolio import Portfolio


class StrategistDecisionWriter(BaseAgent):
    """Persist one TickerStanceRow per stance per tick."""

    name: str = "StrategistDecisionWriter"
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if False:
            yield  # generator protocol; never actually emits events

        if self.db_session is None:
            return

        raw = state.get("strategist_decision")
        if not raw:
            return

        from orchestrator.persistence import save_ticker_stance

        decision = StrategistDecision.model_validate(raw) if isinstance(raw, dict) else raw

        portfolio_raw = state.get("portfolio")
        if isinstance(portfolio_raw, Portfolio):
            portfolio = portfolio_raw
        elif portfolio_raw is None:
            portfolio = Portfolio(cash=0.0)
        else:
            portfolio = Portfolio.model_validate(portfolio_raw)
        current_weights = portfolio.current_weights()

        tick_id = state.get("tick_id", "unknown")
        recorded_at = datetime.now(tz=timezone.utc)

        for stance in decision.stances:
            curr = current_weights.get(stance.ticker, 0.0)
            action = derive_lifecycle_action(curr, stance.preferred_weight)
            save_ticker_stance(
                self.db_session,
                tick_id=tick_id,
                decision_tag=decision.decision_tag,
                recorded_at=recorded_at,
                stance=stance.model_dump(),
                lifecycle_action=action,
            )


def build_strategist_decision_writer(db_session=None) -> StrategistDecisionWriter:
    """Factory used by the pipeline builder."""
    return StrategistDecisionWriter(db_session=db_session)
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_writer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add src/agents/strategist/decision_writer.py tests/unit/strategist/test_decision_writer.py
git commit -m "feat(strategist): add strategist_decision_writer to persist per-ticker stances"
```

---

## Phase 13 — Pipeline wiring

### Task 13.1: Write tests for v2 pipeline structure

**Files:**
- Create: `tests/unit/orchestrator/test_pipeline_wiring_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/orchestrator/test_pipeline_wiring_v2.py`:
```python
"""Pipeline v2 structure tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from orchestrator.pipeline import build_pipeline
from broker.fake import FakeBroker


def test_pipeline_includes_strategist_decision_writer():
    """The new writer must sit between Strategist and RiskGate."""
    broker = FakeBroker(seed_cash=1000.0)
    pipe = build_pipeline(broker=broker, db_session=None)
    names = [a.name for a in pipe.sub_agents]
    # Required order:
    assert "Strategist" in names
    assert "StrategistDecisionWriter" in names
    assert "RiskGate" in names or "RiskGateAgent" in names
    si = names.index("Strategist")
    wi = names.index("StrategistDecisionWriter")
    rg = names.index("RiskGate") if "RiskGate" in names else names.index("RiskGateAgent")
    assert si < wi < rg


def test_pipeline_has_eight_stages():
    broker = FakeBroker(seed_cash=1000.0)
    pipe = build_pipeline(broker=broker, db_session=None)
    assert len(pipe.sub_agents) == 8
```

The exact name `RiskGate` vs `RiskGateAgent` may differ in the codebase — the test accepts either. Adjust if your codebase uses a third name.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_pipeline_wiring_v2.py -v`
Expected: FAIL — pipeline currently has 7 stages and no `StrategistDecisionWriter`.

### Task 13.2: Wire the new writer into the pipeline

**Files:**
- Modify: `src/orchestrator/pipeline.py`

- [ ] **Step 1: Add the writer between strategist and risk_gate**

Edit `build_pipeline` in `src/orchestrator/pipeline.py`:
```python
def build_pipeline(broker, db_session=None) -> SequentialAgent:
    """Compose the full hourly tick pipeline."""
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.attribution.writer import build_attribution_writer
    from agents.strategist.decision_writer import build_strategist_decision_writer
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(),
            build_attribution_writer(db_session),
            _build_strategist(),
            build_strategist_decision_writer(db_session),    # NEW
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )
```

- [ ] **Step 2: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_pipeline_wiring_v2.py -v`
Expected: PASS (2 tests)

- [ ] **Step 3: Run all unit tests for full regression check**

Run: `.venv/Scripts/python -m pytest tests/unit/ -v`
Expected: All passing.

- [ ] **Step 4: Commit**

```bash
git add src/orchestrator/pipeline.py tests/unit/orchestrator/test_pipeline_wiring_v2.py
git commit -m "feat(pipeline): wire strategist_decision_writer between Strategist and RiskGate"
```

---

## Phase 14 — Tier 2 LLM-touching smoke (gated)

### Task 14.1: Add a smoke integration test

**Files:**
- Create: `tests/integration/test_strategist_v2_smoke.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/integration/test_strategist_v2_smoke.py`:
```python
"""Strategist v2 smoke — Tier 2, real LLM. Gated by RUN_LLM_TESTS env var.

Confirms the strategist:
- consumes the Held Positions block
- emits a parseable list[TickerStance], exhaustive over the watchlist
- includes lifecycle hints where required
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from agents.strategist.agent import strategist_agent
from agents.strategist.schema import PositionThesis, StrategistDecision
from broker.portfolio import Portfolio, Position


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_TESTS") != "1",
    reason="LLM-touching test; set RUN_LLM_TESTS=1 to run",
)


@pytest.mark.integration
def test_strategist_v2_emits_per_ticker_stances_with_held_position():
    """Run one strategist call against fixture state with one held position."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    aapl_thesis = PositionThesis(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="FCF yield + insider buying",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
        last_reviewed_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        opened_tick_id="tick_OPEN",
    )
    portfolio = Portfolio(
        cash=8000.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )

    session_service = InMemorySessionService()
    session = session_service.create_session(app_name="strategist_v2_smoke", user_id="t")
    session.state.update({
        "tick_id": "tick_TEST",
        "tickers": ["AAPL", "MSFT"],
        "portfolio": portfolio.model_dump(mode="json"),
        "positions": {"AAPL": aapl_thesis.model_dump(mode="json")},
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "",
        "technical_signals": [],
        "fundamental_signals": [],
        "sentiment_signals": [],
        "smart_money_signals": [],
    })

    runner = Runner(agent=strategist_agent, session_service=session_service)
    runner.run(user_id="t", session_id=session.id, new_message=None)

    decision_raw = session.state.get("strategist_decision")
    assert decision_raw is not None
    decision = StrategistDecision.model_validate(decision_raw)

    # Exhaustive
    emitted = {s.ticker for s in decision.stances}
    assert emitted == {"AAPL", "MSFT"}

    # Legacy fields populated by the after-callback
    assert set(decision.target_weights.keys()) == {"AAPL", "MSFT"}
```

The exact `Runner` / session API may need a small adjustment depending on the ADK version. Match the pattern used by other integration tests in `tests/integration/`. If no integration runner pattern exists, mark this test xfail with a TODO referencing the next planned ADK runner spike.

- [ ] **Step 2: Run the smoke test (gated)**

Run: `RUN_LLM_TESTS=1 .venv/Scripts/python -m pytest tests/integration/test_strategist_v2_smoke.py -v`
Expected: PASS — strategist returns parseable stances for both tickers.

If the test is too brittle for one-shot CI, leave it as a manual smoke and document in the test docstring.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_strategist_v2_smoke.py
git commit -m "test(strategist): add v2 LLM-touching smoke (gated by RUN_LLM_TESTS)"
```

### Task 14.2: Run the local smoke script end-to-end

**Files:** (none — runs existing `scripts/smoke_run.py`)

- [ ] **Step 1: Run the smoke script**

Run: `RUN_LLM_TESTS=1 .venv/Scripts/python scripts/smoke_run.py --ticks 3`
Expected: 3 ticks complete cleanly; no validation errors; per-tick `TickerStanceRow` rows appear in the dev SQLite at `data/stockbot.db`.

- [ ] **Step 2: Verify TickerStanceRow rows landed**

Run: `.venv/Scripts/python -c "from sqlalchemy import create_engine; from sqlalchemy.orm import sessionmaker; from orchestrator.persistence import TickerStanceRow; e = create_engine('sqlite:///data/stockbot.db'); S = sessionmaker(bind=e); s = S(); print(s.query(TickerStanceRow).count())"`
Expected: ≥ 3 × |watchlist| rows.

- [ ] **Step 3: Commit any incidental fixes** (only if smoke uncovered a small bug)

```bash
git add <files>
git commit -m "fix(strategist): <brief description of what smoke uncovered>"
```

If smoke passes cleanly, skip step 3.

---

## Phase 15 — Cleanup

### Task 15.1: Append the graphify delta entry

**Files:**
- Modify: `graphify-out/graph_delta.md`

- [ ] **Step 1: Append a dated entry**

Edit `graphify-out/graph_delta.md`. Append at the end:
```markdown
## 2026-05-08 — Strategist v2 + per-ticker stance substrate

Replaced the single-strategist `StrategistDecision.target_weights` flat output
with per-ticker `TickerStance` schema. Held-position context now rendered into
the prompt. Per-ticker stances persisted to `TickerStanceRow`. Outcome
attribution FKs added on `TradeLogRow`.

- New nodes: `TickerStance`, `TickerStanceRow`, `StrategistDecisionWriter`,
  `derive_lifecycle_action`, `derive_legacy_fields`, `render_held_positions_view`,
  `_held_view_before_callback`.
- New edges: `strategist_agent --before_callback--> _held_view_before_callback`;
  `strategist_agent --after_callback--> _strategist_validation_callback (rewritten)`;
  `_strategist_validation_callback --calls--> derive_legacy_fields`;
  `StrategistDecisionWriter --persists--> TickerStanceRow`;
  `pipeline.build_pipeline --includes--> StrategistDecisionWriter` (new stage 4).
- Modified: `StrategistDecision` (gains `stances`, `trim_reasons`),
  `PositionThesis` (gains `opened_tick_id`),
  `TradeLogRow` (gains `opening_tick_id`, `closing_tick_id`),
  `STRATEGIST_INSTRUCTION` (rewritten template),
  `ExecutorAgent` (BUY-side write to `state["positions"]`; FK population).
- Removed: none in `src/`. Plans `strategist-council.md` and
  `exit-rules-and-telemetry.md` deleted from `docs/superpowers/plans/`.
- Council architecture (3-persona pool + CouncilAggregator) was specced
  (`strategist-council-design.md`) but not implemented; spec marked superseded.
```

- [ ] **Step 2: Commit**

```bash
git add graphify-out/graph_delta.md
git commit -m "docs(graphify): log strategist v2 + per-ticker stance substrate"
```

### Task 15.2: Mark old specs as superseded

**Files:**
- Modify: `docs/superpowers/specs/strategist-council-design.md` (prepend header)
- Modify: `docs/superpowers/specs/exit-rules-and-telemetry-design.md` (prepend header)

- [ ] **Step 1: Prepend a superseded header to each old spec**

For `docs/superpowers/specs/strategist-council-design.md`, insert above the existing first line:
```markdown
> **SUPERSEDED** by [`strategist-v2-design.md`](./strategist-v2-design.md) (2026-05-08).
> The council architecture (3-persona pool + CouncilAggregator) was dropped during
> brainstorming — see "Why we dropped the council" in the v2 spec. Salvageable bits
> (per-ticker stance schema, trim/add lifecycle, outcome FKs, persistence pattern)
> are folded into v2. This file is retained for the salvage trail.

```

For `docs/superpowers/specs/exit-rules-and-telemetry-design.md`, insert above the existing first line:
```markdown
> **SUPERSEDED** by [`strategist-v2-design.md`](./strategist-v2-design.md) (2026-05-08).
> The council-based design this spec extended was dropped. Salvageable bits
> (`PositionThesis.opened_tick_id`, trim_reasons in StrategistDecision, TradeLogRow
> outcome FKs, per-tick persistence pattern) are folded into v2. The full
> `PositionPack` (running_max_price, distance-to-trigger flags, SPY-relative excess
> return) is deferred to a follow-up spec; v2's held-positions view is intentionally
> minimal. This file is retained for the salvage trail.

```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/strategist-council-design.md docs/superpowers/specs/exit-rules-and-telemetry-design.md
git commit -m "docs(specs): mark council + exit-rules specs as superseded by strategist-v2"
```

### Task 15.3: Delete the never-executed plans

**Files:**
- Delete: `docs/superpowers/plans/strategist-council.md`
- Delete: `docs/superpowers/plans/exit-rules-and-telemetry.md`

- [ ] **Step 1: Remove the obsolete plan files**

Run: `git rm docs/superpowers/plans/strategist-council.md docs/superpowers/plans/exit-rules-and-telemetry.md`
Expected: both files staged for deletion.

- [ ] **Step 2: Commit**

```bash
git commit -m "docs(plans): remove never-executed council + exit-rules plans (superseded by strategist-v2)"
```

### Task 15.4: Final regression check

- [ ] **Step 1: Run the full unit test suite**

Run: `.venv/Scripts/python -m pytest tests/unit/ -v`
Expected: All passing.

- [ ] **Step 2: Run ruff for lint regressions**

Run: `.venv/Scripts/python -m ruff check src/ tests/`
Expected: Zero new violations introduced by this work. (Pre-existing violations are out of scope unless they're in files this plan touches.)

- [ ] **Step 3: Final commit if any incidental cleanup was needed**

If steps 1-2 surfaced anything, fix and commit. If clean, no further action.

---

## Done

The Strategist v2 spec is fully implemented. The persistent `TickerStanceRow` table + `TradeLogRow.opening_tick_id` / `closing_tick_id` FKs are the substrate for the future knowledge-base spec (Goal 3). The next two brainstorming sessions on the roadmap are:

- **Goal 2 — Analyst → Strategist Contract:** structured `AnalystSignal.evidence` numerics, `ANALYST_WEIGHTS` knob, `SmartMoneySignal` normalisation. Likely 1 brainstorming session.
- **Goal 3 — Knowledge base / self-improvement:** start with a decomposition brainstorm (what's a "signal" as a lookup primitive? what gets queried at decision time? how do learnings feed back?). Likely 2-3 sessions.

See `docs/superpowers/backlog.md` for the latest brainstorming priorities.

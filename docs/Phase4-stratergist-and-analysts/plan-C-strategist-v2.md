# Plan C — Strategist v2 Against New Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is self-contained — a fresh subagent should be able to pick it up cold using only this file + the spec at `docs/Phase4-stratergist-and-analysts/spec.md`.

**Goal:** Rewrite the strategist to (1) emit per-ticker `TickerStance` instead of a flat `target_weights` dict, (2) consume the new `TickerEvidence` from Plan B instead of four flat analyst-signal lists, (3) see held positions via a structured rendered block, (4) persist per-ticker stances to `TickerStanceRow`, and (5) populate `TradeLogRow.opening_tick_id` / `closing_tick_id` for outcome attribution. The strategist's prompt and agent code are touched **exactly once** by this plan.

**Architecture:** New strategist subpackages (`stance_schema.py`, `lifecycle.py`, `derivation.py`, `held_view.py`, `decision_writer.py`, `evidence_view.py`). The agent rewrites with two `before_agent_callback`s (held view + ticker_evidence rendering) and one `after_agent_callback` (validation + legacy field derivation). New ORM `TickerStanceRow`; `TradeLogRow` gains nullable indexed `opening_tick_id` / `closing_tick_id`. Pipeline gains a `StrategistDecisionWriter` stage between Strategist and RiskGate.

**Tech Stack:** Python 3.11+, Google ADK (`LlmAgent`, `BaseAgent`, `CallbackContext`), Pydantic v2, SQLAlchemy 2 ORM, pytest.

**Reference reading before starting:**
- `docs/Phase4-stratergist-and-analysts/spec.md` — design rationale, lifecycle math, validation rules
- `src/agents/strategist/{schema,prompts,agent}.py` — current strategist
- `src/agents/executor/agent.py` — current executor (will be modified)
- `src/orchestrator/{persistence,pipeline,state}.py` — ORM + pipeline wiring
- `src/contract/{evidence,ticker_evidence,digest}.py` — types from Plan A
- `src/broker/portfolio.py` — `Portfolio.current_weights()` returns `dict[str, float]`

**Project conventions:**
- PYTHONPATH root = `src/`. Imports use `from agents.strategist.lifecycle import …`.
- Run pytest as `.venv/Scripts/python -m pytest`.
- One commit per task. Conventional Commits prefixes.

**Pre-requisites:** Plans A + B merged.

---

## Task C1: Add `stance_schema.py` (TickerStance model)

**Files:**
- Create: `src/agents/strategist/stance_schema.py`
- Create: `tests/unit/strategist/test_stance_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_stance_schema.py`:
```python
"""TickerStance schema tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance


def test_minimal_valid():
    s = TickerStance(ticker="AAPL", preferred_weight=0.05, conviction=0.7, rationale="open")
    assert s.ticker == "AAPL"
    assert s.preferred_weight == 0.05
    assert s.horizon is None


def test_open_with_full_lifecycle_fields():
    s = TickerStance(
        ticker="AAPL", preferred_weight=0.08, conviction=0.7,
        rationale="FCF + insider", horizon="swing",
        target_price=210.0, stop_price=185.0, catalyst="Q3",
    )
    assert s.horizon == "swing"
    assert s.target_price == 210.0


def test_close_with_close_reason():
    s = TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5,
                     rationale="exit", close_reason="thesis broken")
    assert s.close_reason == "thesis broken"


def test_trim_with_trim_reason():
    s = TickerStance(ticker="AAPL", preferred_weight=0.03, conviction=0.5,
                     rationale="reduce", trim_reason="profit-taking")
    assert s.trim_reason == "profit-taking"


def test_rejects_preferred_weight_out_of_range():
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=1.5, conviction=0.5, rationale="x")


def test_rejects_conviction_out_of_range():
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=0.5, conviction=1.5, rationale="x")


def test_rejects_rationale_over_140_chars():
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=0.5, conviction=0.5, rationale="x" * 141)


def test_rejects_unknown_horizon():
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=0.5, conviction=0.5,
                     rationale="x", horizon="forever")


def test_round_trip():
    original = TickerStance(
        ticker="MSFT", preferred_weight=0.06, conviction=0.6,
        rationale="cloud tailwind", horizon="long_term",
        target_price=450.0, stop_price=395.0,
    )
    rebuilt = TickerStance.model_validate(original.model_dump(mode="json"))
    assert rebuilt == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_stance_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.stance_schema'`.

- [ ] **Step 3: Write the schema**

Create `src/agents/strategist/stance_schema.py`:
```python
"""TickerStance — the strategist's per-ticker decision substrate.

The strategist emits one `TickerStance` per watchlist ticker. The after-callback
derives `target_weights` / `new_positions` / `close_reasons` / `trim_reasons`
from the stances, so downstream consumers see the same shape they always saw.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TickerStance(BaseModel):
    """One stance per watchlist ticker per tick."""

    ticker: str
    preferred_weight: float = Field(ge=0.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=140)
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=80)
    close_reason: str | None = Field(default=None, max_length=120)
    trim_reason: str | None = Field(default=None, max_length=120)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_stance_schema.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/stance_schema.py tests/unit/strategist/test_stance_schema.py
git commit -m "feat(strategist): add TickerStance schema"
```

---

## Task C2: Add `lifecycle.py` (`derive_lifecycle_action`)

**Files:**
- Create: `src/agents/strategist/lifecycle.py`
- Create: `tests/unit/strategist/test_lifecycle.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_lifecycle.py`:
```python
"""Lifecycle derivation tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from agents.strategist.lifecycle import (
    OPEN_EPSILON, SIZE_CHANGE_EPSILON, derive_lifecycle_action,
)


def test_open_when_current_zero_preferred_above_epsilon():
    assert derive_lifecycle_action(0.0, 0.05) == "open"


def test_close_when_current_above_epsilon_preferred_zero():
    assert derive_lifecycle_action(0.08, 0.0) == "close"


def test_close_when_preferred_below_open_epsilon():
    assert derive_lifecycle_action(0.08, 0.001) == "close"


def test_trim_when_preferred_meaningfully_lower_but_above_zero():
    # current 0.10, preferred 0.05, delta = 0.05 > SIZE_CHANGE_EPSILON
    assert derive_lifecycle_action(0.10, 0.05) == "trim"


def test_add_when_preferred_meaningfully_higher():
    # current 0.05, preferred 0.10, delta = 0.05 > SIZE_CHANGE_EPSILON
    assert derive_lifecycle_action(0.05, 0.10) == "add"


def test_hold_when_change_below_threshold():
    # 0.05 → 0.06, delta 0.01 < SIZE_CHANGE_EPSILON
    assert derive_lifecycle_action(0.05, 0.06) == "hold"


def test_hold_when_both_below_open_epsilon():
    assert derive_lifecycle_action(0.001, 0.002) == "hold"


def test_constants_are_floats():
    assert isinstance(OPEN_EPSILON, float)
    assert isinstance(SIZE_CHANGE_EPSILON, float)
    assert 0.0 < OPEN_EPSILON < SIZE_CHANGE_EPSILON < 1.0


def test_open_at_exact_epsilon_boundary():
    """current = 0, preferred = OPEN_EPSILON exactly → not yet "open" (uses strictly-greater)."""
    assert derive_lifecycle_action(0.0, OPEN_EPSILON) == "hold"


def test_close_at_exact_epsilon_boundary():
    """current ≤ OPEN_EPSILON, preferred = 0 → close (current was held, preferred isn't)."""
    assert derive_lifecycle_action(OPEN_EPSILON, 0.0) == "hold"  # neither was meaningfully held
    # …but if current strictly above OPEN_EPSILON, close fires:
    assert derive_lifecycle_action(OPEN_EPSILON + 0.0001, 0.0) == "close"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_lifecycle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.lifecycle'`.

- [ ] **Step 3: Write the lifecycle module**

Create `src/agents/strategist/lifecycle.py`:
```python
"""Lifecycle action derivation — what is the strategist actually doing?

The strategist emits a `preferred_weight` per ticker. The lifecycle action
falls out of comparing it to the `current_weight`:

- current ≤ ε ∧ preferred > ε       → "open"
- current > ε ∧ preferred ≤ ε       → "close"
- both > ε ∧ preferred + δ < current → "trim"
- both > ε ∧ preferred > current + δ → "add"
- otherwise                          → "hold"

The thresholds prevent micro-adjustments from triggering full-on lifecycle
events. ε guards "is the position effectively flat?"; δ guards "is the size
change meaningful?".
"""
from __future__ import annotations

from typing import Literal

OPEN_EPSILON: float = 0.005
SIZE_CHANGE_EPSILON: float = 0.02

LifecycleAction = Literal["open", "close", "trim", "add", "hold"]


def derive_lifecycle_action(
    current_weight: float, preferred_weight: float
) -> LifecycleAction:
    held = current_weight > OPEN_EPSILON
    wants_held = preferred_weight > OPEN_EPSILON

    if not held and wants_held:
        return "open"
    if held and not wants_held:
        return "close"
    if held and wants_held:
        if preferred_weight + SIZE_CHANGE_EPSILON < current_weight:
            return "trim"
        if preferred_weight > current_weight + SIZE_CHANGE_EPSILON:
            return "add"
    return "hold"
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_lifecycle.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/lifecycle.py tests/unit/strategist/test_lifecycle.py
git commit -m "feat(strategist): add derive_lifecycle_action with OPEN/SIZE_CHANGE epsilons"
```

---

## Task C3: Add `PositionThesis.opened_tick_id` field

**Files:**
- Modify: `src/agents/strategist/schema.py`
- Create: `tests/unit/strategist/test_position_thesis_opened_tick_id.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_position_thesis_opened_tick_id.py`:
```python
"""PositionThesis.opened_tick_id field tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.schema import PositionThesis


def test_opened_tick_id_defaults_to_empty_string():
    pt = PositionThesis(
        ticker="AAPL",
        opened_at=datetime.now(tz=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="x",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    assert pt.opened_tick_id == ""


def test_opened_tick_id_round_trip():
    pt = PositionThesis(
        ticker="AAPL",
        opened_at=datetime.now(tz=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="x",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
        opened_tick_id="tick_2026-05-08T14:00",
    )
    rebuilt = PositionThesis.model_validate(pt.model_dump(mode="json"))
    assert rebuilt.opened_tick_id == "tick_2026-05-08T14:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_position_thesis_opened_tick_id.py -v`
Expected: FAIL — `PositionThesis` has no `opened_tick_id` field.

- [ ] **Step 3: Add the field**

Open `src/agents/strategist/schema.py`. Find the `PositionThesis` class. Add a new field at the bottom of its field list:
```python
    opened_tick_id: str = ""                           # tick that opened this position
```

Don't change any other field, the class structure, or imports.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_position_thesis_opened_tick_id.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run all strategist unit tests for regression**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: All passing.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/schema.py tests/unit/strategist/test_position_thesis_opened_tick_id.py
git commit -m "feat(strategist): add PositionThesis.opened_tick_id (default empty string)"
```

---

## Task C4: Add `derivation.py` (`derive_legacy_fields`)

**Files:**
- Create: `src/agents/strategist/derivation.py`
- Create: `tests/unit/strategist/test_derivation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_derivation.py`:
```python
"""derive_legacy_fields tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.derivation import (
    DerivedFields, TickContext, derive_legacy_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(prices: dict[str, float] | None = None,
         weights: dict[str, float] | None = None) -> TickContext:
    return TickContext(
        tick_id="tick_X",
        decision_tag="x",
        now=datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc),
        current_prices=prices or {},
        current_weights=weights or {},
    )


def test_open_creates_position_thesis():
    stance = TickerStance(
        ticker="AAPL", preferred_weight=0.08, conviction=0.7,
        rationale="open", horizon="swing",
        target_price=210.0, stop_price=185.0,
    )
    ctx = _ctx(prices={"AAPL": 200.0}, weights={})
    out = derive_legacy_fields([stance], ctx)
    assert out.target_weights == {"AAPL": 0.08}
    assert "AAPL" in out.new_positions
    pt = out.new_positions["AAPL"]
    assert pt.opened_at == ctx.now
    assert pt.opened_price == 200.0
    assert pt.opened_tag == "x"
    assert pt.opened_tick_id == "tick_X"
    assert pt.target_price == 210.0
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_close_records_close_reason():
    stance = TickerStance(
        ticker="AAPL", preferred_weight=0.0, conviction=0.5,
        rationale="exit", close_reason="thesis broken",
    )
    ctx = _ctx(weights={"AAPL": 0.08})
    out = derive_legacy_fields([stance], ctx)
    assert out.target_weights == {"AAPL": 0.0}
    assert out.close_reasons == {"AAPL": "thesis broken"}
    assert out.new_positions == {}
    assert out.trim_reasons == {}


def test_trim_records_trim_reason():
    stance = TickerStance(
        ticker="MSFT", preferred_weight=0.05, conviction=0.5,
        rationale="reduce", trim_reason="lock in profits",
    )
    ctx = _ctx(weights={"MSFT": 0.12})
    out = derive_legacy_fields([stance], ctx)
    assert out.target_weights == {"MSFT": 0.05}
    assert out.trim_reasons == {"MSFT": "lock in profits"}
    assert out.new_positions == {}
    assert out.close_reasons == {}


def test_hold_yields_only_target_weight():
    stance = TickerStance(
        ticker="MSFT", preferred_weight=0.06, conviction=0.5, rationale="hold",
    )
    ctx = _ctx(weights={"MSFT": 0.06})
    out = derive_legacy_fields([stance], ctx)
    assert out.target_weights == {"MSFT": 0.06}
    assert out.new_positions == {}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_multiple_stances_aggregate_correctly():
    stances = [
        TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                     rationale="open", horizon="swing",
                     target_price=210.0, stop_price=185.0),
        TickerStance(ticker="MSFT", preferred_weight=0.0, conviction=0.5,
                     rationale="exit", close_reason="rotate"),
        TickerStance(ticker="NVDA", preferred_weight=0.05, conviction=0.5,
                     rationale="trim", trim_reason="overweight"),
    ]
    ctx = _ctx(prices={"AAPL": 200.0, "MSFT": 410.0, "NVDA": 850.0},
               weights={"MSFT": 0.10, "NVDA": 0.15})
    out = derive_legacy_fields(stances, ctx)
    assert out.target_weights == {"AAPL": 0.08, "MSFT": 0.0, "NVDA": 0.05}
    assert "AAPL" in out.new_positions
    assert out.close_reasons == {"MSFT": "rotate"}
    assert out.trim_reasons == {"NVDA": "overweight"}


def test_open_falls_back_to_zero_when_no_price():
    """If prices dict has no entry, opened_price defaults to 0.0 (caller's problem)."""
    stance = TickerStance(
        ticker="AAPL", preferred_weight=0.08, conviction=0.7,
        rationale="open", horizon="swing",
        target_price=210.0, stop_price=185.0,
    )
    ctx = _ctx(prices={}, weights={})
    out = derive_legacy_fields([stance], ctx)
    assert out.new_positions["AAPL"].opened_price == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_derivation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.derivation'`.

- [ ] **Step 3: Write the derivation module**

Create `src/agents/strategist/derivation.py`:
```python
"""Derive legacy decision fields from per-ticker stances.

The strategist's after-callback runs `derive_legacy_fields` to populate
`StrategistDecision.target_weights` / `new_positions` / `close_reasons` /
`trim_reasons` from the LLM-emitted `stances`. Downstream agents (risk_gate,
executor, memory_writer) keep their existing input shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.schema import PositionThesis
from agents.strategist.stance_schema import TickerStance


@dataclass(frozen=True)
class TickContext:
    """Inputs the derivation needs that aren't on the stance itself."""

    tick_id: str
    decision_tag: str
    now: datetime
    current_prices: dict[str, float]
    current_weights: dict[str, float]


@dataclass(frozen=True)
class DerivedFields:
    target_weights: dict[str, float]
    new_positions: dict[str, PositionThesis]
    close_reasons: dict[str, str]
    trim_reasons: dict[str, str]


def derive_legacy_fields(
    stances: Iterable[TickerStance], ctx: TickContext
) -> DerivedFields:
    target_weights: dict[str, float] = {}
    new_positions: dict[str, PositionThesis] = {}
    close_reasons: dict[str, str] = {}
    trim_reasons: dict[str, str] = {}

    for stance in stances:
        target_weights[stance.ticker] = stance.preferred_weight

        current = ctx.current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(current, stance.preferred_weight)

        if action == "open":
            opened_price = ctx.current_prices.get(stance.ticker, 0.0)
            new_positions[stance.ticker] = PositionThesis(
                ticker=stance.ticker,
                opened_at=ctx.now,
                opened_price=opened_price,
                opened_tag=ctx.decision_tag,
                rationale=stance.rationale,
                horizon=stance.horizon or "swing",
                target_price=stance.target_price,
                stop_price=stance.stop_price,
                catalyst=stance.catalyst,
                last_reviewed_at=ctx.now,
                last_review_note="",
                opened_tick_id=ctx.tick_id,
            )
        elif action == "close" and stance.close_reason:
            close_reasons[stance.ticker] = stance.close_reason
        elif action == "trim" and stance.trim_reason:
            trim_reasons[stance.ticker] = stance.trim_reason

    return DerivedFields(
        target_weights=target_weights,
        new_positions=new_positions,
        close_reasons=close_reasons,
        trim_reasons=trim_reasons,
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_derivation.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/derivation.py tests/unit/strategist/test_derivation.py
git commit -m "feat(strategist): add derive_legacy_fields for after-callback derivation"
```

---

## Task C5: Add `held_view.py` (`render_held_positions_view`)

**Files:**
- Create: `src/agents/strategist/held_view.py`
- Create: `tests/unit/strategist/test_held_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_held_view.py`:
```python
"""Held-positions view rendering tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.held_view import render_held_positions_view
from agents.strategist.schema import PositionThesis
from broker.portfolio import Portfolio, Position


def _thesis(ticker: str = "AAPL", opened_price: float = 192.40,
            target_price: float | None = 210.0,
            stop_price: float | None = 185.0,
            catalyst: str | None = "Q3 earnings",
            rationale: str = "FCF + insider",
            horizon: str = "swing") -> PositionThesis:
    return PositionThesis(
        ticker=ticker,
        opened_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
        opened_price=opened_price,
        opened_tag="open_aapl",
        rationale=rationale,
        horizon=horizon,
        target_price=target_price,
        stop_price=stop_price,
        catalyst=catalyst,
        last_reviewed_at=datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc),
    )


def test_empty_portfolio_returns_no_holdings_message():
    pf = Portfolio(cash=1000.0, positions={})
    out = render_held_positions_view(positions={}, portfolio=pf)
    assert "No held positions" in out


def test_single_holding_block_includes_all_required_lines():
    thesis = _thesis()
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf)
    assert "AAPL" in out
    assert "Opened:" in out
    assert "192.40" in out
    assert "Why:" in out
    assert "FCF + insider" in out
    assert "Aim:" in out
    assert "210.00" in out
    assert "185.00" in out
    assert "Horizon:" in out
    assert "swing" in out
    assert "Catalyst:" in out
    assert "Q3 earnings" in out
    assert "Now:" in out
    assert "198.50" in out


def test_pnl_pct_rendered_when_price_available():
    thesis = _thesis(opened_price=200.0)
    pf = Portfolio(
        cash=0.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=200.0, last_price=210.0)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf)
    assert "+5" in out  # 5% gain


def test_no_target_no_stop_renders_none_message():
    thesis = _thesis(target_price=None, stop_price=None, catalyst=None)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf)
    assert "(none set at open)" in out


def test_no_catalyst_omits_catalyst_line():
    thesis = _thesis(catalyst=None)
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis.model_dump(mode="json")}, portfolio=pf)
    assert "Catalyst:" not in out


def test_multiple_holdings_separated_by_blank_line():
    aapl = _thesis(ticker="AAPL").model_dump(mode="json")
    msft = _thesis(ticker="MSFT", opened_price=410.0,
                    rationale="cloud tailwind",
                    target_price=450.0, stop_price=395.0,
                    catalyst=None).model_dump(mode="json")
    pf = Portfolio(
        cash=500.0,
        positions={
            "AAPL": Position(quantity=5.0, avg_cost=192.40, last_price=198.50),
            "MSFT": Position(quantity=2.0, avg_cost=410.0, last_price=415.0),
        },
    )
    out = render_held_positions_view(
        positions={"AAPL": aapl, "MSFT": msft}, portfolio=pf,
    )
    assert "AAPL" in out
    assert "MSFT" in out
    assert "\n\n" in out


def test_accepts_thesis_instance_or_dict():
    thesis_inst = _thesis()
    pf = Portfolio(
        cash=900.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )
    out = render_held_positions_view(positions={"AAPL": thesis_inst}, portfolio=pf)
    assert "AAPL" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_held_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.held_view'`.

- [ ] **Step 3: Write the held-view module**

Create `src/agents/strategist/held_view.py`:
```python
"""Render the Held Positions block injected into the strategist's prompt.

Pulls thesis info from `state["positions"]` (dict[ticker, thesis_dict]) and
live price/weight from `state["portfolio"]` (Portfolio instance or dict).
"""
from __future__ import annotations

from typing import Any

from agents.strategist.schema import PositionThesis
from broker.portfolio import Portfolio


def _coerce_thesis(value: Any) -> PositionThesis:
    if isinstance(value, PositionThesis):
        return value
    return PositionThesis.model_validate(value)


def _coerce_portfolio(value: Any) -> Portfolio:
    if isinstance(value, Portfolio):
        return value
    return Portfolio.model_validate(value)


def _format_one(thesis: PositionThesis, portfolio: Portfolio) -> str:
    ticker = thesis.ticker
    pos = portfolio.positions.get(ticker)
    weights = portfolio.current_weights()
    curr_weight = weights.get(ticker, 0.0)

    lines: list[str] = [ticker]

    opened_str = thesis.opened_at.strftime("%Y-%m-%d %H:%M")
    lines.append(
        f"  Opened:    {opened_str} at ${thesis.opened_price:.2f}, "
        f"weight {curr_weight:.3f}"
    )
    lines.append(f"  Why:       {thesis.rationale}")

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

    lines.append(f"  Horizon:   {thesis.horizon}")

    if thesis.catalyst:
        lines.append(f"  Catalyst:  {thesis.catalyst}")

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
    positions: dict[str, Any], portfolio: Any
) -> str:
    """Render every held position as a structured block. Empty `positions`
    returns a "no holdings" message."""
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

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_held_view.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/held_view.py tests/unit/strategist/test_held_view.py
git commit -m "feat(strategist): add render_held_positions_view"
```

---

## Task C6: Add `evidence_view.py` — render `TickerEvidence` for the prompt

**Files:**
- Create: `src/agents/strategist/evidence_view.py`
- Create: `tests/unit/strategist/test_evidence_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_evidence_view.py`:
```python
"""TickerEvidence prompt rendering tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.evidence_view import render_ticker_evidence
from contract.evidence import AnalystEvidence, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _ev(analyst: str, direction: str, conf: float, features: dict[str, float] | None = None,
        ticker: str = "AAPL") -> AnalystEvidence:
    return AnalystEvidence(
        ticker=ticker, analyst=analyst, features=features or {},
        verdict=AnalystVerdict(direction=direction, confidence=conf, rationale=f"{analyst} {direction}"),
    )


def _te(ticker: str = "AAPL", direction: str = "bullish", magnitude: float = 0.5,
        disagreement: float = 0.1) -> TickerEvidence:
    return TickerEvidence(
        ticker=ticker,
        tick_id="tick_X",
        recorded_at=datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc),
        per_analyst={
            "technical": _ev("technical", direction, 0.7, {"rsi_14": 60.0}, ticker),
            "fundamental": _ev("fundamental", direction, 0.6, {"pe_trailing": 28.5}, ticker),
            "sentiment": _ev("sentiment", direction, 0.5, {"news_count_7d": 5.0}, ticker),
            "smart_money": _ev("smart_money", "neutral", 0.0, {"is_no_data": 1.0}, ticker),
        },
        aggregate=AggregateVerdict(
            direction=direction, magnitude=magnitude,
            weights_used={"technical": 1.0, "fundamental": 1.0, "sentiment": 1.0, "smart_money": 1.0},
        ),
        disagreement_score=disagreement,
    )


def test_empty_evidence_renders_placeholder():
    out = render_ticker_evidence([])
    assert "no evidence" in out.lower() or "(no" in out.lower()


def test_single_ticker_block_contains_all_sections():
    out = render_ticker_evidence([_te()])
    assert "AAPL" in out
    assert "Aggregate" in out or "aggregate" in out
    assert "bullish" in out
    # Per-analyst verdicts visible
    assert "technical" in out.lower()
    assert "fundamental" in out.lower()
    assert "sentiment" in out.lower()
    assert "smart_money" in out.lower()


def test_disagreement_score_rendered():
    out = render_ticker_evidence([_te(disagreement=0.42)])
    assert "0.42" in out or "disagreement" in out.lower()


def test_no_data_smart_money_marked_clearly():
    out = render_ticker_evidence([_te()])
    # The "no data" smart_money should be distinguishable from a 0.0 confidence neutral
    assert "no data" in out.lower() or "no_data" in out.lower() or "n/a" in out.lower()


def test_multiple_tickers_in_output():
    aapl = _te(ticker="AAPL", direction="bullish")
    msft = _te(ticker="MSFT", direction="bearish")
    out = render_ticker_evidence([aapl, msft])
    assert "AAPL" in out
    assert "MSFT" in out


def test_features_visible_in_output():
    out = render_ticker_evidence([_te()])
    # At least some feature values should be visible to the LLM
    assert "rsi_14" in out or "60" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_evidence_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.evidence_view'`.

- [ ] **Step 3: Write the evidence-view module**

Create `src/agents/strategist/evidence_view.py`:
```python
"""Render TickerEvidence as a prompt-ready string for the strategist.

One block per ticker: aggregate direction + magnitude + disagreement, then a
compact per-analyst summary with the locked feature catalogue values.
"""
from __future__ import annotations

from typing import Iterable

from contract.ticker_evidence import TickerEvidence


def _format_features(features: dict[str, float]) -> str:
    if not features:
        return "(no features)"
    return ", ".join(f"{k}={v:.3g}" for k, v in features.items())


def _format_per_analyst(te: TickerEvidence) -> list[str]:
    lines: list[str] = []
    for analyst in ("technical", "fundamental", "sentiment", "smart_money"):
        ev = te.per_analyst.get(analyst)
        if ev is None:
            lines.append(f"  - {analyst:<12} (missing)")
            continue
        if ev.verdict.is_no_data:
            lines.append(f"  - {analyst:<12} no_data")
            continue
        lines.append(
            f"  - {analyst:<12} {ev.verdict.direction:<7} conf={ev.verdict.confidence:.2f}  "
            f"[{_format_features(ev.features)}]  — {ev.verdict.rationale[:60]}"
        )
    return lines


def render_ticker_evidence(items: Iterable[TickerEvidence]) -> str:
    items = list(items)
    if not items:
        return "(no evidence this tick)"
    blocks: list[str] = []
    for te in items:
        block = [
            te.ticker,
            f"  Aggregate: {te.aggregate.direction} (magnitude {te.aggregate.magnitude:.2f}, "
            f"disagreement {te.disagreement_score:.2f})",
        ]
        block.extend(_format_per_analyst(te))
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_evidence_view.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/evidence_view.py tests/unit/strategist/test_evidence_view.py
git commit -m "feat(strategist): add render_ticker_evidence for prompt context"
```

---

## Task C7: Extend `StrategistDecision` with `stances` + `trim_reasons`

**Files:**
- Modify: `src/agents/strategist/schema.py`
- Create: `tests/unit/strategist/test_decision_schema_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_decision_schema_v2.py`:
```python
"""StrategistDecision v2 tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance


def test_decision_with_stances():
    d = StrategistDecision(
        stances=[
            TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                          rationale="open", horizon="swing",
                          target_price=210.0, stop_price=185.0),
        ],
        target_weights={},
        decision_tag="x", reasoning="x", updated_thesis="y",
        confidence=0.6,
    )
    assert len(d.stances) == 1


def test_decision_trim_reasons_default_empty():
    d = StrategistDecision(
        stances=[], target_weights={},
        decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
    )
    assert d.trim_reasons == {}


def test_decision_trim_reasons_round_trip():
    d = StrategistDecision(
        stances=[], target_weights={"MSFT": 0.05},
        decision_tag="trim", reasoning="x", updated_thesis="y",
        confidence=0.5,
        trim_reasons={"MSFT": "lock in profits"},
    )
    rebuilt = StrategistDecision.model_validate(d.model_dump(mode="json"))
    assert rebuilt.trim_reasons == {"MSFT": "lock in profits"}


def test_legacy_fields_preserved():
    d = StrategistDecision(
        stances=[], target_weights={"AAPL": 0.08},
        decision_tag="x", reasoning="x", updated_thesis="y",
        confidence=0.7,
        new_positions={}, close_reasons={},
    )
    assert d.target_weights == {"AAPL": 0.08}
    assert d.new_positions == {}
    assert d.close_reasons == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_schema_v2.py -v`
Expected: FAIL — `StrategistDecision` has no `stances` / `trim_reasons` fields.

- [ ] **Step 3: Add the fields**

Open `src/agents/strategist/schema.py`. Add the import and extend `StrategistDecision`. The class should look like:
```python
"""Strategist output schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agents.strategist.stance_schema import TickerStance


class PositionThesis(BaseModel):
    """Structured rationale for an open position."""

    ticker: str
    opened_at: datetime
    opened_price: float
    opened_tag: str
    rationale: str = Field(max_length=400)
    horizon: Literal["intraday", "swing", "long_term"]
    target_price: float | None = None
    stop_price: float | None   = None
    catalyst: str | None = Field(default=None, max_length=100)
    last_reviewed_at: datetime
    last_review_note: str = Field(default="", max_length=200)
    opened_tick_id: str = ""


class StrategistDecision(BaseModel):
    """Output from one Strategist LLM call.

    The LLM emits `stances` (per-ticker). The after-callback fills in
    target_weights / new_positions / close_reasons / trim_reasons by deriving
    them from the stances, so downstream consumers see the same shape they always saw.
    """

    stances: list[TickerStance] = Field(default_factory=list)

    target_weights: dict[str, float] = Field(default_factory=dict)
    decision_tag: str
    reasoning: str = Field(max_length=300)
    updated_thesis: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    new_positions: dict[str, PositionThesis] = Field(default_factory=dict)
    close_reasons: dict[str, str] = Field(default_factory=dict)
    trim_reasons: dict[str, str] = Field(default_factory=dict)
```

If the existing module had additional content, preserve it. Only the changes described above are required.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_schema_v2.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run all strategist tests for regression**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: All passing.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/schema.py tests/unit/strategist/test_decision_schema_v2.py
git commit -m "feat(strategist): add stances + trim_reasons to StrategistDecision"
```

---

## Task C8: Rewrite the strategist prompt template

**Files:**
- Modify: `src/agents/strategist/prompts.py`
- Create: `tests/unit/strategist/test_prompts_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_prompts_v2.py`:
```python
"""Strategist v2 prompt tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_template_has_held_positions_slot():
    assert "{held_positions_view}" in STRATEGIST_INSTRUCTION


def test_template_has_ticker_evidence_slot():
    assert "{ticker_evidence}" in STRATEGIST_INSTRUCTION


def test_template_has_state_slots():
    assert "{portfolio}" in STRATEGIST_INSTRUCTION
    assert "{memory_buffer}" in STRATEGIST_INSTRUCTION
    assert "{day_digest}" in STRATEGIST_INSTRUCTION
    assert "{thesis}" in STRATEGIST_INSTRUCTION
    assert "{tickers}" in STRATEGIST_INSTRUCTION


def test_template_no_longer_has_legacy_signal_slots():
    """Legacy four-list dump replaced by single ticker_evidence block."""
    assert "{technical_signals}" not in STRATEGIST_INSTRUCTION
    assert "{fundamental_signals}" not in STRATEGIST_INSTRUCTION
    assert "{sentiment_signals}" not in STRATEGIST_INSTRUCTION
    assert "{smart_money_signals}" not in STRATEGIST_INSTRUCTION


def test_template_no_longer_has_active_positions_dump():
    assert "Active Positions: {positions}" not in STRATEGIST_INSTRUCTION


def test_template_instructs_per_ticker_stance_output():
    assert "TickerStance" in STRATEGIST_INSTRUCTION
    assert "preferred_weight" in STRATEGIST_INSTRUCTION
    assert "conviction" in STRATEGIST_INSTRUCTION


def test_template_documents_lifecycle_hint_rules():
    text = STRATEGIST_INSTRUCTION
    assert "OPEN" in text and "CLOSE" in text and "TRIM" in text
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
        ticker_evidence="AAPL\n  Aggregate: bullish (magnitude 0.42)",
        tickers="['AAPL','MSFT']",
    )
    assert "No held positions" in rendered
    assert "AAPL" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_prompts_v2.py -v`
Expected: FAIL — current prompt lacks `{ticker_evidence}` and `{held_positions_view}`.

- [ ] **Step 3: Replace the prompt template**

Replace `src/agents/strategist/prompts.py`:
```python
"""Strategist v2 prompt template.

Renders held-position context inline so the model sees what it bought, why, and
the targets/stops set on entry. Inputs the per-ticker `TickerEvidence` (built by
the deterministic digest in `contract.digest`) instead of four flat per-analyst
signal lists. Output is a list[TickerStance] exhaustive over the watchlist.
"""

STRATEGIST_INSTRUCTION = """
You are the portfolio strategist for an algorithmic trading bot. You decide a
per-ticker stance for the next trading hour.

## Current State
Portfolio:    {portfolio}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest:   {day_digest}
Thesis:       {thesis}

## Held Positions (your prior decisions)
{held_positions_view}

## Ticker Evidence (digested per-ticker — already aggregated across analysts)
{ticker_evidence}

## Your Job
Emit a TickerStance for EVERY watchlist ticker: {tickers}.

Per stance:
- preferred_weight ∈ [0,1]: your ideal portfolio weight next tick.
- conviction ∈ [0,1]: how strongly you hold this view.
- rationale: ≤140 chars, why.
- If proposing to OPEN (current ≈ 0 → preferred > 0): include horizon,
  target_price, stop_price; catalyst optional.
- If proposing to CLOSE (current > 0 → preferred ≈ 0): include close_reason.
- If proposing to TRIM (current > 0 → preferred meaningfully lower but still
  held): include trim_reason.
- If holding or adding: lifecycle hint fields stay null.

Treat the digested aggregate as a deterministic input; you may disagree with it
based on context (held position thesis, memory, day digest) — call out the
disagreement in your rationale when you do.

Also emit at the decision level:
- decision_tag (snake_case, ≤40 chars): this tick's headline decision.
- reasoning (≤300 chars): overall summary across all stances.
- updated_thesis (≤500 chars): working hypothesis for next tick.
- confidence ∈ [0,1]: overall conviction in this tick's plan.

Watchlist: {tickers}
"""
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_prompts_v2.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/prompts.py tests/unit/strategist/test_prompts_v2.py
git commit -m "feat(strategist): rewrite prompt for held-positions + ticker_evidence + per-stance output"
```

---

## Task C9: Rewrite the strategist agent (callbacks + wiring)

**Files:**
- Modify: `src/agents/strategist/agent.py` (full replacement)
- Create: `tests/unit/strategist/test_strategist_callbacks_v2.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/strategist/test_strategist_callbacks_v2.py`:
```python
"""Strategist v2 before/after callback tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.agent import (
    _evidence_view_before_callback,
    _held_view_before_callback,
    _strategist_validation_callback,
)
from agents.strategist.schema import PositionThesis, StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio, Position
from contract.evidence import AnalystEvidence, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


class _State(dict): pass


class _Ctx:
    def __init__(self, state: dict):
        self.state = state


def _portfolio(holdings: dict | None = None, cash: float = 1000.0) -> Portfolio:
    return Portfolio(
        cash=cash,
        positions={t: Position(quantity=q, avg_cost=ac, last_price=lp)
                   for t, (q, ac, lp) in (holdings or {}).items()},
    )


def _ev(analyst: str, direction: str = "neutral", conf: float = 0.0,
        ticker: str = "AAPL") -> AnalystEvidence:
    return AnalystEvidence(
        ticker=ticker, analyst=analyst, features={},
        verdict=AnalystVerdict(direction=direction, confidence=conf, rationale="x"),
    )


def _te(ticker: str = "AAPL") -> TickerEvidence:
    return TickerEvidence(
        ticker=ticker, tick_id="t",
        recorded_at=datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc),
        per_analyst={a: _ev(a, "neutral", 0.0, ticker) for a in
                      ("technical", "fundamental", "sentiment", "smart_money")},
        aggregate=AggregateVerdict(direction="neutral", magnitude=0.0,
                                   weights_used={"technical": 1.0, "fundamental": 1.0,
                                                 "sentiment": 1.0, "smart_money": 1.0}),
        disagreement_score=0.0,
    )


# ── before callback: held view ────────────────────────────────────────────────


def test_before_callback_renders_no_holdings_message():
    state = _State(positions={}, portfolio=_portfolio().model_dump(mode="json"))
    _held_view_before_callback(_Ctx(state))
    assert "No held positions" in state["held_positions_view"]


def test_before_callback_renders_full_view_with_holdings():
    thesis = PositionThesis(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 22, 14, tzinfo=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="x",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
        last_reviewed_at=datetime(2026, 4, 22, 14, tzinfo=timezone.utc),
    )
    state = _State(
        positions={"AAPL": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"AAPL": (10.0, 192.40, 198.50)}).model_dump(mode="json"),
    )
    _held_view_before_callback(_Ctx(state))
    assert "AAPL" in state["held_positions_view"]
    assert "192.40" in state["held_positions_view"]


# ── before callback: ticker_evidence rendering ───────────────────────────────


def test_evidence_view_callback_builds_ticker_evidence_from_per_analyst_state():
    """The pipeline writes per-analyst evidence to state[{analyst}_evidence];
    the callback assembles them into a TickerEvidence per ticker and renders."""
    state = _State(
        tickers=["AAPL"],
        tick_id="t",
        recorded_at="2026-05-08T14:00:00Z",
        technical_evidence=[_ev("technical", "bullish", 0.6).model_dump(mode="json")],
        fundamental_evidence=[_ev("fundamental", "bullish", 0.5).model_dump(mode="json")],
        sentiment_evidence=[_ev("sentiment", "neutral", 0.3).model_dump(mode="json")],
        smart_money_evidence=[_ev("smart_money", "neutral", 0.0).model_dump(mode="json")],
    )
    _evidence_view_before_callback(_Ctx(state))
    rendered = state["ticker_evidence"]
    assert isinstance(rendered, str)
    assert "AAPL" in rendered
    assert "Aggregate" in rendered or "aggregate" in rendered


# ── after callback: missing tickers ───────────────────────────────────────────


def test_after_reprompts_on_missing_tickers():
    state = _State(
        tickers=["AAPL", "MSFT"],
        positions={},
        portfolio=_portfolio().model_dump(mode="json"),
        tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[TickerStance(ticker="AAPL", preferred_weight=0.0,
                                  conviction=0.5, rationale="hold")],
            decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    assert "MSFT" in out.parts[0].text


def test_after_reprompts_on_extras():
    state = _State(
        tickers=["AAPL"],
        positions={}, portfolio=_portfolio().model_dump(mode="json"), tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5, rationale="hold"),
                TickerStance(ticker="GOOG", preferred_weight=0.05, conviction=0.7,
                             rationale="open", horizon="swing",
                             target_price=200.0, stop_price=170.0),
            ],
            decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    assert "GOOG" in out.parts[0].text


def test_after_reprompts_on_open_without_lifecycle_fields():
    state = _State(
        tickers=["AAPL"],
        positions={}, portfolio=_portfolio().model_dump(mode="json"), tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[TickerStance(ticker="AAPL", preferred_weight=0.05,
                                  conviction=0.7, rationale="open")],
            decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    text = out.parts[0].text
    assert "AAPL" in text
    assert ("horizon" in text or "target_price" in text or "stop_price" in text)


def test_after_reprompts_on_close_without_close_reason():
    thesis = PositionThesis(
        ticker="AAPL", opened_at=datetime.now(tz=timezone.utc),
        opened_price=192.40, opened_tag="x", rationale="x", horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    state = _State(
        tickers=["AAPL"],
        positions={"AAPL": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"AAPL": (10.0, 192.40, 198.50)}).model_dump(mode="json"),
        tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[TickerStance(ticker="AAPL", preferred_weight=0.0,
                                  conviction=0.5, rationale="exit")],
            decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    assert "close_reason" in out.parts[0].text


def test_after_reprompts_on_trim_without_trim_reason():
    thesis = PositionThesis(
        ticker="MSFT", opened_at=datetime.now(tz=timezone.utc),
        opened_price=410.0, opened_tag="x", rationale="x", horizon="swing",
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    state = _State(
        tickers=["MSFT"],
        positions={"MSFT": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"MSFT": (10.0, 410.0, 415.0)}, cash=500).model_dump(mode="json"),
        tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[TickerStance(ticker="MSFT", preferred_weight=0.30,
                                  conviction=0.5, rationale="reduce")],
            decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is not None
    assert "trim_reason" in out.parts[0].text


def test_after_derives_legacy_fields_on_valid_input():
    state = _State(
        tickers=["AAPL"],
        positions={}, portfolio=_portfolio().model_dump(mode="json"), tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[TickerStance(ticker="AAPL", preferred_weight=0.05,
                                  conviction=0.7, rationale="open", horizon="swing",
                                  target_price=210.0, stop_price=185.0)],
            decision_tag="open_aapl", reasoning="x", updated_thesis="y", confidence=0.7,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is None
    decided = state["strategist_decision"]
    assert decided["target_weights"] == {"AAPL": 0.05}
    assert "AAPL" in decided["new_positions"]
    assert decided["new_positions"]["AAPL"]["opened_tick_id"] == "tick_X"
    assert decided["close_reasons"] == {}
    assert decided["trim_reasons"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_strategist_callbacks_v2.py -v`
Expected: FAIL — `_evidence_view_before_callback` and the v2 versions of the other callbacks don't exist.

- [ ] **Step 3: Rewrite the agent module**

Replace `src/agents/strategist/agent.py` (full replacement):
```python
"""Strategist v2 LlmAgent — emits per-ticker TickerStance, derives legacy fields server-side."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.strategist.derivation import TickContext, derive_legacy_fields
from agents.strategist.evidence_view import render_ticker_evidence
from agents.strategist.held_view import render_held_positions_view
from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.prompts import STRATEGIST_INSTRUCTION
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio
from config.digest import DEFAULT_ANALYST_WEIGHTS
from contract.digest import build_ticker_evidence
from contract.evidence import AnalystEvidence
from contract.ticker_evidence import TickerEvidence


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
    portfolio = _coerce_portfolio(state.get("portfolio"))
    state["held_positions_view"] = render_held_positions_view(positions, portfolio)
    return None


def _evidence_view_before_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Build TickerEvidence per ticker from the per-analyst evidence lists, then render."""
    state = callback_context.state
    tickers: list[str] = state.get("tickers", []) or []
    tick_id: str = state.get("tick_id", "unknown")
    recorded_at_raw = state.get("recorded_at")
    recorded_at = (
        datetime.fromisoformat(recorded_at_raw.replace("Z", "+00:00"))
        if isinstance(recorded_at_raw, str)
        else (recorded_at_raw or datetime.now(tz=timezone.utc))
    )

    def _index(key: str) -> dict[str, AnalystEvidence]:
        items = state.get(key, []) or []
        out: dict[str, AnalystEvidence] = {}
        for item in items:
            ev = AnalystEvidence.model_validate(item) if isinstance(item, dict) else item
            out[ev.ticker] = ev
        return out

    tech = _index("technical_evidence")
    fund = _index("fundamental_evidence")
    sent = _index("sentiment_evidence")
    sm = _index("smart_money_evidence")

    ticker_evidence: list[TickerEvidence] = []
    for t in tickers:
        per_analyst: dict[str, AnalystEvidence] = {}
        if t in tech:
            per_analyst["technical"] = tech[t]
        if t in fund:
            per_analyst["fundamental"] = fund[t]
        if t in sent:
            per_analyst["sentiment"] = sent[t]
        if t in sm:
            per_analyst["smart_money"] = sm[t]
        te = build_ticker_evidence(
            per_analyst=per_analyst,
            ticker=t,
            tick_id=tick_id,
            recorded_at=recorded_at,
            weights=DEFAULT_ANALYST_WEIGHTS,
        )
        ticker_evidence.append(te)

    state["ticker_evidence_objects"] = [te.model_dump(mode="json") for te in ticker_evidence]
    state["ticker_evidence"] = render_ticker_evidence(ticker_evidence)
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
    current_prices = {t: pos.last_price for t, pos in portfolio.positions.items()}
    tick_id: str = state.get("tick_id") or state.get("recorded_at", "unknown")

    # 1) Exhaustive
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

    # 3) Lifecycle hint enforcement
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
                    f"Stance for {stance.ticker} closes a position but is missing close_reason."
                )
        elif action == "trim":
            if not stance.trim_reason:
                return _reprompt(
                    f"Stance for {stance.ticker} trims a position but is missing trim_reason."
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

    state["strategist_decision"] = decision.model_dump(mode="json")
    return None


def _composite_before_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Run held-view + evidence-view in sequence."""
    out = _held_view_before_callback(callback_context)
    if out is not None:
        return out
    return _evidence_view_before_callback(callback_context)


strategist_agent = LlmAgent(
    name="Strategist",
    model="gemini-2.0-pro-001",
    instruction=STRATEGIST_INSTRUCTION,
    output_schema=StrategistDecision,
    output_key="strategist_decision",
    before_agent_callback=_composite_before_callback,
    after_agent_callback=_strategist_validation_callback,
)
```

- [ ] **Step 4: Run the v2 callback tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_strategist_callbacks_v2.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run all strategist tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: All passing.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/agent.py tests/unit/strategist/test_strategist_callbacks_v2.py
git commit -m "feat(strategist): rewrite agent with held-view + ticker-evidence + per-stance validation"
```

---

## Task C10: Add `TickerStanceRow` + `save_ticker_stance`

**Files:**
- Modify: `src/orchestrator/persistence.py`
- Create: `tests/unit/orchestrator/test_persistence_ticker_stance.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/orchestrator/test_persistence_ticker_stance.py`:
```python
"""TickerStanceRow tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, TickerStanceRow, save_ticker_stance


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session()


def test_round_trip(db):
    save_ticker_stance(
        db, tick_id="tick_X", decision_tag="open_aapl",
        recorded_at=datetime(2026, 5, 8, 14, tzinfo=timezone.utc),
        stance={
            "ticker": "AAPL", "preferred_weight": 0.08, "conviction": 0.7,
            "rationale": "FCF + insider", "horizon": "swing",
            "target_price": 210.0, "stop_price": 185.0,
            "catalyst": "Q3", "close_reason": None, "trim_reason": None,
        },
        lifecycle_action="open",
    )
    db.commit()
    rows = db.query(TickerStanceRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.tick_id == "tick_X"
    assert r.ticker == "AAPL"
    assert r.preferred_weight == 0.08
    assert r.lifecycle_action == "open"
    assert r.decision_tag == "open_aapl"


def test_nullable_lifecycle_fields(db):
    save_ticker_stance(
        db, tick_id="tick_X", decision_tag="hold_msft",
        recorded_at=datetime(2026, 5, 8, 14, tzinfo=timezone.utc),
        stance={
            "ticker": "MSFT", "preferred_weight": 0.05, "conviction": 0.6,
            "rationale": "still cheap", "horizon": None,
            "target_price": None, "stop_price": None,
            "catalyst": None, "close_reason": None, "trim_reason": None,
        },
        lifecycle_action="hold",
    )
    db.commit()
    r = db.query(TickerStanceRow).first()
    assert r.horizon is None
    assert r.target_price is None
    assert r.lifecycle_action == "hold"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_persistence_ticker_stance.py -v`
Expected: FAIL — `TickerStanceRow` and `save_ticker_stance` don't exist.

- [ ] **Step 3: Add the row + helper**

Open `src/orchestrator/persistence.py`. Append (after the existing `TradeLogRow`, before any helper functions like `make_engine`):
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
    session,
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

If the existing file's imports don't already cover `Mapped`, `mapped_column`, `Integer`, `String`, `Float`, `DateTime`, add them with the existing import block (it should already use SQLAlchemy 2 syntax — match the pattern from `TradeLogRow`).

- [ ] **Step 4: Verify schema creates cleanly**

Run: `.venv/Scripts/python -c "from sqlalchemy import create_engine; from orchestrator.persistence import Base; e = create_engine('sqlite://'); Base.metadata.create_all(e); print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_persistence_ticker_stance.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run all persistence tests**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/ -v`
Expected: All passing.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/persistence.py tests/unit/orchestrator/test_persistence_ticker_stance.py
git commit -m "feat(persistence): add TickerStanceRow + save_ticker_stance"
```

---

## Task C11: Add `TradeLogRow.opening_tick_id` / `closing_tick_id`

**Files:**
- Modify: `src/orchestrator/persistence.py`
- Create: `tests/unit/orchestrator/test_trade_log_tick_id_fks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/orchestrator/test_trade_log_tick_id_fks.py`:
```python
"""TradeLogRow.opening_tick_id / closing_tick_id tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, TickerStanceRow, TradeLogRow


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session()


def test_trade_log_accepts_tick_id_fks(db):
    db.add(TradeLogRow(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=timezone.utc),
        closed_at=datetime(2026, 5, 8, 14, tzinfo=timezone.utc),
        opened_price=192.40, closed_price=210.0,
        pnl_dollar=88.0, pnl_pct=9.13,
        holding_period_hours=504,
        horizon_intent="swing",
        opened_tag="open_aapl", closed_tag="close_aapl",
        opened_rationale="x", close_reason="target",
        catalyst_realised=False,
        opening_tick_id="tick_OPEN", closing_tick_id="tick_CLOSE",
    ))
    db.commit()
    r = db.query(TradeLogRow).first()
    assert r.opening_tick_id == "tick_OPEN"
    assert r.closing_tick_id == "tick_CLOSE"


def test_trade_log_join_to_ticker_stance(db):
    """Closed-trade outcomes can be joined back to the deliberation that opened them."""
    db.add(TickerStanceRow(
        tick_id="tick_OPEN", recorded_at=datetime(2026, 4, 1, 14, tzinfo=timezone.utc),
        ticker="AAPL", preferred_weight=0.08, conviction=0.7, rationale="x",
        horizon="swing", target_price=210.0, stop_price=185.0,
        catalyst=None, close_reason=None, trim_reason=None,
        lifecycle_action="open", decision_tag="open_aapl",
    ))
    db.add(TradeLogRow(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=timezone.utc),
        closed_at=datetime(2026, 5, 8, 14, tzinfo=timezone.utc),
        opened_price=192.40, closed_price=210.0,
        pnl_dollar=88.0, pnl_pct=9.13,
        holding_period_hours=504,
        horizon_intent="swing",
        opened_tag="open_aapl", closed_tag="close_aapl",
        opened_rationale="x", close_reason="target",
        catalyst_realised=False,
        opening_tick_id="tick_OPEN", closing_tick_id="tick_CLOSE",
    ))
    db.commit()
    joined = (
        db.query(TradeLogRow, TickerStanceRow)
        .filter(TradeLogRow.opening_tick_id == TickerStanceRow.tick_id)
        .filter(TradeLogRow.ticker == TickerStanceRow.ticker)
        .all()
    )
    assert len(joined) == 1
    trade, stance = joined[0]
    assert trade.ticker == "AAPL"
    assert stance.lifecycle_action == "open"


def test_tick_id_columns_nullable(db):
    """Old rows pre-Plan-C will have NULL tick IDs — must not break existing queries."""
    db.add(TradeLogRow(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=timezone.utc),
        closed_at=datetime(2026, 5, 8, 14, tzinfo=timezone.utc),
        opened_price=192.40, closed_price=210.0,
        pnl_dollar=88.0, pnl_pct=9.13,
        holding_period_hours=504,
        horizon_intent="swing",
        opened_tag="x", closed_tag="x",
        opened_rationale="x", close_reason="x",
        catalyst_realised=False,
        opening_tick_id=None, closing_tick_id=None,
    ))
    db.commit()
    r = db.query(TradeLogRow).first()
    assert r.opening_tick_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_trade_log_tick_id_fks.py -v`
Expected: FAIL — `TradeLogRow` lacks the new columns.

- [ ] **Step 3: Add the columns**

Open `src/orchestrator/persistence.py`. Find the `TradeLogRow` class. Add at the bottom of its column list (preserving the rest):
```python
    opening_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    closing_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_trade_log_tick_id_fks.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/persistence.py tests/unit/orchestrator/test_trade_log_tick_id_fks.py
git commit -m "feat(persistence): add TradeLogRow.opening_tick_id / closing_tick_id (nullable, indexed)"
```

---

## Task C12: Add `StrategistDecisionWriter` agent

**Files:**
- Create: `src/agents/strategist/decision_writer.py`
- Create: `tests/unit/strategist/test_decision_writer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_decision_writer.py`:
```python
"""StrategistDecisionWriter tests — Tier 1, no LLM."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.strategist.decision_writer import (
    StrategistDecisionWriter, build_strategist_decision_writer,
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
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session()


def test_writes_one_row_per_stance(db):
    decision = StrategistDecision(
        stances=[
            TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                         rationale="open", horizon="swing",
                         target_price=210.0, stop_price=185.0),
            TickerStance(ticker="NVDA", preferred_weight=0.0, conviction=0.8,
                         rationale="exit", close_reason="thesis broken"),
        ],
        target_weights={"AAPL": 0.08, "NVDA": 0.0},
        decision_tag="rotation", reasoning="x", updated_thesis="y", confidence=0.65,
    )
    portfolio = Portfolio(
        cash=900.0,
        positions={"NVDA": Position(quantity=1.0, avg_cost=900.0, last_price=850.0)},
    )
    state = {
        "tick_id": "tick_X",
        "strategist_decision": decision.model_dump(mode="json"),
        "portfolio": portfolio.model_dump(mode="json"),
    }
    writer = StrategistDecisionWriter(db_session=db)
    _run(writer._run_async_impl(_StubCtx(state)))
    db.commit()

    rows = db.query(TickerStanceRow).all()
    assert len(rows) == 2
    by_ticker = {r.ticker: r for r in rows}
    assert by_ticker["AAPL"].lifecycle_action == "open"
    assert by_ticker["NVDA"].lifecycle_action == "close"
    assert all(r.decision_tag == "rotation" for r in rows)


def test_no_op_without_decision(db):
    state = {"tick_id": "t", "strategist_decision": None,
             "portfolio": Portfolio(cash=100.0).model_dump(mode="json")}
    writer = StrategistDecisionWriter(db_session=db)
    _run(writer._run_async_impl(_StubCtx(state)))
    db.commit()
    assert db.query(TickerStanceRow).count() == 0


def test_no_op_without_db_session():
    state = {
        "tick_id": "t",
        "strategist_decision": StrategistDecision(
            stances=[TickerStance(ticker="AAPL", preferred_weight=0.0,
                                  conviction=0.5, rationale="hold")],
            target_weights={"AAPL": 0.0},
            decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
        "portfolio": Portfolio(cash=100.0).model_dump(mode="json"),
    }
    writer = StrategistDecisionWriter(db_session=None)
    _run(writer._run_async_impl(_StubCtx(state)))  # must not raise


def test_factory_returns_agent(db):
    agent = build_strategist_decision_writer(db)
    assert isinstance(agent, StrategistDecisionWriter)
    assert agent.db_session is db
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.strategist.decision_writer'`.

- [ ] **Step 3: Write the writer**

Create `src/agents/strategist/decision_writer.py`:
```python
"""Persists per-ticker stances to TickerStanceRow.

Runs between strategist and risk_gate so the council's intent is recorded even
if risk_gate raises a contract violation downstream.
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
    """One TickerStanceRow per stance per tick."""

    name: str = "StrategistDecisionWriter"
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if False:  # generator protocol; never actually emits events
            yield  # type: ignore[unreachable]

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
    return StrategistDecisionWriter(db_session=db_session)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_decision_writer.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/decision_writer.py tests/unit/strategist/test_decision_writer.py
git commit -m "feat(strategist): add StrategistDecisionWriter for per-ticker stance persistence"
```

---

## Task C13: Update executor — write thesis on BUY + populate trade-log FKs

**Files:**
- Modify: `src/agents/executor/agent.py`
- Create: `tests/unit/executor/__init__.py` (empty if missing)
- Create: `tests/unit/executor/test_open_positions_state.py`

- [ ] **Step 1: Ensure `tests/unit/executor/__init__.py` exists**

If missing, create `tests/unit/executor/__init__.py` empty.

- [ ] **Step 2: Read the existing executor**

Read `src/agents/executor/agent.py` to understand the current flow — what state keys it reads/writes, how it submits orders, how it handles `BrokerRejection`. Match its existing patterns when applying the changes below.

- [ ] **Step 3: Write the failing test**

Create `tests/unit/executor/test_open_positions_state.py`:
```python
"""Executor v2 tests — state["positions"] BUY-side write + TradeLog FKs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.executor.agent import ExecutorAgent
from broker.fake import FakeBroker
from orchestrator.persistence import Base, TradeLogRow
from orchestrator.state import Order


class _StubCtx:
    def __init__(self, state: dict):
        class _S: pass
        self.session = _S()
        self.session.state = state


def _run(coro_gen):
    async def _drain():
        return [ev async for ev in coro_gen]
    return asyncio.run(_drain())


def test_buy_writes_thesis_to_state_positions():
    """On BUY, the executor copies new_positions[ticker] into state["positions"][ticker]."""
    broker = FakeBroker(seed_cash=10_000.0, fills_at={"AAPL": 200.0})
    state = {
        "tick_id": "tick_X",
        "final_orders": [Order(ticker="AAPL", action="BUY", quantity=5, est_price=200.0).model_dump()],
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
    aapl = state["positions"]["AAPL"]
    assert aapl["opened_tick_id"] == "tick_X"
    assert aapl["target_price"] == 220.0


def test_sell_removes_ticker_from_state_positions():
    broker = FakeBroker(seed_cash=0.0, fills_at={"AAPL": 220.0},
                        seed_positions={"AAPL": (5.0, 200.0)})
    aapl_thesis = {
        "ticker": "AAPL",
        "opened_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "opened_price": 200.0, "opened_tag": "open_aapl",
        "rationale": "x", "horizon": "swing",
        "target_price": 220.0, "stop_price": 190.0,
        "catalyst": None,
        "last_reviewed_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "last_review_note": "",
        "opened_tick_id": "tick_OPEN",
    }
    state = {
        "tick_id": "tick_CLOSE",
        "final_orders": [Order(ticker="AAPL", action="SELL", quantity=5, est_price=220.0).model_dump()],
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


def test_sell_writes_tick_id_fks_to_trade_log(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    broker = FakeBroker(seed_cash=0.0, fills_at={"AAPL": 220.0},
                        seed_positions={"AAPL": (5.0, 200.0)})
    aapl_thesis = {
        "ticker": "AAPL",
        "opened_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "opened_price": 200.0, "opened_tag": "open_aapl",
        "rationale": "x", "horizon": "swing",
        "target_price": 220.0, "stop_price": 190.0,
        "catalyst": None,
        "last_reviewed_at": datetime(2026, 4, 1, 14, tzinfo=timezone.utc).isoformat(),
        "last_review_note": "",
        "opened_tick_id": "tick_OPEN",
    }
    state = {
        "tick_id": "tick_CLOSE",
        "final_orders": [Order(ticker="AAPL", action="SELL", quantity=5, est_price=220.0).model_dump()],
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

If `FakeBroker` doesn't accept `seed_positions` / `fills_at` kwargs, look at the existing test fixtures for FakeBroker (`tests/unit/broker/`) and adjust the constructor calls or add the missing constructor support to FakeBroker. Keep changes minimal.

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/executor/test_open_positions_state.py -v`
Expected: FAIL — executor doesn't currently write `state["positions"][ticker]` on BUY; the trade-log helper doesn't populate `opening_tick_id` / `closing_tick_id`.

- [ ] **Step 5: Update the executor**

Open `src/agents/executor/agent.py`. Replace the `_run_async_impl` body's order-loop logic so that:

1. Inside the BUY branch, after a successful fill: `positions[order.ticker] = decision.get("new_positions", {}).get(order.ticker)` (only if non-None).
2. Inside the SELL branch, after the existing trade-log write logic: include `opening_tick_id` (read from `thesis["opened_tick_id"]` if dict, else `getattr(thesis, "opened_tick_id", "")`) and `closing_tick_id = state["tick_id"]` in whatever dict / row construction the trade-log save uses.

Reference implementation (use as a guide; adapt to the existing helper in your project, e.g. `save_trade_log_entry` if it exists, or direct ORM inserts):
```python
# Inside the for-order loop, after a successful fill:
if order.action == "BUY":
    thesis_dict = (decision.get("new_positions") or {}).get(order.ticker)
    if thesis_dict is not None:
        positions[order.ticker] = thesis_dict

elif order.action == "SELL" and order.ticker in positions:
    thesis = positions.get(order.ticker)
    if thesis and self.db_session:
        from orchestrator.persistence import save_trade_log_entry
        opened_price = thesis.get("opened_price") if isinstance(thesis, dict) else thesis.opened_price
        opened_at = thesis.get("opened_at") if isinstance(thesis, dict) else thesis.opened_at
        opened_tick = thesis.get("opened_tick_id") if isinstance(thesis, dict) else getattr(thesis, "opened_tick_id", "")
        opened_tag_val = thesis.get("opened_tag") if isinstance(thesis, dict) else thesis.opened_tag
        opened_rationale_val = thesis.get("rationale") if isinstance(thesis, dict) else thesis.rationale
        horizon_val = thesis.get("horizon") if isinstance(thesis, dict) else thesis.horizon
        closed_at = datetime.now(tz=timezone.utc)
        opened_at_dt = (
            datetime.fromisoformat(opened_at) if isinstance(opened_at, str) else opened_at
        )
        holding_hours = int((closed_at - opened_at_dt).total_seconds() / 3600)
        pnl_pct = (fill.price - opened_price) / opened_price * 100

        save_trade_log_entry(self.db_session, {
            "ticker":               order.ticker,
            "opened_at":            opened_at_dt,
            "closed_at":            closed_at,
            "opened_price":         opened_price,
            "closed_price":         fill.price,
            "pnl_dollar":           (fill.price - opened_price) * fill.quantity,
            "pnl_pct":              pnl_pct,
            "holding_period_hours": holding_hours,
            "horizon_intent":       horizon_val,
            "opened_tag":           opened_tag_val,
            "closed_tag":           decision.get("decision_tag", "unknown"),
            "opened_rationale":     opened_rationale_val,
            "close_reason":         (decision.get("close_reasons") or {}).get(order.ticker, ""),
            "catalyst_realised":    False,
            "opening_tick_id":      opened_tick or None,
            "closing_tick_id":      state.get("tick_id"),
        })
        del positions[order.ticker]
```

If `save_trade_log_entry` doesn't exist in your `persistence.py`, add a tiny helper that takes a dict and creates a `TradeLogRow` (mirror the pattern of `save_ticker_stance` from Task C10). If the executor currently calls a different name for the helper, keep that name and just add the two new dict keys.

- [ ] **Step 6: Run executor tests**

Run: `.venv/Scripts/python -m pytest tests/unit/executor/test_open_positions_state.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run full executor regression**

Run: `.venv/Scripts/python -m pytest tests/ -v -k "executor"`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agents/executor/agent.py src/orchestrator/persistence.py tests/unit/executor/__init__.py tests/unit/executor/test_open_positions_state.py
git commit -m "feat(executor): write thesis on BUY + populate TradeLog tick_id FKs"
```

---

## Task C14: Wire `StrategistDecisionWriter` into the pipeline

**Files:**
- Modify: `src/orchestrator/pipeline.py`
- Create: `tests/unit/orchestrator/test_pipeline_wiring_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/orchestrator/test_pipeline_wiring_v2.py`:
```python
"""Pipeline v2 wiring tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from broker.fake import FakeBroker
from orchestrator.pipeline import build_pipeline


def test_pipeline_includes_strategist_decision_writer():
    pipe = build_pipeline(broker=FakeBroker(seed_cash=1000.0), db_session=None)
    names = [a.name for a in pipe.sub_agents]
    assert "Strategist" in names
    assert "StrategistDecisionWriter" in names
    rg_name = "RiskGate" if "RiskGate" in names else "RiskGateAgent"
    assert rg_name in names
    si = names.index("Strategist")
    wi = names.index("StrategistDecisionWriter")
    rg = names.index(rg_name)
    assert si < wi < rg


def test_pipeline_stage_count_increased_by_one():
    """The decision writer adds one stage."""
    pipe = build_pipeline(broker=FakeBroker(seed_cash=1000.0), db_session=None)
    # Pre-Plan-C count was 7 (analyst_pool, attribution_writer, strategist, risk_gate,
    # executor, memory_writer, snapshotter). Plan C adds StrategistDecisionWriter → 8.
    assert len(pipe.sub_agents) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_pipeline_wiring_v2.py -v`
Expected: FAIL — pipeline currently has 7 stages.

- [ ] **Step 3: Read the existing pipeline**

Read `src/orchestrator/pipeline.py` to confirm the current `build_pipeline` signature and the existing stage names.

- [ ] **Step 4: Wire the writer**

Edit `build_pipeline` in `src/orchestrator/pipeline.py`. Add the import and insert the new stage between `Strategist` and `RiskGate`:
```python
def build_pipeline(broker, db_session=None) -> SequentialAgent:
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


def _build_strategist():
    """Build a fresh Strategist LlmAgent (with v2 callbacks)."""
    from google.adk.agents import LlmAgent
    from agents.strategist.agent import (
        _composite_before_callback,
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
        before_agent_callback=_composite_before_callback,
        after_agent_callback=_strategist_validation_callback,
    )
```

If `_build_strategist`'s structure in the existing pipeline differs (e.g. it takes args, lives elsewhere), preserve that signature and only swap in the new callbacks. The exact stage name `RiskGate` vs `RiskGateAgent` should not change here — preserve whatever name the existing code uses.

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/orchestrator/test_pipeline_wiring_v2.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run all unit tests for full regression**

Run: `.venv/Scripts/python -m pytest tests/unit/ -v`
Expected: All passing.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/pipeline.py tests/unit/orchestrator/test_pipeline_wiring_v2.py
git commit -m "feat(pipeline): wire StrategistDecisionWriter and v2 strategist callbacks"
```

---

## Task C15: Tier 2 LLM-touching smoke (gated)

**Files:**
- Create: `tests/integration/__init__.py` (empty if missing)
- Create: `tests/integration/test_strategist_v2_smoke.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/integration/test_strategist_v2_smoke.py`:
```python
"""Strategist v2 smoke — Tier 2, real LLM. Gated by RUN_LLM_TESTS env var."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from agents.strategist.agent import strategist_agent
from agents.strategist.schema import PositionThesis, StrategistDecision
from broker.portfolio import Portfolio, Position
from contract.evidence import AnalystEvidence, AnalystVerdict


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_TESTS") != "1",
    reason="LLM-touching test; set RUN_LLM_TESTS=1 to run",
)


@pytest.mark.integration
def test_strategist_v2_emits_per_ticker_stances_with_held_position():
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    aapl_thesis = PositionThesis(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 22, 14, tzinfo=timezone.utc),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="FCF + insider buying",
        horizon="swing",
        target_price=210.0, stop_price=185.0,
        last_reviewed_at=datetime(2026, 4, 22, 14, tzinfo=timezone.utc),
        opened_tick_id="tick_OPEN",
    )
    portfolio = Portfolio(
        cash=8000.0,
        positions={"AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50)},
    )

    def _ev(analyst, direction, conf, ticker):
        return AnalystEvidence(
            ticker=ticker, analyst=analyst, features={},
            verdict=AnalystVerdict(direction=direction, confidence=conf, rationale="x"),
        ).model_dump(mode="json")

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
        "technical_evidence":  [_ev("technical",  "bullish", 0.6, t) for t in ("AAPL", "MSFT")],
        "fundamental_evidence":[_ev("fundamental","bullish", 0.5, t) for t in ("AAPL", "MSFT")],
        "sentiment_evidence":  [_ev("sentiment",  "neutral", 0.3, t) for t in ("AAPL", "MSFT")],
        "smart_money_evidence":[_ev("smart_money","neutral", 0.0, t) for t in ("AAPL", "MSFT")],
    })

    runner = Runner(agent=strategist_agent, session_service=session_service)
    runner.run(user_id="t", session_id=session.id, new_message=None)

    decision_raw = session.state.get("strategist_decision")
    assert decision_raw is not None
    decision = StrategistDecision.model_validate(decision_raw)

    emitted = {s.ticker for s in decision.stances}
    assert emitted == {"AAPL", "MSFT"}
    assert set(decision.target_weights.keys()) == {"AAPL", "MSFT"}
```

The exact `Runner` API may differ across ADK versions — check existing integration tests under `tests/integration/`. If no precedent exists, mark this xfail with a TODO referencing a future ADK runner spike.

- [ ] **Step 2: Run the smoke (gated)**

Run: `RUN_LLM_TESTS=1 .venv/Scripts/python -m pytest tests/integration/test_strategist_v2_smoke.py -v`
Expected: PASS — strategist returns parseable stances for both tickers.

If the smoke is too brittle for CI, leave it as a manual smoke and document in the docstring.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_strategist_v2_smoke.py
git commit -m "test(strategist): add v2 LLM-touching smoke (gated by RUN_LLM_TESTS)"
```

---

## Task C16: Final regression pass + graphify delta

- [ ] **Step 1: Run all unit tests**

Run: `.venv/Scripts/python -m pytest tests/unit/ -v`
Expected: All passing.

- [ ] **Step 2: Run ruff**

Run: `.venv/Scripts/python -m ruff check src/ tests/`
Expected: zero new violations introduced by Plan C.

- [ ] **Step 3: Run the local smoke script (if you have RUN_LLM_TESTS=1 + Gemini creds)**

Run: `RUN_LLM_TESTS=1 .venv/Scripts/python scripts/smoke_run.py --ticks 3`
Expected: 3 ticks complete cleanly; `TickerStanceRow` rows appear in the dev SQLite.
If you can't run this (no creds, no Gemini access), skip — the smoke is gated.

- [ ] **Step 4: Append graphify delta entry**

Edit `graphify-out/graph_delta.md`. Append at the end:
```markdown

## YYYY-MM-DD — Phase 4 Plan C: strategist v2 against new contract

Strategist now emits per-ticker `TickerStance` and consumes the per-ticker
`TickerEvidence` built from Plan B's per-analyst evidence. Held-position context
rendered into the prompt. Per-ticker stances persisted to `TickerStanceRow`.
TradeLog gains `opening_tick_id` / `closing_tick_id` outcome attribution FKs.

- New nodes: `agents.strategist.stance_schema.TickerStance`,
  `agents.strategist.lifecycle.derive_lifecycle_action`,
  `agents.strategist.derivation.derive_legacy_fields` (+ `TickContext`, `DerivedFields`),
  `agents.strategist.held_view.render_held_positions_view`,
  `agents.strategist.evidence_view.render_ticker_evidence`,
  `agents.strategist.decision_writer.StrategistDecisionWriter`,
  `agents.strategist.agent._composite_before_callback`,
  `agents.strategist.agent._held_view_before_callback`,
  `agents.strategist.agent._evidence_view_before_callback`,
  `orchestrator.persistence.TickerStanceRow`,
  `orchestrator.persistence.save_ticker_stance`.
- New edges: `strategist_agent --before--> _composite_before_callback --calls--> _held_view + _evidence_view`;
  `strategist_agent --after--> _strategist_validation_callback --calls--> derive_legacy_fields`;
  `_evidence_view_before_callback --calls--> contract.digest.build_ticker_evidence`;
  `StrategistDecisionWriter --persists--> TickerStanceRow`;
  `pipeline.build_pipeline --includes--> StrategistDecisionWriter` (new stage 4 of 8);
  `executor.ExecutorAgent --writes--> state["positions"][ticker]` on BUY;
  `executor.ExecutorAgent --populates--> TradeLogRow.opening_tick_id/closing_tick_id` on SELL.
- Modified: `StrategistDecision` (gains `stances`, `trim_reasons`),
  `PositionThesis` (gains `opened_tick_id`),
  `TradeLogRow` (gains `opening_tick_id`, `closing_tick_id`),
  `STRATEGIST_INSTRUCTION` (rewritten template; consumes `{ticker_evidence}` + `{held_positions_view}`).
- State key changes: writes new `state["ticker_evidence"]` (rendered string) +
  `state["ticker_evidence_objects"]` (list[TickerEvidence dump]).
  Legacy `*_signals` keys still written by analysts (Plan B dual-emit) and still
  consumed by `attribution_writer` / `memory_writer` until Plan D.
```

Replace `YYYY-MM-DD` with today's date.

- [ ] **Step 5: Commit the delta entry**

```bash
git add graphify-out/graph_delta.md
git commit -m "docs(graphify): log Plan C strategist v2 + per-ticker stance substrate"
```

---

## Done

Plan C merged. Strategist now consumes the new contract and emits per-ticker stances. The legacy `*_signals` state keys are still written by analysts (Plan B's dual-emit) and read by `attribution_writer` / `memory_writer`. Plan D drops the legacy path.

**Next:** [Plan D — Cleanup](./plan-D-cleanup.md)

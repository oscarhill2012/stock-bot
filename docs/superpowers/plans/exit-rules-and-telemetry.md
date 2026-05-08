# Exit Rules & Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface position-level rule context (P&L, distance-to-stop, target_reached, etc.) to the strategist council as evidence; treat trim/add as first-class lifecycle decisions; persist council deliberations and per-position rule evaluations into queryable analytics tables for outcome attribution and Spec 3b's future learning loop.

**Architecture:** A deterministic `PositionPackBuilder` runs as the first sub_agent inside the existing `strategist_council` SequentialAgent, reading thesis fields + live broker prices and writing a per-ticker `PositionPack` to session state. The persona LlmAgents see those packs in their prompts. The aggregator gains a `MIN_HELD_WEIGHT` clamp and `trim_reasons` population. Two new writer agents (`CouncilTelemetryWriter`, `PositionPackWriter`) run after the council and persist three new ORM tables. `TradeLogRow` gains `opening_tick_id` / `closing_tick_id` foreign keys for outcome-attribution joins.

**Tech Stack:** Python 3.12 · Pydantic v2 · SQLAlchemy 2.x ORM · Google ADK (`BaseAgent`, `SequentialAgent`) · pytest · yfinance (for SPY snapshot, matching existing snapshotter pattern).

**Hard dependency:** Spec 1 (Strategist Council) MUST be shipped before this plan executes. This plan extends `MemberStance`, `StrategistDecision`, `aggregator.py`, `council.py`, and `prompts.py` — all introduced by Spec 1. Phases reference `resolve_ticker`, `build_thesis_from_proposers`, `_PerTickerOutcome`, `MIN_HELD_WEIGHT`, etc. as if they exist.

**Spec source:** `docs/superpowers/specs/exit-rules-and-telemetry-design.md`

---

## File Structure (locked)

**Created:**
- `src/agents/strategist/position_pack.py` — `PositionPack` model + `build_position_pack()` helper + `render_packs_for_prompt()` formatter
- `src/agents/strategist/pack_builder.py` — `PositionPackBuilder(BaseAgent)` + `build_pack_builder(broker)` factory
- `src/agents/strategist/telemetry_writer.py` — `CouncilTelemetryWriter(BaseAgent)` + `build_council_telemetry_writer(db_session)` factory
- `src/agents/strategist/pack_writer.py` — `PositionPackWriter(BaseAgent)` + `build_position_pack_writer(db_session)` factory

**Modified:**
- `src/agents/strategist/schema.py` — `PositionThesis` gains 5 fields; `StrategistDecision` gains `trim_reasons`
- `src/agents/strategist/member_schema.py` — `MemberStance` gains `trim_reason`
- `src/agents/strategist/aggregator.py` — `MIN_HELD_WEIGHT` clamp; `trim_reasons` populated; `build_thesis_from_proposers` seeds 5 new thesis fields
- `src/agents/strategist/prompts.py` — template gains `{position_packs}` block + trim_reason instruction
- `src/agents/strategist/council.py` — `strategist_council` SequentialAgent gains `position_pack_builder` as first sub_agent
- `src/orchestrator/persistence.py` — three new ORM rows + `TradeLogRow` extension
- `src/orchestrator/pipeline.py` — wires telemetry_writer + pack_writer; passes broker to council factory
- `src/agents/executor/agent.py` — `save_trade_log_entry` call site (lines 93-108) populates `opening_tick_id` from `thesis.opened_tick_id` and `closing_tick_id` from `state["tick_id"]`

**Tests created (flat `tests/unit/` to match repo convention):**
- `tests/unit/test_position_thesis_running_fields.py`
- `tests/unit/test_position_pack.py`
- `tests/unit/test_pack_builder_agent.py`
- `tests/unit/test_council_inner_sequence.py`
- `tests/unit/test_prompts_with_packs.py`
- `tests/unit/test_member_stance_trim_reason.py`
- `tests/unit/test_strategist_decision_trim_reasons.py`
- `tests/unit/test_aggregator_clamp_and_trims.py`
- `tests/unit/test_aggregator_thesis_seeding.py`
- `tests/unit/test_council_stance_persistence.py`
- `tests/unit/test_strategist_decision_persistence.py`
- `tests/unit/test_position_pack_persistence.py`
- `tests/unit/test_trade_log_attribution.py`
- `tests/unit/test_telemetry_writer.py`
- `tests/unit/test_pack_writer.py`
- `tests/unit/test_pipeline_wiring_exits.py`
- `tests/integration/test_council_with_packs_smoke.py`

---

## Phase 1: PositionThesis extension (5 new fields)

**Files:**
- Test: `tests/unit/test_position_thesis_running_fields.py`
- Modify: `src/agents/strategist/schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_position_thesis_running_fields.py`:
```python
"""PositionThesis — new running-state fields for exit rules & telemetry."""
from __future__ import annotations

from datetime import datetime, timezone

from agents.strategist.schema import PositionThesis


def _now():
    return datetime.now(tz=timezone.utc)


def _base_kwargs():
    """Minimum kwargs to instantiate a PositionThesis with the original (Spec 1) fields."""
    return dict(
        ticker="AAPL",
        opened_at=_now(),
        opened_price=192.0,
        opened_tag="council_open",
        rationale="bull thesis",
        horizon="swing",
        last_reviewed_at=_now(),
    )


def test_thesis_accepts_new_fields_with_defaults():
    """Legacy thesis serialised before this spec must still load (all five fields default)."""
    t = PositionThesis(**_base_kwargs())
    assert t.running_max_price == 0.0
    assert t.running_min_price == 0.0
    assert t.spy_price_at_open == 0.0
    assert t.weight_at_open == 0.0
    assert t.opened_tick_id == ""


def test_thesis_accepts_new_fields_explicitly():
    t = PositionThesis(
        **_base_kwargs(),
        running_max_price=210.0,
        running_min_price=185.0,
        spy_price_at_open=505.0,
        weight_at_open=0.08,
        opened_tick_id="tick_abc123",
    )
    assert t.running_max_price == 210.0
    assert t.running_min_price == 185.0
    assert t.spy_price_at_open == 505.0
    assert t.weight_at_open == 0.08
    assert t.opened_tick_id == "tick_abc123"


def test_thesis_round_trip_includes_new_fields():
    t = PositionThesis(
        **_base_kwargs(),
        running_max_price=210.0,
        running_min_price=185.0,
        spy_price_at_open=505.0,
        weight_at_open=0.08,
        opened_tick_id="tick_abc123",
    )
    rebuilt = PositionThesis.model_validate(t.model_dump())
    assert rebuilt.running_max_price == 210.0
    assert rebuilt.opened_tick_id == "tick_abc123"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_position_thesis_running_fields.py -v`
Expected: FAIL — fields not on the model.

- [ ] **Step 3: Add the five fields to PositionThesis**

In `src/agents/strategist/schema.py`, extend `PositionThesis`:
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

    # ── Running per-tick state (added in exit-rules-and-telemetry spec) ──
    running_max_price: float = 0.0   # max(current_price) since open; init = opened_price
    running_min_price: float = 0.0   # min(current_price) since open; init = opened_price
    spy_price_at_open: float = 0.0   # SPY snapshot for benchmark math
    weight_at_open:    float = 0.0   # final_weight chosen by the council at open time
    opened_tick_id:    str   = ""    # join key into StrategistDecisionRow for outcome attribution
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_position_thesis_running_fields.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_position_thesis_running_fields.py src/agents/strategist/schema.py
git commit -m "feat(strategist): add 5 running-state fields to PositionThesis for exit rules"
```

---

## Phase 2: PositionPack model + builder helper

**Files:**
- Test: `tests/unit/test_position_pack.py`
- Create: `src/agents/strategist/position_pack.py`

- [ ] **Step 1: Write the failing schema + math tests**

Create `tests/unit/test_position_pack.py`:
```python
"""PositionPack — Pydantic model + deterministic builder + prompt renderer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agents.strategist.position_pack import (
    PositionPack,
    build_position_pack,
    render_packs_for_prompt,
)
from agents.strategist.schema import PositionThesis


def _now():
    return datetime.now(tz=timezone.utc)


def _thesis(**overrides) -> PositionThesis:
    base = dict(
        ticker="AAPL",
        opened_at=_now() - timedelta(hours=72),
        opened_price=192.0,
        opened_tag="council_open",
        rationale="earnings beat",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
        catalyst="Q3 earnings",
        last_reviewed_at=_now(),
        last_review_note="holding through gap-up",
        running_max_price=208.0,
        running_min_price=190.0,
        spy_price_at_open=500.0,
        weight_at_open=0.08,
        opened_tick_id="tick_abc123",
    )
    base.update(overrides)
    return PositionThesis(**base)


# ── Schema ─────────────────────────────────────────────────────────────

def test_position_pack_schema_basic():
    pack = PositionPack(
        ticker="AAPL",
        opened_at=_now(),
        opened_price=192.0,
        opened_tag="council_open",
        horizon="swing",
        catalyst=None,
        rationale="earnings beat",
        target_price=210.0,
        stop_price=185.0,
        last_review_note="ok",
        current_price=206.0,
        current_weight=0.08,
        weight_at_open=0.08,
        unrealised_pnl_dollar=120.0,
        unrealised_pnl_pct=7.3,
        ticks_held=72,
        hours_held=72.0,
        distance_to_target_pct=-1.9,
        distance_to_stop_pct=11.4,
        target_reached=False,
        stop_breached=False,
        max_price_since_open=208.0,
        min_price_since_open=190.0,
        max_run_up_pct=8.3,
        max_drawdown_pct=-1.0,
        spy_return_since_open_pct=2.0,
        excess_return_since_open_pct=5.3,
    )
    assert pack.ticker == "AAPL"
    assert pack.target_reached is False


# ── build_position_pack — happy path with thresholds ────────────────────

def test_build_pack_pnl_and_distance_above_target():
    """Position trading above target: pct positive, target_reached True."""
    pack = build_position_pack(
        thesis=_thesis(opened_price=192.0, target_price=210.0, stop_price=185.0),
        current_price=212.0,
        current_weight=0.08,
        spy_price_now=510.0,
    )
    # P&L percentage is exact; dollar amount uses implied-shares math (just verify sign + magnitude direction).
    assert pack.unrealised_pnl_pct == pytest.approx((212.0 - 192.0) / 192.0 * 100, rel=1e-6)
    assert pack.unrealised_pnl_dollar > 0
    # Triggers
    assert pack.target_reached is True
    assert pack.stop_breached is False
    # Distance: positive = still below target (we are above target → negative)
    assert pack.distance_to_target_pct < 0
    assert pack.distance_to_stop_pct > 0


def test_build_pack_below_stop():
    pack = build_position_pack(
        thesis=_thesis(opened_price=192.0, target_price=210.0, stop_price=185.0),
        current_price=180.0,
        current_weight=0.08,
        spy_price_now=510.0,
    )
    assert pack.stop_breached is True
    assert pack.target_reached is False
    assert pack.distance_to_stop_pct < 0
    assert pack.unrealised_pnl_pct < 0


def test_build_pack_no_target_or_stop_set():
    """Thesis with no target/stop — flags are None, distances are None."""
    pack = build_position_pack(
        thesis=_thesis(target_price=None, stop_price=None),
        current_price=200.0,
        current_weight=0.05,
        spy_price_now=510.0,
    )
    assert pack.target_reached is None
    assert pack.stop_breached is None
    assert pack.distance_to_target_pct is None
    assert pack.distance_to_stop_pct is None


# ── Time math ───────────────────────────────────────────────────────────

def test_build_pack_hours_held():
    opened = _now() - timedelta(hours=72)
    pack = build_position_pack(
        thesis=_thesis(opened_at=opened),
        current_price=200.0,
        current_weight=0.05,
        spy_price_now=510.0,
    )
    assert 71.5 < pack.hours_held < 72.5
    assert pack.ticks_held == 72   # round(hours_held) — robust to sub-second drift


# ── Running extremes ────────────────────────────────────────────────────

def test_build_pack_run_up_and_drawdown():
    pack = build_position_pack(
        thesis=_thesis(
            opened_price=192.0,
            running_max_price=210.0,
            running_min_price=180.0,
        ),
        current_price=200.0,
        current_weight=0.05,
        spy_price_now=510.0,
    )
    # max_run_up_pct = (210 - 192) / 192 * 100 = 9.375
    assert pack.max_run_up_pct == pytest.approx(9.375, rel=1e-3)
    # max_drawdown_pct = (180 - 192) / 192 * 100 = -6.25
    assert pack.max_drawdown_pct == pytest.approx(-6.25, rel=1e-3)


# ── SPY-relative ────────────────────────────────────────────────────────

def test_build_pack_spy_relative():
    pack = build_position_pack(
        thesis=_thesis(opened_price=192.0, spy_price_at_open=500.0),
        current_price=210.0,        # +9.375% on AAPL
        current_weight=0.08,
        spy_price_now=515.0,        # +3.0% on SPY
    )
    assert pack.spy_return_since_open_pct == pytest.approx(3.0, rel=1e-3)
    # excess = AAPL_pct - SPY_pct
    assert pack.excess_return_since_open_pct == pytest.approx(9.375 - 3.0, rel=1e-3)


def test_build_pack_spy_baseline_zero_yields_zero():
    """If spy_price_at_open is 0.0 (legacy thesis), spy returns are 0.0."""
    pack = build_position_pack(
        thesis=_thesis(opened_price=192.0, spy_price_at_open=0.0),
        current_price=210.0,
        current_weight=0.08,
        spy_price_now=515.0,
    )
    assert pack.spy_return_since_open_pct == 0.0
    assert pack.excess_return_since_open_pct == pytest.approx(9.375, rel=1e-3)


# ── render_packs_for_prompt ──────────────────────────────────────────────

def test_render_packs_for_prompt_returns_indented_json_strings():
    pack = build_position_pack(
        thesis=_thesis(),
        current_price=206.0,
        current_weight=0.08,
        spy_price_now=510.0,
    )
    rendered = render_packs_for_prompt([pack])
    assert isinstance(rendered, str)
    # Each pack rendered as JSON; the joined output should parse as JSON when wrapped in [].
    parsed = json.loads("[" + rendered + "]")
    assert parsed[0]["ticker"] == "AAPL"


def test_render_packs_for_prompt_empty_list():
    assert render_packs_for_prompt([]) == ""
```

Note on the first P&L assertion: I'm computing `(current_price - opened_price) * shares` where shares is implied by `current_weight * portfolio_value / current_price`. The tests use a simplified shares-implied formula — see implementation. If the test math feels off, prefer the literal `(current_price - opened_price)` * implied_shares form in the implementation.

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/test_position_pack.py -v`
Expected: FAIL with `ImportError: cannot import name 'PositionPack'`.

- [ ] **Step 3: Implement position_pack.py**

Create `src/agents/strategist/position_pack.py`:
```python
"""PositionPack — deterministic per-tick snapshot of one open position.

Built once per held ticker by PositionPackBuilder, consumed by:
- the persona prompts (rendered via render_packs_for_prompt)
- the pack writer (persisted to PositionPackRow for Spec 3b analysis)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from agents.strategist.schema import PositionThesis


class PositionPack(BaseModel):
    """Per-tick deterministic snapshot of one open position.

    All fields are derived from PositionThesis + live market data; no LLM
    judgement involved. Personas treat these numbers as authoritative.
    """

    # Identity
    ticker: str

    # Thesis (carried — copied from PositionThesis at pack build time)
    opened_at: datetime
    opened_price: float
    opened_tag: str
    horizon: Literal["intraday", "swing", "long_term"]
    catalyst: str | None
    rationale: str = Field(max_length=400)
    target_price: float | None
    stop_price: float | None
    last_review_note: str = Field(max_length=200)

    # Live market
    current_price: float

    # Position state
    current_weight: float
    weight_at_open: float

    # P&L
    unrealised_pnl_dollar: float
    unrealised_pnl_pct: float

    # Time
    ticks_held: int
    hours_held: float

    # Distance to triggers (None when threshold unset)
    distance_to_target_pct: float | None
    distance_to_stop_pct:   float | None

    # Trigger flags
    target_reached: bool | None
    stop_breached:  bool | None

    # Running extremes since open
    max_price_since_open: float
    min_price_since_open: float
    max_run_up_pct:   float
    max_drawdown_pct: float

    # Benchmark relative
    spy_return_since_open_pct:    float
    excess_return_since_open_pct: float


def build_position_pack(
    *,
    thesis: PositionThesis,
    current_price: float,
    current_weight: float,
    spy_price_now: float,
) -> PositionPack:
    """Construct a PositionPack from a thesis + live data.

    All math is deterministic — no LLM, no broker, just arithmetic.
    """
    # Time held (timezone-aware datetime arithmetic).
    now = datetime.now(tz=timezone.utc)
    opened_at = thesis.opened_at if thesis.opened_at.tzinfo else thesis.opened_at.replace(tzinfo=timezone.utc)
    delta_hours = (now - opened_at).total_seconds() / 3600.0
    hours_held = max(0.0, delta_hours)
    ticks_held = round(hours_held)  # hourly cadence; round() is robust to sub-second drift

    # P&L
    pnl_pct = (current_price - thesis.opened_price) / thesis.opened_price * 100.0 if thesis.opened_price else 0.0
    # Implied dollar P&L for the held weight at portfolio value of $10k baseline
    # (relative metric — treat as direction + magnitude indicator, not GAAP).
    implied_shares = (current_weight * 10_000.0) / current_price if current_price else 0.0
    pnl_dollar = (current_price - thesis.opened_price) * implied_shares

    # Distance to triggers
    if thesis.target_price is not None and thesis.target_price > 0:
        distance_to_target_pct = (thesis.target_price - current_price) / thesis.target_price * 100.0
        target_reached = current_price >= thesis.target_price
    else:
        distance_to_target_pct = None
        target_reached = None

    if thesis.stop_price is not None and thesis.stop_price > 0:
        distance_to_stop_pct = (current_price - thesis.stop_price) / thesis.stop_price * 100.0
        stop_breached = current_price <= thesis.stop_price
    else:
        distance_to_stop_pct = None
        stop_breached = None

    # Running extremes (use thesis fields; pack builder updates them in-place via PackBuilder)
    max_price = thesis.running_max_price if thesis.running_max_price else thesis.opened_price
    min_price = thesis.running_min_price if thesis.running_min_price else thesis.opened_price
    max_run_up_pct = (max_price - thesis.opened_price) / thesis.opened_price * 100.0 if thesis.opened_price else 0.0
    max_drawdown_pct = (min_price - thesis.opened_price) / thesis.opened_price * 100.0 if thesis.opened_price else 0.0

    # SPY-relative (zero baseline if legacy thesis)
    if thesis.spy_price_at_open and thesis.spy_price_at_open > 0:
        spy_return = (spy_price_now - thesis.spy_price_at_open) / thesis.spy_price_at_open * 100.0
    else:
        spy_return = 0.0
    excess_return = pnl_pct - spy_return

    return PositionPack(
        ticker=thesis.ticker,
        opened_at=thesis.opened_at,
        opened_price=thesis.opened_price,
        opened_tag=thesis.opened_tag,
        horizon=thesis.horizon,
        catalyst=thesis.catalyst,
        rationale=thesis.rationale,
        target_price=thesis.target_price,
        stop_price=thesis.stop_price,
        last_review_note=thesis.last_review_note,
        current_price=current_price,
        current_weight=current_weight,
        weight_at_open=thesis.weight_at_open,
        unrealised_pnl_dollar=pnl_dollar,
        unrealised_pnl_pct=pnl_pct,
        ticks_held=ticks_held,
        hours_held=hours_held,
        distance_to_target_pct=distance_to_target_pct,
        distance_to_stop_pct=distance_to_stop_pct,
        target_reached=target_reached,
        stop_breached=stop_breached,
        max_price_since_open=max_price,
        min_price_since_open=min_price,
        max_run_up_pct=max_run_up_pct,
        max_drawdown_pct=max_drawdown_pct,
        spy_return_since_open_pct=spy_return,
        excess_return_since_open_pct=excess_return,
    )


def render_packs_for_prompt(packs: list[PositionPack]) -> str:
    """Format a list of packs for inclusion in the persona prompt.

    Returns one indented JSON block per pack, comma-separated.
    Empty input → empty string (the prompt template should handle this).
    """
    if not packs:
        return ""
    return ",\n".join(p.model_dump_json(indent=2) for p in packs)
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_position_pack.py -v`
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_position_pack.py src/agents/strategist/position_pack.py
git commit -m "feat(strategist): add PositionPack model + deterministic builder + prompt renderer"
```

---

## Phase 3: PositionPackBuilder BaseAgent

**Files:**
- Test: `tests/unit/test_pack_builder_agent.py`
- Create: `src/agents/strategist/pack_builder.py`

The pack builder is the broker-aware ADK agent that runs as the first sub_agent inside the council. It iterates over held positions in `state["positions"]`, builds a `PositionPack` for each, and writes:
- `state["position_packs"]: dict[str, dict]` — ticker → pack as dict (model_dump)
- `state["spy_price"]: float` — current SPY price (used by aggregator's `build_thesis_from_proposers`)

It also updates each thesis's `running_max_price` / `running_min_price` based on the current price.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_pack_builder_agent.py`:
```python
"""PositionPackBuilder — BaseAgent that builds packs from broker + theses."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from agents.strategist.pack_builder import PositionPackBuilder, build_pack_builder
from agents.strategist.schema import PositionThesis


class _FakePosition:
    def __init__(self, last_price: float):
        self.last_price = last_price


class _FakePortfolio:
    def __init__(self, prices: dict[str, float]):
        self.positions = {t: _FakePosition(p) for t, p in prices.items()}


class _FakeBroker:
    def __init__(self, prices: dict[str, float]):
        self._portfolio = _FakePortfolio(prices)

    async def get_portfolio(self):
        return self._portfolio


def _now():
    return datetime.now(tz=timezone.utc)


def _thesis(ticker: str, opened_price: float = 192.0) -> dict:
    """Returns a thesis as a session-state dict (how it's stored)."""
    return PositionThesis(
        ticker=ticker,
        opened_at=_now() - timedelta(hours=24),
        opened_price=opened_price,
        opened_tag="council_open",
        rationale="r",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
        last_reviewed_at=_now(),
        running_max_price=opened_price,
        running_min_price=opened_price,
        spy_price_at_open=500.0,
        weight_at_open=0.08,
        opened_tick_id="tick_abc",
    ).model_dump()


def _make_ctx(state: dict) -> SimpleNamespace:
    """Minimal InvocationContext stub — only ctx.session.state is read."""
    return SimpleNamespace(session=SimpleNamespace(state=state))


def _run(builder: PositionPackBuilder, state: dict):
    async def runner():
        agen = builder._run_async_impl(_make_ctx(state))
        async for _ in agen:
            pass
    asyncio.run(runner())


def test_builder_writes_packs_for_held_positions():
    """One held ticker produces one pack in state["position_packs"]."""
    broker = _FakeBroker({"AAPL": 206.0})
    state = {
        "tickers": ["AAPL", "MSFT"],
        "positions": {"AAPL": _thesis("AAPL")},
    }
    builder = build_pack_builder(broker)
    with patch("agents.strategist.pack_builder._fetch_spy_price", return_value=510.0):
        _run(builder, state)

    assert "position_packs" in state
    assert "AAPL" in state["position_packs"]
    assert "MSFT" not in state["position_packs"]    # not held
    pack = state["position_packs"]["AAPL"]
    assert pack["ticker"] == "AAPL"
    assert pack["current_price"] == 206.0


def test_builder_writes_spy_price_to_state():
    broker = _FakeBroker({})
    state = {"tickers": ["AAPL"], "positions": {}}
    builder = build_pack_builder(broker)
    with patch("agents.strategist.pack_builder._fetch_spy_price", return_value=510.0):
        _run(builder, state)
    assert state["spy_price"] == 510.0


def test_builder_updates_running_max_min_in_thesis():
    """running_max_price climbs when current_price > previous max."""
    broker = _FakeBroker({"AAPL": 215.0})    # higher than running_max_price=192.0
    state = {
        "tickers": ["AAPL"],
        "positions": {"AAPL": _thesis("AAPL", opened_price=192.0)},
    }
    builder = build_pack_builder(broker)
    with patch("agents.strategist.pack_builder._fetch_spy_price", return_value=510.0):
        _run(builder, state)
    updated_thesis = state["positions"]["AAPL"]
    assert updated_thesis["running_max_price"] == 215.0
    # min unchanged because 215 > 192 (the seeded min)
    assert updated_thesis["running_min_price"] == 192.0


def test_builder_skips_ticker_without_thesis_silently():
    """state["positions"]["AAPL"] is the bare current weight, not a thesis dict."""
    broker = _FakeBroker({"AAPL": 206.0})
    state = {
        "tickers": ["AAPL"],
        "positions": {"AAPL": 0.08},   # bare float, no thesis
    }
    builder = build_pack_builder(broker)
    with patch("agents.strategist.pack_builder._fetch_spy_price", return_value=510.0):
        _run(builder, state)
    # No pack produced — but tick proceeds.
    assert state.get("position_packs", {}) == {}


def test_builder_skips_ticker_with_no_broker_price():
    """Broker doesn't know about MSFT — pack builder logs and skips it."""
    broker = _FakeBroker({"AAPL": 206.0})    # no MSFT
    state = {
        "tickers": ["AAPL", "MSFT"],
        "positions": {"AAPL": _thesis("AAPL"), "MSFT": _thesis("MSFT")},
    }
    builder = build_pack_builder(broker)
    with patch("agents.strategist.pack_builder._fetch_spy_price", return_value=510.0):
        _run(builder, state)
    assert "AAPL" in state["position_packs"]
    assert "MSFT" not in state["position_packs"]


def test_builder_handles_yfinance_failure_gracefully():
    """SPY fetch failure → spy_price=0.0; packs still build."""
    broker = _FakeBroker({"AAPL": 206.0})
    state = {
        "tickers": ["AAPL"],
        "positions": {"AAPL": _thesis("AAPL")},
    }
    builder = build_pack_builder(broker)
    with patch("agents.strategist.pack_builder._fetch_spy_price", side_effect=RuntimeError("yf down")):
        _run(builder, state)
    assert state["spy_price"] == 0.0
    assert "AAPL" in state["position_packs"]
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/test_pack_builder_agent.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement pack_builder.py**

Create `src/agents/strategist/pack_builder.py`:
```python
"""PositionPackBuilder — deterministic BaseAgent producing PositionPack objects.

Runs as the first sub_agent inside strategist_council. Reads:
- state["positions"] — dict[ticker, PositionThesis-as-dict]
- broker.get_portfolio() — for current prices on held tickers
- yfinance — for the current SPY price (matches Snapshotter pattern)

Writes:
- state["position_packs"] — dict[ticker, PositionPack-as-dict]
- state["spy_price"] — float (current SPY)
- mutates state["positions"][ticker]["running_max_price"]/["running_min_price"]
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from agents.strategist.position_pack import build_position_pack
from agents.strategist.schema import PositionThesis

log = logging.getLogger(__name__)


def _fetch_spy_price() -> float:
    """Fetch the latest SPY close via yfinance. Mirrors Snapshotter.

    Isolated as a module-level function so tests can patch it.
    Raises on failure; the caller is responsible for catching.
    """
    import yfinance as yf
    spy = yf.Ticker("SPY")
    hist = spy.history(period="1d")
    if hist.empty:
        raise RuntimeError("yfinance returned empty SPY history")
    return float(hist["Close"].iloc[-1])


class PositionPackBuilder(BaseAgent):
    """ADK BaseAgent — builds position packs for held tickers each tick.

    Pure deterministic: broker calls + yfinance + arithmetic. No LLM.
    """

    name: str = "PositionPackBuilder"
    broker: Any

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state

        # ── SPY snapshot (single fetch per tick) ────────────────────────
        try:
            spy_price = _fetch_spy_price()
        except Exception as exc:
            log.warning("SPY price fetch failed: %s — defaulting to 0.0", exc)
            spy_price = 0.0
        state["spy_price"] = spy_price

        # ── Per-held-ticker pack build ──────────────────────────────────
        positions = state.get("positions", {})
        packs: dict[str, dict] = {}

        # Get current prices for held tickers from the broker.
        portfolio = await self.broker.get_portfolio()
        broker_prices = {
            t: pos.last_price
            for t, pos in portfolio.positions.items()
        }

        for ticker, raw in list(positions.items()):
            # Skip if this isn't a thesis dict (legacy bare-weight format).
            if not isinstance(raw, dict) or "opened_price" not in raw:
                continue

            current_price = broker_prices.get(ticker)
            if current_price is None:
                log.warning("No broker price for held ticker %s — skipping pack", ticker)
                continue

            thesis = PositionThesis.model_validate(raw)

            # Update running extremes IN THE THESIS DICT (so it persists to next tick).
            new_max = max(thesis.running_max_price or thesis.opened_price, current_price)
            new_min = min(thesis.running_min_price or thesis.opened_price, current_price) if thesis.running_min_price else current_price
            # Handle the legacy-zero seed (running_min_price == 0.0 means "uninitialised").
            if thesis.running_min_price == 0.0:
                new_min = min(thesis.opened_price, current_price)
            updated_dict = thesis.model_copy(update={
                "running_max_price": new_max,
                "running_min_price": new_min,
            }).model_dump()
            state["positions"][ticker] = updated_dict
            thesis = PositionThesis.model_validate(updated_dict)

            # Determine current_weight from positions / portfolio.
            # Held tickers in state["positions"] carry the thesis; weight comes from portfolio.
            total = portfolio.total_value if hasattr(portfolio, "total_value") else 1.0
            position_value = current_price * portfolio.positions[ticker].quantity if hasattr(portfolio.positions[ticker], "quantity") else 0.0
            current_weight = (position_value / total) if total > 0 else 0.0

            pack = build_position_pack(
                thesis=thesis,
                current_price=current_price,
                current_weight=current_weight,
                spy_price_now=spy_price,
            )
            packs[ticker] = pack.model_dump(mode="json")

        state["position_packs"] = packs
        return
        yield  # required to make this an async generator


def build_pack_builder(broker: Any) -> PositionPackBuilder:
    """Factory used by the council factory to inject the broker."""
    return PositionPackBuilder(broker=broker)
```

Note on `current_weight`: the implementation uses `position.quantity` if the broker's Position object exposes it; the test fixture's `_FakePosition` only has `last_price`, so the weight will be 0.0 in those tests. That's acceptable for the unit tests — the tests assert pack *existence* and *price flow*, not weight precision. Real broker integration provides quantity.

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_pack_builder_agent.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_pack_builder_agent.py src/agents/strategist/pack_builder.py
git commit -m "feat(strategist): add PositionPackBuilder agent + SPY snapshot"
```

---

## Phase 4: Wire pack_builder into council inner sequence

**Files:**
- Test: `tests/unit/test_council_inner_sequence.py`
- Modify: `src/agents/strategist/council.py`

The Spec 1 council is `SequentialAgent([persona_pool, council_aggregator])`. We add `position_pack_builder` as the first sub_agent.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_council_inner_sequence.py`:
```python
"""strategist_council inner SequentialAgent must include pack_builder first."""
from __future__ import annotations

from unittest.mock import MagicMock

from agents.strategist.council import build_strategist_council


def test_council_inner_sequence_starts_with_pack_builder():
    """First sub_agent must be the PositionPackBuilder."""
    broker = MagicMock()
    council = build_strategist_council(broker=broker)
    sub_agents = list(council.sub_agents)
    assert len(sub_agents) == 3
    assert sub_agents[0].name == "PositionPackBuilder"


def test_council_second_sub_agent_is_persona_pool():
    broker = MagicMock()
    council = build_strategist_council(broker=broker)
    assert council.sub_agents[1].name == "PersonaPool"


def test_council_third_sub_agent_is_aggregator():
    broker = MagicMock()
    council = build_strategist_council(broker=broker)
    assert council.sub_agents[2].name == "CouncilAggregator"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_council_inner_sequence.py -v`
Expected: FAIL — pack_builder not in sequence; or `build_strategist_council` doesn't accept `broker`.

- [ ] **Step 3: Modify council.py to accept broker and wire pack_builder**

Update `src/agents/strategist/council.py`:
```python
"""strategist_council — SequentialAgent wrapping pack builder, personas, aggregator."""
from __future__ import annotations

from typing import Any

from google.adk.agents import SequentialAgent

from agents.strategist.pack_builder import build_pack_builder
from agents.strategist.personas import persona_pool
from agents.strategist.aggregator import council_aggregator


def build_strategist_council(broker: Any) -> SequentialAgent:
    """Build a fresh strategist_council with pack_builder wired to the broker.

    Spec 1 introduced (persona_pool + aggregator). This spec inserts
    PositionPackBuilder as the first sub_agent so persona prompts can
    read deterministic position packs.
    """
    return SequentialAgent(
        name="StrategistCouncil",
        sub_agents=[
            build_pack_builder(broker),
            persona_pool,
            council_aggregator,
        ],
    )


# Module-level convenience for tests that don't need a broker.
# Production use must call build_strategist_council(broker=...).
```

If Spec 1's council.py exported a module-level `strategist_council` singleton, remove it — the broker dependency means we always need a factory. Update any callers to use `build_strategist_council(broker)` instead.

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_council_inner_sequence.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_council_inner_sequence.py src/agents/strategist/council.py
git commit -m "feat(strategist): wire PositionPackBuilder as first sub_agent of council"
```

---

## Phase 5: Persona prompt template — `{position_packs}` slot

**Files:**
- Test: `tests/unit/test_prompts_with_packs.py`
- Modify: `src/agents/strategist/prompts.py`

The Spec 1 template fills slots like `{technical_signals}`. This phase adds a `{position_packs}` slot and the trim_reason instruction.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_prompts_with_packs.py`:
```python
"""Persona prompt template — {position_packs} slot + trim instruction."""
from __future__ import annotations

from agents.strategist.prompts import COUNCIL_PROMPT_TEMPLATE, render_persona_prompt
from agents.strategist.personas import VALUE_LENS


def test_template_contains_position_packs_slot():
    assert "{position_packs}" in COUNCIL_PROMPT_TEMPLATE


def test_template_contains_trim_instruction():
    """Mentions trim_reason as a required field on TRIM stances."""
    assert "trim_reason" in COUNCIL_PROMPT_TEMPLATE


def test_render_fills_position_packs_with_empty_string_when_no_holdings():
    state = {
        "portfolio": {},
        "positions": {},
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "",
        "technical_signals": [],
        "fundamental_signals": [],
        "sentiment_signals": [],
        "smart_money_signals": [],
        "tickers": ["AAPL"],
        "position_packs": {},
    }
    rendered = render_persona_prompt(VALUE_LENS, state, persona_name="value")
    # Empty packs render as empty string; the slot is filled, not left literal.
    assert "{position_packs}" not in rendered


def test_render_fills_position_packs_with_json_blocks_when_holdings_present():
    state = {
        "portfolio": {},
        "positions": {},
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "",
        "technical_signals": [],
        "fundamental_signals": [],
        "sentiment_signals": [],
        "smart_money_signals": [],
        "tickers": ["AAPL"],
        "position_packs": {
            "AAPL": {
                "ticker": "AAPL",
                "current_price": 206.0,
                # ... (other fields elided — render_persona_prompt JSON-dumps the dict)
            }
        },
    }
    rendered = render_persona_prompt(VALUE_LENS, state, persona_name="value")
    assert "AAPL" in rendered
    assert "206.0" in rendered
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_prompts_with_packs.py -v`
Expected: FAIL — template doesn't have the slot.

- [ ] **Step 3: Update the prompt template + render function**

Update `src/agents/strategist/prompts.py`. Add the `## Open Positions` block between `## Analyst Signals` and `## Your Job`, and the trim_reason bullet inside `## Your Job`:

```python
"""Council prompt template + render helper."""
from __future__ import annotations

import json
from typing import Any


COUNCIL_PROMPT_TEMPLATE = """
You are the {persona_name} strategist on a 3-member trading council.

## Your Lens
{persona_lens}

## Analyst Reliability
Weight analyst signals as follows when forming your view:
{analyst_weights_table}

## Current State
Portfolio: {portfolio}
Active Positions (with current weights): {positions}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest: {day_digest}
Current Thesis: {thesis}

## Analyst Signals (with structured evidence)
Technical:    {technical_signals}
Fundamental:  {fundamental_signals}
Sentiment:    {sentiment_signals}
Smart Money:  {smart_money_signals}

## Open Positions (deterministic snapshot — believe these numbers)
{position_packs}

You may decide for each held position to:
- HOLD — keep the weight where it is
- TRIM — reduce the weight (any reduction that keeps weight >= MIN_HELD_WEIGHT); include a trim_reason
- CLOSE — set weight to 0; include a close_reason
- ADD  — increase the weight (subject to risk gate caps)

The `rules` block tells you whether the original stop/target hypothesis has fired.
Treat these as inputs to your judgment, not commands. If you choose to override a
fired rule (e.g. holding through stop_breached because the thesis has strengthened),
say so explicitly in your rationale so telemetry can capture the override.

## Your Job
Emit a MemberStance for EVERY watchlist ticker: {tickers}.
- preferred_weight in [0,1]: your ideal portfolio weight for this ticker next tick
- conviction in [0,1]: how strongly you hold this view
- rationale: <=140 chars
- If proposing to open (current 0 -> preferred >0): include horizon, target_price, stop_price, optional catalyst.
- If proposing to close (current >0 -> preferred 0): include close_reason.
- If proposing to trim (current >MIN_HELD_WEIGHT -> preferred lower but still >=MIN_HELD_WEIGHT): include trim_reason.

Output: list[MemberStance], exhaustive over the watchlist.
"""


def _render_packs(packs_dict: dict[str, Any]) -> str:
    """Render the position_packs state value as JSON blocks for the prompt."""
    if not packs_dict:
        return ""
    return ",\n".join(
        json.dumps(pack, indent=2, default=str) for pack in packs_dict.values()
    )


def render_persona_prompt(
    persona_lens: str,
    state: dict[str, Any],
    *,
    persona_name: str,
) -> str:
    """Fill all slots in the council prompt template for one persona."""
    from agents.strategist.config import ANALYST_WEIGHTS

    weights_table = "\n".join(
        f"- {analyst}: {weight}" for analyst, weight in ANALYST_WEIGHTS.items()
    )
    return COUNCIL_PROMPT_TEMPLATE.format(
        persona_name=persona_name,
        persona_lens=persona_lens,
        analyst_weights_table=weights_table,
        portfolio=state.get("portfolio", {}),
        positions=state.get("positions", {}),
        memory_buffer=state.get("memory_buffer", []),
        day_digest=state.get("day_digest", ""),
        thesis=state.get("thesis", ""),
        technical_signals=state.get("technical_signals", []),
        fundamental_signals=state.get("fundamental_signals", []),
        sentiment_signals=state.get("sentiment_signals", []),
        smart_money_signals=state.get("smart_money_signals", []),
        tickers=state.get("tickers", []),
        position_packs=_render_packs(state.get("position_packs", {})),
    )
```

If Spec 1's `prompts.py` already implements `render_persona_prompt` differently, merge the changes — preserve Spec 1's existing slots and add `position_packs` plus the trim_reason bullet.

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_prompts_with_packs.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_prompts_with_packs.py src/agents/strategist/prompts.py
git commit -m "feat(strategist): add {position_packs} slot + trim instruction to persona prompt"
```

---

## Phase 6: MemberStance.trim_reason

**Files:**
- Test: `tests/unit/test_member_stance_trim_reason.py`
- Modify: `src/agents/strategist/member_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_member_stance_trim_reason.py`:
```python
"""MemberStance — optional trim_reason field."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.member_schema import MemberStance


def _kwargs(**over):
    base = dict(
        ticker="AAPL",
        persona="value",
        preferred_weight=0.05,
        conviction=0.7,
        rationale="trim some",
    )
    base.update(over)
    return base


def test_trim_reason_defaults_to_none():
    s = MemberStance(**_kwargs())
    assert s.trim_reason is None


def test_trim_reason_accepts_string():
    s = MemberStance(**_kwargs(trim_reason="target reached, taking some off"))
    assert s.trim_reason == "target reached, taking some off"


def test_trim_reason_max_length_120():
    too_long = "x" * 121
    with pytest.raises(ValidationError):
        MemberStance(**_kwargs(trim_reason=too_long))
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_member_stance_trim_reason.py -v`
Expected: FAIL — `trim_reason` not on the model.

- [ ] **Step 3: Add the field to MemberStance**

In `src/agents/strategist/member_schema.py`, add the field below the existing `close_reason`:
```python
class MemberStance(BaseModel):
    """One council member's per-ticker opinion."""
    ticker: str
    persona: Literal["value", "momentum", "contrarian"]
    preferred_weight: float = Field(ge=0.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=140)

    # Lifecycle hints (Spec 1)
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=80)
    close_reason: str | None = Field(default=None, max_length=120)

    # Lifecycle hint added in exit-rules-and-telemetry spec.
    trim_reason: str | None = Field(default=None, max_length=120)
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_member_stance_trim_reason.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_member_stance_trim_reason.py src/agents/strategist/member_schema.py
git commit -m "feat(strategist): add optional trim_reason to MemberStance"
```

---

## Phase 7: StrategistDecision.trim_reasons

**Files:**
- Test: `tests/unit/test_strategist_decision_trim_reasons.py`
- Modify: `src/agents/strategist/schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_strategist_decision_trim_reasons.py`:
```python
"""StrategistDecision — trim_reasons dict (parallel to close_reasons)."""
from __future__ import annotations

from agents.strategist.schema import StrategistDecision


def _kwargs(**over):
    base = dict(
        target_weights={"AAPL": 0.05},
        decision_tag="council_0o_0c_1t_0a",
        reasoning="trim AAPL from 0.10 to 0.05",
        updated_thesis="bull thesis intact, scaling out partial",
        confidence=0.6,
    )
    base.update(over)
    return base


def test_trim_reasons_defaults_to_empty_dict():
    d = StrategistDecision(**_kwargs())
    assert d.trim_reasons == {}


def test_trim_reasons_accepts_per_ticker_strings():
    d = StrategistDecision(**_kwargs(trim_reasons={"AAPL": "V: target reached | M: momentum cooling"}))
    assert d.trim_reasons["AAPL"].startswith("V:")


def test_trim_reasons_round_trip():
    d = StrategistDecision(**_kwargs(trim_reasons={"AAPL": "took 50% off"}))
    rebuilt = StrategistDecision.model_validate(d.model_dump())
    assert rebuilt.trim_reasons == {"AAPL": "took 50% off"}
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_strategist_decision_trim_reasons.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the field to StrategistDecision**

In `src/agents/strategist/schema.py`, add `trim_reasons` below `close_reasons`:
```python
class StrategistDecision(BaseModel):
    """Full output from one Strategist LLM call."""

    target_weights: dict[str, float]
    decision_tag: str
    reasoning: str = Field(max_length=300)
    updated_thesis: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    new_positions: dict[str, PositionThesis] = Field(default_factory=dict)
    close_reasons: dict[str, str] = Field(default_factory=dict)
    trim_reasons: dict[str, str] = Field(default_factory=dict)   # added in exit-rules-and-telemetry spec
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_strategist_decision_trim_reasons.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_strategist_decision_trim_reasons.py src/agents/strategist/schema.py
git commit -m "feat(strategist): add trim_reasons to StrategistDecision"
```

---

## Phase 8: Aggregator extensions (clamp + trim_reasons + thesis seeding)

**Files:**
- Test: `tests/unit/test_aggregator_clamp_and_trims.py`
- Test: `tests/unit/test_aggregator_thesis_seeding.py`
- Modify: `src/agents/strategist/aggregator.py`

This phase has three independent edits to Spec 1's aggregator. Doing them as one phase because they're all small and live in the same file.

### 8a — MIN_HELD_WEIGHT clamp on trims

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_aggregator_clamp_and_trims.py`:
```python
"""Aggregator — sub-MIN_HELD_WEIGHT trim clamp + trim_reasons population."""
from __future__ import annotations

from datetime import datetime, timezone

from agents.strategist.aggregator import resolve_ticker, CouncilAggregator
from agents.strategist.member_schema import MemberStance
from orchestrator.state import MIN_HELD_WEIGHT


def _now():
    return datetime.now(tz=timezone.utc)


def _ctx():
    return {
        "ticker": "AAPL",
        "opened_at": _now(),
        "opened_price": 200.0,
        "opened_tag": "council_open",
        "last_reviewed_at": _now(),
        "spy_price": 500.0,
        "tick_id": "tick_test",
        "final_weight": 0.05,
    }


def _s(persona, weight, *, conv=0.7, **kw):
    return MemberStance(
        ticker="AAPL", persona=persona, preferred_weight=weight,
        conviction=conv, rationale="x", **kw,
    )


# ── Clamp behaviour (defensive — unreachable in practice given Spec 1's epsilons) ────

def test_trim_branch_never_below_min_held_weight():
    """Sanity check: trim path always returns final >= MIN_HELD_WEIGHT.

    With prefs above CLOSE_EPSILON (else close-quorum fires) and convs > 0,
    confidence_weighted_avg cannot mathematically dip below CLOSE_EPSILON,
    let alone MIN_HELD_WEIGHT. The clamp is defensive belt-and-braces — this
    test verifies the contract holds in a realistic trim scenario.
    """
    members = [
        _s("value", 0.006, conv=0.05),    # just above CLOSE_EPSILON, very low conv
        _s("momentum", 0.006, conv=0.05),
        _s("contrarian", 0.006, conv=0.05),
    ]
    out = resolve_ticker(members, curr=0.10, n_available=3, ctx=_ctx())
    assert out.decision == "trim"
    assert out.final_weight >= MIN_HELD_WEIGHT


# ── Trim reasons population ─────────────────────────────────────────────

def test_trim_reasons_populated_in_v_m_c_order():
    members = [
        _s("value", 0.05, trim_reason="value: book some profit"),
        _s("momentum", 0.05, trim_reason="momentum: cooling"),
        _s("contrarian", 0.05),    # no trim_reason
    ]
    out = resolve_ticker(members, curr=0.10, n_available=3, ctx=_ctx())
    assert out.decision == "trim"
    assert out.trim_reason is not None
    assert "V:" in out.trim_reason
    assert "M:" in out.trim_reason
    assert "C:" not in out.trim_reason
    # V before M
    assert out.trim_reason.find("V:") < out.trim_reason.find("M:")


def test_trim_reasons_caps_at_120_chars():
    long_text = "x" * 200
    members = [
        _s("value", 0.05, trim_reason=long_text),
    ]
    out = resolve_ticker(members, curr=0.10, n_available=1, ctx=_ctx())
    assert len(out.trim_reason) <= 120


def test_trim_reasons_none_when_decision_is_hold():
    members = [
        _s("value", 0.099, trim_reason="should not appear"),
        _s("momentum", 0.10),
        _s("contrarian", 0.10),
    ]
    out = resolve_ticker(members, curr=0.10, n_available=3, ctx=_ctx())
    # Avg ~ 0.0997, abs(diff) < SIZE_CHANGE_EPSILON=0.02 → "hold"
    assert out.decision == "hold"
    assert out.trim_reason is None


# ── Aggregator-level: trim_reasons dict on StrategistDecision ─────────────

def test_aggregator_writes_trim_reasons_dict():
    """Full aggregate() builds trim_reasons dict on StrategistDecision for trim tickers."""
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_s("value", 0.05, trim_reason="V: book")],
        "momentum":   [_s("momentum", 0.05, trim_reason="M: cool")],
        "contrarian": [_s("contrarian", 0.05)],
    }
    positions = {"AAPL": 0.10}

    agg = CouncilAggregator()
    decision, telemetry = agg.aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions=positions,
        prior_thesis="prior",
        tick_context_factory=lambda t: _ctx(),
    )
    assert "AAPL" in decision.trim_reasons
    assert "V:" in decision.trim_reasons["AAPL"]
    assert "M:" in decision.trim_reasons["AAPL"]
    assert telemetry.quorum_decisions["AAPL"] == "trim"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_aggregator_clamp_and_trims.py -v`
Expected: FAIL — `_PerTickerOutcome` has no `trim_reason` field; aggregator doesn't populate `decision.trim_reasons`.

- [ ] **Step 3: Extend `_PerTickerOutcome` and the trim branch in aggregator.py**

In `src/agents/strategist/aggregator.py`, modify the `_PerTickerOutcome` dataclass and the `resolve_ticker` function:

```python
from dataclasses import dataclass
from orchestrator.state import MIN_HELD_WEIGHT


@dataclass(frozen=True)
class _PerTickerOutcome:
    decision: str                         # "open" | "close" | "trim" | "add" | "hold"
    final_weight: float
    thesis: PositionThesis | None
    close_reason: str | None
    disagreement: float
    # NEW field at the END with default — preserves Spec 1's positional callers.
    trim_reason: str | None = None        # populated only when decision == "trim"


def _first_trim_reason(members: list[MemberStance]) -> str | None:
    """Concatenate trim_reason text from all members proposing trim, V->M->C order, cap 120."""
    order = ("value", "momentum", "contrarian")
    proposing = [
        m for m in sorted(members, key=lambda s: order.index(s.persona))
        if m.trim_reason
    ]
    if not proposing:
        return None
    parts = [f"{_PERSONA_LETTER[m.persona]}: {m.trim_reason}" for m in proposing]
    return " | ".join(parts)[:120]


def resolve_ticker(
    members: list[MemberStance],
    *,
    curr: float,
    n_available: int,
    ctx: dict[str, Any],
) -> _PerTickerOutcome:
    """Classify the per-ticker transition and produce the final weight + artefacts."""
    prefs = clamp_preferred_weights([m.preferred_weight for m in members])
    convs = [m.conviction for m in members]
    proposes_open = sum(1 for p in prefs if p > OPEN_EPSILON)
    proposes_close = sum(1 for p in prefs if p < CLOSE_EPSILON)
    disagreement = _variance(prefs)

    # ── Currently flat ──────────────────────────────────────────────
    if curr <= CLOSE_EPSILON:
        if proposes_open >= effective_open_quorum(n_available):
            final = confidence_weighted_avg(prefs, convs)
            ctx_with_final = {**ctx, "final_weight": final}
            thesis = build_thesis_from_proposers(members, ctx_with_final)
            return _PerTickerOutcome("open", final, thesis, None, disagreement)
        return _PerTickerOutcome("hold", 0.0, None, None, disagreement)

    # ── Currently held ──────────────────────────────────────────────
    if proposes_close >= CLOSE_QUORUM:
        return _PerTickerOutcome(
            "close", 0.0, None, _first_close_reason(members), disagreement
        )

    final = confidence_weighted_avg(prefs, convs)

    # Defensive clamp — averaging arithmetic must not flat-close a held position
    # without the close-quorum branch having fired.
    if final < MIN_HELD_WEIGHT:
        final = MIN_HELD_WEIGHT

    delta = final - curr
    if abs(delta) < SIZE_CHANGE_EPSILON:
        return _PerTickerOutcome("hold", final, None, None, disagreement)
    if delta < 0:
        # trim_reason is the NEW (last, kw-or-positional) field on _PerTickerOutcome.
        return _PerTickerOutcome(
            "trim", final, None, None, disagreement,
            trim_reason=_first_trim_reason(members),
        )
    return _PerTickerOutcome("add", final, None, None, disagreement)
```

Then in the `CouncilAggregator.aggregate()` body, populate `trim_reasons` from each ticker's outcome:
```python
trim_reasons: dict[str, str] = {}

for ticker in tickers:
    # ... existing per-ticker resolve loop ...
    outcome = resolve_ticker(...)
    final_weights[ticker] = outcome.final_weight
    quorum_decisions[ticker] = outcome.decision
    disagreement[ticker] = outcome.disagreement
    if outcome.thesis is not None:
        new_positions[ticker] = outcome.thesis
    if outcome.close_reason is not None:
        close_reasons[ticker] = outcome.close_reason
    if outcome.trim_reason is not None:
        trim_reasons[ticker] = outcome.trim_reason
    # ...

# When constructing StrategistDecision, pass trim_reasons:
decision = StrategistDecision(
    target_weights=final_weights,
    decision_tag=decision_tag,
    reasoning=reasoning,
    updated_thesis=prior_thesis,
    confidence=confidence,
    new_positions=new_positions,
    close_reasons=close_reasons,
    trim_reasons=trim_reasons,
)
```

Spec 1's `_PERSONA_LETTER` map should already exist (`{"value": "V", "momentum": "M", "contrarian": "C"}`).

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_aggregator_clamp_and_trims.py -v`
Expected: tests PASS (some may need adjustment to match Spec 1's `_PerTickerOutcome` constructor signature; update tuple positions if Spec 1 used positional args).

### 8b — `build_thesis_from_proposers` seeds 5 new fields

- [ ] **Step 5: Write the failing thesis-seeding test**

Create `tests/unit/test_aggregator_thesis_seeding.py`:
```python
"""Aggregator — build_thesis_from_proposers seeds running fields + opened_tick_id."""
from __future__ import annotations

from datetime import datetime, timezone

from agents.strategist.aggregator import build_thesis_from_proposers
from agents.strategist.member_schema import MemberStance


def _now():
    return datetime.now(tz=timezone.utc)


def _s(persona, **kw):
    return MemberStance(
        ticker="AAPL", persona=persona, preferred_weight=0.10,
        conviction=0.7, rationale="bull",
        horizon="swing", target_price=210.0, stop_price=185.0,
        catalyst="earnings", **kw,
    )


def _ctx_with_extras():
    return {
        "ticker": "AAPL",
        "opened_at": _now(),
        "opened_price": 200.0,
        "opened_tag": "council_open",
        "last_reviewed_at": _now(),
        "spy_price": 500.0,
        "tick_id": "tick_xyz",
        "final_weight": 0.08,
    }


def test_thesis_seeds_running_max_min_to_opened_price():
    members = [_s("value"), _s("momentum"), _s("contrarian")]
    thesis = build_thesis_from_proposers(members, _ctx_with_extras())
    assert thesis.running_max_price == 200.0
    assert thesis.running_min_price == 200.0


def test_thesis_seeds_spy_price_at_open_from_ctx():
    members = [_s("value"), _s("momentum"), _s("contrarian")]
    thesis = build_thesis_from_proposers(members, _ctx_with_extras())
    assert thesis.spy_price_at_open == 500.0


def test_thesis_seeds_weight_at_open_from_ctx_final_weight():
    members = [_s("value"), _s("momentum"), _s("contrarian")]
    thesis = build_thesis_from_proposers(members, _ctx_with_extras())
    assert thesis.weight_at_open == 0.08


def test_thesis_seeds_opened_tick_id_from_ctx():
    members = [_s("value"), _s("momentum"), _s("contrarian")]
    thesis = build_thesis_from_proposers(members, _ctx_with_extras())
    assert thesis.opened_tick_id == "tick_xyz"


def test_thesis_seeds_zero_when_ctx_extras_missing():
    """Missing extras default to legacy-zero — does not raise."""
    minimal_ctx = {
        "ticker": "AAPL",
        "opened_at": _now(),
        "opened_price": 200.0,
        "opened_tag": "council_open",
        "last_reviewed_at": _now(),
    }
    members = [_s("value"), _s("momentum"), _s("contrarian")]
    thesis = build_thesis_from_proposers(members, minimal_ctx)
    assert thesis.spy_price_at_open == 0.0
    assert thesis.weight_at_open == 0.0
    assert thesis.opened_tick_id == ""
```

- [ ] **Step 6: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_aggregator_thesis_seeding.py -v`
Expected: FAIL — Spec 1's `build_thesis_from_proposers` doesn't set the new fields.

- [ ] **Step 7: Extend `build_thesis_from_proposers`**

**This is a diff against Spec 1's existing `build_thesis_from_proposers`. Preserve every line of the existing implementation; the only changes are five additional keyword arguments to the `PositionThesis(...)` constructor at the end of the function.**

Open `src/agents/strategist/aggregator.py`. Locate `build_thesis_from_proposers` (created by Spec 1 — it currently constructs a `PositionThesis` with `ticker`, `opened_at`, `opened_price`, `opened_tag`, `rationale`, `horizon`, `target_price`, `stop_price`, `catalyst`, `last_reviewed_at`, `last_review_note`). Inside the `PositionThesis(...)` call, append the five new kwargs:

```python
    return PositionThesis(
        # ↓↓↓ Spec 1 fields — DO NOT CHANGE ↓↓↓
        ticker=ctx["ticker"],
        opened_at=ctx["opened_at"],
        opened_price=ctx["opened_price"],
        opened_tag=ctx["opened_tag"],
        rationale=rationale_concat,                  # Spec 1's existing variable
        horizon=shortest_horizon,                    # Spec 1's existing variable
        target_price=min_target,                     # Spec 1's existing variable
        stop_price=max_stop,                         # Spec 1's existing variable
        catalyst=first_catalyst,                     # Spec 1's existing variable
        last_reviewed_at=ctx["last_reviewed_at"],
        last_review_note="opened by council",
        # ↑↑↑ Spec 1 fields ↑↑↑

        # ↓↓↓ Exit-rules-and-telemetry — APPEND ONLY ↓↓↓
        running_max_price=ctx["opened_price"],       # init max to entry price
        running_min_price=ctx["opened_price"],       # init min to entry price
        spy_price_at_open=ctx.get("spy_price", 0.0),
        weight_at_open=ctx.get("final_weight", 0.0),
        opened_tick_id=ctx.get("tick_id", ""),
    )
```

If Spec 1's local variable names differ (e.g. `rationale_text` instead of `rationale_concat`), keep Spec 1's names — only the appended kwargs are new.

- [ ] **Step 8: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_aggregator_thesis_seeding.py -v`
Expected: 5 tests PASS.

### 8c — Aggregator's tick_context_factory reads spy_price + tick_id from state

- [ ] **Step 9: Update CouncilAggregator's per-ticker context construction**

The aggregator BaseAgent (Spec 1) has a `tick_context_factory(ticker)` callable. Extend its construction inside `CouncilAggregator._run_async_impl` to pull `spy_price` and `tick_id` from session state:

```python
async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
    state = ctx.session.state
    spy_price = state.get("spy_price", 0.0)
    tick_id = state.get("tick_id", "")
    # ... existing factory ...
    def factory(ticker: str) -> dict:
        return {
            "ticker": ticker,
            "opened_at": datetime.now(tz=timezone.utc),
            "opened_price": _resolve_opened_price(ticker, state),  # Spec 1's logic
            "opened_tag": _resolve_opened_tag(state),               # Spec 1's logic
            "last_reviewed_at": datetime.now(tz=timezone.utc),
            "spy_price": spy_price,    # NEW
            "tick_id": tick_id,        # NEW
            # final_weight is added inside resolve_ticker, not here
        }
    decision, telemetry = self.aggregate(
        stances_by_persona=...,
        tickers=...,
        positions=...,
        prior_thesis=state.get("thesis", ""),
        tick_context_factory=factory,
    )
    # ... write to state ...
```

If Spec 1's aggregator agent inlines context construction differently, locate the factory and add the two new keys.

- [ ] **Step 10: Run all aggregator tests together**

Run: `.venv/Scripts/python -m pytest tests/unit/test_aggregator_clamp_and_trims.py tests/unit/test_aggregator_thesis_seeding.py -v`
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add tests/unit/test_aggregator_clamp_and_trims.py tests/unit/test_aggregator_thesis_seeding.py src/agents/strategist/aggregator.py
git commit -m "feat(strategist): aggregator MIN_HELD clamp + trim_reasons + thesis seeding"
```

---

## Phase 9: New ORM tables

Three independent tables, each with its own test file. Doing them sequentially.

### 9a — `CouncilStanceRow`

**Files:**
- Test: `tests/unit/test_council_stance_persistence.py`
- Modify: `src/orchestrator/persistence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_council_stance_persistence.py`:
```python
"""CouncilStanceRow — ORM round-trip + filtered queries."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, CouncilStanceRow, save_council_stance


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _now():
    return datetime.now(tz=timezone.utc)


def test_council_stance_round_trip():
    session = _make_session()
    save_council_stance(session, {
        "tick_id": "tick_1",
        "recorded_at": _now(),
        "persona": "value",
        "ticker": "AAPL",
        "preferred_weight": 0.10,
        "conviction": 0.7,
        "rationale": "bull",
        "final_weight": 0.08,
        "quorum_decision": "open",
        "disagreement_score": 0.01,
    })
    session.commit()
    rows = session.query(CouncilStanceRow).all()
    assert len(rows) == 1
    assert rows[0].persona == "value"
    assert rows[0].quorum_decision == "open"


def test_council_stance_optional_fields_nullable():
    session = _make_session()
    save_council_stance(session, {
        "tick_id": "tick_1",
        "recorded_at": _now(),
        "persona": "momentum",
        "ticker": "AAPL",
        "preferred_weight": 0.10,
        "conviction": 0.7,
        "rationale": "trend",
        "final_weight": 0.08,
        "quorum_decision": "open",
        "disagreement_score": 0.01,
        "horizon": "swing",
        "target_price": 220.0,
        "stop_price": 190.0,
        "catalyst": "breakout",
        "trim_reason": None,
        "close_reason": None,
        "degraded_member": None,
    })
    session.commit()
    row = session.query(CouncilStanceRow).first()
    assert row.target_price == 220.0
    assert row.trim_reason is None


def test_council_stance_filter_by_persona_and_ticker():
    session = _make_session()
    for persona in ["value", "momentum", "contrarian"]:
        save_council_stance(session, {
            "tick_id": "tick_1",
            "recorded_at": _now(),
            "persona": persona,
            "ticker": "AAPL",
            "preferred_weight": 0.10,
            "conviction": 0.7,
            "rationale": "x",
            "final_weight": 0.08,
            "quorum_decision": "open",
            "disagreement_score": 0.0,
        })
    session.commit()
    value_rows = (
        session.query(CouncilStanceRow)
        .filter(CouncilStanceRow.persona == "value")
        .filter(CouncilStanceRow.ticker == "AAPL")
        .all()
    )
    assert len(value_rows) == 1
    assert value_rows[0].persona == "value"
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/test_council_stance_persistence.py -v`
Expected: FAIL.

- [ ] **Step 3: Add CouncilStanceRow + save_council_stance to persistence.py**

Append to `src/orchestrator/persistence.py`:
```python
# ── CouncilStanceRow ──────────────────────────────────────────────────

class CouncilStanceRow(Base):
    __tablename__ = "council_stances"

    id: Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]       = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    persona: Mapped[str]       = mapped_column(String, index=True)
    ticker: Mapped[str]        = mapped_column(String, index=True)

    # Stance fields
    preferred_weight: Mapped[float] = mapped_column(Float)
    conviction: Mapped[float]  = mapped_column(Float)
    rationale: Mapped[str]     = mapped_column(String)

    # Lifecycle hints (nullable)
    horizon: Mapped[str | None]      = mapped_column(String, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    catalyst: Mapped[str | None]     = mapped_column(String, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    trim_reason: Mapped[str | None]  = mapped_column(String, nullable=True)

    # Aggregator outcome (denormalised — same value for the 3 stance rows of a given ticker)
    final_weight: Mapped[float] = mapped_column(Float)
    quorum_decision: Mapped[str] = mapped_column(String, index=True)
    disagreement_score: Mapped[float] = mapped_column(Float)
    degraded_member: Mapped[str | None] = mapped_column(String, nullable=True)


def save_council_stance(session: Session, entry: dict) -> None:
    """Persist one persona-ticker stance. Caller commits."""
    row = CouncilStanceRow(**entry)
    session.add(row)
    session.flush()
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_council_stance_persistence.py -v`
Expected: 3 tests PASS.

### 9b — `StrategistDecisionRow`

**Files:**
- Test: `tests/unit/test_strategist_decision_persistence.py`
- Modify: `src/orchestrator/persistence.py`

- [ ] **Step 5: Write the failing test**

Create `tests/unit/test_strategist_decision_persistence.py`:
```python
"""StrategistDecisionRow — per-tick decision persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, StrategistDecisionRow, save_strategist_decision


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _now():
    return datetime.now(tz=timezone.utc)


def test_strategist_decision_round_trip():
    session = _make_session()
    save_strategist_decision(session, {
        "tick_id": "tick_1",
        "recorded_at": _now(),
        "decision_tag": "council_1o_0c_0t_0a",
        "reasoning": "opened AAPL",
        "updated_thesis": "bull market",
        "confidence": 0.7,
        "target_weights_json": json.dumps({"AAPL": 0.08}),
        "new_positions_json": json.dumps({}),
        "close_reasons_json": json.dumps({}),
        "trim_reasons_json": json.dumps({}),
        "mean_disagreement": 0.01,
        "degraded_member": None,
    })
    session.commit()
    rows = session.query(StrategistDecisionRow).all()
    assert len(rows) == 1
    assert rows[0].decision_tag == "council_1o_0c_0t_0a"


def test_strategist_decision_tick_id_unique():
    """tick_id has unique constraint — duplicate insert raises."""
    import sqlalchemy.exc
    import pytest

    session = _make_session()
    payload = {
        "tick_id": "tick_dup",
        "recorded_at": _now(),
        "decision_tag": "x",
        "reasoning": "y",
        "updated_thesis": "z",
        "confidence": 0.5,
        "target_weights_json": "{}",
        "new_positions_json": "{}",
        "close_reasons_json": "{}",
        "trim_reasons_json": "{}",
        "mean_disagreement": 0.0,
        "degraded_member": None,
    }
    save_strategist_decision(session, payload)
    session.commit()
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        save_strategist_decision(session, payload)
        session.commit()


def test_strategist_decision_query_by_tag():
    session = _make_session()
    for tag in ["council_1o_0c_0t_0a", "council_0o_1c_0t_0a", "council_0o_0c_1t_0a"]:
        save_strategist_decision(session, {
            "tick_id": tag,    # using tag as tick_id for uniqueness
            "recorded_at": _now(),
            "decision_tag": tag,
            "reasoning": "x",
            "updated_thesis": "y",
            "confidence": 0.5,
            "target_weights_json": "{}",
            "new_positions_json": "{}",
            "close_reasons_json": "{}",
            "trim_reasons_json": "{}",
            "mean_disagreement": 0.0,
            "degraded_member": None,
        })
    session.commit()
    closes = (
        session.query(StrategistDecisionRow)
        .filter(StrategistDecisionRow.decision_tag.like("%_1c_%"))
        .all()
    )
    assert len(closes) == 1
```

- [ ] **Step 6: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/test_strategist_decision_persistence.py -v`
Expected: FAIL.

- [ ] **Step 7: Add StrategistDecisionRow + save_strategist_decision**

Append to `src/orchestrator/persistence.py`:
```python
# ── StrategistDecisionRow ─────────────────────────────────────────────

class StrategistDecisionRow(Base):
    __tablename__ = "strategist_decisions"

    id: Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]       = mapped_column(String, unique=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    decision_tag: Mapped[str]  = mapped_column(String, index=True)
    reasoning: Mapped[str]     = mapped_column(String)
    updated_thesis: Mapped[str] = mapped_column(String)
    confidence: Mapped[float]  = mapped_column(Float)
    target_weights_json: Mapped[str] = mapped_column(String)
    new_positions_json: Mapped[str]  = mapped_column(String)
    close_reasons_json: Mapped[str]  = mapped_column(String, default="{}")
    trim_reasons_json: Mapped[str]   = mapped_column(String, default="{}")
    mean_disagreement: Mapped[float] = mapped_column(Float)
    degraded_member: Mapped[str | None] = mapped_column(String, nullable=True)


def save_strategist_decision(session: Session, entry: dict) -> None:
    """Persist one per-tick strategist decision. Caller commits."""
    row = StrategistDecisionRow(**entry)
    session.add(row)
    session.flush()
```

- [ ] **Step 8: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_strategist_decision_persistence.py -v`
Expected: 3 tests PASS.

### 9c — `PositionPackRow`

**Files:**
- Test: `tests/unit/test_position_pack_persistence.py`
- Modify: `src/orchestrator/persistence.py`

- [ ] **Step 9: Write the failing test**

Create `tests/unit/test_position_pack_persistence.py`:
```python
"""PositionPackRow — per held position per tick."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, PositionPackRow, save_position_pack


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _now():
    return datetime.now(tz=timezone.utc)


def _payload(**over):
    base = dict(
        tick_id="tick_1",
        recorded_at=_now(),
        ticker="AAPL",
        opened_at=_now(),
        opened_price=192.0,
        target_price=210.0,
        stop_price=185.0,
        horizon="swing",
        catalyst="earnings",
        current_price=206.0,
        current_weight=0.08,
        weight_at_open=0.08,
        unrealised_pnl_pct=7.3,
        unrealised_pnl_dollar=120.0,
        ticks_held=72,
        hours_held=72.0,
        distance_to_target_pct=-1.9,
        distance_to_stop_pct=11.4,
        target_reached=False,
        stop_breached=False,
        max_run_up_pct=9.1,
        max_drawdown_pct=-2.3,
        spy_return_since_open_pct=3.2,
        excess_return_since_open_pct=4.1,
        council_action="hold",
        rule_overridden=False,
    )
    base.update(over)
    return base


def test_pack_row_round_trip():
    session = _make_session()
    save_position_pack(session, _payload())
    session.commit()
    rows = session.query(PositionPackRow).all()
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"


def test_pack_row_rule_overridden_indexed_for_query():
    session = _make_session()
    save_position_pack(session, _payload(stop_breached=True, council_action="hold", rule_overridden=True))
    save_position_pack(session, _payload(tick_id="tick_2", stop_breached=False, rule_overridden=False))
    session.commit()
    overrides = session.query(PositionPackRow).filter(PositionPackRow.rule_overridden.is_(True)).all()
    assert len(overrides) == 1


def test_pack_row_nullable_thresholds():
    session = _make_session()
    save_position_pack(session, _payload(
        target_price=None,
        stop_price=None,
        distance_to_target_pct=None,
        distance_to_stop_pct=None,
        target_reached=None,
        stop_breached=None,
    ))
    session.commit()
    row = session.query(PositionPackRow).first()
    assert row.target_price is None
    assert row.stop_breached is None
```

- [ ] **Step 10: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/test_position_pack_persistence.py -v`
Expected: FAIL.

- [ ] **Step 11: Add PositionPackRow + save_position_pack**

Append to `src/orchestrator/persistence.py`:
```python
# ── PositionPackRow ───────────────────────────────────────────────────

class PositionPackRow(Base):
    __tablename__ = "position_packs"

    id: Mapped[int]                = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]           = mapped_column(String, index=True)
    recorded_at: Mapped[datetime]  = mapped_column(DateTime)
    ticker: Mapped[str]            = mapped_column(String, index=True)

    opened_at: Mapped[datetime]    = mapped_column(DateTime)
    opened_price: Mapped[float]    = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    horizon: Mapped[str]           = mapped_column(String)
    catalyst: Mapped[str | None]   = mapped_column(String, nullable=True)

    current_price: Mapped[float]   = mapped_column(Float)
    current_weight: Mapped[float]  = mapped_column(Float)
    weight_at_open: Mapped[float]  = mapped_column(Float)

    unrealised_pnl_pct: Mapped[float]    = mapped_column(Float)
    unrealised_pnl_dollar: Mapped[float] = mapped_column(Float)
    ticks_held: Mapped[int]        = mapped_column(Integer)
    hours_held: Mapped[float]      = mapped_column(Float)

    distance_to_target_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_stop_pct: Mapped[float | None]   = mapped_column(Float, nullable=True)
    target_reached: Mapped[bool | None]          = mapped_column(Boolean, nullable=True)
    stop_breached: Mapped[bool | None]           = mapped_column(Boolean, nullable=True)

    max_run_up_pct: Mapped[float]  = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    spy_return_since_open_pct: Mapped[float] = mapped_column(Float)
    excess_return_since_open_pct: Mapped[float] = mapped_column(Float)

    council_action: Mapped[str]    = mapped_column(String, index=True)
    rule_overridden: Mapped[bool]  = mapped_column(Boolean, index=True)


def save_position_pack(session: Session, entry: dict) -> None:
    """Persist one per-tick per-position pack snapshot. Caller commits."""
    row = PositionPackRow(**entry)
    session.add(row)
    session.flush()
```

- [ ] **Step 12: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_position_pack_persistence.py -v`
Expected: 3 tests PASS.

- [ ] **Step 13: Commit (all of Phase 9)**

```bash
git add tests/unit/test_council_stance_persistence.py tests/unit/test_strategist_decision_persistence.py tests/unit/test_position_pack_persistence.py src/orchestrator/persistence.py
git commit -m "feat(persistence): add CouncilStance, StrategistDecision, PositionPack ORM rows"
```

---

## Phase 10: TradeLogRow attribution + executor update

**Files:**
- Test: `tests/unit/test_trade_log_attribution.py`
- Modify: `src/orchestrator/persistence.py`
- Modify: `src/agents/executor/agent.py`

- [ ] **Step 1: Write the failing test (persistence)**

Create `tests/unit/test_trade_log_attribution.py`:
```python
"""TradeLogRow — opening_tick_id / closing_tick_id for outcome attribution."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, TradeLogRow, save_trade_log_entry


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _now():
    return datetime.now(tz=timezone.utc)


def _entry(**over):
    base = dict(
        ticker="AAPL",
        opened_at=_now(),
        closed_at=_now(),
        opened_price=192.0,
        closed_price=210.0,
        pnl_dollar=18.0,
        pnl_pct=9.4,
        holding_period_hours=72,
        horizon_intent="swing",
        opened_tag="council_open",
        closed_tag="council_close",
        opened_rationale="bull",
        close_reason="target reached",
        catalyst_realised=True,
        opening_tick_id="tick_open_abc",
        closing_tick_id="tick_close_xyz",
    )
    base.update(over)
    return base


def test_trade_log_persists_attribution_keys():
    session = _make_session()
    save_trade_log_entry(session, _entry())
    session.commit()
    row = session.query(TradeLogRow).first()
    assert row.opening_tick_id == "tick_open_abc"
    assert row.closing_tick_id == "tick_close_xyz"


def test_trade_log_filter_by_opening_tick_id():
    session = _make_session()
    save_trade_log_entry(session, _entry(opening_tick_id="tick_A"))
    save_trade_log_entry(session, _entry(opening_tick_id="tick_B", ticker="MSFT"))
    session.commit()
    rows = (
        session.query(TradeLogRow)
        .filter(TradeLogRow.opening_tick_id == "tick_A")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"


def test_trade_log_join_to_strategist_decision():
    """opening_tick_id can be joined against StrategistDecisionRow.tick_id."""
    from orchestrator.persistence import StrategistDecisionRow, save_strategist_decision
    import json
    session = _make_session()

    # Seed a strategist decision and a trade log row sharing the tick_id.
    save_strategist_decision(session, {
        "tick_id": "tick_open_abc",
        "recorded_at": _now(),
        "decision_tag": "council_1o_0c_0t_0a",
        "reasoning": "opened",
        "updated_thesis": "x",
        "confidence": 0.7,
        "target_weights_json": json.dumps({"AAPL": 0.08}),
        "new_positions_json": "{}",
        "close_reasons_json": "{}",
        "trim_reasons_json": "{}",
        "mean_disagreement": 0.0,
        "degraded_member": None,
    })
    save_trade_log_entry(session, _entry(opening_tick_id="tick_open_abc"))
    session.commit()

    joined = (
        session.query(TradeLogRow, StrategistDecisionRow)
        .join(StrategistDecisionRow, TradeLogRow.opening_tick_id == StrategistDecisionRow.tick_id)
        .all()
    )
    assert len(joined) == 1
    trade, decision = joined[0]
    assert trade.ticker == "AAPL"
    assert decision.decision_tag.startswith("council_1o")
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_trade_log_attribution.py -v`
Expected: FAIL — `opening_tick_id`/`closing_tick_id` not on `TradeLogRow`.

- [ ] **Step 3: Add the two columns to TradeLogRow**

In `src/orchestrator/persistence.py`, modify `TradeLogRow`:
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

    # Outcome attribution join keys (added in exit-rules-and-telemetry spec).
    opening_tick_id: Mapped[str] = mapped_column(String, index=True, default="")
    closing_tick_id: Mapped[str] = mapped_column(String, index=True, default="")
```

The defaults (`""`) keep the existing `test_trade_log.py` test passing; production callers always provide real values from the executor update below.

- [ ] **Step 4: Run, expect green for the new test**

Run: `.venv/Scripts/python -m pytest tests/unit/test_trade_log_attribution.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run the EXISTING trade-log test to confirm no regression**

Run: `.venv/Scripts/python -m pytest tests/unit/test_trade_log.py -v`
Expected: PASS (defaults handle the older payload shape).

- [ ] **Step 6: Update the executor to populate the new fields**

In `src/agents/executor/agent.py`, modify the `save_trade_log_entry` call (currently lines 93-108):

```python
save_trade_log_entry(self.db_session, {
    "ticker":              order.ticker,
    "opened_at":           opened_at,
    "closed_at":           closed_at,
    "opened_price":        opened_price,
    "closed_price":        fill.price,
    "pnl_dollar":          (fill.price - opened_price) * fill.quantity,
    "pnl_pct":             pnl_pct,
    "holding_period_hours": holding_hours,
    "horizon_intent":      thesis.get("horizon") if isinstance(thesis, dict) else thesis.horizon,
    "opened_tag":          thesis.get("opened_tag") if isinstance(thesis, dict) else thesis.opened_tag,
    "closed_tag":          state.get("strategist_decision", {}).get("decision_tag", "unknown"),
    "opened_rationale":    thesis.get("rationale") if isinstance(thesis, dict) else thesis.rationale,
    "close_reason":        state.get("strategist_decision", {}).get("close_reasons", {}).get(order.ticker, ""),
    "catalyst_realised":   False,
    # Outcome attribution join keys (added in exit-rules-and-telemetry spec).
    "opening_tick_id":     thesis.get("opened_tick_id", "") if isinstance(thesis, dict) else getattr(thesis, "opened_tick_id", ""),
    "closing_tick_id":     tick_id,
})
```

`tick_id` is already in scope at executor line 36.

- [ ] **Step 7: Run executor integration tests to confirm no regression**

Run: `.venv/Scripts/python -m pytest tests/integration/test_executor_with_fake_broker.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_trade_log_attribution.py src/orchestrator/persistence.py src/agents/executor/agent.py
git commit -m "feat(persistence): TradeLogRow opening/closing tick_id + executor population"
```

---

## Phase 11: CouncilTelemetryWriter agent

**Files:**
- Test: `tests/unit/test_telemetry_writer.py`
- Create: `src/agents/strategist/telemetry_writer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_telemetry_writer.py`:
```python
"""CouncilTelemetryWriter — flushes CouncilStanceRow + StrategistDecisionRow each tick."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.strategist.telemetry_writer import (
    CouncilTelemetryWriter,
    build_council_telemetry_writer,
)
from orchestrator.persistence import (
    Base,
    CouncilStanceRow,
    StrategistDecisionRow,
)


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _now():
    return datetime.now(tz=timezone.utc)


def _make_ctx(state):
    return SimpleNamespace(session=SimpleNamespace(state=state))


def _run(writer, state):
    async def runner():
        async for _ in writer._run_async_impl(_make_ctx(state)):
            pass
    asyncio.run(runner())


def _state_full():
    """Realistic session state after the council has run."""
    return {
        "tick_id": "tick_1",
        "strategist_decision": {
            "target_weights": {"AAPL": 0.08, "MSFT": 0.0},
            "decision_tag": "council_1o_0c_0t_0a",
            "reasoning": "opened AAPL",
            "updated_thesis": "bull",
            "confidence": 0.7,
            "new_positions": {},
            "close_reasons": {},
            "trim_reasons": {},
        },
        "council_telemetry": {
            "stances": [
                {"ticker": "AAPL", "persona": "value",      "preferred_weight": 0.10, "conviction": 0.7, "rationale": "value"},
                {"ticker": "AAPL", "persona": "momentum",   "preferred_weight": 0.10, "conviction": 0.7, "rationale": "trend"},
                {"ticker": "AAPL", "persona": "contrarian", "preferred_weight": 0.05, "conviction": 0.5, "rationale": "skeptic"},
                {"ticker": "MSFT", "persona": "value",      "preferred_weight": 0.0,  "conviction": 0.5, "rationale": "expensive"},
                {"ticker": "MSFT", "persona": "momentum",   "preferred_weight": 0.0,  "conviction": 0.5, "rationale": "weak"},
                {"ticker": "MSFT", "persona": "contrarian", "preferred_weight": 0.0,  "conviction": 0.5, "rationale": "no edge"},
            ],
            "quorum_decisions": {"AAPL": "open", "MSFT": "hold"},
            "disagreement_score": {"AAPL": 0.0008, "MSFT": 0.0},
            "degraded_member": None,
        },
    }


def test_writer_persists_one_decision_row():
    session = _make_session()
    writer = build_council_telemetry_writer(session)
    state = _state_full()
    _run(writer, state)
    decisions = session.query(StrategistDecisionRow).all()
    assert len(decisions) == 1
    assert decisions[0].decision_tag == "council_1o_0c_0t_0a"
    assert decisions[0].tick_id == "tick_1"


def test_writer_persists_one_stance_row_per_persona_per_ticker():
    session = _make_session()
    writer = build_council_telemetry_writer(session)
    state = _state_full()
    _run(writer, state)
    stances = session.query(CouncilStanceRow).all()
    assert len(stances) == 6   # 2 tickers x 3 personas


def test_writer_denormalises_outcome_fields_across_personas_for_one_ticker():
    session = _make_session()
    writer = build_council_telemetry_writer(session)
    state = _state_full()
    _run(writer, state)
    aapl_rows = session.query(CouncilStanceRow).filter(CouncilStanceRow.ticker == "AAPL").all()
    assert len(aapl_rows) == 3
    # All three rows share the same final_weight + quorum_decision.
    assert {r.final_weight for r in aapl_rows} == {0.08}
    assert {r.quorum_decision for r in aapl_rows} == {"open"}


def test_writer_skips_when_no_db_session():
    writer = CouncilTelemetryWriter(db_session=None)
    state = _state_full()
    # Should not raise.
    _run(writer, state)


def test_writer_skips_when_no_decision_in_state():
    session = _make_session()
    writer = build_council_telemetry_writer(session)
    state = {"tick_id": "tick_1"}    # no strategist_decision
    _run(writer, state)
    assert session.query(StrategistDecisionRow).count() == 0
    assert session.query(CouncilStanceRow).count() == 0


def test_writer_propagates_degraded_member():
    session = _make_session()
    writer = build_council_telemetry_writer(session)
    state = _state_full()
    state["council_telemetry"]["degraded_member"] = "contrarian"
    _run(writer, state)
    rows = session.query(CouncilStanceRow).all()
    assert all(r.degraded_member == "contrarian" for r in rows)
    decision = session.query(StrategistDecisionRow).first()
    assert decision.degraded_member == "contrarian"
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/test_telemetry_writer.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement telemetry_writer.py**

Create `src/agents/strategist/telemetry_writer.py`:
```python
"""CouncilTelemetryWriter — persists per-tick council deliberation to DB."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event


class CouncilTelemetryWriter(BaseAgent):
    """ADK BaseAgent — flushes CouncilStanceRow + StrategistDecisionRow each tick.

    Reads:
      state["strategist_decision"] (dict-form StrategistDecision)
      state["council_telemetry"]   (dict-form CouncilTelemetry)
      state["tick_id"]
    Writes:
      one row per persona x ticker into council_stances
      one row into strategist_decisions
    """

    name: str = "CouncilTelemetryWriter"
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if self.db_session is None:
            return
            yield  # pragma: no cover — generator gate

        from orchestrator.persistence import (
            save_council_stance,
            save_strategist_decision,
        )

        state = ctx.session.state
        decision = state.get("strategist_decision")
        telemetry = state.get("council_telemetry")
        tick_id = state.get("tick_id", "unknown")

        if decision is None or telemetry is None:
            return
            yield

        recorded_at = datetime.now(tz=timezone.utc)

        # ── StrategistDecisionRow ──
        d = decision if isinstance(decision, dict) else decision.model_dump()
        t = telemetry if isinstance(telemetry, dict) else telemetry.model_dump()

        mean_disagreement = (
            sum(t.get("disagreement_score", {}).values())
            / max(len(t.get("disagreement_score", {})), 1)
        )

        save_strategist_decision(self.db_session, {
            "tick_id": tick_id,
            "recorded_at": recorded_at,
            "decision_tag": d.get("decision_tag", ""),
            "reasoning": d.get("reasoning", ""),
            "updated_thesis": d.get("updated_thesis", ""),
            "confidence": d.get("confidence", 0.0),
            "target_weights_json": json.dumps(d.get("target_weights", {})),
            "new_positions_json": json.dumps(d.get("new_positions", {}), default=str),
            "close_reasons_json": json.dumps(d.get("close_reasons", {})),
            "trim_reasons_json": json.dumps(d.get("trim_reasons", {})),
            "mean_disagreement": mean_disagreement,
            "degraded_member": t.get("degraded_member"),
        })

        # ── CouncilStanceRow per persona x ticker ──
        quorum = t.get("quorum_decisions", {})
        disagreement = t.get("disagreement_score", {})
        target_weights = d.get("target_weights", {})
        degraded = t.get("degraded_member")

        for stance in t.get("stances", []):
            ticker = stance["ticker"]
            save_council_stance(self.db_session, {
                "tick_id": tick_id,
                "recorded_at": recorded_at,
                "persona": stance["persona"],
                "ticker": ticker,
                "preferred_weight": stance["preferred_weight"],
                "conviction": stance["conviction"],
                "rationale": stance["rationale"],
                "horizon": stance.get("horizon"),
                "target_price": stance.get("target_price"),
                "stop_price": stance.get("stop_price"),
                "catalyst": stance.get("catalyst"),
                "close_reason": stance.get("close_reason"),
                "trim_reason": stance.get("trim_reason"),
                "final_weight": target_weights.get(ticker, 0.0),
                "quorum_decision": quorum.get(ticker, "hold"),
                "disagreement_score": disagreement.get(ticker, 0.0),
                "degraded_member": degraded,
            })

        self.db_session.commit()
        return
        yield  # required to make this an async generator


def build_council_telemetry_writer(db_session=None) -> CouncilTelemetryWriter:
    """Factory for the pipeline builder."""
    return CouncilTelemetryWriter(db_session=db_session)
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_telemetry_writer.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_telemetry_writer.py src/agents/strategist/telemetry_writer.py
git commit -m "feat(strategist): CouncilTelemetryWriter — persist stances + decisions"
```

---

## Phase 12: PositionPackWriter agent

**Files:**
- Test: `tests/unit/test_pack_writer.py`
- Create: `src/agents/strategist/pack_writer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_pack_writer.py`:
```python
"""PositionPackWriter — persists PositionPackRow with council_action + rule_overridden."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.strategist.pack_writer import (
    PositionPackWriter,
    build_position_pack_writer,
    derive_rule_overridden,
)
from orchestrator.persistence import Base, PositionPackRow


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_ctx(state):
    return SimpleNamespace(session=SimpleNamespace(state=state))


def _run(writer, state):
    async def runner():
        async for _ in writer._run_async_impl(_make_ctx(state)):
            pass
    asyncio.run(runner())


def _pack(**over):
    base = dict(
        ticker="AAPL",
        opened_at=datetime.now(tz=timezone.utc) - timedelta(hours=72),
        opened_price=192.0,
        opened_tag="council_open",
        horizon="swing",
        catalyst="earnings",
        rationale="bull",
        target_price=210.0,
        stop_price=185.0,
        last_review_note="ok",
        current_price=206.0,
        current_weight=0.08,
        weight_at_open=0.08,
        unrealised_pnl_dollar=120.0,
        unrealised_pnl_pct=7.3,
        ticks_held=72,
        hours_held=72.0,
        distance_to_target_pct=-1.9,
        distance_to_stop_pct=11.4,
        target_reached=False,
        stop_breached=False,
        max_price_since_open=210.0,
        min_price_since_open=190.0,
        max_run_up_pct=9.4,
        max_drawdown_pct=-1.0,
        spy_return_since_open_pct=3.0,
        excess_return_since_open_pct=4.3,
    )
    base.update(over)
    return base


# ── derive_rule_overridden() pure-function tests ────────────────────────

def test_rule_overridden_held_through_stop():
    """stop_breached AND council_action != 'close' → True."""
    assert derive_rule_overridden(stop_breached=True, target_reached=False, council_action="hold") is True
    assert derive_rule_overridden(stop_breached=True, target_reached=False, council_action="trim") is True
    assert derive_rule_overridden(stop_breached=True, target_reached=False, council_action="add") is True


def test_rule_overridden_normal_close_on_stop():
    """stop_breached AND council closed → not an override."""
    assert derive_rule_overridden(stop_breached=True, target_reached=False, council_action="close") is False


def test_rule_overridden_added_into_target():
    """target_reached AND council added → True."""
    assert derive_rule_overridden(stop_breached=False, target_reached=True, council_action="add") is True


def test_rule_overridden_trimmed_at_target():
    """target_reached AND council trimmed → NOT an override (natural response)."""
    assert derive_rule_overridden(stop_breached=False, target_reached=True, council_action="trim") is False


def test_rule_overridden_held_at_target():
    """target_reached AND council held → not an override (also natural)."""
    assert derive_rule_overridden(stop_breached=False, target_reached=True, council_action="hold") is False


def test_rule_overridden_normal_hold_no_triggers():
    assert derive_rule_overridden(stop_breached=False, target_reached=False, council_action="hold") is False


def test_rule_overridden_handles_none_flags():
    """Thesis without stop/target — flags are None — no override possible."""
    assert derive_rule_overridden(stop_breached=None, target_reached=None, council_action="hold") is False


# ── Writer behaviour ─────────────────────────────────────────────────────

def test_writer_persists_pack_with_council_action_filled():
    session = _make_session()
    writer = build_position_pack_writer(session)
    state = {
        "tick_id": "tick_1",
        "position_packs": {"AAPL": _pack()},
        "council_telemetry": {
            "quorum_decisions": {"AAPL": "hold"},
        },
    }
    _run(writer, state)
    rows = session.query(PositionPackRow).all()
    assert len(rows) == 1
    assert rows[0].council_action == "hold"
    assert rows[0].rule_overridden is False


def test_writer_marks_rule_overridden_for_held_through_stop():
    session = _make_session()
    writer = build_position_pack_writer(session)
    state = {
        "tick_id": "tick_1",
        "position_packs": {"AAPL": _pack(stop_breached=True)},
        "council_telemetry": {
            "quorum_decisions": {"AAPL": "hold"},
        },
    }
    _run(writer, state)
    row = session.query(PositionPackRow).first()
    assert row.rule_overridden is True


def test_writer_skips_when_no_db_session():
    writer = PositionPackWriter(db_session=None)
    state = {"position_packs": {"AAPL": _pack()}, "council_telemetry": {"quorum_decisions": {"AAPL": "hold"}}}
    _run(writer, state)


def test_writer_skips_when_no_packs_in_state():
    session = _make_session()
    writer = build_position_pack_writer(session)
    _run(writer, {"tick_id": "tick_1"})    # no position_packs key
    assert session.query(PositionPackRow).count() == 0


def test_writer_writes_one_row_per_pack():
    session = _make_session()
    writer = build_position_pack_writer(session)
    state = {
        "tick_id": "tick_1",
        "position_packs": {
            "AAPL": _pack(ticker="AAPL"),
            "MSFT": _pack(ticker="MSFT"),
        },
        "council_telemetry": {
            "quorum_decisions": {"AAPL": "hold", "MSFT": "trim"},
        },
    }
    _run(writer, state)
    rows = session.query(PositionPackRow).all()
    assert {r.ticker for r in rows} == {"AAPL", "MSFT"}
    actions = {r.ticker: r.council_action for r in rows}
    assert actions["AAPL"] == "hold"
    assert actions["MSFT"] == "trim"
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/test_pack_writer.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement pack_writer.py**

Create `src/agents/strategist/pack_writer.py`:
```python
"""PositionPackWriter — flushes PositionPackRow with council_action + rule_overridden."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event


def derive_rule_overridden(
    *,
    stop_breached: bool | None,
    target_reached: bool | None,
    council_action: str,
) -> bool:
    """Was a fired stop/target rule overridden by the council?

    Rules:
      - stop_breached AND council_action != "close" -> override (held through stop)
      - target_reached AND council_action == "add"  -> override (added into a target)
      - all other combinations are natural responses, not overrides
    """
    if stop_breached is True and council_action != "close":
        return True
    if target_reached is True and council_action == "add":
        return True
    return False


class PositionPackWriter(BaseAgent):
    """ADK BaseAgent — persists per-tick PositionPackRow."""

    name: str = "PositionPackWriter"
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if self.db_session is None:
            return
            yield  # pragma: no cover

        from orchestrator.persistence import save_position_pack

        state = ctx.session.state
        packs = state.get("position_packs", {})
        if not packs:
            return
            yield

        telemetry = state.get("council_telemetry", {})
        quorum = telemetry.get("quorum_decisions", {}) if isinstance(telemetry, dict) else telemetry.quorum_decisions
        tick_id = state.get("tick_id", "unknown")
        recorded_at = datetime.now(tz=timezone.utc)

        for ticker, pack_dict in packs.items():
            council_action = quorum.get(ticker, "hold")
            row = {
                "tick_id": tick_id,
                "recorded_at": recorded_at,
                "ticker": pack_dict["ticker"],
                "opened_at": pack_dict["opened_at"],
                "opened_price": pack_dict["opened_price"],
                "target_price": pack_dict.get("target_price"),
                "stop_price": pack_dict.get("stop_price"),
                "horizon": pack_dict["horizon"],
                "catalyst": pack_dict.get("catalyst"),
                "current_price": pack_dict["current_price"],
                "current_weight": pack_dict["current_weight"],
                "weight_at_open": pack_dict["weight_at_open"],
                "unrealised_pnl_pct": pack_dict["unrealised_pnl_pct"],
                "unrealised_pnl_dollar": pack_dict["unrealised_pnl_dollar"],
                "ticks_held": pack_dict["ticks_held"],
                "hours_held": pack_dict["hours_held"],
                "distance_to_target_pct": pack_dict.get("distance_to_target_pct"),
                "distance_to_stop_pct": pack_dict.get("distance_to_stop_pct"),
                "target_reached": pack_dict.get("target_reached"),
                "stop_breached": pack_dict.get("stop_breached"),
                "max_run_up_pct": pack_dict["max_run_up_pct"],
                "max_drawdown_pct": pack_dict["max_drawdown_pct"],
                "spy_return_since_open_pct": pack_dict["spy_return_since_open_pct"],
                "excess_return_since_open_pct": pack_dict["excess_return_since_open_pct"],
                "council_action": council_action,
                "rule_overridden": derive_rule_overridden(
                    stop_breached=pack_dict.get("stop_breached"),
                    target_reached=pack_dict.get("target_reached"),
                    council_action=council_action,
                ),
            }
            save_position_pack(self.db_session, row)

        self.db_session.commit()
        return
        yield  # required to make this an async generator


def build_position_pack_writer(db_session=None) -> PositionPackWriter:
    """Factory for the pipeline builder."""
    return PositionPackWriter(db_session=db_session)
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_pack_writer.py -v`
Expected: 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_pack_writer.py src/agents/strategist/pack_writer.py
git commit -m "feat(strategist): PositionPackWriter — persist packs with override flag"
```

---

## Phase 13: Pipeline wiring (outer)

**Files:**
- Test: `tests/unit/test_pipeline_wiring_exits.py`
- Modify: `src/orchestrator/pipeline.py`

The outer pipeline goes from 7 sub_agents to 9. The strategist node changes from a single LlmAgent to the council factory (broker-aware). The two new writers slot in immediately after the council.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_pipeline_wiring_exits.py`:
```python
"""Outer HourlyTick pipeline must include telemetry_writer + pack_writer after council."""
from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator.pipeline import build_pipeline


def test_pipeline_has_nine_sub_agents():
    broker = MagicMock()
    pipeline = build_pipeline(broker, db_session=None)
    assert len(pipeline.sub_agents) == 9


def test_pipeline_order_council_then_writers_then_riskgate():
    broker = MagicMock()
    pipeline = build_pipeline(broker, db_session=None)
    names = [a.name for a in pipeline.sub_agents]
    council_idx       = names.index("StrategistCouncil")
    telemetry_idx     = names.index("CouncilTelemetryWriter")
    pack_writer_idx   = names.index("PositionPackWriter")
    risk_gate_idx     = names.index("RiskGate")
    assert council_idx < telemetry_idx
    assert telemetry_idx < pack_writer_idx
    assert pack_writer_idx < risk_gate_idx


def test_pipeline_attribution_writer_remains_before_council():
    broker = MagicMock()
    pipeline = build_pipeline(broker, db_session=None)
    names = [a.name for a in pipeline.sub_agents]
    assert names.index("AttributionWriter") < names.index("StrategistCouncil")
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/test_pipeline_wiring_exits.py -v`
Expected: FAIL — old pipeline has 7 stages and a Strategist LlmAgent, not StrategistCouncil.

- [ ] **Step 3: Update pipeline.py**

Replace `_build_strategist` with the council factory, and insert the two new writers:
```python
"""Build the HourlyTick SequentialAgent pipeline."""
from __future__ import annotations

from google.adk.agents import SequentialAgent


def _build_analyst_pool():
    from google.adk.agents import ParallelAgent
    from agents.analysts.technical.agent import _build_technical_analyst
    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.sentiment.agent import _build_sentiment_analyst
    from agents.analysts.smart_money.agent import _build_smart_money_analyst
    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            _build_technical_analyst(),
            _build_fundamental_analyst(),
            _build_sentiment_analyst(),
            _build_smart_money_analyst(),
        ],
    )


def _build_memory_writer():
    from agents.memory.writer import MemoryWriter
    return MemoryWriter()


def build_pipeline(broker, db_session=None) -> SequentialAgent:
    """Compose the full hourly tick pipeline."""
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.attribution.writer import build_attribution_writer
    from agents.strategist.council import build_strategist_council
    from agents.strategist.telemetry_writer import build_council_telemetry_writer
    from agents.strategist.pack_writer import build_position_pack_writer
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(),
            build_attribution_writer(db_session),
            build_strategist_council(broker),               # Spec 1 council, broker-aware (this spec)
            build_council_telemetry_writer(db_session),     # NEW (this spec)
            build_position_pack_writer(db_session),         # NEW (this spec)
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )
```

The old `_build_strategist()` helper is deleted (its `Strategist` LlmAgent is replaced by the council).

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/test_pipeline_wiring_exits.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run the existing pipeline composition integration test**

Run: `.venv/Scripts/python -m pytest tests/integration/test_pipeline_composition.py -v`
Expected: PASS. If the test asserts the pipeline has exactly 7 sub_agents, edit the assertion to expect 9. If it asserts a specific named order like `["AnalystPool", "AttributionWriter", "Strategist", "RiskGate", "Executor", "MemoryWriter", "Snapshotter"]`, replace `"Strategist"` with `"StrategistCouncil"` and insert `"CouncilTelemetryWriter"`, `"PositionPackWriter"` between the council and `"RiskGate"`. No behavioral changes to the test logic.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_pipeline_wiring_exits.py src/orchestrator/pipeline.py
git commit -m "feat(orchestrator): wire telemetry + pack writers into hourly tick pipeline"
```

---

## Phase 14: Tier 2 smoke — full council with packs against real LLMs

**Files:**
- Test: `tests/integration/test_council_with_packs_smoke.py`

This is gated by `STOCKBOT_RUN_LLM_TESTS=1` to mirror existing Tier 2 conventions. Run on demand, not in CI.

- [ ] **Step 1: Write the smoke test**

Create `tests/integration/test_council_with_packs_smoke.py`:
```python
"""Tier 2 — full strategist_council with PositionPack input.

Gated by STOCKBOT_RUN_LLM_TESTS=1. Hits real LLMs and yfinance.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("STOCKBOT_RUN_LLM_TESTS"):
    pytest.skip("Tier 2 LLM-gated test", allow_module_level=True)


@pytest.mark.asyncio
async def test_council_runs_with_held_position_pack():
    """Council deliberates with one held position; output validates."""
    from broker.fake import FakeBroker
    from agents.strategist.council import build_strategist_council
    from agents.strategist.schema import PositionThesis
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    fake_broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 206.0, "MSFT": 410.0})
    # Seed an open AAPL position via the fake broker's submit interface.
    await fake_broker.submit_market("AAPL", "BUY", quantity=4)

    council = build_strategist_council(fake_broker)

    state = {
        "tick_id": "tick_smoke",
        "tickers": ["AAPL", "MSFT"],
        "portfolio": {"cash": 9170.0, "total_value": 10_000.0},
        "positions": {
            "AAPL": PositionThesis(
                ticker="AAPL",
                opened_at=datetime.now(tz=timezone.utc) - timedelta(hours=24),
                opened_price=200.0,
                opened_tag="seed",
                rationale="seeded for smoke",
                horizon="swing",
                target_price=215.0,
                stop_price=190.0,
                last_reviewed_at=datetime.now(tz=timezone.utc),
                running_max_price=210.0,
                running_min_price=198.0,
                spy_price_at_open=505.0,
                weight_at_open=0.08,
                opened_tick_id="tick_seed",
            ).model_dump(),
        },
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "bull market",
        "technical_signals": [{"ticker": "AAPL", "direction": "bullish", "confidence": 0.6, "evidence": {}}],
        "fundamental_signals": [{"ticker": "AAPL", "direction": "neutral", "confidence": 0.4, "evidence": {}}],
        "sentiment_signals": [{"ticker": "AAPL", "direction": "bullish", "confidence": 0.5, "evidence": {}}],
        "smart_money_signals": [],
    }

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="stockbot", user_id="smoke", state=state
    )
    invocation = InvocationContext(session=session, agent=council)

    async for _ in council._run_async_impl(invocation):
        pass

    # Pack builder ran first
    assert "position_packs" in session.state
    assert "AAPL" in session.state["position_packs"]
    assert "spy_price" in session.state

    # Council aggregator ran last
    decision = session.state.get("strategist_decision")
    assert decision is not None
    telemetry = session.state.get("council_telemetry")
    assert telemetry is not None
    # AAPL must have a quorum decision recorded
    assert "AAPL" in telemetry["quorum_decisions"]
```

- [ ] **Step 2: Run on demand**

Run: `STOCKBOT_RUN_LLM_TESTS=1 .venv/Scripts/python -m pytest tests/integration/test_council_with_packs_smoke.py -v`
Expected: PASS (skipped without the env var).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_council_with_packs_smoke.py
git commit -m "test(integration): tier-2 smoke for council with position packs"
```

---

## Phase 15: Final verification

- [ ] **Step 1: Run the full unit test suite**

Run: `.venv/Scripts/python -m pytest tests/unit/ -v`
Expected: all PASS, no regressions in existing tests.

- [ ] **Step 2: Run the integration suite (no LLM gate)**

Run: `.venv/Scripts/python -m pytest tests/integration/ -v`
Expected: PASS. Tier 2 LLM-gated tests will skip without `STOCKBOT_RUN_LLM_TESTS=1`.

- [ ] **Step 3: Lint pass**

Run: `.venv/Scripts/python -m ruff check src/ tests/`
Expected: clean (or matches the pre-spec baseline of any pre-existing lint warnings).

- [ ] **Step 4: Append a graph_delta entry**

Edit `graphify-out/graph_delta.md` and append:

```
## YYYY-MM-DD — Exit rules + telemetry persistence (Spec 2 + 3a)

Combined Spec 2 (exit floor/ceiling rules + partial trimming) and Spec 3a (telemetry persistence) ship together. Council remains the sole decision-maker; rules are surfaced as evidence via per-tick PositionPack snapshots; trim/add are first-class lifecycle decisions.

- New nodes:
  - PositionPack (Pydantic model in src/agents/strategist/position_pack.py)
  - PositionPackBuilder (BaseAgent, broker-aware, first sub_agent of strategist_council)
  - CouncilTelemetryWriter (BaseAgent, writes CouncilStanceRow + StrategistDecisionRow)
  - PositionPackWriter (BaseAgent, writes PositionPackRow with council_action + rule_overridden)
  - CouncilStanceRow, StrategistDecisionRow, PositionPackRow ORM tables

- New edges:
  - StrategistCouncil → PositionPackBuilder (sub_agent)
  - HourlyTick → CouncilTelemetryWriter (sub_agent, after StrategistCouncil)
  - HourlyTick → PositionPackWriter (sub_agent, after CouncilTelemetryWriter)
  - TradeLogRow.opening_tick_id → StrategistDecisionRow.tick_id (FK join)

- Changed:
  - PositionThesis: +5 running fields (running_max_price, running_min_price, spy_price_at_open, weight_at_open, opened_tick_id)
  - MemberStance: +trim_reason (optional)
  - StrategistDecision: +trim_reasons (dict)
  - CouncilAggregator: MIN_HELD_WEIGHT clamp on trims; trim_reasons populated; build_thesis_from_proposers seeds new fields
  - Council prompt: gains {position_packs} block + trim_reason instruction
  - HourlyTick pipeline: 7 → 9 stages
  - executor: save_trade_log_entry now populates opening_tick_id + closing_tick_id
```

Replace `YYYY-MM-DD` with the actual current date.

- [ ] **Step 5: Final commit**

```bash
git add graphify-out/graph_delta.md
git commit -m "docs(graph): record exit-rules-and-telemetry shipped"
```

---

## Out-of-scope reminders (cross-reference `docs/superpowers/backlog.md`)

The following are deliberately not in this plan. If you find yourself adding them, stop and confirm with the user:

- **Trailing stops / target ratchets** — `target_price` and `stop_price` stay sticky once opened (Backlog S4).
- **Sub-tick exit evaluation** — hourly tick boundary only (Backlog S3).
- **`risk_clamps_applied` persistence** — useful but explicitly parked (Backlog S8).
- **Round-robin debate** — parallel-vote stays from Spec 1 (Backlog S2).
- **Self-improvement learning loop** — telemetry shipping here is the *substrate*; the loop itself is Backlog S1.
- **Per-evidence-key analyst weighting** — `ANALYST_WEIGHTS` stays per-family (Backlog S5).

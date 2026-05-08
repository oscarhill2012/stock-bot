# Strategist Council Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-LlmAgent strategist with a 3-persona council (value/momentum/contrarian) running in parallel, plus a deterministic CouncilAggregator that reconciles their stances into the existing `StrategistDecision` schema. Lightly enrich `AnalystSignal` with a structured `evidence` blob, migrate `SmartMoneySignal` under the same base, and add an `ANALYST_WEIGHTS` global config knob.

**Architecture:** A new `strategist_council = SequentialAgent(persona_pool, council_aggregator)` replaces the old `strategist_agent` at pipeline position 2. The persona pool is a `ParallelAgent` of three Gemini-Pro LlmAgents that emit `list[MemberStance]`. The aggregator is a pure-Python `BaseAgent` that reconciles stances via asymmetric quorum (2-of-3 to open, any-to-close) and confidence-weighted sizing, then writes `StrategistDecision` to state. `CouncilTelemetry` is written to session state but not persisted in this spec (deferred to Spec 3).

**Tech Stack:** Python 3.11+, Pydantic v2, Google ADK (`google-adk`: `LlmAgent`, `BaseAgent`, `SequentialAgent`, `ParallelAgent`), pytest. Source layout: `src/agents/strategist/`, tests in `tests/unit/strategist/`.

**Spec:** `docs/superpowers/specs/strategist-council-design.md` — read it before starting.

**Shell convention (Windows):** All commands run from the project root (`C:\Users\oscar\OneDrive - Nexus365\Documents\StockBot`). Use `.venv/Scripts/python -m pytest ...` and `.venv/Scripts/python -m ruff ...`. Do **not** prefix commands with `cd`.

**Graphify:** After substantial structural changes (new files, new edges between modules), append a dated entry to `graphify-out/graph_delta.md` per `.claude/CLAUDE.md`. The final cleanup phase has a step for this.

---

## Phase 1 — MemberStance + CouncilTelemetry schemas

**Files:**
- Create: `src/agents/strategist/member_schema.py`
- Create: `tests/unit/strategist/__init__.py` (empty)
- Create: `tests/unit/strategist/test_member_schema.py`

### Task 1.1: Scaffold the test directory

- [ ] **Step 1: Create the test package marker**

Create `tests/unit/strategist/__init__.py`:
```python
```
(Empty file — Python package marker.)

- [ ] **Step 2: Commit**

```bash
git add tests/unit/strategist/__init__.py
git commit -m "test: scaffold tests/unit/strategist package"
```

### Task 1.2: MemberStance — required fields & basic validation

- [ ] **Step 1: Write the failing test**

Create `tests/unit/strategist/test_member_schema.py`:
```python
"""MemberStance and CouncilTelemetry schema tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.member_schema import MemberStance


def _base_stance(**overrides):
    defaults = dict(
        ticker="AAPL",
        persona="value",
        preferred_weight=0.10,
        conviction=0.7,
        rationale="cheap on FCF basis",
    )
    defaults.update(overrides)
    return defaults


def test_minimal_stance_validates():
    s = MemberStance(**_base_stance())
    assert s.ticker == "AAPL"
    assert s.persona == "value"
    assert s.preferred_weight == 0.10
    assert s.conviction == 0.7
    assert s.horizon is None
    assert s.target_price is None
    assert s.stop_price is None
    assert s.catalyst is None
    assert s.close_reason is None


def test_persona_must_be_one_of_three():
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(persona="growth"))


def test_preferred_weight_in_zero_one():
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(preferred_weight=1.1))
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(preferred_weight=-0.01))


def test_conviction_in_zero_one():
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(conviction=1.1))


def test_rationale_max_length_140():
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(rationale="x" * 141))


def test_catalyst_max_length_80():
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(catalyst="x" * 81))


def test_close_reason_max_length_120():
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(close_reason="x" * 121))


def test_horizon_literal():
    s = MemberStance(**_base_stance(horizon="swing"))
    assert s.horizon == "swing"
    with pytest.raises(ValidationError):
        MemberStance(**_base_stance(horizon="weekly"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_member_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.strategist.member_schema'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agents/strategist/member_schema.py`:
```python
"""Council member stance + per-tick telemetry schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MemberStance(BaseModel):
    """One council member's per-ticker opinion.

    The `persona` field is set deterministically by the aggregator from the
    state key the stance was emitted into (value_stances / momentum_stances /
    contrarian_stances), not by the LLM itself.
    """
    ticker: str
    persona: Literal["value", "momentum", "contrarian"]
    preferred_weight: float = Field(ge=0.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=140)

    # Lifecycle hints — populated only when proposing to open a flat position.
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=80)

    # Lifecycle hint — populated only when proposing to close a held position.
    close_reason: str | None = Field(default=None, max_length=120)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_member_schema.py -v`
Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/member_schema.py tests/unit/strategist/test_member_schema.py
git commit -m "feat(strategist): add MemberStance schema for council members"
```

### Task 1.3: CouncilTelemetry schema

- [ ] **Step 1: Add failing test**

Append to `tests/unit/strategist/test_member_schema.py`:
```python
from agents.strategist.member_schema import CouncilTelemetry


def test_council_telemetry_round_trip():
    s = MemberStance(**_base_stance())
    t = CouncilTelemetry(
        stances=[s],
        quorum_decisions={"AAPL": "open"},
        disagreement_score={"AAPL": 0.0},
    )
    dumped = t.model_dump()
    rebuilt = CouncilTelemetry.model_validate(dumped)
    assert rebuilt.quorum_decisions == {"AAPL": "open"}
    assert rebuilt.degraded_member is None


def test_council_telemetry_with_degraded_member():
    t = CouncilTelemetry(
        stances=[],
        quorum_decisions={},
        disagreement_score={},
        degraded_member="momentum",
    )
    assert t.degraded_member == "momentum"
```

- [ ] **Step 2: Run, expect ImportError on CouncilTelemetry**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_member_schema.py -v`
Expected: 2 new tests fail with `ImportError: cannot import name 'CouncilTelemetry'`.

- [ ] **Step 3: Add CouncilTelemetry**

Append to `src/agents/strategist/member_schema.py`:
```python
class CouncilTelemetry(BaseModel):
    """Frozen per-tick record of all member stances + aggregation outcome.

    Persistence is deferred to Spec 3 (signal-pattern memory) — for now this
    lives in session state only and is available for in-tick logging.
    """
    stances: list[MemberStance]
    quorum_decisions: dict[str, str]            # ticker -> "open"|"close"|"trim"|"add"|"hold"
    disagreement_score: dict[str, float]        # ticker -> variance(preferred_weights), in [0, 0.25]
    degraded_member: str | None = None          # set when a persona's stance was unavailable
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_member_schema.py -v`
Expected: 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/member_schema.py tests/unit/strategist/test_member_schema.py
git commit -m "feat(strategist): add CouncilTelemetry schema"
```

---

## Phase 2 — AnalystSignal evidence blob + SmartMoneySignal migration

**Files:**
- Modify: `src/agents/analysts/_common.py` (add `evidence` field)
- Modify: `src/agents/analysts/smart_money/schema.py` (subclass AnalystSignal, migrate fields into evidence)
- Modify: `tests/unit/test_analyst_schemas.py` (existing — extend coverage)
- Create: `tests/unit/strategist/test_analyst_evidence.py`

### Task 2.1: Add `evidence` to AnalystSignal (backwards compatible)

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_analyst_evidence.py`:
```python
"""AnalystSignal evidence blob + SmartMoneySignal migration tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.analysts._common import AnalystSignal


def test_analyst_signal_evidence_defaults_empty():
    s = AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.8)
    assert s.evidence == {}


def test_analyst_signal_evidence_accepts_floats_and_strings():
    s = AnalystSignal(
        ticker="AAPL",
        direction="bullish",
        confidence=0.8,
        evidence={"rsi_14": 62.5, "regime": "uptrend"},
    )
    assert s.evidence["rsi_14"] == 62.5
    assert s.evidence["regime"] == "uptrend"


def test_analyst_signal_evidence_rejects_other_types():
    with pytest.raises(ValidationError):
        AnalystSignal(
            ticker="AAPL",
            direction="bullish",
            confidence=0.8,
            evidence={"bad": [1, 2, 3]},   # list not allowed
        )
```

- [ ] **Step 2: Run, expect failure on `evidence`**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_analyst_evidence.py -v`
Expected: 3 tests fail (no such field `evidence`).

- [ ] **Step 3: Add `evidence` field**

Edit `src/agents/analysts/_common.py` — replace the `AnalystSignal` class with:
```python
class AnalystSignal(BaseModel):
    ticker: str
    direction: str  # "bullish" | "bearish" | "neutral"
    confidence: float = Field(ge=0.0, le=1.0)
    key_factors: list[str] = Field(default_factory=list, max_length=3)
    evidence: dict[str, float | str] = Field(default_factory=dict)
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_analyst_evidence.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run the full analyst-schema test file to confirm no regressions**

Run: `.venv/Scripts/python -m pytest tests/unit/test_analyst_schemas.py -v`
Expected: all existing tests still PASS (defaults preserve compatibility).

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/_common.py tests/unit/strategist/test_analyst_evidence.py
git commit -m "feat(analysts): add evidence blob to AnalystSignal (default empty)"
```

### Task 2.2: Migrate SmartMoneySignal under AnalystSignal

The current `SmartMoneySignal` lives outside the `AnalystSignal` hierarchy. Move it under the base, fold `insiders`/`politicians`/`total_dollar_value` into `evidence`, derive a numeric `confidence` from `conviction`.

- [ ] **Step 1: Write failing test**

Append to `tests/unit/strategist/test_analyst_evidence.py`:
```python
from agents.analysts.smart_money.schema import SmartMoneySignal


def test_smart_money_now_subclasses_analyst_signal():
    assert issubclass(SmartMoneySignal, AnalystSignal)


def test_smart_money_high_conviction_maps_to_high_confidence():
    s = SmartMoneySignal(
        ticker="AAPL",
        direction="bullish",
        conviction="high",
        insiders=["Cook"],
        politicians=[],
        total_dollar_value=1_000_000.0,
    )
    assert s.confidence >= 0.7   # high conviction -> >=0.7
    assert s.evidence["n_insiders"] == 1.0
    assert s.evidence["n_politicians"] == 0.0
    assert s.evidence["total_dollar_value"] == 1_000_000.0
    assert s.evidence["conviction_label"] == "high"


def test_smart_money_low_conviction_maps_to_lower_confidence():
    s = SmartMoneySignal(
        ticker="AAPL",
        direction="bullish",
        conviction="low",
        insiders=[],
        politicians=["Pelosi"],
        total_dollar_value=50_000.0,
    )
    assert s.confidence < 0.7   # low conviction -> <0.7
    assert s.evidence["conviction_label"] == "low"


def test_smart_money_still_rejects_neutral():
    with pytest.raises(ValidationError):
        SmartMoneySignal(
            ticker="AAPL",
            direction="neutral",   # only bullish/bearish allowed — sparse-by-design preserved
            conviction="high",
        )
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_analyst_evidence.py -v`
Expected: 4 new tests fail.

- [ ] **Step 3: Rewrite SmartMoneySignal**

Replace `src/agents/analysts/smart_money/schema.py` with:
```python
"""Smart-money analyst output schema (subclasses AnalystSignal)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents.analysts._common import AnalystSignal


_HIGH_CONVICTION_FLOOR = 0.7
_LOW_CONVICTION_DEFAULT = 0.5


class SmartMoneySignal(AnalystSignal):
    """Signal derived from insider filings, congressional trades, and SC 13D/G holders.

    Sparse by design: only bullish/bearish are emitted. The smart-money fetch
    callback skips the LLM entirely when no material activity is detected, so
    a "neutral" smart-money signal cannot exist.
    """

    direction: Literal["bullish", "bearish"]   # narrows base AnalystSignal.direction

    # Domain-specific raw inputs — kept on the model for ergonomics, but also
    # mirrored into `evidence` so personas can read them through a single lens.
    conviction: Literal["low", "high"] = "low"
    insiders: list[str] = Field(default_factory=list)
    politicians: list[str] = Field(default_factory=list)
    total_dollar_value: float = 0.0

    # AnalystSignal.confidence has no default; provide one here so callers don't
    # have to pass it (we derive a meaningful value from `conviction` below).
    confidence: float = Field(default=_LOW_CONVICTION_DEFAULT, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _populate_derived(self) -> "SmartMoneySignal":
        # Promote high-conviction signals above the default floor.
        if self.conviction == "high" and self.confidence < _HIGH_CONVICTION_FLOOR:
            self.confidence = _HIGH_CONVICTION_FLOOR
        # Mirror raw inputs into evidence for persona-side reading.
        self.evidence = {
            **self.evidence,
            "n_insiders": float(len(self.insiders)),
            "n_politicians": float(len(self.politicians)),
            "total_dollar_value": float(self.total_dollar_value),
            "conviction_label": self.conviction,
        }
        return self
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_analyst_evidence.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Run dependent tests to confirm no regressions**

Run: `.venv/Scripts/python -m pytest tests/unit/test_analyst_schemas.py tests/unit/test_smart_money_gate.py -v`
Expected: all PASS. If any fixture in `test_smart_money_gate.py` constructs `SmartMoneySignal` and breaks, fix the fixture (don't loosen the schema).

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/smart_money/schema.py tests/unit/strategist/test_analyst_evidence.py
git commit -m "feat(analysts): migrate SmartMoneySignal under AnalystSignal with evidence"
```

### Task 2.3: Update technical/fundamental/sentiment analyst prompts to populate evidence

For each of the four analyst prompt files, add a section to the existing instruction text instructing the LLM to populate `evidence` with the documented per-analyst keys. Schema already accepts the field (default empty), so this is a behaviour change in prompts only — no schema work.

The four prompt files:
- `src/agents/analysts/technical/prompts.py`
- `src/agents/analysts/fundamental/prompts.py`
- `src/agents/analysts/sentiment/prompts.py`
- `src/agents/analysts/smart_money/prompts.py`

For each file:

- [ ] **Step 1: Read the current `<X>_INSTRUCTION` constant** (no test for this — prompt content is verified by the integration smoke test, not by structural unit tests).

- [ ] **Step 2: Append an `## Evidence` section to each instruction**

For `technical/prompts.py`, append before the closing triple-quote of `TECHNICAL_INSTRUCTION`:
```
## Evidence
For every signal you emit, populate `evidence` with these numeric keys when available:
- rsi_14: 14-period RSI, in [0, 100]
- macd_hist: MACD histogram value (signed)
- volume_zscore: today's volume z-score vs 20-day mean
- breakout_distance_pct: % distance to nearest 20-day high/low (signed; positive = above high)
- atr_pct: 14-period ATR as % of close
Omit a key entirely if you cannot compute it. Do not fabricate values.
```

For `fundamental/prompts.py`, append:
```
## Evidence
For every signal you emit, populate `evidence` with these numeric keys when available:
- pe: trailing price/earnings ratio
- forward_pe: forward price/earnings ratio
- debt_to_equity: total debt / shareholders' equity
- fcf_yield: trailing free cash flow / market cap
- revenue_growth_yoy: YoY revenue growth, decimal (e.g. 0.12 = 12%)
Omit a key entirely if you cannot compute it. Do not fabricate values.
```

For `sentiment/prompts.py`, append:
```
## Evidence
For every signal you emit, populate `evidence` with these numeric keys when available:
- avg_score: mean sentiment polarity in [-1, 1]
- score_extremity: |avg_score| in [0, 1]; how one-sided the news cycle is
- n_headlines: count of headlines considered (float)
- social_score_delta: same value as social_score_delta on the schema
Omit a key entirely if you cannot compute it. Do not fabricate values.
```

For `smart_money/prompts.py`, append:
```
## Evidence
The smart-money signal automatically mirrors `n_insiders`, `n_politicians`, `total_dollar_value`, and `conviction_label` into `evidence` after construction — you do NOT need to populate `evidence` manually. Just emit the structured fields (insiders, politicians, total_dollar_value, conviction) and the schema's model_validator handles the rest.
```

- [ ] **Step 3: Verify nothing breaks structurally**

Run: `.venv/Scripts/python -m pytest tests/unit/test_analyst_fetchers.py tests/unit/test_analyst_schemas.py tests/integration/test_analyst_pool.py -v`
Expected: all PASS (prompt text changes don't affect Tier 1 tests).

- [ ] **Step 4: Commit**

```bash
git add src/agents/analysts/technical/prompts.py src/agents/analysts/fundamental/prompts.py src/agents/analysts/sentiment/prompts.py src/agents/analysts/smart_money/prompts.py
git commit -m "feat(analysts): instruct LLMs to populate evidence keys"
```

---

## Phase 3 — ANALYST_WEIGHTS config + persona prompt rendering

**Files:**
- Create: `src/agents/strategist/config.py`
- Create: `tests/unit/strategist/test_config.py`
- Replace: `src/agents/strategist/prompts.py` (delete legacy `STRATEGIST_INSTRUCTION`, add `COUNCIL_PROMPT_TEMPLATE` + `render_persona_prompt`)
- Create: `tests/unit/strategist/test_prompts.py`

### Task 3.1: ANALYST_WEIGHTS config

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_config.py`:
```python
"""ANALYST_WEIGHTS config — Tier 1, no LLM."""
from __future__ import annotations

from agents.strategist.config import ANALYST_WEIGHTS


def test_keys_match_four_analysts():
    assert set(ANALYST_WEIGHTS.keys()) == {
        "technical",
        "fundamental",
        "sentiment",
        "smart_money",
    }


def test_values_are_positive_floats():
    for k, v in ANALYST_WEIGHTS.items():
        assert isinstance(v, float)
        assert v > 0.0, f"{k} weight must be > 0"


def test_default_weights_match_spec():
    assert ANALYST_WEIGHTS["technical"]   == 1.0
    assert ANALYST_WEIGHTS["fundamental"] == 1.0
    assert ANALYST_WEIGHTS["sentiment"]   == 0.7
    assert ANALYST_WEIGHTS["smart_money"] == 1.5
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_config.py -v`
Expected: 3 tests fail with import error.

- [ ] **Step 3: Create the config module**

Create `src/agents/strategist/config.py`:
```python
"""Strategist tier configuration knobs.

ANALYST_WEIGHTS is the single global tuning knob for analyst-tier reliability.
Personas are told to weight signals from each analyst family by these values
when forming their views. Per-evidence-key importance is intentionally not
declared here — Spec 3 (signal-pattern memory) will learn it from realised PnL.
"""
from __future__ import annotations

ANALYST_WEIGHTS: dict[str, float] = {
    "technical":   1.0,
    "fundamental": 1.0,
    "sentiment":   0.7,
    "smart_money": 1.5,
}
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_config.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/config.py tests/unit/strategist/test_config.py
git commit -m "feat(strategist): add ANALYST_WEIGHTS config knob"
```

### Task 3.2: render_persona_prompt and COUNCIL_PROMPT_TEMPLATE

We replace the legacy `STRATEGIST_INSTRUCTION` with a shared `COUNCIL_PROMPT_TEMPLATE` plus a `render_persona_prompt(lens)` helper. The lens slot is the only thing that differs between personas; everything else (analyst weights, current state, signals, output spec) is identical.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/strategist/test_prompts.py`:
```python
"""Council prompt rendering tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest

from agents.strategist.prompts import (
    COUNCIL_PROMPT_TEMPLATE,
    render_persona_prompt,
)


def test_template_has_persona_lens_slot():
    assert "{persona_lens}" in COUNCIL_PROMPT_TEMPLATE


def test_template_has_persona_name_slot():
    assert "{persona_name}" in COUNCIL_PROMPT_TEMPLATE


def test_template_has_analyst_weights_table_slot():
    assert "{analyst_weights_table}" in COUNCIL_PROMPT_TEMPLATE


def test_template_has_state_slots():
    for s in ("portfolio", "positions", "memory_buffer", "day_digest", "thesis", "tickers"):
        assert "{" + s + "}" in COUNCIL_PROMPT_TEMPLATE, f"missing {{{s}}}"


def test_template_has_signal_slots():
    for s in ("technical_signals", "fundamental_signals", "sentiment_signals", "smart_money_signals"):
        assert "{" + s + "}" in COUNCIL_PROMPT_TEMPLATE


def test_render_returns_str_with_lens_filled():
    rendered = render_persona_prompt("value", "You are a value investor.")
    assert "{persona_lens}" not in rendered
    assert "You are a value investor." in rendered
    assert "{persona_name}" not in rendered
    assert "value" in rendered.lower()


def test_render_renders_analyst_weights_table():
    rendered = render_persona_prompt("momentum", "Momentum lens text.")
    # Table should contain each analyst name and its weight as a string.
    assert "technical" in rendered
    assert "smart_money" in rendered
    assert "1.5" in rendered                   # smart_money weight
    assert "0.7" in rendered                   # sentiment weight


def test_render_leaves_runtime_slots_unfilled():
    """render_persona_prompt only fills lens + name + analyst_weights_table.
    Runtime state slots (portfolio, signals, etc) are filled by ADK later."""
    rendered = render_persona_prompt("contrarian", "Contrarian lens.")
    assert "{portfolio}" in rendered
    assert "{technical_signals}" in rendered


def test_render_rejects_unknown_persona():
    with pytest.raises(ValueError):
        render_persona_prompt("growth", "lens text")
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_prompts.py -v`
Expected: all fail (legacy `prompts.py` exports `STRATEGIST_INSTRUCTION`, not the new symbols).

- [ ] **Step 3: Replace `prompts.py`**

Replace the contents of `src/agents/strategist/prompts.py` entirely with:
```python
"""Council prompt template + per-persona renderer.

The legacy single-strategist STRATEGIST_INSTRUCTION has been removed; the
council uses one shared template per persona, parameterised by lens.
"""
from __future__ import annotations

from agents.strategist.config import ANALYST_WEIGHTS

PERSONAS: tuple[str, ...] = ("value", "momentum", "contrarian")


COUNCIL_PROMPT_TEMPLATE = """\
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

## Your Job
Emit a MemberStance for EVERY watchlist ticker: {tickers}.
- preferred_weight in [0, 1]: your ideal portfolio weight for this ticker next tick
- conviction in [0, 1]: how strongly you hold this view
- rationale: <=140 chars
- If proposing to open (current 0 -> preferred >0): include horizon, target_price, stop_price, optional catalyst.
- If proposing to close (current >0 -> preferred 0): include close_reason.

Output: list[MemberStance], exhaustive over the watchlist.
"""


def _render_weights_table(weights: dict[str, float]) -> str:
    lines = [f"- {name}: weight {w}" for name, w in weights.items()]
    return "\n".join(lines)


def render_persona_prompt(persona_name: str, persona_lens: str) -> str:
    """Render the council prompt for one persona.

    Only fills static slots (persona_name, persona_lens, analyst_weights_table).
    Runtime state slots (portfolio, signals, etc.) are left as f-string-style
    placeholders; ADK substitutes those at invocation time.
    """
    if persona_name not in PERSONAS:
        raise ValueError(f"unknown persona: {persona_name!r}; expected one of {PERSONAS}")
    table = _render_weights_table(ANALYST_WEIGHTS)
    # Use str.replace for static-only filling — str.format would error on the
    # runtime slots ADK will fill later.
    return (
        COUNCIL_PROMPT_TEMPLATE
        .replace("{persona_name}", persona_name)
        .replace("{persona_lens}", persona_lens)
        .replace("{analyst_weights_table}", table)
    )
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_prompts.py -v`
Expected: 9 tests PASS.

- [ ] **Step 5: Confirm legacy prompt-template test breaks (we'll delete it later)**

Run: `.venv/Scripts/python -m pytest tests/unit/test_strategist_prompt_template.py -v`
Expected: FAILS with ImportError on `STRATEGIST_INSTRUCTION` — that's fine; we'll delete this test file in Phase 7. **Do not fix it now.**

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/prompts.py tests/unit/strategist/test_prompts.py
git commit -m "feat(strategist): replace strategist instruction with council prompt template"
```

---

## Phase 4 — Persona LlmAgents and persona_pool

**Files:**
- Create: `src/agents/strategist/personas.py`
- Create: `tests/unit/strategist/test_personas.py` (Tier 1 structural test only — Tier 2 LLM smoke is in Phase 6)

### Task 4.1: VALUE_LENS, MOMENTUM_LENS, CONTRARIAN_LENS constants

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_personas.py`:
```python
"""Persona lenses + LlmAgent factory tests — Tier 1 structural, no LLM calls."""
from __future__ import annotations

import pytest

from agents.strategist.personas import (
    VALUE_LENS,
    MOMENTUM_LENS,
    CONTRARIAN_LENS,
)


@pytest.mark.parametrize("lens", [VALUE_LENS, MOMENTUM_LENS, CONTRARIAN_LENS])
def test_lens_is_non_empty_str(lens):
    assert isinstance(lens, str)
    assert len(lens.strip()) > 50, "lens text should be substantial, not a placeholder"


def test_lenses_are_distinct():
    assert VALUE_LENS != MOMENTUM_LENS
    assert MOMENTUM_LENS != CONTRARIAN_LENS
    assert VALUE_LENS != CONTRARIAN_LENS


def test_value_lens_mentions_valuation_concepts():
    txt = VALUE_LENS.lower()
    assert any(w in txt for w in ("intrinsic value", "fcf", "free cash flow", "graham", "buffett"))


def test_momentum_lens_mentions_trend_concepts():
    txt = MOMENTUM_LENS.lower()
    assert any(w in txt for w in ("trend", "momentum", "breakout", "macd", "volume"))


def test_contrarian_lens_mentions_extremes():
    txt = CONTRARIAN_LENS.lower()
    assert any(w in txt for w in ("extreme", "fade", "consensus", "mean-revert", "panic"))
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_personas.py -v`
Expected: tests fail with import error.

- [ ] **Step 3: Create personas.py with lenses only (no agents yet)**

Create `src/agents/strategist/personas.py`:
```python
"""The three council member personas — lenses + LlmAgent factories.

Each persona shares the same prompt template (COUNCIL_PROMPT_TEMPLATE) with
its `persona_lens` slot replaced by one of the three constants below. The
personas run in parallel and each emits a `list[MemberStance]` exhaustive
over the watchlist.
"""
from __future__ import annotations


VALUE_LENS = """\
You are a value investor in the Buffett/Graham tradition. You buy quality
businesses trading below intrinsic value and ignore short-term price noise.
You favour: low PE, healthy free-cash-flow yield, low debt-to-equity, durable
revenue growth, and management with skin in the game.
You are skeptical of: hype-driven rallies, momentum without earnings support,
sentiment swings, technical breakouts.
Your default is to size into undervalued names and sit on cash when nothing
qualifies. You prefer fewer, more concentrated bets you understand deeply.
You hold positions through volatility unless the underlying thesis breaks.
"""


MOMENTUM_LENS = """\
You are a momentum trader. You ride trends - buy strength, sell weakness -
and trust the tape over the story. You favour: positive macd histogram,
volume confirmation on breakouts, RSI in 50-70 range (trending but not extreme),
strong relative-strength vs SPY.
You are skeptical of: cheap-looking value traps, contrarian "it'll come back"
arguments, fundamental theses that ignore current price action.
You exit fast when momentum breaks. You don't argue with the market.
You'll size up when multiple technical signals align; you'll go to zero
when the trend rolls over, regardless of fundamentals.
"""


CONTRARIAN_LENS = """\
You are a contrarian. You fade extremes - buy panic, sell euphoria - and look
for setups where the consensus is wrong. You favour: high score_extremity on
the wrong side, RSI < 30 or > 70, smart-money buying when retail is selling
(or vice versa), insider activity diverging from price action.
You are skeptical of: trend-chasing, "this time is different" narratives,
crowded longs, anything where the news cycle and price are pointing the same way.
You size up when sentiment is one-sided and smart money is on the other side.
You'll cut a position if the contrarian setup resolves (extremes mean-revert)
even if you haven't fully realised the upside.
"""
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_personas.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/personas.py tests/unit/strategist/test_personas.py
git commit -m "feat(strategist): add value/momentum/contrarian persona lenses"
```

### Task 4.2: Persona LlmAgent factories + persona_pool

- [ ] **Step 1: Write failing test**

Append to `tests/unit/strategist/test_personas.py`:
```python
from google.adk.agents import LlmAgent, ParallelAgent

from agents.strategist.personas import (
    _build_value_strategist,
    _build_momentum_strategist,
    _build_contrarian_strategist,
    _build_persona_pool,
)
from agents.strategist.member_schema import MemberStance


def test_value_strategist_factory_returns_llm_agent():
    a = _build_value_strategist()
    assert isinstance(a, LlmAgent)
    assert a.name == "ValueStrategist"
    assert a.output_key == "value_stances"
    assert a.model.startswith("gemini")


def test_momentum_strategist_factory():
    a = _build_momentum_strategist()
    assert a.name == "MomentumStrategist"
    assert a.output_key == "momentum_stances"


def test_contrarian_strategist_factory():
    a = _build_contrarian_strategist()
    assert a.name == "ContrarianStrategist"
    assert a.output_key == "contrarian_stances"


def test_persona_pool_has_three_members():
    pool = _build_persona_pool()
    assert isinstance(pool, ParallelAgent)
    assert pool.name == "PersonaPool"
    assert len(pool.sub_agents) == 3
    names = {a.name for a in pool.sub_agents}
    assert names == {"ValueStrategist", "MomentumStrategist", "ContrarianStrategist"}


def test_persona_outputs_are_list_of_member_stance():
    for f in (_build_value_strategist, _build_momentum_strategist, _build_contrarian_strategist):
        a = f()
        # ADK exposes the schema either via a property or attribute; both forms accepted.
        schema = getattr(a, "output_schema", None) or getattr(a, "_output_schema", None)
        assert schema == list[MemberStance]
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_personas.py -v`
Expected: 5 new tests fail with import error.

- [ ] **Step 3: Append the factories + pool to personas.py**

Append to `src/agents/strategist/personas.py`:
```python
from google.adk.agents import LlmAgent, ParallelAgent

from agents.analysts._common import make_exhaustive_validator
from agents.strategist.member_schema import MemberStance
from agents.strategist.prompts import render_persona_prompt


_PERSONA_MODEL = "gemini-2.0-pro-001"


def _build_value_strategist() -> LlmAgent:
    return LlmAgent(
        name="ValueStrategist",
        model=_PERSONA_MODEL,
        instruction=render_persona_prompt("value", VALUE_LENS),
        output_schema=list[MemberStance],
        output_key="value_stances",
        after_agent_callback=make_exhaustive_validator("value_stances"),
    )


def _build_momentum_strategist() -> LlmAgent:
    return LlmAgent(
        name="MomentumStrategist",
        model=_PERSONA_MODEL,
        instruction=render_persona_prompt("momentum", MOMENTUM_LENS),
        output_schema=list[MemberStance],
        output_key="momentum_stances",
        after_agent_callback=make_exhaustive_validator("momentum_stances"),
    )


def _build_contrarian_strategist() -> LlmAgent:
    return LlmAgent(
        name="ContrarianStrategist",
        model=_PERSONA_MODEL,
        instruction=render_persona_prompt("contrarian", CONTRARIAN_LENS),
        output_schema=list[MemberStance],
        output_key="contrarian_stances",
        after_agent_callback=make_exhaustive_validator("contrarian_stances"),
    )


def _build_persona_pool() -> ParallelAgent:
    """Build a fresh PersonaPool each time to avoid ADK's single-parent constraint."""
    return ParallelAgent(
        name="PersonaPool",
        sub_agents=[
            _build_value_strategist(),
            _build_momentum_strategist(),
            _build_contrarian_strategist(),
        ],
    )
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_personas.py -v`
Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/personas.py tests/unit/strategist/test_personas.py
git commit -m "feat(strategist): add persona LlmAgent factories and persona_pool"
```

---

## Phase 5 — CouncilAggregator (the meat)

The aggregator is pure Python. We build it test-first in increments: helpers (sizing, thesis builder), then per-transition logic, then validation, then degraded-mode handling, then the BaseAgent wrapper.

**Files:**
- Create: `src/agents/strategist/aggregator.py`
- Create: `tests/unit/strategist/test_aggregator_constants.py`
- Create: `tests/unit/strategist/test_aggregator_sizing.py`
- Create: `tests/unit/strategist/test_aggregator_thesis.py`
- Create: `tests/unit/strategist/test_aggregator_quorum.py`
- Create: `tests/unit/strategist/test_aggregator_validation.py`
- Create: `tests/unit/strategist/test_aggregator_degraded.py`
- Create: `tests/unit/strategist/test_council_telemetry.py`
- Create: `tests/fixtures/council/three_persona_stances_consensus.json`
- Create: `tests/fixtures/council/three_persona_stances_split.json`
- Create: `tests/fixtures/council/three_persona_stances_quorum_miss.json`
- Create: `tests/fixtures/council/three_persona_stances_close_trigger.json`

### Task 5.1: Constants and effective_open_quorum helper

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_aggregator_constants.py`:
```python
"""Aggregator constants + effective_open_quorum helper."""
from __future__ import annotations

from agents.strategist.aggregator import (
    OPEN_QUORUM,
    CLOSE_QUORUM,
    OPEN_EPSILON,
    CLOSE_EPSILON,
    SIZE_CHANGE_EPSILON,
    MAX_PER_TICKER_HINT,
    PERSONAS,
    effective_open_quorum,
)


def test_constants_match_spec():
    assert OPEN_QUORUM == 2
    assert CLOSE_QUORUM == 1
    assert OPEN_EPSILON == 0.005
    assert CLOSE_EPSILON == 0.005
    assert SIZE_CHANGE_EPSILON == 0.02
    assert MAX_PER_TICKER_HINT == 0.30
    assert PERSONAS == ("value", "momentum", "contrarian")


def test_effective_open_quorum_three_available():
    assert effective_open_quorum(3) == OPEN_QUORUM   # 2 of 3


def test_effective_open_quorum_two_available():
    assert effective_open_quorum(2) == 2             # both must agree


def test_effective_open_quorum_one_available_blocks_opens():
    # Returns a sentinel that exceeds any feasible n_proposers, blocking opens.
    assert effective_open_quorum(1) > 1


def test_effective_open_quorum_zero_blocks_opens():
    assert effective_open_quorum(0) > 0
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_constants.py -v`
Expected: all fail.

- [ ] **Step 3: Create aggregator.py skeleton with constants + helper**

Create `src/agents/strategist/aggregator.py`:
```python
"""CouncilAggregator — deterministic reconciliation of three MemberStance lists.

Pure Python, no LLM. Reads value/momentum/contrarian _stances from session state
and emits a StrategistDecision matching the existing schema. Validation failures
raise StrategistContractViolation immediately (the aggregator is deterministic,
so violations are bugs, not retry cases).
"""
from __future__ import annotations


# Quorum & sizing constants — see strategist-council-design.md "Constants".
OPEN_QUORUM: int = 2          # of 3 personas must propose >0 to open
CLOSE_QUORUM: int = 1         # any single persona can trigger a close
OPEN_EPSILON: float = 0.005   # below this, the member is "proposing 0"
CLOSE_EPSILON: float = 0.005
SIZE_CHANGE_EPSILON: float = 0.02   # delta below this counts as "hold"
MAX_PER_TICKER_HINT: float = 0.30   # defensive clamp; risk gate is the actual enforcer
PERSONAS: tuple[str, ...] = ("value", "momentum", "contrarian")

_BLOCKED = 99   # sentinel: an unreachable quorum count blocks opens entirely


def effective_open_quorum(n_available: int) -> int:
    """Quorum required to open a position given n_available living personas.

    With all 3 personas available we use the normal OPEN_QUORUM (2 of 3).
    With 2 available we require unanimity from the remaining two.
    With <=1 available we block opens entirely by returning a sentinel that
    no realistic proposer count can match. Closes still work via CLOSE_QUORUM.
    """
    if n_available >= 3:
        return OPEN_QUORUM
    if n_available == 2:
        return 2
    return _BLOCKED


class CouncilStanceUnavailable(RuntimeError):
    """All persona stances were missing or unparseable — tick cannot proceed."""
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_constants.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/aggregator.py tests/unit/strategist/test_aggregator_constants.py
git commit -m "feat(strategist): add aggregator constants and effective_open_quorum"
```

### Task 5.2: confidence_weighted_avg sizing helper

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_aggregator_sizing.py`:
```python
"""confidence_weighted_avg + clamp helpers."""
from __future__ import annotations

import math

from agents.strategist.aggregator import (
    confidence_weighted_avg,
    clamp_preferred_weights,
    MAX_PER_TICKER_HINT,
)


def test_simple_unanimous_average():
    # All same conviction -> simple mean of preferred weights.
    out = confidence_weighted_avg(prefs=[0.10, 0.12, 0.08], convs=[0.5, 0.5, 0.5])
    assert math.isclose(out, 0.10, abs_tol=1e-9)


def test_higher_conviction_pulls_average():
    # 0.10 with conviction 0.9 should dominate the others' 0.20 with conviction 0.1.
    out = confidence_weighted_avg(prefs=[0.10, 0.20, 0.20], convs=[0.9, 0.1, 0.1])
    assert out < 0.15   # closer to 0.10 than to 0.20


def test_dissenter_at_zero_dilutes():
    # Two members propose 0.20 with conviction 0.5; one proposes 0 with conviction 0.5.
    # Their dissent should pull the mean below 0.20.
    out = confidence_weighted_avg(prefs=[0.20, 0.20, 0.0], convs=[0.5, 0.5, 0.5])
    assert math.isclose(out, 0.40 / 3, abs_tol=1e-9)


def test_all_zero_conviction_falls_back_to_mean():
    out = confidence_weighted_avg(prefs=[0.10, 0.20, 0.30], convs=[0.0, 0.0, 0.0])
    assert math.isclose(out, 0.20, abs_tol=1e-9)


def test_empty_inputs_return_zero():
    assert confidence_weighted_avg(prefs=[], convs=[]) == 0.0


def test_clamp_preferred_weights_caps_at_max():
    out = clamp_preferred_weights([0.5, 0.10, 0.40])
    assert out == [MAX_PER_TICKER_HINT, 0.10, MAX_PER_TICKER_HINT]


def test_clamp_preferred_weights_floors_negatives_at_zero():
    out = clamp_preferred_weights([-0.05, 0.10])
    assert out == [0.0, 0.10]
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_sizing.py -v`
Expected: fail.

- [ ] **Step 3: Add helpers to aggregator.py**

Append to `src/agents/strategist/aggregator.py`:
```python
def clamp_preferred_weights(prefs: list[float]) -> list[float]:
    """Clamp each preferred_weight to [0, MAX_PER_TICKER_HINT]."""
    return [max(0.0, min(MAX_PER_TICKER_HINT, p)) for p in prefs]


def confidence_weighted_avg(prefs: list[float], convs: list[float]) -> float:
    """Mean of preferred_weights weighted by conviction.

    Empty inputs return 0.0. If every conviction is 0, falls back to the
    unweighted mean. A dissenter who proposes 0 is included at 0 — their
    dissent dilutes the position size; this is intentional.
    """
    if not prefs:
        return 0.0
    total_conv = sum(convs)
    if total_conv == 0.0:
        return sum(prefs) / len(prefs)
    return sum(p * c for p, c in zip(prefs, convs)) / total_conv
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_sizing.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/aggregator.py tests/unit/strategist/test_aggregator_sizing.py
git commit -m "feat(strategist): add aggregator sizing helpers"
```

### Task 5.3: build_thesis_from_proposers helper

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_aggregator_thesis.py`:
```python
"""build_thesis_from_proposers — most-conservative defaults across personas."""
from __future__ import annotations

from datetime import datetime, timezone

from agents.strategist.aggregator import build_thesis_from_proposers
from agents.strategist.member_schema import MemberStance


def _now():
    return datetime.now(tz=timezone.utc)


def _stance(persona, weight, *, horizon=None, target=None, stop=None,
            cat=None, rationale="x"):
    return MemberStance(
        ticker="AAPL",
        persona=persona,
        preferred_weight=weight,
        conviction=0.7,
        rationale=rationale,
        horizon=horizon,
        target_price=target,
        stop_price=stop,
        catalyst=cat,
    )


def _ctx():
    return {
        "ticker": "AAPL",
        "opened_at": _now(),
        "opened_price": 200.0,
        "opened_tag": "council_open",
        "last_reviewed_at": _now(),
    }


def test_only_proposers_contribute_to_rationale():
    # Value & momentum propose to open; contrarian dissents at 0.
    members = [
        _stance("value", 0.10, horizon="long_term", target=250.0, stop=180.0,
                rationale="cheap on FCF"),
        _stance("momentum", 0.10, horizon="swing", target=240.0, stop=190.0,
                rationale="strong breakout"),
        _stance("contrarian", 0.0, rationale="too crowded"),
    ]
    th = build_thesis_from_proposers(members, _ctx())
    assert "V:" in th.rationale
    assert "M:" in th.rationale
    assert "C:" not in th.rationale, "non-proposer must not appear in rationale"


def test_shortest_horizon_wins():
    members = [
        _stance("value", 0.10, horizon="long_term"),
        _stance("momentum", 0.10, horizon="intraday"),
        _stance("contrarian", 0.10, horizon="swing"),
    ]
    th = build_thesis_from_proposers(members, _ctx())
    assert th.horizon == "intraday"


def test_min_target_price_wins():
    members = [
        _stance("value", 0.10, target=250.0),
        _stance("momentum", 0.10, target=230.0),
        _stance("contrarian", 0.10, target=260.0),
    ]
    th = build_thesis_from_proposers(members, _ctx())
    assert th.target_price == 230.0


def test_max_stop_price_wins():
    """Tightest stop is the highest stop_price for a long position."""
    members = [
        _stance("value", 0.10, stop=180.0),
        _stance("momentum", 0.10, stop=195.0),
        _stance("contrarian", 0.10, stop=185.0),
    ]
    th = build_thesis_from_proposers(members, _ctx())
    assert th.stop_price == 195.0


def test_catalyst_v_m_c_priority():
    members = [
        _stance("value", 0.10, cat=None),
        _stance("momentum", 0.10, cat="earnings beat"),
        _stance("contrarian", 0.10, cat="insider buying"),
    ]
    th = build_thesis_from_proposers(members, _ctx())
    assert th.catalyst == "earnings beat"


def test_catalyst_falls_through_to_contrarian():
    members = [
        _stance("value", 0.10, cat=None),
        _stance("momentum", 0.10, cat=None),
        _stance("contrarian", 0.10, cat="insider buying"),
    ]
    th = build_thesis_from_proposers(members, _ctx())
    assert th.catalyst == "insider buying"


def test_rationale_capped_at_400_chars():
    long_rat = "x" * 200
    members = [
        _stance("value", 0.10, rationale=long_rat),
        _stance("momentum", 0.10, rationale=long_rat),
        _stance("contrarian", 0.10, rationale=long_rat),
    ]
    th = build_thesis_from_proposers(members, _ctx())
    assert len(th.rationale) <= 400


def test_no_proposers_raises():
    import pytest
    members = [
        _stance("value", 0.0),
        _stance("momentum", 0.0),
        _stance("contrarian", 0.0),
    ]
    with pytest.raises(ValueError):
        build_thesis_from_proposers(members, _ctx())
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_thesis.py -v`
Expected: fail.

- [ ] **Step 3: Add the helper**

Append to `src/agents/strategist/aggregator.py`:
```python
from typing import Any

from agents.strategist.member_schema import MemberStance
from agents.strategist.schema import PositionThesis

_HORIZON_RANK = {"intraday": 0, "swing": 1, "long_term": 2}
_PERSONA_LETTER = {"value": "V", "momentum": "M", "contrarian": "C"}


def _is_proposer(s: MemberStance) -> bool:
    return s.preferred_weight > OPEN_EPSILON


def build_thesis_from_proposers(
    members: list[MemberStance],
    tick_context: dict[str, Any],
) -> PositionThesis:
    """Build a PositionThesis from the subset of members proposing to open.

    Most-conservative defaults: shortest horizon, lowest target_price,
    highest (tightest) stop_price. Catalyst falls through V -> M -> C.
    """
    proposers = [m for m in members if _is_proposer(m)]
    if not proposers:
        raise ValueError("build_thesis_from_proposers called with no proposers")

    # Multi-voice rationale, in V -> M -> C order, capped at 400 chars.
    proposers_sorted = sorted(
        proposers,
        key=lambda m: ("value", "momentum", "contrarian").index(m.persona),
    )
    parts = [f"{_PERSONA_LETTER[m.persona]}: {m.rationale}" for m in proposers_sorted]
    rationale = " | ".join(parts)[:400]

    # Shortest horizon (intraday < swing < long_term); ignore None.
    horizons = [m.horizon for m in proposers if m.horizon is not None]
    horizon = min(horizons, key=_HORIZON_RANK.__getitem__) if horizons else "swing"

    # Most-conservative entry levels.
    targets = [m.target_price for m in proposers if m.target_price is not None]
    target = min(targets) if targets else None
    stops = [m.stop_price for m in proposers if m.stop_price is not None]
    stop = max(stops) if stops else None

    # First non-null catalyst in V -> M -> C order.
    catalyst = None
    for m in proposers_sorted:
        if m.catalyst is not None:
            catalyst = m.catalyst
            break

    return PositionThesis(
        ticker=tick_context["ticker"],
        opened_at=tick_context["opened_at"],
        opened_price=tick_context["opened_price"],
        opened_tag=tick_context["opened_tag"],
        rationale=rationale,
        horizon=horizon,
        target_price=target,
        stop_price=stop,
        catalyst=catalyst,
        last_reviewed_at=tick_context["last_reviewed_at"],
        last_review_note="",
    )
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_thesis.py -v`
Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/aggregator.py tests/unit/strategist/test_aggregator_thesis.py
git commit -m "feat(strategist): build_thesis_from_proposers with conservative defaults"
```

### Task 5.4: Per-ticker quorum logic + first_close_reason

This is the heart of the aggregator: classify each ticker's transition and produce final weight + lifecycle artefacts.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/strategist/test_aggregator_quorum.py`:
```python
"""Per-ticker quorum + transition classification."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.aggregator import resolve_ticker, _PerTickerOutcome
from agents.strategist.member_schema import MemberStance


def _now():
    return datetime.now(tz=timezone.utc)


def _ctx():
    return {
        "ticker": "AAPL",
        "opened_at": _now(),
        "opened_price": 200.0,
        "opened_tag": "council_open",
        "last_reviewed_at": _now(),
    }


def _s(persona, weight, *, conv=0.7, **kw):
    return MemberStance(
        ticker="AAPL", persona=persona, preferred_weight=weight,
        conviction=conv, rationale="x", **kw,
    )


# ── Currently flat (curr=0) ──────────────────────────────────────────────────

def test_flat_to_flat_all_zero_is_hold():
    members = [_s("value", 0.0), _s("momentum", 0.0), _s("contrarian", 0.0)]
    out = resolve_ticker(members, curr=0.0, n_available=3, ctx=_ctx())
    assert out.decision == "hold"
    assert out.final_weight == 0.0
    assert out.thesis is None
    assert out.close_reason is None


def test_flat_to_held_unanimous_open():
    members = [
        _s("value", 0.10, horizon="long_term", target_price=250.0, stop_price=180.0),
        _s("momentum", 0.12, horizon="swing", target_price=240.0, stop_price=190.0),
        _s("contrarian", 0.10, horizon="swing", target_price=260.0, stop_price=185.0),
    ]
    out = resolve_ticker(members, curr=0.0, n_available=3, ctx=_ctx())
    assert out.decision == "open"
    assert 0.0 < out.final_weight <= 0.30
    assert out.thesis is not None


def test_flat_to_held_quorum_dissent_dilutes():
    # 2-of-3 propose open at 0.20 each, contrarian dissents at 0.
    members = [
        _s("value", 0.20, horizon="long_term", target_price=250.0, stop_price=180.0),
        _s("momentum", 0.20, horizon="swing", target_price=240.0, stop_price=190.0),
        _s("contrarian", 0.0, rationale="too crowded"),
    ]
    out = resolve_ticker(members, curr=0.0, n_available=3, ctx=_ctx())
    assert out.decision == "open"
    # Final weight should be less than 0.20 because dissent dilutes.
    assert out.final_weight < 0.20
    assert out.thesis is not None
    # Thesis built from value + momentum only (no C: marker).
    assert "C:" not in out.thesis.rationale


def test_flat_to_held_quorum_miss_holds():
    members = [
        _s("value", 0.20, horizon="long_term", target_price=250.0, stop_price=180.0),
        _s("momentum", 0.0),
        _s("contrarian", 0.0),
    ]
    out = resolve_ticker(members, curr=0.0, n_available=3, ctx=_ctx())
    assert out.decision == "hold"
    assert out.final_weight == 0.0
    assert out.thesis is None


# ── Currently held (curr>0) ──────────────────────────────────────────────────

def test_held_close_any_member_triggers():
    members = [
        _s("value", 0.10),
        _s("momentum", 0.10),
        _s("contrarian", 0.0, close_reason="extreme has resolved"),
    ]
    out = resolve_ticker(members, curr=0.10, n_available=3, ctx=_ctx())
    assert out.decision == "close"
    assert out.final_weight == 0.0
    assert out.close_reason is not None
    assert "C:" in out.close_reason or "extreme" in out.close_reason


def test_held_trim_when_avg_below_curr():
    members = [_s("value", 0.05), _s("momentum", 0.05), _s("contrarian", 0.05)]
    out = resolve_ticker(members, curr=0.10, n_available=3, ctx=_ctx())
    assert out.decision == "trim"
    assert out.final_weight < 0.10


def test_held_add_when_avg_above_curr():
    members = [_s("value", 0.15), _s("momentum", 0.15), _s("contrarian", 0.15)]
    out = resolve_ticker(members, curr=0.10, n_available=3, ctx=_ctx())
    assert out.decision == "add"
    assert out.final_weight > 0.10


def test_held_hold_when_avg_close_to_curr():
    members = [_s("value", 0.10), _s("momentum", 0.105), _s("contrarian", 0.095)]
    out = resolve_ticker(members, curr=0.10, n_available=3, ctx=_ctx())
    assert out.decision == "hold"
    assert abs(out.final_weight - 0.10) < 0.02
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_quorum.py -v`
Expected: fail.

- [ ] **Step 3: Add resolve_ticker + outcome dataclass**

Append to `src/agents/strategist/aggregator.py`:
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _PerTickerOutcome:
    decision: str                         # "open" | "close" | "trim" | "add" | "hold"
    final_weight: float
    thesis: PositionThesis | None
    close_reason: str | None
    disagreement: float                   # variance(prefs) for telemetry


def _variance(xs: list[float]) -> float:
    if not xs:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def _first_close_reason(members: list[MemberStance]) -> str | None:
    """Concatenate close_reason text from all members proposing close, V->M->C order, cap 120."""
    order = ("value", "momentum", "contrarian")
    closing = [
        m for m in sorted(members, key=lambda s: order.index(s.persona))
        if m.preferred_weight < CLOSE_EPSILON and m.close_reason
    ]
    if not closing:
        # Member triggered close but didn't write a reason — synthesise one.
        closing_silent = [m for m in members if m.preferred_weight < CLOSE_EPSILON]
        if closing_silent:
            letters = "".join(_PERSONA_LETTER[m.persona] for m in closing_silent)
            return f"{letters}: implicit close (no reason given)"
        return None
    parts = [f"{_PERSONA_LETTER[m.persona]}: {m.close_reason}" for m in closing]
    return " | ".join(parts)[:120]


def resolve_ticker(
    members: list[MemberStance],
    *,
    curr: float,
    n_available: int,
    ctx: dict[str, Any],
) -> _PerTickerOutcome:
    """Classify the per-ticker transition and produce the final weight + artefacts.

    `members` is the list of stances for ONE ticker (one per available persona).
    `curr` is the current portfolio weight for that ticker (0 if flat).
    `n_available` is the count of persona stances overall this tick (degraded mode).
    """
    prefs = clamp_preferred_weights([m.preferred_weight for m in members])
    convs = [m.conviction for m in members]
    proposes_open = sum(1 for p in prefs if p > OPEN_EPSILON)
    proposes_close = sum(1 for p in prefs if p < CLOSE_EPSILON)
    disagreement = _variance(prefs)

    # ── Currently flat ──────────────────────────────────────────────
    if curr <= CLOSE_EPSILON:
        if proposes_open >= effective_open_quorum(n_available):
            final = confidence_weighted_avg(prefs, convs)
            thesis = build_thesis_from_proposers(members, ctx)
            return _PerTickerOutcome("open", final, thesis, None, disagreement)
        return _PerTickerOutcome("hold", 0.0, None, None, disagreement)

    # ── Currently held ──────────────────────────────────────────────
    if proposes_close >= CLOSE_QUORUM:
        return _PerTickerOutcome("close", 0.0, None, _first_close_reason(members), disagreement)

    final = confidence_weighted_avg(prefs, convs)
    delta = final - curr
    if abs(delta) < SIZE_CHANGE_EPSILON:
        return _PerTickerOutcome("hold", final, None, None, disagreement)
    if delta < 0:
        # partial reduction; no close_reason required (lifecycle contract relaxed in Spec 2)
        return _PerTickerOutcome("trim", final, None, None, disagreement)
    return _PerTickerOutcome("add", final, None, None, disagreement)
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_quorum.py -v`
Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/aggregator.py tests/unit/strategist/test_aggregator_quorum.py
git commit -m "feat(strategist): per-ticker resolve_ticker with quorum classification"
```

### Task 5.5: Aggregator-level full StrategistDecision assembly + validation

This wraps the per-ticker logic into a function that takes all three stance lists and produces the full `StrategistDecision` + `CouncilTelemetry`. It also runs the existing `validate_lifecycle_contract` and the exhaustive-weights check, raising `StrategistContractViolation` on any violation.

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_aggregator_validation.py`:
```python
"""Aggregator-level assembly + validation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.aggregator import aggregate
from agents.strategist.member_schema import MemberStance
from agents.risk_gate.lifecycle import StrategistContractViolation


def _now():
    return datetime.now(tz=timezone.utc)


def _ctx_for(ticker: str):
    return {
        "ticker": ticker,
        "opened_at": _now(),
        "opened_price": 200.0,
        "opened_tag": "council_open",
        "last_reviewed_at": _now(),
    }


def _stance(ticker, persona, weight, *, conv=0.7, **kw):
    return MemberStance(
        ticker=ticker, persona=persona, preferred_weight=weight, conviction=conv,
        rationale="x", **kw,
    )


def test_assembles_strategist_decision_for_all_tickers():
    tickers = ["AAPL", "MSFT"]
    stances_by_persona = {
        "value":      [_stance("AAPL", "value", 0.0), _stance("MSFT", "value", 0.0)],
        "momentum":   [_stance("AAPL", "momentum", 0.0), _stance("MSFT", "momentum", 0.0)],
        "contrarian": [_stance("AAPL", "contrarian", 0.0), _stance("MSFT", "contrarian", 0.0)],
    }
    decision, telemetry = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={},
        tick_context_factory=_ctx_for,
    )
    assert set(decision.target_weights.keys()) == set(tickers)
    assert all(w == 0.0 for w in decision.target_weights.values())
    assert telemetry.quorum_decisions == {"AAPL": "hold", "MSFT": "hold"}


def test_off_watchlist_member_stance_raises():
    """Aggregator should fail fast if a stance references a ticker not in the watchlist."""
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_stance("AAPL", "value", 0.0), _stance("TSLA", "value", 0.10)],
        "momentum":   [_stance("AAPL", "momentum", 0.0)],
        "contrarian": [_stance("AAPL", "contrarian", 0.0)],
    }
    with pytest.raises(StrategistContractViolation):
        aggregate(
            stances_by_persona=stances_by_persona,
            tickers=tickers,
            positions={},
            tick_context_factory=_ctx_for,
        )


def test_missing_ticker_in_one_persona_raises():
    tickers = ["AAPL", "MSFT"]
    stances_by_persona = {
        "value":      [_stance("AAPL", "value", 0.0)],   # MSFT missing
        "momentum":   [_stance("AAPL", "momentum", 0.0), _stance("MSFT", "momentum", 0.0)],
        "contrarian": [_stance("AAPL", "contrarian", 0.0), _stance("MSFT", "contrarian", 0.0)],
    }
    with pytest.raises(StrategistContractViolation):
        aggregate(
            stances_by_persona=stances_by_persona,
            tickers=tickers,
            positions={},
            tick_context_factory=_ctx_for,
        )


def test_open_includes_position_thesis():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_stance("AAPL", "value", 0.10, horizon="long_term",
                                target_price=250.0, stop_price=180.0)],
        "momentum":   [_stance("AAPL", "momentum", 0.10, horizon="swing",
                                target_price=240.0, stop_price=190.0)],
        "contrarian": [_stance("AAPL", "contrarian", 0.0)],
    }
    decision, telemetry = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={},
        tick_context_factory=_ctx_for,
    )
    assert "AAPL" in decision.new_positions
    assert decision.new_positions["AAPL"].horizon == "swing"   # min horizon
    assert telemetry.quorum_decisions["AAPL"] == "open"


def test_close_includes_close_reason():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_stance("AAPL", "value", 0.10)],
        "momentum":   [_stance("AAPL", "momentum", 0.10)],
        "contrarian": [_stance("AAPL", "contrarian", 0.0, close_reason="extreme resolved")],
    }
    decision, telemetry = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={"AAPL": 0.10},
        tick_context_factory=_ctx_for,
    )
    assert "AAPL" in decision.close_reasons
    assert "extreme" in decision.close_reasons["AAPL"]
    assert telemetry.quorum_decisions["AAPL"] == "close"


def test_decision_tag_is_derived():
    tickers = ["AAPL", "MSFT"]
    stances_by_persona = {
        "value":      [_stance("AAPL", "value", 0.10, horizon="swing", target_price=250.0, stop_price=180.0),
                       _stance("MSFT", "value", 0.0)],
        "momentum":   [_stance("AAPL", "momentum", 0.10, horizon="swing", target_price=240.0, stop_price=190.0),
                       _stance("MSFT", "momentum", 0.0)],
        "contrarian": [_stance("AAPL", "contrarian", 0.0),
                       _stance("MSFT", "contrarian", 0.0)],
    }
    decision, _ = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={},
        tick_context_factory=_ctx_for,
    )
    assert decision.decision_tag.startswith("council_")
    assert "1o" in decision.decision_tag    # one open


def test_updated_thesis_preserved_unchanged():
    """Spec 1: aggregator preserves prior thesis verbatim; Spec 3 will own thesis evolution."""
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_stance("AAPL", "value", 0.0)],
        "momentum":   [_stance("AAPL", "momentum", 0.0)],
        "contrarian": [_stance("AAPL", "contrarian", 0.0)],
    }
    decision, _ = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={},
        tick_context_factory=_ctx_for,
        prior_thesis="bull market until proven otherwise",
    )
    assert decision.updated_thesis == "bull market until proven otherwise"
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_validation.py -v`
Expected: fail (no `aggregate` symbol).

- [ ] **Step 3: Add aggregate() to aggregator.py**

Append to `src/agents/strategist/aggregator.py`:
```python
from typing import Callable

from agents.risk_gate.lifecycle import (
    StrategistContractViolation,
    validate_lifecycle_contract,
)
from agents.strategist.member_schema import CouncilTelemetry
from agents.strategist.schema import StrategistDecision


def _index_by_ticker(stances: list[MemberStance]) -> dict[str, MemberStance]:
    return {s.ticker: s for s in stances}


def aggregate(
    *,
    stances_by_persona: dict[str, list[MemberStance]],
    tickers: list[str],
    positions: dict[str, float],
    tick_context_factory: Callable[[str], dict[str, Any]],
    prior_thesis: str = "",
) -> tuple[StrategistDecision, CouncilTelemetry]:
    """Reconcile three persona stance lists into one StrategistDecision + telemetry.

    `stances_by_persona` keys must be a subset of PERSONAS. Missing personas
    indicate degraded mode (one or more personas failed to emit stances).

    `tick_context_factory` is called per-ticker to produce the dict required by
    PositionThesis construction (opened_at, opened_price, opened_tag, etc.).

    Raises StrategistContractViolation if the assembled decision misses any
    watchlist ticker, includes off-watchlist tickers, or breaks the lifecycle
    contract (open without thesis / close without reason).

    Raises CouncilStanceUnavailable if all three personas are missing.
    """
    available = [p for p in PERSONAS if stances_by_persona.get(p)]
    if not available:
        raise CouncilStanceUnavailable("no persona stances available this tick")

    # Off-watchlist check: every ticker referenced by any stance must be in `tickers`.
    watchlist = set(tickers)
    for persona in available:
        for s in stances_by_persona[persona]:
            if s.ticker not in watchlist:
                raise StrategistContractViolation(
                    f"{persona} emitted off-watchlist ticker {s.ticker!r}"
                )

    # Per-persona indices, defensive on missing-tickers (will surface below).
    indexed = {p: _index_by_ticker(stances_by_persona[p]) for p in available}

    final_weights: dict[str, float] = {}
    new_positions: dict[str, PositionThesis] = {}
    close_reasons: dict[str, str] = {}
    quorum_decisions: dict[str, str] = {}
    disagreement: dict[str, float] = {}
    all_stances: list[MemberStance] = []
    convictions_for_acted: list[float] = []

    for ticker in tickers:
        members: list[MemberStance] = []
        for p in available:
            s = indexed[p].get(ticker)
            if s is None:
                raise StrategistContractViolation(
                    f"{p} did not emit a stance for {ticker}"
                )
            members.append(s)
            all_stances.append(s)

        outcome = resolve_ticker(
            members,
            curr=positions.get(ticker, 0.0),
            n_available=len(available),
            ctx=tick_context_factory(ticker),
        )
        final_weights[ticker] = outcome.final_weight
        quorum_decisions[ticker] = outcome.decision
        disagreement[ticker] = outcome.disagreement
        if outcome.thesis is not None:
            new_positions[ticker] = outcome.thesis
        if outcome.close_reason is not None:
            close_reasons[ticker] = outcome.close_reason
        if outcome.decision != "hold":
            convictions_for_acted.extend(m.conviction for m in members)

    # Lifecycle contract — raises StrategistContractViolation on bad output.
    validate_lifecycle_contract(
        new_weights=final_weights,
        current_weights=positions,
        new_positions=new_positions,
        close_reasons=close_reasons,
    )

    # Counts for decision_tag + reasoning.
    n_open = sum(1 for d in quorum_decisions.values() if d == "open")
    n_close = sum(1 for d in quorum_decisions.values() if d == "close")
    n_trim = sum(1 for d in quorum_decisions.values() if d == "trim")
    n_add = sum(1 for d in quorum_decisions.values() if d == "add")
    mean_disagreement = (
        sum(disagreement.values()) / len(disagreement) if disagreement else 0.0
    )

    decision_tag = f"council_{n_open}o_{n_close}c_{n_trim}t_{n_add}a"
    reasoning = (
        f"council: {n_open} opens, {n_close} closes, {n_trim} trims, {n_add} adds; "
        f"mean disagreement {mean_disagreement:.2f}"
    )[:300]
    confidence = (
        sum(convictions_for_acted) / len(convictions_for_acted)
        if convictions_for_acted else 0.0
    )

    decision = StrategistDecision(
        target_weights=final_weights,
        decision_tag=decision_tag,
        reasoning=reasoning,
        updated_thesis=prior_thesis,                # Spec 1: preserve unchanged
        confidence=confidence,
        new_positions=new_positions,
        close_reasons=close_reasons,
    )
    telemetry = CouncilTelemetry(
        stances=all_stances,
        quorum_decisions=quorum_decisions,
        disagreement_score=disagreement,
        degraded_member=None if len(available) == 3 else _missing_persona(available),
    )
    return decision, telemetry


def _missing_persona(available: list[str]) -> str | None:
    missing = [p for p in PERSONAS if p not in available]
    return missing[0] if missing else None
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_validation.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Run all aggregator tests together**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: all aggregator tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/aggregator.py tests/unit/strategist/test_aggregator_validation.py
git commit -m "feat(strategist): aggregate() assembles full StrategistDecision + telemetry"
```

### Task 5.6: Degraded-mode behaviour

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_aggregator_degraded.py`:
```python
"""Degraded mode: one/two/three personas missing."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.strategist.aggregator import aggregate, CouncilStanceUnavailable
from agents.strategist.member_schema import MemberStance


def _now():
    return datetime.now(tz=timezone.utc)


def _ctx_for(ticker: str):
    return {
        "ticker": ticker,
        "opened_at": _now(),
        "opened_price": 200.0,
        "opened_tag": "council_open",
        "last_reviewed_at": _now(),
    }


def _s(ticker, persona, weight, *, conv=0.7, **kw):
    return MemberStance(
        ticker=ticker, persona=persona, preferred_weight=weight, conviction=conv,
        rationale="x", **kw,
    )


def test_one_persona_missing_two_remaining_must_unanimously_open():
    tickers = ["AAPL"]
    # Both remaining personas at >0 -> open allowed.
    stances_by_persona = {
        "value":      [_s("AAPL", "value", 0.10, horizon="swing", target_price=250.0, stop_price=180.0)],
        "momentum":   [_s("AAPL", "momentum", 0.10, horizon="swing", target_price=240.0, stop_price=190.0)],
        # contrarian missing
    }
    decision, telemetry = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={},
        tick_context_factory=_ctx_for,
    )
    assert telemetry.quorum_decisions["AAPL"] == "open"
    assert telemetry.degraded_member == "contrarian"


def test_one_persona_missing_one_dissents_blocks_open():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_s("AAPL", "value", 0.10, horizon="swing", target_price=250.0, stop_price=180.0)],
        "momentum":   [_s("AAPL", "momentum", 0.0)],   # dissents
    }
    decision, telemetry = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={},
        tick_context_factory=_ctx_for,
    )
    assert telemetry.quorum_decisions["AAPL"] == "hold"
    assert decision.target_weights["AAPL"] == 0.0


def test_two_personas_missing_blocks_opens_entirely():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_s("AAPL", "value", 0.10, horizon="swing", target_price=250.0, stop_price=180.0)],
        # momentum + contrarian missing
    }
    decision, telemetry = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={},
        tick_context_factory=_ctx_for,
    )
    assert telemetry.quorum_decisions["AAPL"] == "hold"
    assert decision.target_weights["AAPL"] == 0.0


def test_two_personas_missing_close_still_works():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value": [_s("AAPL", "value", 0.0, close_reason="thesis broken")],
    }
    decision, telemetry = aggregate(
        stances_by_persona=stances_by_persona,
        tickers=tickers,
        positions={"AAPL": 0.10},
        tick_context_factory=_ctx_for,
    )
    assert telemetry.quorum_decisions["AAPL"] == "close"
    assert "AAPL" in decision.close_reasons


def test_all_three_missing_raises():
    with pytest.raises(CouncilStanceUnavailable):
        aggregate(
            stances_by_persona={},
            tickers=["AAPL"],
            positions={},
            tick_context_factory=_ctx_for,
        )
```

- [ ] **Step 2: Run, expect green** (the aggregate() implementation already handles degraded mode via effective_open_quorum)

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_degraded.py -v`
Expected: 5 tests PASS. **If any fail, fix the aggregator, do not loosen the test.**

- [ ] **Step 3: Commit**

```bash
git add tests/unit/strategist/test_aggregator_degraded.py
git commit -m "test(strategist): degraded-mode council aggregator behaviour"
```

### Task 5.7: CouncilTelemetry surface tests

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_council_telemetry.py`:
```python
"""CouncilTelemetry — disagreement_score and quorum_decisions coverage."""
from __future__ import annotations

from datetime import datetime, timezone

from agents.strategist.aggregator import aggregate
from agents.strategist.member_schema import MemberStance


def _now():
    return datetime.now(tz=timezone.utc)


def _ctx_for(t):
    return {
        "ticker": t, "opened_at": _now(), "opened_price": 200.0,
        "opened_tag": "council_open", "last_reviewed_at": _now(),
    }


def _s(t, p, w, **kw):
    return MemberStance(ticker=t, persona=p, preferred_weight=w, conviction=0.7,
                         rationale="x", **kw)


def test_disagreement_score_in_range():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_s("AAPL", "value", 0.30)],     # max
        "momentum":   [_s("AAPL", "momentum", 0.0)],
        "contrarian": [_s("AAPL", "contrarian", 0.0)],
    }
    _, telemetry = aggregate(
        stances_by_persona=stances_by_persona, tickers=tickers,
        positions={}, tick_context_factory=_ctx_for,
    )
    s = telemetry.disagreement_score["AAPL"]
    assert 0.0 <= s <= 0.25     # max variance for prefs in [0, 0.30]


def test_disagreement_score_zero_on_unanimous():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_s("AAPL", "value", 0.10)],
        "momentum":   [_s("AAPL", "momentum", 0.10)],
        "contrarian": [_s("AAPL", "contrarian", 0.10)],
    }
    _, telemetry = aggregate(
        stances_by_persona=stances_by_persona, tickers=tickers,
        positions={}, tick_context_factory=_ctx_for,
    )
    assert telemetry.disagreement_score["AAPL"] == 0.0


def test_quorum_decisions_populated_for_every_ticker():
    tickers = ["AAPL", "MSFT", "GOOGL"]
    stances_by_persona = {
        "value":      [_s(t, "value", 0.0) for t in tickers],
        "momentum":   [_s(t, "momentum", 0.0) for t in tickers],
        "contrarian": [_s(t, "contrarian", 0.0) for t in tickers],
    }
    _, telemetry = aggregate(
        stances_by_persona=stances_by_persona, tickers=tickers,
        positions={}, tick_context_factory=_ctx_for,
    )
    assert set(telemetry.quorum_decisions.keys()) == set(tickers)


def test_telemetry_stances_includes_all_emitted():
    tickers = ["AAPL"]
    stances_by_persona = {
        "value":      [_s("AAPL", "value", 0.10, horizon="swing", target_price=250.0, stop_price=180.0)],
        "momentum":   [_s("AAPL", "momentum", 0.10, horizon="swing", target_price=240.0, stop_price=190.0)],
        "contrarian": [_s("AAPL", "contrarian", 0.0)],
    }
    _, telemetry = aggregate(
        stances_by_persona=stances_by_persona, tickers=tickers,
        positions={}, tick_context_factory=_ctx_for,
    )
    assert len(telemetry.stances) == 3
```

- [ ] **Step 2: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_council_telemetry.py -v`
Expected: 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/strategist/test_council_telemetry.py
git commit -m "test(strategist): council telemetry surface coverage"
```

### Task 5.8: CouncilAggregator BaseAgent wrapper

The aggregator module so far has a pure `aggregate()` function. We now wrap it in an ADK `BaseAgent` that reads from `ctx.session.state` and writes back the decision + telemetry.

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_aggregator_agent.py`:
```python
"""CouncilAggregator BaseAgent: reads session state, writes decision + telemetry."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

from agents.strategist.aggregator import CouncilAggregator
from agents.strategist.member_schema import MemberStance


def _now():
    return datetime.now(tz=timezone.utc)


def _stance_dict(ticker, persona, weight, **kw):
    """ADK-style: state may hold dicts (model_dump) rather than Pydantic instances."""
    return MemberStance(
        ticker=ticker, persona=persona, preferred_weight=weight,
        conviction=0.7, rationale="x", **kw,
    ).model_dump(mode="json")


def _make_ctx(state):
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.state = state
    return ctx


def test_agent_writes_strategist_decision_and_telemetry():
    state = {
        "tickers": ["AAPL"],
        "positions": {},
        "thesis": "bull market",
        "value_stances":      [_stance_dict("AAPL", "value", 0.0)],
        "momentum_stances":   [_stance_dict("AAPL", "momentum", 0.0)],
        "contrarian_stances": [_stance_dict("AAPL", "contrarian", 0.0)],
        "tick_id": "test-tick-1",
        "current_prices": {"AAPL": 200.0},
    }
    agent = CouncilAggregator()

    async def _run():
        async for _ in agent._run_async_impl(_make_ctx(state)):
            pass

    asyncio.run(_run())

    assert "strategist_decision" in state
    assert "council_telemetry" in state
    assert state["strategist_decision"]["target_weights"] == {"AAPL": 0.0}
    assert state["council_telemetry"]["quorum_decisions"] == {"AAPL": "hold"}
    # Spec 1: thesis preserved.
    assert state["strategist_decision"]["updated_thesis"] == "bull market"


def test_agent_handles_pydantic_or_dict_stances_in_state():
    """ADK output_schema=list[Model] may store dicts; agent must accept either."""
    s_dict = _stance_dict("AAPL", "value", 0.0)
    s_obj = MemberStance(
        ticker="AAPL", persona="momentum", preferred_weight=0.0,
        conviction=0.7, rationale="x",
    )
    state = {
        "tickers": ["AAPL"],
        "positions": {},
        "thesis": "",
        "value_stances":      [s_dict],
        "momentum_stances":   [s_obj],   # Pydantic instance, not dict
        "contrarian_stances": [_stance_dict("AAPL", "contrarian", 0.0)],
        "tick_id": "test-tick-2",
        "current_prices": {"AAPL": 200.0},
    }
    agent = CouncilAggregator()

    async def _run():
        async for _ in agent._run_async_impl(_make_ctx(state)):
            pass

    asyncio.run(_run())

    assert "strategist_decision" in state


def test_agent_reads_current_prices_for_thesis_opened_price():
    state = {
        "tickers": ["AAPL"],
        "positions": {},
        "thesis": "",
        "value_stances": [_stance_dict("AAPL", "value", 0.10,
                                        horizon="swing", target_price=250.0, stop_price=180.0)],
        "momentum_stances": [_stance_dict("AAPL", "momentum", 0.10,
                                            horizon="swing", target_price=240.0, stop_price=190.0)],
        "contrarian_stances": [_stance_dict("AAPL", "contrarian", 0.0)],
        "tick_id": "test-tick-3",
        "current_prices": {"AAPL": 199.50},
    }
    agent = CouncilAggregator()

    async def _run():
        async for _ in agent._run_async_impl(_make_ctx(state)):
            pass

    asyncio.run(_run())

    thesis = state["strategist_decision"]["new_positions"]["AAPL"]
    assert thesis["opened_price"] == 199.50
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_agent.py -v`
Expected: fail (no `CouncilAggregator` class).

- [ ] **Step 3: Add the BaseAgent wrapper**

Append to `src/agents/strategist/aggregator.py`:
```python
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event


def _coerce_stances(raw: list, persona_default: str) -> list[MemberStance]:
    """State may hold dicts (from output_schema=list[Model]) or Pydantic instances.
    Normalise to list[MemberStance]. Persona is forced to `persona_default`,
    regardless of what the LLM may have emitted, to make 'persona' authoritative
    on the aggregator side."""
    out: list[MemberStance] = []
    for item in raw:
        if isinstance(item, MemberStance):
            s = item
        else:
            s = MemberStance.model_validate(item)
        # Force the deterministic persona label.
        if s.persona != persona_default:
            s = s.model_copy(update={"persona": persona_default})
        out.append(s)
    return out


class CouncilAggregator(BaseAgent):
    """ADK BaseAgent that reads persona stances from session state, runs aggregate(),
    and writes StrategistDecision + CouncilTelemetry back to state.
    """

    name: str = "CouncilAggregator"

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state

        stances_by_persona: dict[str, list[MemberStance]] = {}
        for persona in PERSONAS:
            raw = state.get(f"{persona}_stances")
            if raw:
                stances_by_persona[persona] = _coerce_stances(raw, persona)

        tickers: list[str] = list(state.get("tickers", []))
        positions: dict[str, float] = dict(state.get("positions", {}))
        prior_thesis: str = str(state.get("thesis", ""))
        prices: dict[str, float] = state.get("current_prices", {}) or {}
        tick_id: str = str(state.get("tick_id", "unknown"))

        def _ctx_for(ticker: str) -> dict[str, Any]:
            from datetime import datetime, timezone
            now = datetime.now(tz=timezone.utc)
            return {
                "ticker": ticker,
                "opened_at": now,
                "opened_price": float(prices.get(ticker, 0.0)),
                "opened_tag": f"council_open_{tick_id}",
                "last_reviewed_at": now,
            }

        decision, telemetry = aggregate(
            stances_by_persona=stances_by_persona,
            tickers=tickers,
            positions=positions,
            tick_context_factory=_ctx_for,
            prior_thesis=prior_thesis,
        )

        # ADK persists state as serialisable dicts; mirror what the existing strategist did.
        state["strategist_decision"] = decision.model_dump(mode="json")
        state["council_telemetry"] = telemetry.model_dump(mode="json")
        return
        yield   # required to make this an async generator
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_aggregator_agent.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run all aggregator tests together**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ -v`
Expected: every test in the strategist test directory PASSES.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/aggregator.py tests/unit/strategist/test_aggregator_agent.py
git commit -m "feat(strategist): CouncilAggregator BaseAgent wraps aggregate()"
```

---

## Phase 6 — Wire it together: strategist_council + pipeline

**Files:**
- Create: `src/agents/strategist/council.py`
- Modify: `src/agents/strategist/__init__.py`
- Modify: `src/orchestrator/pipeline.py`
- Modify: `tests/integration/test_pipeline_composition.py`
- Create: `tests/integration/test_council_smoke.py` (Tier 2 — gated, optional)

### Task 6.1: strategist_council SequentialAgent

- [ ] **Step 1: Write failing test**

Create `tests/unit/strategist/test_council.py`:
```python
"""strategist_council SequentialAgent — Tier 1 structural test, no LLM calls."""
from __future__ import annotations

from google.adk.agents import SequentialAgent, ParallelAgent

from agents.strategist.council import _build_strategist_council
from agents.strategist.aggregator import CouncilAggregator


def test_council_is_sequential_agent_with_two_children():
    council = _build_strategist_council()
    assert isinstance(council, SequentialAgent)
    assert council.name == "Strategist"     # keep the original name to minimise downstream churn
    assert len(council.sub_agents) == 2


def test_council_first_child_is_persona_pool():
    council = _build_strategist_council()
    pool = council.sub_agents[0]
    assert isinstance(pool, ParallelAgent)
    assert pool.name == "PersonaPool"
    assert len(pool.sub_agents) == 3


def test_council_second_child_is_aggregator():
    council = _build_strategist_council()
    agg = council.sub_agents[1]
    assert isinstance(agg, CouncilAggregator)
    assert agg.name == "CouncilAggregator"


def test_factory_returns_fresh_instance_each_call():
    """Avoid ADK single-parent constraint when building multiple pipelines."""
    a = _build_strategist_council()
    b = _build_strategist_council()
    assert a is not b
    assert a.sub_agents[0] is not b.sub_agents[0]
```

- [ ] **Step 2: Run, expect ImportError**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_council.py -v`
Expected: fail.

- [ ] **Step 3: Create council.py**

Create `src/agents/strategist/council.py`:
```python
"""strategist_council — SequentialAgent wrapping persona_pool + CouncilAggregator.

This is the public symbol that replaces the old single-LlmAgent strategist at
pipeline position 2. Use _build_strategist_council() in build_pipeline() to
get a fresh instance per pipeline (ADK enforces a single-parent constraint).
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.strategist.aggregator import CouncilAggregator
from agents.strategist.personas import _build_persona_pool


def _build_strategist_council() -> SequentialAgent:
    """Build a fresh strategist council each time to avoid ADK's single-parent constraint."""
    return SequentialAgent(
        name="Strategist",   # preserved name — pipeline composition tests pin this position
        sub_agents=[
            _build_persona_pool(),
            CouncilAggregator(),
        ],
    )
```

- [ ] **Step 4: Run, expect green**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/test_council.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/council.py tests/unit/strategist/test_council.py
git commit -m "feat(strategist): strategist_council SequentialAgent (persona_pool + aggregator)"
```

### Task 6.2: Wire into pipeline.py

- [ ] **Step 1: Update the pipeline composition test**

Edit `tests/integration/test_pipeline_composition.py` — replace the file with:
```python
"""Pipeline structural tests — no LLM calls."""
from google.adk.agents import SequentialAgent, ParallelAgent

from broker.fake import FakeBroker
from orchestrator.pipeline import build_pipeline


def test_build_pipeline_returns_sequential_agent():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    assert isinstance(pipeline, SequentialAgent)


def test_pipeline_name():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    assert pipeline.name == "HourlyTick"


def test_pipeline_has_seven_stages():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    assert len(pipeline.sub_agents) == 7


def test_pipeline_stage_names():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    names = [a.name for a in pipeline.sub_agents]
    assert names[0] == "AnalystPool"
    assert names[1] == "AttributionWriter"
    assert names[2] == "Strategist"            # name preserved by strategist_council
    assert names[3] == "RiskGate"
    assert names[4] == "Executor"
    assert names[5] == "MemoryWriter"
    assert names[6] == "Snapshotter"


def test_strategist_position_is_now_a_council():
    """Position 2 must be a SequentialAgent (council), not a single LlmAgent."""
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    strategist = pipeline.sub_agents[2]
    assert isinstance(strategist, SequentialAgent)
    assert len(strategist.sub_agents) == 2
    pool, aggregator = strategist.sub_agents
    assert isinstance(pool, ParallelAgent)
    assert pool.name == "PersonaPool"
    assert aggregator.name == "CouncilAggregator"
```

- [ ] **Step 2: Run, expect failure** (the pipeline still uses the old `_build_strategist`)

Run: `.venv/Scripts/python -m pytest tests/integration/test_pipeline_composition.py -v`
Expected: 4 of 5 PASS, the new `test_strategist_position_is_now_a_council` FAILS.

- [ ] **Step 3: Replace `_build_strategist` in pipeline.py**

Edit `src/orchestrator/pipeline.py`:

Find:
```python
def _build_strategist():
    """Build a fresh Strategist LlmAgent each time."""
    from google.adk.agents import LlmAgent
    from agents.strategist.agent import _strategist_validation_callback
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistDecision
    return LlmAgent(
        name="Strategist",
        model="gemini-2.0-pro-001",
        instruction=STRATEGIST_INSTRUCTION,
        output_schema=StrategistDecision,
        output_key="strategist_decision",
        after_agent_callback=_strategist_validation_callback,
    )
```

Replace with:
```python
def _build_strategist():
    """Build a fresh Strategist council each time."""
    from agents.strategist.council import _build_strategist_council
    return _build_strategist_council()
```

- [ ] **Step 4: Run pipeline tests, expect green**

Run: `.venv/Scripts/python -m pytest tests/integration/test_pipeline_composition.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the full unit + integration suite to catch regressions**

Run: `.venv/Scripts/python -m pytest tests/unit tests/integration -v`
Expected: most tests PASS. Failing tests at this point: `tests/unit/test_strategist_prompt_template.py` (uses removed `STRATEGIST_INSTRUCTION`) and `tests/unit/test_strategist_validators.py` (imports removed `_strategist_validation_callback`). These are intentional and will be deleted in Phase 7.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/pipeline.py tests/integration/test_pipeline_composition.py
git commit -m "feat(orchestrator): wire strategist_council into pipeline at position 2"
```

### Task 6.3: Tier 2 council smoke (LLM-touching, gated)

This test exercises the real LLMs end-to-end. It should be runnable on demand but skipped by default in CI. The repo's existing convention for Tier 2 is to use `@pytest.mark.skipif` or environment-gated execution; mirror it.

- [ ] **Step 1: Check the existing convention**

Run: `.venv/Scripts/python -m pytest tests/integration/ --collect-only -q`
Look at how `test_analyst_pool.py` (existing Tier 2) handles gating. Match that pattern.

- [ ] **Step 2: Write the smoke test**

Create `tests/integration/test_council_smoke.py`:
```python
"""Tier 2 — strategist_council end-to-end with real Gemini Pro calls.

Skipped in default CI; run manually:
    .venv/Scripts/python -m pytest tests/integration/test_council_smoke.py -v -s
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# Match the existing Tier 2 gating pattern in test_analyst_pool.py.
# If that file uses a different env var, change here to match.
pytestmark = pytest.mark.skipif(
    os.environ.get("STOCKBOT_RUN_LLM_TESTS") != "1",
    reason="Tier 2 LLM test — set STOCKBOT_RUN_LLM_TESTS=1 to run",
)

from agents.strategist.council import _build_strategist_council


def _make_ctx(state):
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.state = state
    return ctx


def _now():
    return datetime.now(tz=timezone.utc)


def _bullish_signals(tickers):
    """Minimal viable signals — enough to give personas something to chew on."""
    return [
        {
            "ticker": t,
            "direction": "bullish",
            "confidence": 0.7,
            "key_factors": ["test"],
            "evidence": {},
        }
        for t in tickers
    ]


def test_council_runs_and_writes_decision():
    tickers = ["AAPL", "MSFT"]
    state = {
        "tickers": tickers,
        "portfolio": {"cash": 10_000.0, "positions": {}},
        "positions": {},
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "",
        "technical_signals": _bullish_signals(tickers),
        "fundamental_signals": _bullish_signals(tickers),
        "sentiment_signals": _bullish_signals(tickers),
        "smart_money_signals": [],
        "current_prices": {"AAPL": 200.0, "MSFT": 400.0},
        "tick_id": "smoke-1",
    }
    council = _build_strategist_council()

    async def _run():
        async for _ in council._run_async_impl(_make_ctx(state)):
            pass

    asyncio.run(_run())

    assert "strategist_decision" in state
    assert "council_telemetry" in state
    decision = state["strategist_decision"]
    assert set(decision["target_weights"].keys()) == set(tickers)
    telemetry = state["council_telemetry"]
    assert set(telemetry["quorum_decisions"].keys()) == set(tickers)
```

- [ ] **Step 3: Verify it's skipped by default**

Run: `.venv/Scripts/python -m pytest tests/integration/test_council_smoke.py -v`
Expected: test SKIPPED (no LLM call made). Set the env var only when you want to actually exercise LLMs.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_council_smoke.py
git commit -m "test(strategist): Tier 2 council smoke (gated by STOCKBOT_RUN_LLM_TESTS)"
```

---

## Phase 7 — Cleanup: delete legacy and update __init__

**Files:**
- Modify: `src/agents/strategist/__init__.py`
- Delete: `src/agents/strategist/agent.py`
- Delete: `tests/unit/test_strategist_prompt_template.py`
- Delete: `tests/unit/test_strategist_validators.py`
- Modify: `graphify-out/graph_delta.md` (append entry)

### Task 7.1: Update strategist package exports

- [ ] **Step 1: Read current exports**

Run: `.venv/Scripts/python -c "import agents.strategist; print(agents.strategist.__all__ if hasattr(agents.strategist, '__all__') else '<no __all__>')"`

- [ ] **Step 2: Replace `__init__.py`**

Replace `src/agents/strategist/__init__.py` contents with:
```python
"""Strategist council — multi-persona decision tier.

Public API:
    _build_strategist_council() — returns a fresh SequentialAgent for use in
                                  the orchestrator pipeline.
    StrategistDecision           — the council's external contract (unchanged).
    PositionThesis               — lifecycle artefact for new positions.
"""
from agents.strategist.council import _build_strategist_council
from agents.strategist.schema import PositionThesis, StrategistDecision

__all__ = ["_build_strategist_council", "StrategistDecision", "PositionThesis"]
```

- [ ] **Step 3: Verify nothing broke**

Run: `.venv/Scripts/python -m pytest tests/unit/strategist/ tests/integration/test_pipeline_composition.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/agents/strategist/__init__.py
git commit -m "refactor(strategist): expose council factory as package public API"
```

### Task 7.2: Delete the legacy single-strategist agent + its tests

- [ ] **Step 1: Confirm no remaining importers of the legacy symbols**

Run: `.venv/Scripts/python -c "from agents.strategist.agent import strategist_agent" 2>&1 | head -5`
(Just confirm the module still exists; we'll delete it next.)

Run: `.venv/Scripts/python -m pytest tests/ --collect-only 2>&1 | grep -i "strategist_validators\|strategist_prompt_template" | head -5`
Expected: lists the two test files we're about to delete; no other importers of `STRATEGIST_INSTRUCTION` or `_strategist_validation_callback` should remain. If any do, stop and fix them before deleting.

Run: `grep -r "from agents.strategist.agent" src tests 2>nul || rg "from agents.strategist.agent" src tests`
Expected: no matches. (`pipeline.py` was already updated in Phase 6.)

- [ ] **Step 2: Delete the legacy files**

```bash
git rm src/agents/strategist/agent.py
git rm tests/unit/test_strategist_prompt_template.py
git rm tests/unit/test_strategist_validators.py
```

- [ ] **Step 3: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: every test PASSES (or skips, for Tier 2 gated tests).

- [ ] **Step 4: Run ruff to catch any orphan imports**

Run: `.venv/Scripts/python -m ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor(strategist): delete legacy single-strategist agent and tests"
```

### Task 7.3: graphify delta entry

- [ ] **Step 1: Append a delta section**

Open `graphify-out/graph_delta.md` and append (replace `<DATE>` with today's ISO date, e.g. `2026-05-07`):
```markdown
## <DATE> — Strategist council (Spec 1)

Replaced the single-LlmAgent strategist at pipeline position 2 with a 3-persona council
(value/momentum/contrarian) running in parallel, plus a deterministic CouncilAggregator
that reconciles their stances into the existing StrategistDecision contract. Lightly
enriched AnalystSignal with an `evidence` blob and migrated SmartMoneySignal under the
same base.

- New nodes: MemberStance, CouncilTelemetry, CouncilAggregator, _build_persona_pool,
  _build_strategist_council, ANALYST_WEIGHTS, render_persona_prompt, COUNCIL_PROMPT_TEMPLATE,
  VALUE_LENS, MOMENTUM_LENS, CONTRARIAN_LENS, aggregate, resolve_ticker,
  build_thesis_from_proposers, confidence_weighted_avg, effective_open_quorum,
  CouncilStanceUnavailable
- New edges: pipeline.build_pipeline -> _build_strategist_council;
  CouncilAggregator -> aggregate -> resolve_ticker -> {confidence_weighted_avg, build_thesis_from_proposers, validate_lifecycle_contract};
  persona LlmAgents -> render_persona_prompt -> COUNCIL_PROMPT_TEMPLATE;
  SmartMoneySignal -> AnalystSignal (now subclasses)
- Removed: src/agents/strategist/agent.py (legacy single strategist),
  tests/unit/test_strategist_prompt_template.py, tests/unit/test_strategist_validators.py,
  STRATEGIST_INSTRUCTION constant
- Modified: AnalystSignal gained `evidence: dict[str, float|str]` field;
  pipeline.py position-2 sub_agent reference changed
```

- [ ] **Step 2: Commit**

```bash
git add graphify-out/graph_delta.md
git commit -m "docs(graphify): record strategist council changes in graph_delta"
```

---

## Phase 8 — Final verification

### Task 8.1: Full suite green + lint clean

- [ ] **Step 1: Full pytest run**

Run: `.venv/Scripts/python -m pytest tests/ -v --tb=short`
Expected: every test PASSES (Tier 2 tests SKIP without `STOCKBOT_RUN_LLM_TESTS=1`).

- [ ] **Step 2: ruff lint**

Run: `.venv/Scripts/python -m ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 3: Optional — ruff format check**

Run: `.venv/Scripts/python -m ruff format --check src/ tests/`
Expected: no formatting deltas; if there are, run `ruff format src/ tests/` and commit a separate formatting commit.

### Task 8.2: Tier 2 manual smoke (optional, recommended before merge)

- [ ] **Step 1: Run the council smoke**

```bash
set STOCKBOT_RUN_LLM_TESTS=1 && .venv/Scripts/python -m pytest tests/integration/test_council_smoke.py -v -s
```

Expected: test runs, all three personas emit stances, aggregator produces a valid `StrategistDecision`, no contract violations. Tokens used: ~3 Pro calls.

- [ ] **Step 2: Optional — run the existing 3-tick smoke_run script**

```bash
.venv/Scripts/python -m scripts.smoke_run
```

Expected: 3 ticks complete; stage 2 of each tick runs the new council instead of the old single strategist; FakeBroker accepts trades; equity changes.

### Task 8.3: Sanity-check the spec is honoured

Re-open `docs/superpowers/specs/strategist-council-design.md` and walk each section:
- [ ] Architecture diagram matches what's in `pipeline.py` and `council.py`
- [ ] All schemas described in spec exist in code (MemberStance, CouncilTelemetry, ANALYST_WEIGHTS)
- [ ] Aggregator constants match (`OPEN_QUORUM=2`, `CLOSE_QUORUM=1`, etc.)
- [ ] Persona prompts exist and contain the lens text
- [ ] Failure-mode behaviour matches degraded-mode tests
- [ ] Spec's "implementation order" matches the phases above

If any drift, fix the code (the spec is the source of truth; the user signed it off).

---

## Out-of-scope reminders (do NOT do these in this plan)

- **Round-robin debate convergence** — Spec 3
- **Persisting `council_telemetry` to a database** — Spec 3 (it stays in session state only here)
- **Evaluating `target_price` / `stop_price` as actual exit rules** — Spec 2
- **Per-evidence-key weighting** — Spec 3 (memory loop should learn it)
- **Tuning `ANALYST_WEIGHTS` defaults** — observation work, not a spec change
- **Explicit Gemini-Flash fallback on 429 per persona** — the spec mentions this, but the plan
  relies on the existing degraded-mode handling (Phase 5.6) to absorb a single persona LLM
  failure: if a persona's `*_stances` state is empty, the aggregator runs with the remaining
  personas. Adding bespoke 429-aware fallback machinery is non-trivial cross-cutting work
  with no current pattern in the repo; defer to a separate small spec if paper trading
  shows real 429 pressure.

If you find yourself writing code for any of the above, stop and confirm with the user that scope has actually expanded.

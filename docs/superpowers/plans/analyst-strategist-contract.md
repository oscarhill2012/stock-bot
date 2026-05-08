# Analyst → Strategist Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/analyst-strategist-contract-design.md`

**Goal:** Replace today's prose-heavy four-analyst-list surface to the strategist with a hybrid contract: deterministic feature vectors from code, LLM verdicts on top, collapsed by a code-only digest into one `TickerEvidence` per ticker per tick. Persist as the substrate for the future knowledge base.

**Architecture:** Pure-types + math live in a new `src/contract/` module; tunable knobs in `src/config/digest.py`; deterministic feature extractors live next to each analyst as `features.py`. Two-PR rollout: PR 1 is purely additive (new modules, no wiring). PR 2 wires it in and retires the legacy `AnalystSignal` shape — the bot isn't deployed anywhere, so no dual-write window is needed.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2 ORM, Google ADK (`LlmAgent`, `BaseAgent`, `ParallelAgent`, `SequentialAgent`), `pandas-ta` for technical indicators, pytest.

---

## File Structure

### PR 1 — additive

**Created:**
- `src/contract/__init__.py` — re-exports the contract types
- `src/contract/evidence.py` — `AnalystVerdict`, `AnalystEvidence` base + four subclasses
- `src/contract/ticker_evidence.py` — `AggregateVerdict`, `TickerEvidence`
- `src/contract/digest.py` — `build_ticker_evidence` and helpers
- `src/config/digest.py` — `DIRECTION_DEAD_ZONE`, `DEFAULT_ANALYST_WEIGHTS`
- `src/config/README.txt` — documents every config entry
- `src/agents/analysts/technical/features.py` — `extract_technical_features`
- `src/agents/analysts/fundamental/features.py` — `extract_fundamental_features`
- `src/agents/analysts/sentiment/features.py` — `extract_sentiment_features`
- `src/agents/analysts/smart_money/features.py` — `extract_smart_money_features`
- `tests/contract/__init__.py`
- `tests/contract/test_contract_types.py`
- `tests/contract/test_digest.py`
- `tests/contract/test_features_technical.py`
- `tests/contract/test_features_fundamental.py`
- `tests/contract/test_features_sentiment.py`
- `tests/contract/test_features_smart_money.py`
- `tests/fixtures/contract/<various>.json` — frozen provider-output fixtures

**Modified:**
- `requirements.txt` — add `pandas-ta`

### PR 2 — wire-in + retire legacy

**Created:**
- `src/agents/evidence/__init__.py`
- `src/agents/evidence/writer.py` — new `EvidenceWriter` BaseAgent (replaces `AttributionWriter`)
- `src/agents/evidence/builder.py` — calls `build_ticker_evidence` per ticker, stashes `state["ticker_evidence"]`
- `tests/agents/evidence/test_writer.py`
- `tests/agents/evidence/test_builder.py`

**Modified:**
- `src/agents/analysts/technical/{schema.py, agent.py, prompts.py}` — `features_callback` + `pack_callback`, `output_schema → list[AnalystVerdict]`, `output_key → technical_verdicts`, prompt receives `{technical_features}`, drops `key_factors`
- `src/agents/analysts/fundamental/{schema.py, agent.py, prompts.py}` — same shape
- `src/agents/analysts/sentiment/{schema.py, agent.py, prompts.py}` — same shape
- `src/agents/analysts/smart_money/{schema.py, agent.py, prompts.py}` — same shape
- `src/agents/analysts/_common.py` — remove `AnalystSignal` and `make_exhaustive_validator`'s ticker-keyed assumption (verdict has `ticker` field — keep validator with the same shape)
- `src/agents/strategist/prompts.py` — replace per-analyst evidence blocks + SmartMoney bias paragraph with a single `Per-Ticker Evidence` block reading `{ticker_evidence}`
- `src/orchestrator/persistence.py` — add `AnalystEvidenceRow`, `TickerEvidenceRow`, plus `save_analyst_evidence` / `save_ticker_evidence` writers; stop using `save_attribution_signal`
- `src/orchestrator/pipeline.py` — replace `build_attribution_writer(...)` step with a builder + writer pair
- `scripts/smoke_run.py` (or wherever the existing smoke run lives) — assert `state["ticker_evidence"]` populated

**Deleted in PR 2:**
- `src/agents/attribution/writer.py` (the `AttributionWriter` class) — but `attribution_signals` table stays in the schema for any historical rows
- `src/agents/analysts/_common.py:AnalystSignal` and the four legacy `<Analyst>Signal` classes (`TechnicalSignal`, `FundamentalSignal`, `SentimentSignal`, `SmartMoneySignal`)

---

## PR 1 — Contract module, config, extractors, digest (additive)

### Task 1: Define contract types

**Files:**
- Create: `src/contract/__init__.py`
- Create: `src/contract/evidence.py`
- Create: `src/contract/ticker_evidence.py`
- Test: `tests/contract/test_contract_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_contract_types.py
"""Structural tests for contract types — schema validation + invariants."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contract.evidence import (
    AnalystVerdict,
    AnalystEvidence,
    TechnicalEvidence,
    FundamentalEvidence,
    SentimentEvidence,
    SmartMoneyEvidence,
)
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def test_analyst_verdict_directions_are_constrained():
    AnalystVerdict(direction="bullish", confidence=0.7, rationale="x")
    AnalystVerdict(direction="bearish", confidence=0.7, rationale="x")
    AnalystVerdict(direction="neutral", confidence=0.0, rationale="x")
    with pytest.raises(ValidationError):
        AnalystVerdict(direction="sideways", confidence=0.7, rationale="x")


def test_analyst_verdict_confidence_bounded():
    with pytest.raises(ValidationError):
        AnalystVerdict(direction="bullish", confidence=1.5, rationale="x")
    with pytest.raises(ValidationError):
        AnalystVerdict(direction="bullish", confidence=-0.1, rationale="x")


def test_analyst_verdict_rationale_capped_at_160():
    AnalystVerdict(direction="bullish", confidence=0.5, rationale="x" * 160)
    with pytest.raises(ValidationError):
        AnalystVerdict(direction="bullish", confidence=0.5, rationale="x" * 161)


def test_analyst_verdict_is_no_data_default_false():
    v = AnalystVerdict(direction="neutral", confidence=0.0, rationale="")
    assert v.is_no_data is False


def test_technical_evidence_pins_analyst_literal():
    ev = TechnicalEvidence(
        ticker="AAPL",
        features={"rsi_14": 55.2},
        verdict=AnalystVerdict(direction="bullish", confidence=0.6, rationale="momentum"),
    )
    assert ev.analyst == "technical"


def test_each_analyst_subclass_pins_correct_literal():
    for cls, expected in [
        (TechnicalEvidence,    "technical"),
        (FundamentalEvidence,  "fundamental"),
        (SentimentEvidence,    "sentiment"),
        (SmartMoneyEvidence,   "smart_money"),
    ]:
        ev = cls(
            ticker="AAPL",
            features={},
            verdict=AnalystVerdict(direction="neutral", confidence=0.0, rationale=""),
        )
        assert ev.analyst == expected


def test_smart_money_evidence_carries_name_lists():
    ev = SmartMoneyEvidence(
        ticker="AAPL",
        features={"insider_buy_dollars": 1_000_000.0},
        verdict=AnalystVerdict(direction="bullish", confidence=0.8, rationale="CEO buy"),
        insiders=["Tim Cook"],
        politicians=[],
    )
    assert ev.insiders == ["Tim Cook"]


def test_aggregate_verdict_snapshot_includes_weights():
    av = AggregateVerdict(
        direction="bullish",
        confidence=0.6,
        weights_used={"technical": 1.0, "fundamental": 1.0, "sentiment": 1.0, "smart_money": 1.0},
    )
    assert av.weights_used["smart_money"] == 1.0


def test_ticker_evidence_disagreement_bounded():
    base_verdict = AnalystVerdict(direction="neutral", confidence=0.0, rationale="")
    per_analyst = {
        a: TechnicalEvidence(ticker="AAPL", features={}, verdict=base_verdict)
        if a == "technical"
        else FundamentalEvidence(ticker="AAPL", features={}, verdict=base_verdict)
        if a == "fundamental"
        else SentimentEvidence(ticker="AAPL", features={}, verdict=base_verdict)
        if a == "sentiment"
        else SmartMoneyEvidence(ticker="AAPL", features={}, verdict=base_verdict)
        for a in ("technical", "fundamental", "sentiment", "smart_money")
    }
    with pytest.raises(ValidationError):
        TickerEvidence(
            ticker="AAPL",
            tick_id="t1",
            recorded_at=datetime.now(timezone.utc),
            per_analyst=per_analyst,
            aggregate=AggregateVerdict(direction="neutral", confidence=0.0, weights_used={}),
            disagreement_score=1.5,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/contract/test_contract_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contract'`

- [ ] **Step 3: Create `src/contract/evidence.py`**

```python
# src/contract/evidence.py
"""Contract types for analyst evidence — pure Pydantic, no I/O."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnalystVerdict(BaseModel):
    """The LLM-judgement half of an analyst's contribution to one ticker on one tick."""
    direction:  Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale:  str   = Field(max_length=160)
    is_no_data: bool  = False


class AnalystEvidence(BaseModel):
    """Base for one analyst's contribution to one ticker on one tick."""
    ticker:   str
    analyst:  Literal["technical", "fundamental", "sentiment", "smart_money"]
    features: dict[str, float]
    verdict:  AnalystVerdict


class TechnicalEvidence(AnalystEvidence):
    """Documented features keys: rsi_14, mom_20d, dist_to_50dma_pct,
    dist_to_200dma_pct, vol_ratio_5d_vs_20d, atr_pct, beta, beta_present."""
    analyst: Literal["technical"] = "technical"


class FundamentalEvidence(AnalystEvidence):
    """Documented features keys: trailing_pe, trailing_pe_present, forward_pe,
    forward_pe_present, dividend_yield, market_cap_log, rev_growth_yoy_pct,
    gross_margin_pct, debt_to_equity, debt_to_equity_present."""
    analyst: Literal["fundamental"] = "fundamental"


class SentimentEvidence(AnalystEvidence):
    """Documented features keys: news_avg_sentiment, news_count_24h,
    social_score_delta, social_aggregate_score, headline_severity_max."""
    analyst: Literal["sentiment"] = "sentiment"
    top_headlines: list[str] = Field(default_factory=list, max_length=2)


class SmartMoneyEvidence(AnalystEvidence):
    """Documented features keys: insider_buy_dollars, insider_sell_dollars,
    n_insiders, politician_buy_dollars, politician_sell_dollars,
    n_politicians, sc13d_count, sc13g_count."""
    analyst: Literal["smart_money"] = "smart_money"
    insiders:    list[str] = Field(default_factory=list)
    politicians: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Create `src/contract/ticker_evidence.py`**

```python
# src/contract/ticker_evidence.py
"""TickerEvidence — the canonical KB primitive."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from contract.evidence import AnalystEvidence


class AggregateVerdict(BaseModel):
    direction:    Literal["bullish", "bearish", "neutral"]
    confidence:   float = Field(ge=0.0, le=1.0)
    weights_used: dict[str, float]


class TickerEvidence(BaseModel):
    """One per ticker per tick. Persisted as TickerEvidenceRow."""
    ticker:      str
    tick_id:     str
    recorded_at: datetime

    per_analyst: dict[str, AnalystEvidence]
    aggregate:   AggregateVerdict
    disagreement_score: float = Field(ge=0.0, le=1.0)
```

- [ ] **Step 5: Create `src/contract/__init__.py`**

```python
# src/contract/__init__.py
"""Pure-types contract between analysts, the digest, the strategist, and the KB.

No I/O, no LLMs, no providers. Tunable constants live in src/config/digest.py.
"""
from contract.evidence import (
    AnalystEvidence,
    AnalystVerdict,
    FundamentalEvidence,
    SentimentEvidence,
    SmartMoneyEvidence,
    TechnicalEvidence,
)
from contract.ticker_evidence import AggregateVerdict, TickerEvidence

__all__ = [
    "AnalystEvidence",
    "AnalystVerdict",
    "FundamentalEvidence",
    "SentimentEvidence",
    "SmartMoneyEvidence",
    "TechnicalEvidence",
    "AggregateVerdict",
    "TickerEvidence",
]
```

- [ ] **Step 6: Create `tests/contract/__init__.py`** (empty file).

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/contract/test_contract_types.py -v`
Expected: PASS — all 9 tests green.

- [ ] **Step 8: Commit**

```bash
git add src/contract/ tests/contract/__init__.py tests/contract/test_contract_types.py
git commit -m "feat(contract): add evidence + ticker_evidence pure types"
```

---

### Task 2: Add config knobs and document them

**Files:**
- Create: `src/config/digest.py`
- Create: `src/config/README.txt`

- [ ] **Step 1: Create `src/config/digest.py`**

```python
# src/config/digest.py
"""Tunable knobs for src/contract/digest.py.

These are hand-tuned defaults. Goal 3 (knowledge base) is expected to
learn data-driven replacements once paper trading produces enough ticks.
See src/config/README.txt for what each knob controls.
"""
from __future__ import annotations

DIRECTION_DEAD_ZONE: float = 0.15

DEFAULT_ANALYST_WEIGHTS: dict[str, float] = {
    "technical":   1.0,
    "fundamental": 1.0,
    "sentiment":   1.0,
    "smart_money": 1.0,
}
```

- [ ] **Step 2: Create `src/config/README.txt`**

```
================================================================================
src/config/ — runtime configuration
================================================================================

Tunable values that affect bot behaviour at runtime. Each entry below
documents what it is, what it controls, expected ranges, and what to look
for if you change it.

--------------------------------------------------------------------------------
watchlist.json
--------------------------------------------------------------------------------
List of tickers the bot evaluates each tick. Plain JSON array of strings.
Adding a ticker means every analyst will fetch its data and emit evidence;
the strategist will set a target weight for it. Removing a ticker stops
all of that on the next tick.

--------------------------------------------------------------------------------
digest.py
--------------------------------------------------------------------------------
Tunable knobs for the analyst -> strategist digest step
(src/contract/digest.py). All values are hand-tuned defaults. The plan is
for Goal 3 (knowledge base) to learn data-driven replacements once paper
trading has produced enough ticks.

DIRECTION_DEAD_ZONE  (float, typical range 0.0 - 0.30)
  When the weighted aggregate score for a ticker has |score| <= DEAD_ZONE,
  the aggregate direction is reported as "neutral" instead of bullish or
  bearish. Wider zone = fewer flips, more neutral calls. Narrower zone =
  more reactive but noisier. 0.0 disables the dead zone entirely.

DEFAULT_ANALYST_WEIGHTS  (dict[str, float])
  Per-analyst weight applied when aggregating signed-confidence votes
  into the headline aggregate. Keys must be the four analyst names. Equal
  weights (1.0 each) is the current default — we have no paper-trading
  data yet proving any other weighting helps. SmartMoney's quiet ticks
  abstain rather than vote, so its weight only matters on ticks where it
  actually produced a signal.
  Future: per-evidence-key weighting (backlog B5) will extend this to a
  nested {analyst: {feature_key: weight}} shape.
================================================================================
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `.venv/Scripts/python -c "from config.digest import DIRECTION_DEAD_ZONE, DEFAULT_ANALYST_WEIGHTS; print(DIRECTION_DEAD_ZONE, DEFAULT_ANALYST_WEIGHTS)"`
Expected: Prints `0.15 {'technical': 1.0, 'fundamental': 1.0, 'sentiment': 1.0, 'smart_money': 1.0}`

- [ ] **Step 4: Commit**

```bash
git add src/config/digest.py src/config/README.txt
git commit -m "feat(config): digest knobs + per-entry README"
```

---

### Task 3: Add `pandas-ta` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Add to `requirements.txt`:

```
pandas-ta>=0.3.14b
```

- [ ] **Step 2: Install it in the venv**

Run: `.venv/Scripts/python -m pip install pandas-ta`
Expected: installation succeeds. Verify with `.venv/Scripts/python -c "import pandas_ta; print(pandas_ta.__version__)"`.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: pandas-ta for technical indicators"
```

---

### Task 4: Implement digest aggregation `_aggregate`

**Files:**
- Create: `src/contract/digest.py` (initially with only `_aggregate`)
- Test: `tests/contract/test_digest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_digest.py
"""Tests for the code-only digest. Constructs AnalystEvidence directly — no LLM, no providers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contract.digest import (
    ANALYST_NAMES,
    _aggregate,
    _disagreement_score,
    _fill_missing,
    build_ticker_evidence,
)
from contract.evidence import (
    AnalystVerdict,
    FundamentalEvidence,
    SentimentEvidence,
    SmartMoneyEvidence,
    TechnicalEvidence,
)
from config.digest import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE


def _v(direction: str, conf: float, no_data: bool = False) -> AnalystVerdict:
    return AnalystVerdict(direction=direction, confidence=conf, rationale="", is_no_data=no_data)


def _evidence(analyst: str, ticker: str, verdict: AnalystVerdict):
    cls = {
        "technical":    TechnicalEvidence,
        "fundamental":  FundamentalEvidence,
        "sentiment":    SentimentEvidence,
        "smart_money":  SmartMoneyEvidence,
    }[analyst]
    return cls(ticker=ticker, features={}, verdict=verdict)


def _all(direction: str, conf: float, ticker: str = "AAPL"):
    return {a: _evidence(a, ticker, _v(direction, conf)) for a in ANALYST_NAMES}


def test_aggregate_all_bullish_high_confidence():
    per = _all("bullish", 0.9)
    agg = _aggregate(per, DEFAULT_ANALYST_WEIGHTS)
    assert agg.direction == "bullish"
    assert agg.confidence > 0.8
    assert agg.weights_used == DEFAULT_ANALYST_WEIGHTS


def test_aggregate_all_bearish_high_confidence():
    per = _all("bearish", 0.9)
    agg = _aggregate(per, DEFAULT_ANALYST_WEIGHTS)
    assert agg.direction == "bearish"
    assert agg.confidence > 0.8


def test_aggregate_split_bull_bear_yields_neutral_via_dead_zone():
    per = {
        "technical":    _evidence("technical",    "AAPL", _v("bullish", 0.7)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("bullish", 0.7)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("bearish", 0.7)),
        "smart_money":  _evidence("smart_money",  "AAPL", _v("bearish", 0.7)),
    }
    agg = _aggregate(per, DEFAULT_ANALYST_WEIGHTS)
    assert agg.direction == "neutral"


def test_aggregate_three_abstain_one_bullish_follows_lone_voter():
    per = {
        "technical":    _evidence("technical",    "AAPL", _v("neutral", 0.0, no_data=True)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("neutral", 0.0, no_data=True)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("neutral", 0.0, no_data=True)),
        "smart_money":  _evidence("smart_money",  "AAPL", _v("bullish", 0.9)),
    }
    agg = _aggregate(per, DEFAULT_ANALYST_WEIGHTS)
    assert agg.direction == "bullish"
    assert agg.confidence == pytest.approx(0.9 * 0.9, abs=1e-6)


def test_aggregate_all_abstain_returns_neutral_zero_conf():
    per = _all("neutral", 0.0)
    for a in ANALYST_NAMES:
        per[a] = _evidence(a, "AAPL", _v("neutral", 0.0, no_data=True))
    agg = _aggregate(per, DEFAULT_ANALYST_WEIGHTS)
    assert agg.direction == "neutral"
    assert agg.confidence == 0.0


def test_aggregate_dead_zone_boundary_score_just_above_yields_direction():
    # Construct evidence so that score just exceeds DIRECTION_DEAD_ZONE.
    # Two bullish at conf=0.16, two abstain → score = 0.16 > 0.15 → bullish.
    per = {
        "technical":    _evidence("technical",    "AAPL", _v("bullish", 0.16)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("bullish", 0.16)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("neutral", 0.0, no_data=True)),
        "smart_money":  _evidence("smart_money",  "AAPL", _v("neutral", 0.0, no_data=True)),
    }
    agg = _aggregate(per, DEFAULT_ANALYST_WEIGHTS)
    assert agg.direction == "bullish"


def test_aggregate_dead_zone_boundary_score_at_threshold_yields_neutral():
    # Score == DEAD_ZONE → neutral (strict >).
    per = {
        "technical":    _evidence("technical",    "AAPL", _v("bullish", DIRECTION_DEAD_ZONE)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("neutral", 0.0, no_data=True)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("neutral", 0.0, no_data=True)),
        "smart_money":  _evidence("smart_money",  "AAPL", _v("neutral", 0.0, no_data=True)),
    }
    agg = _aggregate(per, DEFAULT_ANALYST_WEIGHTS)
    assert agg.direction == "neutral"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contract.digest'`

- [ ] **Step 3: Create `src/contract/digest.py` with `_aggregate` and dependencies**

```python
# src/contract/digest.py
"""Code-only digest collapsing four analyst evidence objects into one TickerEvidence."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from config.digest import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE
from contract.evidence import (
    AnalystEvidence,
    AnalystVerdict,
    FundamentalEvidence,
    SentimentEvidence,
    SmartMoneyEvidence,
    TechnicalEvidence,
)
from contract.ticker_evidence import AggregateVerdict, TickerEvidence

ANALYST_NAMES = ("technical", "fundamental", "sentiment", "smart_money")
_DIRECTION_VALUE = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}


def _aggregate(
    per_analyst: dict[str, AnalystEvidence],
    weights: dict[str, float],
) -> AggregateVerdict:
    """Weighted sum of signed-confidence votes; abstaining analysts excluded.

    Direction is set by sign with a dead zone around 0 to avoid noise flips.
    Confidence = |normalized score| * mean(contributing confidences).
    """
    total_weight = 0.0
    weighted_sum = 0.0
    contributing_confs: list[float] = []
    for name in ANALYST_NAMES:
        ev = per_analyst[name]
        if ev.verdict.is_no_data:
            continue
        w = weights.get(name, 1.0)
        weighted_sum += w * _DIRECTION_VALUE[ev.verdict.direction] * ev.verdict.confidence
        total_weight += w
        contributing_confs.append(ev.verdict.confidence)
    if total_weight == 0.0:
        return AggregateVerdict(
            direction="neutral",
            confidence=0.0,
            weights_used=dict(weights),
        )
    score = weighted_sum / total_weight
    if score > DIRECTION_DEAD_ZONE:
        direction = "bullish"
    elif score < -DIRECTION_DEAD_ZONE:
        direction = "bearish"
    else:
        direction = "neutral"
    confidence = abs(score) * (sum(contributing_confs) / len(contributing_confs))
    return AggregateVerdict(
        direction=direction,
        confidence=min(max(confidence, 0.0), 1.0),
        weights_used=dict(weights),
    )


def _disagreement_score(per_analyst: dict[str, AnalystEvidence]) -> float:
    raise NotImplementedError  # implemented in Task 5


def _fill_missing(
    ticker: str,
    evidence_by_analyst: dict[str, AnalystEvidence],
) -> dict[str, AnalystEvidence]:
    raise NotImplementedError  # implemented in Task 6


def build_ticker_evidence(*args, **kwargs) -> TickerEvidence:
    raise NotImplementedError  # implemented in Task 7
```

- [ ] **Step 4: Run the `_aggregate` tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v -k aggregate`
Expected: 7 PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add src/contract/digest.py tests/contract/test_digest.py
git commit -m "feat(contract): _aggregate weighted-vote function with dead zone"
```

---

### Task 5: Implement `_disagreement_score`

**Files:**
- Modify: `src/contract/digest.py`
- Modify: `tests/contract/test_digest.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/contract/test_digest.py`:

```python
def test_disagreement_unanimous_yields_zero():
    per = _all("bullish", 0.9)
    assert _disagreement_score(per) == pytest.approx(0.0, abs=1e-9)


def test_disagreement_max_split_yields_one():
    # Two strong bullish + two strong bearish → variance maxes at 1.0.
    per = {
        "technical":    _evidence("technical",    "AAPL", _v("bullish", 1.0)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("bullish", 1.0)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("bearish", 1.0)),
        "smart_money":  _evidence("smart_money",  "AAPL", _v("bearish", 1.0)),
    }
    assert _disagreement_score(per) == pytest.approx(1.0, abs=1e-9)


def test_disagreement_excludes_abstainers():
    # 2 bullish + 1 bearish, 1 abstaining. Score is over the 3 contributors.
    per = {
        "technical":    _evidence("technical",    "AAPL", _v("bullish", 0.6)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("bullish", 0.6)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("bearish", 0.6)),
        "smart_money":  _evidence("smart_money",  "AAPL", _v("neutral", 0.0, no_data=True)),
    }
    score = _disagreement_score(per)
    assert 0.0 < score < 1.0


def test_disagreement_one_or_zero_contributors_returns_zero():
    per = _all("neutral", 0.0)
    for a in ANALYST_NAMES:
        per[a] = _evidence(a, "AAPL", _v("neutral", 0.0, no_data=True))
    assert _disagreement_score(per) == 0.0
    per["technical"] = _evidence("technical", "AAPL", _v("bullish", 0.7))
    assert _disagreement_score(per) == 0.0  # only one contributor
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v -k disagreement`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Replace `_disagreement_score` stub**

In `src/contract/digest.py`, replace the `_disagreement_score` stub:

```python
def _disagreement_score(per_analyst: dict[str, AnalystEvidence]) -> float:
    """Variance of signed confidences across contributing analysts, clamped to [0,1].

    0.0 = unanimous direction. 1.0 = maximally split (e.g. 2 strong bullish + 2 strong bearish).
    Abstainers (is_no_data=True) are excluded.
    """
    contributing = [ev.verdict for ev in per_analyst.values() if not ev.verdict.is_no_data]
    if len(contributing) < 2:
        return 0.0
    signed = [_DIRECTION_VALUE[v.direction] * v.confidence for v in contributing]
    mean = sum(signed) / len(signed)
    variance = sum((x - mean) ** 2 for x in signed) / len(signed)
    return min(max(variance, 0.0), 1.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v -k disagreement`
Expected: 4 PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add src/contract/digest.py tests/contract/test_digest.py
git commit -m "feat(contract): _disagreement_score function"
```

---

### Task 6: Implement `_fill_missing`

**Files:**
- Modify: `src/contract/digest.py`
- Modify: `tests/contract/test_digest.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/contract/test_digest.py`:

```python
def test_fill_missing_synthesises_smart_money_neutral_no_data():
    partial = {
        "technical":    _evidence("technical",    "AAPL", _v("bullish", 0.7)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("bullish", 0.6)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("neutral", 0.5)),
        # smart_money missing — sparse-by-design
    }
    filled = _fill_missing("AAPL", partial)
    assert "smart_money" in filled
    sm = filled["smart_money"]
    assert sm.ticker == "AAPL"
    assert sm.analyst == "smart_money"
    assert sm.verdict.is_no_data is True
    assert sm.verdict.direction == "neutral"
    assert sm.verdict.confidence == 0.0
    assert sm.features == {}


def test_fill_missing_returns_input_when_all_present():
    full = _all("bullish", 0.6)
    filled = _fill_missing("AAPL", full)
    assert all(filled[a] is full[a] for a in ANALYST_NAMES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v -k fill_missing`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Replace the `_fill_missing` stub**

In `src/contract/digest.py`, replace the stub:

```python
_ANALYST_CLS: dict[str, type[AnalystEvidence]] = {
    "technical":    TechnicalEvidence,
    "fundamental":  FundamentalEvidence,
    "sentiment":    SentimentEvidence,
    "smart_money":  SmartMoneyEvidence,
}


def _fill_missing(
    ticker: str,
    evidence_by_analyst: dict[str, AnalystEvidence],
) -> dict[str, AnalystEvidence]:
    """Fill any missing analyst with a neutral, is_no_data=True placeholder."""
    filled = dict(evidence_by_analyst)
    for name in ANALYST_NAMES:
        if name in filled:
            continue
        cls = _ANALYST_CLS[name]
        filled[name] = cls(
            ticker=ticker,
            features={},
            verdict=AnalystVerdict(
                direction="neutral",
                confidence=0.0,
                rationale="",
                is_no_data=True,
            ),
        )
    return filled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v -k fill_missing`
Expected: 2 PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add src/contract/digest.py tests/contract/test_digest.py
git commit -m "feat(contract): _fill_missing for absent analysts"
```

---

### Task 7: Implement `build_ticker_evidence` end-to-end

**Files:**
- Modify: `src/contract/digest.py`
- Modify: `tests/contract/test_digest.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/contract/test_digest.py`:

```python
def test_build_ticker_evidence_end_to_end():
    partial = {
        "technical":    _evidence("technical",    "AAPL", _v("bullish", 0.8)),
        "fundamental":  _evidence("fundamental",  "AAPL", _v("bullish", 0.7)),
        "sentiment":    _evidence("sentiment",    "AAPL", _v("bullish", 0.6)),
        # smart_money missing
    }
    now = datetime.now(timezone.utc)
    te = build_ticker_evidence(
        ticker="AAPL",
        tick_id="t-001",
        recorded_at=now,
        evidence_by_analyst=partial,
    )
    assert te.ticker == "AAPL"
    assert te.tick_id == "t-001"
    assert te.recorded_at == now
    assert set(te.per_analyst.keys()) == set(ANALYST_NAMES)
    assert te.per_analyst["smart_money"].verdict.is_no_data is True
    assert te.aggregate.direction == "bullish"
    assert 0.0 <= te.aggregate.confidence <= 1.0
    assert 0.0 <= te.disagreement_score <= 1.0
    assert te.aggregate.weights_used == DEFAULT_ANALYST_WEIGHTS


def test_build_ticker_evidence_accepts_custom_weights():
    full = _all("bullish", 0.6)
    custom = {"technical": 2.0, "fundamental": 1.0, "sentiment": 1.0, "smart_money": 1.0}
    te = build_ticker_evidence(
        ticker="AAPL",
        tick_id="t-002",
        recorded_at=datetime.now(timezone.utc),
        evidence_by_analyst=full,
        weights=custom,
    )
    assert te.aggregate.weights_used == custom
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v -k build_ticker`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Replace the `build_ticker_evidence` stub**

In `src/contract/digest.py`, replace the stub:

```python
def build_ticker_evidence(
    *,
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    evidence_by_analyst: dict[str, AnalystEvidence],
    weights: dict[str, float] | None = None,
) -> TickerEvidence:
    """Collapse 4 per-analyst contributions into one TickerEvidence."""
    weights = weights if weights is not None else DEFAULT_ANALYST_WEIGHTS
    per_analyst = _fill_missing(ticker, evidence_by_analyst)
    aggregate = _aggregate(per_analyst, weights)
    disagreement = _disagreement_score(per_analyst)
    return TickerEvidence(
        ticker=ticker,
        tick_id=tick_id,
        recorded_at=recorded_at,
        per_analyst=per_analyst,
        aggregate=aggregate,
        disagreement_score=disagreement,
    )
```

- [ ] **Step 4: Run all digest tests**

Run: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v`
Expected: All tests PASS (15 total: 7 aggregate, 4 disagreement, 2 fill_missing, 2 build_ticker_evidence).

- [ ] **Step 5: Commit**

```bash
git add src/contract/digest.py tests/contract/test_digest.py
git commit -m "feat(contract): build_ticker_evidence top-level"
```

---

### Task 8: Technical features extractor

**Files:**
- Create: `src/agents/analysts/technical/features.py`
- Create: `tests/contract/test_features_technical.py`
- Create: `tests/fixtures/contract/stockstats_aapl.json`

- [ ] **Step 1: Create the fixture**

Save the following as `tests/fixtures/contract/stockstats_aapl.json` (60 daily bars synthesised so RSI / MAs / ATR all have valid windows):

```json
{
  "ticker": "AAPL",
  "history": [
    {"timestamp": "2026-02-01T00:00:00Z", "open": 180, "high": 182, "low": 179, "close": 181, "volume": 50000000},
    {"timestamp": "2026-02-02T00:00:00Z", "open": 181, "high": 183, "low": 180, "close": 182, "volume": 51000000},
    {"timestamp": "2026-02-03T00:00:00Z", "open": 182, "high": 184, "low": 181, "close": 183, "volume": 52000000},
    {"timestamp": "2026-02-04T00:00:00Z", "open": 183, "high": 185, "low": 182, "close": 184, "volume": 53000000},
    {"timestamp": "2026-02-05T00:00:00Z", "open": 184, "high": 186, "low": 183, "close": 185, "volume": 54000000}
  ],
  "market_cap": 3000000000000.0,
  "trailing_pe": 28.5,
  "forward_pe": 26.0,
  "beta": 1.25,
  "dividend_yield": 0.005,
  "fifty_day_average": 178.0,
  "two_hundred_day_average": 170.0,
  "last_price": 185.0,
  "sector": "Technology",
  "long_name": "Apple Inc."
}
```

(NOTE: A 5-bar fixture is intentionally minimal — the test pads it programmatically. If you'd rather extend it to 60 bars by hand, indicator outputs will be more meaningful, but the assertion shape stays the same.)

- [ ] **Step 2: Write the failing test**

```python
# tests/contract/test_features_technical.py
"""Deterministic feature-extraction tests for the technical analyst."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.analysts.technical.features import extract_technical_features
from data.models.market import StockStats

FIXTURE = Path(__file__).parent.parent / "fixtures" / "contract" / "stockstats_aapl.json"


def _load_stats(synthetic_bars: int = 60) -> StockStats:
    """Load fixture and pad with synthetic uptrend bars to fill indicator windows."""
    raw = json.loads(FIXTURE.read_text())
    base_bars = raw["history"]
    last = base_bars[-1]
    # Pad with a smooth uptrend so indicators are defined.
    for i in range(synthetic_bars - len(base_bars)):
        prev = base_bars[-1]
        base_bars.append({
            "timestamp": f"2026-03-{(i % 28) + 1:02d}T00:00:00Z",
            "open":   prev["close"],
            "high":   prev["close"] + 1,
            "low":    prev["close"] - 1,
            "close":  prev["close"] + 0.5,
            "volume": prev["volume"],
        })
    raw["history"] = base_bars
    return StockStats.model_validate(raw)


def test_extract_technical_features_returns_documented_keys():
    stats = _load_stats()
    feats = extract_technical_features("AAPL", stats)
    expected_keys = {
        "rsi_14", "mom_20d", "dist_to_50dma_pct", "dist_to_200dma_pct",
        "vol_ratio_5d_vs_20d", "atr_pct", "beta", "beta_present",
    }
    assert set(feats.keys()) == expected_keys
    for k, v in feats.items():
        assert isinstance(v, float), f"{k} is {type(v)}"


def test_extract_technical_features_beta_present_flag():
    stats = _load_stats()
    feats = extract_technical_features("AAPL", stats)
    assert feats["beta_present"] == 1.0
    assert feats["beta"] == pytest.approx(1.25)

    # When beta is absent, both should be 0.0.
    stats_no_beta = stats.model_copy(update={"beta": None})
    feats2 = extract_technical_features("AAPL", stats_no_beta)
    assert feats2["beta"] == 0.0
    assert feats2["beta_present"] == 0.0


def test_extract_technical_features_dist_to_50dma_pct_sign():
    stats = _load_stats()
    feats = extract_technical_features("AAPL", stats)
    # last_price=185, 50dma=178 → positive distance.
    assert feats["dist_to_50dma_pct"] > 0


def test_extract_technical_features_deterministic():
    stats = _load_stats()
    feats1 = extract_technical_features("AAPL", stats)
    feats2 = extract_technical_features("AAPL", stats)
    assert feats1 == feats2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_technical.py -v`
Expected: FAIL — `extract_technical_features` not yet defined.

- [ ] **Step 4: Implement the extractor**

```python
# src/agents/analysts/technical/features.py
"""Deterministic feature extraction for the technical analyst.

Pure-Python; reads StockStats already populated by technical_fetch_callback.
No I/O, no LLM. Output dict keys are the documented contract on
TechnicalEvidence (see src/contract/evidence.py).
"""
from __future__ import annotations

import math

import pandas as pd
import pandas_ta as ta

from data.models.market import StockStats


def extract_technical_features(ticker: str, stats: StockStats) -> dict[str, float]:
    bars = stats.history
    if not bars:
        return _empty_features()

    df = pd.DataFrame([
        {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ])
    last_price = stats.last_price if stats.last_price is not None else float(df["close"].iloc[-1])

    rsi_series = ta.rsi(df["close"], length=14)
    rsi_14 = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.isna().all() else 50.0

    if len(df) > 20:
        mom_20d = float((df["close"].iloc[-1] / df["close"].iloc[-21] - 1.0) * 100.0)
    else:
        mom_20d = 0.0

    atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
    if atr_series is not None and not atr_series.isna().all() and last_price:
        atr_pct = float(atr_series.iloc[-1] / last_price * 100.0)
    else:
        atr_pct = 0.0

    if len(df) >= 20:
        vol_5  = float(df["volume"].iloc[-5:].mean())
        vol_20 = float(df["volume"].iloc[-20:].mean())
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0
    else:
        vol_ratio = 1.0

    return {
        "rsi_14":              rsi_14,
        "mom_20d":             mom_20d,
        "dist_to_50dma_pct":   _pct_dist(last_price, stats.fifty_day_average),
        "dist_to_200dma_pct":  _pct_dist(last_price, stats.two_hundred_day_average),
        "vol_ratio_5d_vs_20d": float(vol_ratio),
        "atr_pct":             atr_pct,
        "beta":                float(stats.beta) if stats.beta is not None else 0.0,
        "beta_present":        1.0 if stats.beta is not None else 0.0,
    }


def _pct_dist(price: float | None, ma: float | None) -> float:
    if price is None or ma is None or ma == 0:
        return 0.0
    return float((price - ma) / ma * 100.0)


def _empty_features() -> dict[str, float]:
    return {
        "rsi_14": 50.0,
        "mom_20d": 0.0,
        "dist_to_50dma_pct": 0.0,
        "dist_to_200dma_pct": 0.0,
        "vol_ratio_5d_vs_20d": 1.0,
        "atr_pct": 0.0,
        "beta": 0.0,
        "beta_present": 0.0,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_technical.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/technical/features.py tests/contract/test_features_technical.py tests/fixtures/contract/stockstats_aapl.json
git commit -m "feat(analysts/technical): deterministic feature extractor"
```

---

### Task 9: Fundamental features extractor

**Files:**
- Create: `src/agents/analysts/fundamental/features.py`
- Create: `tests/contract/test_features_fundamental.py`
- Create: `tests/fixtures/contract/filing_aapl.json`

- [ ] **Step 1: Create the fixture**

Save as `tests/fixtures/contract/filing_aapl.json`:

```json
{
  "ticker": "AAPL",
  "form_type": "10-Q",
  "filed_at": "2026-04-30T16:00:00Z",
  "accession_no": "0000320193-26-000001",
  "title": "Apple Inc. Quarterly Report",
  "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-20260331.htm",
  "risk_factors_excerpt": "Risks include supply chain disruption ...",
  "mda_excerpt": "Net sales were $XXB, an increase of 6% YoY ..."
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/contract/test_features_fundamental.py
"""Deterministic feature-extraction tests for the fundamental analyst."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.analysts.fundamental.features import extract_fundamental_features
from data.models.filings import Filing
from data.models.market import StockStats

FIXTURE_FILING = Path(__file__).parent.parent / "fixtures" / "contract" / "filing_aapl.json"
FIXTURE_STATS  = Path(__file__).parent.parent / "fixtures" / "contract" / "stockstats_aapl.json"


def _load_filing() -> Filing:
    return Filing.model_validate(json.loads(FIXTURE_FILING.read_text()))


def _load_stats() -> StockStats:
    raw = json.loads(FIXTURE_STATS.read_text())
    return StockStats.model_validate(raw)


def test_extract_fundamental_features_returns_documented_keys():
    feats = extract_fundamental_features("AAPL", filings=[_load_filing()], stats=_load_stats())
    expected = {
        "trailing_pe", "trailing_pe_present",
        "forward_pe",  "forward_pe_present",
        "dividend_yield", "market_cap_log",
        "rev_growth_yoy_pct", "gross_margin_pct",
        "debt_to_equity", "debt_to_equity_present",
    }
    assert set(feats.keys()) == expected
    for k, v in feats.items():
        assert isinstance(v, float)


def test_extract_fundamental_features_market_cap_log_for_zero_returns_zero():
    feats = extract_fundamental_features(
        "AAPL", filings=[_load_filing()],
        stats=_load_stats().model_copy(update={"market_cap": None}),
    )
    assert feats["market_cap_log"] == 0.0


def test_extract_fundamental_features_pe_presence_flags():
    feats = extract_fundamental_features("AAPL", filings=[_load_filing()], stats=_load_stats())
    assert feats["trailing_pe_present"] == 1.0
    assert feats["forward_pe_present"]  == 1.0
    no_pe = _load_stats().model_copy(update={"trailing_pe": None, "forward_pe": None})
    feats2 = extract_fundamental_features("AAPL", filings=[_load_filing()], stats=no_pe)
    assert feats2["trailing_pe_present"] == 0.0
    assert feats2["forward_pe_present"]  == 0.0
    assert feats2["trailing_pe"] == 0.0
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_fundamental.py -v`
Expected: FAIL — module/function not yet defined.

- [ ] **Step 4: Implement the extractor**

```python
# src/agents/analysts/fundamental/features.py
"""Deterministic feature extraction for the fundamental analyst.

Note: rev_growth_yoy_pct, gross_margin_pct, and debt_to_equity are not
currently present on the StockStats / Filing models. They are included
in the feature dict for forward-compatibility (KB shape stable across
provider changes) and emitted as 0.0 with the corresponding *_present
flag at 0.0 until a provider populates them.
"""
from __future__ import annotations

import math

from data.models.filings import Filing
from data.models.market import StockStats


def extract_fundamental_features(
    ticker: str,
    filings: list[Filing],
    stats: StockStats,
) -> dict[str, float]:
    trailing_pe = float(stats.trailing_pe) if stats.trailing_pe is not None else 0.0
    forward_pe  = float(stats.forward_pe)  if stats.forward_pe  is not None else 0.0
    dy          = float(stats.dividend_yield) if stats.dividend_yield is not None else 0.0
    market_cap_log = math.log10(float(stats.market_cap)) if stats.market_cap else 0.0

    return {
        "trailing_pe":           trailing_pe,
        "trailing_pe_present":   1.0 if stats.trailing_pe is not None else 0.0,
        "forward_pe":            forward_pe,
        "forward_pe_present":    1.0 if stats.forward_pe  is not None else 0.0,
        "dividend_yield":        dy,
        "market_cap_log":        market_cap_log,
        "rev_growth_yoy_pct":    0.0,
        "gross_margin_pct":      0.0,
        "debt_to_equity":        0.0,
        "debt_to_equity_present": 0.0,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_fundamental.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/fundamental/features.py tests/contract/test_features_fundamental.py tests/fixtures/contract/filing_aapl.json
git commit -m "feat(analysts/fundamental): deterministic feature extractor"
```

---

### Task 10: Sentiment features extractor

**Files:**
- Create: `src/agents/analysts/sentiment/features.py`
- Create: `tests/contract/test_features_sentiment.py`
- Create: `tests/fixtures/contract/sentiment_aapl.json`

- [ ] **Step 1: Create the fixture**

Save as `tests/fixtures/contract/sentiment_aapl.json`:

```json
{
  "news": [
    {"ticker": "AAPL", "headline": "Apple posts record quarter", "summary": "...", "url": "https://example.com/1", "source": "Reuters", "published_at": "2026-05-07T14:00:00Z", "sentiment": 0.7},
    {"ticker": "AAPL", "headline": "Antitrust probe widens", "summary": "...", "url": "https://example.com/2", "source": "WSJ", "published_at": "2026-05-08T08:00:00Z", "sentiment": -0.5}
  ],
  "social": {
    "ticker": "AAPL",
    "snapshots": [
      {"platform": "twitter", "mention_count": 10000, "positive_score": 0.6, "negative_score": 0.2, "score": 0.4},
      {"platform": "reddit",  "mention_count":  3000, "positive_score": 0.5, "negative_score": 0.3, "score": 0.2}
    ],
    "aggregate_score": 0.35
  },
  "social_score_delta": 0.1
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/contract/test_features_sentiment.py
"""Deterministic feature-extraction tests for the sentiment analyst."""
from __future__ import annotations

import json
from pathlib import Path

from agents.analysts.sentiment.features import extract_sentiment_features
from data.models.news import NewsArticle
from data.models.sentiment import SocialSentiment

FIXTURE = Path(__file__).parent.parent / "fixtures" / "contract" / "sentiment_aapl.json"


def _load():
    raw = json.loads(FIXTURE.read_text())
    news   = [NewsArticle.model_validate(n) for n in raw["news"]]
    social = SocialSentiment.model_validate(raw["social"])
    return news, social, raw["social_score_delta"]


def test_extract_sentiment_features_returns_documented_keys():
    news, social, delta = _load()
    feats = extract_sentiment_features("AAPL", news=news, social=social, social_score_delta=delta)
    expected = {
        "news_avg_sentiment", "news_count_24h",
        "social_score_delta", "social_aggregate_score",
        "headline_severity_max",
    }
    assert set(feats.keys()) == expected
    for v in feats.values():
        assert isinstance(v, float)


def test_extract_sentiment_features_news_avg():
    news, social, delta = _load()
    feats = extract_sentiment_features("AAPL", news=news, social=social, social_score_delta=delta)
    assert abs(feats["news_avg_sentiment"] - ((0.7 + -0.5) / 2)) < 1e-6
    assert feats["news_count_24h"] >= 1.0


def test_extract_sentiment_features_handles_empty_news():
    _, social, delta = _load()
    feats = extract_sentiment_features("AAPL", news=[], social=social, social_score_delta=delta)
    assert feats["news_avg_sentiment"] == 0.0
    assert feats["news_count_24h"] == 0.0
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_sentiment.py -v`
Expected: FAIL — module not defined.

- [ ] **Step 4: Implement the extractor**

```python
# src/agents/analysts/sentiment/features.py
"""Deterministic feature extraction for the sentiment analyst."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from data.models.news import NewsArticle
from data.models.sentiment import SocialSentiment

# Keyword heuristics for headline severity.
_HIGH_SEVERITY = ("crash", "fraud", "bankruptcy", "lawsuit", "investigation", "fired", "resign")
_LOW_SEVERITY  = ("beat", "record", "growth", "upgrade", "wins")


def extract_sentiment_features(
    ticker: str,
    news: list[NewsArticle],
    social: SocialSentiment | None,
    social_score_delta: float,
) -> dict[str, float]:
    sentiments = [a.sentiment for a in news if a.sentiment is not None]
    news_avg = sum(sentiments) / len(sentiments) if sentiments else 0.0

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    news_24h = float(sum(1 for a in news if a.published_at >= cutoff))

    severity = 0.0
    for a in news:
        h = a.headline.lower()
        if any(kw in h for kw in _HIGH_SEVERITY):
            severity = max(severity, 1.0)
        elif any(kw in h for kw in _LOW_SEVERITY):
            severity = max(severity, 0.5)

    social_agg = float(social.aggregate_score) if social is not None else 0.0

    return {
        "news_avg_sentiment":     float(news_avg),
        "news_count_24h":         news_24h,
        "social_score_delta":     float(social_score_delta),
        "social_aggregate_score": social_agg,
        "headline_severity_max":  severity,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_sentiment.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/sentiment/features.py tests/contract/test_features_sentiment.py tests/fixtures/contract/sentiment_aapl.json
git commit -m "feat(analysts/sentiment): deterministic feature extractor"
```

---

### Task 11: Smart-money features extractor

**Files:**
- Create: `src/agents/analysts/smart_money/features.py`
- Create: `tests/contract/test_features_smart_money.py`
- Create: `tests/fixtures/contract/smart_money_aapl.json`

- [ ] **Step 1: Create the fixture**

Save as `tests/fixtures/contract/smart_money_aapl.json`:

```json
{
  "insider_trades": [
    {"ticker": "AAPL", "insider_name": "Tim Cook", "insider_title": "CEO", "side": "buy",  "shares": 1000, "price": 180.0, "transaction_date": "2026-05-01"},
    {"ticker": "AAPL", "insider_name": "Luca Maestri", "insider_title": "CFO", "side": "sell", "shares": 500,  "price": 182.0, "transaction_date": "2026-05-03"}
  ],
  "politician_trades": [
    {"ticker": "AAPL", "politician": "Sen. X", "party": "I", "side": "buy", "value_usd": 50000.0, "transaction_date": "2026-05-02"}
  ],
  "sc13d_count": 1,
  "sc13g_count": 0
}
```

(NOTE: This fixture's exact field names depend on the existing `data.models.trades` shapes. If they differ, adjust the fixture and the test loader to match `InsiderTrade` / political-trade Pydantic models. The extractor's signature stays the same.)

- [ ] **Step 2: Write the failing test**

```python
# tests/contract/test_features_smart_money.py
"""Deterministic feature-extraction tests for the smart-money analyst."""
from __future__ import annotations

import json
from pathlib import Path

from agents.analysts.smart_money.features import extract_smart_money_features


FIXTURE = Path(__file__).parent.parent / "fixtures" / "contract" / "smart_money_aapl.json"


def _load_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_extract_smart_money_features_returns_documented_keys():
    feats = extract_smart_money_features("AAPL", _load_payload())
    expected = {
        "insider_buy_dollars", "insider_sell_dollars", "n_insiders",
        "politician_buy_dollars", "politician_sell_dollars", "n_politicians",
        "sc13d_count", "sc13g_count",
    }
    assert set(feats.keys()) == expected
    for v in feats.values():
        assert isinstance(v, float)


def test_extract_smart_money_features_aggregates_insider_dollars():
    feats = extract_smart_money_features("AAPL", _load_payload())
    assert feats["insider_buy_dollars"]  == 1000 * 180.0
    assert feats["insider_sell_dollars"] == 500  * 182.0
    assert feats["n_insiders"] == 2.0


def test_extract_smart_money_features_empty_payload_yields_zeros():
    empty = {"insider_trades": [], "politician_trades": [], "sc13d_count": 0, "sc13g_count": 0}
    feats = extract_smart_money_features("AAPL", empty)
    assert all(v == 0.0 for v in feats.values())
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_smart_money.py -v`
Expected: FAIL — module not defined.

- [ ] **Step 4: Implement the extractor**

```python
# src/agents/analysts/smart_money/features.py
"""Deterministic feature extraction for the smart-money analyst.

Reads the dict already populated by smart_money_fetch_callback. The exact
schema of insider/politician trade entries lives upstream — this extractor
treats them as plain dicts to stay decoupled from data.models.trades.
"""
from __future__ import annotations

from typing import Any


def extract_smart_money_features(
    ticker: str,
    payload: dict[str, Any],
) -> dict[str, float]:
    insider_trades = payload.get("insider_trades", []) or []
    politician_trades = payload.get("politician_trades", []) or []

    insider_buy = sum(_dollar(t) for t in insider_trades if str(t.get("side", "")).lower() == "buy")
    insider_sell = sum(_dollar(t) for t in insider_trades if str(t.get("side", "")).lower() == "sell")

    pol_buy  = sum(float(t.get("value_usd", 0.0)) for t in politician_trades if str(t.get("side", "")).lower() == "buy")
    pol_sell = sum(float(t.get("value_usd", 0.0)) for t in politician_trades if str(t.get("side", "")).lower() == "sell")

    return {
        "insider_buy_dollars":     float(insider_buy),
        "insider_sell_dollars":    float(insider_sell),
        "n_insiders":              float(len(insider_trades)),
        "politician_buy_dollars":  float(pol_buy),
        "politician_sell_dollars": float(pol_sell),
        "n_politicians":           float(len(politician_trades)),
        "sc13d_count":             float(payload.get("sc13d_count", 0)),
        "sc13g_count":             float(payload.get("sc13g_count", 0)),
    }


def _dollar(trade: dict[str, Any]) -> float:
    if "value_usd" in trade:
        return float(trade["value_usd"])
    shares = float(trade.get("shares", 0.0))
    price  = float(trade.get("price", 0.0))
    return shares * price
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/contract/test_features_smart_money.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Run all PR1 tests together**

Run: `.venv/Scripts/python -m pytest tests/contract/ -v`
Expected: All ~25 tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/smart_money/features.py tests/contract/test_features_smart_money.py tests/fixtures/contract/smart_money_aapl.json
git commit -m "feat(analysts/smart_money): deterministic feature extractor"
```

- [ ] **Step 8: Append a graphify delta entry**

Append to `graphify-out/graph_delta.md` (graphify-out is gitignored — no commit needed):

```
## 2026-05-08 — Contract module + analyst feature extractors (PR1 of B1)

PR1 of the analyst → strategist contract: pure-types contract, code-only digest,
deterministic feature extractors per analyst. No wiring yet — all additive.

- New nodes: `src/contract/{__init__.py, evidence.py, ticker_evidence.py, digest.py}`,
  `src/config/{digest.py, README.txt}`, `src/agents/analysts/<each>/features.py`
- New edges: `src/contract/digest.py` → `src/config/digest.py` (constants)
- Deps: `pandas-ta` added to requirements.txt
```

---

> **End of PR 1.** All code in PR 1 is purely additive — nothing in the running pipeline references it yet. PR 2 wires it in and retires the legacy shape.

---

## PR 2 — Wire-in + retire legacy

### Task 12: Persistence rows for `AnalystEvidenceRow` and `TickerEvidenceRow`

**Files:**
- Modify: `src/orchestrator/persistence.py`
- Create: `tests/orchestrator/test_persistence_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator/test_persistence_evidence.py
"""Persistence tests for the new evidence rows."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from orchestrator.persistence import (
    AnalystEvidenceRow,
    TickerEvidenceRow,
    make_engine,
    save_analyst_evidence,
    save_ticker_evidence,
)
from contract.digest import build_ticker_evidence
from contract.evidence import AnalystVerdict, TechnicalEvidence


def test_save_analyst_evidence_round_trip(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    with Session(engine) as session:
        ev = TechnicalEvidence(
            ticker="AAPL",
            features={"rsi_14": 55.2, "mom_20d": 3.0},
            verdict=AnalystVerdict(direction="bullish", confidence=0.7, rationale="up"),
        )
        save_analyst_evidence(session, tick_id="t-001", evidence=ev)
        session.commit()
        row = session.query(AnalystEvidenceRow).one()
        assert row.tick_id == "t-001"
        assert row.analyst == "technical"
        assert row.ticker  == "AAPL"
        assert row.direction == "bullish"
        assert row.confidence == 0.7
        assert json.loads(row.features_json) == {"rsi_14": 55.2, "mom_20d": 3.0}


def test_save_ticker_evidence_round_trip(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    with Session(engine) as session:
        partial = {
            "technical": TechnicalEvidence(
                ticker="AAPL",
                features={"rsi_14": 55.2},
                verdict=AnalystVerdict(direction="bullish", confidence=0.8, rationale="up"),
            ),
        }
        te = build_ticker_evidence(
            ticker="AAPL",
            tick_id="t-001",
            recorded_at=datetime.now(timezone.utc),
            evidence_by_analyst=partial,
        )
        save_ticker_evidence(session, evidence=te)
        session.commit()
        row = session.query(TickerEvidenceRow).one()
        assert row.tick_id == "t-001"
        assert row.ticker  == "AAPL"
        assert row.aggregate_direction in ("bullish", "neutral")
        assert json.loads(row.weights_used_json)["technical"] == 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/orchestrator/test_persistence_evidence.py -v`
Expected: FAIL — `AnalystEvidenceRow` / `TickerEvidenceRow` / `save_*` not defined.

- [ ] **Step 3: Append the new tables and writers to `src/orchestrator/persistence.py`**

Add to `src/orchestrator/persistence.py` (do not remove `AttributionSignalsRow` yet — it's removed in Task 18):

```python
import json
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

# Existing imports / Base / engine factory above stay unchanged.


class AnalystEvidenceRow(Base):
    """One row per (analyst, ticker, tick). Carries structured evidence + verdict."""
    __tablename__ = "analyst_evidence"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id:     Mapped[str]      = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    analyst:     Mapped[str]      = mapped_column(String, index=True)
    ticker:      Mapped[str]      = mapped_column(String, index=True)

    direction:  Mapped[str]   = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    rationale:  Mapped[str]   = mapped_column(String, default="")
    is_no_data: Mapped[bool]  = mapped_column(Boolean, default=False)

    features_json: Mapped[str]        = mapped_column(String, default="{}")
    extras_json:   Mapped[str | None] = mapped_column(String, nullable=True)


class TickerEvidenceRow(Base):
    """One row per (ticker, tick). The KB lookup primitive."""
    __tablename__ = "ticker_evidence"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id:     Mapped[str]      = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    ticker:      Mapped[str]      = mapped_column(String, index=True)

    aggregate_direction:  Mapped[str]   = mapped_column(String)
    aggregate_confidence: Mapped[float] = mapped_column(Float)
    disagreement_score:   Mapped[float] = mapped_column(Float)
    weights_used_json:    Mapped[str]   = mapped_column(String, default="{}")

    __table_args__ = (Index("ix_ticker_evidence_ticker_tick", "ticker", "tick_id"),)


def save_analyst_evidence(session: Session, *, tick_id: str, evidence) -> None:
    from datetime import timezone
    extras: dict | None = None
    if hasattr(evidence, "top_headlines") and evidence.top_headlines:
        extras = {"top_headlines": list(evidence.top_headlines)}
    if hasattr(evidence, "insiders"):
        extras = (extras or {}) | {
            "insiders":    list(getattr(evidence, "insiders", []) or []),
            "politicians": list(getattr(evidence, "politicians", []) or []),
        }
    row = AnalystEvidenceRow(
        tick_id=tick_id,
        recorded_at=datetime.now(tz=timezone.utc),
        analyst=evidence.analyst,
        ticker=evidence.ticker,
        direction=evidence.verdict.direction,
        confidence=evidence.verdict.confidence,
        rationale=evidence.verdict.rationale,
        is_no_data=evidence.verdict.is_no_data,
        features_json=json.dumps(evidence.features),
        extras_json=json.dumps(extras) if extras else None,
    )
    session.add(row)


def save_ticker_evidence(session: Session, *, evidence) -> None:
    row = TickerEvidenceRow(
        tick_id=evidence.tick_id,
        recorded_at=evidence.recorded_at,
        ticker=evidence.ticker,
        aggregate_direction=evidence.aggregate.direction,
        aggregate_confidence=evidence.aggregate.confidence,
        disagreement_score=evidence.disagreement_score,
        weights_used_json=json.dumps(evidence.aggregate.weights_used),
    )
    session.add(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/orchestrator/test_persistence_evidence.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/persistence.py tests/orchestrator/test_persistence_evidence.py
git commit -m "feat(persistence): AnalystEvidenceRow + TickerEvidenceRow + writers"
```

---

### Task 13: New `EvidenceBuilder` and `EvidenceWriter` agents

**Files:**
- Create: `src/agents/evidence/__init__.py`
- Create: `src/agents/evidence/builder.py`
- Create: `src/agents/evidence/writer.py`
- Create: `tests/agents/evidence/test_builder.py`
- Create: `tests/agents/evidence/test_writer.py`

- [ ] **Step 1: Write the failing builder test**

```python
# tests/agents/evidence/test_builder.py
"""Tests for the EvidenceBuilder ADK agent."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.evidence.builder import EvidenceBuilder
from contract.evidence import AnalystVerdict, TechnicalEvidence, FundamentalEvidence


@pytest.mark.asyncio
async def test_evidence_builder_collapses_per_analyst_evidence_into_ticker_evidence():
    state: dict = {
        "tickers": ["AAPL", "MSFT"],
        "tick_id": "t-001",
        "technical_evidence": [
            TechnicalEvidence(
                ticker="AAPL", features={"rsi_14": 55.0},
                verdict=AnalystVerdict(direction="bullish", confidence=0.7, rationale=""),
            ).model_dump(),
            TechnicalEvidence(
                ticker="MSFT", features={"rsi_14": 45.0},
                verdict=AnalystVerdict(direction="neutral", confidence=0.3, rationale=""),
            ).model_dump(),
        ],
        "fundamental_evidence": [
            FundamentalEvidence(
                ticker="AAPL", features={"trailing_pe": 28.0},
                verdict=AnalystVerdict(direction="bullish", confidence=0.6, rationale=""),
            ).model_dump(),
            FundamentalEvidence(
                ticker="MSFT", features={"trailing_pe": 32.0},
                verdict=AnalystVerdict(direction="bearish", confidence=0.5, rationale=""),
            ).model_dump(),
        ],
        # sentiment_evidence + smart_money_evidence missing — gets neutral-fill.
    }

    ctx = SimpleNamespace(session=SimpleNamespace(state=state))
    builder = EvidenceBuilder()
    async for _ in builder._run_async_impl(ctx):
        pass

    assert "ticker_evidence" in state
    assert len(state["ticker_evidence"]) == 2
    by_ticker = {te["ticker"]: te for te in state["ticker_evidence"]}
    assert set(by_ticker) == {"AAPL", "MSFT"}
    assert set(by_ticker["AAPL"]["per_analyst"].keys()) == {
        "technical", "fundamental", "sentiment", "smart_money"
    }
```

- [ ] **Step 2: Write the failing writer test**

```python
# tests/agents/evidence/test_writer.py
"""Tests for the EvidenceWriter ADK agent."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from agents.evidence.writer import EvidenceWriter
from contract.digest import build_ticker_evidence
from contract.evidence import AnalystVerdict, TechnicalEvidence
from orchestrator.persistence import AnalystEvidenceRow, TickerEvidenceRow, make_engine


@pytest.mark.asyncio
async def test_evidence_writer_persists_per_analyst_and_per_ticker(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    with Session(engine) as session:
        ev = TechnicalEvidence(
            ticker="AAPL", features={"rsi_14": 60.0},
            verdict=AnalystVerdict(direction="bullish", confidence=0.8, rationale="x"),
        )
        te = build_ticker_evidence(
            ticker="AAPL", tick_id="t-001",
            recorded_at=datetime.now(timezone.utc),
            evidence_by_analyst={"technical": ev},
        )
        state = {
            "tick_id": "t-001",
            "technical_evidence":   [ev.model_dump()],
            "fundamental_evidence": [],
            "sentiment_evidence":   [],
            "smart_money_evidence": [],
            "ticker_evidence":      [te.model_dump()],
        }
        ctx = SimpleNamespace(session=SimpleNamespace(state=state))
        writer = EvidenceWriter(db_session=session)
        async for _ in writer._run_async_impl(ctx):
            pass
        # 1 AnalystEvidenceRow (technical AAPL) + 1 TickerEvidenceRow.
        assert session.query(AnalystEvidenceRow).count() == 1
        assert session.query(TickerEvidenceRow).count() == 1
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/agents/evidence/ -v`
Expected: FAIL — modules / classes not defined.

- [ ] **Step 4: Create `src/agents/evidence/builder.py`**

```python
# src/agents/evidence/builder.py
"""ADK agent that collapses per-analyst evidence into TickerEvidence per ticker."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from contract.digest import ANALYST_NAMES, build_ticker_evidence
from contract.evidence import (
    AnalystEvidence,
    FundamentalEvidence,
    SentimentEvidence,
    SmartMoneyEvidence,
    TechnicalEvidence,
)

_ANALYST_CLS: dict[str, type[AnalystEvidence]] = {
    "technical":   TechnicalEvidence,
    "fundamental": FundamentalEvidence,
    "sentiment":   SentimentEvidence,
    "smart_money": SmartMoneyEvidence,
}


class EvidenceBuilder(BaseAgent):
    name: str = "EvidenceBuilder"
    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str       = state.get("tick_id", "unknown")
        now = datetime.now(tz=timezone.utc)

        # Group per-analyst evidence by ticker.
        by_ticker_then_analyst: dict[str, dict[str, AnalystEvidence]] = {t: {} for t in tickers}
        for analyst in ANALYST_NAMES:
            cls = _ANALYST_CLS[analyst]
            for raw in state.get(f"{analyst}_evidence", []) or []:
                ev = cls.model_validate(raw) if isinstance(raw, dict) else raw
                by_ticker_then_analyst.setdefault(ev.ticker, {})[analyst] = ev

        ticker_evidence = []
        for ticker in tickers:
            te = build_ticker_evidence(
                ticker=ticker,
                tick_id=tick_id,
                recorded_at=now,
                evidence_by_analyst=by_ticker_then_analyst.get(ticker, {}),
            )
            ticker_evidence.append(te.model_dump(mode="json"))
        state["ticker_evidence"] = ticker_evidence
        return
        yield  # required to make this a generator
```

- [ ] **Step 5: Create `src/agents/evidence/writer.py`**

```python
# src/agents/evidence/writer.py
"""ADK agent that persists analyst evidence and ticker evidence to the DB."""
from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from contract.digest import ANALYST_NAMES
from contract.evidence import (
    AnalystEvidence,
    FundamentalEvidence,
    SentimentEvidence,
    SmartMoneyEvidence,
    TechnicalEvidence,
)
from contract.ticker_evidence import TickerEvidence

_ANALYST_CLS = {
    "technical":   TechnicalEvidence,
    "fundamental": FundamentalEvidence,
    "sentiment":   SentimentEvidence,
    "smart_money": SmartMoneyEvidence,
}


class EvidenceWriter(BaseAgent):
    name: str = "EvidenceWriter"
    db_session: Any = None
    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if self.db_session is None:
            return
            yield  # pragma: no cover
        from orchestrator.persistence import save_analyst_evidence, save_ticker_evidence

        state = ctx.session.state
        tick_id: str = state.get("tick_id", "unknown")

        for analyst in ANALYST_NAMES:
            cls = _ANALYST_CLS[analyst]
            for raw in state.get(f"{analyst}_evidence", []) or []:
                ev = cls.model_validate(raw) if isinstance(raw, dict) else raw
                save_analyst_evidence(self.db_session, tick_id=tick_id, evidence=ev)

        for raw in state.get("ticker_evidence", []) or []:
            te = TickerEvidence.model_validate(raw) if isinstance(raw, dict) else raw
            save_ticker_evidence(self.db_session, evidence=te)

        self.db_session.commit()
        return
        yield
```

- [ ] **Step 6: Create `src/agents/evidence/__init__.py`**

```python
from .builder import EvidenceBuilder
from .writer import EvidenceWriter

__all__ = ["EvidenceBuilder", "EvidenceWriter"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/agents/evidence/ -v`
Expected: 2 PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agents/evidence/ tests/agents/evidence/
git commit -m "feat(agents): EvidenceBuilder + EvidenceWriter"
```

---

### Task 14: Wire technical analyst — features_callback + pack_callback + new output

**Files:**
- Modify: `src/agents/analysts/technical/agent.py`
- Modify: `src/agents/analysts/technical/schema.py`
- Modify: `src/agents/analysts/technical/prompts.py`

- [ ] **Step 1: Replace `src/agents/analysts/technical/schema.py`**

```python
"""Technical analyst output schema — re-exports contract types."""
from __future__ import annotations

from contract.evidence import AnalystVerdict, TechnicalEvidence

__all__ = ["AnalystVerdict", "TechnicalEvidence"]
```

- [ ] **Step 2: Replace `src/agents/analysts/technical/prompts.py`**

```python
TECHNICAL_INSTRUCTION = """
You are a technical analyst. You receive raw OHLCV market data PLUS a pre-computed
deterministic feature vector for each ticker. Your job is to interpret them and
emit a verdict.

For EACH ticker in the watchlist, output one AnalystVerdict object.
You MUST emit a verdict for ALL watchlist tickers.

Each AnalystVerdict:
- ticker: string                                # set this to the ticker
- direction: "bullish" | "bearish" | "neutral"
- confidence: float 0.0-1.0
- rationale: short string ≤160 chars (1-2 sentences); cite the features that drove your call

The features dict is the deterministic ground truth — do not re-derive RSI / momentum
yourself; trust the values you are given. Use the raw data only for context the
features can't capture.

Pre-computed features per ticker: {technical_features}
Raw data per ticker: {technical_data}
Watchlist: {tickers}
"""
```

- [ ] **Step 3: Replace `src/agents/analysts/technical/agent.py`**

```python
"""Technical analyst LlmAgent + features and packing callbacks."""
from __future__ import annotations

from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.analysts._common import make_exhaustive_verdict_validator
from contract.evidence import AnalystVerdict, TechnicalEvidence
from .features import extract_technical_features
from .fetch import technical_fetch_callback
from .prompts import TECHNICAL_INSTRUCTION


def technical_features_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Compute deterministic features per ticker and stash them in state."""
    state = callback_context.state
    raw_data = state.get("technical_data", {}) or {}
    from data.models.market import StockStats
    feats = {}
    for ticker, raw in raw_data.items():
        stats = raw if isinstance(raw, StockStats) else StockStats.model_validate(raw)
        feats[ticker] = extract_technical_features(ticker, stats)
    state["technical_features"] = feats
    return None


def technical_pack_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Combine LLM verdicts + features into TechnicalEvidence per ticker."""
    state = callback_context.state
    verdicts = state.get("technical_verdicts", []) or []
    features_by_ticker = state.get("technical_features", {}) or {}
    evidence = []
    for v_raw in verdicts:
        v = AnalystVerdict.model_validate(v_raw) if isinstance(v_raw, dict) else v_raw
        ticker = v_raw["ticker"] if isinstance(v_raw, dict) else getattr(v_raw, "ticker", None)
        if ticker is None:
            continue
        ev = TechnicalEvidence(
            ticker=ticker,
            features=features_by_ticker.get(ticker, {}),
            verdict=v,
        )
        evidence.append(ev.model_dump(mode="json"))
    state["technical_evidence"] = evidence
    return None


# The LLM emits a list of {ticker, direction, confidence, rationale, is_no_data} objects.
# We attach a 'ticker' field to AnalystVerdict via a thin wrapper schema for output.
class _TechnicalVerdictItem(AnalystVerdict):
    """LLM output item — adds the ticker the verdict refers to."""
    ticker: str


def _build_technical_analyst() -> LlmAgent:
    return LlmAgent(
        name="TechnicalAnalyst",
        model="gemini-2.0-flash-001",
        instruction=TECHNICAL_INSTRUCTION,
        output_schema=list[_TechnicalVerdictItem],
        output_key="technical_verdicts",
        before_agent_callback=[technical_fetch_callback, technical_features_callback],
        after_agent_callback=[
            make_exhaustive_verdict_validator("technical_verdicts"),
            technical_pack_callback,
        ],
    )


technical_analyst = _build_technical_analyst()
```

- [ ] **Step 4: Update `src/agents/analysts/_common.py`**

Replace the file with:

```python
"""Shared analyst base callback utilities (post-contract)."""
from __future__ import annotations

from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types


def make_exhaustive_verdict_validator(verdicts_key: str, tickers_key: str = "tickers"):
    """Re-prompt if any watchlist ticker is missing from the emitted verdict list."""
    def _validator(callback_context: CallbackContext) -> Optional[genai_types.Content]:
        state = callback_context.state
        verdicts = state.get(verdicts_key, []) or []
        tickers  = state.get(tickers_key, []) or []
        if not tickers:
            return None
        emitted = {(v["ticker"] if isinstance(v, dict) else getattr(v, "ticker", None)) for v in verdicts}
        missing = [t for t in tickers if t not in emitted]
        if missing:
            return genai_types.Content(
                parts=[genai_types.Part(
                    text=f"You missed these tickers: {missing}. Emit a verdict for every watchlist ticker.",
                )],
                role="user",
            )
        return None
    return _validator
```

- [ ] **Step 5: Run a quick smoke test**

Run: `.venv/Scripts/python -m pytest tests/contract/ tests/agents/evidence/ -v`
Expected: All previous tests still pass. (No tests added in this task — the analyst LLM is exercised by the smoke run in Task 19.)

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/technical/ src/agents/analysts/_common.py
git commit -m "feat(analysts/technical): wire features + pack callbacks; emit verdicts"
```

---

### Task 15: Wire fundamental analyst

**Files:**
- Modify: `src/agents/analysts/fundamental/{schema.py, prompts.py, agent.py}`

- [ ] **Step 1: Replace `src/agents/analysts/fundamental/schema.py`**

```python
"""Fundamental analyst output schema — re-exports contract types."""
from __future__ import annotations

from contract.evidence import AnalystVerdict, FundamentalEvidence

__all__ = ["AnalystVerdict", "FundamentalEvidence"]
```

- [ ] **Step 2: Replace `src/agents/analysts/fundamental/prompts.py`**

```python
FUNDAMENTAL_INSTRUCTION = """
You are a fundamental analyst. You receive SEC filings (10-K, 10-Q, 8-K) PLUS a
pre-computed deterministic feature vector per ticker (P/E, market cap, etc.).

For EACH ticker, output one AnalystVerdict object. You MUST emit a verdict
for ALL watchlist tickers.

Each AnalystVerdict:
- ticker: string
- direction: "bullish" | "bearish" | "neutral"
- confidence: float 0.0-1.0
- rationale: ≤160 chars; cite the features and any specific filings that drove your call

Trust the features — do not re-derive numerics. Use the filings text for context
the features can't capture (Item 1A risks, MD&A commentary).

Pre-computed features per ticker: {fundamental_features}
Filings per ticker: {fundamental_data}
Watchlist: {tickers}
"""
```

- [ ] **Step 3: Replace `src/agents/analysts/fundamental/agent.py`**

```python
"""Fundamental analyst LlmAgent + features and packing callbacks."""
from __future__ import annotations

from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.analysts._common import make_exhaustive_verdict_validator
from contract.evidence import AnalystVerdict, FundamentalEvidence
from .features import extract_fundamental_features
from .fetch import fundamental_fetch_callback
from .prompts import FUNDAMENTAL_INSTRUCTION


def fundamental_features_callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
    state = callback_context.state
    raw_data = state.get("fundamental_data", {}) or {}
    stats_by_ticker = state.get("technical_data", {}) or {}  # share StockStats already fetched

    from data.models.filings import Filing
    from data.models.market import StockStats

    feats = {}
    for ticker, raw_filings in raw_data.items():
        filings = [Filing.model_validate(f) if isinstance(f, dict) else f for f in (raw_filings or [])]
        stats_raw = stats_by_ticker.get(ticker)
        stats = stats_raw if isinstance(stats_raw, StockStats) else StockStats.model_validate(stats_raw) if stats_raw else None
        feats[ticker] = extract_fundamental_features(ticker, filings=filings, stats=stats) if stats else {}
    state["fundamental_features"] = feats
    return None


def fundamental_pack_callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
    state = callback_context.state
    verdicts = state.get("fundamental_verdicts", []) or []
    feats = state.get("fundamental_features", {}) or {}
    evidence = []
    for v_raw in verdicts:
        v = AnalystVerdict.model_validate(v_raw) if isinstance(v_raw, dict) else v_raw
        ticker = v_raw["ticker"] if isinstance(v_raw, dict) else getattr(v_raw, "ticker", None)
        if ticker is None:
            continue
        ev = FundamentalEvidence(
            ticker=ticker,
            features=feats.get(ticker, {}),
            verdict=v,
        )
        evidence.append(ev.model_dump(mode="json"))
    state["fundamental_evidence"] = evidence
    return None


class _FundamentalVerdictItem(AnalystVerdict):
    ticker: str


def _build_fundamental_analyst() -> LlmAgent:
    return LlmAgent(
        name="FundamentalAnalyst",
        model="gemini-2.0-flash-001",
        instruction=FUNDAMENTAL_INSTRUCTION,
        output_schema=list[_FundamentalVerdictItem],
        output_key="fundamental_verdicts",
        before_agent_callback=[fundamental_fetch_callback, fundamental_features_callback],
        after_agent_callback=[
            make_exhaustive_verdict_validator("fundamental_verdicts"),
            fundamental_pack_callback,
        ],
    )


fundamental_analyst = _build_fundamental_analyst()
```

- [ ] **Step 4: Verify all existing tests still pass**

Run: `.venv/Scripts/python -m pytest tests/contract/ tests/agents/evidence/ tests/orchestrator/ -v`
Expected: all pre-existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/
git commit -m "feat(analysts/fundamental): wire features + pack callbacks; emit verdicts"
```

---

### Task 16: Wire sentiment analyst

**Files:**
- Modify: `src/agents/analysts/sentiment/{schema.py, prompts.py, agent.py}`

- [ ] **Step 1: Replace `src/agents/analysts/sentiment/schema.py`**

```python
"""Sentiment analyst output schema — re-exports contract types."""
from __future__ import annotations

from contract.evidence import AnalystVerdict, SentimentEvidence

__all__ = ["AnalystVerdict", "SentimentEvidence"]
```

- [ ] **Step 2: Replace `src/agents/analysts/sentiment/prompts.py`**

```python
SENTIMENT_INSTRUCTION = """
You are a sentiment analyst. You receive news articles and social-sentiment
snapshots PLUS a pre-computed deterministic feature vector per ticker
(news avg sentiment, social score delta, headline severity, etc.).

For EACH ticker, output one AnalystVerdict object. You MUST emit a verdict
for ALL watchlist tickers.

Each AnalystVerdict:
- ticker: string
- direction: "bullish" | "bearish" | "neutral"
- confidence: float 0.0-1.0
- rationale: ≤160 chars; cite the features or specific headlines.

Trust the features — do not re-derive numerics. Use the raw text for nuance
the features can't capture.

Pre-computed features per ticker: {sentiment_features}
News + social per ticker: {sentiment_data}
Watchlist: {tickers}
"""
```

- [ ] **Step 3: Replace `src/agents/analysts/sentiment/agent.py`**

```python
"""Sentiment analyst LlmAgent + features and packing callbacks."""
from __future__ import annotations

from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.analysts._common import make_exhaustive_verdict_validator
from contract.evidence import AnalystVerdict, SentimentEvidence
from .features import extract_sentiment_features
from .fetch import sentiment_fetch_callback
from .prompts import SENTIMENT_INSTRUCTION


def sentiment_features_callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
    state = callback_context.state
    raw_data = state.get("sentiment_data", {}) or {}
    from data.models.news import NewsArticle
    from data.models.sentiment import SocialSentiment

    feats = {}
    for ticker, payload in raw_data.items():
        news_raw = (payload or {}).get("news", []) or []
        social_raw = (payload or {}).get("social")
        delta = float((payload or {}).get("social_score_delta", 0.0))
        news = [NewsArticle.model_validate(n) if isinstance(n, dict) else n for n in news_raw]
        social = SocialSentiment.model_validate(social_raw) if isinstance(social_raw, dict) else social_raw
        feats[ticker] = extract_sentiment_features(ticker, news=news, social=social, social_score_delta=delta)
    state["sentiment_features"] = feats
    return None


def sentiment_pack_callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
    state = callback_context.state
    verdicts = state.get("sentiment_verdicts", []) or []
    feats = state.get("sentiment_features", {}) or {}
    evidence = []
    for v_raw in verdicts:
        v = AnalystVerdict.model_validate(v_raw) if isinstance(v_raw, dict) else v_raw
        ticker = v_raw["ticker"] if isinstance(v_raw, dict) else getattr(v_raw, "ticker", None)
        if ticker is None:
            continue
        ev = SentimentEvidence(
            ticker=ticker,
            features=feats.get(ticker, {}),
            verdict=v,
        )
        evidence.append(ev.model_dump(mode="json"))
    state["sentiment_evidence"] = evidence
    return None


class _SentimentVerdictItem(AnalystVerdict):
    ticker: str


def _build_sentiment_analyst() -> LlmAgent:
    return LlmAgent(
        name="SentimentAnalyst",
        model="gemini-2.0-flash-001",
        instruction=SENTIMENT_INSTRUCTION,
        output_schema=list[_SentimentVerdictItem],
        output_key="sentiment_verdicts",
        before_agent_callback=[sentiment_fetch_callback, sentiment_features_callback],
        after_agent_callback=[
            make_exhaustive_verdict_validator("sentiment_verdicts"),
            sentiment_pack_callback,
        ],
    )


sentiment_analyst = _build_sentiment_analyst()
```

- [ ] **Step 4: Verify tests still pass**

Run: `.venv/Scripts/python -m pytest tests/contract/ tests/agents/evidence/ tests/orchestrator/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/sentiment/
git commit -m "feat(analysts/sentiment): wire features + pack callbacks; emit verdicts"
```

---

### Task 17: Wire smart-money analyst

**Files:**
- Modify: `src/agents/analysts/smart_money/{schema.py, prompts.py, agent.py}`

- [ ] **Step 1: Replace `src/agents/analysts/smart_money/schema.py`**

```python
"""Smart-money analyst output schema — re-exports contract types."""
from __future__ import annotations

from contract.evidence import AnalystVerdict, SmartMoneyEvidence

__all__ = ["AnalystVerdict", "SmartMoneyEvidence"]
```

- [ ] **Step 2: Replace `src/agents/analysts/smart_money/prompts.py`**

```python
SMART_MONEY_INSTRUCTION = """
You are a smart-money analyst. You receive insider trades, politician trades,
and notable holder filings (SC 13D/13G) PLUS a pre-computed deterministic
feature vector per ticker (insider $, n_insiders, n_politicians, sc13d_count, etc.).

This is a SPARSE signal. For tickers in the watchlist with NO smart-money activity,
emit a verdict with direction="neutral", confidence=0.0, is_no_data=true, and a
brief rationale like "no activity detected". Do not omit them.

For each ticker WITH activity, output one AnalystVerdict:
- ticker: string
- direction: "bullish" | "bearish" | "neutral"
- confidence: float 0.0-1.0  (use 0.5+ for "low conviction", 0.8+ for "high")
- rationale: ≤160 chars; name the people / activity that drove the call.

Trust the features — do not re-derive dollar totals. Use the raw entries for
attribution (who is buying/selling).

Pre-computed features per ticker: {smart_money_features}
Raw activity per ticker: {smart_money_data}
Watchlist: {tickers}
"""
```

- [ ] **Step 3: Replace `src/agents/analysts/smart_money/agent.py`**

```python
"""Smart-money analyst LlmAgent + features and packing callbacks."""
from __future__ import annotations

from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.analysts._common import make_exhaustive_verdict_validator
from contract.evidence import AnalystVerdict, SmartMoneyEvidence
from .features import extract_smart_money_features
from .fetch import smart_money_fetch_callback
from .prompts import SMART_MONEY_INSTRUCTION


def smart_money_features_callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
    state = callback_context.state
    raw_data = state.get("smart_money_data", {}) or {}
    feats = {}
    for ticker, payload in raw_data.items():
        feats[ticker] = extract_smart_money_features(ticker, payload or {})
    state["smart_money_features"] = feats
    return None


def smart_money_pack_callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
    state = callback_context.state
    verdicts = state.get("smart_money_verdicts", []) or []
    feats = state.get("smart_money_features", {}) or {}
    evidence = []
    for v_raw in verdicts:
        v = AnalystVerdict.model_validate(v_raw) if isinstance(v_raw, dict) else v_raw
        ticker = v_raw["ticker"] if isinstance(v_raw, dict) else getattr(v_raw, "ticker", None)
        if ticker is None:
            continue
        ev = SmartMoneyEvidence(
            ticker=ticker,
            features=feats.get(ticker, {}),
            verdict=v,
        )
        evidence.append(ev.model_dump(mode="json"))
    state["smart_money_evidence"] = evidence
    return None


class _SmartMoneyVerdictItem(AnalystVerdict):
    ticker: str


def _build_smart_money_analyst() -> LlmAgent:
    return LlmAgent(
        name="SmartMoneyAnalyst",
        model="gemini-2.0-flash-001",
        instruction=SMART_MONEY_INSTRUCTION,
        output_schema=list[_SmartMoneyVerdictItem],
        output_key="smart_money_verdicts",
        before_agent_callback=[smart_money_fetch_callback, smart_money_features_callback],
        after_agent_callback=[
            make_exhaustive_verdict_validator("smart_money_verdicts"),
            smart_money_pack_callback,
        ],
    )


smart_money_analyst = _build_smart_money_analyst()
```

- [ ] **Step 4: Verify tests still pass**

Run: `.venv/Scripts/python -m pytest tests/contract/ tests/agents/evidence/ tests/orchestrator/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/smart_money/
git commit -m "feat(analysts/smart_money): wire features + pack callbacks; emit verdicts"
```

---

### Task 18: Update strategist prompt + wire EvidenceBuilder/Writer into pipeline + delete legacy

**Files:**
- Modify: `src/agents/strategist/prompts.py`
- Modify: `src/orchestrator/pipeline.py`
- Delete: `src/agents/attribution/writer.py` (and remove the import in pipeline)

- [ ] **Step 1: Replace `src/agents/strategist/prompts.py`**

```python
"""Strategist prompt template — consumes ticker_evidence."""

STRATEGIST_INSTRUCTION = """
You are the portfolio strategist for an algorithmic trading bot. You integrate
per-ticker evidence into target portfolio weights for the next trading hour.

## Current State
Portfolio: {portfolio}
Active Positions: {positions}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest: {day_digest}
Current Thesis: {thesis}

## Per-Ticker Evidence
For each watchlist ticker, you receive a TickerEvidence with:
- aggregate.direction + aggregate.confidence (weighted across analysts)
- disagreement_score (0 = unanimous, 1 = split)
- per_analyst breakdown: each analyst's direction, confidence, and 1-2 sentence rationale
- per_analyst.*.features: deterministic numerics (RSI, P/E, sentiment delta, insider $, etc.)
- per_analyst.smart_money.verdict.is_no_data: True means smart_money was quiet for this ticker

How to read this:
- The aggregate is a starting point, not a verdict. You can override it.
- High disagreement (>0.5) means analysts split — read the per-analyst rationales
  before deciding. A confident aggregate with high disagreement usually means
  one strong analyst overrode the others; that's worth scrutinising.
- Smart-money is_no_data=True means "quiet," not "bearish." Don't treat absence as a vote.

Evidence: {ticker_evidence}

## Rules
1. Emit a target weight for EVERY watchlist ticker (including 0 for no position).
2. Weights must be in [0, 1]. Cash floor is enforced by the risk gate — aim naturally.
3. When opening a position (weight rises from 0 to >0), include a PositionThesis in new_positions.
4. When closing a position (weight drops from >0 to 0), include a reason in close_reasons.
5. decision_tag: snake_case, describes this tick's key decision.
6. reasoning: ≤300 chars summary.
7. updated_thesis: ≤500 chars working hypothesis for next tick.

Watchlist: {tickers}
"""
```

- [ ] **Step 2: Replace `src/orchestrator/pipeline.py`**

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


def _build_strategist():
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


def _build_memory_writer():
    from agents.memory.writer import MemoryWriter
    return MemoryWriter()


def build_pipeline(broker, db_session=None) -> SequentialAgent:
    from agents.evidence.builder import EvidenceBuilder
    from agents.evidence.writer import EvidenceWriter
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(),
            EvidenceBuilder(),
            EvidenceWriter(db_session=db_session),
            _build_strategist(),
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )
```

- [ ] **Step 3: Delete the AttributionWriter file**

```bash
git rm src/agents/attribution/writer.py
```

- [ ] **Step 4: Update `src/agents/attribution/__init__.py`**

If the `__init__.py` exports `AttributionWriter` or `build_attribution_writer`, replace it with an empty file (or delete the directory entirely if no other code lives there). Verify with:

```bash
.venv/Scripts/python -c "import agents.attribution"
```

If the import succeeds and is unused elsewhere, remove the directory:

```bash
.venv/Scripts/python -c "import sys; from pathlib import Path; print(list(Path('src').rglob('*.py')))" | grep -i attribution
```

If only `src/agents/attribution/__init__.py` remains, delete it. Run: `.venv/Scripts/python -m pytest tests/ -v` after to verify nothing else still imported it.

- [ ] **Step 5: Run all tests**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: all PASS. If anything imports `attribution.writer` or `AttributionWriter`, fix the import (it should be unused after this task).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(pipeline): wire EvidenceBuilder + Writer; retire AttributionWriter"
```

---

### Task 19: Smoke run validation + final cleanup

**Files:**
- Modify: `scripts/smoke_run.py` (path may differ — find the existing smoke run script)

- [ ] **Step 1: Locate the smoke-run script**

Run: `.venv/Scripts/python -c "from pathlib import Path; print([p for p in Path('src').rglob('smoke_run*')])"`
Plus: `.venv/Scripts/python -c "from pathlib import Path; print([p for p in Path('scripts').rglob('smoke_run*')])"`

Identify the file. The existing `README.txt` references `python -m scripts.smoke_run --ticks 1`, so it's under `src/scripts/` or `scripts/`.

- [ ] **Step 2: Add an assertion on `state["ticker_evidence"]`**

In the smoke-run script, after the pipeline runs but before exit, assert:

```python
state = session_service.get_session(...).state
te = state.get("ticker_evidence")
assert te is not None and len(te) == len(state.get("tickers", [])), (
    f"ticker_evidence missing or wrong length: got {len(te) if te else 0}, "
    f"expected {len(state.get('tickers', []))}"
)
print(f"ticker_evidence populated for all {len(te)} tickers ✓")
```

If the smoke-run script doesn't have access to the session state at the end, add a callback to the last pipeline step that prints/logs a summary.

- [ ] **Step 3: Run the smoke run on FakeBroker**

Run: `PYTHONPATH=src .venv/Scripts/python -m scripts.smoke_run --ticks 1`
Expected: the run completes without errors, and the assertion above passes. Cost: ~$0.07 of LLM calls.

- [ ] **Step 4: Sanity-check the new tables**

Run: `.venv/Scripts/python -c "
from sqlalchemy import inspect
from orchestrator.persistence import make_engine
e = make_engine('sqlite:///stockbot.db')
print(inspect(e).get_table_names())
"`
Expected output includes `analyst_evidence` and `ticker_evidence`.

Spot-check a couple of rows (this is exploratory, no assertion needed):

```bash
.venv/Scripts/python -c "
from sqlalchemy.orm import Session
from orchestrator.persistence import AnalystEvidenceRow, TickerEvidenceRow, make_engine
e = make_engine('sqlite:///stockbot.db')
with Session(e) as s:
    print('analyst rows:', s.query(AnalystEvidenceRow).count())
    print('ticker rows:', s.query(TickerEvidenceRow).count())
    for r in s.query(TickerEvidenceRow).limit(3):
        print(r.ticker, r.aggregate_direction, r.aggregate_confidence, r.disagreement_score)
"
```

- [ ] **Step 5: Append a graphify delta entry**

Append to `graphify-out/graph_delta.md` (gitignored):

```
## 2026-05-08 — Wire-in PR2 of B1 contract

Wired the contract module into the running pipeline. Analysts now emit
AnalystVerdict (was list[<Analyst>Signal]); EvidenceBuilder + EvidenceWriter
sit between the analyst pool and the strategist. AttributionWriter retired.

- New nodes: src/agents/evidence/{builder.py, writer.py}
- New edges: agents/evidence/builder.py → contract/digest.py;
  agents/evidence/writer.py → orchestrator/persistence.py;
  pipeline.py → agents/evidence/{builder,writer}
- Deleted: src/agents/attribution/writer.py (table attribution_signals stays for historical rows)
- Renamed state keys: <analyst>_signals → <analyst>_verdicts (LLM output) + <analyst>_evidence (post-pack)
- Schema: analyst_evidence, ticker_evidence tables added; AnalystSignal classes removed
```

- [ ] **Step 6: Final commit**

```bash
git add scripts/ src/scripts/ 2>/dev/null
git commit --allow-empty -m "feat(smoke-run): assert ticker_evidence populated; PR2 of B1 complete"
```

---

## Spec coverage check

For self-review during execution: each spec section should map to one or more tasks above.

| Spec section | Tasks |
|---|---|
| Contract types (AnalystVerdict, AnalystEvidence, 4 subclasses) | Task 1 |
| TickerEvidence + AggregateVerdict | Task 1 |
| Config knobs + README.txt | Task 2 |
| Digest math (`_aggregate`) | Task 4 |
| Disagreement score | Task 5 |
| `_fill_missing` | Task 6 |
| `build_ticker_evidence` | Task 7 |
| Technical features extractor | Task 8 |
| Fundamental features extractor | Task 9 |
| Sentiment features extractor | Task 10 |
| Smart-money features extractor | Task 11 |
| `pandas-ta` dependency | Task 3 |
| `AnalystEvidenceRow` + `TickerEvidenceRow` + writers | Task 12 |
| `EvidenceBuilder` + `EvidenceWriter` | Task 13 |
| Wire 4 analysts (features_callback, pack_callback, output_schema → AnalystVerdict, prompt update) | Tasks 14-17 |
| Strategist prompt change | Task 18 |
| Pipeline rewire (EvidenceBuilder/Writer in, AttributionWriter out) | Task 18 |
| Legacy `*_signals` keys + `<Analyst>Signal` classes removed | Tasks 14-17 (per-analyst) + Task 18 (pipeline + writer) |
| Smoke run assertion | Task 19 |
| Layer 1 tests (feature extractors) | Tasks 8-11 |
| Layer 2 tests (digest) | Tasks 4-7 |
| Layer 3 smoke | Task 19 |

---

## Notes for the executing engineer

- **`PYTHONPATH=src` matters.** This codebase imports from `src/` as the root (e.g., `from contract.evidence import ...`, not `from src.contract.evidence`). When running tests or scripts, ensure `PYTHONPATH=src` is set, or use the existing pytest config that handles this.
- **No `cd` prefixes on Bash commands** — the project's `.claude/CLAUDE.md` says the Bash tool already runs at the project root.
- **Run a single test file with verbose output**: `.venv/Scripts/python -m pytest tests/contract/test_digest.py -v`
- **Run all tests**: `.venv/Scripts/python -m pytest tests/ -v`
- **Linter**: `.venv/Scripts/python -m ruff check src/`
- **Smoke run cost**: ~$0.07 per tick on real LLMs; only run after Task 19 to validate end-to-end.
- **If a feature extractor breaks because the data model field names differ from the fixture**: adjust the fixture, not the extractor signature. Provider models are the source of truth for raw data shape.
- **Verdict items carry `ticker`** because the LLM emits a list of verdicts that need to be matched back to tickers. The `AnalystVerdict` base type does NOT carry `ticker` — only the per-analyst `_<Analyst>VerdictItem` wrapper does. `pack_callback` reads `ticker` off the dict and constructs the `AnalystEvidence` with it.

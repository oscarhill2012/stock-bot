# Plan A — Contract Scaffolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is self-contained — a fresh subagent should be able to pick it up cold using only this file + the spec at `docs/Phase4-stratergist-and-analysts/spec.md`.

**Goal:** Add the analyst-strategist contract types (`AnalystVerdict`, `AnalystEvidence`, `AggregateVerdict`, `TickerEvidence`), the deterministic aggregator (`build_ticker_evidence`), and the digest config — all pure new code, nothing imports it yet.

**Architecture:** New `src/contract/` package + `src/config/digest.py`. After this plan merges, the bot runs identically; the new modules are dead until Plan B imports them.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest. `pandas-ta` is added to `requirements.txt` here for Plan B to use later.

**Reference reading before starting:**
- `docs/Phase4-stratergist-and-analysts/spec.md` — design rationale, math, feature catalogue
- `src/agents/strategist/schema.py` — existing Pydantic conventions
- `src/config/README.md` — config-file conventions (every change here updates the README)

**Project conventions:**
- PYTHONPATH root = `src/`. Import as `from contract.evidence import AnalystEvidence`, **not** `from src.contract.evidence import ...`.
- Run pytest as `.venv/Scripts/python -m pytest`.
- Run ruff as `.venv/Scripts/python -m ruff check src/ tests/`.
- One commit per task. Use Conventional Commits prefixes (`feat`, `test`, `chore`, `docs`).

---

## Task A1: Create `src/contract/` package + `evidence.py` (verdict + per-analyst evidence types)

**Files:**
- Create: `src/contract/__init__.py` (empty)
- Create: `src/contract/evidence.py`
- Create: `tests/unit/contract/__init__.py` (empty)
- Create: `tests/unit/contract/test_evidence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_evidence.py`:
```python
"""AnalystVerdict + AnalystEvidence schema tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystEvidence, AnalystVerdict


def test_verdict_valid():
    v = AnalystVerdict(direction="bullish", confidence=0.7, rationale="RSI cooled + uptrend intact")
    assert v.direction == "bullish"
    assert v.confidence == 0.7
    assert v.is_no_data is False


def test_verdict_neutral_no_data_flag():
    v = AnalystVerdict(direction="neutral", confidence=0.0, rationale="no filings", is_no_data=True)
    assert v.is_no_data is True


def test_verdict_rejects_bad_direction():
    with pytest.raises(ValidationError):
        AnalystVerdict(direction="up", confidence=0.5, rationale="x")


def test_verdict_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        AnalystVerdict(direction="bullish", confidence=1.5, rationale="x")


def test_verdict_rejects_rationale_over_160_chars():
    with pytest.raises(ValidationError):
        AnalystVerdict(direction="bullish", confidence=0.5, rationale="x" * 161)


def test_evidence_valid():
    e = AnalystEvidence(
        ticker="AAPL",
        analyst="technical",
        features={"rsi_14": 42.3, "pct_change_5d": -0.018},
        verdict=AnalystVerdict(direction="bearish", confidence=0.6, rationale="weakening"),
    )
    assert e.ticker == "AAPL"
    assert e.analyst == "technical"
    assert e.features["rsi_14"] == 42.3


def test_evidence_rejects_unknown_analyst():
    with pytest.raises(ValidationError):
        AnalystEvidence(
            ticker="AAPL",
            analyst="macro",
            features={},
            verdict=AnalystVerdict(direction="neutral", confidence=0.0, rationale="x"),
        )


def test_evidence_round_trip():
    original = AnalystEvidence(
        ticker="MSFT",
        analyst="fundamental",
        features={"pe_trailing": 32.5, "fcf_yield_pct": 2.4},
        verdict=AnalystVerdict(direction="neutral", confidence=0.4, rationale="balanced"),
    )
    dumped = original.model_dump(mode="json")
    rebuilt = AnalystEvidence.model_validate(dumped)
    assert rebuilt == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_evidence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contract'`.

- [ ] **Step 3: Write the implementation**

Create `src/contract/__init__.py` empty.

Create `src/contract/evidence.py`:
```python
"""Per-analyst evidence types — code-only digest substrate.

Each analyst returns one AnalystEvidence per ticker per tick. The deterministic
aggregator in `contract.digest` collapses the four analysts' evidence into one
TickerEvidence per ticker.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AnalystName = Literal["technical", "fundamental", "sentiment", "smart_money"]


class AnalystVerdict(BaseModel):
    """LLM-emitted directional call for one ticker."""

    direction: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=160)
    is_no_data: bool = False


class AnalystEvidence(BaseModel):
    """One analyst's structured output for one ticker on one tick.

    `features` carries the deterministic feature extractor's output (numeric
    only — no strings). Keys are analyst-specific; see Phase 4 spec for the
    locked catalogue per analyst.
    """

    ticker: str
    analyst: AnalystName
    features: dict[str, float]
    verdict: AnalystVerdict
```

Also create `tests/unit/contract/__init__.py` empty.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_evidence.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/__init__.py src/contract/evidence.py tests/unit/contract/__init__.py tests/unit/contract/test_evidence.py
git commit -m "feat(contract): add AnalystVerdict + AnalystEvidence types"
```

---

## Task A2: Add `TickerEvidence` + `AggregateVerdict`

**Files:**
- Create: `src/contract/ticker_evidence.py`
- Create: `tests/unit/contract/test_ticker_evidence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_ticker_evidence.py`:
```python
"""TickerEvidence + AggregateVerdict tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystEvidence, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _ev(analyst: str, direction: str, confidence: float) -> AnalystEvidence:
    return AnalystEvidence(
        ticker="AAPL",
        analyst=analyst,
        features={},
        verdict=AnalystVerdict(direction=direction, confidence=confidence, rationale="x"),
    )


def test_aggregate_valid():
    a = AggregateVerdict(direction="bullish", magnitude=0.42, weights_used={"technical": 1.0, "fundamental": 1.0})
    assert a.direction == "bullish"
    assert a.magnitude == 0.42


def test_aggregate_rejects_bad_magnitude():
    with pytest.raises(ValidationError):
        AggregateVerdict(direction="neutral", magnitude=1.5, weights_used={})


def test_ticker_evidence_valid():
    te = TickerEvidence(
        ticker="AAPL",
        tick_id="tick_X",
        recorded_at=datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc),
        per_analyst={
            "technical": _ev("technical", "bullish", 0.6),
            "fundamental": _ev("fundamental", "bearish", 0.4),
        },
        aggregate=AggregateVerdict(direction="neutral", magnitude=0.1, weights_used={"technical": 1.0, "fundamental": 1.0}),
        disagreement_score=0.55,
    )
    assert te.ticker == "AAPL"
    assert "technical" in te.per_analyst


def test_ticker_evidence_rejects_disagreement_out_of_range():
    with pytest.raises(ValidationError):
        TickerEvidence(
            ticker="AAPL",
            tick_id="tick_X",
            recorded_at=datetime.now(tz=timezone.utc),
            per_analyst={},
            aggregate=AggregateVerdict(direction="neutral", magnitude=0.0, weights_used={}),
            disagreement_score=1.5,
        )


def test_ticker_evidence_round_trip():
    original = TickerEvidence(
        ticker="MSFT",
        tick_id="tick_Y",
        recorded_at=datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc),
        per_analyst={"technical": _ev("technical", "bullish", 0.5)},
        aggregate=AggregateVerdict(direction="bullish", magnitude=0.5, weights_used={"technical": 1.0}),
        disagreement_score=0.0,
    )
    dumped = original.model_dump(mode="json")
    rebuilt = TickerEvidence.model_validate(dumped)
    assert rebuilt == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_ticker_evidence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contract.ticker_evidence'`.

- [ ] **Step 3: Write the implementation**

Create `src/contract/ticker_evidence.py`:
```python
"""TickerEvidence — the per-ticker per-tick aggregate the strategist reads.

Built deterministically from per-analyst AnalystEvidence by `contract.digest.build_ticker_evidence`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from contract.evidence import AnalystEvidence


class AggregateVerdict(BaseModel):
    """Cross-analyst summary direction + magnitude.

    `magnitude` is the absolute value of the weighted-confidence sum, normalised
    by the total weight. `direction` is "neutral" when magnitude < dead-zone.
    """

    direction: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    weights_used: dict[str, float]


class TickerEvidence(BaseModel):
    """One row of evidence the strategist sees for a ticker on a tick.

    `disagreement_score` is variance-based across the analysts' signed confidences,
    normalised to [0,1]. High disagreement = analysts conflict.
    """

    ticker: str
    tick_id: str
    recorded_at: datetime
    per_analyst: dict[str, AnalystEvidence]
    aggregate: AggregateVerdict
    disagreement_score: float = Field(ge=0.0, le=1.0)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_ticker_evidence.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/ticker_evidence.py tests/unit/contract/test_ticker_evidence.py
git commit -m "feat(contract): add TickerEvidence + AggregateVerdict types"
```

---

## Task A3: Add `src/config/digest.py` (weights + dead zone) + README update

**Files:**
- Create: `src/config/digest.py`
- Modify: `src/config/README.md` (append a new section)
- Create: `tests/unit/contract/test_digest_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_digest_config.py`:
```python
"""Digest config tests — Tier 1, no LLM."""
from __future__ import annotations

from contract.evidence import AnalystName  # noqa: F401  (used in type-checks)
from config.digest import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE


def test_default_weights_cover_all_four_analysts():
    assert set(DEFAULT_ANALYST_WEIGHTS.keys()) == {
        "technical", "fundamental", "sentiment", "smart_money"
    }


def test_default_weights_are_all_one():
    for w in DEFAULT_ANALYST_WEIGHTS.values():
        assert w == 1.0


def test_dead_zone_is_a_positive_float_under_one():
    assert isinstance(DIRECTION_DEAD_ZONE, float)
    assert 0.0 < DIRECTION_DEAD_ZONE < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_digest_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config.digest'`.

- [ ] **Step 3: Write the config module**

Create `src/config/digest.py`:
```python
"""Aggregator config for the analyst → strategist digest.

Only knobs required by `contract.digest.build_ticker_evidence` live here. Per-key
nested weighting (e.g. `smart_money.n_politicians > 2 ⇒ +x`) is deferred to a
future spec; for now weights are per-analyst-family only.
"""
from __future__ import annotations

DEFAULT_ANALYST_WEIGHTS: dict[str, float] = {
    "technical": 1.0,
    "fundamental": 1.0,
    "sentiment": 1.0,
    "smart_money": 1.0,
}

DIRECTION_DEAD_ZONE: float = 0.15
```

- [ ] **Step 4: Update `src/config/README.md`**

Read the existing `src/config/README.md`, then append a new section at the end. Use the existing section style as a template — the new section must include the file purpose and every constant's meaning.

Append (verbatim) at the end of `src/config/README.md`:
```markdown

---

## `digest.py` — analyst → strategist aggregator config

Knobs for the deterministic per-ticker digest built by `contract.digest.build_ticker_evidence`.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `DEFAULT_ANALYST_WEIGHTS` | `dict[str, float]` | `{technical: 1.0, fundamental: 1.0, sentiment: 1.0, smart_money: 1.0}` | Per-analyst weight applied to that analyst's signed confidence in the aggregate vote. Equal-weight default. The slot for learned per-key weighting (B5 in the backlog) sits on top of this. |
| `DIRECTION_DEAD_ZONE` | `float` | `0.15` | Magnitude threshold below which the aggregate direction collapses to `"neutral"` regardless of the sign of the weighted sum. Prevents flip-flopping when one low-confidence analyst drags the aggregate across zero. |
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_digest_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/config/digest.py src/config/README.md tests/unit/contract/test_digest_config.py
git commit -m "feat(config): add digest aggregator config (DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE)"
```

---

## Task A4: Implement `build_ticker_evidence` (the deterministic aggregator)

**Files:**
- Create: `src/contract/digest.py`
- Create: `tests/unit/contract/test_digest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_digest.py`:
```python
"""build_ticker_evidence aggregator tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contract.digest import build_ticker_evidence
from contract.evidence import AnalystEvidence, AnalystVerdict
from config.digest import DEFAULT_ANALYST_WEIGHTS


def _ev(analyst: str, direction: str, conf: float, ticker: str = "AAPL") -> AnalystEvidence:
    return AnalystEvidence(
        ticker=ticker,
        analyst=analyst,
        features={},
        verdict=AnalystVerdict(direction=direction, confidence=conf, rationale="x"),
    )


def _now():
    return datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)


# ── Direction sign + dead zone ────────────────────────────────────────────────


def test_all_bullish_high_confidence_aggregates_bullish():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.8),
        "fundamental": _ev("fundamental", "bullish", 0.7),
        "sentiment": _ev("sentiment", "bullish", 0.6),
        "smart_money": _ev("smart_money", "bullish", 0.9),
    }
    te = build_ticker_evidence(per_analyst, ticker="AAPL", tick_id="t",
                                recorded_at=_now(), weights=DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.direction == "bullish"
    assert te.aggregate.magnitude > 0.5


def test_all_bearish_aggregates_bearish():
    per_analyst = {a: _ev(a, "bearish", 0.7) for a in DEFAULT_ANALYST_WEIGHTS}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.direction == "bearish"


def test_split_low_confidence_falls_into_dead_zone_neutral():
    # 2 bullish at 0.1, 2 bearish at 0.1 → weighted sum = 0 → neutral
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.1),
        "fundamental": _ev("fundamental", "bullish", 0.1),
        "sentiment": _ev("sentiment", "bearish", 0.1),
        "smart_money": _ev("smart_money", "bearish", 0.1),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.direction == "neutral"


def test_one_strong_bullish_beats_three_weak_neutrals_outside_dead_zone():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.95),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "sentiment": _ev("sentiment", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    # weighted_sum = 0.95; magnitude = 0.95/4 = 0.2375 > dead_zone (0.15)
    assert te.aggregate.direction == "bullish"


def test_dead_zone_collapses_marginally_positive_to_neutral():
    # 0.5 weighted-confidence sum across 4 analysts → magnitude 0.125 < 0.15
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.5),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "sentiment": _ev("sentiment", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.direction == "neutral"


# ── Disagreement score ────────────────────────────────────────────────────────


def test_unanimous_agreement_disagreement_zero():
    per_analyst = {a: _ev(a, "bullish", 0.7) for a in DEFAULT_ANALYST_WEIGHTS}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.disagreement_score < 0.01


def test_max_split_disagreement_high():
    # Two strongly bullish, two strongly bearish
    per_analyst = {
        "technical": _ev("technical", "bullish", 1.0),
        "fundamental": _ev("fundamental", "bullish", 1.0),
        "sentiment": _ev("sentiment", "bearish", 1.0),
        "smart_money": _ev("smart_money", "bearish", 1.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.disagreement_score > 0.5


# ── Missing analyst neutral-fill ──────────────────────────────────────────────


def test_missing_analysts_neutral_filled():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.8),
        # fundamental, sentiment, smart_money all missing
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    # The missing three should still appear as neutral entries in per_analyst
    assert set(te.per_analyst.keys()) == set(DEFAULT_ANALYST_WEIGHTS.keys())
    for missing in ("fundamental", "sentiment", "smart_money"):
        assert te.per_analyst[missing].verdict.direction == "neutral"
        assert te.per_analyst[missing].verdict.confidence == 0.0
        assert te.per_analyst[missing].verdict.is_no_data is True


def test_smart_money_no_data_flag_treated_as_neutral():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.6),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "sentiment": _ev("sentiment", "bullish", 0.6),
        "smart_money": AnalystEvidence(
            ticker="AAPL",
            analyst="smart_money",
            features={"is_no_data": 1.0},
            verdict=AnalystVerdict(
                direction="bullish",  # ignored because is_no_data
                confidence=0.9,
                rationale="x",
                is_no_data=True,
            ),
        ),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    # smart_money's "bullish 0.9" must NOT inflate the aggregate
    # because is_no_data=True. So aggregate is built from 3 analysts at 0.6 each.
    # weighted sum = 1.8; magnitude = 1.8/4 = 0.45
    assert te.aggregate.direction == "bullish"
    assert te.aggregate.magnitude == pytest.approx(0.45, rel=0.01)


# ── weights_used snapshotting ─────────────────────────────────────────────────


def test_weights_used_snapshotted_into_aggregate():
    per_analyst = {a: _ev(a, "bullish", 0.5) for a in DEFAULT_ANALYST_WEIGHTS}
    custom = {"technical": 2.0, "fundamental": 1.0, "sentiment": 0.5, "smart_money": 1.0}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), custom)
    assert te.aggregate.weights_used == custom


# ── Ticker / tick_id / recorded_at carry-through ──────────────────────────────


def test_metadata_propagated():
    per_analyst = {a: _ev(a, "neutral", 0.0, ticker="MSFT") for a in DEFAULT_ANALYST_WEIGHTS}
    when = datetime(2026, 5, 8, 16, 30, tzinfo=timezone.utc)
    te = build_ticker_evidence(per_analyst, "MSFT", "tick_42", when, DEFAULT_ANALYST_WEIGHTS)
    assert te.ticker == "MSFT"
    assert te.tick_id == "tick_42"
    assert te.recorded_at == when
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_digest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contract.digest'`.

- [ ] **Step 3: Write the aggregator**

Create `src/contract/digest.py`:
```python
"""Deterministic per-ticker digest — collapses 4 analysts → 1 TickerEvidence.

Pure Python, no LLM, no I/O. The strategist consumes the output instead of four
separate per-analyst signal lists. See `docs/Phase4-stratergist-and-analysts/spec.md`
for the math + design rationale.
"""
from __future__ import annotations

from datetime import datetime
from statistics import variance
from typing import Iterable, Mapping

from config.digest import DIRECTION_DEAD_ZONE
from contract.evidence import AnalystEvidence, AnalystName, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence

_ALL_ANALYSTS: tuple[AnalystName, ...] = ("technical", "fundamental", "sentiment", "smart_money")


def _direction_sign(direction: str) -> int:
    return {"bullish": 1, "bearish": -1, "neutral": 0}[direction]


def _fill_missing(
    per_analyst: Mapping[str, AnalystEvidence],
    ticker: str,
    weights: Mapping[str, float],
) -> dict[str, AnalystEvidence]:
    """Fill in neutral-zero AnalystEvidence for any analyst named in `weights`
    but absent from `per_analyst`. is_no_data=True so the aggregator can ignore it.
    """
    filled: dict[str, AnalystEvidence] = dict(per_analyst)
    for name in weights:
        if name in filled:
            continue
        filled[name] = AnalystEvidence(
            ticker=ticker,
            analyst=name,  # type: ignore[arg-type]
            features={},
            verdict=AnalystVerdict(
                direction="neutral",
                confidence=0.0,
                rationale="(no analyst output this tick)",
                is_no_data=True,
            ),
        )
    return filled


def _weighted_signed_confidences(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> list[float]:
    """List of `weight × sign(direction) × confidence` per analyst, with
    is_no_data analysts contributing 0.0 (effectively ignored)."""
    out: list[float] = []
    for name in weights:
        ev = per_analyst.get(name)
        if ev is None:
            out.append(0.0)
            continue
        if ev.verdict.is_no_data:
            out.append(0.0)
            continue
        sign = _direction_sign(ev.verdict.direction)
        out.append(weights[name] * sign * ev.verdict.confidence)
    return out


def _aggregate(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> AggregateVerdict:
    contributions = _weighted_signed_confidences(per_analyst, weights)
    weighted_sum = sum(contributions)
    total_weight = sum(weights.values()) or 1.0
    magnitude = abs(weighted_sum) / total_weight

    if magnitude < DIRECTION_DEAD_ZONE:
        direction = "neutral"
    elif weighted_sum > 0:
        direction = "bullish"
    else:
        direction = "bearish"

    return AggregateVerdict(
        direction=direction,  # type: ignore[arg-type]
        magnitude=min(magnitude, 1.0),  # clamp; weights >1.0 could push past
        weights_used=dict(weights),
    )


def _disagreement_score(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> float:
    """Variance of per-analyst signed confidences (no-data ignored), normalised
    to [0,1]. Variance is computed over signed confidences in [-1,+1], so the
    theoretical max variance is 1.0 (two values at +1 and -1)."""
    signed: list[float] = []
    for name in weights:
        ev = per_analyst.get(name)
        if ev is None or ev.verdict.is_no_data:
            continue
        signed.append(_direction_sign(ev.verdict.direction) * ev.verdict.confidence)
    if len(signed) < 2:
        return 0.0
    return min(variance(signed), 1.0)


def build_ticker_evidence(
    per_analyst: Mapping[str, AnalystEvidence],
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    weights: Mapping[str, float],
) -> TickerEvidence:
    """Collapse per-analyst evidence into one TickerEvidence.

    `weights` must cover every analyst the digest considers. Missing analysts
    in `per_analyst` are neutral-filled (no_data) so the aggregator's shape is
    invariant to provider sparseness.
    """
    filled = _fill_missing(per_analyst, ticker, weights)
    aggregate = _aggregate(filled, weights)
    disagreement = _disagreement_score(filled, weights)

    return TickerEvidence(
        ticker=ticker,
        tick_id=tick_id,
        recorded_at=recorded_at,
        per_analyst=filled,
        aggregate=aggregate,
        disagreement_score=disagreement,
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_digest.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/digest.py tests/unit/contract/test_digest.py
git commit -m "feat(contract): implement build_ticker_evidence (deterministic aggregator)"
```

---

## Task A5: Add `pandas-ta` to requirements

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add pandas-ta**

Read `requirements.txt`, then add a new line for `pandas-ta`. Pin to a recent stable version (`pandas-ta>=0.3.14b,<0.4`). If the file already contains `pandas-ta`, this task is a no-op — record that and skip to commit.

If adding, add the line in alphabetical order with the other deps (or at the end if order isn't strictly maintained — match the file's existing convention).

- [ ] **Step 2: Verify install resolves**

Run: `.venv/Scripts/python -m pip install -r requirements.txt`
Expected: clean install, no resolver errors.

- [ ] **Step 3: Verify pandas-ta imports cleanly**

Run: `.venv/Scripts/python -c "import pandas_ta as ta; print(ta.__name__)"`
Expected: `pandas_ta`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore(deps): add pandas-ta for the technical analyst feature extractor"
```

---

## Task A6: Final regression pass

- [ ] **Step 1: Run all unit tests**

Run: `.venv/Scripts/python -m pytest tests/unit/ -v`
Expected: All passing (existing tests + new contract tests).

- [ ] **Step 2: Run ruff**

Run: `.venv/Scripts/python -m ruff check src/ tests/`
Expected: zero new violations introduced by Plan A. Pre-existing violations elsewhere in the codebase are out of scope for this plan.

- [ ] **Step 3: Verify the new modules import cleanly**

Run: `.venv/Scripts/python -c "from contract.digest import build_ticker_evidence; from contract.evidence import AnalystEvidence, AnalystVerdict; from contract.ticker_evidence import TickerEvidence, AggregateVerdict; from config.digest import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Append graphify delta entry**

Edit `graphify-out/graph_delta.md`. Append at the end:
```markdown

## YYYY-MM-DD — Phase 4 Plan A: contract scaffolding (additive)

Added `src/contract/` package + `src/config/digest.py`. Pure new code, no consumer
yet (Plan B will start writing AnalystEvidence; Plan C will start reading it).

- New nodes: `src/contract/__init__.py`, `src/contract/evidence.py` (`AnalystVerdict`, `AnalystEvidence`),
  `src/contract/ticker_evidence.py` (`AggregateVerdict`, `TickerEvidence`),
  `src/contract/digest.py` (`build_ticker_evidence`, `_aggregate`, `_disagreement_score`, `_fill_missing`),
  `src/config/digest.py` (`DEFAULT_ANALYST_WEIGHTS`, `DIRECTION_DEAD_ZONE`).
- New edges: `digest.build_ticker_evidence --uses--> _fill_missing/_aggregate/_disagreement_score`;
  `digest --imports--> config.digest.DIRECTION_DEAD_ZONE`.
- No removals. No existing call edges modified.
```

Replace `YYYY-MM-DD` with today's date.

- [ ] **Step 5: Commit the delta entry**

```bash
git add graphify-out/graph_delta.md
git commit -m "docs(graphify): log Plan A contract scaffolding addition"
```

---

## Done

Plan A merged. The bot's runtime behaviour is unchanged — none of `analyst_pool`, strategist, risk_gate, executor, or memory_writer imports `contract.*` yet. The next plan (Plan B) is the first consumer.

**Next:** [Plan B — Per-analyst extractors with dual-emit](./plan-B-extractors-dual-emit.md)

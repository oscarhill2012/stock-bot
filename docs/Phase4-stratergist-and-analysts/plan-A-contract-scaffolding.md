# Plan A — Contract Scaffolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is self-contained — a fresh subagent should be able to pick it up cold using only this file + the spec at `docs/Phase4-stratergist-and-analysts/spec.md`.

**Goal:** Add the analyst-strategist contract types (`AnalystVerdict`, `AnalystEvidence`, `AggregateVerdict`, `TickerEvidence`), the deterministic aggregator (`build_ticker_evidence`), and the digest config — all pure new code, nothing imports it yet.

**Architecture:** New `src/contract/` package, including `src/contract/digest_defaults.py` (the aggregator's tunable constants — co-located with the code that uses them, not promoted to JSON config under `config/`). After this plan merges, the bot runs identically; the new modules are dead until Plan B imports them.

**Schema design — affordances for the future learning loop (Goal 3):** the schema below is shaped to match Plan D's persisted ORM rows so the contract is identical from Plan A through Plan D. The fields beyond bare-minimum direction/confidence are deliberate substrate for the knowledge base described in `docs/superpowers/backlog.md#B2`:

- per-analyst **`magnitude`** — numeric signal-strength (independent of `confidence`) so B5's per-evidence-key weighting can learn "feature-X at value-Y is predictive" rather than the all-or-nothing "trust this analyst more" of family-level weights.
- per-analyst **`key_factors`** (list of short strings) — structured per-analyst rationale that survives JSON serialisation. The KB will use these as the lookup primitive for "the last N times we saw a setup shaped like this."
- aggregate **`confidence`** distinct from **`magnitude`** — confidence answers "how sure are we?", magnitude answers "how far from neutral?". The KB will key on patterns like high-magnitude/low-confidence or vice versa, so they must be separable.
- aggregate **`summary`** — a short rendered string of the cross-analyst stance, suitable for injecting "similar past setup: [summary] → [outcome]" snippets back into future strategist prompts once the KB exists.
- aggregate **`disagreement`** sits alongside `lean`/`magnitude`/`confidence`/`summary` inside `AggregateVerdict` (rather than at `TickerEvidence` top level) so the whole stance object is one self-contained KB lookup row.

> **Note (2026-05-08):** The original draft of this plan put the digest constants in `src/config/digest.py`. The project has since moved JSON-only config to `config/` at the project root and dropped `src/config/` entirely. `DEFAULT_ANALYST_WEIGHTS` and `DIRECTION_DEAD_ZONE` are *behavioural defaults* (dict + float consumed by `build_ticker_evidence`), not runtime-tunable JSON, so they live next to the aggregator in `src/contract/digest_defaults.py`. If a future spec needs them tunable without code changes, promote them to `config/digest.json` then with a small loader.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest. `pandas-ta` is added to `requirements.txt` here for Plan B to use later.

**Reference reading before starting:**
- `docs/Phase4-stratergist-and-analysts/spec.md` — design rationale, math, feature catalogue
- `src/agents/strategist/schema.py` — existing Pydantic conventions
- `config/README.md` — JSON config index (Plan A adds no entries; the digest defaults live in code, not here)

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

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystEvidence, AnalystVerdict


def _verdict(**overrides) -> AnalystVerdict:
    base = dict(
        lean="bullish",
        magnitude=0.5,
        confidence=0.7,
        rationale="RSI cooled + uptrend intact",
        key_factors=["rsi_14: 42"],
        is_no_data=False,
    )
    base.update(overrides)
    return AnalystVerdict(**base)


def _now() -> datetime:
    return datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)


def test_verdict_valid():
    v = _verdict()
    assert v.lean == "bullish"
    assert v.magnitude == 0.5
    assert v.confidence == 0.7
    assert v.key_factors == ["rsi_14: 42"]
    assert v.is_no_data is False


def test_verdict_neutral_no_data_flag():
    v = _verdict(lean="neutral", magnitude=0.0, confidence=0.0,
                 rationale="no filings", key_factors=[], is_no_data=True)
    assert v.is_no_data is True


def test_verdict_key_factors_default_empty():
    v = AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.0, rationale="x")
    assert v.key_factors == []


def test_verdict_rejects_bad_lean():
    with pytest.raises(ValidationError):
        _verdict(lean="up")


def test_verdict_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        _verdict(confidence=1.5)


def test_verdict_rejects_magnitude_out_of_range():
    with pytest.raises(ValidationError):
        _verdict(magnitude=1.5)


def test_verdict_rejects_rationale_over_160_chars():
    with pytest.raises(ValidationError):
        _verdict(rationale="x" * 161)


def test_evidence_valid():
    e = AnalystEvidence(
        ticker="AAPL",
        analyst="technical",
        tick_id="2026-05-08T14:00:00Z",
        recorded_at=_now(),
        features={"rsi_14": 42.3, "pct_change_5d": -0.018},
        feature_warnings=[],
        verdict=_verdict(lean="bearish", magnitude=0.4, confidence=0.6, rationale="weakening"),
    )
    assert e.ticker == "AAPL"
    assert e.analyst == "technical"
    assert e.tick_id == "2026-05-08T14:00:00Z"
    assert e.features["rsi_14"] == 42.3
    assert e.feature_warnings == []


def test_evidence_feature_warnings_default_empty():
    e = AnalystEvidence(
        ticker="AAPL",
        analyst="technical",
        tick_id="t",
        recorded_at=_now(),
        features={},
        verdict=_verdict(),
    )
    assert e.feature_warnings == []


def test_evidence_rejects_unknown_analyst():
    with pytest.raises(ValidationError):
        AnalystEvidence(
            ticker="AAPL",
            analyst="macro",
            tick_id="t",
            recorded_at=_now(),
            features={},
            verdict=_verdict(lean="neutral", magnitude=0.0, confidence=0.0, rationale="x"),
        )


def test_evidence_round_trip():
    original = AnalystEvidence(
        ticker="MSFT",
        analyst="fundamental",
        tick_id="2026-05-08T15:00:00Z",
        recorded_at=_now(),
        features={"pe_trailing": 32.5, "fcf_yield_pct": 2.4},
        feature_warnings=["pe_forward unavailable"],
        verdict=_verdict(lean="neutral", magnitude=0.1, confidence=0.4,
                         rationale="balanced", key_factors=["pe_trailing: 32.5"]),
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

The schema below is the same shape that Plan D persists to SQLite, so the
contract is identical from Plan A through Plan D. Several fields exist to
support the future knowledge-base / learning loop (see backlog B2):

- `magnitude` is independent of `confidence` so per-evidence-key weighting
  (backlog B5) can learn that some feature ranges matter more than others.
- `key_factors` survives JSON round-tripping and is the structured pattern-
  recall primitive the KB will key off.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AnalystName = Literal["technical", "fundamental", "sentiment", "smart_money"]


class AnalystVerdict(BaseModel):
    """LLM-emitted directional call for one ticker."""

    lean: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=160)
    key_factors: list[str] = Field(default_factory=list, max_length=8)
    is_no_data: bool = False


class AnalystEvidence(BaseModel):
    """One analyst's structured output for one ticker on one tick.

    `features` carries the deterministic feature extractor's output (numeric
    only — no strings). Keys are analyst-specific; see Phase 4 spec for the
    locked catalogue per analyst. `feature_warnings` records any
    extractor-emitted issues (missing data window, NaN replacement, etc.) so
    downstream consumers can tell "extractor returned 0.0 because the input
    was missing" apart from "extractor returned a real 0.0".
    """

    ticker: str
    analyst: AnalystName
    tick_id: str
    recorded_at: datetime
    features: dict[str, float]
    feature_warnings: list[str] = Field(default_factory=list)
    verdict: AnalystVerdict
```

Also create `tests/unit/contract/__init__.py` empty.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_evidence.py -v`
Expected: PASS (11 tests).

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


def _now() -> datetime:
    return datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)


def _ev(analyst: str, lean: str, magnitude: float, confidence: float) -> AnalystEvidence:
    return AnalystEvidence(
        ticker="AAPL",
        analyst=analyst,
        tick_id="tick_X",
        recorded_at=_now(),
        features={},
        feature_warnings=[],
        verdict=AnalystVerdict(
            lean=lean, magnitude=magnitude, confidence=confidence,
            rationale="x", key_factors=[], is_no_data=False,
        ),
    )


def _agg(**overrides) -> AggregateVerdict:
    base = dict(lean="bullish", magnitude=0.42, confidence=0.6,
                disagreement=0.1, summary="3/4 bullish, 1 neutral")
    base.update(overrides)
    return AggregateVerdict(**base)


def test_aggregate_valid():
    a = _agg()
    assert a.lean == "bullish"
    assert a.magnitude == 0.42
    assert a.confidence == 0.6
    assert a.disagreement == 0.1
    assert a.summary.startswith("3/4")


def test_aggregate_rejects_bad_magnitude():
    with pytest.raises(ValidationError):
        _agg(magnitude=1.5)


def test_aggregate_rejects_bad_disagreement():
    with pytest.raises(ValidationError):
        _agg(disagreement=1.5)


def test_aggregate_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        _agg(confidence=-0.1)


def test_aggregate_summary_default_empty():
    a = AggregateVerdict(lean="neutral", magnitude=0.0, confidence=0.0, disagreement=0.0)
    assert a.summary == ""


def test_ticker_evidence_valid():
    te = TickerEvidence(
        ticker="AAPL",
        tick_id="tick_X",
        recorded_at=_now(),
        per_analyst={
            "technical": _ev("technical", "bullish", 0.6, 0.6),
            "fundamental": _ev("fundamental", "bearish", 0.4, 0.4),
        },
        aggregate=_agg(lean="neutral", magnitude=0.1, confidence=0.5, disagreement=0.55,
                       summary="split"),
        weights={"technical": 1.0, "fundamental": 1.0},
    )
    assert te.ticker == "AAPL"
    assert "technical" in te.per_analyst
    assert te.weights["technical"] == 1.0


def test_ticker_evidence_round_trip():
    original = TickerEvidence(
        ticker="MSFT",
        tick_id="tick_Y",
        recorded_at=datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc),
        per_analyst={"technical": _ev("technical", "bullish", 0.5, 0.5)},
        aggregate=_agg(lean="bullish", magnitude=0.5, confidence=0.5, disagreement=0.0,
                       summary="1 bullish"),
        weights={"technical": 1.0},
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

Built deterministically from per-analyst AnalystEvidence by
`contract.digest.build_ticker_evidence`. The shape mirrors the persisted
`TickerEvidenceRow` defined in Plan D so a TickerEvidence object can round-
trip to and from SQLite without any field-name translation.

`AggregateVerdict` carries `lean` + `magnitude` + `confidence` + `disagreement`
+ `summary` so the whole cross-analyst stance is one self-contained record —
this is the lookup primitive the future knowledge-base loop (backlog B2) will
key on. `weights` lives at the `TickerEvidence` level (not nested inside the
aggregate) so the snapshotted weighting can evolve independently of stance
fields without breaking aggregate-row equality.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from contract.evidence import AnalystEvidence


class AggregateVerdict(BaseModel):
    """Cross-analyst summary stance.

    `magnitude` = |weighted signed-confidence sum| / total weight, the
    "how far from neutral" axis. `lean` is "neutral" when magnitude < dead-zone.
    `confidence` is the mean confidence across contributing (non-no_data)
    analysts — kept separate from magnitude so the KB can distinguish
    high-magnitude/low-confidence setups from high-magnitude/high-confidence
    ones. `disagreement` is variance of signed confidences in [0,1].
    `summary` is a short rendered string ("3/4 bullish, 1 neutral") suitable
    for dropping into prompts or KB lookups.
    """

    lean: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    disagreement: float = Field(ge=0.0, le=1.0)
    summary: str = Field(default="", max_length=240)


class TickerEvidence(BaseModel):
    """One row of evidence the strategist sees for a ticker on a tick."""

    ticker: str
    tick_id: str
    recorded_at: datetime
    per_analyst: dict[str, AnalystEvidence]
    aggregate: AggregateVerdict
    weights: dict[str, float]
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_ticker_evidence.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/ticker_evidence.py tests/unit/contract/test_ticker_evidence.py
git commit -m "feat(contract): add TickerEvidence + AggregateVerdict types"
```

---

## Task A3: Add `src/contract/digest_defaults.py` (weights + dead zone)

**Files:**
- Create: `src/contract/digest_defaults.py`
- Create: `tests/unit/contract/test_digest_defaults.py`

These constants are behavioural defaults consumed only by `contract.digest.build_ticker_evidence` — they sit beside the aggregator, not in `config/` (which is JSON-only). See the architecture note at the top of this plan for the rationale.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_digest_defaults.py`:
```python
"""Digest defaults tests — Tier 1, no LLM."""
from __future__ import annotations

from contract.evidence import AnalystName  # noqa: F401  (used in type-checks)
from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE


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

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_digest_defaults.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contract.digest_defaults'`.

- [ ] **Step 3: Write the defaults module**

Create `src/contract/digest_defaults.py`:
```python
"""Tunable defaults for the analyst → strategist digest aggregator.

Co-located with `contract.digest` because they're behavioural defaults consumed
only by `build_ticker_evidence`, not runtime-tunable JSON config. Per-key nested
weighting (e.g. `smart_money.n_politicians > 2 ⇒ +x`) is deferred to a future
spec; for now weights are per-analyst-family only. If a future spec needs these
tunable without code changes, promote to `config/digest.json` + a loader.
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

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_digest_defaults.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/digest_defaults.py tests/unit/contract/test_digest_defaults.py
git commit -m "feat(contract): add digest aggregator defaults (DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE)"
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
from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS
from contract.evidence import AnalystEvidence, AnalystVerdict


def _now():
    return datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)


def _ev(analyst: str, lean: str, conf: float, ticker: str = "AAPL",
        magnitude: float | None = None) -> AnalystEvidence:
    """Build an AnalystEvidence. By default magnitude == confidence (the LLM is
    instructed to keep them aligned unless it has a reason not to). Tests that
    care about the magnitude/confidence split pass `magnitude=` explicitly."""
    return AnalystEvidence(
        ticker=ticker,
        analyst=analyst,
        tick_id="t",
        recorded_at=_now(),
        features={},
        feature_warnings=[],
        verdict=AnalystVerdict(
            lean=lean,
            magnitude=conf if magnitude is None else magnitude,
            confidence=conf,
            rationale="x",
            key_factors=[],
            is_no_data=False,
        ),
    )


# ── Lean sign + dead zone ─────────────────────────────────────────────────────


def test_all_bullish_high_confidence_aggregates_bullish():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.8),
        "fundamental": _ev("fundamental", "bullish", 0.7),
        "sentiment": _ev("sentiment", "bullish", 0.6),
        "smart_money": _ev("smart_money", "bullish", 0.9),
    }
    te = build_ticker_evidence(per_analyst, ticker="AAPL", tick_id="t",
                                recorded_at=_now(), weights=DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "bullish"
    assert te.aggregate.magnitude > 0.5


def test_all_bearish_aggregates_bearish():
    per_analyst = {a: _ev(a, "bearish", 0.7) for a in DEFAULT_ANALYST_WEIGHTS}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "bearish"


def test_split_low_confidence_falls_into_dead_zone_neutral():
    # 2 bullish at 0.1, 2 bearish at 0.1 → weighted sum = 0 → neutral
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.1),
        "fundamental": _ev("fundamental", "bullish", 0.1),
        "sentiment": _ev("sentiment", "bearish", 0.1),
        "smart_money": _ev("smart_money", "bearish", 0.1),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "neutral"


def test_one_strong_bullish_beats_three_weak_neutrals_outside_dead_zone():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.95),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "sentiment": _ev("sentiment", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    # weighted_sum = 0.95; magnitude = 0.95/4 = 0.2375 > dead_zone (0.15)
    assert te.aggregate.lean == "bullish"


def test_dead_zone_collapses_marginally_positive_to_neutral():
    # 0.5 weighted-confidence sum across 4 analysts → magnitude 0.125 < 0.15
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.5),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "sentiment": _ev("sentiment", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "neutral"


# ── Aggregate confidence (mean of contributing analysts) ──────────────────────


def test_aggregate_confidence_is_mean_of_contributing_analysts():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.4),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "sentiment": _ev("sentiment", "bullish", 0.8),
        "smart_money": _ev("smart_money", "bullish", 0.6),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    # mean(0.4, 0.6, 0.8, 0.6) = 0.6
    assert te.aggregate.confidence == pytest.approx(0.6, rel=0.01)


def test_aggregate_confidence_excludes_no_data_analysts():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.9),
        "fundamental": _ev("fundamental", "bullish", 0.9),
        "sentiment": _ev("sentiment", "bullish", 0.9),
        # smart_money missing → neutral-filled, is_no_data=True → excluded
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    # mean over the three contributing analysts only
    assert te.aggregate.confidence == pytest.approx(0.9, rel=0.01)


# ── Aggregate summary (rendered string) ───────────────────────────────────────


def test_aggregate_summary_describes_lean_breakdown():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.6),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "sentiment": _ev("sentiment", "bullish", 0.6),
        "smart_money": _ev("smart_money", "bearish", 0.6),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert "3" in te.aggregate.summary  # mentions the count
    assert "bullish" in te.aggregate.summary.lower()


# ── Disagreement (lives on aggregate) ─────────────────────────────────────────


def test_unanimous_agreement_disagreement_zero():
    per_analyst = {a: _ev(a, "bullish", 0.7) for a in DEFAULT_ANALYST_WEIGHTS}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.disagreement < 0.01


def test_max_split_disagreement_high():
    # Two strongly bullish, two strongly bearish
    per_analyst = {
        "technical": _ev("technical", "bullish", 1.0),
        "fundamental": _ev("fundamental", "bullish", 1.0),
        "sentiment": _ev("sentiment", "bearish", 1.0),
        "smart_money": _ev("smart_money", "bearish", 1.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.disagreement > 0.5


# ── Missing analyst neutral-fill ──────────────────────────────────────────────


def test_missing_analysts_neutral_filled():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.8),
        # fundamental, sentiment, smart_money all missing
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert set(te.per_analyst.keys()) == set(DEFAULT_ANALYST_WEIGHTS.keys())
    for missing in ("fundamental", "sentiment", "smart_money"):
        assert te.per_analyst[missing].verdict.lean == "neutral"
        assert te.per_analyst[missing].verdict.magnitude == 0.0
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
            tick_id="t",
            recorded_at=_now(),
            features={"is_no_data": 1.0},
            feature_warnings=[],
            verdict=AnalystVerdict(
                lean="bullish",  # ignored because is_no_data
                magnitude=0.9,
                confidence=0.9,
                rationale="x",
                key_factors=[],
                is_no_data=True,
            ),
        ),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    # smart_money's "bullish 0.9" must NOT inflate the aggregate because
    # is_no_data=True. weighted sum = 1.8 (3 × 0.6); magnitude = 1.8/4 = 0.45
    assert te.aggregate.lean == "bullish"
    assert te.aggregate.magnitude == pytest.approx(0.45, rel=0.01)


# ── weights snapshotting (top-level on TickerEvidence) ────────────────────────


def test_weights_snapshotted_at_top_level():
    per_analyst = {a: _ev(a, "bullish", 0.5) for a in DEFAULT_ANALYST_WEIGHTS}
    custom = {"technical": 2.0, "fundamental": 1.0, "sentiment": 0.5, "smart_money": 1.0}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), custom)
    assert te.weights == custom


# ── Per-analyst magnitude flows through unchanged ─────────────────────────────


def test_per_analyst_magnitude_preserved_in_dump():
    """Per-analyst `magnitude` must survive aggregation untouched — it's the
    substrate the future per-evidence-key weighting (B5) will learn against."""
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.7, magnitude=0.9),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "sentiment": _ev("sentiment", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.per_analyst["technical"].verdict.magnitude == pytest.approx(0.9)


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

from collections import Counter
from datetime import datetime
from statistics import mean, variance
from typing import Mapping

from contract.digest_defaults import DIRECTION_DEAD_ZONE
from contract.evidence import AnalystEvidence, AnalystName, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence

_ALL_ANALYSTS: tuple[AnalystName, ...] = ("technical", "fundamental", "sentiment", "smart_money")


def _lean_sign(lean: str) -> int:
    return {"bullish": 1, "bearish": -1, "neutral": 0}[lean]


def _fill_missing(
    per_analyst: Mapping[str, AnalystEvidence],
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
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
            tick_id=tick_id,
            recorded_at=recorded_at,
            features={},
            feature_warnings=[],
            verdict=AnalystVerdict(
                lean="neutral",
                magnitude=0.0,
                confidence=0.0,
                rationale="(no analyst output this tick)",
                key_factors=[],
                is_no_data=True,
            ),
        )
    return filled


def _weighted_signed_confidences(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> list[float]:
    """List of `weight × sign(lean) × confidence` per analyst, with
    is_no_data analysts contributing 0.0 (effectively ignored)."""
    out: list[float] = []
    for name in weights:
        ev = per_analyst.get(name)
        if ev is None or ev.verdict.is_no_data:
            out.append(0.0)
            continue
        sign = _lean_sign(ev.verdict.lean)
        out.append(weights[name] * sign * ev.verdict.confidence)
    return out


def _disagreement(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> float:
    """Variance of per-analyst signed confidences (no-data ignored), clamped
    to [0,1]. Signed confidences are in [-1,+1], so two analysts at +1 and -1
    produce variance 1.0."""
    signed: list[float] = []
    for name in weights:
        ev = per_analyst.get(name)
        if ev is None or ev.verdict.is_no_data:
            continue
        signed.append(_lean_sign(ev.verdict.lean) * ev.verdict.confidence)
    if len(signed) < 2:
        return 0.0
    return min(variance(signed), 1.0)


def _summary(per_analyst: Mapping[str, AnalystEvidence], weights: Mapping[str, float]) -> str:
    """Render a short human-readable cross-analyst summary, e.g.
    "3 bullish / 1 neutral / 0 bearish". Skips no_data analysts."""
    counts: Counter[str] = Counter()
    for name in weights:
        ev = per_analyst.get(name)
        if ev is None or ev.verdict.is_no_data:
            continue
        counts[ev.verdict.lean] += 1
    if sum(counts.values()) == 0:
        return "no contributing analysts"
    parts = [f"{counts.get(lean, 0)} {lean}" for lean in ("bullish", "neutral", "bearish")]
    return " / ".join(parts)


def _aggregate(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> AggregateVerdict:
    contributions = _weighted_signed_confidences(per_analyst, weights)
    weighted_sum = sum(contributions)
    total_weight = sum(weights.values()) or 1.0
    magnitude = abs(weighted_sum) / total_weight

    if magnitude < DIRECTION_DEAD_ZONE:
        lean = "neutral"
    elif weighted_sum > 0:
        lean = "bullish"
    else:
        lean = "bearish"

    contributing_confidences = [
        ev.verdict.confidence
        for name in weights
        for ev in (per_analyst.get(name),)
        if ev is not None and not ev.verdict.is_no_data
    ]
    confidence = mean(contributing_confidences) if contributing_confidences else 0.0

    return AggregateVerdict(
        lean=lean,  # type: ignore[arg-type]
        magnitude=min(magnitude, 1.0),
        confidence=min(max(confidence, 0.0), 1.0),
        disagreement=_disagreement(per_analyst, weights),
        summary=_summary(per_analyst, weights),
    )


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
    filled = _fill_missing(per_analyst, ticker, tick_id, recorded_at, weights)
    aggregate = _aggregate(filled, weights)

    return TickerEvidence(
        ticker=ticker,
        tick_id=tick_id,
        recorded_at=recorded_at,
        per_analyst=filled,
        aggregate=aggregate,
        weights=dict(weights),
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/unit/contract/test_digest.py -v`
Expected: PASS (13 tests).

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

Run: `.venv/Scripts/python -c "from contract.digest import build_ticker_evidence; from contract.evidence import AnalystEvidence, AnalystVerdict; from contract.ticker_evidence import TickerEvidence, AggregateVerdict; from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS, DIRECTION_DEAD_ZONE; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Append graphify delta entry**

Edit `graphify-out/graph_delta.md`. Append at the end:
```markdown

## YYYY-MM-DD — Phase 4 Plan A: contract scaffolding (additive)

Added `src/contract/` package. Pure new code, no consumer yet (Plan B will
start writing AnalystEvidence; Plan C will start reading it).

- New nodes: `src/contract/__init__.py`, `src/contract/evidence.py` (`AnalystVerdict`, `AnalystEvidence`),
  `src/contract/ticker_evidence.py` (`AggregateVerdict`, `TickerEvidence`),
  `src/contract/digest.py` (`build_ticker_evidence`, `_aggregate`, `_disagreement`, `_summary`, `_fill_missing`, `_lean_sign`, `_weighted_signed_confidences`),
  `src/contract/digest_defaults.py` (`DEFAULT_ANALYST_WEIGHTS`, `DIRECTION_DEAD_ZONE`).
- New edges: `digest.build_ticker_evidence --uses--> _fill_missing/_aggregate`;
  `digest._aggregate --uses--> _weighted_signed_confidences/_disagreement/_summary`;
  `digest --imports--> digest_defaults.DIRECTION_DEAD_ZONE`.
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

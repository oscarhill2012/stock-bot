# Phase 5 — Analyst Re-Categorisation + Deterministic-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-categorise the analyst pool from 4 (Technical, Fundamental, Sentiment, SmartMoney) to 5 (Technical, Fundamental, News, Social, SmartMoney); make Technical / Social / SmartMoney deterministic (`BaseAgent`) and keep Fundamental / News as narrowed-LLM (`LlmAgent`); migrate insider trades into Fundamental with a Form 4 deep-pull (footnotes, transaction codes, 10b5-1 flag, derivative table); land a closed-vocabulary, KB-readable evidence schema; close on a real-LLM surface-trace acceptance gate.

**Architecture:** `ParallelAgent("AnalystPool", ...)` grows from four to five children. Each analyst's fetch callback writes `state["{analyst}_data"]`. The deterministic analysts run `derive_<analyst>_verdict(features, h)` and write `state["{analyst}_verdicts"]` directly (no LLM round-trip); the two LLM analysts keep their `LlmAgent` shape. The shared `make_evidence_callback` after-callback (analyst-agnostic) builds `AnalystEvidence` from features + verdicts and writes `state["{analyst}_evidence"]`. Downstream digest, strategist, risk-gate, executor, memory-writer, and snapshotter are untouched. Heuristics live in `config/analyst_heuristics.json` and load once via `load_heuristics()` (cached). A `TraceWriter` opt-in via `state["_trace"]` captures one JSON file per tick at every pipeline boundary; `scripts/trace_tick.py` drives a single-ticker acceptance run.

**Tech Stack:** Python 3.12, Google ADK (`google-adk`), Pydantic 2.x (`ConfigDict(frozen=True, extra="forbid")`), SQLAlchemy 2.x (Mapped/mapped_column), pytest, `edgartools` (Form 4 SEC filings), Finnhub (news + social sentiment aggregates), Quiver (politician trades + 13F), Trading 212 paper broker, Gemini 2.5 (`gemini-2.5-pro` for strategist, `gemini-2.5-flash-lite` for analyst LLMs).

---

## Reference Reading (before starting any task)

These are the load-bearing docs. Read them in this order:

1. `docs/Phase5-analyst-refine/spec.md` — the design spec this plan implements. The "Rollout" section names each step; this plan elaborates it.
2. `CLAUDE.md` (project root) and `.claude/CLAUDE.md` — house style, shell conventions, graphify workflow.
3. `graphify-out/GRAPH_REPORT.md` + `graphify-out/graph_delta.md` — orientation map. If `graph_delta.md` already lists "no changes since last `/graphify . --update`", the report is fresh.
4. `src/contract/evidence.py` — `AnalystEvidence` / `AnalystVerdict` / `AnalystName` Literal. The contract the plan does *not* break.
5. `src/agents/analysts/_common.py` — `make_evidence_callback`. Pattern every analyst uses; do not modify.
6. `src/orchestrator/pipeline.py` — `_build_analyst_pool` and `build_pipeline`. Composition point.
7. `src/orchestrator/persistence.py` — `AnalystEvidenceRow`. Composite index lands here in step 12.
8. `src/lifecycle/initialise.py` — `_check_env` / `_check_live_tables_empty` / `_check_broker_cash`. Pattern for `_check_heuristics()` in step 1.
9. `config/README.md` and `config/data.json` — config conventions.
10. `src/data/config.py::get_config()` — `lru_cache(maxsize=1)` loader pattern to mirror in `heuristics.py`.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `config/analyst_heuristics.json` | **Create** | Thresholds + vocabularies for all five analysts; one JSON; loaded once at boot. |
| `config/README.md` | Modify | Document the new file + risk-tag suffix scheme. |
| `src/agents/analysts/heuristics.py` | **Create** | Frozen Pydantic models for each heuristics section + `load_heuristics()` (cached). |
| `src/contract/evidence.py` | Modify | Expand `AnalystName` Literal: add `"news"` + `"social"`, drop `"sentiment"`. |
| `src/data/models/trades.py` | Modify | Extend `InsiderTrade`; add `InsiderDerivativeTrade`; add `Form4Bundle`. |
| `src/data/models/__init__.py` | Modify | Re-export `InsiderDerivativeTrade` + `Form4Bundle`. |
| `src/data/providers/insider_trades/edgar.py` | Modify | Parse footnotes, transaction codes, 10b5-1 flag, derivative table; return `Form4Bundle`. |
| `src/agents/analysts/smart_money/fetch.py` | Modify | Drop insider; keep politicians + notable holders. |
| `src/agents/analysts/smart_money/agent.py` | Modify | Replace `LlmAgent` with `BaseAgent` deterministic body. |
| `src/agents/analysts/smart_money/prompts.py` | **Delete** | No prompt needed for deterministic analyst. |
| `src/contract/extractors/smart_money.py` | Modify | Closed-vocab cleanup (insider tags already gone from features); add `derive_smart_money_verdict`. |
| `src/agents/analysts/fundamental/fetch.py` | Modify | Fetch `stats/` + `filings/` + `insider_trades/`. |
| `src/agents/analysts/fundamental/agent.py` | Modify | Inject `FundamentalVocabulary`; closed-vocab schema. |
| `src/agents/analysts/fundamental/prompts.py` | Modify | New template with prose + insider supplement + vocab placeholders. |
| `src/contract/extractors/fundamental.py` | Modify | Add insider columns + filings-derived numeric columns. |
| `src/agents/analysts/sentiment/` | **Delete** (move) | Whole directory renamed to `news/`. |
| `src/agents/analysts/news/` | **Create** (move) | Renamed from `sentiment/`; news-only fetch + LLM. |
| `src/agents/analysts/news/agent.py` | Modify | Rename class `SentimentAnalyst` → `NewsAnalyst`; state keys; output_key. |
| `src/agents/analysts/news/fetch.py` | Modify | Drop social_sentiment; news-only. |
| `src/agents/analysts/news/prompts.py` | Modify | Inject `NewsVocabulary`. |
| `src/contract/extractors/sentiment.py` | **Delete** (move) | Renamed to `news.py`. |
| `src/contract/extractors/news.py` | **Create** (move) | Same logic as old sentiment extractor; scoped to news. |
| `src/agents/analysts/social/` | **Create** | New deterministic analyst directory. |
| `src/agents/analysts/social/__init__.py` | **Create** | Package marker. |
| `src/agents/analysts/social/agent.py` | **Create** | `BaseAgent`-based deterministic Social analyst. |
| `src/agents/analysts/social/fetch.py` | **Create** | Pull `social_sentiment/` only. |
| `src/contract/extractors/social.py` | **Create** | `extract_social_features` + `derive_social_verdict`. |
| `src/agents/analysts/technical/agent.py` | Modify | Replace `LlmAgent` with `BaseAgent` deterministic body. |
| `src/agents/analysts/technical/prompts.py` | **Delete** | No prompt needed. |
| `src/contract/extractors/technical.py` | Modify | Add `derive_technical_verdict` next to existing extractor. |
| `src/orchestrator/pipeline.py` | Modify | `_build_analyst_pool` grows to five children + threads heuristics. |
| `src/orchestrator/persistence.py` | Modify | Composite `Index('ix_analyst_evidence_lookup', analyst, ticker, recorded_at)`. |
| `src/lifecycle/initialise.py` | Modify | Add `_check_heuristics()` call to `initialise()`. |
| `src/observability/__init__.py` | **Create** | Package marker. |
| `src/observability/trace.py` | **Create** | `TraceWriter` + `_trace_maybe(...)` no-op hook helper. |
| `scripts/trace_tick.py` | **Create** | CLI entry to run one trace tick. |
| `.gitignore` | Modify | Add `docs/surface-traces/`. |
| `docs/Phase5-analyst-refine/analyst-llm-narrowing.md` | **Delete** | Stale pre-pivot plan; superseded by this file. |
| Test files (per task) | **Create / Modify** | See per-task `Test:` entries. |

---

## Cross-Cutting Conventions

These apply to **every** task. Re-read them before each task; they are the rules the task list does not repeat.

1. **British English everywhere** — `colour`, `behaviour`, `analyse`, `optimise`, `organisation`. Includes docstrings, comments, log strings, error messages, commit subjects. The codebase already uses British English in its current modules.
2. **Docstrings on every new function.** Describe purpose, parameters, return value. Inline comments explain non-trivial logic.
3. **Whitespace for legibility.** Blank lines separate logical blocks.
4. **TDD.** Each task is "write failing test → run it to confirm it fails → minimal implementation → run it to confirm it passes → commit." Do not skip the *run the failing test* step — it catches collector-level errors and validates the test actually exercises the new code.
5. **One commit per task.** Subject line starts `feat(phase5): ...` or `refactor(phase5): ...` or `test(phase5): ...`. Body is a single short paragraph.
6. **Never `git add` anything under `graphify-out/`.** The path is gitignored; the helper graph is local-only.
7. **No `cd "/home/oscarhill2012/Documents/Repository/StockBot"` prefixes.** The Bash tool already runs in the project root. Run `pytest`, `ruff`, `git` commands directly.
8. **`PYTHONPATH=src` for ad-hoc invocations.** `pytest` already resolves through `pyproject.toml`; CLI scripts use `PYTHONPATH=src python -m scripts.<name>`.
9. **Ruff after every task.** Run `.venv/bin/python -m ruff check src/ tests/` before each commit; fix issues in the same commit. Don't `--fix` blindly across the repo; fix only the diff.
10. **Type hints on all new code.** Use `from __future__ import annotations` at the top of every new module.
11. **Frozen Pydantic models.** New models add `model_config = ConfigDict(frozen=True, extra="forbid")` unless the existing peer class doesn't.
12. **No graphify-out edits during execution.** Append `graph_delta.md` only at the very end (after step 14), one combined entry summarising all phase-5 structural changes. Bullet new/changed/removed nodes and edges per the existing format. Do *not* commit the delta.
13. **No "TODO" comments.** If a follow-up exists, it goes in `docs/superpowers/backlog.md` (already done for B12/B13/B14/B15/B16).
14. **Tests live under `tests/unit/` or `tests/integration/`.** Mirror the source tree. Integration tests carry `@pytest.mark.integration`.
15. **Commit only what the task creates/edits.** No drive-by formatting; no `git add -A`. Use explicit paths.

---

## Task 1 — Config + Heuristics Models

**Spec reference:** "Configuration" section + Rollout step 1.

**Files:**
- Create: `config/analyst_heuristics.json`
- Create: `src/agents/analysts/heuristics.py`
- Modify: `config/README.md`
- Modify: `src/lifecycle/initialise.py` (add `_check_heuristics()` call)
- Test: `tests/unit/test_analyst_heuristics.py` (create)
- Test: `tests/unit/test_lifecycle_initialise.py` (extend — add one failure-path case)

- [ ] **Step 1: Write the failing test for the heuristics loader**

Create `tests/unit/test_analyst_heuristics.py`:

```python
"""Tier-1 unit tests for the analyst-heuristics loader.

Validates schema correctness, range enforcement, and that the loader is
cached (lru_cache) so changing the file after first load does not refresh
the in-process value.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.analysts.heuristics import (
    AnalystHeuristics,
    FundamentalVocabulary,
    NewsVocabulary,
    SocialHeuristics,
    SmartMoneyHeuristics,
    TechnicalHeuristics,
    load_heuristics,
)


def _valid_payload() -> dict:
    """Return a fully populated, valid heuristics payload."""
    return {
        "technical": {
            "rsi_overbought": 75, "rsi_oversold": 25,
            "pct_change_momentum_scale": 4.0,
            "vol_ratio_breakout": 1.5, "vol_ratio_dry_up": 0.7,
            "atr_high_volatility_pct": 5.0, "near_52w_extreme_pct": 5.0,
            "confidence_base": 0.5, "confidence_boost_step": 0.2,
            "confidence_penalty_step": 0.3, "magnitude_cap": 1.0,
        },
        "social": {
            "score_neutral_band": 0.05, "score_to_magnitude_scale": 2.0,
            "high_volume_mentions": 200, "high_volume_magnitude_boost": 0.15,
            "confidence_volume_floor": 30,
            "platform_disagreement_threshold": 0.3,
            "confidence_base": 0.4, "confidence_boost_step": 0.2,
            "confidence_penalty_step": 0.2, "magnitude_cap": 1.0,
        },
        "smart_money": {
            "multi_filer_min_count": 3, "high_activity_trade_count": 5,
            "lone_filer_confidence_floor": 0.1,
            "consensus_confidence_ceiling": 0.9, "magnitude_cap": 1.0,
        },
        "fundamental_vocabulary": {
            "guidance": ["raised", "maintained", "lowered", "none"],
            "tone": ["confident", "cautious", "defensive", "mixed"],
            "risks": ["regulatory", "litigation", "going_concern"],
            "insider_signals": ["cluster_buying", "cluster_selling", "mixed"],
        },
        "news_vocabulary": {
            "catalysts": ["earnings", "guidance", "none"],
            "novelty": ["high", "medium", "low"],
            "direction": ["positive", "negative", "mixed", "none"],
        },
        "golden_set": {"min_direction_agreement_pct": 70},
    }


def test_valid_payload_parses() -> None:
    """A complete, valid payload validates without error."""
    h = AnalystHeuristics.model_validate(_valid_payload())
    assert isinstance(h.technical, TechnicalHeuristics)
    assert isinstance(h.social, SocialHeuristics)
    assert isinstance(h.smart_money, SmartMoneyHeuristics)
    assert isinstance(h.fundamental_vocabulary, FundamentalVocabulary)
    assert isinstance(h.news_vocabulary, NewsVocabulary)


def test_rsi_overbought_out_of_range_rejected() -> None:
    """RSI overbought above 100 must raise ValidationError."""
    payload = _valid_payload()
    payload["technical"]["rsi_overbought"] = 150
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_confidence_base_out_of_range_rejected() -> None:
    """Confidence base outside [0, 1] must raise ValidationError."""
    payload = _valid_payload()
    payload["social"]["confidence_base"] = 1.5
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_missing_section_rejected() -> None:
    """Omitting a top-level section must raise ValidationError."""
    payload = _valid_payload()
    del payload["social"]
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_unknown_field_rejected() -> None:
    """Unknown keys must raise (extra='forbid')."""
    payload = _valid_payload()
    payload["technical"]["unknown_knob"] = 42
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_load_heuristics_reads_config_file(tmp_path: Path, monkeypatch) -> None:
    """`load_heuristics()` reads the on-disk JSON and validates it."""
    cfg = tmp_path / "analyst_heuristics.json"
    cfg.write_text(json.dumps(_valid_payload()))
    monkeypatch.setenv("ANALYST_HEURISTICS_PATH", str(cfg))
    load_heuristics.cache_clear()
    h = load_heuristics()
    assert h.technical.rsi_overbought == 75
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_analyst_heuristics.py -v
```
Expected: `ImportError` / collection error — module `agents.analysts.heuristics` does not exist yet.

- [ ] **Step 3: Create `config/analyst_heuristics.json`**

Write the file with exactly the payload in the spec's "Configuration" section (all five sections + `golden_set`). Use UK-spelt comments only if comments are added (JSON has none here).

```json
{
  "technical": {
    "rsi_overbought": 75,
    "rsi_oversold": 25,
    "pct_change_momentum_scale": 4.0,
    "vol_ratio_breakout": 1.5,
    "vol_ratio_dry_up": 0.7,
    "atr_high_volatility_pct": 5.0,
    "near_52w_extreme_pct": 5.0,
    "confidence_base": 0.5,
    "confidence_boost_step": 0.2,
    "confidence_penalty_step": 0.3,
    "magnitude_cap": 1.0
  },
  "social": {
    "score_neutral_band": 0.05,
    "score_to_magnitude_scale": 2.0,
    "high_volume_mentions": 200,
    "high_volume_magnitude_boost": 0.15,
    "confidence_volume_floor": 30,
    "platform_disagreement_threshold": 0.3,
    "confidence_base": 0.4,
    "confidence_boost_step": 0.2,
    "confidence_penalty_step": 0.2,
    "magnitude_cap": 1.0
  },
  "smart_money": {
    "multi_filer_min_count": 3,
    "high_activity_trade_count": 5,
    "lone_filer_confidence_floor": 0.1,
    "consensus_confidence_ceiling": 0.9,
    "magnitude_cap": 1.0
  },
  "fundamental_vocabulary": {
    "guidance":  ["raised", "maintained", "lowered", "none"],
    "tone":      ["confident", "cautious", "defensive", "mixed"],
    "risks":     ["regulatory", "litigation", "cybersecurity", "supply_chain",
                  "macro", "competition", "key_person", "debt_refinance",
                  "going_concern", "guidance_change", "customer_concentration"],
    "insider_signals": ["cluster_buying", "cluster_selling",
                        "planned_sale_dominant", "discretionary_sale_dominant",
                        "option_exercise_dump", "option_exercise_hold",
                        "gift_disposal", "mixed"]
  },
  "news_vocabulary": {
    "catalysts": ["earnings", "guidance", "m_and_a", "regulatory",
                  "product_launch", "legal", "macro", "downgrade",
                  "upgrade", "none"],
    "novelty":   ["high", "medium", "low"],
    "direction": ["positive", "negative", "mixed", "none"]
  },
  "golden_set": {
    "min_direction_agreement_pct": 70
  }
}
```

- [ ] **Step 4: Create `src/agents/analysts/heuristics.py`**

```python
"""Typed loader for `config/analyst_heuristics.json`.

Models every section of the heuristics file as a frozen Pydantic class so
out-of-range or unknown values fail at boot rather than at tick 1. The
`load_heuristics()` accessor is cached via `lru_cache(maxsize=1)` — same
pattern as `src/data/config.py::get_config()`. Hot-reload is intentionally
not supported (see spec §Configuration).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# Default path relative to repo root. Overridable via env var for tests.
_DEFAULT_PATH = Path("config/analyst_heuristics.json")


class _Frozen(BaseModel):
    """Common config — frozen, no unknown keys, no defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TechnicalHeuristics(_Frozen):
    """Thresholds for the deterministic technical verdict."""

    rsi_overbought: float            = Field(ge=50.0, le=100.0)
    rsi_oversold: float              = Field(ge=0.0, le=50.0)
    pct_change_momentum_scale: float = Field(gt=0.0)
    vol_ratio_breakout: float        = Field(gt=1.0)
    vol_ratio_dry_up: float          = Field(gt=0.0, lt=1.0)
    atr_high_volatility_pct: float   = Field(gt=0.0)
    near_52w_extreme_pct: float      = Field(gt=0.0)
    confidence_base: float           = Field(ge=0.0, le=1.0)
    confidence_boost_step: float     = Field(ge=0.0, le=1.0)
    confidence_penalty_step: float   = Field(ge=0.0, le=1.0)
    magnitude_cap: float             = Field(gt=0.0, le=1.0)


class SocialHeuristics(_Frozen):
    """Thresholds for the deterministic social verdict."""

    score_neutral_band: float               = Field(ge=0.0, le=1.0)
    score_to_magnitude_scale: float         = Field(gt=0.0)
    high_volume_mentions: int               = Field(gt=0)
    high_volume_magnitude_boost: float      = Field(ge=0.0, le=1.0)
    confidence_volume_floor: int            = Field(ge=0)
    platform_disagreement_threshold: float  = Field(ge=0.0, le=1.0)
    confidence_base: float                  = Field(ge=0.0, le=1.0)
    confidence_boost_step: float            = Field(ge=0.0, le=1.0)
    confidence_penalty_step: float          = Field(ge=0.0, le=1.0)
    magnitude_cap: float                    = Field(gt=0.0, le=1.0)


class SmartMoneyHeuristics(_Frozen):
    """Thresholds for the deterministic smart-money verdict."""

    multi_filer_min_count: int          = Field(ge=1)
    high_activity_trade_count: int      = Field(ge=1)
    lone_filer_confidence_floor: float  = Field(ge=0.0, le=1.0)
    consensus_confidence_ceiling: float = Field(ge=0.0, le=1.0)
    magnitude_cap: float                = Field(gt=0.0, le=1.0)


class FundamentalVocabulary(_Frozen):
    """Closed-vocabulary tag lists for the narrowed Fundamental LLM."""

    guidance: list[str] = Field(min_length=1)
    tone:     list[str] = Field(min_length=1)
    risks:    list[str] = Field(min_length=1)
    insider_signals: list[str] = Field(min_length=1)


class NewsVocabulary(_Frozen):
    """Closed-vocabulary tag lists for the narrowed News LLM."""

    catalysts: list[str] = Field(min_length=1)
    novelty:   list[str] = Field(min_length=1)
    direction: list[str] = Field(min_length=1)


class GoldenSetConfig(_Frozen):
    """Tunables for the golden-set sanity test."""

    min_direction_agreement_pct: int = Field(ge=0, le=100)


class AnalystHeuristics(_Frozen):
    """Top-level config object — one per JSON file."""

    technical: TechnicalHeuristics
    social: SocialHeuristics
    smart_money: SmartMoneyHeuristics
    fundamental_vocabulary: FundamentalVocabulary
    news_vocabulary: NewsVocabulary
    golden_set: GoldenSetConfig


@lru_cache(maxsize=1)
def load_heuristics() -> AnalystHeuristics:
    """Read `config/analyst_heuristics.json` (or `ANALYST_HEURISTICS_PATH`) and validate.

    Raises ``pydantic.ValidationError`` on malformed content and
    ``FileNotFoundError`` if the file does not exist. Cached for the
    lifetime of the process; clear via ``load_heuristics.cache_clear()``
    in tests.
    """
    path = Path(os.environ.get("ANALYST_HEURISTICS_PATH", str(_DEFAULT_PATH)))
    raw = json.loads(path.read_text())
    return AnalystHeuristics.model_validate(raw)
```

- [ ] **Step 5: Run the heuristics tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_analyst_heuristics.py -v
```
Expected: all six tests pass.

- [ ] **Step 6: Write failing test for `_check_heuristics()` lifecycle hook**

Append to `tests/unit/test_lifecycle_initialise.py` (file already exists):

```python
def test_check_heuristics_raises_on_malformed_config(monkeypatch, tmp_path):
    """A malformed `analyst_heuristics.json` must surface at boot via initialise()."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    monkeypatch.setenv("ANALYST_HEURISTICS_PATH", str(bad))

    from agents.analysts.heuristics import load_heuristics
    from lifecycle.initialise import _check_heuristics  # imported here so module-time errors surface

    load_heuristics.cache_clear()
    with pytest.raises(Exception):  # JSONDecodeError or ValidationError
        _check_heuristics()
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_lifecycle_initialise.py::test_check_heuristics_raises_on_malformed_config -v
```
Expected: `ImportError` — `_check_heuristics` is not defined yet.

- [ ] **Step 7: Add `_check_heuristics()` to lifecycle**

Edit `src/lifecycle/initialise.py`:
- Add a top-level function near the other `_check_*` helpers:

```python
def _check_heuristics() -> None:
    """Fail-fast load of analyst heuristics. Surfaces JSON errors at boot."""
    # Imported here so the lifecycle module does not pull agents on import.
    from agents.analysts.heuristics import load_heuristics

    load_heuristics()  # raises ValidationError if malformed
```

- Inside `initialise(...)`, call `_check_heuristics()` immediately after `_check_env()`.

- [ ] **Step 8: Run the lifecycle test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_lifecycle_initialise.py -v
```
Expected: existing tests still pass + new test passes.

- [ ] **Step 9: Update `config/README.md`**

Append a section documenting `analyst_heuristics.json`: one short paragraph per section (technical / social / smart_money / fundamental_vocabulary / news_vocabulary / golden_set), the suffix scheme for risk tags (`<risk>:<value>[_added|_removed|_intensified]`), and a note that values load once at boot (no hot-reload).

- [ ] **Step 10: Lint and commit**

Run:
```bash
.venv/bin/python -m ruff check src/agents/analysts/heuristics.py src/lifecycle/initialise.py tests/unit/test_analyst_heuristics.py tests/unit/test_lifecycle_initialise.py
```
Fix any lint errors. Then:

```bash
git add config/analyst_heuristics.json config/README.md \
        src/agents/analysts/heuristics.py src/lifecycle/initialise.py \
        tests/unit/test_analyst_heuristics.py tests/unit/test_lifecycle_initialise.py
git commit -m "feat(phase5): add analyst-heuristics config + loader + lifecycle check"
```

---

## Task 2 — Expand `AnalystName` Literal

**Spec reference:** "Contract invariants — what does NOT change" (the one unavoidable change) + Rollout step 2.

**Files:**
- Modify: `src/contract/evidence.py`
- Test: `tests/unit/test_analyst_name_literal.py` (create)
- Test: `tests/unit/test_evidence_row_persistence.py` (extend)

- [ ] **Step 1: Write the failing literal test**

Create `tests/unit/test_analyst_name_literal.py`:

```python
"""The AnalystName Literal must include 'news' + 'social' and exclude 'sentiment'."""
from __future__ import annotations

from typing import get_args

from contract.evidence import AnalystName


def test_analyst_name_includes_news_and_social() -> None:
    """Post-Phase-5: 'news' and 'social' are first-class analyst names."""
    members = set(get_args(AnalystName))
    assert "news" in members
    assert "social" in members


def test_analyst_name_excludes_sentiment() -> None:
    """Post-Phase-5: 'sentiment' no longer exists as an analyst name."""
    members = set(get_args(AnalystName))
    assert "sentiment" not in members


def test_analyst_name_full_membership() -> None:
    """The full set is exactly the five Phase-5 analysts."""
    members = set(get_args(AnalystName))
    assert members == {"technical", "fundamental", "news", "social", "smart_money"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_analyst_name_literal.py -v
```
Expected: fails — current literal is `"technical" | "fundamental" | "sentiment" | "smart_money"`.

- [ ] **Step 3: Update the Literal**

Edit `src/contract/evidence.py:23`:

```python
AnalystName = Literal["technical", "fundamental", "news", "social", "smart_money"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_analyst_name_literal.py -v
```
Expected: all three tests pass.

- [ ] **Step 5: Extend round-trip persistence test**

Append to `tests/unit/test_evidence_row_persistence.py`:

```python
@pytest.mark.parametrize("analyst", ["news", "social"])
def test_evidence_row_round_trip_for_new_analyst_names(analyst, sqlite_session):
    """`news` and `social` analyst rows round-trip cleanly through the DB."""
    from contract.evidence import AnalystEvidence, AnalystVerdict
    from datetime import UTC, datetime

    ev = AnalystEvidence(
        ticker="AAPL",
        analyst=analyst,
        tick_id="tick-001",
        recorded_at=datetime.now(tz=UTC),
        features={"score": 0.42},
        feature_warnings=[],
        verdict=AnalystVerdict(
            lean="bullish", magnitude=0.5, confidence=0.6,
            rationale="round-trip", key_factors=["positive"],
            is_no_data=False,
        ),
    )
    # Persist via the same path the production EvidenceWriter uses.
    from orchestrator.persistence import save_analyst_evidence
    save_analyst_evidence(sqlite_session, [ev.model_dump(mode="json")])
    sqlite_session.commit()

    from orchestrator.persistence import AnalystEvidenceRow
    rows = sqlite_session.query(AnalystEvidenceRow).filter_by(ticker="AAPL").all()
    assert any(r.analyst == analyst for r in rows)
```

(`sqlite_session` is the existing fixture in this file. If `save_analyst_evidence` has a different signature, mirror existing tests in the same file.)

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_evidence_row_persistence.py -v
```
Expected: passes (the column is plain `String`, no schema migration needed).

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/python -m ruff check src/contract/evidence.py tests/unit/test_analyst_name_literal.py tests/unit/test_evidence_row_persistence.py
git add src/contract/evidence.py tests/unit/test_analyst_name_literal.py tests/unit/test_evidence_row_persistence.py
git commit -m "feat(phase5): expand AnalystName literal — add news + social, drop sentiment"
```

> **Note for the next steps:** the rest of the codebase still references `"sentiment"` in places (state keys, fetch callbacks, agent class names). Those references will be updated in **Task 6** when the directory is renamed. Until then, type-checking on those modules may surface warnings; that is expected and resolved by Task 6.

---

## Task 3 — Insider Provider Expansion (Form 4 Deep-Pull)

**Spec reference:** "Insider expansion (Form 4 deep-pull)" + Rollout step 3.

**Files:**
- Modify: `src/data/models/trades.py`
- Modify: `src/data/models/__init__.py`
- Modify: `src/data/providers/insider_trades/edgar.py`
- Test: `tests/unit/test_insider_model_roundtrip.py` (create)
- Test: `tests/unit/test_form4_parser.py` (create)

- [ ] **Step 1: Write the failing model round-trip test**

Create `tests/unit/test_insider_model_roundtrip.py`:

```python
"""Round-trip and rejection tests for the extended insider trade models."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from data.models import Form4Bundle, InsiderDerivativeTrade, InsiderTrade


def _common_kwargs() -> dict:
    """Minimum required kwargs for an InsiderTrade."""
    return {
        "ticker": "AAPL",
        "insider_name": "Tim Cook",
        "insider_title": "CEO",
        "side": "buy",
        "shares": 1000.0,
        "price_per_share": 175.5,
        "transaction_date": date(2026, 5, 1),
        "filed_at": datetime(2026, 5, 2, tzinfo=UTC),
        "form_type": "4",
    }


def test_insider_trade_round_trip_with_new_fields() -> None:
    """InsiderTrade preserves transaction_code, is_10b5_1, footnote round-trip."""
    payload = _common_kwargs() | {
        "transaction_code": "P",
        "is_10b5_1": True,
        "footnote": "Sale pursuant to Rule 10b5-1 plan adopted 2025-12-01.",
    }
    t = InsiderTrade.model_validate(payload)
    assert t.transaction_code == "P"
    assert t.is_10b5_1 is True
    assert t.footnote is not None
    assert t.model_dump(mode="json")["transaction_code"] == "P"


def test_insider_trade_defaults_new_fields_to_none_or_false() -> None:
    """Omitting new fields keeps backwards-compatible defaults."""
    t = InsiderTrade.model_validate(_common_kwargs())
    assert t.transaction_code is None
    assert t.is_10b5_1 is False
    assert t.footnote is None


def test_insider_trade_rejects_unknown_field() -> None:
    """extra='forbid' rejects stray keys."""
    payload = _common_kwargs() | {"some_unknown_field": 1}
    with pytest.raises(ValidationError):
        InsiderTrade.model_validate(payload)


def test_insider_derivative_trade_round_trip() -> None:
    """InsiderDerivativeTrade round-trips strike, type, footnote."""
    payload = {
        "ticker": "MSFT", "insider_name": "Satya Nadella",
        "insider_title": "CEO", "side": "buy",
        "derivative_type": "option",
        "underlying_shares": 500.0, "strike_price": 200.0,
        "transaction_date": date(2026, 4, 12),
        "filed_at": datetime(2026, 4, 13, tzinfo=UTC),
        "transaction_code": "M", "is_10b5_1": False,
        "footnote": "Exercise of stock option granted 2020-01-01.",
    }
    t = InsiderDerivativeTrade.model_validate(payload)
    assert t.derivative_type == "option"
    assert t.strike_price == 200.0


def test_form4_bundle_wraps_both_lists() -> None:
    """Form4Bundle holds parallel trades + derivatives lists."""
    bundle = Form4Bundle(trades=[], derivatives=[])
    assert bundle.trades == []
    assert bundle.derivatives == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_insider_model_roundtrip.py -v
```
Expected: `ImportError` — `Form4Bundle` and `InsiderDerivativeTrade` do not exist.

- [ ] **Step 3: Extend `src/data/models/trades.py`**

Add `transaction_code`, `is_10b5_1`, `footnote` to `InsiderTrade`. Then append `InsiderDerivativeTrade` and `Form4Bundle`:

```python
class InsiderTrade(BaseModel):
    """One Form 4 common-stock transaction row.

    Captures both the structured fields the deterministic extractor consumes
    and the narrative supplement (footnote + transaction code + 10b5-1 flag)
    that lets the Fundamental LLM separate mechanical sales from
    discretionary ones.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    insider_name: str
    insider_title: str | None = None
    side: TradeSide
    shares: float
    price_per_share: float | None = None
    transaction_date: date
    filed_at: datetime
    form_type: str

    # Narrative + categorical supplement added in Phase 5.
    transaction_code: str | None = None   # P/S/A/M/F/G/D/X — Form 4 Table I col 3
    is_10b5_1: bool = False               # From the form-level flag or footnote regex
    footnote: str | None = None           # Free-text footnote on the row (prose)


class InsiderDerivativeTrade(BaseModel):
    """One Form 4 derivative-securities transaction row.

    Option exercises, option grants, RSU vestings, warrant transactions.
    Strike + underlying-shares + footnote together describe whether a
    transaction is dilutive vesting, an in-the-money exercise, an
    exercise-and-hold (bullish), or an exercise-and-dump.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    insider_name: str
    insider_title: str | None = None
    side: TradeSide
    derivative_type: str | None = None    # "option", "rsu", "warrant", "performance_award"
    underlying_shares: float
    strike_price: float | None = None
    transaction_date: date
    filed_at: datetime
    transaction_code: str | None = None
    is_10b5_1: bool = False
    footnote: str | None = None


class Form4Bundle(BaseModel):
    """One ticker's parsed Form 4 contents — both transaction tables."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trades: list[InsiderTrade] = Field(default_factory=list)
    derivatives: list[InsiderDerivativeTrade] = Field(default_factory=list)
```

Update imports at the top of the module so `Field` is available if it isn't already.

- [ ] **Step 4: Re-export the new models from `src/data/models/__init__.py`**

Add `InsiderDerivativeTrade` and `Form4Bundle` to the `from .trades import (...)` block and to `__all__`.

- [ ] **Step 5: Run the round-trip test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_insider_model_roundtrip.py -v
```
Expected: all five tests pass.

- [ ] **Step 6: Write the failing Form-4 parser test**

Create `tests/unit/test_form4_parser.py`. Use synthetic dict fixtures shaped like what `edgartools` returns — do not hit the network.

```python
"""Tests for the extended _parse_form4 — footnote + code + 10b5-1 + derivatives."""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest


def _fake_form4_obj():
    """Construct a minimal SimpleNamespace mirroring the edgartools shape.

    The real edgartools API exposes `.common_stock_purchases`,
    `.common_stock_sales`, `.derivative_securities`, `.footnotes` (mapping
    of footnote id → text), and `.equity_swap_or_planned_sale`. The parser
    consumes attribute access, so SimpleNamespace is sufficient.
    """
    return SimpleNamespace(
        common_stock_purchases=[
            {
                "shares": 1000.0,
                "price_per_share": 175.5,
                "transaction_date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "transaction_code": "P",
                "footnote_ids": ["F1"],
            },
        ],
        common_stock_sales=[
            {
                "shares": 500.0,
                "price_per_share": 180.0,
                "transaction_date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "transaction_code": "S",
                "footnote_ids": ["F2"],
            },
        ],
        derivative_securities=[
            {
                "underlying_shares": 200.0,
                "strike_price": 100.0,
                "derivative_type": "option",
                "transaction_date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "transaction_code": "M",
                "side": "buy",
                "footnote_ids": [],
            },
        ],
        footnotes={
            "F1": "Open-market purchase; not pursuant to any plan.",
            "F2": "Sale effected pursuant to a Rule 10b5-1 trading plan.",
        },
        equity_swap_or_planned_sale=False,
        filed_at="2026-05-02T13:30:00Z",
        ticker="AAPL",
        form_type="4",
    )


def test_parse_form4_extracts_footnote_and_code():
    """A common-stock purchase row picks up its footnote text and transaction code."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_obj())
    purchases = [t for t in bundle.trades if t.side == "buy"]
    assert len(purchases) == 1
    assert purchases[0].transaction_code == "P"
    assert "Open-market" in (purchases[0].footnote or "")
    assert purchases[0].is_10b5_1 is False


def test_parse_form4_detects_10b5_1_via_footnote_regex():
    """A sale row carrying 10b5-1 footnote sets `is_10b5_1` even if form flag is False."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_obj())
    sales = [t for t in bundle.trades if t.side == "sell"]
    assert len(sales) == 1
    assert sales[0].is_10b5_1 is True


def test_parse_form4_extracts_derivative_row():
    """Derivative table produces an InsiderDerivativeTrade with strike + type."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_obj())
    assert len(bundle.derivatives) == 1
    d = bundle.derivatives[0]
    assert d.derivative_type == "option"
    assert d.strike_price == 100.0
    assert d.transaction_code == "M"
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_form4_parser.py -v
```
Expected: fails — `_parse_form4` either returns the old shape or the new helpers don't exist.

- [ ] **Step 7: Extend `src/data/providers/insider_trades/edgar.py`**

Implementation guide (preserve existing `_iter_rows` / `_row_get` / `_coerce_date` / `_to_float` helpers — extend, don't rewrite):

```python
import re
_TEN_B5_1_RE = re.compile(r"10b5[-\s]?1", re.IGNORECASE)


def _extract_footnote(row: dict, form4: object) -> str | None:
    """Resolve a row's footnote_ids list against the form-level footnotes map.

    Joins multiple footnotes with ' | '. Returns None when no footnote ids
    are present.
    """
    ids = row.get("footnote_ids") or []
    fmap = getattr(form4, "footnotes", {}) or {}
    parts = [fmap[fid] for fid in ids if fid in fmap]
    return " | ".join(parts) if parts else None


def _is_planned_sale(form4: object, footnote: str | None) -> bool:
    """Derive the 10b5-1 flag from the form flag, falling back to the footnote regex."""
    if bool(getattr(form4, "equity_swap_or_planned_sale", False)):
        return True
    if footnote and _TEN_B5_1_RE.search(footnote):
        return True
    return False
```

Rewrite `_parse_form4(form4)` to:
1. Iterate `common_stock_purchases` → build `InsiderTrade(side="buy", ...)` with `transaction_code`, `footnote`, `is_10b5_1` populated.
2. Iterate `common_stock_sales` → same with `side="sell"`.
3. Iterate `derivative_securities` → build `InsiderDerivativeTrade` with `derivative_type`, `strike_price`, `underlying_shares`, `transaction_code`, `footnote`, `is_10b5_1`.
4. Return `Form4Bundle(trades=[...], derivatives=[...])`.

Update the call site (`_extract` and the public fetch function in the same module) so the public API now returns `Form4Bundle`. If the existing public fetch is shaped `def fetch(...) -> list[InsiderTrade]:`, change the return type to `Form4Bundle` and update its body. Callers will be touched in Task 5.

- [ ] **Step 8: Run the parser tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_form4_parser.py tests/unit/test_insider_model_roundtrip.py -v
```
Expected: all eight tests pass.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/python -m ruff check src/data/models/trades.py src/data/models/__init__.py src/data/providers/insider_trades/edgar.py tests/unit/test_insider_model_roundtrip.py tests/unit/test_form4_parser.py
git add src/data/models/trades.py src/data/models/__init__.py \
        src/data/providers/insider_trades/edgar.py \
        tests/unit/test_insider_model_roundtrip.py tests/unit/test_form4_parser.py
git commit -m "feat(phase5): Form 4 deep-pull — footnotes, codes, 10b5-1 flag, derivatives"
```

---

## Task 4 — SmartMoney Insider Removal + Closed-Vocab Cleanup

**Spec reference:** "Deterministic verdict heuristics → derive_smart_money_verdict" + Rollout step 4. The extractor is already insider-free; this task is mostly vocabulary cleanup, plus updating the fetch callback to stop pulling insider data.

**Files:**
- Modify: `src/agents/analysts/smart_money/fetch.py`
- Modify: `src/contract/extractors/smart_money.py` (closed-vocab notes only; main rewrite in Task 9)
- Test: `tests/unit/test_smart_money_fetch.py` (extend or create — assert insider not requested)
- Test: `tests/unit/test_extract_smart_money_features.py` (existing — update fixtures to drop insider)

- [ ] **Step 1: Write the failing fetch test**

Either extend the existing smart-money fetch test or create `tests/unit/test_smart_money_fetch.py`:

```python
"""smart_money_fetch_callback must NOT pull insider_trades."""
from __future__ import annotations

from typing import Any

import pytest


def test_smart_money_fetch_does_not_call_insider_domain(monkeypatch):
    """After Phase 5, smart_money_fetch_callback no longer fetches insider trades."""
    called_domains: list[str] = []

    def fake_get_for(ticker: str, domain: str, **kwargs) -> Any:
        called_domains.append(domain)
        # Return shape mirrors the existing provider stubs.
        return {} if domain == "notable_holders" else []

    # The fetch callback dispatches through the provider registry; patch the
    # function that the smart_money fetch calls. Adjust path to match the
    # actual registry helper used in fetch.py if it differs.
    from agents.analysts.smart_money import fetch as fetch_mod
    monkeypatch.setattr(fetch_mod, "get_for", fake_get_for, raising=False)

    # Build a minimal CallbackContext-like stub.
    ctx = type("Ctx", (), {})()
    ctx.state = {"tickers": ["AAPL"]}

    fetch_mod.smart_money_fetch_callback(ctx)

    assert "insider_trades" not in called_domains
    assert "politician_trades" in called_domains
    assert "notable_holders" in called_domains
```

(If the existing fetch.py uses a different dispatch helper — read the file before writing the test — adjust the monkeypatch target so it matches the actual call site.)

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_smart_money_fetch.py -v
```
Expected: fails — current `smart_money_fetch_callback` still calls `insider_trades`.

- [ ] **Step 2: Edit `src/agents/analysts/smart_money/fetch.py`**

Delete the `insider_trades` branch from the callback. Remove the `INSIDER_THRESHOLD` / `INSIDER_LOOKBACK_DAYS` module constants if they are only used by the deleted branch. Update the no-signal short-circuit check to consider politicians + notable holders only.

The post-edit callback writes `state["smart_money_data"]` as a dict keyed by ticker with only `{"politicians": [...], "notable_holders": [...]}` per ticker — no `"insiders"` key.

Update the module docstring to reflect that smart-money is now external-observer flows only.

- [ ] **Step 3: Edit `src/contract/extractors/smart_money.py`**

The `_KEYS` tuple is already insider-free. Confirm by re-reading the file. The closed-vocabulary update lands here:
- Add a module docstring section listing the closed vocabulary: `{net_buying, net_selling, multi_filer_consensus, lone_filer, high_volume_flow, mixed_activity}`.
- Remove any insider-related vocabulary references in comments.

(The full deterministic verdict function is added in Task 9, not here.)

- [ ] **Step 4: Run the fetch test + the existing extractor test**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_smart_money_fetch.py tests/unit/test_extract_smart_money_features.py -v
```
Expected: both pass. The existing extractor test asserts the `_KEYS` tuple shape; that has been insider-free since Phase 4.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check src/agents/analysts/smart_money/fetch.py src/contract/extractors/smart_money.py tests/unit/test_smart_money_fetch.py
git add src/agents/analysts/smart_money/fetch.py src/contract/extractors/smart_money.py tests/unit/test_smart_money_fetch.py
git commit -m "refactor(phase5): smart_money drops insider — external-observer flows only"
```

---

## Task 5 — Fundamental Insider Addition

**Spec reference:** "Data re-categorisation → Extractors → extract_fundamental_features" + "Insider expansion → Fundamental LLM prompt" + Rollout step 5.

**Files:**
- Modify: `src/agents/analysts/fundamental/fetch.py`
- Modify: `src/contract/extractors/fundamental.py`
- Test: `tests/unit/test_fundamental_fetch.py` (extend or create)
- Test: `tests/unit/test_extract_fundamental_features.py` (extend)

- [ ] **Step 1: Write the failing fetch test**

Create or extend `tests/unit/test_fundamental_fetch.py`:

```python
"""fundamental_fetch_callback must pull stats + filings + insider Form4Bundle."""
from __future__ import annotations


def test_fundamental_fetch_pulls_three_domains(monkeypatch):
    """After Phase 5, fundamental fetches stats + filings + insider_trades."""
    called_domains: list[str] = []

    def fake_get_for(ticker: str, domain: str, **kwargs):
        called_domains.append(domain)
        if domain == "stats":
            return {"pe_trailing": 25.0, "revenue_growth_yoy": 0.08}
        if domain == "filings":
            return []
        if domain == "insider_trades":
            from data.models import Form4Bundle
            return Form4Bundle(trades=[], derivatives=[])
        return None

    from agents.analysts.fundamental import fetch as fetch_mod
    monkeypatch.setattr(fetch_mod, "get_for", fake_get_for, raising=False)

    ctx = type("Ctx", (), {})()
    ctx.state = {"tickers": ["AAPL"]}

    fetch_mod.fundamental_fetch_callback(ctx)

    assert "stats" in called_domains
    assert "filings" in called_domains
    assert "insider_trades" in called_domains

    # State payload shape: per-ticker dict with three sub-keys.
    fundata = ctx.state["fundamental_data"]["AAPL"]
    assert "stats" in fundata
    assert "filings" in fundata
    assert "insider" in fundata
```

(Adjust the registry helper name to whatever the actual fetch.py uses — read it first.)

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_fundamental_fetch.py -v
```
Expected: fails — current fetch only pulls `filings`.

- [ ] **Step 2: Rewrite `src/agents/analysts/fundamental/fetch.py`**

The new fetch callback iterates the watchlist and, for each ticker, dispatches three provider calls (`stats`, `filings`, `insider_trades`). It writes the per-ticker payload as:

```python
state["fundamental_data"][ticker] = {
    "stats": <stats payload>,
    "filings": <filings list>,
    "insider": <Form4Bundle>,
}
```

Use the existing dispatch helper used in this file (`get_for(...)` or equivalent). Wrap each provider call in a small try/except that logs and falls back to an empty payload; partial failure of one domain must not break the other two. Docstring describes the new triad.

- [ ] **Step 3: Run the fetch test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_fundamental_fetch.py -v
```
Expected: passes.

- [ ] **Step 4: Write the failing extractor test for new insider columns**

Extend `tests/unit/test_extract_fundamental_features.py` with these cases (table-driven where possible):

```python
from datetime import UTC, date, datetime

from data.models import Form4Bundle, InsiderDerivativeTrade, InsiderTrade
from contract.extractors.fundamental import extract_fundamental_features


def _bundle_with_cluster_buys() -> Form4Bundle:
    """Three officers each buying — should trigger cluster_buy_flag."""
    base = {
        "ticker": "AAPL", "side": "buy", "shares": 1000.0,
        "price_per_share": 150.0, "form_type": "4",
        "transaction_date": date(2026, 5, 1),
        "filed_at": datetime(2026, 5, 2, tzinfo=UTC),
    }
    return Form4Bundle(
        trades=[
            InsiderTrade(**base, insider_name="Tim Cook", insider_title="CEO"),
            InsiderTrade(**base, insider_name="Luca Maestri", insider_title="CFO"),
            InsiderTrade(**base, insider_name="Greg Joswiak", insider_title="SVP"),
        ],
        derivatives=[],
    )


def test_extractor_emits_insider_columns():
    """The extractor now produces every Phase-5 insider feature column."""
    raw = {
        "stats": {"pe_trailing": 25.0, "revenue_growth_yoy": 0.08},
        "filings": [],
        "insider": _bundle_with_cluster_buys(),
    }
    features = extract_fundamental_features(raw, "AAPL")
    for key in (
        "insider_net_dollars_30d", "insider_n_buys_30d", "insider_n_sells_30d",
        "insider_cluster_buy_flag", "insider_cluster_sell_flag",
        "insider_planned_sale_ratio", "insider_max_filer_role_rank",
        "insider_derivative_exercise_count", "insider_derivative_grant_count",
        "days_since_last_filing", "n_filings_30d",
    ):
        assert key in features, f"missing feature {key}"


def test_extractor_cluster_buy_flag_fires_with_three_distinct_officers():
    """Three or more distinct officer-level buyers in the window flips cluster_buy_flag."""
    raw = {
        "stats": {},
        "filings": [],
        "insider": _bundle_with_cluster_buys(),
    }
    features = extract_fundamental_features(raw, "AAPL")
    assert features["insider_cluster_buy_flag"] == 1.0
    assert features["insider_n_buys_30d"] == 3.0
    assert features["insider_n_sells_30d"] == 0.0


def test_extractor_planned_sale_ratio_counts_10b5_1_correctly():
    """planned_sale_ratio = (10b5-1 sales) / total sales, clamped to [0, 1]."""
    base = {
        "ticker": "AAPL", "side": "sell", "shares": 100.0,
        "price_per_share": 150.0, "form_type": "4",
        "transaction_date": date(2026, 5, 1),
        "filed_at": datetime(2026, 5, 2, tzinfo=UTC),
        "insider_name": "Tim Cook", "insider_title": "CEO",
    }
    bundle = Form4Bundle(
        trades=[
            InsiderTrade(**base, is_10b5_1=True),
            InsiderTrade(**base, is_10b5_1=True),
            InsiderTrade(**base, is_10b5_1=False),
        ],
        derivatives=[],
    )
    features = extract_fundamental_features({"stats": {}, "filings": [], "insider": bundle}, "AAPL")
    assert abs(features["insider_planned_sale_ratio"] - (2 / 3)) < 1e-6


def test_extractor_returns_zero_columns_when_no_insider_data():
    """Empty Form4Bundle yields zeros for every insider column (is_no_data path)."""
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": Form4Bundle(trades=[], derivatives=[])},
        "AAPL",
    )
    assert features["insider_n_buys_30d"] == 0.0
    assert features["insider_cluster_buy_flag"] == 0.0
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_extract_fundamental_features.py -v
```
Expected: fails — the extractor doesn't emit insider columns yet.

- [ ] **Step 5: Rewrite `src/contract/extractors/fundamental.py`**

Add the new feature keys to `_KEYS` (or whatever the canonical key constant is named):

```python
_KEYS = (
    # Existing stats columns.
    "pe_trailing", "pe_forward", "peg", "revenue_growth_yoy",
    "profit_margin", "debt_to_equity", "fcf_yield_pct", "roe",
    "analyst_rating_avg",
    # New filings-derived numerics.
    "days_since_last_filing", "n_filings_30d",
    # New insider columns.
    "insider_net_dollars_30d", "insider_n_buys_30d", "insider_n_sells_30d",
    "insider_cluster_buy_flag", "insider_cluster_sell_flag",
    "insider_planned_sale_ratio", "insider_max_filer_role_rank",
    "insider_derivative_exercise_count", "insider_derivative_grant_count",
)
```

Implementation guidance:
- **`insider_net_dollars_30d`** — `sum(buy.shares * buy.price_per_share for buy in bundle.trades within 30 days) - sum(sell.shares * sell.price_per_share ...)`.
- **`insider_n_buys_30d` / `_n_sells_30d`** — simple counts within the 30-day window.
- **`insider_cluster_buy_flag`** — `1.0` when distinct-officer count among buys ≥ 3, else `0.0`. Same for sells.
- **`insider_planned_sale_ratio`** — `(count of 10b5-1 sells) / total sells`, `0.0` when total sells is 0.
- **`insider_max_filer_role_rank`** — map `insider_title` → numeric rank (`{"CEO": 5, "CFO": 4, "President": 4, "SVP": 3, "VP": 2, "Director": 1, None: 0}`); take the max across the 30-day window. Encapsulate the map in a module-level constant.
- **`insider_derivative_exercise_count`** — count of derivative rows whose `transaction_code == "M"`.
- **`insider_derivative_grant_count`** — count of derivative rows whose `transaction_code == "A"`.
- **`days_since_last_filing`** — days between `recorded_at` (UTC now) and `max(f.filed_at for f in filings)`; `9999.0` when no filings.
- **`n_filings_30d`** — count of filings whose `filed_at` is within 30 days.

The function continues to return a `dict[str, float]` covering every key in `_KEYS` (no missing keys; zero on no data).

Add a helper module-level constant for the officer role map so it can be unit-tested in isolation if needed.

- [ ] **Step 6: Run the extractor tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_extract_fundamental_features.py -v
```
Expected: all new tests pass; pre-existing tests still pass.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check src/agents/analysts/fundamental/fetch.py src/contract/extractors/fundamental.py tests/unit/test_fundamental_fetch.py tests/unit/test_extract_fundamental_features.py
git add src/agents/analysts/fundamental/fetch.py src/contract/extractors/fundamental.py tests/unit/test_fundamental_fetch.py tests/unit/test_extract_fundamental_features.py
git commit -m "feat(phase5): fundamental fetches stats+filings+insider; extractor adds insider columns"
```

---

## Task 6 — Sentiment → News Rename

**Spec reference:** "Narrowed LLM mandates → NewsAnalyst" + Rollout step 6. Pure rename — no logic change.

**Files:**
- Move: `src/agents/analysts/sentiment/` → `src/agents/analysts/news/`
- Move: `src/contract/extractors/sentiment.py` → `src/contract/extractors/news.py`
- Modify: state keys (`sentiment_data` → `news_data`) wherever they appear in source code
- Modify: output_key (`sentiment_verdicts` → `news_verdicts`)
- Modify: agent class name (`SentimentAnalyst` → `NewsAnalyst`)
- Modify: `src/orchestrator/state.py` (rename `sentiment_data` to `news_data`)
- Modify: `src/orchestrator/pipeline.py` (import + child reference)
- Test: rename `tests/unit/test_sentiment_*.py` → `tests/unit/test_news_*.py` if any exist; update fixtures.

- [ ] **Step 1: Inventory existing references**

Before the rename, capture every site that needs updating:

```bash
grep -rn "sentiment_data\|sentiment_verdicts\|SentimentAnalyst\|sentiment_fetch_callback\|sentiment_vocabulary\|analysts.sentiment\|extractors.sentiment\|extract_sentiment_features" src/ tests/ scripts/ 2>&1
```
Save the output for the commit message body.

- [ ] **Step 2: Move the analyst directory**

```bash
git mv src/agents/analysts/sentiment src/agents/analysts/news
git mv src/contract/extractors/sentiment.py src/contract/extractors/news.py
```

- [ ] **Step 3: Update identifiers inside the moved files**

Open each moved file and rename:
- `src/agents/analysts/news/agent.py`: class `SentimentAnalyst` → `NewsAnalyst`; module docstring; `name="SentimentAnalyst"` → `name="NewsAnalyst"`; `output_key="sentiment_verdicts"` → `output_key="news_verdicts"`; factory `_build_sentiment_analyst` → `_build_news_analyst`; `make_evidence_callback(analyst="sentiment", ...)` → `make_evidence_callback(analyst="news", ...)`; the extractor import switches to `from contract.extractors.news import extract_news_features`.
- `src/agents/analysts/news/fetch.py`: function `sentiment_fetch_callback` → `news_fetch_callback`; remove any `social_sentiment` / `mention_count` references (those migrate to Task 7); state key writes from `sentiment_data` → `news_data`; module docstring.
- `src/agents/analysts/news/prompts.py`: rename `SENTIMENT_INSTRUCTION` constant → `NEWS_INSTRUCTION`; module docstring.
- `src/agents/analysts/news/__init__.py`: package docstring + any re-exports.
- `src/contract/extractors/news.py`: function `extract_sentiment_features` → `extract_news_features`; module docstring; comments.

- [ ] **Step 4: Update orchestrator state**

Edit `src/orchestrator/state.py`:

```python
# Replace:
sentiment_data: dict[str, Any]      = Field(default_factory=dict)
# With:
news_data: dict[str, Any]           = Field(default_factory=dict)
```

If there is also a `sentiment_evidence` field, rename it to `news_evidence`. If those keys are not modelled in `TickState` and only live as raw dict keys in `session.state`, no change here.

- [ ] **Step 5: Update pipeline composition**

Edit `src/orchestrator/pipeline.py:13`:
- `from agents.analysts.sentiment.agent import _build_sentiment_analyst` → `from agents.analysts.news.agent import _build_news_analyst`
- Child invocation `_build_sentiment_analyst()` → `_build_news_analyst()`

- [ ] **Step 6: Update all other reference sites**

Walk the grep output from Step 1. For each remaining hit, update accordingly. Common sites include:
- `src/contract/evidence_writer.py` or `src/agents/contract/evidence_writer.py` (the EvidenceWriter that knows the analyst keys list)
- `src/contract/digest.py`
- `tests/**`
- `scripts/**`

If any tests are named `test_sentiment_*`, `git mv` them to `test_news_*` and update the imports inside.

- [ ] **Step 7: Run the full unit + integration smoke suite**

```bash
.venv/bin/python -m pytest tests/unit/ tests/integration/ -v -x
```
Expected: all green. Any failure is a missed rename — chase it.

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/python -m ruff check src/ tests/
git add -u
git add src/agents/analysts/news/ src/contract/extractors/news.py
git commit -m "refactor(phase5): rename sentiment → news analyst (rename only, no logic change)"
```

> The rename leaves the new News module pulling **both** news and social_sentiment from its fetch callback (whatever was there before). Task 7 splits social out into its own analyst; Task 11 narrows the News LLM prompt.

---

## Task 7 — New Social Analyst (Deterministic)

**Spec reference:** "Analyst pool" → Social row + "Data re-categorisation → Extractors → extract_social_features" + "Deterministic verdict heuristics → derive_social_verdict" + Rollout step 7.

**Files:**
- Create: `src/agents/analysts/social/__init__.py`
- Create: `src/agents/analysts/social/agent.py`
- Create: `src/agents/analysts/social/fetch.py`
- Create: `src/contract/extractors/social.py`
- Modify: `src/agents/analysts/news/fetch.py` (drop the `social_sentiment` branch)
- Modify: `src/orchestrator/pipeline.py` (add the 5th child)
- Modify: `src/orchestrator/state.py` (add `social_data`)
- Test: `tests/unit/test_extract_social_features.py` (create)
- Test: `tests/unit/test_derive_social_verdict.py` (create)
- Test: `tests/unit/test_social_fetch.py` (create)

- [ ] **Step 1: Write failing tests for `extract_social_features` + `derive_social_verdict`**

Create `tests/unit/test_extract_social_features.py`:

```python
"""Tier-1 tests for extract_social_features."""
from __future__ import annotations

from contract.extractors.social import extract_social_features


def test_extractor_emits_expected_keys():
    """All Phase-5 social feature keys are present."""
    features = extract_social_features({}, "AAPL")
    for key in (
        "mention_count_total", "mention_count_reddit", "mention_count_twitter",
        "aggregate_score", "score_velocity_24h",
        "platform_score_disagreement", "is_no_data",
    ):
        assert key in features


def test_extractor_no_data_path():
    """Empty payload sets is_no_data=1.0 and zero counts."""
    f = extract_social_features({}, "AAPL")
    assert f["mention_count_total"] == 0.0
    assert f["is_no_data"] == 1.0


def test_extractor_aggregates_across_platforms():
    """Reddit + Twitter counts sum into mention_count_total."""
    payload = {
        "reddit": {"mention_count": 50, "positive_score": 0.4, "negative_score": 0.1},
        "twitter": {"mention_count": 120, "positive_score": 0.2, "negative_score": 0.2},
    }
    f = extract_social_features(payload, "AAPL")
    assert f["mention_count_total"] == 170.0
    assert f["mention_count_reddit"] == 50.0
    assert f["mention_count_twitter"] == 120.0
    assert f["is_no_data"] == 0.0


def test_platform_score_disagreement_high_when_platforms_diverge():
    """abs(reddit_net - twitter_net) above zero registers in platform_score_disagreement."""
    payload = {
        "reddit":  {"mention_count": 50,  "positive_score": 0.8, "negative_score": 0.0},
        "twitter": {"mention_count": 120, "positive_score": 0.0, "negative_score": 0.8},
    }
    f = extract_social_features(payload, "AAPL")
    assert f["platform_score_disagreement"] > 0.5
```

Create `tests/unit/test_derive_social_verdict.py`:

```python
"""Tier-1 tests for derive_social_verdict."""
from __future__ import annotations

import pytest

from agents.analysts.heuristics import SocialHeuristics
from contract.extractors.social import derive_social_verdict


def _h() -> SocialHeuristics:
    """Canonical fixture matching config defaults."""
    return SocialHeuristics(
        score_neutral_band=0.05, score_to_magnitude_scale=2.0,
        high_volume_mentions=200, high_volume_magnitude_boost=0.15,
        confidence_volume_floor=30, platform_disagreement_threshold=0.3,
        confidence_base=0.4, confidence_boost_step=0.2,
        confidence_penalty_step=0.2, magnitude_cap=1.0,
    )


def _features(**overrides) -> dict:
    base = {
        "mention_count_total": 100.0, "mention_count_reddit": 50.0,
        "mention_count_twitter": 50.0, "aggregate_score": 0.0,
        "score_velocity_24h": 0.0, "platform_score_disagreement": 0.0,
        "is_no_data": 0.0,
    }
    base.update(overrides)
    return base


def test_no_data_path():
    """is_no_data=1.0 returns the no-data verdict shape."""
    v = derive_social_verdict(_features(mention_count_total=0, is_no_data=1.0), _h())
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0
    assert v.is_no_data is True


def test_positive_cluster_is_bullish():
    """Positive aggregate score above neutral band leans bullish."""
    v = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=100), _h())
    assert v.lean == "bullish"
    assert v.magnitude > 0.0


def test_negative_cluster_is_bearish():
    """Negative aggregate score below neutral band leans bearish."""
    v = derive_social_verdict(_features(aggregate_score=-0.4), _h())
    assert v.lean == "bearish"


def test_neutral_band_keeps_lean_neutral():
    """Aggregate score inside the band leans neutral."""
    v = derive_social_verdict(_features(aggregate_score=0.02), _h())
    assert v.lean == "neutral"


def test_high_volume_boosts_magnitude():
    """mention_count > high_volume_mentions adds high_volume_magnitude_boost."""
    low  = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=10), _h())
    high = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=500), _h())
    assert high.magnitude >= low.magnitude


def test_platform_disagreement_penalises_confidence():
    """Reddit/Twitter divergence above threshold drops confidence."""
    agree    = derive_social_verdict(_features(aggregate_score=0.4, platform_score_disagreement=0.0, mention_count_total=100), _h())
    disagree = derive_social_verdict(_features(aggregate_score=0.4, platform_score_disagreement=0.5, mention_count_total=100), _h())
    assert disagree.confidence < agree.confidence


def test_key_factors_use_closed_vocabulary():
    """All emitted key_factors fall inside the closed vocabulary."""
    v = derive_social_verdict(_features(aggregate_score=0.4, mention_count_total=500), _h())
    allowed = {"positive", "negative", "mixed", "high_volume", "low_volume",
               "reddit_dominant", "twitter_dominant", "platforms_agree", "platforms_disagree"}
    for tag in v.key_factors:
        assert tag in allowed, f"out-of-vocab tag: {tag}"
```

Run both:
```bash
.venv/bin/python -m pytest tests/unit/test_extract_social_features.py tests/unit/test_derive_social_verdict.py -v
```
Expected: fail — module doesn't exist.

- [ ] **Step 2: Create `src/contract/extractors/social.py`**

```python
"""Social-sentiment feature extractor + deterministic verdict derivation.

Consumes the Finnhub `stock_social_sentiment` payload (pre-aggregated; no raw
posts ever flow through here) and produces a fixed-shape feature dict plus
a deterministic AnalystVerdict via `derive_social_verdict`.
"""
from __future__ import annotations

from typing import Any

from agents.analysts.heuristics import SocialHeuristics
from contract.evidence import AnalystVerdict


_KEYS: tuple[str, ...] = (
    "mention_count_total", "mention_count_reddit", "mention_count_twitter",
    "aggregate_score", "score_velocity_24h",
    "platform_score_disagreement", "is_no_data",
)


def _net(scores: dict[str, Any]) -> float:
    """Compute one platform's net polarity score = positive - negative."""
    pos = float(scores.get("positive_score") or 0.0)
    neg = float(scores.get("negative_score") or 0.0)
    return pos - neg


def extract_social_features(raw: dict[str, Any], ticker: str) -> dict[str, float]:
    """Reduce the per-ticker social payload to the Phase-5 feature vector.

    Expected `raw` shape (one ticker's slice):
        {"reddit":  {"mention_count": int, "positive_score": float, "negative_score": float, ...},
         "twitter": {"mention_count": int, "positive_score": float, "negative_score": float, ...}}

    Returns a dict with every key in `_KEYS`; missing inputs yield zeros and
    set `is_no_data=1.0`.
    """
    reddit  = raw.get("reddit")  or {}
    twitter = raw.get("twitter") or {}

    n_reddit  = float(reddit.get("mention_count")  or 0.0)
    n_twitter = float(twitter.get("mention_count") or 0.0)
    n_total   = n_reddit + n_twitter

    if n_total == 0:
        return {k: (1.0 if k == "is_no_data" else 0.0) for k in _KEYS}

    reddit_net  = _net(reddit)
    twitter_net = _net(twitter)

    # Weighted aggregate score by mention count.
    aggregate = (reddit_net * n_reddit + twitter_net * n_twitter) / n_total

    # Platform disagreement = abs gap between platform-level net scores;
    # large when one platform is bullish and the other bearish.
    disagreement = abs(reddit_net - twitter_net) if (n_reddit and n_twitter) else 0.0

    return {
        "mention_count_total":         n_total,
        "mention_count_reddit":        n_reddit,
        "mention_count_twitter":       n_twitter,
        "aggregate_score":             aggregate,
        "score_velocity_24h":          0.0,   # placeholder — prior-tick comparison wires later
        "platform_score_disagreement": disagreement,
        "is_no_data":                  0.0,
    }


def derive_social_verdict(features: dict[str, float], h: SocialHeuristics) -> AnalystVerdict:
    """Map social feature vector → AnalystVerdict using the Phase-5 heuristic.

    See spec §"derive_social_verdict" for the rule. Pure function; safe for
    table-driven unit tests.
    """
    if features.get("is_no_data", 0.0) >= 1.0 or features["mention_count_total"] == 0:
        return AnalystVerdict(
            lean="neutral", magnitude=0.0, confidence=0.0,
            rationale="no social mentions", key_factors=[], is_no_data=True,
        )

    score   = features["aggregate_score"]
    n_total = features["mention_count_total"]

    # Lean ----------------------------------------------------------------
    if score >  h.score_neutral_band:
        lean = "bullish"
    elif score < -h.score_neutral_band:
        lean = "bearish"
    else:
        lean = "neutral"

    # Magnitude -----------------------------------------------------------
    magnitude = min(abs(score) * h.score_to_magnitude_scale, h.magnitude_cap)
    if n_total > h.high_volume_mentions:
        magnitude = min(magnitude + h.high_volume_magnitude_boost, h.magnitude_cap)

    # Confidence ----------------------------------------------------------
    confidence = h.confidence_base
    if n_total >= h.confidence_volume_floor:
        confidence += h.confidence_boost_step
    if features["platform_score_disagreement"] > h.platform_disagreement_threshold:
        confidence -= h.confidence_penalty_step
    confidence = max(0.0, min(1.0, confidence))

    # Key factors (closed vocabulary) ------------------------------------
    factors: list[str] = []
    if lean == "bullish":
        factors.append("positive")
    elif lean == "bearish":
        factors.append("negative")
    else:
        factors.append("mixed")

    if n_total > h.high_volume_mentions:
        factors.append("high_volume")
    elif n_total < h.confidence_volume_floor:
        factors.append("low_volume")

    if features["platform_score_disagreement"] > h.platform_disagreement_threshold:
        factors.append("platforms_disagree")
    else:
        factors.append("platforms_agree")

    if features["mention_count_reddit"] > 2 * features["mention_count_twitter"]:
        factors.append("reddit_dominant")
    elif features["mention_count_twitter"] > 2 * features["mention_count_reddit"]:
        factors.append("twitter_dominant")

    rationale = ", ".join(factors)[:160]

    return AnalystVerdict(
        lean=lean, magnitude=magnitude, confidence=confidence,
        rationale=rationale, key_factors=factors, is_no_data=False,
    )
```

- [ ] **Step 3: Create `src/agents/analysts/social/fetch.py`**

```python
"""Fetch callback for the deterministic Social analyst.

Pulls only `social_sentiment/` (Finnhub Reddit + Twitter aggregates).
Writes `state["social_data"][ticker]` keyed by ticker.
"""
from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext

# Adjust the import target to whatever the project uses elsewhere — match
# how the other fetch.py modules dispatch to providers.
from data.registry import get_for


def social_fetch_callback(ctx: CallbackContext) -> None:
    """Populate state['social_data'] for every ticker in the watchlist."""
    tickers = ctx.state.get("tickers", []) or []
    payload: dict[str, dict] = {}
    for ticker in tickers:
        try:
            payload[ticker] = get_for(ticker, "social_sentiment") or {}
        except Exception:
            # Provider failure must not break the parallel pool; treat as no data.
            payload[ticker] = {}
    ctx.state["social_data"] = payload
```

Adjust `from data.registry import get_for` to match the actual project import (read `src/agents/analysts/news/fetch.py` and mirror its pattern).

- [ ] **Step 4: Create `src/agents/analysts/social/agent.py`**

```python
"""Deterministic Social analyst (BaseAgent).

Runs `extract_social_features` then `derive_social_verdict` for every
ticker, writes the verdict list to state['social_verdicts'], and lets the
shared `make_evidence_callback` after-callback build the AnalystEvidence
rows on `state['social_evidence']`.
"""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import SocialHeuristics
from agents.analysts.social.fetch import social_fetch_callback
from contract.extractors.social import (
    derive_social_verdict,
    extract_social_features,
)


class SocialAnalyst(BaseAgent):
    """One per-tick deterministic social verdict per ticker."""

    h: SocialHeuristics

    def __init__(self, h: SocialHeuristics) -> None:
        super().__init__(
            name="SocialAnalyst",
            before_agent_callback=social_fetch_callback,
            after_agent_callback=make_evidence_callback(
                analyst="social",
                extractor=extract_social_features,
                verdicts_state_key="social_verdicts",
            ),
        )
        # Use object.__setattr__ if BaseAgent is frozen-ish; otherwise plain.
        object.__setattr__(self, "h", h)

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        """Compute verdicts deterministically — no LLM call."""
        state = ctx.session.state
        tickers = state.get("tickers", []) or []
        data    = state.get("social_data", {}) or {}

        verdicts: list[dict] = []
        for ticker in tickers:
            features = extract_social_features(data.get(ticker, {}), ticker)
            verdict  = derive_social_verdict(features, self.h)
            v_dict = verdict.model_dump(mode="json")
            v_dict["ticker"] = ticker
            verdicts.append(v_dict)
        state["social_verdicts"] = verdicts

        # BaseAgent must yield at least once; empty event is sufficient for ADK.
        return
        yield  # pragma: no cover — async-generator idiom

def _build_social_analyst(h: SocialHeuristics) -> SocialAnalyst:
    """Construct a fresh SocialAnalyst — used by the pipeline composer."""
    return SocialAnalyst(h)
```

Verify against the existing BaseAgent patterns in the codebase (the project already uses the `return; yield` idiom — check via grep `return\n.*yield`). Match constructor signature conventions to whichever existing BaseAgent (e.g. `RiskGateAgent`) is closest.

- [ ] **Step 5: Create `src/agents/analysts/social/__init__.py`**

```python
"""Deterministic Social analyst package (Phase 5)."""
```

- [ ] **Step 6: Edit `src/agents/analysts/news/fetch.py` to drop social_sentiment**

Remove the branch / call site that pulls `social_sentiment`. The News fetch now writes `state["news_data"]` with news-only payloads (no `social_sentiment` sub-key).

- [ ] **Step 7: Wire the new analyst into the pipeline**

Edit `src/orchestrator/pipeline.py`:
- Add: `from agents.analysts.social.agent import _build_social_analyst`
- In `_build_analyst_pool`, add `_build_social_analyst(...)` as the fifth child.
- Defer heuristics threading to Task 12 if it gets too noisy here; for now, call `load_heuristics().social` inline:

```python
def _build_analyst_pool():
    """Build a fresh AnalystPool each tick — now five children (3 deterministic + 2 LLM)."""
    from google.adk.agents import ParallelAgent

    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.news.agent          import _build_news_analyst
    from agents.analysts.social.agent        import _build_social_analyst
    from agents.analysts.smart_money.agent   import _build_smart_money_analyst
    from agents.analysts.technical.agent     import _build_technical_analyst
    from agents.analysts.heuristics          import load_heuristics

    h = load_heuristics()
    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            _build_technical_analyst(),                       # deterministic later (Task 8)
            _build_fundamental_analyst(),                     # LLM (Task 10)
            _build_news_analyst(),                            # LLM (Task 11)
            _build_social_analyst(h.social),                  # deterministic now
            _build_smart_money_analyst(),                     # deterministic later (Task 9)
        ],
    )
```

Technical / Smart-money factories still match their current signature here — they receive their heuristics in later tasks.

- [ ] **Step 8: Add `social_data` to `TickState`**

Edit `src/orchestrator/state.py`:

```python
social_data: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 9: Write the failing fetch test**

Create `tests/unit/test_social_fetch.py`:

```python
"""Tier-1 test: social_fetch_callback writes state['social_data'] keyed by ticker."""
from __future__ import annotations


def test_social_fetch_writes_state_dict(monkeypatch):
    from agents.analysts.social import fetch as fetch_mod

    def fake_get_for(ticker, domain, **kwargs):
        assert domain == "social_sentiment"
        return {"reddit": {"mention_count": 10, "positive_score": 0.3, "negative_score": 0.1}}

    monkeypatch.setattr(fetch_mod, "get_for", fake_get_for, raising=False)

    ctx = type("Ctx", (), {})()
    ctx.state = {"tickers": ["AAPL"]}
    fetch_mod.social_fetch_callback(ctx)
    assert "AAPL" in ctx.state["social_data"]
    assert "reddit" in ctx.state["social_data"]["AAPL"]
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_social_fetch.py tests/unit/test_extract_social_features.py tests/unit/test_derive_social_verdict.py -v
```
Expected: all pass.

- [ ] **Step 10: Run the integration smoke test as a sanity check**

```bash
.venv/bin/python -m pytest tests/integration/ -v -x -m integration
```
Expected: green if `social_data` is plumbed through. Fix any KeyError / missing-state issues uncovered.

- [ ] **Step 11: Lint and commit**

```bash
.venv/bin/python -m ruff check src/agents/analysts/social/ src/contract/extractors/social.py src/orchestrator/pipeline.py src/orchestrator/state.py src/agents/analysts/news/fetch.py tests/unit/test_social_fetch.py tests/unit/test_extract_social_features.py tests/unit/test_derive_social_verdict.py
git add src/agents/analysts/social/ src/contract/extractors/social.py src/orchestrator/pipeline.py src/orchestrator/state.py src/agents/analysts/news/fetch.py tests/unit/test_social_fetch.py tests/unit/test_extract_social_features.py tests/unit/test_derive_social_verdict.py
git commit -m "feat(phase5): add deterministic Social analyst as 5th AnalystPool child"
```

---

## Task 8 — Deterministic Technical Analyst

**Spec reference:** "Deterministic verdict heuristics → derive_technical_verdict" + Rollout step 8.

**Files:**
- Modify: `src/agents/analysts/technical/agent.py` (LlmAgent → BaseAgent)
- Delete: `src/agents/analysts/technical/prompts.py`
- Modify: `src/contract/extractors/technical.py` (add `derive_technical_verdict`)
- Modify: `src/orchestrator/pipeline.py` (thread `h.technical`)
- Test: `tests/unit/test_derive_technical_verdict.py` (create)
- Test: `tests/unit/test_technical_agent.py` (extend or create — assert BaseAgent shape)

- [ ] **Step 1: Write failing tests for `derive_technical_verdict`**

Create `tests/unit/test_derive_technical_verdict.py`:

```python
"""Tier-1 tests for derive_technical_verdict — table-driven cases per spec."""
from __future__ import annotations

import pytest

from agents.analysts.heuristics import TechnicalHeuristics
from contract.extractors.technical import derive_technical_verdict


def _h() -> TechnicalHeuristics:
    return TechnicalHeuristics(
        rsi_overbought=75, rsi_oversold=25,
        pct_change_momentum_scale=4.0,
        vol_ratio_breakout=1.5, vol_ratio_dry_up=0.7,
        atr_high_volatility_pct=5.0, near_52w_extreme_pct=5.0,
        confidence_base=0.5, confidence_boost_step=0.2,
        confidence_penalty_step=0.3, magnitude_cap=1.0,
    )


def _features(**overrides) -> dict:
    base = {
        "rsi_14": 50.0, "pct_change_5d": 0.0, "pct_change_20d": 0.0,
        "vol_ratio_20d": 1.0, "atr_pct_14": 2.0,
        "pct_from_52w_high": 10.0, "pct_from_52w_low": 30.0,
    }
    base.update(overrides)
    return base


def test_no_data_path():
    """All-zero core features ⇒ is_no_data."""
    v = derive_technical_verdict(_features(rsi_14=0, pct_change_20d=0, atr_pct_14=0), _h())
    assert v.is_no_data is True
    assert v.lean == "neutral"


def test_uptrend_20d():
    """Positive 20-day momentum leans bullish."""
    v = derive_technical_verdict(_features(pct_change_20d=0.08, pct_change_5d=0.03), _h())
    assert v.lean == "bullish"


def test_downtrend_20d():
    """Negative 20-day momentum leans bearish."""
    v = derive_technical_verdict(_features(pct_change_20d=-0.08, pct_change_5d=-0.03), _h())
    assert v.lean == "bearish"


def test_overbought_exhaustion_flips_to_bearish():
    """RSI > overbought AND positive 5d momentum ⇒ bearish exhaustion flip."""
    v = derive_technical_verdict(_features(rsi_14=80, pct_change_5d=0.04, pct_change_20d=0.05), _h())
    assert v.lean == "bearish"


def test_oversold_capitulation_flips_to_bullish():
    """RSI < oversold AND negative 5d momentum ⇒ bullish capitulation flip."""
    v = derive_technical_verdict(_features(rsi_14=20, pct_change_5d=-0.04, pct_change_20d=-0.05), _h())
    assert v.lean == "bullish"


def test_vol_breakout_boosts_magnitude():
    """High vol_ratio above threshold lifts magnitude."""
    quiet = derive_technical_verdict(_features(pct_change_20d=0.08, vol_ratio_20d=1.0), _h())
    boom  = derive_technical_verdict(_features(pct_change_20d=0.08, vol_ratio_20d=2.0), _h())
    assert boom.magnitude > quiet.magnitude


def test_momentum_agree_boosts_confidence():
    """5d sign aligned with 20d sign lifts confidence."""
    agree    = derive_technical_verdict(_features(pct_change_5d=0.03, pct_change_20d=0.08), _h())
    disagree = derive_technical_verdict(_features(pct_change_5d=-0.03, pct_change_20d=0.08), _h())
    assert agree.confidence > disagree.confidence


def test_near_52w_high_boosts_confidence():
    """Within near_52w_extreme_pct of 52-week high boosts confidence."""
    far  = derive_technical_verdict(_features(pct_change_20d=0.08, pct_from_52w_high=20.0), _h())
    near = derive_technical_verdict(_features(pct_change_20d=0.08, pct_from_52w_high=2.0), _h())
    assert near.confidence > far.confidence


def test_high_atr_penalises_confidence():
    """ATR pct above the threshold drops confidence."""
    calm   = derive_technical_verdict(_features(pct_change_20d=0.08, atr_pct_14=2.0), _h())
    choppy = derive_technical_verdict(_features(pct_change_20d=0.08, atr_pct_14=8.0), _h())
    assert choppy.confidence < calm.confidence


def test_closed_vocabulary():
    """All emitted key_factors are inside the closed technical vocabulary."""
    allowed = {"trend_up_20d", "trend_down_20d", "momentum_agree", "momentum_disagree",
               "rsi_overbought", "rsi_oversold", "near_52w_high", "near_52w_low",
               "vol_breakout", "vol_dry_up", "high_volatility"}
    v = derive_technical_verdict(_features(pct_change_20d=0.08, pct_change_5d=0.03, vol_ratio_20d=2.0, pct_from_52w_high=2.0), _h())
    for tag in v.key_factors:
        assert tag in allowed
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_derive_technical_verdict.py -v
```
Expected: fails — `derive_technical_verdict` does not exist.

- [ ] **Step 2: Implement `derive_technical_verdict`**

Read the existing `src/contract/extractors/technical.py` first to confirm the actual `_KEYS` shape. Then append `derive_technical_verdict` to that module (do not split — keep extractor + derivation co-located, mirror Social):

```python
def derive_technical_verdict(features: dict[str, float], h: TechnicalHeuristics) -> AnalystVerdict:
    """Map technical feature vector → AnalystVerdict using the Phase-5 heuristic.

    See spec §"derive_technical_verdict" for the rule. Pure function; safe for
    table-driven unit tests.
    """
    from math import copysign

    # No-data fingerprint matches extractor's zero-on-empty output.
    if features["rsi_14"] == 0 and features["pct_change_20d"] == 0 and features["atr_pct_14"] == 0:
        return AnalystVerdict(
            lean="neutral", magnitude=0.0, confidence=0.0,
            rationale="no price data", key_factors=[], is_no_data=True,
        )

    factors: list[str] = []

    # Base lean = sign of 20-day momentum.
    sign20 = copysign(1, features["pct_change_20d"]) if features["pct_change_20d"] != 0 else 0
    sign5  = copysign(1, features["pct_change_5d"])  if features["pct_change_5d"]  != 0 else 0

    lean = "bullish" if sign20 > 0 else ("bearish" if sign20 < 0 else "neutral")
    if sign20 > 0:
        factors.append("trend_up_20d")
    elif sign20 < 0:
        factors.append("trend_down_20d")

    if sign5 == sign20 and sign20 != 0:
        factors.append("momentum_agree")
    elif sign5 != 0 and sign20 != 0:
        factors.append("momentum_disagree")

    # Overbought / oversold flips.
    rsi = features["rsi_14"]
    if rsi > h.rsi_overbought:
        factors.append("rsi_overbought")
        if features["pct_change_5d"] > 0:
            lean = "bearish"
    if rsi < h.rsi_oversold:
        factors.append("rsi_oversold")
        if features["pct_change_5d"] < 0:
            lean = "bullish"

    # Volume context.
    if features["vol_ratio_20d"] > h.vol_ratio_breakout:
        factors.append("vol_breakout")
    elif features["vol_ratio_20d"] < h.vol_ratio_dry_up:
        factors.append("vol_dry_up")

    # 52-week extremes.
    if features.get("pct_from_52w_high", 100.0) <= h.near_52w_extreme_pct:
        factors.append("near_52w_high")
    if features.get("pct_from_52w_low", 100.0) <= h.near_52w_extreme_pct:
        factors.append("near_52w_low")

    # Volatility flag.
    if features["atr_pct_14"] > h.atr_high_volatility_pct:
        factors.append("high_volatility")

    # Magnitude.
    magnitude = min(abs(features["pct_change_20d"]) * h.pct_change_momentum_scale, h.magnitude_cap)
    if "vol_breakout" in factors:
        magnitude = min(magnitude + 0.15, h.magnitude_cap)
    if "vol_dry_up" in factors:
        magnitude = max(magnitude - 0.10, 0.0)

    # Confidence.
    confidence = h.confidence_base
    if "momentum_agree" in factors:
        confidence += h.confidence_boost_step
    if "near_52w_high" in factors or "near_52w_low" in factors:
        confidence += h.confidence_boost_step
    if "high_volatility" in factors:
        confidence -= h.confidence_penalty_step
    confidence = max(0.0, min(1.0, confidence))

    rationale = ", ".join(factors)[:160] or "neutral"

    return AnalystVerdict(
        lean=lean, magnitude=magnitude, confidence=confidence,
        rationale=rationale, key_factors=factors, is_no_data=False,
    )
```

Adjust feature key names to match the actual `_KEYS` tuple already in `extractors/technical.py`. If the existing names differ (e.g. `dist_52w_high_pct` vs `pct_from_52w_high`), use the actual names — both in the implementation AND in the test fixtures above.

- [ ] **Step 3: Run the derive tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_derive_technical_verdict.py -v
```
Expected: all pass.

- [ ] **Step 4: Rewrite `src/agents/analysts/technical/agent.py` as a BaseAgent**

Mirror the SocialAnalyst pattern from Task 7:

```python
"""Deterministic Technical analyst (Phase 5). LlmAgent removed."""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import TechnicalHeuristics
from agents.analysts.technical.fetch import technical_fetch_callback
from contract.extractors.technical import (
    derive_technical_verdict,
    extract_technical_features,
)


class TechnicalAnalyst(BaseAgent):
    """Per-tick deterministic technical verdict for every ticker."""

    h: TechnicalHeuristics

    def __init__(self, h: TechnicalHeuristics) -> None:
        super().__init__(
            name="TechnicalAnalyst",
            before_agent_callback=technical_fetch_callback,
            after_agent_callback=make_evidence_callback(
                analyst="technical",
                extractor=extract_technical_features,
                verdicts_state_key="technical_verdicts",
            ),
        )
        object.__setattr__(self, "h", h)

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        """Compute verdicts deterministically — no LLM call."""
        state = ctx.session.state
        tickers = state.get("tickers", []) or []
        data    = state.get("technical_data", {}) or {}
        verdicts: list[dict] = []
        for ticker in tickers:
            features = extract_technical_features(data.get(ticker, {}), ticker)
            verdict  = derive_technical_verdict(features, self.h)
            v_dict = verdict.model_dump(mode="json")
            v_dict["ticker"] = ticker
            verdicts.append(v_dict)
        state["technical_verdicts"] = verdicts
        return
        yield  # pragma: no cover


def _build_technical_analyst(h: TechnicalHeuristics) -> TechnicalAnalyst:
    """Construct a fresh TechnicalAnalyst — used by the pipeline composer."""
    return TechnicalAnalyst(h)
```

Delete `src/agents/analysts/technical/prompts.py` — no longer needed:

```bash
git rm src/agents/analysts/technical/prompts.py
```

- [ ] **Step 5: Update pipeline composition**

In `src/orchestrator/pipeline.py`:
- `_build_technical_analyst()` → `_build_technical_analyst(h.technical)`.

- [ ] **Step 6: Run the full unit + integration smoke suite**

```bash
.venv/bin/python -m pytest tests/unit/ tests/integration/ -v -x
```
Expected: green.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check src/contract/extractors/technical.py src/agents/analysts/technical/agent.py src/orchestrator/pipeline.py tests/unit/test_derive_technical_verdict.py
git add src/contract/extractors/technical.py src/agents/analysts/technical/agent.py src/orchestrator/pipeline.py tests/unit/test_derive_technical_verdict.py
git rm src/agents/analysts/technical/prompts.py
git commit -m "feat(phase5): deterministic Technical analyst — replace LlmAgent with BaseAgent"
```

---

## Task 9 — Deterministic SmartMoney Analyst

**Spec reference:** "Deterministic verdict heuristics → derive_smart_money_verdict" + Rollout step 9.

**Files:**
- Modify: `src/agents/analysts/smart_money/agent.py` (LlmAgent → BaseAgent)
- Delete: `src/agents/analysts/smart_money/prompts.py`
- Modify: `src/contract/extractors/smart_money.py` (add `derive_smart_money_verdict`)
- Modify: `src/orchestrator/pipeline.py` (thread `h.smart_money`)
- Test: `tests/unit/test_derive_smart_money_verdict.py` (create)

- [ ] **Step 1: Write failing tests for `derive_smart_money_verdict`**

Create `tests/unit/test_derive_smart_money_verdict.py`:

```python
"""Tier-1 tests for derive_smart_money_verdict."""
from __future__ import annotations

from agents.analysts.heuristics import SmartMoneyHeuristics
from contract.extractors.smart_money import derive_smart_money_verdict


def _h() -> SmartMoneyHeuristics:
    return SmartMoneyHeuristics(
        multi_filer_min_count=3, high_activity_trade_count=5,
        lone_filer_confidence_floor=0.1,
        consensus_confidence_ceiling=0.9, magnitude_cap=1.0,
    )


def _features(**overrides) -> dict:
    base = {
        "n_politicians": 0.0, "n_buys_30d": 0.0, "n_sells_30d": 0.0,
        "total_dollar_value_buys": 0.0, "total_dollar_value_sells": 0.0,
        "net_flow_dollar": 0.0, "is_no_data": 0.0,
    }
    base.update(overrides)
    return base


def test_no_data_returns_neutral_no_data():
    """is_no_data flag yields the no-data verdict."""
    v = derive_smart_money_verdict(_features(is_no_data=1.0), _h())
    assert v.is_no_data is True
    assert v.lean == "neutral"


def test_net_buying_leans_bullish():
    """Positive net flow ⇒ bullish."""
    v = derive_smart_money_verdict(_features(net_flow_dollar=50_000, total_dollar_value_buys=60_000, total_dollar_value_sells=10_000, n_politicians=2, n_buys_30d=3), _h())
    assert v.lean == "bullish"


def test_net_selling_leans_bearish():
    """Negative net flow ⇒ bearish."""
    v = derive_smart_money_verdict(_features(net_flow_dollar=-50_000, total_dollar_value_buys=10_000, total_dollar_value_sells=60_000, n_politicians=2, n_sells_30d=3), _h())
    assert v.lean == "bearish"


def test_lone_filer_confidence_floor():
    """One filer + one trade is capped at lone_filer_confidence_floor."""
    v = derive_smart_money_verdict(_features(n_politicians=1, n_buys_30d=1, net_flow_dollar=1_000, total_dollar_value_buys=1_000), _h())
    assert v.confidence <= 0.2  # generous slack above floor


def test_consensus_ceiling_when_many_filers_high_activity():
    """Many filers + high activity raises confidence near ceiling."""
    v = derive_smart_money_verdict(_features(n_politicians=5, n_buys_30d=6, net_flow_dollar=50_000, total_dollar_value_buys=60_000, total_dollar_value_sells=10_000), _h())
    assert v.confidence >= 0.5


def test_magnitude_uses_flow_asymmetry_not_absolute_dollars():
    """magnitude scales by flow ratio, not raw dollars."""
    small = derive_smart_money_verdict(_features(net_flow_dollar=900, total_dollar_value_buys=1_000, total_dollar_value_sells=100, n_politicians=2, n_buys_30d=2), _h())
    big   = derive_smart_money_verdict(_features(net_flow_dollar=9_000, total_dollar_value_buys=10_000, total_dollar_value_sells=1_000, n_politicians=2, n_buys_30d=2), _h())
    # Same flow asymmetry → similar magnitudes.
    assert abs(small.magnitude - big.magnitude) < 0.05


def test_closed_vocabulary():
    """key_factors stays inside the closed smart-money vocabulary."""
    allowed = {"net_buying", "net_selling", "multi_filer_consensus",
               "lone_filer", "high_volume_flow", "mixed_activity"}
    v = derive_smart_money_verdict(_features(net_flow_dollar=50_000, total_dollar_value_buys=60_000, total_dollar_value_sells=10_000, n_politicians=5, n_buys_30d=6), _h())
    for tag in v.key_factors:
        assert tag in allowed
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_derive_smart_money_verdict.py -v
```
Expected: fails — function doesn't exist.

- [ ] **Step 2: Implement `derive_smart_money_verdict`**

Append to `src/contract/extractors/smart_money.py`:

```python
def derive_smart_money_verdict(features: dict[str, float], h: SmartMoneyHeuristics) -> AnalystVerdict:
    """Map smart-money feature vector → AnalystVerdict using the Phase-5 heuristic.

    External-observer flows only (politicians + 13F); insider migrated to
    Fundamental. See spec §"derive_smart_money_verdict".
    """
    from math import copysign

    if features.get("is_no_data", 0.0) >= 1.0:
        return AnalystVerdict(
            lean="neutral", magnitude=0.0, confidence=0.0,
            rationale="no smart-money activity",
            key_factors=[], is_no_data=True,
        )

    net   = features["net_flow_dollar"]
    buys  = features["total_dollar_value_buys"]
    sells = features["total_dollar_value_sells"]
    nf    = features["n_politicians"]
    trades = features["n_buys_30d"] + features["n_sells_30d"]

    factors: list[str] = []

    # Lean.
    if net > 0:
        lean = "bullish"
        factors.append("net_buying")
    elif net < 0:
        lean = "bearish"
        factors.append("net_selling")
    else:
        lean = "neutral"
        factors.append("mixed_activity")

    # Magnitude — flow asymmetry, not raw dollars.
    denom = buys + sells + 1.0   # +1 guards division-by-zero
    magnitude = min(abs(net) / denom, h.magnitude_cap)

    # Confidence — interpolated by filer count and activity.
    if nf >= h.multi_filer_min_count and trades >= h.high_activity_trade_count:
        confidence = h.consensus_confidence_ceiling
        factors.append("multi_filer_consensus")
        factors.append("high_volume_flow")
    elif nf <= 1 and trades <= 1:
        confidence = h.lone_filer_confidence_floor
        factors.append("lone_filer")
    else:
        # Linear interp between floor and ceiling, weighted equally by filers + trades.
        span_f = max(0.0, (nf - 1) / max(1, h.multi_filer_min_count - 1))
        span_t = max(0.0, (trades - 1) / max(1, h.high_activity_trade_count - 1))
        weight = min(1.0, (span_f + span_t) / 2.0)
        confidence = h.lone_filer_confidence_floor + weight * (
            h.consensus_confidence_ceiling - h.lone_filer_confidence_floor
        )

    confidence = max(0.0, min(1.0, confidence))
    rationale = ", ".join(factors)[:160] or "neutral"

    return AnalystVerdict(
        lean=lean, magnitude=magnitude, confidence=confidence,
        rationale=rationale, key_factors=factors, is_no_data=False,
    )
```

Add `from agents.analysts.heuristics import SmartMoneyHeuristics` and `from contract.evidence import AnalystVerdict` at the top of the module if not present.

- [ ] **Step 3: Run the derive tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_derive_smart_money_verdict.py -v
```
Expected: all pass.

- [ ] **Step 4: Rewrite `src/agents/analysts/smart_money/agent.py` as a BaseAgent**

Mirror Tasks 7 + 8:

```python
"""Deterministic SmartMoney analyst (Phase 5). LlmAgent removed."""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.events import Event

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import SmartMoneyHeuristics
from agents.analysts.smart_money.fetch import smart_money_fetch_callback
from contract.extractors.smart_money import (
    derive_smart_money_verdict,
    extract_smart_money_features,
)


class SmartMoneyAnalyst(BaseAgent):
    """Per-tick deterministic smart-money verdict for every ticker."""

    h: SmartMoneyHeuristics

    def __init__(self, h: SmartMoneyHeuristics) -> None:
        super().__init__(
            name="SmartMoneyAnalyst",
            before_agent_callback=smart_money_fetch_callback,
            after_agent_callback=make_evidence_callback(
                analyst="smart_money",
                extractor=extract_smart_money_features,
                verdicts_state_key="smart_money_verdicts",
            ),
        )
        object.__setattr__(self, "h", h)

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        """Compute verdicts deterministically — no LLM call."""
        state = ctx.session.state
        tickers = state.get("tickers", []) or []
        data    = state.get("smart_money_data", {}) or {}
        verdicts: list[dict] = []
        for ticker in tickers:
            features = extract_smart_money_features(data.get(ticker, {}), ticker)
            verdict  = derive_smart_money_verdict(features, self.h)
            v_dict = verdict.model_dump(mode="json")
            v_dict["ticker"] = ticker
            verdicts.append(v_dict)
        state["smart_money_verdicts"] = verdicts
        return
        yield  # pragma: no cover


def _build_smart_money_analyst(h: SmartMoneyHeuristics) -> SmartMoneyAnalyst:
    """Construct a fresh SmartMoneyAnalyst — used by the pipeline composer."""
    return SmartMoneyAnalyst(h)
```

Delete the prompts file:

```bash
git rm src/agents/analysts/smart_money/prompts.py
```

- [ ] **Step 5: Update pipeline composition**

In `src/orchestrator/pipeline.py`:
- `_build_smart_money_analyst()` → `_build_smart_money_analyst(h.smart_money)`.

- [ ] **Step 6: Run the full unit + integration smoke suite**

```bash
.venv/bin/python -m pytest tests/unit/ tests/integration/ -v -x
```
Expected: green.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check src/contract/extractors/smart_money.py src/agents/analysts/smart_money/agent.py src/orchestrator/pipeline.py tests/unit/test_derive_smart_money_verdict.py
git add src/contract/extractors/smart_money.py src/agents/analysts/smart_money/agent.py src/orchestrator/pipeline.py tests/unit/test_derive_smart_money_verdict.py
git rm src/agents/analysts/smart_money/prompts.py
git commit -m "feat(phase5): deterministic SmartMoney analyst — replace LlmAgent with BaseAgent"
```

---

## Task 10 — Narrowed Fundamental LLM

**Spec reference:** "Narrowed LLM mandates → FundamentalAnalyst — prose + insider supplement" + "Insider expansion → Fundamental LLM prompt" + Rollout step 10.

**Files:**
- Modify: `src/agents/analysts/fundamental/agent.py` (inject vocab, wire fresh prompt)
- Modify: `src/agents/analysts/fundamental/prompts.py` (full rewrite with closed vocab + insider supplement)
- Modify: `src/orchestrator/pipeline.py` (thread `h.fundamental_vocabulary`)
- Test: `tests/unit/test_fundamental_prompt_render.py` (create)
- Test: `tests/integration/test_fundamental_canned_output.py` (extend or create — schema-validate a canned LLM output)

- [ ] **Step 1: Write failing tests for prompt rendering**

Create `tests/unit/test_fundamental_prompt_render.py`:

```python
"""Tier-1 tests for the Fundamental LLM prompt template."""
from __future__ import annotations

import re

from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import FundamentalVocabulary


def _vocab() -> FundamentalVocabulary:
    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=["regulatory", "litigation", "cybersecurity", "going_concern"],
        insider_signals=["cluster_buying", "cluster_selling", "planned_sale_dominant", "mixed"],
    )


def test_vocabulary_placeholders_resolve():
    """All {placeholder} tokens are substituted by build_fundamental_instruction."""
    rendered = build_fundamental_instruction(_vocab())
    assert "{guidance_options}" not in rendered
    assert "{tone_options}" not in rendered
    assert "{risk_tags}" not in rendered
    assert "{insider_signals}" not in rendered
    # No surviving unfilled braces of the closed-vocab token shape.
    assert not re.search(r"\{[a-z_]+\}", rendered)


def test_vocabulary_values_appear_in_rendered_prompt():
    """Each closed-vocab term lands in the rendered prompt."""
    rendered = build_fundamental_instruction(_vocab())
    for term in ("raised", "maintained", "confident", "cluster_buying", "regulatory"):
        assert term in rendered


def test_insider_supplement_block_present():
    """The rendered prompt contains the insider numerics + footnote block."""
    rendered = build_fundamental_instruction(_vocab())
    assert "INSIDER ACTIVITY" in rendered
    assert "INSIDER FOOTNOTES" in rendered
    assert "insider_net_dollars_30d" in rendered or "net Form-4 dollars" in rendered
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_fundamental_prompt_render.py -v
```
Expected: fails — `build_fundamental_instruction` does not exist or the prompt is the old shape.

- [ ] **Step 2: Rewrite `src/agents/analysts/fundamental/prompts.py`**

```python
"""Fundamental analyst prompt — Phase 5 (closed vocab + insider supplement).

The narrowed Fundamental LLM reads MD&A excerpts, risk-factor excerpts, and
Form 4 footnotes (prose). It also receives a small block of structured
insider numerics (10b5-1 ratio, cluster flags, role rank, derivative counts)
to anchor its prose reasoning in quant context. It emits closed-vocabulary
tags only — no free text in `key_factors`.
"""
from __future__ import annotations

from agents.analysts.heuristics import FundamentalVocabulary


_TEMPLATE = """You are the Fundamental analyst.

For each ticker in the batch, reason over the company's filings prose
(MD&A excerpts, risk factors) AND the insider activity block (numeric flows
+ footnote prose). You must produce a structured verdict per ticker.

Closed vocabulary (use these tags ONLY in key_factors):

  guidance:<value>            ∈ {guidance_options}
  tone:<value>                ∈ {tone_options}
  risk:<value>                ∈ {risk_tags}
                                 (optionally suffixed with _added | _removed | _intensified
                                  when comparing against the prior filing in the dump)
  insider:<value>             ∈ {insider_signals}
  going_concern:true          when going-concern language is present

For each ticker output a JSON object with fields:
  lean         ∈ {{bullish, bearish, neutral}}
  magnitude    ∈ [0, 1]
  confidence   ∈ [0, 1]
  rationale    string ≤160 chars naming the dominant finding
  key_factors  list of closed-vocabulary tags (≤8)
  is_no_data   true if no excerpts AND no insider activity

Decision rule:
- Cluster open-market buys by multiple officers + raised guidance + confident
  tone → strongly bullish.
- Discretionary sale dominance + lowered guidance + cautious/defensive tone
  → strongly bearish.
- Treat 10b5-1 planned sales as low-signal (discount their weight).
- Treat exercise-and-hold as bullish (insider declined to sell).
- Treat exercise-and-dump as bearish.
- Conflicting inputs → neutral with low confidence.

For each ticker the inputs you receive are:

  --- COMPANY FILINGS (PROSE) ---
  {{filings_excerpts}}

  --- INSIDER ACTIVITY (30d, structured) ---
  net Form-4 dollars:           {{insider_net_dollars_30d}}
  buys / sells (count):         {{insider_n_buys_30d}} / {{insider_n_sells_30d}}
  cluster_buying:               {{insider_cluster_buy_flag}}
  cluster_selling:              {{insider_cluster_sell_flag}}
  planned-sale ratio (10b5-1):  {{insider_planned_sale_ratio}}
  top filer role:               {{insider_max_filer_role_name}}
  derivative exercises:         {{insider_derivative_exercise_count}}
  derivative grants:            {{insider_derivative_grant_count}}

  --- INSIDER FOOTNOTES (≤5, prose) ---
  {{insider_footnote_excerpts}}

Emit one JSON object per ticker in a top-level array under the key
`fundamental_verdicts`. Each object must include a `ticker` field.
"""


def build_fundamental_instruction(vocab: FundamentalVocabulary) -> str:
    """Render the Fundamental LLM instruction with the closed vocabulary baked in.

    The double-braced runtime placeholders ({{filings_excerpts}} etc.) survive
    this substitution and are filled by the agent's runtime context-builder.
    """
    return _TEMPLATE.format(
        guidance_options="{" + " | ".join(vocab.guidance) + "}",
        tone_options    ="{" + " | ".join(vocab.tone) + "}",
        risk_tags       ="{" + " | ".join(vocab.risks) + "}",
        insider_signals ="{" + " | ".join(vocab.insider_signals) + "}",
    )
```

The double-brace pattern lets `str.format` leave the runtime placeholders intact for ADK's context formatter.

- [ ] **Step 3: Wire the agent factory to consume the vocabulary**

Edit `src/agents/analysts/fundamental/agent.py`:

```python
def _build_fundamental_analyst(vocab: FundamentalVocabulary) -> LlmAgent:
    """Construct a fresh FundamentalAnalyst LlmAgent with closed-vocab prompt."""
    from agents.analysts.fundamental.prompts import build_fundamental_instruction

    instruction = build_fundamental_instruction(vocab)
    return LlmAgent(
        name="FundamentalAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=instruction,
        output_key="fundamental_verdicts",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=make_evidence_callback(
            analyst="fundamental",
            extractor=extract_fundamental_features,
            verdicts_state_key="fundamental_verdicts",
        ),
    )
```

(Match the exact import set from the existing file — only the body and signature change.)

The runtime context-builder that fills `{filings_excerpts}` / insider placeholders is part of `fundamental/fetch.py` or a small adjacent helper. Per Phase-5 conventions, render those substitutions inside the fetch callback so by the time the LLM runs, the prompt is fully concrete. Concretely: extend the fetch callback (already touched in Task 5) so that `state["fundamental_prompt_context"]` is a dict the prompt template's runtime `.format(**ctx)` consumes. ADK's `LlmAgent` supports `context_builder` injection — wire it to read that state slice.

- [ ] **Step 4: Update pipeline composition**

In `src/orchestrator/pipeline.py`:
- `_build_fundamental_analyst()` → `_build_fundamental_analyst(h.fundamental_vocabulary)`.

- [ ] **Step 5: Run prompt-render tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_fundamental_prompt_render.py -v
```
Expected: all pass.

- [ ] **Step 6: Add a canned-LLM-output schema-validation test**

Create or extend `tests/integration/test_fundamental_canned_output.py`:

```python
"""Schema-validation: a canned 'good' LLM output passes; a bad one fails."""
from __future__ import annotations

import pytest

from contract.evidence import AnalystVerdict


def test_canned_good_verdict_validates():
    """An LLM-shaped dict with closed-vocab tags parses to AnalystVerdict."""
    raw = {
        "lean": "bullish", "magnitude": 0.6, "confidence": 0.7,
        "rationale": "raised guidance + cluster_buying",
        "key_factors": ["guidance:raised", "tone:confident", "insider:cluster_buying"],
        "is_no_data": False,
    }
    AnalystVerdict.model_validate(raw)


def test_canned_bad_verdict_with_out_of_range_magnitude_rejected():
    """magnitude > 1 is rejected by the schema."""
    raw = {
        "lean": "bullish", "magnitude": 1.5, "confidence": 0.7,
        "rationale": "x", "key_factors": [], "is_no_data": False,
    }
    with pytest.raises(Exception):
        AnalystVerdict.model_validate(raw)
```

(The "tags fall in vocabulary" enforcement is a runtime check, not a schema check — that landing strip is the surface-trace step. Schema test confirms the shape only.)

Run:
```bash
.venv/bin/python -m pytest tests/integration/test_fundamental_canned_output.py -v
```
Expected: passes.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check src/agents/analysts/fundamental/ src/orchestrator/pipeline.py tests/unit/test_fundamental_prompt_render.py tests/integration/test_fundamental_canned_output.py
git add src/agents/analysts/fundamental/ src/orchestrator/pipeline.py tests/unit/test_fundamental_prompt_render.py tests/integration/test_fundamental_canned_output.py
git commit -m "feat(phase5): narrowed Fundamental LLM — closed vocab + insider supplement"
```

---

## Task 11 — Narrowed News LLM

**Spec reference:** "Narrowed LLM mandates → NewsAnalyst — prose-only mandate" + Rollout step 11.

**Files:**
- Modify: `src/agents/analysts/news/prompts.py` (inject `NewsVocabulary`, drop polarity numerics)
- Modify: `src/agents/analysts/news/agent.py` (accept `NewsVocabulary`)
- Modify: `src/orchestrator/pipeline.py` (thread `h.news_vocabulary`)
- Test: `tests/unit/test_news_prompt_render.py` (create — same shape as fundamental's)

- [ ] **Step 1: Write failing tests for the news prompt template**

Create `tests/unit/test_news_prompt_render.py`:

```python
"""Tier-1 tests for the News LLM prompt template."""
from __future__ import annotations

import re

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_placeholders_resolve():
    rendered = build_news_instruction(_vocab())
    for tok in ("{catalyst_options}", "{novelty_options}", "{direction_options}"):
        assert tok not in rendered


def test_vocab_terms_present():
    rendered = build_news_instruction(_vocab())
    for term in ("earnings", "guidance", "m_and_a", "high", "medium", "positive", "negative"):
        assert term in rendered


def test_no_polarity_numerics_in_prompt():
    """The news LLM no longer sees polarity statistics — pulled from the prompt."""
    rendered = build_news_instruction(_vocab())
    # Spot-check that historical numeric-block phrasing is absent.
    for forbidden in ("positive_score", "negative_score", "mention_count"):
        assert forbidden not in rendered
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_news_prompt_render.py -v
```
Expected: fails — `build_news_instruction` does not exist yet.

- [ ] **Step 2: Rewrite `src/agents/analysts/news/prompts.py`**

```python
"""News analyst prompt — Phase 5 (closed-vocab, prose-only).

The narrowed News LLM reads headlines + article summaries only. Polarity
statistics that used to live in the prompt are removed; numeric features go
through the extractor channel instead.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary


_TEMPLATE = """You are the News analyst.

For each ticker in the batch, read the supplied headlines and article
summaries. Output a structured verdict per ticker using ONLY the closed
vocabulary below.

Closed vocabulary (use these tags ONLY in key_factors):

  catalyst:<type>     ∈ {catalyst_options}
  novelty:<level>     ∈ {novelty_options}
  direction:<value>   ∈ {direction_options}
  material:<bool>     when material to a long-only fund

For each ticker output a JSON object with fields:
  lean         ∈ {{bullish, bearish, neutral}}
  magnitude    ∈ [0, 1]
  confidence   ∈ [0, 1]
  rationale    string ≤160 chars naming the dominant catalyst
  key_factors  list of closed-vocabulary tags (≤8)
  is_no_data   true if no headlines in the window

Decision rule:
- Lean ←  positive direction → bullish; negative → bearish; mixed/none → neutral.
- Magnitude ← novelty × material.
- Confidence scales with headline count; <3 articles caps confidence low.

Per-ticker inputs:

  --- HEADLINES & SUMMARIES ---
  {{news_excerpts}}

Emit one JSON object per ticker in a top-level array under the key
`news_verdicts`. Each object must include a `ticker` field.
"""


def build_news_instruction(vocab: NewsVocabulary) -> str:
    """Render the News LLM instruction with the closed vocabulary baked in."""
    return _TEMPLATE.format(
        catalyst_options ="{" + " | ".join(vocab.catalysts) + "}",
        novelty_options  ="{" + " | ".join(vocab.novelty) + "}",
        direction_options="{" + " | ".join(vocab.direction) + "}",
    )
```

- [ ] **Step 3: Update the agent factory to accept the vocabulary**

Edit `src/agents/analysts/news/agent.py`:

```python
def _build_news_analyst(vocab: NewsVocabulary) -> LlmAgent:
    """Construct a fresh NewsAnalyst LlmAgent with closed-vocab prompt."""
    from agents.analysts.news.prompts import build_news_instruction

    instruction = build_news_instruction(vocab)
    return LlmAgent(
        name="NewsAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=instruction,
        output_key="news_verdicts",
        before_agent_callback=news_fetch_callback,
        after_agent_callback=make_evidence_callback(
            analyst="news",
            extractor=extract_news_features,
            verdicts_state_key="news_verdicts",
        ),
    )
```

- [ ] **Step 4: Update pipeline composition**

In `src/orchestrator/pipeline.py`:
- `_build_news_analyst()` → `_build_news_analyst(h.news_vocabulary)`.

- [ ] **Step 5: Run news prompt-render tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_news_prompt_render.py -v
```
Expected: passes.

- [ ] **Step 6: Full test sweep**

```bash
.venv/bin/python -m pytest tests/unit/ tests/integration/ -v -x
```
Expected: green.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check src/agents/analysts/news/ src/orchestrator/pipeline.py tests/unit/test_news_prompt_render.py
git add src/agents/analysts/news/ src/orchestrator/pipeline.py tests/unit/test_news_prompt_render.py
git commit -m "feat(phase5): narrowed News LLM — closed vocab + prose-only mandate"
```

---

## Task 12 — Persistence Index

**Spec reference:** "Persistence — KB-readiness without migration → One new index" + Rollout step 12.

**Files:**
- Modify: `src/orchestrator/persistence.py` (composite Index)
- Test: `tests/unit/test_evidence_index.py` (create)

- [ ] **Step 1: Write the failing metadata-introspection test**

Create `tests/unit/test_evidence_index.py`:

```python
"""SQLAlchemy metadata introspection — composite (analyst, ticker, recorded_at) index."""
from __future__ import annotations


def test_analyst_evidence_has_composite_lookup_index():
    """The Phase-5 composite index is declared on AnalystEvidenceRow."""
    from orchestrator.persistence import AnalystEvidenceRow

    declared = {ix.name for ix in AnalystEvidenceRow.__table__.indexes}
    assert "ix_analyst_evidence_lookup" in declared

    target = next(
        ix for ix in AnalystEvidenceRow.__table__.indexes
        if ix.name == "ix_analyst_evidence_lookup"
    )
    cols = [c.name for c in target.columns]
    assert cols == ["analyst", "ticker", "recorded_at"]
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_evidence_index.py -v
```
Expected: fails — index is not declared.

- [ ] **Step 2: Declare the index in `persistence.py`**

Find the `AnalystEvidenceRow` class declaration and add `Index('ix_analyst_evidence_lookup', 'analyst', 'ticker', 'recorded_at')` to its `__table_args__`. Also add `Index` to the SQLAlchemy import line:

```python
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, UniqueConstraint, create_engine

class AnalystEvidenceRow(Base):
    __tablename__ = "analyst_evidence"
    __table_args__ = (
        # (existing UniqueConstraint or other args here, if any),
        Index("ix_analyst_evidence_lookup", "analyst", "ticker", "recorded_at"),
    )
    # ...existing column definitions...
```

If the class already has `__table_args__`, append the `Index(...)` to the existing tuple. If not, create it as shown above.

- [ ] **Step 3: Run the index test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_evidence_index.py -v
```
Expected: passes.

- [ ] **Step 4: Run the full unit suite — make sure no existing test breaks**

```bash
.venv/bin/python -m pytest tests/unit/ -v -x
```
Expected: green.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check src/orchestrator/persistence.py tests/unit/test_evidence_index.py
git add src/orchestrator/persistence.py tests/unit/test_evidence_index.py
git commit -m "feat(phase5): composite analyst_evidence index (analyst, ticker, recorded_at)"
```

---

## Task 13 — Surface Tracing Harness

**Spec reference:** "Surface tracing — first live-LLM validation" + Rollout step 13. The harness adds **no** production-path behaviour — every hook is gated behind `state.get("_trace")`.

**Files:**
- Create: `src/observability/__init__.py`
- Create: `src/observability/trace.py`
- Create: `scripts/trace_tick.py`
- Modify: `.gitignore` (add `docs/surface-traces/`)
- Modify: per-analyst fetch callbacks, deterministic BaseAgents, digest builder, risk gate, executor — each gets a single `_trace_maybe(...)` line at the relevant boundary.
- Test: `tests/unit/test_trace_writer.py` (create)
- Test: `tests/unit/test_trace_maybe_noop.py` (create)

- [ ] **Step 1: Write failing tests for `TraceWriter`**

Create `tests/unit/test_trace_writer.py`:

```python
"""Tier-1 tests for the surface-trace writer."""
from __future__ import annotations

import json
from pathlib import Path

from observability.trace import TraceWriter, _trace_maybe


def test_snapshot_appends_section():
    """snapshot() appends a labelled JSON section in insertion order."""
    tw = TraceWriter()
    tw.snapshot("01_fetch_news", {"AAPL": {"headlines": []}})
    tw.snapshot("01_fetch_social", {"AAPL": {"reddit": {}}})
    assert list(tw._sections.keys()) == ["01_fetch_news", "01_fetch_social"]


def test_llm_pair_writes_in_and_out_sections():
    """llm_pair writes label_in and label_out adjacent."""
    tw = TraceWriter()
    tw.llm_pair("03_fundamental_llm", "PROMPT TEXT", "RESPONSE TEXT", model="gemini-2.5-flash-lite")
    assert "03_fundamental_llm_in" in tw._sections
    assert "03_fundamental_llm_out" in tw._sections


def test_finalise_writes_json(tmp_path: Path):
    """finalise() writes a single JSON document with all sections."""
    tw = TraceWriter()
    tw.snapshot("01_x", {"a": 1})
    out = tmp_path / "trace.json"
    tw.finalise(out)
    body = json.loads(out.read_text())
    assert body["01_x"] == {"a": 1}
```

Create `tests/unit/test_trace_maybe_noop.py`:

```python
"""_trace_maybe is a single dict-lookup no-op when state has no '_trace' key."""
from __future__ import annotations


def test_trace_maybe_returns_quickly_with_no_trace():
    """No '_trace' in state → no allocation, no exception."""
    from observability.trace import _trace_maybe
    _trace_maybe({}, "01_x", {"data": "payload"})


def test_trace_maybe_routes_to_writer():
    """'_trace' in state → snapshot routed to the writer."""
    from observability.trace import TraceWriter, _trace_maybe
    tw = TraceWriter()
    _trace_maybe({"_trace": tw}, "01_x", {"data": 1})
    assert "01_x" in tw._sections
```

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_trace_writer.py tests/unit/test_trace_maybe_noop.py -v
```
Expected: fails — `observability` module does not exist.

- [ ] **Step 2: Create `src/observability/__init__.py`**

```python
"""Observability primitives — TraceWriter for the Phase-5 surface trace."""
```

- [ ] **Step 3: Create `src/observability/trace.py`**

```python
"""Append-only JSON snapshot collector for one tick.

Production runs do not instantiate this; the `trace_tick.py` entrypoint
sets `state["_trace"]` to a TraceWriter, and every callback opportunistically
routes through `_trace_maybe(state, ...)`. Production tick state has no
`"_trace"` key, so the helper is a single dict lookup no-op.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TraceWriter:
    """Collect labelled JSON sections for one tick; flush to disk on demand."""

    def __init__(self) -> None:
        # OrderedDict semantics; Python 3.7+ dicts are insertion-ordered.
        self._sections: dict[str, Any] = {}

    def snapshot(
        self,
        label: str,
        payload: Any,
        *,
        state_keys: list[str] | None = None,
    ) -> None:
        """Append one labelled JSON section to the trace."""
        record: dict[str, Any] = {"data": payload}
        if state_keys is not None:
            record["state_keys"] = state_keys
        self._sections[label] = record

    def llm_pair(
        self,
        label_base: str,
        prompt: str,
        response: str,
        *,
        model: str,
    ) -> None:
        """Append a paired LLM in/out section."""
        self._sections[f"{label_base}_in"]  = {"model": model, "prompt": prompt}
        self._sections[f"{label_base}_out"] = {"model": model, "response": response}

    def finalise(self, out_path: Path) -> None:
        """Flush the trace to disk as a single JSON document.

        The output is one object keyed by label; section order is preserved.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(self._sections, indent=2, default=str))


def _trace_maybe(
    state: dict[str, Any],
    label: str,
    payload: Any,
    *,
    state_keys: list[str] | None = None,
) -> None:
    """No-op trace hook — calls TraceWriter.snapshot iff state['_trace'] is set.

    Single dict lookup in the no-trace path; safe to sprinkle at every
    pipeline boundary without measurable overhead.
    """
    tw = state.get("_trace") if isinstance(state, dict) else None
    if tw is None:
        return
    tw.snapshot(label, payload, state_keys=state_keys)
```

- [ ] **Step 4: Run the trace tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_trace_writer.py tests/unit/test_trace_maybe_noop.py -v
```
Expected: passes.

- [ ] **Step 5: Sprinkle `_trace_maybe(...)` hooks at every pipeline boundary**

For each touchpoint, import `_trace_maybe` from `observability.trace` and add one call at the end of the relevant function:

| Module | Hook | Label |
|---|---|---|
| `src/agents/analysts/technical/fetch.py` (`technical_fetch_callback`) | end of callback | `01_fetch_technical` |
| `src/agents/analysts/fundamental/fetch.py` | end of callback | `01_fetch_fundamental` |
| `src/agents/analysts/news/fetch.py`     | end of callback | `01_fetch_news` |
| `src/agents/analysts/social/fetch.py`   | end of callback | `01_fetch_social` |
| `src/agents/analysts/smart_money/fetch.py` | end of callback | `01_fetch_smart_money` |
| `src/agents/analysts/technical/agent.py` (`_run_async_impl`) | after verdicts written | `02_technical_verdict` |
| `src/agents/analysts/social/agent.py`       | after verdicts written | `02_social_verdict` |
| `src/agents/analysts/smart_money/agent.py`  | after verdicts written | `02_smart_money_verdict` |
| `src/contract/evidence_writer.py` (or `agents/contract/evidence_writer.py`) → after `build_ticker_evidence(...)` returns | `04_digest` |
| `src/agents/risk_gate/agent.py` | before clamp loop ←→ after | `06_risk_gate_in` / `06_risk_gate_out` |
| `src/agents/executor/agent.py`  | after broker calls collected | `07_broker_calls` |

For the two LLM agents (Fundamental + News + Strategist), add ADK `before_model_callback` / `after_model_callback` hooks **only when `state.get("_trace")` is set** that call `tw.llm_pair(...)`. The simplest path: in each LLM agent's `_build_*` factory, check `if os.environ.get("STOCKBOT_TRACE") == "1":` and attach the callbacks; `scripts/trace_tick.py` sets that env var.

Each touch should be one line of the shape:

```python
_trace_maybe(ctx.state, "01_fetch_news", payload)
```

Pull `state` from whichever object the function has in scope (`ctx.state` for callbacks, `ctx.session.state` for BaseAgent bodies). Pass the payload that boundary just produced.

- [ ] **Step 6: Create `scripts/trace_tick.py`**

```python
"""Single-ticker surface-trace entrypoint.

Usage:
    PYTHONPATH=src python -m scripts.trace_tick --ticker AAPL [--out docs/surface-traces/]

Runs one full hourly tick with the production pipeline against the real
LLM, paper broker, against a single ticker. Captures a labelled JSON
trace at every pipeline boundary and writes it to disk.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from observability.trace import TraceWriter


async def main_async(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticker", required=True)
    p.add_argument("--out", default="docs/surface-traces")
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Mark the process as trace mode so LLM-side callbacks attach themselves.
    os.environ["STOCKBOT_TRACE"] = "1"

    # Build the production pipeline. Adjust imports/wiring to match the
    # actual scripts/initialise.py + orchestrator/tick.py entrypoint.
    from broker.trading212 import Trading212Broker
    from orchestrator.pipeline import build_pipeline
    from orchestrator.tick import run_once

    import httpx
    broker = Trading212Broker(
        mode="paper",
        api_key=os.environ["TRADING212_API_KEY"],
        http_client=httpx.AsyncClient(),
        instrument_map={},
    )

    pipeline = build_pipeline(broker)
    tick_id = f"trace-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S')}"

    tw = TraceWriter()
    state = {
        "_trace": tw,
        "tick_id": tick_id,
        "tickers": [args.ticker],
    }

    try:
        await run_once(pipeline, state)
    except Exception as exc:  # noqa: BLE001
        # Flush the partial trace before propagating.
        path = out_dir / f"{tick_id}-{args.ticker}-PARTIAL.json"
        tw.finalise(path)
        print(f"✗ trace tick failed; partial written to {path}", file=sys.stderr)
        print(f"  cause: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    path = out_dir / f"{tick_id}-{args.ticker}.json"
    tw.finalise(path)
    print(f"✓ trace written to {path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
```

Adjust the broker / pipeline / `run_once` imports to match the actual project entrypoint shape — read `scripts/smoke_run.py` and mirror it where the trace tick differs.

- [ ] **Step 7: Add `docs/surface-traces/` to `.gitignore`**

```bash
# Edit .gitignore: append a new line
docs/surface-traces/
```

- [ ] **Step 8: Run the full test suite once more**

```bash
.venv/bin/python -m pytest tests/ -v -x
```
Expected: green. The `_trace_maybe` hooks must not break any existing behaviour (the no-op path).

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/python -m ruff check src/observability/ scripts/trace_tick.py tests/unit/test_trace_writer.py tests/unit/test_trace_maybe_noop.py
git add src/observability/ scripts/trace_tick.py .gitignore tests/unit/test_trace_writer.py tests/unit/test_trace_maybe_noop.py \
        src/agents/analysts/ src/agents/risk_gate/ src/agents/executor/ src/contract/
git commit -m "feat(phase5): surface-trace harness — TraceWriter + opt-in hooks via state['_trace']"
```

---

## Task 14 — Live Validation (Acceptance Gate)

**Spec reference:** "Surface tracing — first live-LLM validation → What the baseline trace validates" + Rollout step 14. This is the **manual** step — no automated checks.

**Files:**
- Verify: `.env` contains `TRADING212_API_KEY` and `GOOGLE_API_KEY` (or whichever Gemini auth path the project uses; ask the user if uncertain).
- Run: `scripts/trace_tick.py --ticker AAPL`
- Inspect: `docs/surface-traces/<tick_id>-AAPL.json`
- Append: one `## YYYY-MM-DD` entry to `graphify-out/graph_delta.md` (local only; never commit)
- Delete: `docs/Phase5-analyst-refine/analyst-llm-narrowing.md` (already done at plan-write time)

- [ ] **Step 1: Verify the .env**

Check that `.env` contains the credentials the production pipeline needs. Do **not** print the values. If `.env` is missing a Gemini key, **stop and ask the user** rather than guessing the variable name (per the temporary cross-platform note in the user's global CLAUDE.md — anything env-sensitive should be confirmed).

```bash
grep -E '^(TRADING212_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY)=' .env >/dev/null && echo "env vars present" || echo "missing — ask the user"
```

- [ ] **Step 2: Run the trace tick**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.trace_tick --ticker AAPL
```

Expected: prints `✓ trace written to docs/surface-traces/<tick_id>-AAPL.json`. If the script exits non-zero, inspect the `*-PARTIAL.json` file to identify which boundary failed, fix the cause, re-run.

- [ ] **Step 3: Eyeball the trace JSON**

Open the trace file and walk top-to-bottom. Check the acceptance bullets from the spec:

- **`01_fetch_*` sections** carry the per-ticker raw payloads with sensible shapes (no empties for ticker AAPL on a market-open run).
- **`02_*_verdict` sections** show `features` populated and `verdict.lean / magnitude / confidence` non-trivial.
- **`03_fundamental_llm_in`** contains the rendered insider supplement block: numeric flows + at least zero or more footnote excerpts. Vocabulary tokens appear in the closed-vocab section.
- **`03_fundamental_llm_out`** — verify the LLM emits `key_factors` entries that match the vocabulary. Reject if you see invented tags like `risk:debt_problems`.
- **`03_news_llm_in`** — only news content, no polarity numerics.
- **`03_news_llm_out`** — closed-vocab adherence.
- **`04_digest`** — `ticker_evidence` carries five analyst rows (technical / fundamental / news / social / smart_money) for AAPL.
- **`05_strategist_llm_in`** — strategist sees the new 5-analyst evidence shape correctly rendered.
- **`06_*`** — risk-gate clamp records present and reasonable.
- **`07_broker_calls`** — calls landed against the paper broker.

- [ ] **Step 4: Iterate as needed**

If the trace surfaces any of these, fix in a follow-up commit before declaring acceptance:
- LLM emits an out-of-vocab tag → tighten prompt language (`prompts.py` for the offender).
- Insider supplement renders garbled / missing → fix the fetch's context builder (`fundamental/fetch.py`).
- Deterministic verdict has clearly wrong lean for AAPL on a known-uptrend day → revisit the heuristic numbers in `config/analyst_heuristics.json`.

For each fix: new commit on this PR, re-run `trace_tick.py`, replace the trace file.

- [ ] **Step 5: Confirm the old plan file is gone**

```bash
test -e docs/Phase5-analyst-refine/analyst-llm-narrowing.md && echo "still present — delete it" || echo "✓ deleted"
```

(It was deleted at plan-write time; this step is a guard for execution that diverges from that.)

- [ ] **Step 6: Update `graphify-out/graph_delta.md` (local only, never commit)**

Append one combined dated section summarising all phase-5 structural changes:

```
## YYYY-MM-DD — phase 5 analyst re-categorisation

Re-shaped the analyst pool from 4 → 5 children. Technical, Social,
SmartMoney are now BaseAgent (deterministic). Fundamental, News are
narrowed LlmAgent. Insider moved from SmartMoney → Fundamental with
Form 4 deep-pull (footnotes, codes, 10b5-1, derivatives).

- New nodes: agents.analysts.social.{agent,fetch,__init__},
  contract.extractors.social, observability.trace, scripts.trace_tick,
  data.models.{InsiderDerivativeTrade, Form4Bundle},
  agents.analysts.heuristics, contract.extractors.{technical,smart_money}
  → derive_*_verdict functions.
- Renamed: agents.analysts.sentiment → agents.analysts.news;
  contract.extractors.sentiment → contract.extractors.news;
  state key sentiment_data → news_data.
- Removed: agents.analysts.{technical,smart_money}.prompts;
  insider branch from smart_money fetch + extractor;
  social_sentiment branch from news (formerly sentiment) fetch.
- New edges: orchestrator.pipeline → social analyst factory; fundamental
  fetch → insider provider; lifecycle.initialise → heuristics loader.
```

**Do NOT `git add` this file.** It is gitignored.

- [ ] **Step 7: Final acceptance**

Run the full suite once more:

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
```

Expected: green on both. With the live trace in `docs/surface-traces/` (uncommitted) and all 14 tasks landed, the PR is ready for human review.

Do **not** push or open a PR autonomously — the user explicitly said "dont commit yet though id like to update phase 5 first" during the brainstorming phase, and the project's CLAUDE.md says to confirm before externally visible actions.

---

## Plan Self-Review Checklist (for the author — already run)

1. **Spec coverage** — every section of `spec.md` maps to at least one task:
   - "Why" + "Goals" / "Non-goals" — context, no code; reflected throughout.
   - "Analyst pool — final shape" — Tasks 6/7/8/9/10/11 (one per analyst) + pipeline composition.
   - "Data re-categorisation" — Tasks 4/5/6/7 (each analyst's fetch + extractor rewiring).
   - "Insider expansion" — Task 3 (model + parser) + Task 5 (extractor consumption) + Task 10 (LLM supplement).
   - "Deterministic verdict heuristics" — Tasks 7 (Social) + 8 (Technical) + 9 (Smart-money).
   - "Narrowed LLM mandates" — Tasks 10 (Fundamental) + 11 (News).
   - "Contract invariants" + `AnalystName` change — Task 2.
   - "Configuration" + heuristics — Task 1.
   - "Persistence" — Task 12.
   - "Surface tracing" — Task 13.
   - "Test strategy" — embedded in each task's TDD steps (T1 throughout, T2 in Tasks 6/7/8/11, T3 in Task 14).
   - "Rollout" — exactly mirrored as 14 tasks.

2. **Placeholder scan** — no "TBD", no "implement later", no "see comment", no "similar to Task N" without showing code. Every code step includes the actual code an engineer would type. The few "adjust import path to match the actual fetch.py" notes are guardrails for the engineer to verify against a real file path, not placeholders for missing code.

3. **Type consistency** —
   - `AnalystName` Literal: `"technical" | "fundamental" | "news" | "social" | "smart_money"` (Tasks 2, 6, 7).
   - Heuristics class names: `TechnicalHeuristics`, `SocialHeuristics`, `SmartMoneyHeuristics`, `FundamentalVocabulary`, `NewsVocabulary`, `GoldenSetConfig`, `AnalystHeuristics` (Task 1, used in Tasks 7-11).
   - Function names: `derive_technical_verdict` / `derive_social_verdict` / `derive_smart_money_verdict` (Tasks 7/8/9 — same naming scheme).
   - Factory names: `_build_<name>_analyst(h_or_vocab)` (Tasks 1/7/8/9/10/11 — same signature shape).
   - Insider models: `InsiderTrade` + `InsiderDerivativeTrade` + `Form4Bundle` (Task 3, used in Tasks 5, 10).
   - State keys: `{analyst}_data` / `{analyst}_verdicts` / `{analyst}_evidence` (every task).
   - Insider feature columns: spelled identically across spec, tests, and extractor implementation (Task 5).

The plan is self-contained for a fresh engineer with no prior context.

---

## Execution Handoff

**Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task; review between tasks; fast iteration. Per the writing-plans skill, this uses `superpowers:subagent-driven-development`. Each task above is self-contained and matches the subagent dispatch contract (clear "Files", complete TDD steps, exact commit subject).

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans` with batch execution and checkpoints for review.

The user is expected to choose between these two paths.


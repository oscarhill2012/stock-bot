# Spec A — Surgical Correctness and Input Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land all twenty-one Spec A fixes (S1–S9, D1.1–D1.3, D2.1, H4, M1, M3, M4, M5, R1–R5) in one execution pass so the first follow-up backtest is run against a clean pipeline.

**Architecture:** Five bands of overlapping file surface, threaded in dependency order — (1) config + schema, (2) pipeline code, (3) analyst prompts, (4) strategist prompt + evidence renderer, (5) observability / backtest reporting. Band (1) lands first because rendered prompts in bands (3) and (4) read from the new config values. Bands (2) and (5) are independent and can be parallelised once (1) is in.

**Tech Stack:** Python 3.14, Pydantic v2 (`model_validator`, `Field(max_length=…)`), Google ADK `BaseAgent`/`LlmAgent`, tenacity (`AsyncRetrying`, `before_sleep` hook), pytest. All commands run from project root with `PYTHONPATH=src .venv/bin/python …`.

---

## File Map

Files this plan creates or modifies. One responsibility per file; tasks below produce self-contained changes.

### Created

| Path | Responsibility |
|---|---|
| `config/risk_gate.json` | Default values for the five risk-gate constants (R1/R2/R3 land here as JSON values) |
| `src/config/risk_gate.py` | Loader for `config/risk_gate.json` — mirrors `src/config/strategist.py` pattern (R4) |
| `tests/backtest/test_reference_prices.py` | S1 — Phase 2 PIT-clamp behaviour for `reference_prices` |
| `tests/executor/test_executor_bookkeeping.py` | S2 — 1 % trim vs full-exit thesis bookkeeping |
| `tests/backtest/test_cache_hits_audit.py` | S3 — audit `cache_hits` matches structured-log count |
| `tests/unit/test_reporting_span_names.py` | S4 — `name.startswith(...)` span-name fix |
| `tests/unit/test_decision_logger_strict_serialiser.py` | S5 — strict serialiser raises on un-dumpable types |
| `tests/unit/agents/strategist/test_decision_tag_derivation.py` | S6 — `decision_tag` enum over (prior, new) pairs |
| `tests/unit/test_trace_writer_exception_logging.py` | S7 — `logger.exception` inside `contextlib.suppress` |
| `tests/backtest/test_tripwire_advisory_rename.py` | S8 — renamed tripwires no longer counted as actionable |
| `tests/agents/test_llm_retry_agent_name.py` | S9 — retry warning carries wrapped agent name |
| `tests/contract/test_evidence_schema.py` | D1.1 — `model_validator` rejects `report=None` when `is_no_data=False` |
| `tests/unit/test_news_prompt_report_required.py` | D1.2 — news prompt strengthened wording |
| `tests/unit/test_fundamental_prompt_report_required.py` | D1.2 — fundamental prompt strengthened wording |
| `tests/unit/agents/strategist/test_evidence_view_missing_report.py` | D1.3 — visibility placeholder for degenerate `report=None` |
| `tests/unit/test_fundamental_prompt_decision_rule.py` | D2.1 — neutral-anchored fundamental decision rule |
| `tests/unit/test_analyst_config_rationale_budget.py` | H4 — derived property + clamp behaviour |
| `tests/unit/test_analyst_prompts_anti_truncation.py` | M1 — anti-truncation guard line present |
| `tests/unit/test_news_prompt_bearish_nudge.py` | M4 — news bearish-trigger guidance present |
| `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py` | M3 — `[Social]` rows omitted when all `is_no_data=True` |
| `tests/unit/test_strategist_prompt_worked_examples_ticker.py` | M5 — `XYZ` replaces `AAPL` in worked examples |
| `tests/unit/test_risk_gate_config_loader.py` | R4 — loader contract test |
| `tests/unit/test_strategist_prompt_risk_substitutions.py` | R5 — prompt substitutes config-driven percentages |

### Modified

| Path | Why |
|---|---|
| `src/config/analysts.py` | H4 — add `verdict_rationale_prompt_headroom_chars` field + derived property |
| `src/contract/evidence.py` | D1.1 — add `model_validator` to `AnalystVerdict` |
| `src/orchestrator/state.py` | R4 — import constants from new loader, keep re-exported names |
| `src/backtest/runner.py` | S1 — relocate `_seed_reference_prices` to Phase 2 (per tick) |
| `src/agents/executor/agent.py` | S2 — gate `del positions[…]` and `save_trade_log_entry` on true close |
| `src/agents/analysts/report_cache.py` | S3 — stop direct state mutation (delete the `_report_cache_hits_for_audit` write) |
| `src/backtest/driver.py` | S3 — drain `obs/logs/` instead of `state["_report_cache_hits_for_audit"]` |
| `src/backtest/audit/telemetry.py` | S3 — receive `report_cache_hits` from log drain rather than state |
| `src/backtest/reporting.py` | S4 — `name.startswith(...)` + rename fill-count metric |
| `src/agents/analysts/fundamental/fetch_agent.py` | S5 — `.model_dump()` on `insider_bundle` |
| `src/backtest/decision_logger.py` | S5 — recursive `_coerce` + strict default serialiser |
| `src/agents/strategist/derivation.py` | S6 — derive `decision_tag` from (prior, new) weight |
| `src/observability/trace.py` | S7 — `logger.exception` inside `contextlib.suppress` |
| `src/backtest/audit/tripwires.py` | S8 — rename two tripwires to `*_advisory` + drop from actionable set |
| `src/agents/llm_retry.py` | S9 — closure-based `before_sleep` that carries `self.inner.name` |
| `src/agents/analysts/news/prompts.py` | D1.2 + M1 + M4 + H4 — strengthened wording, anti-truncation, bearish nudge, derived budget |
| `src/agents/analysts/fundamental/prompts.py` | D1.2 + D2.1 + M1 + H4 — strengthened wording, decision-rule rewrite, anti-truncation, derived budget |
| `src/agents/strategist/evidence_view.py` | D1.3 + M3 — placeholder for `report=None`; drop dead Social rows |
| `src/agents/strategist/prompts.py` | M5 + R5 — `XYZ` ticker; risk-rule restatement with config-driven percentages |
| `config/README.md` | H4 + R4 — document new fields + new `risk_gate.json` file |
| `config/analysts.json` | H4 — add `verdict_rationale_prompt_headroom_chars: 50` to `output_caps` |

---

## Implementation Order

Five bands; tasks numbered top-to-bottom inside this plan. **Band (1) must land before bands (3) and (4)** because the rendered prompts in those bands read from the new config values. Bands (2) and (5) are independent of (1).

- **Band 1 — Config + schema layer** (Tasks 1–3): H4, D1.1, R4 (with R1/R2/R3 as JSON values).
- **Band 2 — Pipeline code** (Tasks 4–9): S1, S2, S3, S5, S6, S9.
- **Band 3 — Analyst prompts** (Tasks 10–13): D1.2, D2.1, H4-switch, M1, M4.
- **Band 4 — Strategist prompt + evidence** (Tasks 14–17): D1.3, M3, M5, R5.
- **Band 5 — Observability / backtest reporting** (Tasks 18–20): S4, S7, S8.

---

## Band 1 — Config + Schema Layer

### Task 1 — H4: derived rationale prompt budget on `AnalystsConfig`

**Files:**
- Modify: `src/config/analysts.py`
- Modify: `config/analysts.json`
- Modify: `config/README.md`
- Create: `tests/unit/test_analyst_config_rationale_budget.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_analyst_config_rationale_budget.py`:

```python
"""H4 — derived rationale prompt budget on AnalystsConfig.

The schema cap (``verdict_rationale_max_chars``) absorbs the LLM's natural
overshoot via ``slack_percent``.  The prompt-facing budget sits *below* the
schema cap so the LLM has room to overshoot without tripping schema
validation.  This module verifies the derived property's behaviour at the
four interesting points along the headroom axis.
"""
from __future__ import annotations

import json
from pathlib import Path

from config.analysts import load_analysts_config


def _write_config(tmp_path: Path, *, cap: int, headroom: int) -> Path:
    """Build a minimal ``config/analysts.json``-shaped fixture file.

    Only the fields required by ``AnalystsConfig`` are populated; sensible
    defaults are used for every other knob so the loader does not fail
    validation on the surrounding shape.
    """
    payload = {
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 50,
            "max_summary_chars": 800,
            "llm": {"http_timeout_seconds": 60, "max_output_tokens": 2048},
        },
        "fundamental": {
            "max_filing_mda_chars":       8000,
            "max_filing_risk_chars":      4000,
            "max_insider_footnotes":      10,
            "max_insider_footnote_chars": 800,
            "llm": {"http_timeout_seconds": 60, "max_output_tokens": 2048},
        },
        "cache": {"enabled": False, "directory": "/tmp/cache"},
        "output_caps": {
            "verdict_rationale_max_chars":              cap,
            "verdict_rationale_prompt_headroom_chars":  headroom,
            "report_summary_max_chars":                 2000,
            "report_driver_name_max_chars":             80,
            "report_driver_body_max_chars":             400,
        },
    }
    p = tmp_path / "analysts.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_default_headroom_subtracts(tmp_path: Path) -> None:
    """200 cap minus 50 headroom yields a 150-char prompt budget."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=50))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 150


def test_zero_headroom_returns_cap(tmp_path: Path) -> None:
    """Zero headroom — the prompt budget equals the schema cap."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=0))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 200


def test_negative_headroom_clamps_to_cap(tmp_path: Path) -> None:
    """Negative headroom would push the budget above the cap — clamp to cap."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=-50))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 200


def test_oversize_headroom_clamps_to_floor(tmp_path: Path) -> None:
    """Headroom > cap would yield ≤0 — clamp to the 40-char floor."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=500))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_analyst_config_rationale_budget.py -v`

Expected: FAIL with `AttributeError: 'OutputCaps' object has no attribute 'verdict_rationale_prompt_budget'` (or `ValidationError` on the unknown `verdict_rationale_prompt_headroom_chars` field).

- [ ] **Step 3: Add the field + derived property to `OutputCaps`**

Open `src/config/analysts.py`. Locate the `OutputCaps` class (around lines 92–121). Inside the class body, immediately after `verdict_rationale_max_chars`, add the headroom field:

```python
    verdict_rationale_max_chars:    int = Field(ge=50,  le=1000)

    # Prompt-facing headroom — derived budget shown to the LLM is
    # ``verdict_rationale_max_chars - verdict_rationale_prompt_headroom_chars``
    # (with safety clamps).  Keeps the prompt tighter than the schema cap so
    # the LLM's natural 1–5 % overshoot does not trip ``string_too_long``.
    verdict_rationale_prompt_headroom_chars: int = Field(ge=-100, le=1000, default=50)

    report_summary_max_chars:       int = Field(ge=200, le=8000)
    report_driver_name_max_chars:   int = Field(ge=20,  le=200)
    report_driver_body_max_chars:   int = Field(ge=100, le=4000)
```

Then add the derived property immediately below the field declarations, still inside the `OutputCaps` class:

```python
    @property
    def verdict_rationale_prompt_budget(self) -> int:
        """Prompt-facing rationale budget — the value the LLM is told.

        Derived from the schema-facing cap minus the configured headroom so
        raising or lowering ``verdict_rationale_max_chars`` automatically
        re-tunes what the LLM is asked to produce.  The result is clamped on
        both sides:
          * lower bound 40 — a meaningless or negative budget can never
            reach the prompt (catches headroom > cap misconfigurations);
          * upper bound ``verdict_rationale_max_chars`` — the prompt budget
            can never exceed the schema cap, defeating the purpose (catches
            negative-headroom misconfigurations).
        """

        budget = (
            self.verdict_rationale_max_chars
            - self.verdict_rationale_prompt_headroom_chars
        )
        return max(40, min(self.verdict_rationale_max_chars, budget))
```

- [ ] **Step 4: Add the field to `config/analysts.json`**

Open `config/analysts.json` and add the new key inside `output_caps`. The exact placement is just below `verdict_rationale_max_chars`:

```json
    "output_caps": {
        "verdict_rationale_max_chars":              200,
        "verdict_rationale_prompt_headroom_chars":  50,
        "report_summary_max_chars":                 2000,
        ...
    }
```

(Preserve the existing values for the other fields — the snippet shows the new key in context.)

- [ ] **Step 5: Document in `config/README.md`**

Open `config/README.md`. Locate the `analysts.json` section (search for `## \`analysts.json\``). Add a row to the `output_caps` table describing the new field:

```markdown
| `output_caps.verdict_rationale_prompt_headroom_chars` | int | Headroom subtracted from the schema cap to derive the prompt-facing rationale budget. Keeps the LLM's natural overshoot inside the +slack_percent schema cap. Default 50 — raise if a future LLM tightens its character-counting; lower toward 0 if you want the LLM to use the full cap. |
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_analyst_config_rationale_budget.py -v`

Expected: 4 passed.

- [ ] **Step 7: Run the wider analyst-config tests to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ -k "analyst_config or analysts_config" -v`

Expected: existing analyst-config tests still pass (no breakage from the new field).

- [ ] **Step 8: Commit**

```bash
git add src/config/analysts.py config/analysts.json config/README.md tests/unit/test_analyst_config_rationale_budget.py
git commit -m "$(cat <<'EOF'
feat(config): derived rationale prompt budget on AnalystsConfig (H4)

Adds verdict_rationale_prompt_headroom_chars + verdict_rationale_prompt_budget
derived property so the prompt-facing budget sits below the schema cap by a
config-tunable margin.  Lays the groundwork for the analyst prompts to switch
from the schema cap to the derived budget (next pass).
EOF
)"
```

---

### Task 2 — D1.1: `model_validator` enforcing report-required on `AnalystVerdict`

**Files:**
- Modify: `src/contract/evidence.py`
- Create: `tests/contract/test_evidence_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/contract/test_evidence_schema.py`:

```python
"""D1.1 — schema-level enforcement that ``report`` accompanies non-no-data verdicts.

The News and Fundamental analyst LLMs were silently emitting
``report: null`` on a non-trivial fraction of ``is_no_data=False`` verdicts
in the baseline-2025-09 run (30.7 % and 3.6 % respectively).  The schema
previously declared ``report: AnalystReport | None = None``, which made
``report=None`` *valid*; the prompt instructed the LLM otherwise but the
schema did not enforce.

This module covers the new ``model_validator`` that rejects the
``is_no_data=False, report=None`` combination at the contract boundary.
``llm_retry`` already classifies ``pydantic.ValidationError`` as retryable,
so an offending LLM response triggers ADK's existing retry path
automatically.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver


def _valid_report() -> AnalystReport:
    """Build a minimal valid ``AnalystReport`` for round-trip tests."""

    return AnalystReport(
        summary="Test summary — exercises the report-required validator.",
        drivers=[
            ReportDriver(name="driver-one", direction="bull", weight=0.6, body="Body one."),
            ReportDriver(name="driver-two", direction="bear", weight=0.4, body="Body two."),
        ],
    )


def test_report_required_when_data_present_raises() -> None:
    """``is_no_data=False`` with ``report=None`` must fail schema validation."""

    with pytest.raises(ValidationError) as excinfo:
        AnalystVerdict.model_validate(
            {
                "lean":        "bullish",
                "magnitude":   0.5,
                "confidence":  0.6,
                "rationale":   "x",
                "key_factors": [],
                "is_no_data":  False,
                "report":      None,
            }
        )

    # The error message should be specific enough to debug from logs alone.
    assert "report is required" in str(excinfo.value)


def test_report_required_when_no_data_allows_none() -> None:
    """``is_no_data=True`` with ``report=None`` is the genuine no-data case."""

    v = AnalystVerdict.model_validate(
        {
            "lean":        "neutral",
            "magnitude":   0.0,
            "confidence":  0.0,
            "rationale":   "no data",
            "key_factors": [],
            "is_no_data":  True,
            "report":      None,
        }
    )
    assert v.report is None
    assert v.is_no_data is True


def test_valid_verdict_with_report_round_trips() -> None:
    """A populated report round-trips through ``model_validate`` unchanged."""

    payload = {
        "lean":        "bullish",
        "magnitude":   0.5,
        "confidence":  0.6,
        "rationale":   "Positive guidance signal.",
        "key_factors": [],
        "is_no_data":  False,
        "report":      _valid_report().model_dump(),
    }
    v = AnalystVerdict.model_validate(payload)
    assert v.report is not None
    assert v.report.summary.startswith("Test summary")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_evidence_schema.py -v`

Expected: `test_report_required_when_data_present_raises` FAILS with `Failed: DID NOT RAISE <class 'pydantic.ValidationError'>` — the other two pass on the existing schema.

- [ ] **Step 3: Add the `model_validator` to `AnalystVerdict`**

Open `src/contract/evidence.py`. At the top of the file, ensure `model_validator` is imported from pydantic:

```python
from pydantic import BaseModel, Field, model_validator
```

Locate the `AnalystVerdict` class (around lines 100–113). Inside the class, immediately after the `report: AnalystReport | None = None` line, add:

```python
    @model_validator(mode="after")
    def _report_required_when_data_present(self) -> "AnalystVerdict":
        """Reject verdicts that claim data but omit the report block.

        LLM analysts must emit ``report`` whenever ``is_no_data=False`` — the
        strategist reads the prose to weigh evidence.  Schema-level
        enforcement is the source of truth; the prompt instruction is the
        LLM-facing statement of the same rule.  ``llm_retry`` already
        classifies ``pydantic.ValidationError`` as retryable, so an
        offending LLM response is automatically retried up to the
        configured cap.
        """

        if not self.is_no_data and self.report is None:
            raise ValueError(
                "report is required when is_no_data=False — "
                "the analyst must emit a summary + drivers block "
                "alongside the verdict"
            )
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_evidence_schema.py -v`

Expected: 3 passed.

- [ ] **Step 5: Run wider contract tests to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/ tests/unit/contract/ -v`

Expected: all existing contract tests still pass (existing fixtures should already populate `report` for non-no-data verdicts).

- [ ] **Step 6: Commit**

```bash
git add src/contract/evidence.py tests/contract/test_evidence_schema.py
git commit -m "$(cat <<'EOF'
feat(contract): require report block on non-no-data analyst verdicts (D1.1)

Adds a Pydantic model_validator to AnalystVerdict that rejects the
(is_no_data=False, report=None) combination.  Closes the silent loophole
that let the News analyst drop the report block on 30.7 % of verdicts in
baseline-2025-09 — llm_retry already retries pydantic.ValidationError so
ADK's existing retry path covers the rollout.
EOF
)"
```

---

### Task 3 — R4: `config/risk_gate.json` + loader + `state.py` re-export (carries R1/R2/R3)

**Files:**
- Create: `config/risk_gate.json`
- Create: `src/config/risk_gate.py`
- Create: `tests/unit/test_risk_gate_config_loader.py`
- Modify: `src/orchestrator/state.py`
- Modify: `config/README.md`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_risk_gate_config_loader.py`:

```python
"""R4 — loader contract for ``config/risk_gate.json``.

The five risk-gate constants used to live as module-level literals in
``src/orchestrator/state.py``.  Moving them to ``config/risk_gate.json``
matches the project-wide "all configuration in config/*.json" convention
and makes R1/R2/R3 (cash-floor removal, max-delta widen, turnover lift)
operator-tunable.

This test covers two contracts:
1. ``load_risk_gate_config(path=...)`` returns a frozen dataclass-like
   object whose fields equal the JSON contents.
2. Importing the constants from ``orchestrator.state`` still resolves
   them by their legacy names so the wider codebase keeps working.
"""
from __future__ import annotations

import json
from pathlib import Path

from config.risk_gate import load_risk_gate_config


def test_loader_maps_each_json_field(tmp_path: Path) -> None:
    """Every JSON key surfaces as an identically-named attribute."""

    p = tmp_path / "risk_gate.json"
    p.write_text(
        json.dumps(
            {
                "min_held_weight":       0.002,
                "max_position_weight":   0.25,
                "cash_floor_weight":     0.05,
                "max_delta_per_ticker":  0.04,
                "max_total_turnover":    0.40,
            }
        ),
        encoding="utf-8",
    )

    cfg = load_risk_gate_config(path=p)

    assert cfg.min_held_weight       == 0.002
    assert cfg.max_position_weight   == 0.25
    assert cfg.cash_floor_weight     == 0.05
    assert cfg.max_delta_per_ticker  == 0.04
    assert cfg.max_total_turnover    == 0.40


def test_state_reexports_resolve_by_legacy_name() -> None:
    """``orchestrator.state`` re-exports the five constants by name.

    Importing from ``orchestrator.state`` is the historical entry point
    (used by ``src/agents/risk_gate/constraints.py`` and
    ``src/agents/risk_gate/agent.py``).  Renaming would break those call
    sites; the loader pattern keeps the names stable.
    """

    from orchestrator.state import (
        CASH_FLOOR_WEIGHT,
        MAX_DELTA_PER_TICKER,
        MAX_POSITION_WEIGHT,
        MAX_TOTAL_TURNOVER,
        MIN_HELD_WEIGHT,
    )

    # Values come from the live ``config/risk_gate.json`` so we assert
    # *types* and *the R1/R2/R3 defaults* rather than freezing fixture
    # values into the test.
    for name, value in (
        ("MIN_HELD_WEIGHT",      MIN_HELD_WEIGHT),
        ("MAX_POSITION_WEIGHT",  MAX_POSITION_WEIGHT),
        ("CASH_FLOOR_WEIGHT",    CASH_FLOOR_WEIGHT),
        ("MAX_DELTA_PER_TICKER", MAX_DELTA_PER_TICKER),
        ("MAX_TOTAL_TURNOVER",   MAX_TOTAL_TURNOVER),
    ):
        assert isinstance(value, float), f"{name} must be float, got {type(value)}"

    # R1/R2/R3 defaults — these are the values shipped by this spec.
    assert CASH_FLOOR_WEIGHT    == 0.00
    assert MAX_DELTA_PER_TICKER == 0.05
    assert MAX_TOTAL_TURNOVER   == 0.50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_risk_gate_config_loader.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'config.risk_gate'`.

- [ ] **Step 3: Create `config/risk_gate.json` with R1/R2/R3 defaults**

Create `config/risk_gate.json`:

```json
{
    "min_held_weight":       0.001,
    "max_position_weight":   0.20,
    "cash_floor_weight":     0.00,
    "max_delta_per_ticker":  0.05,
    "max_total_turnover":    0.50
}
```

(`cash_floor_weight` = 0.00 lands R1; `max_delta_per_ticker` = 0.05 lands R2; `max_total_turnover` = 0.50 lands R3.)

- [ ] **Step 4: Create `src/config/risk_gate.py` loader**

Create `src/config/risk_gate.py`. Mirror the `src/config/strategist.py` shape — Pydantic model + `load_…` helper + `@lru_cache` `get_…` singleton.

```python
"""Loader for ``config/risk_gate.json`` — the five risk-gate constants.

The constants govern position-sizing constraints applied by
``src/agents/risk_gate/constraints.py`` and surfaced to the strategist
prompt via ``src/agents/strategist/prompts.py``.  Centralising them in
JSON matches the project-wide "all configuration in config/*.json"
convention and makes operator tuning a config edit rather than a code
edit.

The module-level singleton ``get_risk_gate_config()`` is the production
entry point; ``load_risk_gate_config(path=...)`` exists for tests that
want to feed a custom file.

A note on coupling: ``src/orchestrator/state.py`` re-exports each field
under its legacy ``MAX_DELTA_PER_TICKER`` etc. name so every existing
``from orchestrator.state import …`` call site keeps working unchanged.
The strategist prompt module reads this loader directly so the
prompt-stated percentages stay in lockstep with the gate-enforced ones.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# Project-root-relative default path. The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather
# than relative to this file.
_DEFAULT_PATH = Path("config/risk_gate.json")


class RiskGateConfig(BaseModel):
    """Top-level shape of ``config/risk_gate.json``.

    Attributes
    ----------
    min_held_weight:
        Lifecycle threshold — a position is considered "open" above this
        weight.  Below it the executor treats the slot as flat.
    max_position_weight:
        Single-ticker concentration cap.  The risk gate scales down any
        proposed weight that exceeds this value.
    cash_floor_weight:
        Minimum cash reserve fraction.  Total watchlist weight is
        capped at ``1 - cash_floor_weight``.  R1 ships ``0.00`` —
        operator may re-introduce a floor by editing the JSON.
    max_delta_per_ticker:
        Maximum weight change per tick per ticker.  R2 ships ``0.05`` —
        five-times the original 0.01 so a 5 % conviction expresses in
        one tick instead of five.
    max_total_turnover:
        Maximum total portfolio turnover per tick (sum of ``|delta|``
        across the watchlist).  R3 ships ``0.50`` — rescales to the new
        per-ticker cap.
    """

    min_held_weight:       float = Field(ge=0.0, le=0.10)
    max_position_weight:   float = Field(gt=0.0, le=1.0)
    cash_floor_weight:     float = Field(ge=0.0, le=0.50)
    max_delta_per_ticker:  float = Field(gt=0.0, le=1.0)
    max_total_turnover:    float = Field(gt=0.0, le=2.0)


def load_risk_gate_config(*, path: Path | None = None) -> RiskGateConfig:
    """Read and validate ``config/risk_gate.json``.

    Parameters
    ----------
    path:
        Override the default path.  Useful in tests that want to supply
        a temporary file without touching the source tree.

    Returns
    -------
    RiskGateConfig
        Validated configuration object.
    """

    p = path or _DEFAULT_PATH
    payload = json.loads(p.read_text(encoding="utf-8"))
    return RiskGateConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_risk_gate_config() -> RiskGateConfig:
    """Production entry point — cached load of the default config path.

    Memoised via ``lru_cache`` so the JSON file is only read once per
    process.  A process restart is required after editing
    ``config/risk_gate.json`` to pick up changes.
    """

    return load_risk_gate_config()
```

- [ ] **Step 5: Update `src/orchestrator/state.py` to import from the loader**

Open `src/orchestrator/state.py`. Replace lines 8–14 (the `# ── Risk-gate constants ───…` block) with:

```python
# ── Risk-gate constants ───────────────────────────────────────────────────────
# Resolved from ``config/risk_gate.json`` at import time so every existing
# ``from orchestrator.state import MAX_DELTA_PER_TICKER`` etc. site keeps
# working unchanged after the R4 migration.  See ``src/config/risk_gate.py``.
from config.risk_gate import get_risk_gate_config as _get_risk_cfg

_risk = _get_risk_cfg()

MIN_HELD_WEIGHT:      float = _risk.min_held_weight        # open-position threshold
MAX_POSITION_WEIGHT:  float = _risk.max_position_weight    # single-ticker concentration cap
CASH_FLOOR_WEIGHT:    float = _risk.cash_floor_weight      # minimum cash reserve fraction
MAX_DELTA_PER_TICKER: float = _risk.max_delta_per_ticker   # maximum weight change per tick per ticker
MAX_TOTAL_TURNOVER:   float = _risk.max_total_turnover     # maximum total portfolio turnover per tick
ORDER_EPSILON:        float = 1e-6                          # weight change below this is ignored (no order generated)
```

Leave `ORDER_EPSILON` as a code-level constant — it is a numerical tolerance, not an operator-tunable knob.

- [ ] **Step 6: Document the new file in `config/README.md`**

Open `config/README.md`. In the file-summary table near the top, add a row:

```markdown
| `risk_gate.json` | Risk-gate constraint constants — cash floor, max delta, turnover ceiling, position cap, held-weight floor | `src/config/risk_gate.py` (`get_risk_gate_config()`) |
```

Then add a new section after the `strategist.json` section (search for `## \`strategist.json\``):

```markdown
---

## `risk_gate.json` — position-sizing constraint constants

Loaded by `src/config/risk_gate.py` and re-exported under the legacy
`MAX_DELTA_PER_TICKER` etc. names from `src/orchestrator/state.py`.  The
strategist prompt also reads these values directly via
`src/agents/strategist/prompts.py` so the prompt-stated percentages stay
in lockstep with what the gate enforces.

| Setting | Type | Meaning |
|---|---|---|
| `min_held_weight` | float | Lifecycle threshold — a position is considered "open" above this weight.  Below it the executor treats the slot as flat. Default 0.001. |
| `max_position_weight` | float | Single-ticker concentration cap.  The risk gate scales down any proposed weight that exceeds this value. Default 0.20. |
| `cash_floor_weight` | float | Minimum cash reserve fraction.  Total watchlist weight is capped at `1 - cash_floor_weight`.  Default 0.00 (R1 ships no cash floor; raise to re-introduce one). |
| `max_delta_per_ticker` | float | Maximum weight change per tick per ticker.  Default 0.05 (R2 widened from 0.01 so a 5 % conviction expresses in one tick). |
| `max_total_turnover` | float | Maximum total portfolio turnover per tick (sum of `|delta|` across the watchlist).  Default 0.50 (R3 lifted from 0.30 to rescale to the new per-ticker cap). |
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_risk_gate_config_loader.py -v`

Expected: 2 passed.

- [ ] **Step 8: Run the existing risk-gate test suite to verify R1/R2/R3 effects**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_risk_gate_constraints.py tests/unit/test_risk_gate_orders.py -v`

Expected: the tests still pass — most should be invariant under the new values; any that hard-coded the old `0.01` delta or `0.10` cash floor must be updated to reference the constants by name. **If any test fails because it hard-coded the old value, fix the test to read `MAX_DELTA_PER_TICKER` / `CASH_FLOOR_WEIGHT` / `MAX_TOTAL_TURNOVER` from `orchestrator.state` rather than reasserting the literal.**

- [ ] **Step 9: Run the wider config-conformance suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/config/ -v`

Expected: no regression. The `test_no_hardcoded_models.py`-shaped sweeps target other configs but should not flag the new file.

- [ ] **Step 10: Commit**

```bash
git add config/risk_gate.json src/config/risk_gate.py src/orchestrator/state.py config/README.md tests/unit/test_risk_gate_config_loader.py
git commit -m "$(cat <<'EOF'
feat(config): risk-gate constants in config/risk_gate.json (R1+R2+R3+R4)

Moves the five risk-gate constants from src/orchestrator/state.py into a
new config/risk_gate.json so they can be tuned without a code change.
Default values land R1 (cash_floor 0.10→0.00), R2 (max_delta 0.01→0.05)
and R3 (max_turnover 0.30→0.50) — the relaxed envelope sized for the
post-baseline backtest.  Existing call sites import the constants by
their legacy names; nothing else moves.
EOF
)"
```

---

## Band 2 — Pipeline Code

These six tasks (S1, S2, S3, S5, S6, S9) are independent of each other once Band 1 is in. They can be executed in any order; subagents may run them in parallel.

### Task 4 — S1: PIT-clamp `reference_prices` at Phase 2 (per tick)

**Files:**
- Modify: `src/backtest/runner.py`
- Create: `tests/backtest/test_reference_prices.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backtest/test_reference_prices.py`:

```python
"""S1 — Phase 2 PIT-clamp for ``reference_prices``.

``_seed_reference_prices`` previously read SPY + sector ETF bars over the
entire backtest window at run start (Phase 1), embedding future bars into
``state["reference_prices"]``.  The technical extractor re-clamped by
``as_of`` downstream, but the contract requires the tick-scoped row to
be populated fresh from its source of truth at Phase 2 (per tick).

This test pins the per-tick clamp: after seeding at a specific ``as_of``,
no bar in any ``reference_prices[symbol]`` row may carry a timestamp
later than ``as_of``.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.runner import _seed_reference_prices
from data.models import OHLCVBar


@pytest.fixture
def store_with_spy(tmp_path: Path) -> CachedDataStore:
    """Create a temporary cache with two weeks of SPY bars."""

    db_path = tmp_path / "cache.sqlite"
    store = CachedDataStore(db_path)

    # Two weeks of synthetic SPY bars, one per trading day at midnight UTC.
    bars = [
        OHLCVBar(
            timestamp=datetime(2026, 5, 1, 0, 0, tzinfo=UTC) + timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low= 99.0 + i,
            close=100.5 + i,
            volume=1_000_000,
        )
        for i in range(14)
    ]
    store.write_ohlcv("SPY", bars)
    return store


def test_seed_clamps_to_as_of(store_with_spy: CachedDataStore) -> None:
    """No reference_prices bar may have ``ts > as_of``."""

    as_of = datetime(2026, 5, 7, 13, 30, tzinfo=UTC)

    ref = _seed_reference_prices(
        store=store_with_spy,
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 14),
        as_of=as_of,
    )

    assert "SPY" in ref
    for bar in ref["SPY"].bars:
        assert bar.timestamp <= as_of, (
            f"reference_prices[SPY] leaked future bar at {bar.timestamp} "
            f"(as_of={as_of})"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_reference_prices.py -v`

Expected: FAIL with `TypeError: _seed_reference_prices() got an unexpected keyword argument 'as_of'` (the existing signature has no `as_of` parameter).

- [ ] **Step 3: Add the `as_of` parameter to `_seed_reference_prices`**

Open `src/backtest/runner.py`. Locate `_seed_reference_prices` (lines 64–107). Update the signature and body:

```python
def _seed_reference_prices(
    *,
    store,
    window_start,
    window_end,
    as_of: datetime | None = None,
) -> dict:
    """Build ``state["reference_prices"]`` from cached SPY + sector ETF bars.

    Mirrors what ``orchestrator.tick._fetch_reference_prices`` does on live
    runs — returns a ``{symbol: PriceHistory}`` dict so the technical
    extractor can compute ``relative_strength_vs_spy_*`` and
    ``relative_strength_vs_sector_*`` features during backtest replay.

    When ``as_of`` is supplied, bars beyond that instant are filtered out
    before returning — this is the Phase 2 contract: the tick-scoped
    ``reference_prices`` row must be PIT-correct against the current tick.
    When ``as_of`` is ``None`` the function reads the full ``[window_start,
    window_end]`` slice unchanged (preserved for legacy Phase 1 callers
    that we are deprecating in S1).

    Parameters
    ----------
    store:
        Open ``CachedDataStore`` instance.
    window_start, window_end:
        Inclusive date bounds for the backtest window.
    as_of:
        Optional tick timestamp.  When set, bars later than ``as_of`` are
        dropped before return.  Defaults to ``None`` (no clamp).

    Returns
    -------
    dict[str, PriceHistory]
        One ``PriceHistory`` per reference symbol found in the cache.
    """

    from data.models import PriceHistory

    # Import the canonical reference-symbol list from the fetch script so
    # the two lists can never drift apart.
    from scripts.backtest_fetch import _REFERENCE_SYMBOLS

    ref: dict = {}

    for symbol in _REFERENCE_SYMBOLS:
        bars = store.read_ohlcv(symbol, window_start, window_end)

        # Phase 2 PIT clamp — strip any bar whose timestamp sits past the
        # current tick.  The defence-in-depth re-clamp in
        # ``src/contract/extractors/technical.py`` covers the legacy
        # Phase 1 call site; this is the literal contract.
        if as_of is not None:
            bars = [b for b in bars if b.timestamp <= as_of]

        if bars:
            ref[symbol] = PriceHistory(ticker=symbol, bars=bars)

    return ref
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_reference_prices.py -v`

Expected: 1 passed.

- [ ] **Step 5: Relocate the seed call to Phase 2 (per tick) in `Driver`**

Open `src/backtest/driver.py`. Locate the per-tick loop body (search for `_run_one_tick`). Add a call that refreshes `state["reference_prices"]` at the start of every tick — immediately after `state["tick_id"]` is set and before the analyst before-callbacks fire. The exact insertion point is inside `_run_one_tick`, right after the tick metadata is seeded.

```python
        # Phase 2 — refresh tick-scoped reference_prices from the cache so
        # the row is PIT-correct against the current as_of.  Legacy Phase 1
        # seed in ``runner.py`` is retained as a no-op safety net but the
        # values it writes are overwritten here on every tick.
        from backtest.runner import _seed_reference_prices
        from backtest.providers._store_handle import get_store as _get_store

        try:
            _store = _get_store()
        except RuntimeError:
            _store = None

        if _store is not None:
            ref = _seed_reference_prices(
                store        = _store,
                window_start = tick.as_of.date(),
                window_end   = tick.as_of.date(),
                as_of        = tick.as_of,
            )
            state["reference_prices"] = {
                sym: ph.model_dump(mode="json") for sym, ph in ref.items()
            }
```

(If `Driver._run_one_tick` does not already have a `state` argument and `tick` is named differently — the existing code uses `tick.as_of` per the audit telemetry call — adapt to match the surrounding identifiers. Read the function body before inserting to confirm the variable names.)

- [ ] **Step 6: Run the audit-tripwire test to confirm Phase 1 leak is no longer visible**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/ -k "reference_prices or audit" -v`

Expected: existing audit tests still pass; the new test passes; no regression in the broader backtest suite.

- [ ] **Step 7: Commit**

```bash
git add src/backtest/runner.py src/backtest/driver.py tests/backtest/test_reference_prices.py
git commit -m "$(cat <<'EOF'
fix(backtest): PIT-clamp reference_prices at Phase 2 (S1)

_seed_reference_prices now accepts as_of and strips bars later than it;
the Driver calls it at the start of every tick so the tick-scoped row is
PIT-correct.  Closes the any_filter_key_after_as_of tripwire that fires
on tick 1 in baseline-2025-09 and removes a future-data trap any new
reference_prices consumer would have walked into.
EOF
)"
```

---

### Task 5 — S2: executor only deletes positions on a true close

**Files:**
- Modify: `src/agents/executor/agent.py`
- Create: `tests/executor/test_executor_bookkeeping.py`

- [ ] **Step 1: Write the failing test**

Create `tests/executor/test_executor_bookkeeping.py`:

```python
"""S2 — Executor bookkeeping: keep positions on trim, delete only on close.

``src/agents/executor/agent.py:156`` previously did
``del positions[order.ticker]`` on every SELL — including 1 % trims that
do not actually close the position.  With ``MAX_DELTA_PER_TICKER`` at
0.01 (and now 0.05) this wiped the position thesis before the position
was empty, contradicting the §A ``positions`` row contract.

This test pins the new behaviour:
1. A 1 % trim from a 5 % position leaves ``state["positions"][ticker]``
   intact.
2. A full exit (SELL down to 0) removes the entry from
   ``state["positions"]`` and writes exactly one ``TradeLogRow``.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

# Test scaffolding deliberately follows the patterns established by the
# sibling tests in tests/agents/executor/test_isolated_failure.py.  Reuse
# whatever broker + state fixtures the surrounding suite exposes.


pytestmark = pytest.mark.asyncio


@pytest.mark.skip(
    reason=(
        "Scaffold — implementer fills in using FakeBroker + ExecutorAgent. "
        "Mark as failing once scaffolding lands so the implementation step "
        "drives the real assertions."
    )
)
async def test_trim_preserves_position_thesis() -> None:
    """A SELL that leaves >0 shares must not wipe state['positions'][T]."""

    raise NotImplementedError


@pytest.mark.skip(
    reason=(
        "Scaffold — implementer fills in using FakeBroker + ExecutorAgent."
    )
)
async def test_full_exit_writes_one_trade_log_row_and_deletes() -> None:
    """A SELL that drains the position to 0 must delete + write trade log."""

    raise NotImplementedError
```

**Note to implementer.** Use `FakeBroker` from `src/broker/fake.py` plus the executor wiring used in `tests/agents/executor/test_isolated_failure.py` (read that file for the exact construction). The two scenarios:

1. Seed `FakeBroker` with `{TICKER: (100 shares, $100)}`. Seed `state["positions"][TICKER]` with a `PositionThesis`-shaped dict. Submit a SELL for 1 share. After execution, assert `state["positions"][TICKER]` still present; assert `TradeLogRow` was **not** written (db_session integer count unchanged).
2. Same starting state. Submit a SELL for 100 shares. After execution, assert `TICKER not in state["positions"]`; assert exactly one `TradeLogRow` written.

The skipped scaffolding marks the work boundary — replace `@pytest.mark.skip(...)` with full async test bodies once the broker fixture is in hand. Remove the `skip` decorator before running the test in step 2.

- [ ] **Step 2: Run tests to verify both fail (after un-skipping)**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/executor/test_executor_bookkeeping.py -v`

Expected: both fail. `test_trim_preserves_position_thesis` fails because the SELL branch unconditionally deletes; `test_full_exit_writes_one_trade_log_row_and_deletes` may already pass on the existing code (full exit is the case the existing code handles correctly).

- [ ] **Step 3: Gate `del positions[…]` and trade-log write on broker-empty**

Open `src/agents/executor/agent.py`. Locate the SELL branch (lines 97–156). Replace the unconditional `del positions[order.ticker]` and the unconditional `save_trade_log_entry` call with a guard that fires only when the broker reports zero remaining shares.

Insert before the existing `elif order.action == "SELL"` branch body, after the `try:` and `fill = await self.broker.submit(...)` lines that execute the order. The post-fill broker state is the source of truth — use it:

```python
                # SELL — only close the position and write the trade log
                # when the broker reports the slot is empty post-fill.  A
                # 1 % trim (or any sell that leaves >0 shares) preserves
                # the thesis so the next tick's strategist still has the
                # opening context.
                elif order.action == "SELL" and order.ticker in positions:

                    # Source-of-truth check: query the broker post-fill.
                    # Falling back to "prior portfolio minus fill.quantity"
                    # would assume no parallel fills landed in between;
                    # the broker is the only place that knows.
                    portfolio_after = await self.broker.get_portfolio()
                    remaining_qty   = portfolio_after.positions_by_ticker.get(
                        order.ticker, 0.0,
                    )

                    if remaining_qty <= 0.0:

                        # Full close — write the trade log entry and delete
                        # the slot.  This block is the *previous* SELL
                        # branch body unchanged.
                        thesis = positions.get(order.ticker)
                        if thesis and self.db_session:
                            from orchestrator.persistence import save_trade_log_entry

                            # … (existing save_trade_log_entry body
                            # preserved verbatim — opened_price, closed_at,
                            # pnl_pct calculation, save_trade_log_entry
                            # call) …

                        del positions[order.ticker]

                    # else: trim — preserve the slot and do not write a
                    # trade log row.  The next SELL that drains it to zero
                    # will trigger the full-close branch above.
```

(The `… (existing save_trade_log_entry body preserved verbatim …) …` placeholder marks the existing code from lines 100–153 of the original SELL branch — keep it byte-identical inside the new `if remaining_qty <= 0.0:` block.)

**Important.** The exact API for "remaining quantity by ticker" depends on `FakeBroker.get_portfolio()`'s return shape. Inspect that first; the snippet above assumes a `positions_by_ticker` mapping. If it's a list of `Position` records, adapt the lookup accordingly. Read `src/broker/fake.py` before writing the replacement.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/executor/test_executor_bookkeeping.py -v`

Expected: 2 passed.

- [ ] **Step 5: Run wider executor + position-lifecycle tests to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trade_log.py tests/unit/test_position_lifecycle.py tests/agents/executor/ -v`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agents/executor/agent.py tests/executor/test_executor_bookkeeping.py
git commit -m "$(cat <<'EOF'
fix(executor): only delete positions and write trade log on true close (S2)

A 1 % trim used to wipe state['positions'][T] and emit a TradeLogRow.
The executor now queries the broker post-fill and only treats the
position as closed when remaining_qty <= 0.0.  Unblocks Spec B — the
thesis the foundational-memory work persists is no longer mid-trim
corrupted.
EOF
)"
```

---

### Task 6 — S3: drain `_report_cache_hits_for_audit` via `obs/logs/`

**Files:**
- Modify: `src/agents/analysts/report_cache.py`
- Modify: `src/backtest/driver.py`
- Modify: `src/backtest/audit/telemetry.py`
- Create: `tests/backtest/test_cache_hits_audit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backtest/test_cache_hits_audit.py`:

```python
"""S3 — audit ``cache_hits`` count matches structured-log ``cache_hit`` count.

``src/agents/analysts/report_cache.py:579`` previously mutated parent
state from inside per-ticker sub-agents
(``state.setdefault("_report_cache_hits_for_audit", []).append(...)``).
Mutations inside per-ticker BaseAgents are not reliably propagated
through ADK's session merge; in the baseline-2025-09 run the audit saw
26 hits while the structured ``report_cache_hit`` log emitted 469.

The fix relocates the audit reader to consume ``obs/logs/`` directly
(Rule 8 — observability is additive).  This test pins the new contract:
the audit ``cache_hits`` count for a sample tick must equal the number
of ``report_cache_hit`` log events recorded in that tick's
``obs/logs/<slug>.json``.
"""
from __future__ import annotations

import json
from pathlib import Path


def _count_hits(audit_record: dict) -> int:
    """Return the audit-side report-cache-hit count for one tick.

    The exact key is ``len(audit_record["report_cache_hits"])`` per
    ``src/backtest/audit/telemetry.py`` — keep the lookup encapsulated
    here so a future field rename only touches this helper.
    """

    return len(audit_record.get("report_cache_hits", []))


def _count_log_hits(log_payload: dict) -> int:
    """Return the structured-log ``report_cache_hit`` event count."""

    return sum(
        1 for event in log_payload.get("events", [])
        if event.get("message") == "report_cache_hit"
    )


def test_audit_cache_hits_match_log_count_for_known_tick(tmp_path: Path) -> None:
    """Audit and log counts agree on a hand-crafted tick.

    A real backtest re-run is out of band for unit tests.  This case
    constructs a synthetic obs-logs payload with N report_cache_hit
    events and a synthetic audit record produced by the *new* telemetry
    builder; the two counts must match.
    """

    log_payload = {
        "events": [
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_miss", "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
        ],
    }

    # Build_telemetry_record currently expects an in-memory list; once S3
    # lands, it accepts the count directly (or the parsed log payload).
    # The implementer wires the chosen shape; this assertion is the
    # contract.
    from backtest.audit.telemetry import build_telemetry_record_from_logs

    record = build_telemetry_record_from_logs(log_payload=log_payload)

    assert _count_hits(record) == _count_log_hits(log_payload)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_cache_hits_audit.py -v`

Expected: FAIL with `ImportError: cannot import name 'build_telemetry_record_from_logs' from 'backtest.audit.telemetry'`.

- [ ] **Step 3: Stop the direct state mutation in `report_cache.py`**

Open `src/agents/analysts/report_cache.py`. Locate the `_record_cache_hit_for_audit` (or equivalently-named) helper that does the `state.setdefault("_report_cache_hits_for_audit", []).append(...)` call around line 579. Delete the function body entirely, or shrink it to a no-op with a deprecation comment:

```python
def _record_cache_hit_for_audit(
    state, *, analyst: str, ticker: str, input_hash: str,
    originating_as_of: str | None,
) -> None:
    """No-op — audit drains report-cache hits from ``obs/logs/`` since S3.

    The previous direct ``state.setdefault(...)`` mutation from inside
    per-ticker sub-agents was not reliably propagated through ADK's
    session merge; the audit count drifted from the structured-log count.
    The audit now reads ``obs/logs/`` (Rule 8 — observability is
    additive) so the in-state list is no longer the source of truth.

    The structured ``report_cache_hit`` log emit (in
    ``agents.analysts.cache_callbacks``) remains the single source of
    truth and is unchanged.
    """

    return None
```

Find every call site of `_record_cache_hit_for_audit` (grep the codebase) and leave the calls in place — they now hit a no-op, but removing them risks missing one. Schedule deletion of the helper itself for a follow-up cleanup commit; YAGNI for this spec.

- [ ] **Step 4: Add `build_telemetry_record_from_logs` to `audit/telemetry.py`**

Open `src/backtest/audit/telemetry.py`. Locate the existing `build_telemetry_record` (around the top of the module). Add a new sibling function that reads from the log payload:

```python
def build_telemetry_record_from_logs(*, log_payload: dict) -> dict:
    """Build an audit telemetry record from a parsed ``obs/logs/`` payload.

    The legacy ``build_telemetry_record`` pathway used
    ``state["_report_cache_hits_for_audit"]`` as the source of truth for
    cache-hit count; that surface was unreliable across per-ticker
    BaseAgents (see S3).  The new pathway consumes the same structured
    ``report_cache_hit`` log events the metrics report already counts so
    audit and log agree by construction.

    Parameters
    ----------
    log_payload:
        Parsed JSON payload from ``obs/logs/<tick-slug>.json`` — a dict
        with an ``events`` key whose entries each carry ``message`` and
        ``logger`` strings.

    Returns
    -------
    dict
        Telemetry record shape compatible with the existing
        ``write_telemetry_record`` consumer.  Only the cache-hit field
        is populated by this function; the caller merges the rest.
    """

    hits = [
        {"event": "report_cache_hit"}
        for event in log_payload.get("events", [])
        if event.get("message") == "report_cache_hit"
    ]
    return {"report_cache_hits": hits}
```

- [ ] **Step 5: Update `driver.py` to feed the log payload into the audit builder**

Open `src/backtest/driver.py`. Locate the audit-build block (around line 305 — the call to `build_telemetry_record`). Change the source for `report_cache_hits` to be the same log payload `drain_tick` is about to flush.

The existing flow calls `build_telemetry_record(...)` with `report_cache_hits=state.get("_report_cache_hits_for_audit", [])`. Change to:

```python
            telemetry = build_telemetry_record(
                tick                       = tick,
                run_id                     = self._run_id,
                strict_mode                = os.environ.get("STOCKBOT_STRICT_AS_OF") == "1",
                per_domain                 = per_domain,
                # Cache-hits now sourced from the structured-log drain
                # rather than direct state mutation (S3).  The drain
                # flushes a payload identical to the one obs/logs/ will
                # contain after this tick.
                report_cache_hits          = self._drain_logs_cache_hits(),
                db_writes_recorded_at      = {},
                wall_clock_fallback_fired  = wallclock_fallback_count > 0,
            )
```

Add a private helper on the `Driver` class:

```python
    def _drain_logs_cache_hits(self) -> list[dict]:
        """Return the report-cache-hit list for the current tick.

        Inspects the in-memory log buffer that ``drain_tick`` is about
        to flush; counts ``report_cache_hit`` messages and returns one
        placeholder dict per hit so the audit ``len(report_cache_hits)``
        contract is preserved.  When the buffer is empty (or the log
        handle is unset), returns an empty list.
        """

        handle = self._obs_handles.get("logs") if hasattr(self, "_obs_handles") else None
        if handle is None:
            return []

        events = getattr(handle, "buffered_events", None) or []
        return [
            {"event": "report_cache_hit"}
            for ev in events
            if getattr(ev, "message", None) == "report_cache_hit"
            or (isinstance(ev, dict) and ev.get("message") == "report_cache_hit")
        ]
```

(Adapt the attribute lookups to match whatever shape `_obs_handles["logs"]` exposes — read `src/observability/drain.py` and `src/backtest/driver.py:__init__` to confirm.)

Delete the existing `state.pop("_report_cache_hits_for_audit", None)` line — there is nothing to pop now.

- [ ] **Step 6: Run the new audit test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_cache_hits_audit.py -v`

Expected: 1 passed.

- [ ] **Step 7: Run the wider audit + driver test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/ -v`

Expected: no regression.

- [ ] **Step 8: Commit**

```bash
git add src/agents/analysts/report_cache.py src/backtest/driver.py src/backtest/audit/telemetry.py tests/backtest/test_cache_hits_audit.py
git commit -m "$(cat <<'EOF'
fix(audit): drain report-cache hits from obs/logs/ rather than state (S3)

The previous state.setdefault('_report_cache_hits_for_audit', []) mutate
inside per-ticker BaseAgents was silently dropped by ADK's session merge
- baseline-2025-09 saw 26 audit hits vs 469 logged.  Audit now reads
the same structured-log events the metrics report counts so the two
numbers agree by construction; removes one Rule-1 hot spot rather than
patching it.
EOF
)"
```

---

### Task 7 — S5: insider `.model_dump()` + strict decision-logger serialiser

**Files:**
- Modify: `src/agents/analysts/fundamental/fetch_agent.py`
- Modify: `src/backtest/decision_logger.py`
- Create: `tests/unit/test_decision_logger_strict_serialiser.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_decision_logger_strict_serialiser.py`:

```python
"""S5 — strict decision-logger serialiser + insider .model_dump() at fetch.

Two coupled symptoms in baseline-2025-09:

1. ``decisions/*.json[analyst_inputs.fundamental.insider]`` was a 2 292-char
   Python repr of ``Form4Bundle`` rather than a JSON dict.  Sibling fields
   ``ratios`` / ``filings`` used ``.model_dump()``; insider did not.
2. ``decision_logger.py:136`` used ``json.dumps(snapshot, indent=2,
   default=str)`` — ``default=str`` falls back to ``repr()`` on any
   un-dumpable type, silently emitting a string instead of failing loud.

Fix: call ``.model_dump()`` on the insider bundle at fetch time, and
replace ``default=str`` with a strict serialiser that raises on
un-dumpable types.  This module covers the strict-serialiser contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.decision_logger import _serialise_snapshot


class _NotJsonable:
    """A class with no JSON-serialisable representation."""

    def __repr__(self) -> str:
        return "<not-jsonable>"


def test_serialiser_handles_nested_pydantic_models() -> None:
    """Pydantic models nested inside lists/dicts round-trip as dicts."""

    from contract.evidence import AnalystVerdict

    nested = {
        "verdict": AnalystVerdict.model_validate(
            {
                "lean":        "neutral",
                "magnitude":   0.0,
                "confidence":  0.0,
                "rationale":   "no data",
                "key_factors": [],
                "is_no_data":  True,
                "report":      None,
            }
        ),
        "list_of_verdicts": [
            AnalystVerdict.model_validate(
                {
                    "lean":        "neutral",
                    "magnitude":   0.0,
                    "confidence":  0.0,
                    "rationale":   "no data",
                    "key_factors": [],
                    "is_no_data":  True,
                    "report":      None,
                }
            ),
        ],
    }

    out = _serialise_snapshot(nested)
    parsed = json.loads(out)

    assert isinstance(parsed["verdict"], dict)
    assert parsed["verdict"]["lean"] == "neutral"
    assert isinstance(parsed["list_of_verdicts"], list)
    assert isinstance(parsed["list_of_verdicts"][0], dict)


def test_serialiser_raises_on_unjsonable() -> None:
    """An un-dumpable type must raise, not silently emit a repr string."""

    with pytest.raises(TypeError):
        _serialise_snapshot({"bad": _NotJsonable()})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_decision_logger_strict_serialiser.py -v`

Expected: FAIL with `ImportError: cannot import name '_serialise_snapshot' from 'backtest.decision_logger'`.

- [ ] **Step 3: Add strict serialiser to `decision_logger.py`**

Open `src/backtest/decision_logger.py`. Locate `_coerce` (lines 25–33) and `write_text(json.dumps(snapshot, indent=2, default=str), …)` (line 136).

Replace the existing `_coerce` and the `json.dumps(...)` call with:

```python
def _coerce(value):
    """Recursively coerce Pydantic models nested inside dicts/lists.

    Top-level Pydantic instances are dumped via ``.model_dump()``.  Dicts
    and lists are walked so models nested anywhere in the structure are
    coerced too.  Anything that is already a JSON primitive (None, bool,
    int, float, str) is returned unchanged.

    Anything else falls through to ``json.dumps``' default handling,
    which now (via ``_strict_default``) raises ``TypeError`` rather than
    silently emitting ``repr()``.
    """

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_coerce(v) for v in value]

    return value


def _strict_default(value):
    """``json.dumps`` ``default=`` handler — raise loudly on unsupported types.

    The previous ``default=str`` quietly emitted ``repr(value)`` for any
    type ``json.dumps`` did not recognise — that is exactly how the
    ``Form4Bundle`` regression slipped in (the model instance got
    ``repr``'d into a 2 292-char string).  Forcing a ``TypeError`` here
    means any new un-dumpable field shows up immediately as a failing
    backtest rather than as a silently-corrupted decision row.
    """

    raise TypeError(
        f"decision_logger: refusing to serialise {type(value).__name__} "
        f"— add an explicit ``.model_dump()`` at the producing call site "
        f"or extend _coerce to handle this shape"
    )


def _serialise_snapshot(snapshot: dict) -> str:
    """Public entry point for the strict snapshot serialiser.

    Coerces nested Pydantic models via ``_coerce`` then runs
    ``json.dumps`` with the strict default handler.  Tests can call this
    directly to pin the contract.
    """

    coerced = _coerce(snapshot)
    return json.dumps(coerced, indent=2, default=_strict_default)
```

Then change the existing `outpath.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")` call (line 136) to:

```python
        outpath.write_text(_serialise_snapshot(snapshot), encoding="utf-8")
```

- [ ] **Step 4: Add `.model_dump()` to the insider bundle in `fetch_agent.py`**

Open `src/agents/analysts/fundamental/fetch_agent.py`. Locate the assignment at line 168:

```python
            fundamental_data[ticker] = {
                "ratios":  ratios_payload,
                "filings": filings_payload,
                "insider": insider_bundle,
            }
```

Change to:

```python
            fundamental_data[ticker] = {
                "ratios":  ratios_payload,
                "filings": filings_payload,
                # ``.model_dump()`` so the downstream decision logger
                # serialises the bundle as a JSON dict rather than
                # falling back to ``repr(Form4Bundle)`` and emitting a
                # 2 KB string.  Sibling fields are already dicts/lists;
                # this is the symmetry fix.
                "insider": insider_bundle.model_dump(mode="json"),
            }
```

(Use `mode="json"` so datetimes nested inside trades serialise as ISO strings rather than Python `datetime` objects, which the strict serialiser would now reject.)

- [ ] **Step 5: Run the new test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_decision_logger_strict_serialiser.py -v`

Expected: 2 passed.

- [ ] **Step 6: Run wider decision-logger + fundamental-fetch tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/ tests/agents/analysts/ -k "decision_logger or fundamental_fetch or fetch_agent" -v`

Expected: no regression.

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/fundamental/fetch_agent.py src/backtest/decision_logger.py tests/unit/test_decision_logger_strict_serialiser.py
git commit -m "$(cat <<'EOF'
fix(backtest): strict decision-logger serialiser + insider .model_dump (S5)

decision_logger now coerces Pydantic models nested in lists/dicts via
_coerce and replaces default=str with a strict TypeError-raising
fallback.  fundamental/fetch_agent calls .model_dump(mode='json') on the
Form4Bundle so sibling fields stay in lockstep.  Closes the silent 2 KB
repr-string regression that landed in every baseline-2025-09 decision
file's analyst_inputs.fundamental.insider field.
EOF
)"
```

---

### Task 8 — S6: `decision_tag` derivation from (prior, new) weight

**Files:**
- Modify: `src/agents/strategist/derivation.py`
- Create: `tests/unit/agents/strategist/test_decision_tag_derivation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/strategist/test_decision_tag_derivation.py`:

```python
"""S6 — derive ``decision_tag`` from (prior, new) weight pairs.

``decision_tag`` was a constant ``catalyst_driven_entry`` across all 46
ticks of baseline-2025-09, regardless of whether the decision was an
opening BUY, a 1 % ramp, a trim, a full exit or a hold-flat.  This made
it useless as a memory key — any Spec B / Spec C memory writer keyed on
intent would see one bucket.

The fix derives the tag from prior-vs-new weight in
``strategist/derivation.py``.  This module covers the six enum
categories spelled out in the spec table.
"""
from __future__ import annotations

import pytest

from agents.strategist.derivation import derive_decision_tag


@pytest.mark.parametrize(
    "prior, new, expected",
    [
        (0.0,  0.05, "entry"),       # flat → positive
        (0.02, 0.05, "ramp"),        # smaller positive → larger positive
        (0.05, 0.02, "trim"),        # larger positive → smaller positive
        (0.05, 0.0,  "exit"),        # positive → flat
        (0.0,  0.0,  "hold_flat"),   # flat → flat
        (0.05, 0.05, "hold"),        # positive → same positive
    ],
)
def test_decision_tag_categories(prior: float, new: float, expected: str) -> None:
    """Each (prior, new) pair maps to the expected enum tag."""

    assert derive_decision_tag(prior=prior, new=new) == expected


def test_decision_tag_uses_epsilon_for_zero_comparison() -> None:
    """Tiny dust positions are treated as flat for tag purposes.

    A residual 1e-9 weight should not flip an exit into a trim.  The
    derivation uses ``ORDER_EPSILON`` (1e-6) as the zero threshold —
    anything below that counts as 0.0.
    """

    assert derive_decision_tag(prior=0.05, new=1e-9) == "exit"
    assert derive_decision_tag(prior=1e-9, new=0.05) == "entry"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_decision_tag_derivation.py -v`

Expected: FAIL with `ImportError: cannot import name 'derive_decision_tag' from 'agents.strategist.derivation'`.

- [ ] **Step 3: Implement `derive_decision_tag` in `derivation.py`**

Open `src/agents/strategist/derivation.py`. Near the top of the module (after the imports, before `derive_legacy_fields`), add:

```python
from orchestrator.state import ORDER_EPSILON


def derive_decision_tag(*, prior: float, new: float) -> str:
    """Categorise a (prior, new) weight pair as a six-way decision tag.

    The tag is the discriminating memory key downstream consumers (Spec
    B / Spec C) use to distinguish entries from trims from holds.  The
    previous fixed string ``catalyst_driven_entry`` collapsed all six
    cases into one bucket.

    Categories (with ``ORDER_EPSILON``-based zero comparison so dust
    positions do not flip exit/entry into trim/ramp):

    | Tag        | Condition                                          |
    |------------|----------------------------------------------------|
    | ``entry``    | ``prior ≈ 0`` AND ``new > 0``                    |
    | ``ramp``     | ``0 < prior < new``                              |
    | ``trim``     | ``prior > new > 0``                              |
    | ``exit``     | ``prior > 0`` AND ``new ≈ 0``                    |
    | ``hold_flat``| ``prior ≈ 0`` AND ``new ≈ 0``                    |
    | ``hold``     | ``prior == new`` AND ``prior > 0``               |

    Parameters
    ----------
    prior:
        The previous tick's weight for this ticker.
    new:
        The new (proposed) weight for this ticker.

    Returns
    -------
    str
        One of the six tag strings above.
    """

    prior_zero = prior < ORDER_EPSILON
    new_zero   = new   < ORDER_EPSILON

    if prior_zero and new_zero:
        return "hold_flat"
    if prior_zero and not new_zero:
        return "entry"
    if not prior_zero and new_zero:
        return "exit"

    # Both > 0 from here on.
    if new > prior:
        return "ramp"
    if new < prior:
        return "trim"
    return "hold"
```

- [ ] **Step 4: Wire the derivation into `derive_legacy_fields`**

Open `src/agents/strategist/derivation.py`. Locate `derive_legacy_fields` (around line 79). Inside the per-stance loop, alongside `lifecycle_action = derive_lifecycle_action(...)`, derive the per-stance tag:

```python
        # Per-stance decision tag (S6).  The legacy decision_tag was a
        # constant string in the LLM output; deriving it from the actual
        # weight delta gives Spec B / Spec C memory writers a
        # discriminating key.
        prior_weight = ctx.watchlist.get(stance.ticker, 0.0)
        decision_tag = derive_decision_tag(
            prior = prior_weight,
            new   = stance.preferred_weight,
        )
```

Then store it on the per-stance derived record (the exact field name depends on `DerivedFields` — read the existing fields and follow the pattern; if no per-stance tag field exists, add one).

If the spec's intent is for `decision_tag` to surface on the top-level `StrategistDecision`, also derive an *aggregate* tag (e.g. dominant tag across non-hold-flat stances) — but the spec table reads per-(prior, new) pair, so the natural home is per-stance. Confirm with the spec re-read and pick one. **Recommendation:** per-stance `decision_tag` on the derived record; top-level `strategist_decision.decision_tag` stays as the LLM-emitted string for now.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_decision_tag_derivation.py -v`

Expected: 7 passed (six parametric cases + the epsilon test).

- [ ] **Step 6: Run wider strategist + derivation tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/ tests/unit/test_strategist_schema.py -v`

Expected: no regression. If the schema test fails on the new field, extend the schema fixture to expect it.

- [ ] **Step 7: Commit**

```bash
git add src/agents/strategist/derivation.py tests/unit/agents/strategist/test_decision_tag_derivation.py
git commit -m "$(cat <<'EOF'
feat(strategist): derive decision_tag from prior+new weight (S6)

derive_decision_tag(prior, new) returns one of {entry, ramp, trim, exit,
hold_flat, hold} based on the weight delta — the single ORDER_EPSILON
threshold guards against dust positions flipping exit into trim.  Wired
into derive_legacy_fields per-stance so Spec B / Spec C memory writers
have a discriminating intent key rather than the constant
catalyst_driven_entry string the LLM was emitting.
EOF
)"
```

---

### Task 9 — S9: tenacity `before_sleep` carries wrapped agent name

**Files:**
- Modify: `src/agents/llm_retry.py`
- Create: `tests/agents/test_llm_retry_agent_name.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_llm_retry_agent_name.py`:

```python
"""S9 — tenacity retry warnings include the wrapped agent's name.

The previous ``before_sleep_log(_LOGGER, logging.WARNING)`` emitted
records with no agent attribution — all 28 retry warnings in
baseline-2025-09 logged ``<unknown>`` for the agent.  Attributing
retries to News / Fundamental / Strategist required an adjacent-row
heuristic.

The fix replaces the stock helper with a small closure that captures
``self.inner.name`` at wrapper-construction time.  This test pins the
contract: the captured log record's message must contain the inner
agent's name.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.agents import BaseAgent
from google.adk.events import Event

from agents.llm_retry import RetryingAgentWrapper
from config.llm_retry import RetryConfig


class _FlakyAgent(BaseAgent):
    """Test double that raises one retryable exception then succeeds.

    Mirrors the shape of a real LlmAgent for the retry wrapper's
    perspective — it exposes ``.name`` and ``.run_async`` and yields one
    ``Event`` on success.
    """

    name: str = "TestAnalyst"

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        if not getattr(self, "_raised", False):
            self._raised = True
            # A pydantic.ValidationError-shape exception classified as
            # retryable by ``_is_retryable``.  Using ImportError-free
            # construction so the test does not pull in pydantic
            # internals.
            from pydantic import ValidationError
            try:
                from pydantic import BaseModel, Field
                class _S(BaseModel):
                    x: int = Field(ge=0)
                _S.model_validate({"x": -1})
            except ValidationError as exc:
                raise exc

        yield Event(author=self.name)


pytestmark = pytest.mark.asyncio


async def test_retry_warning_includes_inner_agent_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The before_sleep hook attributes each retry to the wrapped agent."""

    cfg = RetryConfig(
        max_attempts        = 3,
        base_delay_seconds  = 0.001,
        max_delay_seconds   = 0.01,
    )
    wrapper = RetryingAgentWrapper(
        inner         = _FlakyAgent(),
        retry_config  = cfg,
    )

    caplog.set_level(logging.WARNING, logger="agents.llm_retry")

    # Run the wrapper; consume the (empty) async-gen so the retry loop
    # actually executes.  The ctx argument is replaced with a stub since
    # this test only exercises the retry wrapper's behaviour.
    async for _ in wrapper.run_async(ctx=None):  # type: ignore[arg-type]
        pass

    retry_records = [
        r for r in caplog.records
        if r.name == "agents.llm_retry" and r.levelno == logging.WARNING
    ]
    assert retry_records, "expected at least one retry-warning log record"
    assert any("TestAnalyst" in r.getMessage() for r in retry_records), (
        f"no retry record carried the wrapped agent name; messages were: "
        f"{[r.getMessage() for r in retry_records]}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/agents/test_llm_retry_agent_name.py -v`

Expected: FAIL — the retry record's message contains `<unknown>` (or no agent name) rather than `TestAnalyst`.

- [ ] **Step 3: Replace `before_sleep_log` with a closure**

Open `src/agents/llm_retry.py`. At the top of the file, alongside the existing tenacity imports, add `RetryCallState` and the `Callable` import:

```python
from collections.abc import AsyncGenerator, Callable
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
```

(Drop `before_sleep_log` from the import list — it is no longer used.)

Add a module-level factory above the `RetryingAgentWrapper` class:

```python
def _make_before_sleep(name: str) -> Callable[[RetryCallState], None]:
    """Build a tenacity ``before_sleep`` hook that names the wrapped agent.

    The stock ``before_sleep_log`` helper emits records with no hook
    for the wrapped agent's identity; later log analysis cannot tell
    which agent retried without an adjacent-row heuristic.  Capturing
    the agent name at wrapper-construction time means every retry
    record carries it.

    Parameters
    ----------
    name:
        The inner agent's ``.name`` — captured by closure so the hook
        knows which agent is retrying without consulting the
        ``RetryCallState``.

    Returns
    -------
    Callable
        A function suitable for ``AsyncRetrying(..., before_sleep=...)``.
    """

    def _hook(retry_state: RetryCallState) -> None:
        exc = (
            retry_state.outcome.exception()
            if retry_state.outcome is not None
            else None
        )
        _LOGGER.warning(
            "Retrying %s after %s (attempt %s)",
            name,
            type(exc).__name__ if exc else "<unknown>",
            retry_state.attempt_number,
        )

    return _hook
```

Then at the `AsyncRetrying` call site (line 317), change:

```python
            before_sleep = before_sleep_log(_LOGGER, logging.WARNING),
```

to:

```python
            before_sleep = _make_before_sleep(self.inner.name),
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/agents/test_llm_retry_agent_name.py -v`

Expected: 1 passed.

- [ ] **Step 5: Run the wider llm-retry tests to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/agents/ -k "llm_retry or retry" -v`

Expected: no regression.

- [ ] **Step 6: Commit**

```bash
git add src/agents/llm_retry.py tests/agents/test_llm_retry_agent_name.py
git commit -m "$(cat <<'EOF'
feat(observability): retry warnings carry wrapped agent name (S9)

Replaces tenacity's stock before_sleep_log with a closure that captures
self.inner.name at wrapper-construction time.  Every retry record now
attributes itself to the analyst that retried — closes the <unknown>
gap that forced the baseline-2025-09 LLM analysis to fall back to an
adjacent-row heuristic and that would become unworkable once memory
writes start landing their own retries into the same log stream.
EOF
)"
```

---

## Band 3 — Analyst Prompts

These four tasks (D1.2, D2.1, H4-switch, M1, M4) cluster in the two analyst-prompt files. **Important coupling:** D1.2 and M1 touch both `news/prompts.py` and `fundamental/prompts.py`; D2.1 touches only fundamental; M4 touches only news; H4-switch touches both. Bundle them into two tasks (one per prompt file) so each commit is self-contained.

### Task 10 — News prompt: D1.2 + M1 + M4 + H4-switch

**Files:**
- Modify: `src/agents/analysts/news/prompts.py`
- Create: `tests/unit/test_news_prompt_report_required.py`
- Create: `tests/unit/test_analyst_prompts_anti_truncation.py` (covers news + fundamental — populated here for news, extended in Task 11 for fundamental)
- Create: `tests/unit/test_news_prompt_bearish_nudge.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_news_prompt_report_required.py`:

```python
"""D1.2 — news prompt requires `report` whenever `is_no_data=false`.

The prompt previously said ``omit only when is_no_data=true``; the LLM
violated the instruction at 30.7 % across the baseline-2025-09 run.
D1.1 closes the loophole at the schema; D1.2 strengthens the wording
the LLM sees so the prompt and the schema sing in unison.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_report_required_wording_present() -> None:
    """The strengthened wording must appear in the rendered prompt."""

    rendered = build_news_instruction(_vocab())
    assert "REQUIRED whenever is_no_data=false" in rendered
    assert "Omit ONLY when" in rendered
    assert "summary plus 2 drivers" in rendered


def test_legacy_omit_only_wording_absent() -> None:
    """The old softer wording must not coexist with the new hard rule."""

    rendered = build_news_instruction(_vocab())
    assert "omit only when is_no_data=true" not in rendered
```

Create `tests/unit/test_analyst_prompts_anti_truncation.py`:

```python
"""M1 — anti-truncation guard present in news + fundamental prompts.

Five of 28 LLM retries in baseline-2025-09 were JSON-truncation EOFs
where the model ran into ``max_output_tokens`` while repeating a token.
A one-line prompt guard nudges the model away from the
``AMZN_AMZN_AMZN_…`` / ``\\n\\n\\n…`` / ``0000000000…`` failure mode.
"""
from __future__ import annotations

from agents.analysts.heuristics import FundamentalVocabulary, NewsVocabulary
from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.news.prompts        import build_news_instruction


_GUARD_FRAGMENT = "repeat a token or symbol three or more times in a row"


def _news_vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def _fundamental_vocab() -> FundamentalVocabulary:
    # The implementer must use whatever construction the existing
    # fundamental prompt-render test uses (tests/unit/test_fundamental_prompt_render.py)
    # so the test pulls real closed-vocab values.
    raise NotImplementedError("fill in from sibling test")


def test_news_prompt_has_anti_truncation_guard() -> None:
    rendered = build_news_instruction(_news_vocab())
    assert _GUARD_FRAGMENT in rendered


# Fundamental case added in Task 11 — placeholder kept here so the two
# halves of M1 are visible in one file.
```

Create `tests/unit/test_news_prompt_bearish_nudge.py`:

```python
"""M4 — news prompt contains explicit bearish-trigger guidance.

News verdict stance distribution was 467 bullish vs 25 bearish across
baseline-2025-09.  The corrective anchor is a short list of common
bearish triggers the model should not round up to neutral.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_news_bearish_triggers_present() -> None:
    """The rendered news prompt cites the canonical bearish anchors."""

    rendered = build_news_instruction(_vocab())
    for fragment in (
        "missed guidance",
        "downgrade",
        "supplier loss",
        "executive departure",
        "regulatory action",
        "do NOT default to neutral",
    ):
        assert fragment in rendered, f"missing bearish-anchor fragment: {fragment}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_news_prompt_report_required.py tests/unit/test_news_prompt_bearish_nudge.py -v`

Expected: both fail — the strengthened wording, anti-truncation guard, and bearish anchors are not yet in the prompt.

(Skip `test_analyst_prompts_anti_truncation.py` for now — its `_fundamental_vocab()` is `NotImplementedError`; that half lands in Task 11.)

- [ ] **Step 3: Apply D1.2, M1, M4, H4-switch to `news/prompts.py`**

Open `src/agents/analysts/news/prompts.py`. Apply four coupled edits:

**D1.2** — locate line 61 (`report       object — see schema below; omit only when is_no_data=true.`). Replace with:

```
report       object — see schema below.  REQUIRED whenever is_no_data=false;
             emit at minimum a summary plus 2 drivers.  Omit ONLY when
             is_no_data=true.
```

**M1** — locate the line immediately before the `--- HEADLINES & SUMMARIES FOR {ticker} ---` block. Insert:

```
Stop emitting if you are about to repeat a token or symbol three or more
times in a row.  Return the verdict as-is and never emit filler tokens.

```

(Note the trailing blank line for legibility.)

**M4** — locate lines 77–81 (the decision-rule block with positive/negative/mixed mapping). Append after the existing mapping:

```
- Bearish is appropriate for missed guidance, downgrade, supplier loss,
  executive departure, regulatory action, or adverse legal outcome — do
  NOT default to neutral when evidence is materially negative.
```

**H4-switch** — locate line 125 (`rationale_max = out_caps.verdict_rationale_max_chars`). Change to:

```python
    rationale_max = out_caps.verdict_rationale_prompt_budget
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_news_prompt_report_required.py tests/unit/test_news_prompt_bearish_nudge.py -v`

Expected: 3 passed (2 + 1).

- [ ] **Step 5: Run the existing news-prompt render tests to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_news_prompt_render.py -v`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/news/prompts.py tests/unit/test_news_prompt_report_required.py tests/unit/test_news_prompt_bearish_nudge.py tests/unit/test_analyst_prompts_anti_truncation.py
git commit -m "$(cat <<'EOF'
feat(analysts/news): report-required + anti-truncation + bearish nudge + derived budget (D1.2 + M1 + M4 + H4)

Strengthens the report-required wording so the prompt sings in unison
with the new schema validator (D1.1).  Adds a one-line anti-truncation
guard before the headlines block to head off AMZN_AMZN_AMZN truncation
EOFs.  Adds an explicit bearish-trigger anchor so the LLM stops rounding
materially negative news up to neutral.  Switches the rationale prompt
budget from the schema cap to the derived verdict_rationale_prompt_budget
so the LLM is told ~150 chars rather than the 230-char schema slack.
EOF
)"
```

---

### Task 11 — Fundamental prompt: D1.2 + D2.1 + M1 + H4-switch

**Files:**
- Modify: `src/agents/analysts/fundamental/prompts.py`
- Create: `tests/unit/test_fundamental_prompt_report_required.py`
- Modify: `tests/unit/test_analyst_prompts_anti_truncation.py` (replace the `_fundamental_vocab()` placeholder)
- Create: `tests/unit/test_fundamental_prompt_decision_rule.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fundamental_prompt_report_required.py`:

```python
"""D1.2 — fundamental prompt requires `report` whenever `is_no_data=false`.

Symmetric companion to the news D1.2 test.  Fundamental's missing-report
rate was lower (3.6 %) but the same loophole — closing it preserves
schema/prompt alignment.
"""
from __future__ import annotations

# Implementer: copy the vocab + builder import patterns from
# tests/unit/test_fundamental_prompt_render.py and replace the body.
from agents.analysts.fundamental.prompts import build_fundamental_instruction


def test_report_required_wording_present() -> None:
    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert "REQUIRED whenever is_no_data=false" in rendered
    assert "Omit ONLY when" in rendered
    assert "summary plus 2 drivers" in rendered


def test_legacy_omit_only_wording_absent() -> None:
    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert "omit only when is_no_data=true" not in rendered


def _fundamental_vocab():
    """Match the construction used by tests/unit/test_fundamental_prompt_render.py."""

    # NOTE TO IMPLEMENTER — read the sibling test for the exact vocab
    # construction; this function must return a populated
    # ``FundamentalVocabulary`` whose closed-vocab fields the prompt
    # template substitutes.
    raise NotImplementedError("fill in from sibling test")
```

Create `tests/unit/test_fundamental_prompt_decision_rule.py`:

```python
"""D2.1 — fundamental decision rule rewritten with neutral anchors.

The previous triple-AND-conjunction bullish trigger was structurally
unreachable for mega-cap watchlists, producing 0 bullish across 920
verdicts.  The replacement is anchor-based: routine 10b5-1 sales are
NEUTRAL not bearish, absence of activity is neutral, going-concern
language overrides, conflicting inputs land neutral.
"""
from __future__ import annotations

from agents.analysts.fundamental.prompts import build_fundamental_instruction


def _vocab():
    """See sibling test for construction details."""

    raise NotImplementedError("fill in from sibling test")


def test_new_anchors_present() -> None:
    """The four neutral anchors must appear in the rendered prompt."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    # Routine 10b5-1 = neutral
    assert "Routine 10b5-1" in rendered
    assert "NOT bearish" in rendered
    # Absence = neutral
    assert "Absence of insider activity is neutral" in rendered
    # Going-concern override
    assert "Going-concern language present" in rendered
    # Conflicting → neutral low conf
    assert "Conflicting inputs" in rendered


def test_old_and_conjunction_absent() -> None:
    """The structurally-unreachable AND-conjunction must be gone."""

    rendered = build_fundamental_instruction(vocab=_vocab())
    assert "cluster open-market buys" not in rendered
    assert "raised guidance" not in rendered or "Routine 10b5-1" in rendered
    # ``raised guidance`` may still appear inside other text — the
    # combined assertion guards against the *AND-conjunction phrasing*
    # specifically.
    assert "strongly bullish" not in rendered.lower()
```

Modify `tests/unit/test_analyst_prompts_anti_truncation.py` (from Task 10). Replace the `_fundamental_vocab()` body so the fundamental half can run:

```python
def _fundamental_vocab() -> FundamentalVocabulary:
    """See tests/unit/test_fundamental_prompt_render.py for the canonical shape."""

    # Replace this with the exact construction from the sibling test.
    raise NotImplementedError("fill in from sibling test")


def test_fundamental_prompt_has_anti_truncation_guard() -> None:
    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert _GUARD_FRAGMENT in rendered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_fundamental_prompt_report_required.py tests/unit/test_fundamental_prompt_decision_rule.py tests/unit/test_analyst_prompts_anti_truncation.py -v`

Expected: all fail (after the `_vocab` `NotImplementedError`s are replaced with the real construction). The wording changes are not yet in the prompt.

- [ ] **Step 3: Apply D1.2, D2.1, M1, H4-switch to `fundamental/prompts.py`**

Open `src/agents/analysts/fundamental/prompts.py`. Apply four coupled edits:

**D1.2** — locate line 77 (the `report       object — see schema below; omit only when is_no_data=true.` line — identical to news). Replace with the same strengthened wording as Task 10:

```
report       object — see schema below.  REQUIRED whenever is_no_data=false;
             emit at minimum a summary plus 2 drivers.  Omit ONLY when
             is_no_data=true.
```

**D2.1** — locate lines 93–101 (the triple-AND-conjunction decision rule). Replace the entire block with:

```
Decision guidance (anchors — reason from the evidence; this is not a
decision tree):

- Lean reflects the dominant signal across guidance, tone, risk-factor
  changes, and insider activity.  Use the full bullish / bearish range as
  the evidence supports.

- Routine 10b5-1 (planned) sales are pre-scheduled and disclosed in advance.
  They are NEUTRAL signal — NOT bearish.
- Discretionary open-market sales are bearish; clusters of them are
  strongly so.

- Absence of insider activity is neutral, not bearish — default to neutral
  with low confidence when there is nothing material to say.

- Going-concern language present → strongly bearish (overrides other signals).
- Conflicting inputs → neutral with low confidence.
```

**M1** — locate the line immediately before the `--- FUNDAMENTAL CONTEXT FOR {ticker} ---` block (or whatever the equivalent landmark is — match the surrounding template). Insert the same guard line as the news prompt:

```
Stop emitting if you are about to repeat a token or symbol three or more
times in a row.  Return the verdict as-is and never emit filler tokens.

```

**H4-switch** — find the line that sets `rationale_max = out_caps.verdict_rationale_max_chars`. Change to:

```python
    rationale_max = out_caps.verdict_rationale_prompt_budget
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_fundamental_prompt_report_required.py tests/unit/test_fundamental_prompt_decision_rule.py tests/unit/test_analyst_prompts_anti_truncation.py -v`

Expected: all pass.

- [ ] **Step 5: Run the existing fundamental-prompt render tests to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_fundamental_prompt_render.py -v`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/fundamental/prompts.py tests/unit/test_fundamental_prompt_report_required.py tests/unit/test_fundamental_prompt_decision_rule.py tests/unit/test_analyst_prompts_anti_truncation.py
git commit -m "$(cat <<'EOF'
feat(analysts/fundamental): neutral-anchored decision rule + report-required + anti-truncation + derived budget (D1.2 + D2.1 + M1 + H4)

Rewrites the bullish-trigger AND-conjunction (cluster buys + raised
guidance + tone) with anchor-based guidance: routine 10b5-1 sales NEUTRAL
not bearish, absence neutral, going-concern overrides.  Removes the
structural impossibility that produced 0/920 bullish in baseline-2025-09
without prescribing a new bullish path.  Adds the symmetric D1.2 wording
strengthening, M1 anti-truncation guard, and H4 derived-budget switch.
EOF
)"
```

---

## Band 4 — Strategist Prompt + Evidence Renderer

These four tasks (D1.3, M3, M5, R5) cluster in two files. **R5 depends on R4 (Task 3)** — the strategist prompt's risk-rule restatement reads from `config/risk_gate.py`.

### Task 12 — Evidence renderer: D1.3 + M3

**Files:**
- Modify: `src/agents/strategist/evidence_view.py`
- Create: `tests/unit/agents/strategist/test_evidence_view_missing_report.py`
- Create: `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/strategist/test_evidence_view_missing_report.py`:

```python
"""D1.3 — strategist evidence renders a visibility placeholder when report=None.

The schema validator (D1.1) closes the loophole for normal flow; this
test pins the defence-in-depth layer.  If a future regression somehow
re-introduces ``report=None`` on a ``is_no_data=False`` verdict, the
strategist sees the absence as data rather than silently reasoning over
less evidence.
"""
from __future__ import annotations

from agents.strategist.evidence_view import _format_per_analyst
from contract.evidence import AnalystEvidence, AnalystVerdict, TickerEvidence


def _verdict(*, is_no_data: bool, report=None) -> AnalystVerdict:
    return AnalystVerdict.model_construct(
        # ``model_construct`` skips the new D1.1 validator so we can
        # construct the degenerate (is_no_data=False, report=None)
        # combination for this defence-in-depth test specifically.
        lean        = "bullish",
        magnitude   = 0.5,
        confidence  = 0.6,
        rationale   = "x",
        key_factors = [],
        is_no_data  = is_no_data,
        report      = report,
    )


def test_missing_report_renders_placeholder() -> None:
    """A non-no-data verdict with report=None renders the placeholder."""

    te = TickerEvidence(
        ticker      = "AAPL",
        per_analyst = {
            "news": AnalystEvidence(
                verdict  = _verdict(is_no_data=False, report=None),
                features = {},
            ),
        },
    )

    lines = _format_per_analyst(te)
    joined = "\n".join(lines)

    assert "(no report this tick — analyst compliance failure)" in joined
```

Create `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py`:

```python
"""M3 — strategist evidence omits ``[Social]`` rows when all are is_no_data.

Social is permanently no-data (no provider wired) — the strategist's
per-ticker block was rendering 20 dead ``[Social] is_no_data: true`` rows
per tick, ~600 chars of dead attention.  This test pins the omission
behaviour and the symmetric "populated row appears when data lands"
contract.
"""
from __future__ import annotations

from agents.strategist.evidence_view import _format_per_analyst
from contract.evidence import AnalystEvidence, AnalystVerdict, TickerEvidence


def _no_data_verdict() -> AnalystVerdict:
    return AnalystVerdict.model_validate(
        {
            "lean":        "neutral",
            "magnitude":   0.0,
            "confidence":  0.0,
            "rationale":   "no data",
            "key_factors": [],
            "is_no_data":  True,
            "report":      None,
        }
    )


def test_no_data_social_row_omitted() -> None:
    """A no-data Social verdict produces no Social line in the rendered block."""

    te = TickerEvidence(
        ticker      = "AAPL",
        per_analyst = {
            "social": AnalystEvidence(verdict=_no_data_verdict(), features={}),
        },
    )

    lines = _format_per_analyst(te)
    joined = "\n".join(lines)
    assert "social" not in joined.lower(), (
        f"expected no social line for is_no_data=True, got: {joined!r}"
    )


def test_populated_social_row_appears() -> None:
    """A populated Social verdict still renders normally."""

    verdict = AnalystVerdict.model_validate(
        {
            "lean":        "bullish",
            "magnitude":   0.6,
            "confidence":  0.7,
            "rationale":   "active social chatter",
            "key_factors": [],
            "is_no_data":  False,
            "report":      {
                "summary":  "Active discussion across stocktwits and reddit.",
                "drivers":  [
                    {"name": "vol-up", "direction": "bull", "weight": 0.6, "body": "x"},
                    {"name": "tone",   "direction": "bull", "weight": 0.4, "body": "y"},
                ],
            },
        }
    )

    te = TickerEvidence(
        ticker      = "AAPL",
        per_analyst = {
            "social": AnalystEvidence(verdict=verdict, features={}),
        },
    )

    lines = _format_per_analyst(te)
    joined = "\n".join(lines)
    assert "social" in joined.lower(), (
        f"expected social line for populated verdict, got: {joined!r}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_evidence_view_missing_report.py tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py -v`

Expected: both fail.

- [ ] **Step 3: Apply D1.3 + M3 to `evidence_view.py`**

Open `src/agents/strategist/evidence_view.py`. Locate `_format_per_analyst` (lines 37–84). Apply two edits inside the per-analyst loop:

**M3** — at the top of the loop, after `ev = te.per_analyst.get(analyst)`, add a guard that skips Social when no-data:

```python
        if ev is None:
            # Slot present in the canonical catalogue but absent from this tick's data.
            lines.append(f"  - {analyst:<12} (missing)")
            continue

        # M3 — drop dead Social rows.  Social has no live provider; the
        # strategist was reading ``[Social] is_no_data: true`` × 20
        # tickers as dead attention.  Skip emitting the row entirely
        # when the verdict is no-data; populated Social verdicts still
        # render via the normal path below.
        if analyst == "social" and ev.verdict.is_no_data:
            continue

        if ev.verdict.is_no_data:
            # No-data verdict — no features were available; signal to LLM explicitly.
            lines.append(f"  - {analyst:<12} no_data")
            continue
```

**D1.3** — immediately before the rationale-truncation block (after the `ev.verdict.is_no_data` branch), add the missing-report placeholder:

```python
        # D1.3 — defence-in-depth: surface the absence of a report
        # block when the verdict claims data but the report field is
        # somehow None.  D1.1 closes this loophole at the schema; this
        # branch fires only on a future regression and makes the gap
        # immediately visible in the rendered evidence block.
        if not ev.verdict.is_no_data and ev.verdict.report is None:
            lines.append(
                f"  - {analyst:<12} (no report this tick — analyst compliance failure)"
            )
            continue

        # Truncate rationale to keep the per-analyst line compact, …
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_evidence_view_missing_report.py tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py -v`

Expected: 3 passed (1 + 2).

- [ ] **Step 5: Run the wider evidence-view tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/ -v`

Expected: no regression.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/evidence_view.py tests/unit/agents/strategist/test_evidence_view_missing_report.py tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py
git commit -m "$(cat <<'EOF'
feat(strategist): visibility for missing reports + drop dead Social rows (D1.3 + M3)

D1.3 surfaces a `(no report this tick — analyst compliance failure)`
placeholder in the strategist evidence block when a non-no-data verdict
arrives without a report — defence-in-depth against any future D1.1
regression.  M3 omits the [Social] row when its verdict is no-data,
saving ~600 chars of dead attention per strategist call.  Populated
Social rows still render normally so a future provider drops in cleanly.
EOF
)"
```

---

### Task 13 — Strategist prompt: M5 + R5

**Files:**
- Modify: `src/agents/strategist/prompts.py`
- Create: `tests/unit/test_strategist_prompt_worked_examples_ticker.py`
- Create: `tests/unit/test_strategist_prompt_risk_substitutions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_strategist_prompt_worked_examples_ticker.py`:

```python
"""M5 — strategist worked examples use the generic XYZ ticker.

The previous AAPL anchoring was a known mild-bias source where the LLM
latched onto the specific ticker when reasoning about the example shape.
The fix is purely cosmetic — replace AAPL with XYZ.
"""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_worked_examples_use_xyz() -> None:
    """Both worked examples reference XYZ rather than AAPL."""

    # The worked-examples section lives under ``## Two worked examples``.
    # Slice that section out of the full instruction so the assertion
    # is unaffected by stray AAPL/XYZ references elsewhere.
    header   = "## Two worked examples"
    body     = STRATEGIST_INSTRUCTION.split(header, 1)[1]
    examples = body.split("\n\n", 4)[:3]
    examples_text = "\n\n".join(examples)

    assert "XYZ" in examples_text
    assert "AAPL" not in examples_text
```

Create `tests/unit/test_strategist_prompt_risk_substitutions.py`:

```python
"""R5 — strategist prompt restates risk rules with config-driven values.

The prompt previously had a hard-coded "single-ticker weight at 20% and
keeps ≥10% cash" sentence.  R4 moved the constants to
``config/risk_gate.json``; R5 makes the prompt substitute them at module
import so a future config change automatically updates the prompt.
"""
from __future__ import annotations

import json
from pathlib import Path


def test_default_substitutions_visible() -> None:
    """With shipped defaults the prompt cites 20 %, 5 %, 50 %, no cash floor."""

    # Import the module fresh so the patched config (if any earlier test
    # mutated state) is re-applied.
    from importlib import reload
    import agents.strategist.prompts as prompts_mod
    reload(prompts_mod)

    text = prompts_mod.STRATEGIST_INSTRUCTION

    assert "20%" in text, "max_position_weight (20 %) must surface in the prompt"
    assert "5%" in text,  "max_delta_per_ticker (5 %) must surface in the prompt"
    assert "50%" in text, "max_total_turnover (50 %) must surface in the prompt"
    assert "No cash floor" in text, "default cash_floor=0 stanza must surface"


def test_substitutions_track_config_changes(tmp_path: Path, monkeypatch) -> None:
    """Editing the config + reloading flips the rendered percentages."""

    # Build a tweaked config file and point the loader at it.
    cfg_file = tmp_path / "risk_gate.json"
    cfg_file.write_text(
        json.dumps(
            {
                "min_held_weight":       0.001,
                "max_position_weight":   0.20,
                "cash_floor_weight":     0.05,
                "max_delta_per_ticker":  0.02,
                "max_total_turnover":    0.40,
            }
        ),
        encoding="utf-8",
    )

    from config import risk_gate as rg
    monkeypatch.setattr(rg, "_DEFAULT_PATH", cfg_file)
    rg.get_risk_gate_config.cache_clear()

    from importlib import reload
    import agents.strategist.prompts as prompts_mod
    reload(prompts_mod)

    text = prompts_mod.STRATEGIST_INSTRUCTION

    assert "2%" in text, "patched max_delta_per_ticker (2 %) must surface"
    assert "40%" in text, "patched max_total_turnover (40 %) must surface"
    assert "Cash reserve ≥5%" in text, "patched cash floor stanza must surface"
    assert "No cash floor" not in text, "default stanza must not coexist"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_strategist_prompt_worked_examples_ticker.py tests/unit/test_strategist_prompt_risk_substitutions.py -v`

Expected: both fail. M5 fails because the worked examples still use `AAPL`; R5 fails because the prompt has hard-coded `20 % … ≥10 % cash`.

- [ ] **Step 3: Apply M5 to the worked examples**

Open `src/agents/strategist/prompts.py`. Locate the worked-examples block (lines 101–117). Replace `AAPL` with `XYZ` in both examples (the two ticker references inside the JSON blobs, and the heading line if any):

```
OPEN (currently flat, opening at 0.05):
{{"ticker": "XYZ", "preferred_weight": 0.05, "conviction": 0.7,
"rationale": "Strong fundamentals, bullish technical setup",
"horizon": "swing", "target_price": 215.0, "stop_price": 180.0,
"catalyst": "earnings beat expected next week",
"close_reason": null, "trim_reason": null}}

CLOSE (held at 0.05, exiting to 0.0):
{{"ticker": "XYZ", "preferred_weight": 0.0, "conviction": 0.7,
"rationale": "Thesis invalidated by guidance cut",
"horizon": null, "target_price": null, "stop_price": null,
"catalyst": null,
"close_reason": "guidance cut invalidates thesis",
"trim_reason": null}}
```

- [ ] **Step 4: Apply R5 — risk substitutions**

Still in `src/agents/strategist/prompts.py`. At the top of the module, alongside the existing strategist-config import, add the risk-gate import:

```python
from config.risk_gate import get_risk_gate_config
```

After the existing `_STANCE = ...` / `_DECISION = ...` resolutions (the strategist-config singletons), add the risk-gate percentages:

```python
# R5 — risk-gate percentages, resolved at import time from
# ``config/risk_gate.json`` so a future config edit auto-updates the
# prompt without code change.  The integer-rounded percentages match
# how the LLM thinks about caps (and what the gate enforces — the gate
# operates on the float fractions, so 0.05 vs "5 %" stay aligned).
_RISK              = get_risk_gate_config()
_MAX_POSITION_PCT  = int(round(_RISK.max_position_weight  * 100))
_MAX_DELTA_PCT     = int(round(_RISK.max_delta_per_ticker * 100))
_MAX_TURNOVER_PCT  = int(round(_RISK.max_total_turnover   * 100))
_CASH_FLOOR_PCT    = int(round(_RISK.cash_floor_weight    * 100))

# Conditional cash-floor stanza — operator can re-introduce a floor by
# editing the JSON; the prompt re-renders accordingly without code.
if _RISK.cash_floor_weight <= 0.0:
    _CASH_FLOOR_STANZA = (
        "- No cash floor — full deployment is permitted when conviction "
        "supports it."
    )
else:
    _CASH_FLOOR_STANZA = (
        f"- Watchlist weight sum capped at "
        f"{100 - _CASH_FLOOR_PCT}% (cash reserve ≥{_CASH_FLOOR_PCT}%)."
    )
```

Locate lines 86–88 (the `Downstream caps single-ticker weight at 20%` paragraph). Replace with:

```
preferred_weight: float in [0.0, 1.0].  Long-only — 0.0 is the floor.

Hard rules the risk gate enforces after you respond (so a stance that
violates them will be clamped — propose values that already respect them):
- Single-ticker weight capped at {{MAX_POSITION_PCT}}%.
- Per-ticker weight change capped at {{MAX_DELTA_PCT}}% per tick — if you
  want to size up faster, the gate will trim your delta back to
  {{MAX_DELTA_PCT}}% and you ramp over multiple ticks.
- Total per-tick turnover (sum of |deltas| across watchlist) capped at
  {{MAX_TURNOVER_PCT}}%.
{{CASH_FLOOR_STANZA}}
```

Then extend the existing `.replace()` chain (lines 121–129) with the four new substitutions plus the stanza:

```python
STRATEGIST_INSTRUCTION = (
    _RAW_INSTRUCTION
    .replace("{{DECISION_REASONING_MAX}}",  str(_DECISION.reasoning_max_chars))
    .replace("{{DECISION_THESIS_MAX}}",     str(_DECISION.updated_thesis_max_chars))
    .replace("{{STANCE_RATIONALE_MAX}}",    str(_STANCE.rationale_max_chars))
    .replace("{{STANCE_CATALYST_MAX}}",     str(_STANCE.catalyst_max_chars))
    .replace("{{STANCE_CLOSE_REASON_MAX}}", str(_STANCE.close_reason_max_chars))
    .replace("{{STANCE_TRIM_REASON_MAX}}",  str(_STANCE.trim_reason_max_chars))
    .replace("{{MAX_POSITION_PCT}}",        str(_MAX_POSITION_PCT))
    .replace("{{MAX_DELTA_PCT}}",           str(_MAX_DELTA_PCT))
    .replace("{{MAX_TURNOVER_PCT}}",        str(_MAX_TURNOVER_PCT))
    .replace("{{CASH_FLOOR_STANZA}}",       _CASH_FLOOR_STANZA)
)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_strategist_prompt_worked_examples_ticker.py tests/unit/test_strategist_prompt_risk_substitutions.py -v`

Expected: 3 passed (1 + 2).

- [ ] **Step 6: Run wider strategist-prompt tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/ tests/unit/test_strategist_schema.py -v`

Expected: no regression.

- [ ] **Step 7: Commit**

```bash
git add src/agents/strategist/prompts.py tests/unit/test_strategist_prompt_worked_examples_ticker.py tests/unit/test_strategist_prompt_risk_substitutions.py
git commit -m "$(cat <<'EOF'
feat(strategist): config-driven risk-rule substitutions + XYZ examples (M5 + R5)

Replaces the AAPL worked-examples anchor with the generic XYZ.  Adds a
config-driven risk-rule stanza so the prompt cites 20 %, 5 %, 50 %, and
the no-cash-floor sentence from config/risk_gate.json.  Editing the JSON
flips the rendered percentages without code change — keeps the prompt
and gate in lockstep through any future tuning.
EOF
)"
```

---

## Band 5 — Observability / Backtest Reporting

These three tasks (S4, S7, S8) are independent of every other band. They live in backtest-only files (§D1 carve-out) and have no live counterpart. They should land before any backtest re-run.

### Task 14 — S4: span-name prefix bugs in `reporting.py`

**Files:**
- Modify: `src/backtest/reporting.py`
- Create: `tests/unit/test_reporting_span_names.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_reporting_span_names.py`:

```python
"""S4 — reporting.py uses startswith() against ADK span names.

ADK emits spans named ``generate_content <model_id>`` and
``invoke_agent <agent_name>``; the previous ``if name == "generate_content"``
exact-match rejected every span.  Token counters showed 0/0/0 and the
per-agent latency section was empty despite both being populated in
``obs/traces/*.json``.

This module pins the two prefix-match contracts via the
``_aggregate_obs_artefacts`` reader.
"""
from __future__ import annotations

import json
from pathlib import Path


def _write_trace(p: Path, *, spans: list[dict]) -> None:
    """Materialise one obs/traces/*.json file with the supplied spans."""

    p.write_text(json.dumps({"spans": spans}), encoding="utf-8")


def test_generate_content_with_model_suffix_is_counted(tmp_path: Path) -> None:
    """A span named ``generate_content gemini-2.5-flash-lite`` is counted."""

    from backtest.reporting import _aggregate_obs_artefacts

    obs_dir = tmp_path / "obs"
    (obs_dir / "traces").mkdir(parents=True)
    _write_trace(
        obs_dir / "traces" / "tick.json",
        spans=[
            {
                "name":       "generate_content gemini-2.5-flash-lite",
                "attributes": {
                    "gen_ai.usage.input_tokens":  1543,
                    "gen_ai.usage.output_tokens": 88,
                },
                "duration_ms": 12_300,
            },
        ],
    )

    agg = _aggregate_obs_artefacts(obs_dir)

    assert agg is not None
    assert agg["tokens"]["input"]  == 1543
    assert agg["tokens"]["output"] == 88
    assert agg["tokens"]["total"]  == 1631


def test_invoke_agent_with_name_suffix_is_counted(tmp_path: Path) -> None:
    """An ``invoke_agent FundamentalAnalyst_AAPL`` span is grouped by agent."""

    from backtest.reporting import _aggregate_obs_artefacts

    obs_dir = tmp_path / "obs"
    (obs_dir / "traces").mkdir(parents=True)
    _write_trace(
        obs_dir / "traces" / "tick.json",
        spans=[
            {
                "name":       "invoke_agent FundamentalAnalyst_AAPL",
                "attributes": {"gen_ai.agent.name": "FundamentalAnalyst_AAPL"},
                "duration_ms": 11_500,
            },
            {
                "name":       "invoke_agent FundamentalAnalyst_AAPL",
                "attributes": {"gen_ai.agent.name": "FundamentalAnalyst_AAPL"},
                "duration_ms": 12_500,
            },
        ],
    )

    agg = _aggregate_obs_artefacts(obs_dir)

    assert agg is not None
    bucket = agg["agent_latency_ms"]["FundamentalAnalyst_AAPL"]
    assert bucket["count"] == 2
    assert bucket["min"]   == 11_500
    assert bucket["max"]   == 12_500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_reporting_span_names.py -v`

Expected: both fail — the exact-`==` checks reject the suffixed span names.

- [ ] **Step 3: Switch to `startswith()` in `reporting.py`**

Open `src/backtest/reporting.py`. Locate lines 581 and 590 inside `_aggregate_obs_artefacts`. Replace the exact-match conditionals:

```python
                # Token usage lives on ``generate_content`` spans only.  ADK
                # emits them as ``generate_content <model_id>`` (e.g.
                # ``generate_content gemini-2.5-flash-lite``); use a prefix
                # match so the model-id suffix doesn't reject the span.
                if name.startswith("generate_content"):
                    generate_spans += 1
                    input_tokens   += int(attrs.get("gen_ai.usage.input_tokens",  0) or 0)
                    output_tokens  += int(attrs.get("gen_ai.usage.output_tokens", 0) or 0)

                # ``invoke_agent`` spans carry the agent name in
                # ``gen_ai.agent.name`` and the wall-clock duration on
                # the span itself.  ADK suffixes the span name with the
                # agent name (e.g. ``invoke_agent FundamentalAnalyst_AAPL``);
                # prefix-match so the suffix doesn't reject it.
                if name.startswith("invoke_agent"):
                    agent       = attrs.get("gen_ai.agent.name", "<unknown>")
                    duration_ms = float(span.get("duration_ms", 0.0) or 0.0)
                    # … (existing bucket-accumulation code unchanged) …
```

Also rename the metrics-file fill-count label to surface the closed-round-trip semantics. Locate where the metrics file is written (search for `Total fills`) and rename to `Closed round-trips` (the underlying value is unchanged — this is purely a label fix):

```python
        f.write(f"- Closed round-trips: {fill_count}\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_reporting_span_names.py -v`

Expected: 2 passed.

- [ ] **Step 5: Run the wider reporting tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_equity_curve.py tests/unit/test_plot_equity.py tests/unit/test_spy_metrics.py -v`

Expected: no regression.

- [ ] **Step 6: Commit**

```bash
git add src/backtest/reporting.py tests/unit/test_reporting_span_names.py
git commit -m "$(cat <<'EOF'
fix(backtest): prefix-match ADK span names in reporting (S4)

ADK emits `generate_content <model_id>` and `invoke_agent <agent_name>`;
the previous exact-match rejected every span, leaving the metrics report
with `LLM tokens — input 0, output 0` and an empty per-agent latency
table despite both being populated in obs/traces/*.json.  Switches both
checks to startswith() and renames `Total fills` to `Closed round-trips`
so the closed-round-trip semantics are visible in the metrics file.
EOF
)"
```

---

### Task 15 — S7: `logger.exception` inside `contextlib.suppress`

**Files:**
- Modify: `src/observability/trace.py`
- Create: `tests/unit/test_trace_writer_exception_logging.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_trace_writer_exception_logging.py`:

```python
"""S7 — strategist trace exception logs via logger.exception().

``observability/trace.py:163`` previously did
``with contextlib.suppress(Exception): tw.snapshot(...)`` — silently
swallowing any serialisation failure.  Tick 1's ``03_strategist`` trace
was missing in baseline-2025-09 because the LLM ran but the trace write
crashed and the suppress hid it.

The fix logs via ``logger.exception`` inside the suppress so single-tick
drops are not invisible while the suppress still keeps the run alive.
"""
from __future__ import annotations

import logging

import pytest

from observability.trace import _trace_section


class _ExplodingTraceWriter:
    """Stand-in TraceWriter whose ``snapshot`` raises on every call."""

    def snapshot(self, *args, **kwargs):
        raise RuntimeError("simulated trace serialisation crash")


def test_trace_failure_logs_exception(caplog: pytest.LogCaptureFixture) -> None:
    """A snapshot crash logs an exception record but does not propagate."""

    state = {"_trace": _ExplodingTraceWriter()}

    caplog.set_level(logging.WARNING, logger="observability.trace")

    # Should not raise — suppress keeps the run alive.
    _trace_section(state, label="03_strategist", payload={"x": 1})

    exception_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert exception_records, (
        "expected a logged warning/exception when the trace writer crashes"
    )
    assert any("simulated trace serialisation crash" in r.getMessage() for r in exception_records), (
        f"expected the crash message in the log, got: "
        f"{[r.getMessage() for r in exception_records]}"
    )
```

(Adapt `_trace_section` to whatever the real helper name is — read `src/observability/trace.py` to confirm. The patch site is the function that wraps the existing `contextlib.suppress(Exception)` at line 163.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trace_writer_exception_logging.py -v`

Expected: FAIL — no log record is emitted (the suppress is silent).

- [ ] **Step 3: Add `logger.exception` inside the suppress**

Open `src/observability/trace.py`. Locate line 163 (`with contextlib.suppress(Exception): tw.snapshot(label, payload, state_keys=state_keys)`).

Add a module-level logger at the top of the file if one is not present:

```python
import logging

_LOGGER = logging.getLogger(__name__)
```

Replace the suppress block with:

```python
    # Route to the writer; serialisation errors are logged but otherwise
    # swallowed so the no-op *production* path is never affected by
    # trace-side failures.  The previous silent suppress hid a tick-1
    # ``03_strategist`` drop in baseline-2025-09; the explicit log puts
    # any future drop on the operator's radar.
    try:
        tw.snapshot(label, payload, state_keys=state_keys)
    except Exception:
        _LOGGER.exception(
            "trace writer snapshot failed for label=%s — run continues",
            label,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trace_writer_exception_logging.py -v`

Expected: 1 passed.

- [ ] **Step 5: Run the wider trace-writer tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trace_writer.py tests/unit/test_trace_maybe_noop.py tests/unit/test_llm_trace_callbacks.py -v`

Expected: no regression.

- [ ] **Step 6: Commit**

```bash
git add src/observability/trace.py tests/unit/test_trace_writer_exception_logging.py
git commit -m "$(cat <<'EOF'
fix(observability): log trace-writer crashes instead of silently swallowing (S7)

contextlib.suppress(Exception) silently hid the tick-1 03_strategist
trace failure in baseline-2025-09; the LLM ran for 38.6 s but the trace
went missing.  Replaces the suppress with try/except + logger.exception
so a future crash surfaces in the log without aborting the run.
EOF
)"
```

---

### Task 16 — S8: rename benign tripwires to `*_advisory`

**Files:**
- Modify: `src/backtest/audit/tripwires.py`
- Modify: `src/backtest/audit/telemetry.py`
- Create: `tests/backtest/test_tripwire_advisory_rename.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backtest/test_tripwire_advisory_rename.py`:

```python
"""S8 — benign tripwires renamed to ``*_advisory`` and excluded from actionable summary.

Two tripwires fired benignly on every (relevant) tick in baseline-2025-09:

- ``midnight_utc_timestamps_seen`` (46/46 ticks) — date-only sources
  promoted to midnight is steady state.
- ``open_tick_sameday_bar`` (23/23 open ticks) — provider strips the
  same-day bar before the consumer sees it; the audit fires before the
  strip.

Renaming both to ``*_advisory`` documents why they are benign and gets
them out of the "tripwires_fired" actionable count so real signal is
not drowned out.
"""
from __future__ import annotations

from backtest.audit.tripwires import (
    ACTIONABLE_TRIPWIRES,
    compute_tripwires,
)


def test_renamed_tripwires_exist() -> None:
    """Both tripwires surface under the new ``*_advisory`` names."""

    # ``compute_tripwires`` must accept the existing inputs unchanged
    # but produce keys ``midnight_utc_timestamps_seen_advisory`` and
    # ``open_tick_sameday_bar_advisory`` rather than the legacy names.
    result = compute_tripwires(
        telemetry={
            "per_domain":   {
                "price_history": {
                    "as_of":         "2025-09-02T13:30:00",
                    "phase":         "open",
                    "tickers":       {
                        "AAPL": {
                            "count":            1,
                            "min_ts":           "2025-09-02T00:00:00",
                            "max_ts":           "2025-09-02T00:00:00",
                            "midnight_count":   1,
                            "missing_count":    0,
                            "sameday_bar_seen": True,
                        },
                    },
                },
            },
            "strict_mode": True,
        },
    )

    assert "midnight_utc_timestamps_seen_advisory" in result
    assert "open_tick_sameday_bar_advisory"        in result


def test_renamed_tripwires_not_in_actionable_set() -> None:
    """The advisory tripwires are excluded from ``ACTIONABLE_TRIPWIRES``."""

    assert "midnight_utc_timestamps_seen_advisory" not in ACTIONABLE_TRIPWIRES
    assert "open_tick_sameday_bar_advisory"        not in ACTIONABLE_TRIPWIRES


def test_legacy_keys_absent() -> None:
    """The old (un-suffixed) names must not coexist alongside the new ones."""

    result = compute_tripwires(
        telemetry={
            "per_domain":   {},
            "strict_mode":  True,
        },
    )

    assert "midnight_utc_timestamps_seen" not in result
    assert "open_tick_sameday_bar"        not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_tripwire_advisory_rename.py -v`

Expected: all fail — the legacy keys still exist; the `ACTIONABLE_TRIPWIRES` set is not yet exposed.

- [ ] **Step 3: Apply the rename in `tripwires.py`**

Open `src/backtest/audit/tripwires.py`. Locate the definitions around lines 71–72.

Rename the two output keys throughout `compute_tripwires`:

- `midnight_utc_timestamps_seen` → `midnight_utc_timestamps_seen_advisory`
- `open_tick_sameday_bar`        → `open_tick_sameday_bar_advisory`

Add explanatory comments above each rename:

```python
            # ``*_advisory`` suffix denotes a benign tripwire that fires
            # on every relevant tick by design:
            #
            # - midnight_utc_timestamps_seen_advisory — date-only sources
            #   (e.g. SEC filings) are promoted to 00:00 UTC at cache
            #   write.  Every tick reading those domains will see at
            #   least one midnight timestamp; the fire signals nothing
            #   wrong.
            #
            # - open_tick_sameday_bar_advisory — the price-history cache
            #   stores daily bars at 00:00 UTC; ``read_ohlcv`` includes
            #   the day-of bar via ``func.date(ts) <= end``.  The
            #   provider strips that same-day bar before the consumer
            #   sees it (see src/backtest/providers/price_history_cache.py:92-93),
            #   but the audit fires *before* the strip.
            #
            # Both are documented here rather than dropped so the audit
            # record still carries useful provenance data; the
            # ``ACTIONABLE_TRIPWIRES`` set below excludes them from the
            # operator-facing "tripwires fired" count.
```

Add a module-level set defining what counts as actionable:

```python
ACTIONABLE_TRIPWIRES: frozenset[str] = frozenset(
    {
        "any_filter_key_after_as_of",
        "wallclock_fallback_fired",
        "strict_mode_violated",
        # Add other actionable tripwire keys here.  The two
        # ``*_advisory`` tripwires are deliberately excluded.
    }
)
```

(Replace the placeholder set entries with whatever the existing actionable tripwire keys are — grep `tripwires.py` for the legacy "fired" enumeration. Keep the set frozenset so consumers can iterate without mutation risk.)

- [ ] **Step 4: Update any consumer that referenced the old names**

Grep for `midnight_utc_timestamps_seen` and `open_tick_sameday_bar` across the codebase:

```bash
grep -rn "midnight_utc_timestamps_seen\|open_tick_sameday_bar" src/ scripts/ tests/
```

Update each call site — most live in `src/backtest/audit/telemetry.py` near line 184 (the construction) and in the summary writer (the count rendering). Append `_advisory` everywhere.

- [ ] **Step 5: Run the new test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/test_tripwire_advisory_rename.py -v`

Expected: 3 passed.

- [ ] **Step 6: Run the wider audit-tripwire tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/audit/ tests/backtest/leak_regressions/ -v`

Expected: no regression. Any existing fixtures asserting the legacy names must be updated alongside the rename — this is a JSON-key contract change and downstream fixture files (e.g. `tests/fixtures/audit/*.tick.json`) may carry the old name. Regenerate them if so.

- [ ] **Step 7: Commit**

```bash
git add src/backtest/audit/tripwires.py src/backtest/audit/telemetry.py tests/backtest/test_tripwire_advisory_rename.py
git commit -m "$(cat <<'EOF'
fix(audit): rename benign tripwires to *_advisory + exclude from actionable set (S8)

midnight_utc_timestamps_seen and open_tick_sameday_bar fire on every
relevant tick by design (date-only source promotion; provider-stripped
same-day bar).  Renames both to *_advisory and adds ACTIONABLE_TRIPWIRES
so the operator-facing "tripwires fired" count no longer drowns real
signal in benign noise.
EOF
)"
```

---

## End-to-End Verification (After All Bands Land)

These steps confirm the full spec landed cleanly across bands. Run after every previous task is committed.

- [ ] **Step 1: Full unit + integration suite passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`

Expected: all pass. Any failure is a regression introduced by the spec; do not proceed until clean.

- [ ] **Step 2: Lint passes**

Run: `.venv/bin/python -m ruff check src/ scripts/ tests/`

Expected: no issues. Fix any lint failures with targeted edits — do not blanket-ignore.

- [ ] **Step 3: Type-check passes**

Run: `.venv/bin/python -m mypy src/ scripts/` (if mypy is wired in this project — check `pyproject.toml`; skip the step if not).

Expected: no new errors versus the pre-spec baseline.

- [ ] **Step 4: Backtest re-run on baseline-2025-09**

Run: `PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --runs-root backtests/baseline-2025-09/runs/spec-a-rollout`

Expected: a clean run-tree under `backtests/baseline-2025-09/runs/spec-a-rollout/`. Inspect:

- `report/metrics.md`: non-zero token counters; per-agent latency table populated; "Closed round-trips" label (S4).
- `audit/*.tick.json`: `cache_hits` count matches structured-log count (S3); no `*_advisory` tripwire in the actionable summary (S8).
- `decisions/*.json`: `analyst_inputs.fundamental.insider` is a JSON dict (S5); `decision_tag` discriminates entries from trims (S6).
- LLM analysis: news missing-report rate ≈ 0 (D1.1); fundamental bullish-rate > 0 across the window (D2.1); rationale `string_too_long` retries ≈ 0 (H4); truncation EOFs ≈ 0 (M1).
- Strategist behaviour: cash drag < 78 % (R1/R2/R3); positions spread across more than 3 tickers.

- [ ] **Step 5: Optional — verify backlog candidates added to `docs/superpowers/backlog.md`**

Per the spec §12, six candidates were surfaced. They are out of scope for this plan but should land in the backlog after Spec A approval. Confirm `docs/superpowers/backlog.md` has entries for:

1. Vocab rename `planned_sale_dominant` → `routine_sale_dominant`.
2. Extend derived-budget pattern to summary / driver caps.
3. Diagnose SPY-return discrepancy in `reporting.py`.
4. Conviction-scaled `max_delta_per_ticker`.
5. Per-ticker stop-price enforcement at the risk gate.
6. `MIN_INVESTED_WEIGHT` floor on the strategist prompt.

---

## Self-Review

### Spec coverage

| Spec item | Task | Status |
|---|---|---|
| H4 — derived rationale budget | Task 1 (config), Tasks 10 + 11 (prompt switch) | ✓ |
| D1.1 — schema validator | Task 2 | ✓ |
| R4 — `config/risk_gate.json` + loader | Task 3 | ✓ |
| R1 — remove cash floor | Task 3 (JSON default) | ✓ |
| R2 — widen max_delta | Task 3 (JSON default) | ✓ |
| R3 — lift turnover | Task 3 (JSON default) | ✓ |
| S1 — `reference_prices` PIT clamp | Task 4 | ✓ |
| S2 — executor bookkeeping | Task 5 | ✓ |
| S3 — `cache_hits` audit relocated | Task 6 | ✓ |
| S5 — insider `.model_dump()` + strict serialiser | Task 7 | ✓ |
| S6 — `decision_tag` derivation | Task 8 | ✓ |
| S9 — retry agent-name closure | Task 9 | ✓ |
| D1.2 — news report-required wording | Task 10 | ✓ |
| D1.2 — fundamental report-required wording | Task 11 | ✓ |
| D2.1 — fundamental decision-rule rewrite | Task 11 | ✓ |
| M1 — anti-truncation guard (news + fundamental) | Tasks 10 + 11 | ✓ |
| M4 — news bearish nudge | Task 10 | ✓ |
| D1.3 — visibility placeholder | Task 12 | ✓ |
| M3 — drop dead Social rows | Task 12 | ✓ |
| M5 — `XYZ` ticker in worked examples | Task 13 | ✓ |
| R5 — config-driven prompt substitutions | Task 13 | ✓ |
| S4 — span-name prefix bugs | Task 14 | ✓ |
| S7 — trace-writer exception logging | Task 15 | ✓ |
| S8 — tripwire `*_advisory` rename | Task 16 | ✓ |

Every fix in spec §2's table has a task. End-to-end verification (Step 4 above) covers the backtest-level outcomes promised in spec §8.

### Placeholder scan

- Task 5 (S2) Step 1 ships skipped scaffold tests because the broker fixture must be sourced from the existing `tests/agents/executor/test_isolated_failure.py` — implementer fills the body. Marked explicitly as scaffold with `pytest.mark.skip` so it cannot silently pass; Step 2 re-runs after un-skipping.
- Task 11 (fundamental prompt) Step 1 has `NotImplementedError` `_fundamental_vocab()` and `_vocab()` stubs that the implementer fills from the sibling `tests/unit/test_fundamental_prompt_render.py`. This is deliberate — the canonical vocab shape lives there and would drift if hard-coded here. The plan flags both stubs to the implementer.

These are the only deliberate placeholder bridges, both bounded and explicit. Every other step ships complete code or commands.

### Type consistency

- `decision_tag` enum strings (Task 8) match the spec table verbatim: `entry`, `ramp`, `trim`, `exit`, `hold_flat`, `hold`.
- `RiskGateConfig` field names (Task 3) match `OrderEpsilon`-less `MIN_HELD_WEIGHT` / `MAX_POSITION_WEIGHT` / `CASH_FLOOR_WEIGHT` / `MAX_DELTA_PER_TICKER` / `MAX_TOTAL_TURNOVER` legacy constants after re-export.
- `verdict_rationale_prompt_budget` (Task 1) is the property name referenced by both `news/prompts.py` and `fundamental/prompts.py` (Tasks 10 + 11).
- Strategist substitution markers (Task 13) — `{{MAX_POSITION_PCT}}`, `{{MAX_DELTA_PCT}}`, `{{MAX_TURNOVER_PCT}}`, `{{CASH_FLOOR_STANZA}}` — all four appear in both the template text and the `.replace()` chain.
- `ACTIONABLE_TRIPWIRES` (Task 16) is a `frozenset[str]` exported from `tripwires.py`; the test imports it under that name.

No naming drift detected.

---

**End of plan.**

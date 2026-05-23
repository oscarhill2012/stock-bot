# Three-layer LLM retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/Phase10-post-first-backtest/specs/llm-retry-three-layer.md`

**Goal:** Extend the single `RetryingAgentWrapper` to retry on three independent failure classes (Vertex 429, wall-clock timeout, Pydantic schema validation) with per-class budgets, per-agent `max_output_tokens` caps, structured exhaustion logging, and per-tick retry counters surfaced on the existing terminal-summary rows.

**Architecture:** No new wrapper classes. The existing `RetryingAgentWrapper` is rewritten internally; its constructor gains three new args (`timeout_seconds`, `policies`, `retry_state_key`). `asyncio.wait_for(...)` enforces the per-agent timeout around the inner agent's `run_async`. Config splits across three JSON files: `config/retry_429.json` (project-wide 429 policy only), and per-agent `llm` blocks added to `config/analysts.json` and `config/strategist.json`. The `tenacity` dependency is dropped from the retry path; backoff and sleep are hand-rolled.

**Tech Stack:** Python 3.14, Google ADK (`google-adk`), `google.genai`, Pydantic v2, pytest. No new dependencies.

---

## File responsibility map

Files this plan touches, grouped by concern:

| File | Responsibility | Action |
|---|---|---|
| `config/llm_retry.json` | 429 retry policy JSON | **Rename** → `config/retry_429.json` |
| `config/retry_429.json` | 429 retry policy JSON (renamed; narrower comment) | Renamed-from above |
| `config/analysts.json` | Analyst input caps + new `llm` block per analyst | Extend |
| `config/strategist.json` | Strategist char caps + new `llm` block | Extend |
| `config/README.md` | Config-file documentation | Update |
| `src/config/llm_retry.py` | 429 policy loader | **Rename** → `src/config/retry_429.py`; rename symbols |
| `src/config/retry_429.py` | 429 policy loader (renamed) | Renamed-from above |
| `src/config/analysts.py` | Analyst config Pydantic models | Add `LlmCaps` model; attach to `NewsCaps.llm` / `FundamentalCaps.llm` |
| `src/config/strategist.py` | Strategist config Pydantic models | Add `LlmCaps` model; attach to `StrategistConfig.llm` |
| `src/agents/llm_retry.py` | The retry wrapper | Rewrite internals; new ctor + `_classify` + helpers |
| `src/contract/evidence.py` | Pydantic evidence schema | Update import path for the renamed 429 loader |
| `src/agents/analysts/news/per_ticker.py` | News per-ticker branch factory | Wire `generate_content_config` + new wrapper args |
| `src/agents/analysts/fundamental/per_ticker.py` | Fundamental per-ticker branch factory | Same |
| `src/agents/strategist/agent.py` | Strategist factory + validation callback | Wire factory; pass `retries=` in `emit_analyst_summary` call |
| `src/agents/analysts/news/joiner.py` | News joiner | Pass `retries=` in `emit_analyst_summary` call |
| `src/agents/analysts/fundamental/joiner.py` | Fundamental joiner | Pass `retries=` in `emit_analyst_summary` call |
| `src/observability/terminal_log.py` | Terminal summary renderer | Extend `emit_analyst_summary` with optional `retries=` |
| `tests/unit/agents/test_llm_retry.py` | Wrapper unit tests | **Rewrite** for the new API (ctor + classification + telemetry) |
| `tests/unit/config/test_retry_429.py` | 429 loader tests | **Create** (no prior test file existed at this path) |
| `tests/unit/config/test_analysts_config.py` | Analyst config tests | Extend |
| `tests/unit/config/test_strategist_config.py` | Strategist config tests | **Create** |
| `tests/unit/observability/test_terminal_log.py` | Terminal summary tests | Extend (`retries=` suffix) |
| `tests/analysts/test_per_ticker_branch.py` | Per-ticker branch wiring tests | Extend |
| `tests/agents/strategist/test_build_strategist.py` | Strategist factory wiring tests | **Create** |
| `tests/integration/test_retry_smoke.py` | End-to-end one-tick smoke | **Create** |

---

## Conventions & project pinned rules

These bind every task in this plan:

- **Bash convention.** Run commands from the project root **without** prepending `cd "/home/oscarhill2012/Documents/Repository/StockBot" && ...`. The Bash tool already runs there. Commands shown below assume this.
- **Test entrypoint.** `PYTHONPATH=src .venv/bin/python -m pytest tests/<path> -v` for individual tests; `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` for the full suite. Linter: `.venv/bin/python -m ruff check src/`.
- **British English** in code, comments, docstrings, prose. (`colour`, `behaviour`, `organisation`, `analyse`, `optimise`.)
- **Comments are mandatory** on non-trivial logic. Every function gets a docstring with purpose, parameters, return.
- **Whitespace for legibility** — blank lines between logical blocks.
- **No live API calls in tests** (per `docs/test-policy.md`). Stub at the leaf HTTP boundary, or use fake LlmAgents.
- **TDD discipline.** Write failing test → run it red → minimal code → run green → commit. One concern per task.
- **No backwards-compatibility shims.** The codebase is pre-deployment. Clean rename in one commit; no transitional aliases.

---

## Task 1: Rename `config/llm_retry.json` → `config/retry_429.json` and the loader module

**Files:**
- Rename: `config/llm_retry.json` → `config/retry_429.json`
- Rename: `src/config/llm_retry.py` → `src/config/retry_429.py`
- Modify: `src/contract/evidence.py` (one import)
- Modify: `config/README.md` (one row + description tweak)

This task does the file-rename plumbing only. The wrapper file (`src/agents/llm_retry.py`) and its test file are NOT renamed — only the *config* loader file is. The 429 policy values are unchanged.

- [ ] **Step 1: Rename the JSON config file**

```bash
git mv config/llm_retry.json config/retry_429.json
```

- [ ] **Step 2: Narrow the `_comment` in the renamed JSON file**

Edit `config/retry_429.json`. Replace the `_comment` value with:

```
Vertex AI HTTP 429 retry policy. See src/config/retry_429.py for the loader. Timeout and schema retry counts live per-agent in config/analysts.json and config/strategist.json — only the 429 policy is project-wide.
```

Leave `max_attempts`, `base_delay_seconds`, `max_delay_seconds` values unchanged (`5`, `2.0`, `30.0`).

- [ ] **Step 3: Rename the loader Python module**

```bash
git mv src/config/llm_retry.py src/config/retry_429.py
```

- [ ] **Step 4: Update symbols inside the renamed loader**

Edit `src/config/retry_429.py`:

1. In the module docstring, replace every occurrence of `llm_retry.json` with `retry_429.json`.
2. Update `_DEFAULT_PATH`:

   ```python
   _DEFAULT_PATH = Path("config/retry_429.json")
   ```

3. Rename `RetryConfig` to `Retry429Policy` everywhere in the file. (Same Pydantic model body; only the class name changes.)
4. Rename `load_retry_config` to `load_retry_429_policy`.
5. Rename `get_retry_config` to `get_retry_429_policy`.
6. Keep `_reset_cache()` named exactly the same — it is a private test hook that callers reference by `from src.config.retry_429 import _reset_cache`.

- [ ] **Step 5: Update the one consumer import in `src/contract/evidence.py`**

```bash
grep -n 'from config.llm_retry\|import.*llm_retry' src/contract/evidence.py
```

Replace the matched import with:

```python
from config.retry_429 import get_retry_429_policy
```

…and replace any in-file uses of `get_retry_config()` or `RetryConfig` with `get_retry_429_policy()` / `Retry429Policy` respectively. The wrapper module (`src/agents/llm_retry.py`) will be updated in Task 7 along with its rewrite — leave it alone for now (Step 7 of this task fixes the only remaining broken import).

- [ ] **Step 6: Update `src/agents/llm_retry.py`'s import (minimal patch only)**

Edit `src/agents/llm_retry.py`. Find the import block near the top:

```python
from config.llm_retry import RetryConfig, get_retry_config
```

Replace with:

```python
from config.retry_429 import Retry429Policy as RetryConfig, get_retry_429_policy as get_retry_config
```

This keeps the wrapper's existing code working unchanged (still calls things `RetryConfig` / `get_retry_config` internally) until Task 7 rewrites it. This is a transitional alias **scoped to a single file** and removed in Task 7 — not a public-API shim.

- [ ] **Step 7: Update `config/README.md`**

Find the table row that documents `llm_retry.json`. Replace the row's `File` and `Purpose` columns with:

```
| `retry_429.json` | Backoff + retry policy for Vertex AI HTTP 429 (RESOURCE_EXHAUSTED) responses. Per-agent timeout/schema retry counts live in `analysts.json` / `strategist.json`. | `src/config/retry_429.py` (`get_retry_429_policy()`) |
```

- [ ] **Step 8: Run the existing test suite to confirm the rename didn't break anything**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -40
```

Expected: every test that was previously passing still passes. The wrapper file's transitional alias from Step 6 keeps existing wrapper tests green.

- [ ] **Step 9: Run ruff**

```bash
.venv/bin/python -m ruff check src/
```

Expected: clean (no new warnings).

- [ ] **Step 10: Commit**

```bash
git add config/retry_429.json src/config/retry_429.py src/contract/evidence.py src/agents/llm_retry.py config/README.md
# git rm of the moved-from files is implicit in the git mv — confirm staging:
git status --short
git commit -m "$(cat <<'EOF'
refactor(config): rename llm_retry config to retry_429

Carries only the 429 policy. Timeout and schema retry counts move
per-agent into analysts.json / strategist.json in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `LlmCaps` to analyst config + extend `config/analysts.json`

**Files:**
- Modify: `config/analysts.json`
- Modify: `src/config/analysts.py`
- Test: `tests/unit/config/test_analysts_config.py`

- [ ] **Step 1: Write the failing test for the new `LlmCaps` block on `news` and `fundamental`**

Append to `tests/unit/config/test_analysts_config.py`:

```python
def test_load_analysts_config_exposes_news_llm_caps(tmp_path) -> None:
    """The loaded config exposes `news.llm.{timeout_seconds, max_output_tokens, timeout_retries, schema_retries}`."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    cfg = load_analysts_config(path=cfg_path)

    assert cfg.news.llm.timeout_seconds   == 60
    assert cfg.news.llm.max_output_tokens == 2000
    assert cfg.news.llm.timeout_retries   == 3
    assert cfg.news.llm.schema_retries    == 3

    assert cfg.fundamental.llm.timeout_seconds   == 60
    assert cfg.fundamental.llm.max_output_tokens == 2000
    assert cfg.fundamental.llm.timeout_retries   == 3
    assert cfg.fundamental.llm.schema_retries    == 3


def test_load_analysts_config_rejects_zero_timeout_seconds(tmp_path) -> None:
    """`timeout_seconds <= 0` raises at load time, not at first use."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   0,                       # invalid
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_path)


def test_load_analysts_config_rejects_tiny_max_output_tokens(tmp_path) -> None:
    """`max_output_tokens < 256` raises at load time."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 100,                     # below ge=256 floor
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_path)
```

Make sure the imports at the top of the file include `json`, `pytest`, `pydantic.ValidationError`, and `load_analysts_config`. Add any that are missing.

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v 2>&1 | tail -20
```

Expected: the three new tests fail with `AttributeError: 'NewsCaps' object has no attribute 'llm'` (or similar). Existing tests still pass.

- [ ] **Step 3: Add the `LlmCaps` Pydantic model and attach to `NewsCaps` / `FundamentalCaps`**

Edit `src/config/analysts.py`. Above the `NewsCaps` class, add:

```python
class LlmCaps(BaseModel):
    """Per-LLM-agent runtime caps used by the retry wrapper.

    Each LLM-calling agent (each analyst, the strategist) carries its own
    instance of this block in its config file.  The wrapper reads
    ``timeout_seconds`` to bound each call's wall-clock time, the LlmAgent
    receives ``max_output_tokens`` via ``GenerateContentConfig`` to bound
    output length, and the wrapper composes per-class retry budgets from
    ``timeout_retries`` and ``schema_retries``.

    The project-wide HTTP 429 policy is **not** here — it lives in
    ``config/retry_429.json`` because it is identical across agents.

    Attributes
    ----------
    timeout_seconds:
        Per-call wall-clock timeout in seconds.  Enforced via
        ``asyncio.wait_for(...)`` inside ``RetryingAgentWrapper``.  Range
        ``(0, 600]``.
    max_output_tokens:
        Cap on the model's generated output tokens.  Set on every call
        (not just retries) so output loops cannot wedge the tick in the
        first place.  Range ``[256, 32768]``.
    timeout_retries:
        Total attempts the wrapper makes when wall-clock timeouts fire.
        ``3`` means one initial try plus up to two retries.  Range
        ``[1, 10]``.
    schema_retries:
        Total attempts the wrapper makes when ``pydantic.ValidationError``
        fires (output_schema parse failed).  Same shape as
        ``timeout_retries``.
    """

    timeout_seconds:   float = Field(gt=0.0, le=600.0)
    max_output_tokens: int   = Field(ge=256, le=32_768)
    timeout_retries:   int   = Field(ge=1, le=10)
    schema_retries:    int   = Field(ge=1, le=10)
```

Then extend `NewsCaps` and `FundamentalCaps`:

```python
class NewsCaps(BaseModel):
    """Truncation caps for the News analyst's LLM context."""

    max_articles_per_ticker: int = Field(ge=1, le=200)
    max_summary_chars:       int = Field(ge=1, le=10_000)
    llm:                     LlmCaps                       # NEW


class FundamentalCaps(BaseModel):
    """Truncation caps for the Fundamental analyst's LLM context."""

    max_filing_mda_chars:       int = Field(ge=1, le=20_000)
    max_filing_risk_chars:      int = Field(ge=1, le=20_000)
    max_insider_footnotes:      int = Field(ge=0, le=50)
    max_insider_footnote_chars: int = Field(ge=1, le=5_000)
    llm:                        LlmCaps                    # NEW
```

- [ ] **Step 4: Extend `config/analysts.json` with the new `llm` blocks**

Edit `config/analysts.json` — add `llm` blocks inside `news` and `fundamental`:

```json
{
  "slack_percent": 15,
  "news": {
    "max_articles_per_ticker": 25,
    "max_summary_chars":       1500,
    "llm": {
      "timeout_seconds":   60,
      "max_output_tokens": 2000,
      "timeout_retries":   3,
      "schema_retries":    3
    }
  },
  "fundamental": {
    "max_filing_mda_chars":       1500,
    "max_filing_risk_chars":      1500,
    "max_insider_footnotes":      5,
    "max_insider_footnote_chars": 400,
    "llm": {
      "timeout_seconds":   60,
      "max_output_tokens": 2000,
      "timeout_retries":   3,
      "schema_retries":    3
    }
  },
  "output_caps": {
    "verdict_rationale_max_chars":             200,
    "verdict_rationale_prompt_headroom_chars":  50,
    "report_summary_max_chars":     1000,
    "report_driver_name_max_chars":   60,
    "report_driver_body_max_chars": 500
  },
  "cache": {
    "enabled":   true,
    "directory": "cache/reports"
  }
}
```

- [ ] **Step 5: Run the tests to verify they now pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v 2>&1 | tail -20
```

Expected: every test passes, including the three new ones.

- [ ] **Step 6: Run the full suite to catch any incidental breakage**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: no new failures. If a test fails because it loads `analysts.json` directly and doesn't supply the new `llm` block, fix it in this task by updating the test fixture.

- [ ] **Step 7: Update `config/README.md`**

Find the `analysts.json` section. Under the existing settings table, add an `llm` subsection (or extend the existing table) documenting each new field. Use the same row format as the existing entries. Example rows:

```
| `news.llm.timeout_seconds` | float | Wall-clock timeout (seconds) for one News-analyst LLM call. Range `(0, 600]`. Default 60. |
| `news.llm.max_output_tokens` | int | Cap on output tokens per call. Range `[256, 32768]`. Default 2000. |
| `news.llm.timeout_retries` | int | Total attempts on timeout (1 initial try + retries). Range `[1, 10]`. Default 3. |
| `news.llm.schema_retries` | int | Total attempts on `pydantic.ValidationError`. Range `[1, 10]`. Default 3. |
```

Add the same four rows for `fundamental.llm.*`.

- [ ] **Step 8: Commit**

```bash
git add config/analysts.json src/config/analysts.py tests/unit/config/test_analysts_config.py config/README.md
git commit -m "$(cat <<'EOF'
feat(config): per-analyst LLM timeout/token/retry caps

Adds an `llm` block to news and fundamental analysts in analysts.json
covering timeout_seconds, max_output_tokens, timeout_retries, and
schema_retries. Loader validates each field at module load.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `LlmCaps` to strategist config + extend `config/strategist.json`

**Files:**
- Modify: `config/strategist.json`
- Modify: `src/config/strategist.py`
- Create: `tests/unit/config/test_strategist_config.py`

- [ ] **Step 1: Write the failing test for `strategist.llm`**

Create `tests/unit/config/test_strategist_config.py`:

```python
"""Unit tests for ``src/config/strategist.py`` — Pydantic-validated loader
for ``config/strategist.json``.

Focus of this file: the new ``llm`` block carrying the per-strategist
timeout / max-tokens / retry budgets.  Other strategist config (char caps,
slack) is covered by existing call-site tests.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from config.strategist import load_strategist_config


def _valid_strategist_json() -> dict:
    """Return a minimum-valid strategist.json payload as a dict.

    Used as the starting point for both happy-path and bad-value tests
    so each test only highlights the specific field it perturbs.
    """

    return {
        "slack_percent": 15,
        "decision_caps": {
            "reasoning_max_chars":      1000,
            "updated_thesis_max_chars":  800,
        },
        "stance_caps": {
            "rationale_max_chars":    250,
            "catalyst_max_chars":     120,
            "close_reason_max_chars": 120,
            "trim_reason_max_chars":  120,
        },
        "position_thesis_caps": {
            "rationale_max_chars":          400,
            "catalyst_max_chars":           100,
            "last_review_note_max_chars":   200,
        },
        "llm": {
            "timeout_seconds":   180,
            "max_output_tokens": 8000,
            "timeout_retries":   3,
            "schema_retries":    3,
        },
    }


def test_load_strategist_config_exposes_llm_caps(tmp_path) -> None:
    """The loaded config exposes `strategist.llm.{...}` with correct values."""

    p = tmp_path / "strategist.json"
    p.write_text(json.dumps(_valid_strategist_json()))

    cfg = load_strategist_config(path=p)

    assert cfg.llm.timeout_seconds   == 180
    assert cfg.llm.max_output_tokens == 8000
    assert cfg.llm.timeout_retries   == 3
    assert cfg.llm.schema_retries    == 3


def test_load_strategist_config_rejects_zero_timeout(tmp_path) -> None:
    """`timeout_seconds <= 0` raises at load time."""

    payload = _valid_strategist_json()
    payload["llm"]["timeout_seconds"] = 0

    p = tmp_path / "strategist.json"
    p.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_strategist_config(path=p)


def test_load_strategist_config_rejects_tiny_max_output_tokens(tmp_path) -> None:
    """`max_output_tokens < 256` raises at load time."""

    payload = _valid_strategist_json()
    payload["llm"]["max_output_tokens"] = 100

    p = tmp_path / "strategist.json"
    p.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_strategist_config(path=p)


def test_load_strategist_config_rejects_zero_retries(tmp_path) -> None:
    """`timeout_retries < 1` and `schema_retries < 1` both raise."""

    payload_timeout = _valid_strategist_json()
    payload_timeout["llm"]["timeout_retries"] = 0
    p1 = tmp_path / "strategist1.json"
    p1.write_text(json.dumps(payload_timeout))
    with pytest.raises(ValidationError):
        load_strategist_config(path=p1)

    payload_schema = _valid_strategist_json()
    payload_schema["llm"]["schema_retries"] = 0
    p2 = tmp_path / "strategist2.json"
    p2.write_text(json.dumps(payload_schema))
    with pytest.raises(ValidationError):
        load_strategist_config(path=p2)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_strategist_config.py -v 2>&1 | tail -20
```

Expected: failures pointing at a missing `llm` field on `StrategistConfig`, or "extra field not permitted".

- [ ] **Step 3: Import `LlmCaps` and attach to `StrategistConfig`**

Edit `src/config/strategist.py`:

1. Add the import near the top, alongside the existing Pydantic imports:

   ```python
   from config.analysts import LlmCaps
   ```

   (Both config files share the same `LlmCaps` shape — importing keeps the schema canonical and avoids drift.)

2. Find the `StrategistConfig` class. Add the `llm` field, with the existing docstring style:

   ```python
   class StrategistConfig(BaseModel):
       """Top-level strategist configuration."""

       slack_percent:        int                = Field(ge=0, le=50)
       decision_caps:        DecisionCaps
       stance_caps:          StanceCaps
       position_thesis_caps: PositionThesisCaps
       llm:                  LlmCaps                                # NEW
   ```

   If the current class shape differs from the above (different field order, extra fields), preserve those and add `llm` as the new field — do not reorder.

- [ ] **Step 4: Extend `config/strategist.json` with the new `llm` block**

Edit `config/strategist.json`:

```json
{
  "slack_percent": 15,
  "decision_caps": {
    "reasoning_max_chars":      1000,
    "updated_thesis_max_chars":  800
  },
  "stance_caps": {
    "rationale_max_chars":    250,
    "catalyst_max_chars":     120,
    "close_reason_max_chars": 120,
    "trim_reason_max_chars":  120
  },
  "position_thesis_caps": {
    "rationale_max_chars":         400,
    "catalyst_max_chars":          100,
    "last_review_note_max_chars":  200
  },
  "llm": {
    "timeout_seconds":   180,
    "max_output_tokens": 8000,
    "timeout_retries":   3,
    "schema_retries":    3
  }
}
```

- [ ] **Step 5: Run the new test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_strategist_config.py -v 2>&1 | tail -20
```

Expected: all four tests pass.

- [ ] **Step 6: Run the full suite to catch any incidental breakage**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: no new failures. If a test fixture loads `strategist.json` directly without the new `llm` block, update that fixture in this task.

- [ ] **Step 7: Update `config/README.md`**

Find the `strategist.json` section. Add four rows (same format as Task 2 Step 7):

```
| `llm.timeout_seconds` | float | Wall-clock timeout (seconds) for the strategist LLM call. Range `(0, 600]`. Default 180. |
| `llm.max_output_tokens` | int | Cap on output tokens per strategist call. Range `[256, 32768]`. Default 8000. |
| `llm.timeout_retries` | int | Total attempts on timeout (1 initial try + retries). Range `[1, 10]`. Default 3. |
| `llm.schema_retries` | int | Total attempts on `pydantic.ValidationError`. Range `[1, 10]`. Default 3. |
```

- [ ] **Step 8: Commit**

```bash
git add config/strategist.json src/config/strategist.py tests/unit/config/test_strategist_config.py config/README.md
git commit -m "$(cat <<'EOF'
feat(config): strategist LLM timeout/token/retry caps

Adds an `llm` block to strategist.json mirroring the per-analyst shape
introduced in the previous commit — same fields, same validation, but
with larger defaults (180s timeout, 8000 tokens) suited to the
strategist's full-watchlist stance output.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `_classify(exc)` and per-class predicate helpers to `src/agents/llm_retry.py`

**Files:**
- Modify: `src/agents/llm_retry.py`
- Modify: `tests/unit/agents/test_llm_retry.py` (add classifier tests at the file end)

This task introduces only the classification machinery — the wrapper internals are still the existing tenacity-based code. We extend the wrapper's logic in Task 7.

- [ ] **Step 1: Write the failing tests for the classifier**

Append to `tests/unit/agents/test_llm_retry.py` (after the existing tests):

```python
# ---------------------------------------------------------------------------
# Tests for the per-class predicate helpers and the top-level _classify dispatcher
# ---------------------------------------------------------------------------

import asyncio
from pydantic import BaseModel as _BM, ValidationError as _VE

from agents.llm_retry import _classify, _is_rate_limit, _is_timeout, _is_schema_error


class _Tiny(_BM):
    """Trivial Pydantic model used to construct a real ValidationError."""

    name: str


def _make_validation_error() -> _VE:
    """Produce a real ``pydantic.ValidationError`` by failing a model parse."""

    try:
        _Tiny.model_validate({"name": 123})           # 123 is not a string
    except _VE as ve:
        return ve

    raise AssertionError("Pydantic accepted invalid payload — test premise broken.")


def test_is_rate_limit_recognises_429_client_error() -> None:
    """A google.genai ClientError with status_code 429 classifies as rate_limit."""

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )

    assert _is_rate_limit(err) is True
    assert _classify(err)      == "rate_limit"


def test_is_rate_limit_walks_cause_chain() -> None:
    """A 429 wrapped via `raise X from Y` still classifies as rate_limit."""

    inner = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )
    try:
        try:
            raise inner
        except ClientError as ce:
            raise RuntimeError("wrapped") from ce
    except RuntimeError as outer:
        assert _is_rate_limit(outer) is True
        assert _classify(outer)      == "rate_limit"


def test_is_timeout_recognises_asyncio_timeout() -> None:
    """asyncio.TimeoutError / TimeoutError classify as timeout."""

    assert _is_timeout(asyncio.TimeoutError()) is True
    assert _is_timeout(TimeoutError())          is True
    assert _classify(asyncio.TimeoutError())    == "timeout"
    assert _classify(TimeoutError())            == "timeout"


def test_is_schema_error_recognises_pydantic_validation_error() -> None:
    """A real ``pydantic.ValidationError`` classifies as schema."""

    ve = _make_validation_error()

    assert _is_schema_error(ve) is True
    assert _classify(ve)        == "schema"


def test_is_schema_error_walks_cause_chain() -> None:
    """A wrapped ValidationError still classifies as schema."""

    ve = _make_validation_error()
    try:
        try:
            raise ve
        except _VE as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert _is_schema_error(outer) is True
        assert _classify(outer)        == "schema"


def test_classify_returns_none_for_unhandled() -> None:
    """A vanilla ValueError is not retryable — _classify returns None."""

    assert _classify(ValueError("nope")) is None


def test_classify_returns_none_for_strategist_contract_violation() -> None:
    """StrategistContractViolation is NOT classified — it is a contract bug
    that retry will not fix."""

    from agents.risk_gate.lifecycle import StrategistContractViolation

    assert _classify(StrategistContractViolation("off-watchlist")) is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v -k "rate_limit or timeout or schema or classify" 2>&1 | tail -30
```

Expected: ImportError or AttributeError on `_classify` / `_is_rate_limit` / `_is_timeout` / `_is_schema_error`.

- [ ] **Step 3: Add the classifier helpers to `src/agents/llm_retry.py`**

Edit `src/agents/llm_retry.py`. **Above** the existing `_is_resource_exhausted` function, add a new `_is_rate_limit`:

```python
def _is_rate_limit(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or any link in its cause chain) is a
    Vertex AI HTTP 429 / RESOURCE_EXHAUSTED response.

    This is the rate-limit predicate used by :func:`_classify`.  The body
    is the same as the legacy ``_is_resource_exhausted`` — kept identical
    so existing behaviour is preserved verbatim.

    Two detection layers (matching the legacy function):

    * ADK's :class:`google.adk.models.google_llm._ResourceExhaustedError`
      — defensive import so a future rename does not silently break us.
    * The underlying :class:`google.genai.errors.ClientError` with
      ``status_code == 429`` — caught directly and via ``__cause__``.

    Parameters
    ----------
    exc:
        The exception to classify.

    Returns
    -------
    bool
        ``True`` if this exception (or anything in its cause chain) is
        a Vertex 429; ``False`` otherwise.
    """

    # Layer 1 — ADK's wrapper class.  Defensive import.
    try:
        from google.adk.models.google_llm import _ResourceExhaustedError

        if isinstance(exc, _ResourceExhaustedError):
            return True

    except ImportError:
        pass

    # Layer 2 — the underlying SDK error.
    try:
        from google.genai.errors import ClientError

        if isinstance(exc, ClientError) and getattr(exc, "status_code", None) == 429:
            return True

    except ImportError:
        pass

    # Walk the __cause__ chain.  Stop on self-loops (defensive).
    cause = exc.__cause__

    if cause is not None and cause is not exc:
        return _is_rate_limit(cause)

    return False
```

Below it (or anywhere in the module above `RetryingAgentWrapper`), add the new predicates and dispatcher:

```python
def _is_timeout(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` is a wall-clock timeout the wrapper should retry.

    ``asyncio.TimeoutError`` is an alias for the built-in ``TimeoutError``
    from Python 3.11 onwards — checking the built-in covers both.  We do
    NOT classify network-layer ``httpx.TimeoutException`` here: those
    would only fire if Vertex itself raised an HTTP-layer timeout
    (rare, and a real infra error that retry will not fix).

    Parameters
    ----------
    exc:
        The exception to classify.

    Returns
    -------
    bool
        ``True`` if ``exc`` is a ``TimeoutError`` (or alias); ``False``
        otherwise.
    """

    return isinstance(exc, TimeoutError)


def _is_schema_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or its cause chain) is a Pydantic
    ``ValidationError`` from the LLM output_schema parse.

    Walks ``__cause__`` so a ValidationError wrapped via
    ``raise SomethingElse from ve`` still classifies as a schema error.
    ``StrategistContractViolation`` is deliberately *not* classified — it
    is raised by the strategist's validation callback *after* the
    schema parse already succeeded, and is a systemic contract bug that
    retry will not fix.

    Parameters
    ----------
    exc:
        The exception to classify.

    Returns
    -------
    bool
        ``True`` if a ``pydantic.ValidationError`` appears anywhere in
        the cause chain.
    """

    # Defensive import — Pydantic is a hard project dependency, but we
    # mirror the import-guard style used by _is_rate_limit so the module
    # is uniformly robust.
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            return True

    except ImportError:
        return False

    cause = exc.__cause__

    if cause is not None and cause is not exc:
        return _is_schema_error(cause)

    return False


def _classify(exc: BaseException) -> str | None:
    """Top-level retry classifier — dispatches to the per-class predicates.

    Returns one of ``"rate_limit"``, ``"timeout"``, ``"schema"``, or
    ``None`` (not retryable).  Order matters when two predicates could
    in principle match the same exception — none currently overlap, but
    the order encodes priority should that ever change: rate-limit first
    (most common transient), then timeout, then schema.

    Parameters
    ----------
    exc:
        The exception raised by the inner agent.

    Returns
    -------
    str | None
        Class name to look up in the policy dict, or ``None`` if the
        wrapper should re-raise immediately.
    """

    if _is_rate_limit(exc):
        return "rate_limit"

    if _is_timeout(exc):
        return "timeout"

    if _is_schema_error(exc):
        return "schema"

    return None
```

Do **not** delete the existing `_is_resource_exhausted` function in this task — Task 7 removes it. The new code coexists alongside until then.

- [ ] **Step 4: Run the classifier tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v -k "rate_limit or timeout or schema or classify" 2>&1 | tail -30
```

Expected: every new test passes.

- [ ] **Step 5: Run the full suite to confirm no regression**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: no new failures. Existing wrapper tests (`test_succeeds_first_try_forwards_all_events`, `test_retries_on_429_then_succeeds`, etc.) still pass because the wrapper code is untouched in this task.

- [ ] **Step 6: Run ruff**

```bash
.venv/bin/python -m ruff check src/agents/llm_retry.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/agents/llm_retry.py tests/unit/agents/test_llm_retry.py
git commit -m "$(cat <<'EOF'
feat(retry): add three-class exception classifier

Adds _is_rate_limit (parallel to existing _is_resource_exhausted),
_is_timeout, _is_schema_error, and the _classify dispatcher. Wrapper
internals still use the legacy 429-only path; this commit only
introduces the predicates the next commit will consume.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add `RetryPolicy`, sleep/backoff helpers, and `_merge_increment` to `src/agents/llm_retry.py`

**Files:**
- Modify: `src/agents/llm_retry.py`
- Modify: `tests/unit/agents/test_llm_retry.py`

- [ ] **Step 1: Write the failing tests for `RetryPolicy`, `_compute_exp_jitter`, `_sleep_per_policy`, `_merge_increment`**

Append to `tests/unit/agents/test_llm_retry.py`:

```python
# ---------------------------------------------------------------------------
# Tests for RetryPolicy, _compute_exp_jitter, _sleep_per_policy, _merge_increment
# ---------------------------------------------------------------------------

from agents.llm_retry import (
    RetryPolicy,
    _compute_exp_jitter,
    _sleep_per_policy,
    _merge_increment,
)


def test_retry_policy_immediate_rejects_delay_fields() -> None:
    """An ``immediate`` policy ignores base/max delay (both default to 0)."""

    p = RetryPolicy(max_attempts=3, backoff="immediate")

    assert p.max_attempts       == 3
    assert p.backoff            == "immediate"
    assert p.base_delay_seconds == 0.0
    assert p.max_delay_seconds  == 0.0


def test_retry_policy_exp_jitter_requires_positive_delays() -> None:
    """An ``exp_jitter`` policy stores positive base/max delay values."""

    p = RetryPolicy(
        max_attempts       = 5,
        backoff            = "exp_jitter",
        base_delay_seconds = 2.0,
        max_delay_seconds  = 30.0,
    )

    assert p.base_delay_seconds == 2.0
    assert p.max_delay_seconds  == 30.0


def test_compute_exp_jitter_grows_with_attempt_number() -> None:
    """Each successive attempt's delay grows, capped at max."""

    delays = [
        _compute_exp_jitter(attempt_n=n, base=2.0, max_=30.0)
        for n in range(1, 6)
    ]

    # Monotonic non-decreasing (jitter introduces variance but never below base).
    assert all(d >= 2.0  for d in delays)
    assert all(d <= 30.0 for d in delays)
    # The last attempts should saturate near max (with some jitter slack).
    assert delays[-1] >= 10.0


@pytest.mark.asyncio
async def test_sleep_per_policy_immediate_does_not_sleep(monkeypatch) -> None:
    """An ``immediate`` policy passes 0 to asyncio.sleep (or skips it)."""

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    p = RetryPolicy(max_attempts=3, backoff="immediate")
    await _sleep_per_policy(p, attempt_n=1)

    # Either the helper skipped asyncio.sleep entirely, or it passed 0.
    assert sleeps == [] or sleeps == [0.0]


@pytest.mark.asyncio
async def test_sleep_per_policy_exp_jitter_sleeps_within_bounds(monkeypatch) -> None:
    """An ``exp_jitter`` policy sleeps for a value within [base, max]."""

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    p = RetryPolicy(
        max_attempts       = 5,
        backoff            = "exp_jitter",
        base_delay_seconds = 2.0,
        max_delay_seconds  = 30.0,
    )
    await _sleep_per_policy(p, attempt_n=1)

    assert len(sleeps) == 1
    assert 2.0 <= sleeps[0] <= 30.0


def test_merge_increment_returns_new_dict() -> None:
    """``_merge_increment`` is pure — does not mutate the input."""

    current = {"rate_limit": 1}
    out     = _merge_increment(current, "timeout")

    assert current == {"rate_limit": 1}                   # input untouched
    assert out     == {"rate_limit": 1, "timeout": 1}


def test_merge_increment_increments_existing_key() -> None:
    """An already-present class increments by 1."""

    current = {"schema": 2}
    out     = _merge_increment(current, "schema")

    assert out == {"schema": 3}
```

- [ ] **Step 2: Run to verify the tests fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v -k "retry_policy or exp_jitter or sleep_per_policy or merge_increment" 2>&1 | tail -30
```

Expected: ImportError on `RetryPolicy`, `_compute_exp_jitter`, etc.

- [ ] **Step 3: Add the helpers to `src/agents/llm_retry.py`**

Edit `src/agents/llm_retry.py`. At the top, add the necessary imports:

```python
import random
from typing import Literal
```

Add the `RetryPolicy` model and helpers (place above `RetryingAgentWrapper`):

```python
class RetryPolicy(BaseModel):
    """Per-class retry policy used by :class:`RetryingAgentWrapper`.

    The wrapper holds a dict of policies keyed by class name
    (``"rate_limit"`` / ``"timeout"`` / ``"schema"``).  Each class has
    its own ``max_attempts`` budget and its own backoff schedule.

    Attributes
    ----------
    max_attempts:
        Total number of attempts for this class — one initial try plus
        retries.  ``3`` means "one try plus up to two retries".  Must be
        ``>= 1``.
    backoff:
        Either ``"immediate"`` (no sleep between retries — used for
        model-misbehaviour classes like timeout and schema) or
        ``"exp_jitter"`` (used for transient quota classes — currently
        only ``rate_limit``).
    base_delay_seconds:
        Lower bound on the per-retry sleep when ``backoff ==
        "exp_jitter"``.  Ignored otherwise.
    max_delay_seconds:
        Upper bound on the per-retry sleep when ``backoff ==
        "exp_jitter"``.  Ignored otherwise.
    """

    max_attempts:       int                              = Field(ge=1, le=20)
    backoff:            Literal["immediate", "exp_jitter"]
    base_delay_seconds: float = Field(default=0.0, ge=0.0)
    max_delay_seconds:  float = Field(default=0.0, ge=0.0)


def _compute_exp_jitter(*, attempt_n: int, base: float, max_: float) -> float:
    """Return an exponential-with-jitter delay in seconds for the n-th retry.

    Mirrors tenacity's ``wait_exponential_jitter`` behaviour without the
    dependency: delay = min(max_, base * 2^(attempt_n - 1)) + random
    jitter in [0, base).  Saturates at ``max_`` once exponential growth
    exceeds it.

    Parameters
    ----------
    attempt_n:
        1-based count of attempts already consumed for this class
        (i.e. the first retry passes ``attempt_n=1``).
    base:
        Lower-bound delay seed in seconds.
    max_:
        Upper-bound cap in seconds.

    Returns
    -------
    float
        Delay in seconds, in the range ``[base, max_]``.
    """

    # Exponential growth from the base, capped at max_.  attempt_n is
    # 1-based so the first retry sleeps near base; the second near 2*base; etc.
    grown   = min(max_, base * (2 ** max(0, attempt_n - 1)))

    # Add jitter in [0, base) so simultaneous wrappers don't lock-step.
    jitter  = random.uniform(0, base)

    # Final clamp — jitter could push above max_ if max_ is close to grown.
    return min(max_, grown + jitter)


async def _sleep_per_policy(policy: RetryPolicy, *, attempt_n: int) -> None:
    """Sleep between retries according to ``policy.backoff``.

    For ``"immediate"`` policies this is a no-op (returns immediately
    without calling ``asyncio.sleep``) — used for timeout and schema
    classes where backing off does not help.  For ``"exp_jitter"`` it
    sleeps for the value returned by :func:`_compute_exp_jitter`.

    Parameters
    ----------
    policy:
        The per-class policy.
    attempt_n:
        1-based count of attempts already consumed for this class
        (passed through to ``_compute_exp_jitter``).
    """

    if policy.backoff == "immediate":
        return

    delay = _compute_exp_jitter(
        attempt_n = attempt_n,
        base      = policy.base_delay_seconds,
        max_      = policy.max_delay_seconds,
    )
    await asyncio.sleep(delay)


def _merge_increment(current: dict, cls: str) -> dict:
    """Return a new dict equal to ``current`` with ``current[cls]`` += 1.

    Pure function — does not mutate ``current``.  Used by the retry
    wrapper to build the ``state_delta`` payload for the per-tick
    retry-counter accumulator.

    Parameters
    ----------
    current:
        Current accumulator dict (may be empty / may lack ``cls``).
    cls:
        Retry-class name to increment (``"rate_limit"``, ``"timeout"``,
        ``"schema"``).

    Returns
    -------
    dict
        New dict equal to ``current`` with ``cls`` incremented by 1.
    """

    out      = dict(current)
    out[cls] = out.get(cls, 0) + 1
    return out
```

Also add `asyncio` to the imports at the top of the file if it's not already there.

- [ ] **Step 4: Run the helper tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v -k "retry_policy or exp_jitter or sleep_per_policy or merge_increment" 2>&1 | tail -30
```

Expected: every helper test passes.

- [ ] **Step 5: Run the full suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: no regressions.

- [ ] **Step 6: Run ruff**

```bash
.venv/bin/python -m ruff check src/agents/llm_retry.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/agents/llm_retry.py tests/unit/agents/test_llm_retry.py
git commit -m "$(cat <<'EOF'
feat(retry): per-class RetryPolicy + exp-jitter / merge helpers

Adds RetryPolicy, _compute_exp_jitter, _sleep_per_policy, and pure
_merge_increment helpers consumed by the rewritten wrapper in the next
commit. No behavioural change yet — the wrapper still runs the legacy
tenacity path until the rewrite lands.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add `build_retry_policies` factory helper

**Files:**
- Modify: `src/agents/llm_retry.py`
- Modify: `tests/unit/agents/test_llm_retry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agents/test_llm_retry.py`:

```python
# ---------------------------------------------------------------------------
# Tests for build_retry_policies — composes the per-agent policy dict
# from the per-agent retry counts plus the project-wide 429 policy.
# ---------------------------------------------------------------------------

from agents.llm_retry import build_retry_policies


def test_build_retry_policies_composes_three_classes(monkeypatch) -> None:
    """The returned dict has exactly three classes with correct shapes."""

    # Stub the 429 policy loader so the test is hermetic.
    from config import retry_429 as cfg_mod

    monkeypatch.setattr(
        cfg_mod,
        "get_retry_429_policy",
        lambda: cfg_mod.Retry429Policy(
            max_attempts       = 5,
            base_delay_seconds = 2.0,
            max_delay_seconds  = 30.0,
        ),
    )

    policies = build_retry_policies(timeout_retries=3, schema_retries=3)

    assert set(policies.keys()) == {"rate_limit", "timeout", "schema"}

    assert policies["rate_limit"].max_attempts       == 5
    assert policies["rate_limit"].backoff            == "exp_jitter"
    assert policies["rate_limit"].base_delay_seconds == 2.0
    assert policies["rate_limit"].max_delay_seconds  == 30.0

    assert policies["timeout"].max_attempts == 3
    assert policies["timeout"].backoff      == "immediate"

    assert policies["schema"].max_attempts == 3
    assert policies["schema"].backoff      == "immediate"
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v -k "build_retry_policies" 2>&1 | tail -20
```

Expected: ImportError on `build_retry_policies`.

- [ ] **Step 3: Add the helper to `src/agents/llm_retry.py`**

Edit `src/agents/llm_retry.py`. Place this function above `RetryingAgentWrapper`:

```python
def build_retry_policies(
    *,
    timeout_retries: int,
    schema_retries:  int,
) -> dict[str, RetryPolicy]:
    """Compose the per-agent retry-policy dict for the wrapper.

    The 429 (``rate_limit``) policy is project-wide — loaded once from
    ``config/retry_429.json``.  The ``timeout`` and ``schema`` policies
    are per-agent, with their ``max_attempts`` supplied by the caller
    and their backoff hard-coded to ``"immediate"`` (no sleep — these
    are model-misbehaviour failures, not capacity issues).

    Parameters
    ----------
    timeout_retries:
        Total attempts the wrapper makes on wall-clock timeout
        (``asyncio.TimeoutError``).
    schema_retries:
        Total attempts the wrapper makes on
        ``pydantic.ValidationError`` from the LLM ``output_schema`` parse.

    Returns
    -------
    dict[str, RetryPolicy]
        Policies keyed by class name; passed to
        :class:`RetryingAgentWrapper`'s ``policies`` constructor arg.
    """

    # Resolve the project-wide 429 policy.  ``get_retry_429_policy()`` is
    # cached, so this is effectively free after the first call.
    from config.retry_429 import get_retry_429_policy

    cfg = get_retry_429_policy()

    return {
        "rate_limit": RetryPolicy(
            max_attempts       = cfg.max_attempts,
            backoff            = "exp_jitter",
            base_delay_seconds = cfg.base_delay_seconds,
            max_delay_seconds  = cfg.max_delay_seconds,
        ),
        "timeout":    RetryPolicy(max_attempts=timeout_retries, backoff="immediate"),
        "schema":     RetryPolicy(max_attempts=schema_retries,  backoff="immediate"),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v -k "build_retry_policies" 2>&1 | tail -20
```

Expected: pass.

- [ ] **Step 5: Run the full suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/agents/llm_retry.py tests/unit/agents/test_llm_retry.py
git commit -m "$(cat <<'EOF'
feat(retry): build_retry_policies composes per-agent policy dict

Reads the project-wide 429 policy from config/retry_429.json and
combines it with caller-supplied timeout/schema attempt counts. The
next commit wires factories to pass these into the wrapper.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Rewrite `RetryingAgentWrapper` internals

**Files:**
- Modify: `src/agents/llm_retry.py`
- Modify: `tests/unit/agents/test_llm_retry.py` (rewrite existing tests for the new ctor)

This is the largest task. The wrapper's constructor signature changes, its run loop is hand-rolled (no more tenacity), and `asyncio.wait_for` enforces the per-call timeout. The existing 429-only tests are rewritten to match the new constructor.

- [ ] **Step 1: Write the failing tests for the new wrapper behaviour**

Replace the **entire contents** of `tests/unit/agents/test_llm_retry.py` with the following. (Some of the classifier/helper tests added in Tasks 4–6 are re-listed here so this file is fully self-contained and the engineer doesn't need to merge fragments.)

```python
"""Unit tests for :class:`agents.llm_retry.RetryingAgentWrapper` and the
classification / sleep / merge helpers it relies on.

Covers (per the three-layer retry spec):

* Per-class budget independence (a timeout consumes only the timeout
  counter; not the 429 counter).
* asyncio.wait_for enforcement of the per-agent timeout.
* Event buffering — failed-attempt events are discarded; only the
  successful attempt's events flush.
* state_delta emission of the per-tick retry counter accumulator.
* StrategistContractViolation propagates immediately (not retried).
* Structured llm_retry_exhausted ERROR log on terminal exhaustion.
* Existing 429 happy-path / persistent / non-retryable behaviour
  preserved verbatim.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.errors import ClientError
from pydantic import BaseModel as _BM, ValidationError as _VE

from agents.llm_retry import (
    RetryingAgentWrapper,
    RetryPolicy,
    build_retry_policies,
    _classify,
    _compute_exp_jitter,
    _is_rate_limit,
    _is_schema_error,
    _is_timeout,
    _merge_increment,
    _sleep_per_policy,
)


# ---------------------------------------------------------------------------
# Shared fixtures and stubs
# ---------------------------------------------------------------------------


class _Tiny(_BM):
    """Trivial Pydantic model used to construct a real ValidationError."""

    name: str


def _make_validation_error() -> _VE:
    """Produce a real ``pydantic.ValidationError`` by failing a model parse."""

    try:
        _Tiny.model_validate({"name": 123})
    except _VE as ve:
        return ve

    raise AssertionError("Pydantic accepted invalid payload — test premise broken.")


def _fast_policies(
    *,
    rate_limit_attempts: int = 5,
    timeout_attempts:    int = 3,
    schema_attempts:     int = 3,
) -> dict[str, RetryPolicy]:
    """Build a policy dict with sub-second 429 backoff for fast tests."""

    return {
        "rate_limit": RetryPolicy(
            max_attempts       = rate_limit_attempts,
            backoff            = "exp_jitter",
            base_delay_seconds = 0.001,
            max_delay_seconds  = 0.005,
        ),
        "timeout":    RetryPolicy(max_attempts=timeout_attempts, backoff="immediate"),
        "schema":     RetryPolicy(max_attempts=schema_attempts,  backoff="immediate"),
    }


class _FakeInner:
    """Configurable fake of an ADK BaseAgent.

    Stores a script of per-attempt outcomes.  On each ``run_async`` call
    it advances the script: an outcome can be either an Exception
    (raised) or a list of Events (yielded).  Used by every wrapper test
    to simulate transient / persistent failures and successes.
    """

    def __init__(
        self,
        *,
        name:     str,
        script:   list[Any],          # each item: Exception | list[Event] | "sleep"
        sleep_s:  float | None = None,
    ) -> None:
        self.name        = name
        self._script     = list(script)
        self._sleep_s    = sleep_s
        self.call_count  = 0

    async def run_async(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Yield (or raise) per the next scripted outcome."""

        self.call_count += 1

        if not self._script:
            raise AssertionError(
                f"_FakeInner({self.name!r}) ran out of scripted outcomes "
                f"(call {self.call_count})"
            )

        outcome = self._script.pop(0)

        if isinstance(outcome, BaseException):
            raise outcome

        if outcome == "sleep":
            # Sleep longer than the wrapper's timeout so asyncio.wait_for fires.
            await asyncio.sleep(self._sleep_s)
            yield Event(author=self.name, content=None, actions=EventActions())
            return

        # Otherwise it's a list of Events to yield.
        for ev in outcome:
            yield ev


def _ctx_with_state() -> InvocationContext:
    """Return a MagicMock-ish ctx whose .session.state is a real dict.

    The wrapper writes to ``ctx.session.state.get(retry_state_key)`` and
    yields ``Event(state_delta=...)``.  Tests inspect the dict directly.
    """

    ctx = MagicMock(spec=InvocationContext)
    ctx.session.state = {}
    return ctx


# ---------------------------------------------------------------------------
# Classification predicates and dispatcher
# ---------------------------------------------------------------------------


def test_is_rate_limit_recognises_429_client_error() -> None:
    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )

    assert _is_rate_limit(err) is True
    assert _classify(err)      == "rate_limit"


def test_is_rate_limit_walks_cause_chain() -> None:
    inner = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )
    try:
        try:
            raise inner
        except ClientError as ce:
            raise RuntimeError("wrapped") from ce
    except RuntimeError as outer:
        assert _is_rate_limit(outer) is True
        assert _classify(outer)      == "rate_limit"


def test_is_timeout_recognises_asyncio_timeout() -> None:
    assert _is_timeout(asyncio.TimeoutError()) is True
    assert _is_timeout(TimeoutError())          is True
    assert _classify(asyncio.TimeoutError())    == "timeout"


def test_is_schema_error_recognises_pydantic_validation_error() -> None:
    ve = _make_validation_error()

    assert _is_schema_error(ve) is True
    assert _classify(ve)        == "schema"


def test_is_schema_error_walks_cause_chain() -> None:
    ve = _make_validation_error()
    try:
        try:
            raise ve
        except _VE as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert _is_schema_error(outer) is True
        assert _classify(outer)        == "schema"


def test_classify_returns_none_for_unhandled() -> None:
    assert _classify(ValueError("nope")) is None


def test_classify_returns_none_for_strategist_contract_violation() -> None:
    from agents.risk_gate.lifecycle import StrategistContractViolation

    assert _classify(StrategistContractViolation("off-watchlist")) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_retry_policy_immediate_defaults_delays_to_zero() -> None:
    p = RetryPolicy(max_attempts=3, backoff="immediate")
    assert p.base_delay_seconds == 0.0
    assert p.max_delay_seconds  == 0.0


def test_compute_exp_jitter_grows_with_attempt_number() -> None:
    delays = [
        _compute_exp_jitter(attempt_n=n, base=2.0, max_=30.0)
        for n in range(1, 6)
    ]
    assert all(d >= 2.0  for d in delays)
    assert all(d <= 30.0 for d in delays)
    assert delays[-1] >= 10.0


@pytest.mark.asyncio
async def test_sleep_per_policy_immediate_does_not_sleep(monkeypatch) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    p = RetryPolicy(max_attempts=3, backoff="immediate")
    await _sleep_per_policy(p, attempt_n=1)

    assert sleeps == [] or sleeps == [0.0]


@pytest.mark.asyncio
async def test_sleep_per_policy_exp_jitter_sleeps_within_bounds(monkeypatch) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    p = RetryPolicy(
        max_attempts       = 5,
        backoff            = "exp_jitter",
        base_delay_seconds = 2.0,
        max_delay_seconds  = 30.0,
    )
    await _sleep_per_policy(p, attempt_n=1)

    assert len(sleeps) == 1
    assert 2.0 <= sleeps[0] <= 30.0


def test_merge_increment_returns_new_dict_and_increments() -> None:
    current = {"rate_limit": 1}
    out     = _merge_increment(current, "timeout")
    assert current == {"rate_limit": 1}
    assert out     == {"rate_limit": 1, "timeout": 1}

    out2 = _merge_increment({"schema": 2}, "schema")
    assert out2 == {"schema": 3}


def test_build_retry_policies_composes_three_classes(monkeypatch) -> None:
    from config import retry_429 as cfg_mod

    monkeypatch.setattr(
        cfg_mod,
        "get_retry_429_policy",
        lambda: cfg_mod.Retry429Policy(
            max_attempts       = 5,
            base_delay_seconds = 2.0,
            max_delay_seconds  = 30.0,
        ),
    )

    policies = build_retry_policies(timeout_retries=3, schema_retries=3)
    assert set(policies.keys()) == {"rate_limit", "timeout", "schema"}
    assert policies["rate_limit"].max_attempts == 5
    assert policies["rate_limit"].backoff      == "exp_jitter"
    assert policies["timeout"].max_attempts    == 3
    assert policies["timeout"].backoff         == "immediate"
    assert policies["schema"].max_attempts     == 3
    assert policies["schema"].backoff          == "immediate"


# ---------------------------------------------------------------------------
# Wrapper happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_succeeds_first_try_forwards_all_events() -> None:
    """Inner succeeds on first call; every event is yielded in order."""

    ev1 = Event(author="X", content=None, actions=EventActions())
    ev2 = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(name="X", script=[[ev1, ev2]])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    ctx = _ctx_with_state()

    out: list[Event] = []
    async for ev in wrapper.run_async(ctx):
        out.append(ev)

    assert inner.call_count == 1
    assert out == [ev1, ev2]


# ---------------------------------------------------------------------------
# Per-class retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_retries_up_to_max_then_raises() -> None:
    """Six consecutive 429s exhaust max_attempts=5 and re-raise."""

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )

    inner = _FakeInner(name="X", script=[err] * 6)

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(rate_limit_attempts=5),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(ClientError):
        async for _ in wrapper.run_async(_ctx_with_state()):
            pass

    assert inner.call_count == 5


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds() -> None:
    """Two 429s followed by success yields only the success events."""

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )
    ev  = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(name="X", script=[err, err, [ev]])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    out: list[Event] = []
    async for e in wrapper.run_async(_ctx_with_state()):
        out.append(e)

    assert inner.call_count == 3
    # The two retry state_delta events come first; the success event last.
    assert out[-1] is ev


@pytest.mark.asyncio
async def test_timeout_retries_up_to_max_then_raises() -> None:
    """Four consecutive timeouts exhaust max_attempts=3 and re-raise TimeoutError."""

    inner = _FakeInner(
        name    = "X",
        script  = ["sleep"] * 4,
        sleep_s = 1.0,                                  # longer than wrapper timeout
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 0.05,                         # 50ms — easy to overshoot
        policies        = _fast_policies(timeout_attempts=3),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(TimeoutError):
        async for _ in wrapper.run_async(_ctx_with_state()):
            pass

    assert inner.call_count == 3


@pytest.mark.asyncio
async def test_schema_retries_up_to_max_then_raises() -> None:
    """Four ValidationErrors exhaust max_attempts=3 and re-raise."""

    inner = _FakeInner(
        name   = "X",
        script = [_make_validation_error() for _ in range(4)],
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(schema_attempts=3),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(_VE):
        async for _ in wrapper.run_async(_ctx_with_state()):
            pass

    assert inner.call_count == 3


@pytest.mark.asyncio
async def test_independent_budgets_per_class() -> None:
    """One 429 + one timeout + one schema + success — none of those budgets
    individually exhaust, so all four attempts run and the success yields."""

    err_429 = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )
    ev      = Event(author="X", content=None, actions=EventActions())

    # Script: 429 → timeout (sleep) → schema → success.
    inner = _FakeInner(
        name    = "X",
        script  = [err_429, "sleep", _make_validation_error(), [ev]],
        sleep_s = 1.0,
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 0.05,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    out: list[Event] = []
    async for e in wrapper.run_async(_ctx_with_state()):
        out.append(e)

    assert inner.call_count == 4
    assert out[-1] is ev


@pytest.mark.asyncio
async def test_unclassified_exception_propagates_immediately() -> None:
    """A ValueError is unclassified — wrapper raises on the first attempt."""

    inner = _FakeInner(name="X", script=[ValueError("boom")])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(ValueError, match="boom"):
        async for _ in wrapper.run_async(_ctx_with_state()):
            pass

    assert inner.call_count == 1


@pytest.mark.asyncio
async def test_strategist_contract_violation_not_retried() -> None:
    """StrategistContractViolation propagates immediately (no retry)."""

    from agents.risk_gate.lifecycle import StrategistContractViolation

    inner = _FakeInner(
        name   = "Strategist",
        script = [StrategistContractViolation("off-watchlist")],
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_strategist_retries",
    )

    with pytest.raises(StrategistContractViolation):
        async for _ in wrapper.run_async(_ctx_with_state()):
            pass

    assert inner.call_count == 1


# ---------------------------------------------------------------------------
# Event buffering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_buffer_discards_failed_attempt_events() -> None:
    """A failed attempt's yielded events do not reach the outer pipeline."""

    err = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )

    e_fail = Event(author="X", content=None, actions=EventActions())
    e_succ = Event(author="X", content=None, actions=EventActions())

    # Custom fake that yields one event THEN raises on the first call,
    # then yields a different event and succeeds on the second.
    class _PartialFail:
        name = "X"
        call_count = 0

        async def run_async(self, ctx):                # type: ignore[no-untyped-def]
            _PartialFail.call_count += 1

            if _PartialFail.call_count == 1:
                yield e_fail
                raise err

            yield e_succ

    wrapper = RetryingAgentWrapper(
        inner           = _PartialFail(),
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    out: list[Event] = []
    async for e in wrapper.run_async(_ctx_with_state()):
        out.append(e)

    # The failed-attempt event e_fail must NOT appear in the inner-event stream.
    # The success event e_succ must appear (after the retry's state_delta event).
    assert e_fail not in out
    assert e_succ in out


# ---------------------------------------------------------------------------
# Per-tick retry-counter telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_emits_state_delta_event_for_obs_counter() -> None:
    """After a retry, the wrapper has yielded a state_delta event with
    the retry-counter increment BEFORE the inner's success events."""

    err = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )
    ev  = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(name="X", script=[err, [ev]])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_news_retries",
    )

    ctx = _ctx_with_state()

    out: list[Event] = []
    async for e in wrapper.run_async(ctx):
        out.append(e)

    # The first event must be the state_delta increment for rate_limit;
    # the success event ev must come after.
    delta_evs = [
        e for e in out
        if e.actions is not None
        and e.actions.state_delta
        and "temp:_obs_news_retries" in (e.actions.state_delta or {})
    ]

    assert len(delta_evs) == 1
    delta = delta_evs[0].actions.state_delta["temp:_obs_news_retries"]
    assert delta == {"rate_limit": 1}

    # And the increment event comes before the success event in the stream.
    assert out.index(delta_evs[0]) < out.index(ev)


# ---------------------------------------------------------------------------
# Exhaustion log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhaustion_emits_structured_error_log(caplog) -> None:
    """On terminal exhaustion, exactly one llm_retry_exhausted ERROR row appears."""

    import logging

    caplog.set_level(logging.ERROR, logger="agents.llm_retry")

    err = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )

    inner = _FakeInner(name="X", script=[err] * 6)

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(rate_limit_attempts=5),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(ClientError):
        async for _ in wrapper.run_async(_ctx_with_state()):
            pass

    exhausted = [r for r in caplog.records if r.message == "llm_retry_exhausted"]
    assert len(exhausted) == 1
    rec = exhausted[0]
    assert rec.exhausted_class == "rate_limit"
    assert rec.attempts_used   == {"rate_limit": 5, "timeout": 0, "schema": 0}
```

- [ ] **Step 2: Run the new test file to confirm everything fails (or fails to import)**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v 2>&1 | tail -30
```

Expected: failures pointing at the new wrapper signature (`timeout_seconds`, `policies`, `retry_state_key` aren't accepted yet) and/or missing log behaviour.

- [ ] **Step 3: Rewrite `RetryingAgentWrapper` in `src/agents/llm_retry.py`**

Edit `src/agents/llm_retry.py`. Delete the existing class body (and the transitional alias import from Task 1 Step 6 — we now use the new symbols directly). Keep `_is_rate_limit`, `_is_timeout`, `_is_schema_error`, `_classify`, `RetryPolicy`, `build_retry_policies`, `_compute_exp_jitter`, `_sleep_per_policy`, `_merge_increment` from Tasks 4–6.

Also delete the now-unused `_is_resource_exhausted` (its callers were tests, and Task 4's tests already cover the rename).

Replace the rest of the file (everything from the legacy class onward) with:

```python
import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

_LOGGER = logging.getLogger(__name__)


def _log_retry(
    agent_name: str,
    cls:        str,
    exc:        BaseException,
    remaining:  dict[str, int],
) -> None:
    """Emit a structured WARNING just before sleep-and-retry.

    Carries the wrapped agent's name, the retry class, the exception
    type/message, and the per-class remaining counts.  One row per
    retry attempt — log analysis can grep on ``kind="llm_retry_attempt"``
    to see the full retry trail.

    Parameters
    ----------
    agent_name:
        Name of the inner agent (e.g. ``"NewsAnalyst_AAPL"``).
    cls:
        Retry class — one of ``"rate_limit"``, ``"timeout"``, ``"schema"``.
    exc:
        The exception that triggered the retry.
    remaining:
        Per-class remaining attempts at the moment of the retry.
    """

    _LOGGER.warning(
        "llm_retry_attempt",
        extra={
            "kind":               "llm_retry_attempt",
            "agent":              agent_name,
            "retry_class":        cls,
            "exc_type":           type(exc).__name__,
            "exc_message":        str(exc),
            "remaining_attempts": dict(remaining),
        },
    )


def _log_exhausted(
    agent_name: str,
    cls:        str,
    exc:        BaseException,
    policies:   dict[str, RetryPolicy],
    remaining:  dict[str, int],
) -> None:
    """Emit a single structured ERROR row when a retry class exhausts.

    The wrapper calls this exactly once per terminal failure — the
    ``exhausted_class`` field names the class that ran out of attempts,
    and ``attempts_used`` shows how many attempts each class consumed
    during this wrapper run (useful for spotting cross-class chains
    like "timed out once, then schema-failed three times").

    Parameters
    ----------
    agent_name:
        Name of the inner agent.
    cls:
        The class that just exhausted.
    exc:
        The exception that exhausted the budget.
    policies:
        The wrapper's policies dict (used to back-compute attempts_used).
    remaining:
        Per-class remaining attempts at the moment of exhaustion.
    """

    _LOGGER.error(
        "llm_retry_exhausted",
        extra={
            "kind":            "llm_retry_exhausted",
            "agent":           agent_name,
            "exhausted_class": cls,
            "exc_type":        type(exc).__name__,
            "exc_message":     str(exc),
            "attempts_used":   {
                c: policies[c].max_attempts - r
                for c, r in remaining.items()
            },
        },
    )


class RetryingAgentWrapper(BaseAgent):
    """Proxy an inner ADK agent with three-class retry + per-call timeout.

    The wrapper recognises three retryable failure classes and applies an
    independent attempt budget to each:

    * **rate_limit** — Vertex HTTP 429 (RESOURCE_EXHAUSTED).
    * **timeout**    — ``asyncio.TimeoutError`` raised by the per-call
                       ``asyncio.wait_for`` that bounds the inner agent's
                       wall-clock time.
    * **schema**     — ``pydantic.ValidationError`` from ADK's
                       output_schema parse.

    The inner agent's events are buffered until an attempt completes
    without raising; only the successful attempt's events flush to the
    outer pipeline.  The wrapper's own retry-counter ``state_delta``
    events ARE forwarded immediately (not buffered) so downstream
    callbacks see a running total mid-tick.

    The wrapper MUST only wrap a single LLM-calling agent (a bare
    ``LlmAgent``).  Wrapping a ``SequentialAgent`` breaks
    inter-child state propagation — see the strategist factory
    docstring for the full rationale.

    Attributes
    ----------
    inner:
        The wrapped agent (typically a bare ``LlmAgent``).
    timeout_seconds:
        Per-call wall-clock timeout in seconds.  Enforced via
        ``asyncio.wait_for`` around ``inner.run_async``.
    policies:
        Per-class retry policy dict keyed by ``"rate_limit"`` /
        ``"timeout"`` / ``"schema"``.  Built via
        :func:`build_retry_policies` at factory time.
    retry_state_key:
        Session-state key the wrapper increments on every retry — used
        by ``observability.terminal_log.emit_analyst_summary`` to render
        the per-tick retry suffix on the analyst summary rows.
    """

    inner:           Any
    timeout_seconds: float
    policies:        dict[str, RetryPolicy]
    retry_state_key: str

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        name:            str | None = None,
        inner:           Any,
        timeout_seconds: float,
        policies:        dict[str, RetryPolicy],
        retry_state_key: str,
    ) -> None:
        """Initialise the wrapper.

        Parameters
        ----------
        name:
            ADK agent name.  Defaults to ``"<inner.name>Retrying"`` so
            traces show the wrapping unambiguously.
        inner:
            The wrapped agent instance — must expose
            ``async def run_async(ctx)`` as an async generator.
        timeout_seconds:
            Per-call wall-clock timeout.  ``asyncio.wait_for(...)``
            raises ``asyncio.TimeoutError`` if the inner exceeds this.
        policies:
            Per-class retry policy dict.  Use :func:`build_retry_policies`
            to compose.
        retry_state_key:
            Session-state key for the per-tick retry-counter accumulator.
        """

        resolved_name = name if name is not None else f"{inner.name}Retrying"

        super().__init__(
            name            = resolved_name,
            inner           = inner,
            timeout_seconds = timeout_seconds,
            policies        = policies,
            retry_state_key = retry_state_key,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Drive the inner agent with per-class retry + wall-clock timeout.

        Per-attempt flow:

        1. Reset the events buffer.
        2. Drive the inner inside ``asyncio.wait_for(timeout_seconds)``.
           On success, break out and flush the buffer.
        3. On exception, classify; if unclassified, re-raise immediately.
        4. Decrement the matching class's remaining counter.
        5. Yield a ``state_delta`` event incrementing the retry-state-key
           accumulator (so terminal-log callbacks see the running total).
        6. If the class is now exhausted, log ``llm_retry_exhausted`` and
           re-raise.
        7. Otherwise log ``llm_retry_attempt``, sleep per the policy, and
           continue.

        Parameters
        ----------
        ctx:
            ADK invocation context.

        Yields
        ------
        Event
            The wrapper's own ``state_delta`` events (one per retry) and
            then, on success, every event from the successful attempt.
        """

        # Per-attempt event buffer — rebound at the start of every attempt.
        events: list[Event] = []

        # Per-class attempt counters — decremented when that class fires.
        remaining = {cls: pol.max_attempts for cls, pol in self.policies.items()}

        while True:
            events = []

            try:
                # Inner driver — packaged as a closure so asyncio.wait_for
                # has something cancellable.  We can't put `yield` directly
                # inside wait_for, so we collect into the events buffer
                # and flush after the loop terminates with success.
                async def _drive() -> None:
                    async for ev in self.inner.run_async(ctx):
                        events.append(ev)

                await asyncio.wait_for(_drive(), timeout=self.timeout_seconds)

                # Success — break to flush.
                break

            except BaseException as exc:
                cls = _classify(exc)

                if cls is None:
                    # Unclassified — re-raise immediately.  The
                    # IsolatedFailureWrapper (analysts) or backtest driver
                    # (strategist) handles it from here.
                    raise

                remaining[cls] -= 1

                # Emit the per-tick retry-counter state_delta BEFORE
                # checking exhaustion so the terminal-log row reflects
                # this attempt even when the next decision is to raise.
                current = ctx.session.state.get(self.retry_state_key) or {}
                yield Event(
                    author  = self.name,
                    content = None,
                    actions = EventActions(
                        state_delta = {
                            self.retry_state_key: _merge_increment(current, cls),
                        },
                    ),
                )

                if remaining[cls] <= 0:
                    _log_exhausted(self.inner.name, cls, exc, self.policies, remaining)
                    raise

                _log_retry(self.inner.name, cls, exc, remaining)

                # attempts_consumed_for_class — feeds exp-jitter for the
                # 429 path so the backoff grows attempt-by-attempt.
                # No-op for "immediate" policies.
                attempts_consumed = self.policies[cls].max_attempts - remaining[cls]
                await _sleep_per_policy(self.policies[cls], attempt_n=attempts_consumed)

                continue

        # Reached only on a successful attempt — flush buffered inner
        # events in original order.
        for ev in events:
            yield ev
```

Also remove the `tenacity` imports from the top of the file — they are no longer used.

- [ ] **Step 4: Run the wrapper test file to verify everything passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v 2>&1 | tail -40
```

Expected: every test passes.

- [ ] **Step 5: Run the full suite to catch any downstream breakage**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -50
```

Expected: factory wiring tests for News, Fundamental, Strategist may now fail because the wrapper construction signature changed. **Defer those failures** — Tasks 9–11 fix the factories. If any *other* test fails (not a factory wiring test), investigate now.

If `tests/agents/test_llm_retry_agent_name.py` references `RetryConfig`, `_is_resource_exhausted`, or the old ctor signature, update it in this task — fix to use the new ctor with `_fast_policies(...)` and `retry_state_key`. Keep its assertion semantics intact.

- [ ] **Step 6: Run ruff**

```bash
.venv/bin/python -m ruff check src/agents/llm_retry.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/agents/llm_retry.py tests/unit/agents/test_llm_retry.py tests/agents/test_llm_retry_agent_name.py
git commit -m "$(cat <<'EOF'
feat(retry): three-class wrapper with per-call timeout

Rewrites RetryingAgentWrapper internals:
- per-class budgets for rate_limit / timeout / schema
- asyncio.wait_for around inner.run_async
- yielded state_delta event for the per-tick retry-counter accumulator
- structured llm_retry_attempt WARNING per retry
- structured llm_retry_exhausted ERROR on terminal exhaustion
- drops the tenacity dependency from this module

Factory call sites are updated in the next three commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Extend `emit_analyst_summary` with optional `retries` kwarg

**Files:**
- Modify: `src/observability/terminal_log.py`
- Modify: `tests/unit/observability/test_terminal_log.py`

- [ ] **Step 1: Write the failing tests for the retry-suffix rendering**

Append to `tests/unit/observability/test_terminal_log.py`:

```python
def test_emit_analyst_summary_no_retries_renders_clean(caplog) -> None:
    """No retry suffix is rendered when retries is None or empty."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "news",
        calls         = [{"ticker": "AAPL", "elapsed": 1.0, "prompt_tokens": 1000, "candidate_tokens": 500, "ok": True}],
        ticker_count  = 1,
    )

    rows = [r.message for r in caplog.records if "news" in r.message]
    assert rows, "expected at least one stockbot.tick row mentioning 'news'"
    assert "retries" not in rows[-1]


def test_emit_analyst_summary_renders_retries_suffix(caplog) -> None:
    """A non-empty retries dict renders a ` · retries <class>×<n>` suffix."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "fundamental",
        calls         = [{"ticker": "AAPL", "elapsed": 1.0, "prompt_tokens": 1000, "candidate_tokens": 500, "ok": True}],
        ticker_count  = 1,
        retries       = {"rate_limit": 2},
    )

    rows = [r.message for r in caplog.records if "fundamental" in r.message]
    assert any("retries rate_limit×2" in r for r in rows)


def test_emit_analyst_summary_renders_multiple_retry_classes(caplog) -> None:
    """Multiple non-zero classes all appear in the suffix in fixed order
    (rate_limit, timeout, schema)."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "strategist",
        calls         = [{"ticker": "decision", "elapsed": 2.0, "prompt_tokens": 5000, "candidate_tokens": 3000, "ok": True}],
        ticker_count  = 1,
        retries       = {"schema": 2, "timeout": 1},      # given out-of-order
    )

    rows = [r.message for r in caplog.records if "strategist" in r.message]
    last = rows[-1]
    # Fixed order: rate_limit then timeout then schema.  rate_limit is zero so it's omitted.
    assert "retries timeout×1 schema×2" in last


def test_emit_analyst_summary_skips_zero_classes(caplog) -> None:
    """Zero-count classes are omitted from the suffix."""

    import logging

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    emit_analyst_summary(
        "news",
        calls         = [{"ticker": "AAPL", "elapsed": 1.0, "prompt_tokens": 1000, "candidate_tokens": 500, "ok": True}],
        ticker_count  = 1,
        retries       = {"rate_limit": 0, "timeout": 1, "schema": 0},
    )

    rows = [r.message for r in caplog.records if "news" in r.message]
    last = rows[-1]
    assert "retries timeout×1" in last
    assert "rate_limit" not in last
    assert "schema"     not in last
```

If `emit_analyst_summary` is not already imported at the top of the existing test file, add it: `from observability.terminal_log import emit_analyst_summary`.

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/observability/test_terminal_log.py -v -k "retries or no_retries" 2>&1 | tail -20
```

Expected: TypeError on the `retries=` kwarg.

- [ ] **Step 3: Extend `emit_analyst_summary` in `src/observability/terminal_log.py`**

Edit `src/observability/terminal_log.py`. Find the function signature near line 459:

```python
def emit_analyst_summary(
    analyst_label: str,
    *,
    calls: list[dict],
    ticker_count: int,
) -> None:
```

Extend it:

```python
def emit_analyst_summary(
    analyst_label: str,
    *,
    calls:        list[dict],
    ticker_count: int,
    retries:      dict[str, int] | None = None,
) -> None:
```

Update the docstring (insert a paragraph describing the new `retries`
parameter just above the existing ``Returns`` block):

```
    retries:
        Optional per-tick retry-class counter dict, written by
        :class:`agents.llm_retry.RetryingAgentWrapper` to a per-analyst
        session-state key.  When non-empty, a ``· retries
        <class>×<n>`` suffix is appended to the summary row for each
        non-zero class.  Class order in the suffix is fixed:
        ``rate_limit``, ``timeout``, ``schema``.
```

Find the row-rendering code (the function builds a `row` string and emits it via the `stockbot.tick` logger). Just **before** the row is emitted, append the retry suffix:

```python
    # Per-tick retry-counter suffix.  Only non-zero classes render; the
    # fixed order (rate_limit, timeout, schema) matches the
    # _classify dispatcher's priority order and keeps row layout stable.
    if retries:

        retry_order = ("rate_limit", "timeout", "schema")
        parts       = [
            f"{cls}×{retries[cls]}"
            for cls in retry_order
            if retries.get(cls)                                # non-zero only
        ]

        if parts:
            row = f"{row} · retries {' '.join(parts)}"
```

Place this block immediately before the `tick_log.info(row)` (or equivalent) call. The exact variable name (`row` vs `summary_row` vs `msg`) depends on the current implementation — match what the existing function uses.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/observability/test_terminal_log.py -v 2>&1 | tail -30
```

Expected: every test passes, including the four new ones.

- [ ] **Step 5: Run the full suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: no regressions beyond the deferred factory-wiring failures from Task 7.

- [ ] **Step 6: Run ruff**

```bash
.venv/bin/python -m ruff check src/observability/terminal_log.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/observability/terminal_log.py tests/unit/observability/test_terminal_log.py
git commit -m "$(cat <<'EOF'
feat(observability): retries suffix on analyst summary rows

emit_analyst_summary gains an optional retries kwarg. When non-empty,
appends ` · retries <class>×<count>` per non-zero class to the existing
summary row. Zero-count classes are omitted; class order is fixed
(rate_limit, timeout, schema). Joiner / strategist call sites pass it
in the next three commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Wire the News per-ticker factory

**Files:**
- Modify: `src/agents/analysts/news/per_ticker.py`
- Modify: `tests/analysts/test_per_ticker_branch.py`

- [ ] **Step 1: Write the failing wiring test for News**

Append to `tests/analysts/test_per_ticker_branch.py`:

```python
def test_news_branch_wires_llm_caps_from_config() -> None:
    """The News per-ticker branch reads `news.llm.*` and passes them to
    the LlmAgent (max_output_tokens) and to the RetryingAgentWrapper
    (timeout_seconds, policies, retry_state_key)."""

    from agents.analysts.news.per_ticker import build_news_branch_for_ticker
    from agents.analysts.heuristics       import load_heuristics
    from agents.isolated_failure          import IsolatedFailureWrapper
    from agents.llm_retry                 import RetryingAgentWrapper

    vocab = load_heuristics().news

    branch = build_news_branch_for_ticker("AAPL", vocab)

    assert isinstance(branch, IsolatedFailureWrapper)

    retrying = branch.inner
    assert isinstance(retrying, RetryingAgentWrapper)

    # Wrapper-level wiring.
    assert retrying.timeout_seconds == 60
    assert retrying.retry_state_key == "temp:_obs_news_retries"
    assert set(retrying.policies.keys()) == {"rate_limit", "timeout", "schema"}
    assert retrying.policies["timeout"].max_attempts  == 3
    assert retrying.policies["schema"].max_attempts   == 3

    # LlmAgent-level wiring — max_output_tokens flows into generate_content_config.
    llm = retrying.inner
    cfg = llm.generate_content_config

    assert cfg is not None
    assert cfg.max_output_tokens == 2000
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v -k "news_branch_wires_llm_caps" 2>&1 | tail -20
```

Expected: fails because the factory doesn't pass the new wrapper args yet.

- [ ] **Step 3: Edit `src/agents/analysts/news/per_ticker.py`**

Add the new imports near the existing ones:

```python
from google.genai import types as genai_types

from agents.llm_retry      import RetryingAgentWrapper, build_retry_policies
from config.analysts       import get_analysts_config
```

(Some of these may already be imported — keep them in alphabetical order with the rest and de-duplicate.)

Find the section that constructs the `LlmAgent`. Just above it, read the caps:

```python
    llm_caps = get_analysts_config().news.llm
```

Update the `LlmAgent(...)` call to pass `generate_content_config`:

```python
    llm = LlmAgent(
        name                    = f"NewsAnalyst_{ticker}",
        model                   = model,
        instruction             = instruction,
        output_schema           = TickerVerdict,
        output_key              = f"temp:news_verdict_{ticker}",
        before_model_callback   = before_cb,
        after_model_callback    = after_cb,
        generate_content_config = genai_types.GenerateContentConfig(
            max_output_tokens = llm_caps.max_output_tokens,
        ),
    )
```

Update the `RetryingAgentWrapper(...)` call:

```python
    retrying = RetryingAgentWrapper(
        name            = f"NewsAnalyst_{ticker}_retrying",
        inner           = llm,
        timeout_seconds = llm_caps.timeout_seconds,
        policies        = build_retry_policies(
            timeout_retries = llm_caps.timeout_retries,
            schema_retries  = llm_caps.schema_retries,
        ),
        retry_state_key = "temp:_obs_news_retries",
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v -k "news_branch_wires_llm_caps" 2>&1 | tail -20
```

Expected: pass.

- [ ] **Step 5: Run the full per-ticker branch test file**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v 2>&1 | tail -30
```

Expected: all existing News-related tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/news/per_ticker.py tests/analysts/test_per_ticker_branch.py
git commit -m "$(cat <<'EOF'
feat(analysts/news): wire LLM caps + three-class retry policies

News per-ticker factory now reads news.llm.{timeout_seconds,
max_output_tokens, timeout_retries, schema_retries} and passes them
to the LlmAgent (via GenerateContentConfig) and the
RetryingAgentWrapper. Retry state key is temp:_obs_news_retries.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Wire the Fundamental per-ticker factory

**Files:**
- Modify: `src/agents/analysts/fundamental/per_ticker.py`
- Modify: `tests/analysts/test_per_ticker_branch.py`

Mirror of Task 9.

- [ ] **Step 1: Write the failing test**

Append to `tests/analysts/test_per_ticker_branch.py`:

```python
def test_fundamental_branch_wires_llm_caps_from_config() -> None:
    """The Fundamental per-ticker branch reads `fundamental.llm.*` and
    passes them through correctly."""

    from agents.analysts.fundamental.per_ticker import build_fundamental_branch_for_ticker
    from agents.analysts.heuristics             import load_heuristics
    from agents.isolated_failure                import IsolatedFailureWrapper
    from agents.llm_retry                       import RetryingAgentWrapper

    vocab = load_heuristics().fundamental

    branch = build_fundamental_branch_for_ticker("AAPL", vocab)

    assert isinstance(branch, IsolatedFailureWrapper)

    retrying = branch.inner
    assert isinstance(retrying, RetryingAgentWrapper)

    assert retrying.timeout_seconds == 60
    assert retrying.retry_state_key == "temp:_obs_fundamental_retries"
    assert retrying.policies["timeout"].max_attempts == 3
    assert retrying.policies["schema"].max_attempts  == 3

    llm = retrying.inner
    cfg = llm.generate_content_config

    assert cfg is not None
    assert cfg.max_output_tokens == 2000
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v -k "fundamental_branch_wires_llm_caps" 2>&1 | tail -20
```

- [ ] **Step 3: Edit `src/agents/analysts/fundamental/per_ticker.py`**

Apply the same shape of edit as Task 9 Step 3, but read `get_analysts_config().fundamental.llm` and use `retry_state_key="temp:_obs_fundamental_retries"`. Imports + `llm_caps = …` line + extended `LlmAgent(...)` ctor + extended `RetryingAgentWrapper(...)` ctor.

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v -k "fundamental" 2>&1 | tail -30
```

- [ ] **Step 5: Run the full per-ticker branch suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_per_ticker_branch.py -v 2>&1 | tail -30
```

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/fundamental/per_ticker.py tests/analysts/test_per_ticker_branch.py
git commit -m "$(cat <<'EOF'
feat(analysts/fundamental): wire LLM caps + three-class retry policies

Symmetric to the News factory change in the previous commit. Reads
fundamental.llm.* from analysts.json and threads it through the
LlmAgent + RetryingAgentWrapper. Retry state key is
temp:_obs_fundamental_retries.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Wire the Strategist factory

**Files:**
- Modify: `src/agents/strategist/agent.py`
- Create: `tests/agents/strategist/test_build_strategist.py`

- [ ] **Step 1: Write the failing wiring test**

Create `tests/agents/strategist/test_build_strategist.py`:

```python
"""Wiring test for ``agents.strategist.agent.build_strategist``.

Asserts the factory produces a SequentialAgent whose second sub-agent
is a RetryingAgentWrapper carrying the strategist.llm caps from
config/strategist.json, and whose inner LlmAgent receives a
GenerateContentConfig with max_output_tokens=strategist.llm.max_output_tokens.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.llm_retry          import RetryingAgentWrapper
from agents.strategist.agent   import build_strategist


def test_build_strategist_wires_llm_caps_from_config() -> None:
    """SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent]] with
    strategist.llm.* threaded through."""

    branch = build_strategist()

    assert isinstance(branch, SequentialAgent)
    assert len(branch.sub_agents) == 2

    retrying = branch.sub_agents[1]
    assert isinstance(retrying, RetryingAgentWrapper)

    assert retrying.timeout_seconds == 180
    assert retrying.retry_state_key == "temp:_obs_strategist_retries"
    assert set(retrying.policies.keys()) == {"rate_limit", "timeout", "schema"}
    assert retrying.policies["timeout"].max_attempts == 3
    assert retrying.policies["schema"].max_attempts  == 3

    llm = retrying.inner
    cfg = llm.generate_content_config

    assert cfg is not None
    assert cfg.max_output_tokens == 8000
```

Also create the `tests/agents/strategist/` directory and an empty `__init__.py` if either does not exist.

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/agents/strategist/test_build_strategist.py -v 2>&1 | tail -20
```

- [ ] **Step 3: Edit `src/agents/strategist/agent.py::build_strategist`**

Add the import inside the `build_strategist` function (alongside the existing local imports):

```python
    from google.genai import types as genai_types

    from agents.llm_retry      import RetryingAgentWrapper, build_retry_policies
    from config.strategist     import get_strategist_config
```

Just after `model_name = get_models_config().strategist`, read the caps:

```python
    llm_caps = get_strategist_config().llm
```

Update the `LlmAgent(...)` call to pass `generate_content_config`:

```python
    llm = LlmAgent(
        name                    = "Strategist",
        model                   = model_name,
        instruction             = STRATEGIST_INSTRUCTION,
        output_schema           = StrategistDecision,
        output_key              = "strategist_decision",
        after_agent_callback    = _strategist_validation_callback,
        before_model_callback   = before_model,
        after_model_callback    = after_model,
        generate_content_config = genai_types.GenerateContentConfig(
            max_output_tokens = llm_caps.max_output_tokens,
        ),
    )
```

Update the `RetryingAgentWrapper(...)` call:

```python
    wrapped_llm = RetryingAgentWrapper(
        name            = "StrategistLlmRetrying",
        inner           = llm,
        timeout_seconds = llm_caps.timeout_seconds,
        policies        = build_retry_policies(
            timeout_retries = llm_caps.timeout_retries,
            schema_retries  = llm_caps.schema_retries,
        ),
        retry_state_key = "temp:_obs_strategist_retries",
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/agents/strategist/test_build_strategist.py -v 2>&1 | tail -20
```

- [ ] **Step 5: Run the full suite — every previously-deferred failure should now be green**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -50
```

Expected: every test that wasn't broken by Tasks 9–11's wiring should now be back to green. If any test still fails because it constructs the wrapper or LlmAgent directly with the old shape, fix it in this task.

- [ ] **Step 6: Run ruff**

```bash
.venv/bin/python -m ruff check src/
```

- [ ] **Step 7: Commit**

```bash
git add src/agents/strategist/agent.py tests/agents/strategist/test_build_strategist.py tests/agents/strategist/__init__.py
git commit -m "$(cat <<'EOF'
feat(strategist): wire LLM caps + three-class retry policies

Strategist factory now reads strategist.llm.* and threads it through
its LlmAgent (max_output_tokens=8000) and RetryingAgentWrapper
(timeout=180s, timeout/schema retries=3). Retry state key is
temp:_obs_strategist_retries. SequentialAgent topology preserved
verbatim — wrap remains inside, not around.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Wire `retries=` pass-through in the News joiner

**Files:**
- Modify: `src/agents/analysts/news/joiner.py`
- Modify: `tests/analysts/test_per_ticker_branch.py` (or wherever joiner tests live — check first)

- [ ] **Step 1: Find the joiner's call to `emit_analyst_summary`**

```bash
grep -n "emit_analyst_summary" src/agents/analysts/news/joiner.py
```

- [ ] **Step 2: Write the failing test**

If a joiner test file exists (`tests/analysts/test_news_joiner.py` or similar), append the test there. If not, create the file. The test asserts that when the joiner is run on a session-state with a populated retry counter, the summary row carries the suffix:

```python
def test_news_joiner_passes_retries_to_summary(caplog, monkeypatch) -> None:
    """The joiner reads temp:_obs_news_retries and passes it as the
    `retries=` kwarg to emit_analyst_summary."""

    import logging
    from agents.analysts.news.joiner import NewsJoinerAgent          # adjust to the actual class name

    caplog.set_level(logging.INFO, logger="stockbot.tick")

    # Construct the joiner; arrange a session-state-like object that
    # carries one cached News call record and a populated retry counter.
    # The exact fixture shape depends on the joiner's implementation —
    # mirror the pattern used by neighbouring joiner tests.
    state = {
        "temp:news_verdict_AAPL": {"ticker": "AAPL", "lean": "bullish", "confidence": 0.7, "rationale": "ok", "is_no_data": False, "drivers": []},
        "tickers":                ["AAPL"],
        "temp:_obs_news_calls":   [{"ticker": "AAPL", "elapsed": 1.0, "prompt_tokens": 1000, "candidate_tokens": 500, "ok": True}],
        "temp:_obs_news_retries": {"rate_limit": 2},
    }

    # Run the joiner against `state` — the joiner shape varies; if it's a
    # callback rather than an Agent, construct a minimal CallbackContext
    # carrying this state and call the callback directly.  Match neighbouring
    # joiner tests.

    rows = [r.message for r in caplog.records if "news" in r.message]
    assert any("retries rate_limit×2" in r for r in rows)
```

**Note for the implementer:** Examine an existing joiner test (e.g. in `tests/analysts/` or near `tests/analysts/test_per_ticker_branch.py`) to see how the joiner is invoked in tests, then adapt the fixture shape above to match.

- [ ] **Step 3: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v -k "news_joiner_passes_retries" 2>&1 | tail -20
```

- [ ] **Step 4: Edit `src/agents/analysts/news/joiner.py`**

Locate the call to `emit_analyst_summary(...)`. Add the `retries=` kwarg by reading from the same session-state object the joiner already accesses:

```python
emit_analyst_summary(
    "news",
    calls         = state.get("temp:_obs_news_calls") or [],
    ticker_count  = len(state.get("tickers") or []),
    retries       = state.get("temp:_obs_news_retries") or {},
)
```

The exact variable name for the state dict (`state`, `ctx.session.state`, `callback_context.state`) depends on the joiner's structure — match the existing pattern in the file.

- [ ] **Step 5: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v -k "news_joiner_passes_retries" 2>&1 | tail -20
```

- [ ] **Step 6: Run the full suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/news/joiner.py tests/
git commit -m "$(cat <<'EOF'
feat(analysts/news): join retry counter into terminal summary row

News joiner now passes temp:_obs_news_retries to emit_analyst_summary
so the per-tick summary row carries the new `· retries <class>×<n>`
suffix when any News retry fired during the tick.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Wire `retries=` pass-through in the Fundamental joiner

**Files:**
- Modify: `src/agents/analysts/fundamental/joiner.py`
- Modify: a fundamental-joiner test file (analogous to Task 12)

Mirror of Task 12 for `fundamental`.

- [ ] **Step 1: Find the joiner's call**

```bash
grep -n "emit_analyst_summary" src/agents/analysts/fundamental/joiner.py
```

- [ ] **Step 2: Write the failing test**

Same shape as Task 12 Step 2, with:

- `analyst_label` = `"fundamental"`
- state keys `temp:fundamental_verdict_AAPL`, `temp:_obs_fundamental_calls`, `temp:_obs_fundamental_retries`
- Assertion: `"retries timeout×1"` (or similar — pick any class)

- [ ] **Step 3: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v -k "fundamental_joiner_passes_retries" 2>&1 | tail -20
```

- [ ] **Step 4: Edit `src/agents/analysts/fundamental/joiner.py`**

Add `retries=state.get("temp:_obs_fundamental_retries") or {}` to the existing `emit_analyst_summary(...)` call.

- [ ] **Step 5: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v -k "fundamental_joiner_passes_retries" 2>&1 | tail -20
```

- [ ] **Step 6: Run the full suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/fundamental/joiner.py tests/
git commit -m "$(cat <<'EOF'
feat(analysts/fundamental): join retry counter into terminal summary row

Symmetric to the previous commit for the News joiner. Fundamental
joiner now passes temp:_obs_fundamental_retries to
emit_analyst_summary.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Wire `retries=` pass-through in the Strategist validation callback

**Files:**
- Modify: `src/agents/strategist/agent.py::_strategist_validation_callback`
- Modify: `tests/agents/strategist/` (add a callback test, or extend an existing one)

- [ ] **Step 1: Locate the callback's call to `emit_analyst_summary`**

```bash
grep -n "emit_analyst_summary" src/agents/strategist/agent.py
```

Expected: one call inside `_strategist_validation_callback` (look for the comment block `── Terminal summary row ──`).

- [ ] **Step 2: Write the failing test**

Create or append to `tests/agents/strategist/test_validation_callback.py`:

```python
"""Test that the strategist validation callback passes the retry counter
to emit_analyst_summary."""
from __future__ import annotations

import logging
import os

import pytest

from agents.strategist.agent import _strategist_validation_callback


def test_strategist_validation_callback_passes_retries(monkeypatch, caplog) -> None:
    """When STOCKBOT_TERMINAL_LOG=1 the callback emits a strategist row
    carrying the per-tick retry suffix derived from
    temp:_obs_strategist_retries."""

    monkeypatch.setenv("STOCKBOT_TERMINAL_LOG", "1")
    caplog.set_level(logging.INFO, logger="stockbot.tick")

    # Build a CallbackContext whose .state carries the minimum keys the
    # callback reads: tickers, strategist_decision (valid), portfolio,
    # _obs_strategist_calls accumulator, _obs_strategist_retries counter.
    # Match the shape used by neighbouring callback tests.
    #
    # Skip cleanly if the test premise can't be assembled — the existing
    # codebase already has a strategist-callback test pattern; reuse it.
    pytest.importorskip("google.adk.agents.callback_context")
    from google.adk.agents.callback_context import CallbackContext

    # ... (assemble ctx per the existing pattern; see
    # tests/agents/strategist/test_validation_callback*.py for examples) ...

    # Invoke the callback.
    _strategist_validation_callback(ctx)

    rows = [r.message for r in caplog.records if "strategist" in r.message]
    assert any("retries schema×1" in r for r in rows)
```

The exact `ctx` construction depends on the neighbouring test patterns in `tests/agents/strategist/`. If no such tests exist, look at `src/agents/strategist/agent.py::_strategist_validation_callback` to see what state keys it reads, then construct a minimal happy-path state.

- [ ] **Step 3: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/agents/strategist/test_validation_callback.py -v 2>&1 | tail -20
```

- [ ] **Step 4: Edit `src/agents/strategist/agent.py::_strategist_validation_callback`**

Find the `emit_analyst_summary(...)` call inside the `if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":` block. Add the `retries=` kwarg:

```python
        _strat_calls:   list[dict] = state.get("temp:_obs_strategist_calls")   or []
        _strat_retries: dict       = state.get("temp:_obs_strategist_retries") or {}

        emit_analyst_summary(
            "strategist",
            calls         = _strat_calls,
            ticker_count  = 1,
            retries       = _strat_retries,
        )
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/agents/strategist/test_validation_callback.py -v 2>&1 | tail -20
```

- [ ] **Step 6: Run the full suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

- [ ] **Step 7: Commit**

```bash
git add src/agents/strategist/agent.py tests/agents/strategist/
git commit -m "$(cat <<'EOF'
feat(strategist): join retry counter into terminal summary row

The validation callback now reads temp:_obs_strategist_retries and
passes it to emit_analyst_summary. Closes the loop: all three LLM
agents (news, fundamental, strategist) now surface their per-tick
retry counts on the terminal summary rows.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: End-to-end smoke test — one tick with a forced schema retry

**Files:**
- Create: `tests/integration/test_retry_smoke.py`

This test runs the per-ticker News branch (factory → wrapper → fake LlmAgent) against a synthesised session state, forces a `ValidationError` on the first call, and verifies (a) a valid verdict is produced, (b) `temp:_obs_news_retries` ends the tick at `{"schema": 1}`. No live API.

- [ ] **Step 1: Write the smoke test**

Create `tests/integration/test_retry_smoke.py`:

```python
"""End-to-end smoke test for the three-layer LLM retry.

Runs one News per-ticker branch against a fake LlmAgent that raises a
real ``pydantic.ValidationError`` on its first call and succeeds on the
second.  Asserts:

1. The wrapper produces a valid TickerVerdict (success after one retry).
2. The per-tick retry-counter accumulator
   ``temp:_obs_news_retries`` ends at ``{"schema": 1}``.

Honours the no-live-API hard rule in ``docs/test-policy.md`` — the
LlmAgent is a hand-built fake that never touches Vertex.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.adk.agents              import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events              import Event, EventActions
from pydantic                       import BaseModel, ValidationError

from agents.llm_retry import RetryingAgentWrapper, build_retry_policies


class _Tiny(BaseModel):
    """Pydantic model used to construct a real ValidationError."""

    name: str


def _make_validation_error() -> ValidationError:
    """Real ValidationError from a deliberately-failed parse."""

    try:
        _Tiny.model_validate({"name": 123})
    except ValidationError as ve:
        return ve

    raise AssertionError("Pydantic accepted invalid payload — test premise broken.")


class _FakeLlmAgent(BaseAgent):
    """ADK BaseAgent that raises ValidationError once, then succeeds."""

    name:        str = "FakeNewsAnalyst_AAPL"
    call_count:  int = 0

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Fail once, then yield a success event."""

        type(self).call_count += 1

        if type(self).call_count == 1:
            raise _make_validation_error()

        yield Event(
            author  = self.name,
            content = None,
            actions = EventActions(state_delta={"verdict_present": True}),
        )


def _ctx_with_state() -> InvocationContext:
    """A MagicMock ctx whose .session.state is a real dict."""

    ctx = MagicMock(spec=InvocationContext)
    ctx.session.state = {}
    return ctx


@pytest.mark.asyncio
async def test_one_schema_retry_succeeds_and_counter_records_it() -> None:
    """One forced ValidationError + success → wrapper succeeds, counter is {"schema": 1}."""

    _FakeLlmAgent.call_count = 0                          # reset class-level counter

    wrapper = RetryingAgentWrapper(
        inner           = _FakeLlmAgent(),
        timeout_seconds = 5.0,
        policies        = build_retry_policies(timeout_retries=3, schema_retries=3),
        retry_state_key = "temp:_obs_news_retries",
    )

    ctx = _ctx_with_state()

    events: list[Event] = []
    async for ev in wrapper.run_async(ctx):
        events.append(ev)

    # The state_delta events the wrapper yields must include the retry-counter
    # increment AND the success event from the fake.
    retry_evs = [
        e for e in events
        if e.actions and e.actions.state_delta
        and "temp:_obs_news_retries" in (e.actions.state_delta or {})
    ]
    success_evs = [
        e for e in events
        if e.actions and e.actions.state_delta
        and "verdict_present" in (e.actions.state_delta or {})
    ]

    assert len(retry_evs)   == 1
    assert retry_evs[0].actions.state_delta["temp:_obs_news_retries"] == {"schema": 1}
    assert len(success_evs) == 1

    # And the FakeLlmAgent was called exactly twice (one fail, one succeed).
    assert _FakeLlmAgent.call_count == 2
```

Add `tests/integration/__init__.py` (empty) if `tests/integration/` does not already exist.

- [ ] **Step 2: Run the smoke test**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_retry_smoke.py -v 2>&1 | tail -20
```

Expected: pass.

- [ ] **Step 3: Run the full suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: full green.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_retry_smoke.py tests/integration/__init__.py
git commit -m "$(cat <<'EOF'
test(retry): end-to-end smoke for forced schema retry

Drives a fake LlmAgent that raises ValidationError once then succeeds,
through the rewritten RetryingAgentWrapper, and asserts the wrapper
produces the success event AND emits a state_delta event recording
{"schema": 1} in temp:_obs_news_retries. Honours the no-live-API
hard rule.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Final verification + manual sanity-check note

**Files:**
- None modified.

- [ ] **Step 1: Run the full pytest suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v 2>&1 | tail -60
```

Expected: every test passes. If any test fails, fix it in this task before declaring done. Do **not** mark this task complete with red tests.

- [ ] **Step 2: Run ruff**

```bash
.venv/bin/python -m ruff check src/
```

Expected: clean.

- [ ] **Step 3: Confirm config/README.md is current**

Open `config/README.md` and verify:

1. The `retry_429.json` row exists (no stale `llm_retry.json` row).
2. The `analysts.json` section documents `news.llm.*` and `fundamental.llm.*` (all four sub-fields each).
3. The `strategist.json` section documents `llm.*` (all four sub-fields).

If any documentation is missing, add it and commit as a single `docs(config): ...` commit.

- [ ] **Step 4: Manual sanity check — terminal-log retry suffix**

This is **not** an automated test — a one-off behavioural validation to confirm the new suffix actually renders. Run a one-tick replay with `max_output_tokens` clamped low enough to provoke a real schema failure:

```bash
# Make a one-off, temporary clamp.  DO NOT COMMIT THIS — it's diagnostic.
.venv/bin/python -c "
import json
p = 'config/analysts.json'
cfg = json.loads(open(p).read())
cfg['news']['llm']['max_output_tokens'] = 50
open(p, 'w').write(json.dumps(cfg, indent=2))
print('clamped news.llm.max_output_tokens to 50 — restore with git checkout', p)
"

# Then run a known one-tick smoke (per docs/test-policy.md §A).  Look for
# a stderr row of the shape:
#   news: 0/1 ✗ … · retries schema×3
# in the terminal output.

STOCKBOT_TERMINAL_LOG=1 PYTHONPATH=src .venv/bin/python -m scripts.replay_backtest --window baseline-2025-09 --ticks 1 2>&1 | grep -E "news:|fundamental:|strategist:"

# Restore the clamp.
git checkout config/analysts.json
```

Expected: the `news:` (or `fundamental:`) summary row carries a `· retries schema×<N>` suffix. If it does NOT, debug — the wrapper, the joiner pass-through, or the `emit_analyst_summary` suffix code is wrong.

(If `scripts/replay_backtest.py` requires args this plan doesn't show, consult the script's `--help`. The user runs this manually per `project_replay_backtest_manual_tool.md` — they know the invocation.)

- [ ] **Step 5: Update `graphify-out/graph_delta.md`**

Append a dated entry to `graphify-out/graph_delta.md` describing the structural changes — per `CLAUDE.md`'s graphify convention. Match the existing entry style:

```markdown
## 2026-MM-DD — Three-layer LLM retry (rate-limit / timeout / schema)

### New symbols

- `agents.llm_retry.RetryPolicy` (Pydantic) — per-class retry policy
  with ``max_attempts`` + ``backoff`` Literal["immediate", "exp_jitter"]
  + optional ``base_delay_seconds`` / ``max_delay_seconds``.
- `agents.llm_retry._classify` — top-level dispatcher returning
  ``"rate_limit"`` / ``"timeout"`` / ``"schema"`` / ``None``.
- `agents.llm_retry._is_rate_limit` (replaces `_is_resource_exhausted`).
- `agents.llm_retry._is_timeout`.
- `agents.llm_retry._is_schema_error`.
- `agents.llm_retry._compute_exp_jitter` — pure tenacity replacement.
- `agents.llm_retry._sleep_per_policy` — async sleep helper.
- `agents.llm_retry._merge_increment` — pure dict-increment helper.
- `agents.llm_retry.build_retry_policies` — composes per-agent policy
  dict from the project-wide 429 policy + caller-supplied timeout /
  schema counts.
- `agents.llm_retry._log_retry` / `_log_exhausted` — structured WARNING
  and ERROR helpers.
- `config.analysts.LlmCaps` (Pydantic) — per-agent ``timeout_seconds``,
  ``max_output_tokens``, ``timeout_retries``, ``schema_retries``.
  Attached as ``NewsCaps.llm``, ``FundamentalCaps.llm``,
  ``StrategistConfig.llm``.

### Renamed symbols

- `config/llm_retry.json` → `config/retry_429.json`.
- `src.config.llm_retry` → `src.config.retry_429`.  `RetryConfig` →
  `Retry429Policy`; `load_retry_config` → `load_retry_429_policy`;
  `get_retry_config` → `get_retry_429_policy`.

### Removed symbols

- `agents.llm_retry._is_resource_exhausted` (replaced by `_is_rate_limit`).
- `agents.llm_retry`'s tenacity imports (`AsyncRetrying`, `RetryCallState`,
  `retry_if_exception`, `stop_after_attempt`, `wait_exponential_jitter`).
- `agents.llm_retry._make_before_sleep` (replaced by `_log_retry`).

### Behavioural changes

- `agents.llm_retry.RetryingAgentWrapper` now wraps inner runs in
  `asyncio.wait_for(timeout_seconds)` and retries three independent
  classes with per-class budgets (429: 5 attempts, exp-jitter backoff;
  timeout: 3 attempts, no backoff; schema: 3 attempts, no backoff).
  Constructor changes: `retry_config` removed; new args
  `timeout_seconds`, `policies`, `retry_state_key`.
- News, Fundamental, Strategist factories now read per-agent ``llm``
  caps and pass them to the LlmAgent (via
  `google.genai.types.GenerateContentConfig(max_output_tokens=…)`) and
  to `RetryingAgentWrapper`.
- `observability.terminal_log.emit_analyst_summary` gains optional
  `retries` kwarg; renders a `· retries <class>×<n>` suffix.
- All three joiners / the strategist validation callback pass
  `state.get("temp:_obs_<agent>_retries")` to `emit_analyst_summary`.
```

Commit the graph_delta update separately:

```bash
git add graphify-out/graph_delta.md
git commit -m "$(cat <<'EOF'
docs(graphify): append delta for three-layer LLM retry

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Verify the branch is clean and ready**

```bash
git status
git log --oneline -20
```

Expected: working tree clean; the last ~15 commits describe the three-layer retry work end-to-end.

- [ ] **Step 7: Report completion to the user**

Summarise:

- Spec implemented in 16 tasks; each task one focused commit.
- All tests pass (`pytest tests/ -v`); ruff clean.
- Manual sanity check confirmed the new `· retries <class>×<n>` suffix renders.
- Graph delta updated.
- Next steps (user-driven): observe a real backtest run and confirm the wall-clock improvements (fewer wedged ticks); revisit defaults in `analysts.json` / `strategist.json` if any cap turns out to be too tight.

---

## Self-review

This is a checklist run against the spec at
`docs/Phase10-post-first-backtest/specs/llm-retry-three-layer.md`. Each section number below maps to the corresponding spec section.

**Spec coverage**

| Spec section | Implementing task(s) |
|---|---|
| §2 in-scope: rate_limit / timeout / schema retry classes | Tasks 4, 5, 7 |
| §2 in-scope: per-agent `asyncio.wait_for` timeout | Task 7 |
| §2 in-scope: per-agent `max_output_tokens` cap | Tasks 9, 10, 11 |
| §2 in-scope: rename of llm_retry.json → retry_429.json | Task 1 |
| §2 in-scope: per-agent `llm` blocks in analysts.json / strategist.json | Tasks 2, 3 |
| §2 in-scope: drop tenacity from the retry path | Tasks 5, 7 |
| §2 in-scope: per-tick retry telemetry in session state | Task 7 |
| §2 in-scope: terminal-summary suffix | Task 8 |
| §2 in-scope: structured `llm_retry_exhausted` ERROR log | Task 7 |
| §3 contract anchors §C Rule 1 (`state_delta` event) | Task 7 |
| §4 wrapping topology preserved | Tasks 9, 10, 11 |
| §5 configuration shape (all four sub-keys per agent + 429 file) | Tasks 1, 2, 3 |
| §5.6 config/README.md updates | Tasks 1, 2, 3, 16 |
| §6 exception classification (3 classes + None) | Task 4 |
| §6.3 StrategistContractViolation explicit non-retry | Task 4 (`test_classify_returns_none_for_strategist_contract_violation`) |
| §7 wrapper internals (run loop + helpers) | Tasks 5, 6, 7 |
| §8 max_output_tokens wiring at every factory | Tasks 9, 10, 11 |
| §9 terminal-log integration | Task 8 |
| §9.4 caller-side wiring for retries kwarg | Tasks 12, 13, 14 |
| §10.3 `llm_retry_exhausted` ERROR | Task 7 |
| §11 testing tiers A/B/C/D | Tasks 2, 3, 4, 5, 6, 7, 9, 10, 11, 15 |
| §11.6 manual sanity check | Task 16 |
| §12 migration notes (single-commit rename, no shim) | Task 1 (and the wrapper-rewrite Task 7 cleans up the transitional alias) |
| §15 acceptance criteria | Task 16 |

**Placeholder scan:** no TBD / TODO / "implement later" markers remain. The only intentionally test-implementer-flexible spots are Task 12–14's joiner-fixture construction (the implementer must mirror neighbouring test patterns because joiner test scaffolding varies); these are flagged inline with explicit instructions.

**Type consistency:** symbol names checked across tasks — `RetryPolicy` (5, 6, 7), `_classify` (4, 7), `build_retry_policies` (6, 9, 10, 11), `_merge_increment` (5, 7), `Retry429Policy` (1, 6), `get_retry_429_policy` (1, 6, 16), `temp:_obs_<agent>_retries` (7, 9, 10, 11, 12, 13, 14), `LlmCaps` (2, 3), all consistent. The wrapper constructor args `timeout_seconds` / `policies` / `retry_state_key` are used consistently across tasks 7, 9, 10, 11, 15.

**Scope check:** focused on the three-layer retry feature. No scope creep into adjacent areas (no changes to `IsolatedFailureWrapper`, no `StrategistContractViolation` retry, no changes to `_strategist_validation_callback` beyond passing the retries kwarg).

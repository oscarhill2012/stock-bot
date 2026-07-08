# Plan 4 — Linkage Analyst (Digester, Exposure Map, Matcher, Registry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/Phase14-analyst-refactor/specs/analyst-drift-refactor-design.md` — this plan implements §6.3 (linkage analyst) on the §6.1 architecture, honouring §7 (error handling) and §8 (testing). §6.4 lists rejected alternatives — do not resurrect them.

> **EXECUTION ORDER — READ FIRST.** Plan 4 executes **after Plans 2 and 3 have landed**, and its build is additionally gated on Plan 2's eval showing the drift reframe has signal (spec §3). This plan **consumes** deliverables owned by its siblings and **trusts them completely** — there are NO defensive shims, NO `if key in state` fallbacks, and NO local stubs for anything Plans 2/3 own. Specifically:
> - `state["macro_articles"]` (Plan 3) is **always present** — a list of `MacroArticle.model_dump(mode="json")` dicts, shape `{"article": {<serialised NewsArticle, published_at ISO string>}, "mentioned_tickers": ["..."]}`, empty list on a quiet tick. If it is absent, that is a **loud `KeyError` bug in Plan 3**, not a condition this plan handles.
> - `agents.analysts.news.history.NewsHistoryStore` (Plan 2) with `async staleness(namespace, text) -> float`, `async record(namespace, article_key, text, published_at) -> None`, plus the `get_news_history_store()` / `reset_news_history_store()` singletons.
> - `AnalystVerdict.horizon_days: int = Field(default=1, ge=1)` (Plan 2, on `src/contract/evidence.py`).
> - `config/analysts.json::staleness_similarity_threshold` (top-level float, Plan 2) — shared verbatim by this plan's `"macro"` staleness pass.

**Goal:** Add a fourth analyst stream, `linkage`, that positions for economic-links drift (Cohen & Frazzini): a deterministic staleness pre-filter over the macro stream, one flash-class **event digester** call per tick, a persistent per-run **event registry** with horizon decay, an offline-built per-ticker **exposure map**, and one flash-class **matcher** call per tick that crosses active events against exposures to emit per-ticker `AnalystVerdict`s carrying `horizon_days`. The stream is wired end-to-end into the pipeline, strategist, evidence persistence, and scoreboard.

**Architecture:** A new package `src/agents/analysts/linkage/` owns the branch, assembled (mirroring the News branch) as `SequentialAgent[LinkageStalenessAgent, LinkageDigesterAgent, LinkageRegistryAgent, LinkageMatcherAgent, LinkageJoinerAgent]`. The two LLM stages (`LinkageDigesterAgent`, `LinkageMatcherAgent`) are `BaseAgent` subclasses that call an **injectable async `llm_fn(prompt, schema) -> BaseModel`** boundary (the testable seam, mirroring Plan 2's injectable `embed_fn`). The stages are sequentially interdependent (the registry reads the digest; the matcher reads the active set), so the **whole branch** is wrapped in one `IsolatedFailureWrapper` (analyst `linkage`, ticker sentinel `"_ALL"`) rather than each stage individually — any stage's exhausted call or a stale-map error fails loudly and is *contained to the linkage stream*: the wrapper logs a structured `branch_failed` record, the analyst pool and the other three analysts continue, and the digest neutral-fills the absent `linkage` slot for that tick. The registry is a SQLite `linkage_events` table via the existing `src/orchestrator/persistence.py` ORM, fronted by a per-run store (`src/agents/analysts/linkage/registry.py`) reset per window replay exactly as Plan 2 resets its history store. The exposure map is a JSON data artefact built offline by `scripts/build_exposure_map.py` and loaded read-only on the tick path, failing loudly when stale.

**Tech Stack:** Python 3.12, Pydantic v2 (`extra="forbid"` emit schemas, caps stated in prompts not `max_length`), Google ADK (`BaseAgent` state_delta events, `google.genai` structured output), SQLite via SQLAlchemy ORM, pytest + pytest-asyncio.

## Global Constraints

Every task's requirements implicitly include this section.

- **British English** everywhere — identifiers, comments, docstrings, prose (`colour`, `behaviour`, `analyse`, `normalise`, `optimise`).
- **Every function gets a docstring** stating purpose, parameters, and return value. Comment non-trivial logic inline. Separate logical blocks with blank lines for legibility.
- **Loud failures:** raise rather than degrade to null/empty/neutral. Every test asserts a **positive signal** (a verdict/event actually appears with the right shape), never merely the absence of an error. The one sanctioned quiet path is a genuine **quiet tick** (no novel macro articles, or no active events, or no exposed tickers) — which is **logged explicitly** and distinguished from a **failure** (which raises).
- **Token economics (spec D4):** at most **two flash-class LLM calls per tick** for the whole watchlist — the digester and the matcher. The deterministic staleness pre-filter does the volume kill; on a quiet tick each LLM stage is **skipped**, not called with empty input.
- **ADK rules:** every read of `state["as_of"]` goes through `resolve_as_of`; every datetime written to state is ISO-stringified first (`model_dump(mode="json")` or `.isoformat()`) — the backtest `DatabaseSessionService` cannot hold `datetime`. Never mutate `adk_session.state["temp:_*"]` after `create_session`; if a temp handle is ever needed use a `BasePlugin.before_run_callback`. Per-stage `temp:linkage_*` keys are private to the branch; the joiner owns the durable `linkage_verdicts` / `linkage_evidence` keys.
- **No `max_length` on LLM free-text schema fields** (Vertex pad-toward-cap pathology). Prose caps are stated in the prompt only. `max_length` on a **list** field (a count bound, e.g. `key_factors`) is allowed.
- **Config convention:** linkage-specific settings live in a new `config/linkage.json` modelled by `src/config/linkage.py`; model IDs live in `config/models.json` (per that file's one-slot-per-role convention); the shared staleness threshold stays in `config/analysts.json` (Plan 2). Every new setting gets a `config/README.md` row **in the same task that introduces it**. Never hardcode a config value in source.
- **Names are pinned — use verbatim:** analyst name `linkage`; state keys `linkage_verdicts`, `linkage_evidence`; registry table `linkage_events`; event categories `{macro, sector, merger}`; staleness namespace literal `"macro"`.
- **Shell conventions:** never prefix commands with `cd`. Tests: `.venv/bin/python -m pytest <path> -v`. Lint: `.venv/bin/python -m ruff check src/ tests/`. Scripts: `PYTHONPATH=src .venv/bin/python -m scripts.<name>`.
- **Git quirk:** new files under `tests/unit/data/` are silently swallowed by `.git/info/exclude` — stage them with `git add -f`. (Noted again in each affected commit step.)
- **Commits:** one per task, message given in the task. End every commit message with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Cross-plan facts this plan relies on (verified 2026-07-07)

- `state["macro_articles"]` is emitted unconditionally by `NewsFetchAgent` (Plan 3, Task 3) as `[MacroArticle.model_dump(mode="json"), ...]`.
- The strategist's per-ticker view is fed by `agents/strategist/context_shim.py`, which currently indexes evidence via `_index_evidence(state, "news_evidence")` at line 375 — the linkage index is added alongside.
- `DEFAULT_ANALYST_WEIGHTS` (`src/contract/digest.py:56`) currently holds only `technical`/`fundamental`/`news`; the digest neutral-fills (with a WARNING) any weighted analyst absent this tick, so `linkage` must be added there **only in the same task that wires it into the pipeline and the shim** (avoids phantom-slot dilution).
- `src/agents/contract/evidence_writer.py::_EVIDENCE_KEYS` is a **hardcoded tuple** of `(state_key, analyst)` pairs; the `analyst_evidence` SQLite table is populated only from those pairs. The backtest scoreboard (`src/backtest/scoreboard.py`) derives its analyst set **dynamically from that table** (`ScoreboardResult.analysts` is data-driven), so adding `("linkage_evidence", "linkage")` to `_EVIDENCE_KEYS` is what makes `linkage` first-class in the scoreboard's `(analyst, ...)` clustering. The only additional scoreboard knob is `primary_horizon_by_analyst["linkage"]` in `config/backtest_settings.json`.
- LLM structured-output precedent for the injectable-`llm_fn` default: `src/agents/memory/compress.py` uses `genai.Client().models.generate_content(model=..., contents=...)`. The linkage default `llm_fn` uses the same client with `response_schema`/`response_mime_type` for JSON.
- The News branch factory (`src/agents/analysts/news/agent.py`) and per-ticker wrapper usage (`IsolatedFailureWrapper` from `agents.isolated_failure`) are the templates the linkage branch mirrors.

---

### Task 1: Config — `config/linkage.json`, loader, and model slots

**Files:**
- Create: `config/linkage.json`
- Create: `src/config/linkage.py`
- Modify: `config/models.json`
- Modify: `src/config/models.py` (add the three linkage model-ID fields to `ModelsConfig`)
- Modify: `config/README.md`
- Test: `tests/unit/config/test_linkage_config.py`

**Interfaces:**
- Consumes: nothing (leaf task).
- Produces (consumed by Tasks 3–12):
  - `src/config/linkage.py`: `class LinkageLlmCaps(BaseModel)`, `class LinkageConfig(BaseModel)`, `def load_linkage_config(path: Path = _DEFAULT_PATH) -> LinkageConfig`, `def get_linkage_config() -> LinkageConfig` (`@lru_cache(maxsize=1)`).
  - `LinkageConfig` fields: `exposure_map_path: str`, `exposure_map_staleness_cap_days: int`, `event_horizon_days: dict[str, int]` (keys `macro`/`sector`/`merger`), `digester: LinkageLlmCaps`, `matcher: LinkageLlmCaps`.
  - `LinkageLlmCaps` fields: `timeout_seconds: int`, `max_output_tokens: int`, `thinking_level: str`, `temperature: float`, `timeout_retries: int`, `schema_retries: int`.
  - `config/models.json` gains `linkage_digester`, `linkage_matcher`, `linkage_exposure_builder`; `ModelsConfig` gains the matching string fields.

- [ ] **Step 1: Write the failing config tests**

Create `tests/unit/config/test_linkage_config.py` with exactly this content:

```python
"""Unit tests for the linkage analyst config (Phase 14 Plan 4).

Locks the committed ``config/linkage.json`` shape and the three linkage
model slots in ``config/models.json`` so a missing or malformed key is a
loud import-time / load-time failure, never a silent default.
"""
from __future__ import annotations

from pathlib import Path

from config.linkage import LinkageConfig, get_linkage_config, load_linkage_config
from config.models import get_models_config


def test_linkage_config_loads_from_committed_file():
    """The committed config file parses into a fully-populated LinkageConfig."""
    cfg = load_linkage_config(Path("config/linkage.json"))

    assert isinstance(cfg, LinkageConfig)
    assert cfg.exposure_map_path == "data/linkage/exposure_map.json"
    assert cfg.exposure_map_staleness_cap_days == 7
    # Every event category carries a drift horizon in trading days.
    assert set(cfg.event_horizon_days) == {"macro", "sector", "merger"}
    assert all(v >= 1 for v in cfg.event_horizon_days.values())


def test_linkage_llm_caps_are_present_for_both_stages():
    """Digester and matcher each carry an independent LLM caps block."""
    cfg = get_linkage_config()

    for caps in (cfg.digester, cfg.matcher, cfg.exposure_builder):
        assert caps.timeout_seconds >= 1
        assert caps.max_output_tokens >= 1
        assert 0.0 <= caps.temperature <= 2.0
        assert caps.timeout_retries >= 0
        assert caps.schema_retries >= 0


def test_models_config_exposes_the_three_linkage_slots():
    """The linkage stages resolve their model IDs from config/models.json."""
    models = get_models_config()

    assert models.linkage_digester
    assert models.linkage_matcher
    assert models.linkage_exposure_builder
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/config/test_linkage_config.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'config.linkage'`.

- [ ] **Step 3: Create `config/linkage.json`**

```json
{
  "_comment": "Linkage analyst (Phase 14 Plan 4) settings. Model IDs live in config/models.json; the shared staleness threshold lives in config/analysts.json (staleness_similarity_threshold).",
  "exposure_map_path": "data/linkage/exposure_map.json",
  "exposure_map_staleness_cap_days": 7,
  "event_horizon_days": {
    "macro":  5,
    "sector": 21,
    "merger": 42
  },
  "digester": {
    "timeout_seconds":   60,
    "max_output_tokens": 8000,
    "thinking_level":    "medium",
    "temperature":       0.2,
    "timeout_retries":   3,
    "schema_retries":    3
  },
  "matcher": {
    "timeout_seconds":   60,
    "max_output_tokens": 8000,
    "thinking_level":    "medium",
    "temperature":       0.2,
    "timeout_retries":   3,
    "schema_retries":    3
  },
  "exposure_builder": {
    "timeout_seconds":   180,
    "max_output_tokens": 8000,
    "thinking_level":    "high",
    "temperature":       0.2,
    "timeout_retries":   3,
    "schema_retries":    3
  }
}
```

(The `exposure_builder` block caps the **offline** deeper-model pass in `scripts/build_exposure_map.py`. It is off the tick path, so a longer `timeout_seconds` and `thinking_level: high` are affordable and desirable for map quality.)

- [ ] **Step 4: Create `src/config/linkage.py`**

```python
"""Loader for ``config/linkage.json`` — linkage analyst settings (Phase 14).

A Pydantic-validated wrapper around the JSON file at the project root,
mirroring the ``src/config/models.py`` / ``src/config/analysts.py`` pattern:
``get_linkage_config()`` is the cached production entry point and
``load_linkage_config(path=...)`` is the test hook for a custom file.

What lives here vs elsewhere:
  * Model IDs live in ``config/models.json`` (one slot per role).
  * The staleness similarity threshold is shared with the per-ticker news
    filter and lives in ``config/analysts.json`` (Plan 2).
  * Everything linkage-specific — the exposure-map artefact path and
    staleness cap, per-category drift horizons, and the two LLM caps
    blocks — lives here.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# Project-root-relative default path.  The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather than
# to this file — matches the convention in ``src/config/models.py``.
_DEFAULT_PATH = Path("config/linkage.json")


class LinkageLlmCaps(BaseModel):
    """Runtime caps for one linkage LLM stage (digester or matcher).

    Attributes
    ----------
    timeout_seconds:
        Wall-clock bound for a single call before it is treated as timed out.
    max_output_tokens:
        Output-token ceiling passed to the model's generation config.
    thinking_level:
        Gemini-3 thinking effort enum (e.g. ``"low"``/``"medium"``/``"high"``).
    temperature:
        Sampling temperature — low keeps structured output stable.
    timeout_retries:
        Number of retries permitted on a timed-out call.
    schema_retries:
        Number of retries permitted on a schema-validation failure.
    """

    timeout_seconds:   int   = Field(ge=1)
    max_output_tokens: int   = Field(ge=1)
    thinking_level:    str
    temperature:       float = Field(ge=0.0, le=2.0)
    timeout_retries:   int   = Field(ge=0)
    schema_retries:    int   = Field(ge=0)


class LinkageConfig(BaseModel):
    """Top-level shape of ``config/linkage.json``.

    Attributes
    ----------
    exposure_map_path:
        Project-root-relative path to the exposure-map JSON data artefact
        built offline by ``scripts/build_exposure_map.py``.
    exposure_map_staleness_cap_days:
        Maximum age (``as_of`` minus the map's ``built_at``) in calendar days
        before the tick fails loudly rather than match against a stale map.
    event_horizon_days:
        Per-category drift horizon in trading days — how long an event of
        each category (``macro``/``sector``/``merger``) stays active in the
        registry before it expires.
    digester:
        LLM caps for the event digester stage.
    matcher:
        LLM caps for the matcher stage.
    exposure_builder:
        LLM caps for the offline per-ticker exposure-map builder (off the
        tick path — see ``scripts/build_exposure_map.py``).
    """

    exposure_map_path:               str
    exposure_map_staleness_cap_days: int = Field(ge=1)
    event_horizon_days:              dict[str, int]
    digester:                        LinkageLlmCaps
    matcher:                         LinkageLlmCaps
    exposure_builder:                LinkageLlmCaps


def load_linkage_config(path: Path = _DEFAULT_PATH) -> LinkageConfig:
    """Load and validate ``config/linkage.json`` from ``path``.

    Parameters
    ----------
    path:
        Filesystem path to the JSON config. Defaults to the project-root file.

    Returns
    -------
    LinkageConfig
        The validated config object.
    """
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    # Drop the leading ``_comment`` documentation key before validation so it
    # does not collide with the strict model shape.
    raw.pop("_comment", None)

    return LinkageConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_linkage_config() -> LinkageConfig:
    """Return the process-wide cached linkage config.

    The file is read exactly once per process; a runtime edit needs a restart
    (matches ``src/config/models.py`` hot-reload semantics).

    Returns
    -------
    LinkageConfig
        The cached config object.
    """
    return load_linkage_config()
```

- [ ] **Step 5: Add the three model slots**

In `config/models.json`, add three keys after `"fundamental_analyst"`:

```json
  "linkage_digester": "gemini-3.5-flash",
  "linkage_matcher": "gemini-3.5-flash",
  "linkage_exposure_builder": "gemini-2.5-pro",
```

In `src/config/models.py`, add three fields to `ModelsConfig` immediately after the `fundamental_analyst` field (match the file's existing field style and docstring listing):

```python
    linkage_digester: str = Field(min_length=1)
    linkage_matcher: str = Field(min_length=1)
    linkage_exposure_builder: str = Field(min_length=1)
```

(The digester and matcher use a flash-class model per spec D4; the exposure builder is the offline deeper-model pass — spec §6.3.)

- [ ] **Step 6: Document every new setting in `config/README.md`**

Add a `linkage.json` section (mirror the layout of the existing `analysts.json` section) with one row per setting:

```markdown
### `config/linkage.json`

Settings for the linkage analyst (Phase 14 Plan 4). Model IDs live in `models.json`; the staleness similarity threshold is shared with the news filter and lives in `analysts.json`.

| Setting | Type | Description |
| --- | --- | --- |
| `exposure_map_path` | string | Project-root-relative path to the exposure-map JSON artefact built offline by `scripts/build_exposure_map.py`. Default `data/linkage/exposure_map.json`. |
| `exposure_map_staleness_cap_days` | int, `>= 1` | Maximum age (`as_of` − map `built_at`) in calendar days before a tick fails loudly rather than matching against a stale exposure map. Default `7`. |
| `event_horizon_days` | dict[str, int] | Per-category drift horizon in trading days an event stays active in the registry before expiry. Keys `macro`/`sector`/`merger`. Default `{"macro": 5, "sector": 21, "merger": 42}`. |
| `digester.timeout_seconds` | int, `>= 1` | Wall-clock bound for one event-digester LLM call. Default `60`. |
| `digester.max_output_tokens` | int, `>= 1` | Output-token ceiling for the digester call. Default `8000`. |
| `digester.thinking_level` | string | Gemini-3 thinking effort enum for the digester. Default `medium`. |
| `digester.temperature` | float, `0`–`2` | Sampling temperature for the digester. Default `0.2`. |
| `digester.timeout_retries` | int, `>= 0` | Retries on a timed-out digester call. Default `3`. |
| `digester.schema_retries` | int, `>= 0` | Retries on a schema-validation failure of the digester call. Default `3`. |
| `matcher.timeout_seconds` | int, `>= 1` | Wall-clock bound for one matcher LLM call. Default `60`. |
| `matcher.max_output_tokens` | int, `>= 1` | Output-token ceiling for the matcher call. Default `8000`. |
| `matcher.thinking_level` | string | Gemini-3 thinking effort enum for the matcher. Default `medium`. |
| `matcher.temperature` | float, `0`–`2` | Sampling temperature for the matcher. Default `0.2`. |
| `matcher.timeout_retries` | int, `>= 0` | Retries on a timed-out matcher call. Default `3`. |
| `matcher.schema_retries` | int, `>= 0` | Retries on a schema-validation failure of the matcher call. Default `3`. |
| `exposure_builder.*` | same shape as `digester`/`matcher` | LLM caps for the **offline** per-ticker exposure-map builder (`scripts/build_exposure_map.py`). Off the tick path, so `timeout_seconds` default `180` and `thinking_level` default `high`. Other defaults match the digester block. |
```

Also add the three new `models.json` rows to that file's model-slot table:

```markdown
| `linkage_digester` | string | Model ID for the linkage event-digester flash-class call. Default `gemini-3.5-flash`. |
| `linkage_matcher` | string | Model ID for the linkage matcher flash-class call. Default `gemini-3.5-flash`. |
| `linkage_exposure_builder` | string | Model ID for the offline per-ticker exposure-map builder (`scripts/build_exposure_map.py`). Default `gemini-2.5-pro`. |
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/config/test_linkage_config.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add config/linkage.json config/models.json config/README.md src/config/linkage.py src/config/models.py tests/unit/config/test_linkage_config.py
git commit -m "feat(linkage): add config/linkage.json, loader, and model slots (Phase 14 Plan 4)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `linkage` analyst name in the contract

**Files:**
- Modify: `src/contract/evidence.py:25` (`AnalystName` Literal)
- Test: `tests/unit/contract/test_linkage_analyst_name.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AnalystName` Literal now includes `"linkage"`, so `AnalystEvidence(analyst="linkage", ...)` validates (consumed by the joiner in Task 9 and the evidence writer in Task 11).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_linkage_analyst_name.py`:

```python
"""The linkage analyst name must be a first-class member of AnalystName."""
from __future__ import annotations

from datetime import datetime

from contract.evidence import AnalystEvidence, AnalystVerdict


def test_analyst_evidence_accepts_linkage_analyst():
    """An AnalystEvidence tagged 'linkage' validates without error."""
    evidence = AnalystEvidence(
        ticker="AAPL",
        analyst="linkage",
        tick_id="t-1",
        recorded_at=datetime(2026, 2, 10, 14, 0),
        features={"n_events": 1.0},
        verdict=AnalystVerdict(
            lean="bullish",
            magnitude=0.4,
            confidence=0.6,
            rationale="Sector peer surprise drifts positive.",
        ),
    )

    assert evidence.analyst == "linkage"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_linkage_analyst_name.py -v`
Expected: FAIL with a Pydantic `ValidationError` — `"linkage"` is not a permitted `AnalystName` literal.

- [ ] **Step 3: Add `"linkage"` to the Literal**

In `src/contract/evidence.py`, change line 25 from:

```python
AnalystName = Literal["technical", "fundamental", "news", "social", "smart_money"]
```

to:

```python
AnalystName = Literal["technical", "fundamental", "news", "social", "smart_money", "linkage"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_linkage_analyst_name.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/contract/evidence.py tests/unit/contract/test_linkage_analyst_name.py
git commit -m "feat(contract): add linkage to the AnalystName literal (Phase 14 Plan 4)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Linkage Pydantic contracts (`schemas.py`)

**Files:**
- Create: `src/agents/analysts/linkage/__init__.py` (empty package marker)
- Create: `src/agents/analysts/linkage/schemas.py`
- Test: `tests/unit/agents/analysts/linkage/test_schemas.py`
- Create: `tests/unit/agents/analysts/linkage/__init__.py` (empty — package marker for the test tree)

**Interfaces:**
- Consumes: nothing.
- Produces (consumed by Tasks 5–10):
  - `LinkageEvent(summary: str, category: Literal["macro","sector","merger"], entities: list[str], surprise_direction: Literal["positive","negative","mixed","none"], novelty: Literal["new","developing","reiteration"])` — digester emit item, `extra="forbid"`.
  - `LinkageDigest(events: list[LinkageEvent])` — digester emit batch, `extra="forbid"`.
  - `LinkageMatch(ticker, lean, magnitude, confidence, horizon_days, channel, rationale, key_factors)` — matcher emit item, `extra="forbid"`.
  - `LinkageMatchBatch(matches: list[LinkageMatch])` — matcher emit batch, `extra="forbid"`.
  - `TickerExposure(ticker, sector, commodity_sensitivities, geographies, key_customers, key_suppliers, regulatory_exposure)` — one exposure-map row, `extra="forbid"`.
  - `ExposureMap(built_at: datetime, watchlist: list[str], exposures: dict[str, TickerExposure])`.

- [ ] **Step 1: Write the failing schema tests**

Create `tests/unit/agents/analysts/linkage/__init__.py` as an empty file, then create `tests/unit/agents/analysts/linkage/test_schemas.py`:

```python
"""Validation tests for the linkage Pydantic contracts (Phase 14 Plan 4).

These lock the emit shapes the digester and matcher must produce and the
exposure-map artefact shape.  Positive assertions: a well-formed payload
validates and round-trips; the free-text fields carry NO ``max_length``
(the Vertex pad-toward-cap pathology guard).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from agents.analysts.linkage.schemas import (
    ExposureMap,
    LinkageDigest,
    LinkageEvent,
    LinkageMatch,
    LinkageMatchBatch,
    TickerExposure,
)


def test_linkage_event_validates_a_well_formed_event():
    """A well-formed digester event validates and keeps its fields."""
    event = LinkageEvent(
        summary="Taiwan foundry capacity cut on quake disruption.",
        category="sector",
        entities=["TSMC", "semiconductors"],
        surprise_direction="negative",
        novelty="new",
    )

    assert event.category == "sector"
    assert event.surprise_direction == "negative"


def test_linkage_event_rejects_unknown_category():
    """Categories are closed-vocab — an unknown value is rejected loudly."""
    with pytest.raises(ValidationError):
        LinkageEvent(
            summary="x",
            category="geopolitics",  # not in {macro, sector, merger}
            entities=[],
            surprise_direction="negative",
            novelty="new",
        )


def test_linkage_event_forbids_extra_fields():
    """extra='forbid' — schema drift fails loudly, never silently drops."""
    with pytest.raises(ValidationError):
        LinkageEvent(
            summary="x",
            category="macro",
            entities=[],
            surprise_direction="none",
            novelty="new",
            tickers=["AAPL"],  # not a field on LinkageEvent
        )


def test_linkage_event_summary_has_no_length_cap():
    """A long summary is accepted — no max_length pad-target on free text."""
    long_summary = "word " * 4000
    event = LinkageEvent(
        summary=long_summary,
        category="macro",
        entities=[],
        surprise_direction="mixed",
        novelty="developing",
    )

    assert event.summary == long_summary


def test_linkage_digest_batches_events():
    """The digest batch wraps a list of events (may be empty)."""
    digest = LinkageDigest(events=[])
    assert digest.events == []


def test_linkage_match_validates_and_carries_horizon_days():
    """A matcher output carries a per-verdict horizon >= 1."""
    match = LinkageMatch(
        ticker="AAPL",
        lean="bullish",
        magnitude=0.4,
        confidence=0.6,
        horizon_days=21,
        channel="supplier: shares TSMC foundry exposure",
        rationale="Positive sector surprise drifts into AAPL via its foundry supplier.",
        key_factors=["channel:supplier"],
    )

    assert match.horizon_days == 21
    assert match.lean == "bullish"


def test_linkage_match_rejects_zero_horizon():
    """horizon_days ge=1 — a zero horizon is meaningless and rejected."""
    with pytest.raises(ValidationError):
        LinkageMatch(
            ticker="AAPL",
            lean="bullish",
            magnitude=0.4,
            confidence=0.6,
            horizon_days=0,
            channel="x",
            rationale="y",
        )


def test_linkage_match_batch_wraps_matches():
    """The matcher batch wraps a list of matches (may be empty)."""
    batch = LinkageMatchBatch(matches=[])
    assert batch.matches == []


def test_exposure_map_round_trips_through_json():
    """The exposure-map artefact serialises datetimes to ISO for on-disk JSON."""
    exposure = TickerExposure(
        ticker="AAPL",
        sector="Technology Hardware",
        commodity_sensitivities=["rare earths"],
        geographies=["China", "United States"],
        key_customers=[],
        key_suppliers=["TSMC"],
        regulatory_exposure=["US export controls"],
    )
    exposure_map = ExposureMap(
        built_at=datetime(2026, 7, 1, 9, 0),
        watchlist=["AAPL"],
        exposures={"AAPL": exposure},
    )

    payload = exposure_map.model_dump(mode="json")

    assert isinstance(payload["built_at"], str)
    assert payload["exposures"]["AAPL"]["key_suppliers"] == ["TSMC"]
    # Round-trips back into the model.
    assert ExposureMap.model_validate(payload).exposures["AAPL"].sector == "Technology Hardware"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_schemas.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage'`.

- [ ] **Step 3: Create the package marker and `schemas.py`**

Create `src/agents/analysts/linkage/__init__.py` as an empty file. Create `src/agents/analysts/linkage/schemas.py` with exactly this content:

```python
"""Pydantic contracts for the linkage analyst (Phase 14 Plan 4).

Three families of shape:

  * **Digester emit** — ``LinkageDigest`` wraps a list of ``LinkageEvent``,
    the normalised events the flash-class digester distils from novel macro
    articles.  Closed-vocab where possible (``category``,
    ``surprise_direction``, ``novelty``).
  * **Matcher emit** — ``LinkageMatchBatch`` wraps a list of ``LinkageMatch``,
    one per exposed ticker, each carrying a ``horizon_days`` drift window.
  * **Exposure map** — ``ExposureMap`` is the offline-built per-ticker
    channel artefact the matcher reads.

All emit schemas set ``extra="forbid"`` so any drift between a schema and a
stale prompt fails loudly rather than silently dropping fields.  Free-text
fields carry NO ``max_length`` — Vertex's constrained decoder treats a
schema ``maxLength`` as a fill target; prose caps are stated in the prompt.
List fields carry a count bound (``max_length``) where sensible — a count
bound is not the pad-target pathology.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LinkageEvent(BaseModel):
    """One normalised event distilled by the digester from macro news.

    Attributes
    ----------
    summary:
        Free-text one-to-two sentence description of the event. No
        ``max_length`` — the prompt states the length bound.
    category:
        Closed-vocab bucket: ``macro`` (economy-wide), ``sector``
        (industry-wide), or ``merger`` (deal / corporate action).
    entities:
        Named organisations, commodities, or places the event concerns
        (free-text tags; count-bounded).
    surprise_direction:
        Directional surprise relative to expectations —
        ``positive``/``negative``/``mixed``/``none``.
    novelty:
        Whether this is a ``new`` story, a ``developing`` continuation, or a
        ``reiteration`` of already-known information.
    """

    model_config = ConfigDict(extra="forbid")

    summary:            str
    category:           Literal["macro", "sector", "merger"]
    entities:           list[str] = Field(default_factory=list, max_length=12)
    surprise_direction: Literal["positive", "negative", "mixed", "none"]
    novelty:            Literal["new", "developing", "reiteration"]


class LinkageDigest(BaseModel):
    """Digester emit batch — the events extracted from this tick's novel news.

    Attributes
    ----------
    events:
        Zero or more ``LinkageEvent`` records. Empty is a legitimate emit on
        a tick whose novel articles carry no drift-relevant event.
    """

    model_config = ConfigDict(extra="forbid")

    events: list[LinkageEvent] = Field(default_factory=list)


class LinkageMatch(BaseModel):
    """One per-ticker linkage call emitted by the matcher.

    Attributes
    ----------
    ticker:
        Watchlist ticker the linkage applies to.
    lean:
        Directional lean — ``bullish``/``bearish``/``neutral``.
    magnitude:
        Strength of the lean in [0, 1].
    confidence:
        Confidence in the lean in [0, 1].
    horizon_days:
        Trading days the drift lean is expected to hold (>= 1) — sourced
        from the driving event's registry horizon.
    channel:
        Free-text description of the exposure channel that carried the event
        to this ticker (e.g. "supplier: shares TSMC foundry exposure"). No
        ``max_length`` — prompt-stated bound.
    rationale:
        Free-text justification. No ``max_length`` — prompt-stated bound.
    key_factors:
        Closed-vocab-ish tags (count-bounded) for machine aggregation.
    """

    model_config = ConfigDict(extra="forbid")

    ticker:       str
    lean:         Literal["bullish", "bearish", "neutral"]
    magnitude:    float = Field(ge=0.0, le=1.0)
    confidence:   float = Field(ge=0.0, le=1.0)
    horizon_days: int   = Field(ge=1)
    channel:      str
    rationale:    str
    key_factors:  list[str] = Field(default_factory=list, max_length=8)


class LinkageMatchBatch(BaseModel):
    """Matcher emit batch — one match per exposed ticker.

    Attributes
    ----------
    matches:
        Zero or more ``LinkageMatch`` records. Empty is a legitimate emit on
        a tick where no watchlist ticker is exposed to any active event.
    """

    model_config = ConfigDict(extra="forbid")

    matches: list[LinkageMatch] = Field(default_factory=list)


class TickerExposure(BaseModel):
    """The economic-link channels for one watchlist ticker.

    Attributes
    ----------
    ticker:
        The ticker this row describes.
    sector:
        Human-readable sector / industry label.
    commodity_sensitivities:
        Commodities whose price moves materially affect the firm.
    geographies:
        Countries / regions of material revenue or supply exposure.
    key_customers:
        Named material customers (economic-link drift sources).
    key_suppliers:
        Named material suppliers.
    regulatory_exposure:
        Regulatory regimes / bodies with material influence over the firm.
    """

    model_config = ConfigDict(extra="forbid")

    ticker:                  str
    sector:                  str
    commodity_sensitivities: list[str] = Field(default_factory=list, max_length=20)
    geographies:             list[str] = Field(default_factory=list, max_length=20)
    key_customers:           list[str] = Field(default_factory=list, max_length=20)
    key_suppliers:           list[str] = Field(default_factory=list, max_length=20)
    regulatory_exposure:     list[str] = Field(default_factory=list, max_length=20)


class ExposureMap(BaseModel):
    """The offline-built per-ticker exposure artefact read by the matcher.

    Attributes
    ----------
    built_at:
        When the map was built (used for the staleness-cap check). Serialised
        to an ISO string in the on-disk JSON via ``model_dump(mode="json")``.
    watchlist:
        The tickers the map covers — a change vs the live watchlist forces a
        rebuild (loud failure on the tick path).
    exposures:
        Ticker → ``TickerExposure``.
    """

    built_at:  datetime
    watchlist: list[str]
    exposures: dict[str, TickerExposure]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_schemas.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/linkage/__init__.py src/agents/analysts/linkage/schemas.py tests/unit/agents/analysts/linkage/__init__.py tests/unit/agents/analysts/linkage/test_schemas.py
git commit -m "feat(linkage): add Pydantic contracts for events, matches, and exposure map

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Event registry — persistence row + per-run store with horizon decay

**Design note (resolved ambiguity — read before implementing).** The spec asks for a SQLite `linkage_events` table *and* for the registry to be "per-run/per-window in backtests, never persisted across windows (mirror Plan 2's Driver.run reset pattern)". These are reconciled with **two roles, not two sources of truth in tension**:

1. **Per-run in-memory registry** (`LinkageEventRegistry`, this task) is the **active-events source of truth** the matcher reads. It is a process-global singleton reset per window replay via `reset_linkage_registry()` — the *exact* shape of Plan 2's `NewsHistoryStore`, so the Driver.run reset (Task 14) is a pure singleton swap with no db-session coupling.
2. **SQLite `linkage_events` table** (this task) is the **durable, inspectable audit record** written through transactionally with the tick by the `LinkageRegistryAgent` (Task 8), sitting in the same run db as `analyst_evidence`. Backtest runs already use a fresh db per window, so the table is naturally PIT-isolated; the in-memory reset is the belt-and-braces PIT guarantee the spec asks for.

The registry class stays **pure in-memory** (no session dependency) so it is unit-testable without a database; the write-through lives in the agent.

**Files:**
- Modify: `src/orchestrator/persistence.py` (add `LinkageEventRow` + `save_linkage_event` + `load_linkage_events`)
- Create: `src/agents/analysts/linkage/registry.py`
- Test: `tests/unit/agents/analysts/linkage/test_registry.py`
- Test: `tests/unit/orchestrator/test_linkage_event_persistence.py`

**Interfaces:**
- Consumes: nothing from earlier linkage tasks (uses `sqlalchemy` primitives already imported in `persistence.py`).
- Produces (consumed by Tasks 8, 14):
  - `persistence.LinkageEventRow` (ORM), `persistence.save_linkage_event(session, entry: dict) -> None` (idempotent by `event_id` via `session.merge`), `persistence.load_linkage_events(session) -> list[dict]`.
  - `registry.RegisteredEvent` dataclass `(event_id, summary, category, tickers, direction, event_date, horizon_days, source_article_ids)`.
  - `registry.make_event_id(summary: str, event_date: datetime, category: str) -> str` (deterministic).
  - `registry.LinkageEventRegistry` with `record(event: RegisteredEvent) -> bool` (True if newly added, False if the `event_id` was already present — idempotent) and `active_events(as_of: datetime) -> list[RegisteredEvent]` (events whose `event_date + horizon_days` calendar days has not passed `as_of`).
  - `registry.get_linkage_registry() -> LinkageEventRegistry` / `registry.reset_linkage_registry() -> None` (module singleton, mirrors Plan 2).

- [ ] **Step 1: Write the failing registry tests**

Create `tests/unit/agents/analysts/linkage/test_registry.py`:

```python
"""Unit tests for the per-run linkage event registry (Phase 14 Plan 4).

The registry is the matcher's active-events source of truth: it holds
normalised events keyed by a deterministic ``event_id`` and answers "which
events are still inside their drift horizon as of this tick?".  Expiry is
by calendar days (``event_date + horizon_days``) — a deliberate simple
approximation of the trading-day horizon that keeps the registry free of an
NYSE-calendar dependency.
"""
from __future__ import annotations

from datetime import datetime

from agents.analysts.linkage.registry import (
    LinkageEventRegistry,
    RegisteredEvent,
    get_linkage_registry,
    make_event_id,
    reset_linkage_registry,
)


def _event(summary: str, event_date: datetime, horizon_days: int) -> RegisteredEvent:
    """Build a RegisteredEvent with a deterministic id for tests.

    Parameters
    ----------
    summary:
        Event summary text (drives the id hash).
    event_date:
        The event's registration date.
    horizon_days:
        Drift horizon in days.

    Returns
    -------
    RegisteredEvent
        A populated event.
    """
    return RegisteredEvent(
        event_id=make_event_id(summary, event_date, "sector"),
        summary=summary,
        category="sector",
        tickers=["AAPL"],
        direction="negative",
        event_date=event_date,
        horizon_days=horizon_days,
        source_article_ids=["https://news/a1"],
    )


def test_make_event_id_is_deterministic_and_distinguishing():
    """Same inputs → same id; a different date → a different id."""
    d1 = datetime(2026, 2, 10, 14, 0)
    d2 = datetime(2026, 2, 11, 14, 0)

    assert make_event_id("x", d1, "sector") == make_event_id("x", d1, "sector")
    assert make_event_id("x", d1, "sector") != make_event_id("x", d2, "sector")


def test_record_is_idempotent_by_event_id():
    """Re-recording the same event returns False and does not duplicate it."""
    registry = LinkageEventRegistry()
    event = _event("Foundry outage", datetime(2026, 2, 10), horizon_days=21)

    assert registry.record(event) is True
    assert registry.record(event) is False
    assert len(registry.active_events(datetime(2026, 2, 10))) == 1


def test_active_events_includes_events_inside_the_horizon():
    """An event is active up to and including its expiry day."""
    registry = LinkageEventRegistry()
    registry.record(_event("Foundry outage", datetime(2026, 2, 10), horizon_days=21))

    # 20 calendar days later — still inside the 21-day horizon.
    active = registry.active_events(datetime(2026, 3, 2))

    assert len(active) == 1
    assert active[0].summary == "Foundry outage"


def test_active_events_excludes_expired_events():
    """Past the horizon, the event drops out of the active set (decay)."""
    registry = LinkageEventRegistry()
    registry.record(_event("Foundry outage", datetime(2026, 2, 10), horizon_days=21))

    # 30 calendar days later — well past the 21-day horizon.
    active = registry.active_events(datetime(2026, 3, 12))

    assert active == []


def test_active_events_are_returned_oldest_first():
    """Deterministic ordering — oldest event_date first."""
    registry = LinkageEventRegistry()
    registry.record(_event("Older", datetime(2026, 2, 10), horizon_days=30))
    registry.record(_event("Newer", datetime(2026, 2, 20), horizon_days=30))

    summaries = [e.summary for e in registry.active_events(datetime(2026, 2, 25))]

    assert summaries == ["Older", "Newer"]


def test_singleton_reset_swaps_the_instance():
    """reset_linkage_registry() hands back a fresh empty registry (PIT reset)."""
    first = get_linkage_registry()
    first.record(_event("Leftover", datetime(2026, 2, 10), horizon_days=21))

    reset_linkage_registry()
    second = get_linkage_registry()

    assert second is not first
    assert second.active_events(datetime(2026, 2, 10)) == []

    # Leave no shared state behind.
    reset_linkage_registry()
```

- [ ] **Step 2: Run the registry tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_registry.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.registry'`.

- [ ] **Step 3: Create `src/agents/analysts/linkage/registry.py`**

```python
"""Per-run linkage event registry with horizon decay (Phase 14 Plan 4).

The registry is the matcher's active-events source of truth.  Normalised
events (distilled by the digester, enriched with tickers/direction/horizon
by the registry stage) are held keyed by a deterministic ``event_id`` so a
re-observed event on a later tick does not duplicate.  ``active_events``
answers the drift-window question: which events are still inside their
horizon as of the current tick?

Lifecycle — PIT correctness (spec §6.3):
    The registry is strictly PER-RUN state, mirroring Plan 2's
    ``NewsHistoryStore``.  Live trading accumulates events within one process
    run; the backtest driver calls ``reset_linkage_registry()`` at the start
    of every window replay so events never leak across windows.  Durable
    audit rows are written separately to SQLite by the registry AGENT (see
    ``persistence.save_linkage_event``); this in-memory store is the fast,
    resettable active-events source.

Expiry approximation:
    Horizons are expressed in trading days but expiry here is computed in
    CALENDAR days (``event_date + horizon_days``).  This keeps the registry
    free of an NYSE-calendar dependency; the slight window-shortening is
    immaterial to drift positioning and is deliberate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import blake2b


def make_event_id(summary: str, event_date: datetime, category: str) -> str:
    """Return a stable identity hash for an event.

    Two events with the same summary text, registration date, and category
    are the same event — re-observing it on a later tick must not duplicate
    it.  The date is included so a recurring headline on different days does
    not collide.

    Parameters
    ----------
    summary:
        The event's summary text.
    event_date:
        The event's registration date (the tick's ``as_of``).
    category:
        The event category (``macro``/``sector``/``merger``).

    Returns
    -------
    str
        A ``"evt:<blake2b-digest>"`` identity key.
    """
    digest = blake2b(
        f"{summary}|{event_date.isoformat()}|{category}".encode(),
        digest_size=12,
    ).hexdigest()

    return f"evt:{digest}"


@dataclass
class RegisteredEvent:
    """One event held in the registry, enriched with drift metadata.

    Attributes
    ----------
    event_id:
        Deterministic identity (see :func:`make_event_id`).
    summary:
        Free-text event description (from the digester).
    category:
        ``macro``/``sector``/``merger``.
    tickers:
        Watchlist tickers the source macro articles mentioned (deterministic;
        the matcher may still link the event to further tickers via the
        exposure map).
    direction:
        The event's surprise direction (from the digester).
    event_date:
        Registration date — the tick's ``as_of`` when first recorded.
    horizon_days:
        Drift horizon in days (per-category, from ``config/linkage.json``).
    source_article_ids:
        Stable article keys of the macro articles that produced the event.
    """

    event_id:           str
    summary:            str
    category:           str
    tickers:            list[str]
    direction:          str
    event_date:         datetime
    horizon_days:       int
    source_article_ids: list[str] = field(default_factory=list)


class LinkageEventRegistry:
    """In-memory, per-run store of registered events with horizon decay."""

    def __init__(self) -> None:
        """Initialise an empty registry."""
        # event_id → RegisteredEvent.  A dict preserves insertion order, but
        # active_events re-sorts by event_date for deterministic output.
        self._events: dict[str, RegisteredEvent] = {}

    def record(self, event: RegisteredEvent) -> bool:
        """Record ``event`` unless its id is already present.

        Idempotent per ``event_id`` — recording an already-known event is a
        no-op, so a drift event observed across many ticks is stored once.

        Parameters
        ----------
        event:
            The event to record.

        Returns
        -------
        bool
            ``True`` if the event was newly added; ``False`` if the id was
            already present.
        """
        if event.event_id in self._events:
            return False

        self._events[event.event_id] = event
        return True

    def active_events(self, as_of: datetime) -> list[RegisteredEvent]:
        """Return events still inside their drift horizon at ``as_of``.

        An event is active while ``as_of <= event_date + horizon_days`` (in
        calendar days).  Results are sorted oldest-first for deterministic
        matcher input.

        Parameters
        ----------
        as_of:
            The current tick timestamp.

        Returns
        -------
        list[RegisteredEvent]
            Active events, oldest ``event_date`` first.
        """
        active = [
            event
            for event in self._events.values()
            if as_of <= event.event_date + timedelta(days=event.horizon_days)
        ]

        return sorted(active, key=lambda e: e.event_date)


# ── Module-level per-run singleton ────────────────────────────────────────
#
# Mirrors Plan 2's NewsHistoryStore: one registry per process run, reset by
# the backtest driver before each window replay.

_REGISTRY: LinkageEventRegistry | None = None


def get_linkage_registry() -> LinkageEventRegistry:
    """Return the process-wide per-run registry, creating it on first use.

    Returns
    -------
    LinkageEventRegistry
        The shared registry for the current run.
    """
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = LinkageEventRegistry()
    return _REGISTRY


def reset_linkage_registry() -> None:
    """Discard the current registry so the next access builds a fresh one.

    Called by the backtest driver before each window replay — events must
    never leak across windows or repeated runs in one process (PIT
    correctness, spec §6.3).

    Returns
    -------
    None
    """
    global _REGISTRY
    _REGISTRY = None
```

- [ ] **Step 4: Run the registry tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_registry.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Write the failing persistence test**

Create `tests/unit/orchestrator/test_linkage_event_persistence.py`:

```python
"""Durable audit persistence for linkage events (Phase 14 Plan 4).

The in-memory registry is the active-events source of truth; this SQLite
table is the durable, inspectable audit record written through with the
tick.  save is idempotent by event_id (merge) so a re-observed event does
not error on a second tick.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import (
    Base,
    load_linkage_events,
    save_linkage_event,
)


def _session():
    """Build an in-memory SQLite session with the schema created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _entry(event_id: str) -> dict:
    """Build a linkage-event row dict for persistence."""
    return {
        "event_id": event_id,
        "summary": "Foundry outage disrupts sector supply.",
        "category": "sector",
        "tickers": ["AAPL", "NVDA"],
        "direction": "negative",
        "event_date": datetime(2026, 2, 10, 14, 0),
        "horizon_days": 21,
        "source_article_ids": ["https://news/a1"],
    }


def test_save_and_load_round_trips_lists_as_json():
    """List columns round-trip through JSON encoding."""
    session = _session()
    save_linkage_event(session, _entry("evt:1"))
    session.commit()

    rows = load_linkage_events(session)

    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt:1"
    assert rows[0]["tickers"] == ["AAPL", "NVDA"]
    assert rows[0]["source_article_ids"] == ["https://news/a1"]


def test_save_is_idempotent_by_event_id():
    """Re-saving the same event_id merges rather than raising a PK clash."""
    session = _session()
    save_linkage_event(session, _entry("evt:1"))
    save_linkage_event(session, _entry("evt:1"))
    session.commit()

    assert len(load_linkage_events(session)) == 1
```

- [ ] **Step 6: Run the persistence test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/orchestrator/test_linkage_event_persistence.py -v`
Expected: FAIL with `ImportError` (`save_linkage_event` / `load_linkage_events` not defined).

- [ ] **Step 7: Add the ORM row and helpers to `persistence.py`**

In `src/orchestrator/persistence.py`, add `import json` is already present at the top. Add this block after the `TradeLogRow` / `save_trade_log_entry` section (anywhere among the row definitions):

```python
# ── LinkageEventRow ───────────────────────────────────────────────────

class LinkageEventRow(Base):
    """One registered linkage event — durable audit record (Phase 14 Plan 4).

    The in-memory ``LinkageEventRegistry`` is the matcher's active-events
    source of truth; this table is the inspectable audit trail written
    through transactionally with the tick.  ``event_id`` is the primary key
    so a re-observed event merges rather than duplicating.
    """

    __tablename__ = "linkage_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    summary: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, index=True)
    # JSON-encoded list[str] — SQLite has no native array type.
    tickers: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String)
    event_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer)
    # JSON-encoded list[str] of source article keys.
    source_article_ids: Mapped[str] = mapped_column(String)


def save_linkage_event(session: Session, entry: dict) -> None:
    """Persist (or merge) one linkage-event audit row.

    Idempotent by ``event_id`` via ``session.merge`` so a drift event
    re-observed on a later tick updates in place rather than raising a
    primary-key clash.  List fields are JSON-encoded for SQLite storage.
    The caller is responsible for committing.

    Parameters
    ----------
    session:
        Active SQLAlchemy session.
    entry:
        Dict with keys ``event_id``, ``summary``, ``category``, ``tickers``
        (list), ``direction``, ``event_date`` (datetime), ``horizon_days``
        (int), ``source_article_ids`` (list).

    Returns
    -------
    None
    """
    row = LinkageEventRow(
        event_id=entry["event_id"],
        summary=entry["summary"],
        category=entry["category"],
        tickers=json.dumps(entry["tickers"]),
        direction=entry["direction"],
        event_date=entry["event_date"],
        horizon_days=entry["horizon_days"],
        source_article_ids=json.dumps(entry["source_article_ids"]),
    )

    # merge() upserts on the primary key, so re-observing an event is safe.
    session.merge(row)
    session.flush()


def load_linkage_events(session: Session) -> list[dict]:
    """Read all linkage-event audit rows back as dicts (JSON lists decoded).

    Parameters
    ----------
    session:
        Active SQLAlchemy session.

    Returns
    -------
    list[dict]
        One dict per row with ``tickers`` and ``source_article_ids`` decoded
        back into lists.
    """
    rows = session.query(LinkageEventRow).all()

    return [
        {
            "event_id": row.event_id,
            "summary": row.summary,
            "category": row.category,
            "tickers": json.loads(row.tickers),
            "direction": row.direction,
            "event_date": row.event_date,
            "horizon_days": row.horizon_days,
            "source_article_ids": json.loads(row.source_article_ids),
        }
        for row in rows
    ]
```

- [ ] **Step 8: Run both test files to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_registry.py tests/unit/orchestrator/test_linkage_event_persistence.py -v`
Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agents/analysts/linkage/registry.py src/orchestrator/persistence.py tests/unit/agents/analysts/linkage/test_registry.py tests/unit/orchestrator/test_linkage_event_persistence.py
git commit -m "feat(linkage): add per-run event registry with horizon decay + SQLite audit row

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Exposure map — shared LLM seam, loader/freshness, and offline builder script

**Design note.** The exposure map is the only knowledge-heavy, look-ahead-prone artefact (spec §9). It is built **offline**, **never on the tick path**, and loaded read-only. The tick path fails loudly if the map is stale (spec §7). This task also introduces the **shared injectable `llm_fn` seam** (`make_default_llm_fn`) that Tasks 7 and 9 reuse for the digester and matcher — one boundary, three call sites, all stub-injectable in tests.

**Files:**
- Create: `src/agents/analysts/linkage/llm.py` (the shared `make_default_llm_fn` seam)
- Create: `src/agents/analysts/linkage/exposure.py` (loader, freshness assert, builder, save)
- Create: `scripts/build_exposure_map.py` (offline CLI entrypoint)
- Test: `tests/unit/agents/analysts/linkage/test_llm.py`
- Test: `tests/unit/agents/analysts/linkage/test_exposure.py`

**Interfaces:**
- Consumes: `schemas.TickerExposure`, `schemas.ExposureMap` (Task 3); `config.linkage.get_linkage_config` (Task 1); `config.models.get_models_config` (Task 1); `agents.thinking_config.build_thinking_config`; `orchestrator.stock_picker.get_watchlist`.
- Produces (consumed by Tasks 7, 9, 11):
  - `llm.make_default_llm_fn(*, model, max_output_tokens, thinking_level, temperature, timeout_seconds, timeout_retries, schema_retries, client_factory=None) -> Callable[[str, type[BaseModel]], Awaitable[BaseModel]]` — a default async `llm_fn(prompt, schema)` grounded in `google.genai` structured output, with a bounded retry loop.
  - `exposure.load_exposure_map(path: str | Path) -> ExposureMap` (raises `FileNotFoundError` loudly).
  - `exposure.assert_exposure_map_fresh(exposure_map: ExposureMap, as_of: datetime, cap_days: int) -> None` (raises `StaleExposureMapError` when past the cap).
  - `exposure.StaleExposureMapError(RuntimeError)`.
  - `async exposure.build_exposure_map(watchlist: list[str], *, llm_fn, built_at: datetime | None = None) -> ExposureMap`.
  - `exposure.save_exposure_map(exposure_map: ExposureMap, path: str | Path) -> None`.

- [ ] **Step 1: Write the failing LLM-seam tests**

Create `tests/unit/agents/analysts/linkage/test_llm.py`:

```python
"""Unit tests for the shared linkage LLM seam (Phase 14 Plan 4).

The default ``llm_fn`` grounds the digester, matcher, and exposure builder
in one place.  Tests inject a fake genai client via ``client_factory`` so no
network call happens, and assert the positive path (valid JSON parses to the
schema), the schema-retry path, and loud exhaustion.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from agents.analysts.linkage.llm import make_default_llm_fn


class _Toy(BaseModel):
    """Minimal schema for exercising the seam."""

    value: int


class _FakeResponse:
    """Stand-in for a genai GenerateContentResponse (only ``.text`` is read)."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    """Fake ``client.models`` returning queued responses in order."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0

    def generate_content(self, *, model, contents, config):  # noqa: ANN001
        """Return the next queued response text, counting calls."""
        self.calls += 1
        return _FakeResponse(self._texts.pop(0))


class _FakeClient:
    """Fake genai client exposing a ``.models`` with queued responses."""

    def __init__(self, texts: list[str]) -> None:
        self.models = _FakeModels(texts)


def test_default_llm_fn_parses_valid_json():
    """A valid JSON body parses straight into the target schema."""
    client = _FakeClient(['{"value": 7}'])
    llm_fn = make_default_llm_fn(
        model="fake-model",
        max_output_tokens=512,
        thinking_level="low",
        temperature=0.2,
        timeout_seconds=5,
        timeout_retries=0,
        schema_retries=0,
        client_factory=lambda: client,
    )

    result = asyncio.run(llm_fn("prompt", _Toy))

    assert result == _Toy(value=7)
    assert client.models.calls == 1


def test_default_llm_fn_retries_on_schema_error_then_succeeds():
    """A malformed body is retried up to schema_retries and then parses."""
    client = _FakeClient(["not json", '{"value": 3}'])
    llm_fn = make_default_llm_fn(
        model="fake-model",
        max_output_tokens=512,
        thinking_level="low",
        temperature=0.2,
        timeout_seconds=5,
        timeout_retries=0,
        schema_retries=1,
        client_factory=lambda: client,
    )

    result = asyncio.run(llm_fn("prompt", _Toy))

    assert result == _Toy(value=3)
    assert client.models.calls == 2


def test_default_llm_fn_raises_loudly_when_schema_retries_exhausted():
    """Persistent malformed output raises rather than degrading to a default."""
    client = _FakeClient(["nope", "still nope"])
    llm_fn = make_default_llm_fn(
        model="fake-model",
        max_output_tokens=512,
        thinking_level="low",
        temperature=0.2,
        timeout_seconds=5,
        timeout_retries=0,
        schema_retries=1,
        client_factory=lambda: client,
    )

    with pytest.raises(ValueError):
        asyncio.run(llm_fn("prompt", _Toy))
```

- [ ] **Step 2: Run the LLM-seam tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_llm.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.llm'`.

- [ ] **Step 3: Create `src/agents/analysts/linkage/llm.py`**

```python
"""Shared injectable LLM seam for the linkage stages (Phase 14 Plan 4).

The digester, matcher, and offline exposure builder all issue exactly one
structured-output call.  Rather than each embedding a genai client, they
accept an injectable ``async llm_fn(prompt, schema) -> BaseModel`` (the
testable seam, mirroring Plan 2's ``embed_fn``).  ``make_default_llm_fn``
builds the production default, grounded in ``google.genai`` structured
output with a bounded retry loop.

Loud-failure policy: the retry loop distinguishes a **timeout** (retried up
to ``timeout_retries``) from a **schema-validation failure** (retried up to
``schema_retries``).  When either budget is exhausted the underlying
exception propagates unchanged — the caller wraps the stage in
``IsolatedFailureWrapper`` so an exhausted call fails loudly at the isolation
boundary rather than degrading to an empty result.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from agents.thinking_config import build_thinking_config

# The seam type every linkage stage consumes.
LlmFn = Callable[[str, type[BaseModel]], Awaitable[BaseModel]]


def make_default_llm_fn(
    *,
    model: str,
    max_output_tokens: int,
    thinking_level: str,
    temperature: float,
    timeout_seconds: int,
    timeout_retries: int,
    schema_retries: int,
    client_factory: Callable[[], Any] | None = None,
) -> LlmFn:
    """Build the production default ``llm_fn`` for a linkage stage.

    Parameters
    ----------
    model:
        Resolved model ID (from ``config/models.json``).
    max_output_tokens:
        Output-token ceiling for the generation config.
    thinking_level:
        Gemini-3 thinking effort enum, threaded through ``build_thinking_config``.
    temperature:
        Sampling temperature.
    timeout_seconds:
        Wall-clock bound for a single call.
    timeout_retries:
        Retries permitted on a timed-out call.
    schema_retries:
        Retries permitted on a schema-validation failure.
    client_factory:
        Optional zero-arg factory returning a genai-style client (for tests).
        Defaults to constructing a real ``google.genai.Client``.

    Returns
    -------
    LlmFn
        An ``async llm_fn(prompt, schema) -> BaseModel`` closure.
    """

    def _make_client() -> Any:
        """Construct the genai client lazily so imports stay cheap in tests."""
        if client_factory is not None:
            return client_factory()

        from google import genai  # type: ignore[import]

        return genai.Client()

    async def _llm_fn(prompt: str, schema: type[BaseModel]) -> BaseModel:
        """Issue one structured-output call, parsing the body into ``schema``.

        Retries a timeout up to ``timeout_retries`` and a schema-validation
        failure up to ``schema_retries``; re-raises once either budget is
        spent (loud failure).

        Parameters
        ----------
        prompt:
            The fully-rendered prompt text.
        schema:
            The Pydantic model the JSON body must validate against.

        Returns
        -------
        BaseModel
            A validated instance of ``schema``.
        """
        from google.genai import types as genai_types  # type: ignore[import]

        client = _make_client()

        # Generation config is identical across attempts; only the response
        # varies.  ``response_schema`` steers the model to emit JSON matching
        # the target shape; we still re-validate the text ourselves so a
        # malformed body is a loud, retryable error rather than a silent None.
        config = genai_types.GenerateContentConfig(
            max_output_tokens = max_output_tokens,
            temperature       = temperature,
            thinking_config   = build_thinking_config(
                thinking_budget = None,
                thinking_level  = thinking_level,
            ),
            response_mime_type = "application/json",
            response_schema    = schema,
        )

        timeouts_left = timeout_retries
        schema_left   = schema_retries

        while True:
            try:
                # Run the blocking SDK call in a worker thread so the tick's
                # event loop is not stalled, bounded by the wall-clock timeout.
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model    = model,
                        contents = prompt,
                        config   = config,
                    ),
                    timeout = timeout_seconds,
                )

                # Parse-and-validate ourselves — raises ValidationError on a
                # malformed body, which the schema-retry branch handles.
                return schema.model_validate_json(response.text)

            except (asyncio.TimeoutError, TimeoutError):
                if timeouts_left <= 0:
                    raise
                timeouts_left -= 1

            except (ValidationError, ValueError):
                if schema_left <= 0:
                    raise
                schema_left -= 1

    return _llm_fn
```

- [ ] **Step 4: Run the LLM-seam tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_llm.py -v`
Expected: all 3 tests PASS. (`model_validate_json` raises `ValidationError`, a subclass of `ValueError`, so the malformed-body cases are caught by the `ValueError` branch.)

- [ ] **Step 5: Write the failing exposure tests**

Create `tests/unit/agents/analysts/linkage/test_exposure.py`:

```python
"""Unit tests for the exposure-map loader, freshness gate, and builder."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from agents.analysts.linkage.exposure import (
    StaleExposureMapError,
    assert_exposure_map_fresh,
    build_exposure_map,
    load_exposure_map,
    save_exposure_map,
)
from agents.analysts.linkage.schemas import ExposureMap, TickerExposure


def _map() -> ExposureMap:
    """Build a one-ticker exposure map for round-trip tests."""
    return ExposureMap(
        built_at=datetime(2026, 7, 1, 9, 0),
        watchlist=["AAPL"],
        exposures={
            "AAPL": TickerExposure(
                ticker="AAPL",
                sector="Technology Hardware",
                commodity_sensitivities=["rare earths"],
                geographies=["China", "United States"],
                key_customers=[],
                key_suppliers=["TSMC"],
                regulatory_exposure=["US export controls"],
            )
        },
    )


def test_save_and_load_round_trips(tmp_path: Path):
    """A saved map loads back byte-for-byte equal."""
    path = tmp_path / "exposure_map.json"
    save_exposure_map(_map(), path)

    loaded = load_exposure_map(path)

    assert loaded == _map()
    assert loaded.exposures["AAPL"].key_suppliers == ["TSMC"]


def test_load_missing_file_raises_loudly(tmp_path: Path):
    """A missing artefact raises rather than returning an empty map."""
    with pytest.raises(FileNotFoundError):
        load_exposure_map(tmp_path / "does_not_exist.json")


def test_freshness_gate_passes_within_cap():
    """A map younger than the cap (live) passes the freshness gate."""
    # built 2026-07-01, tick 2026-07-05, cap 7 days → fresh.
    assert_exposure_map_fresh(_map(), datetime(2026, 7, 5), cap_days=7) is None


def test_freshness_gate_passes_for_backtest_past_ticks():
    """A backtest tick before built_at never trips the cap."""
    # tick 2026-02-01 precedes built_at 2026-07-01 → age is negative → fresh.
    assert_exposure_map_fresh(_map(), datetime(2026, 2, 1), cap_days=7) is None


def test_freshness_gate_raises_past_cap():
    """A map older than the cap fails the tick loudly (spec §7)."""
    with pytest.raises(StaleExposureMapError):
        assert_exposure_map_fresh(_map(), datetime(2026, 7, 20), cap_days=7)


def test_build_exposure_map_produces_populated_exposures():
    """The builder emits one populated TickerExposure per watchlist ticker."""

    async def _stub_llm_fn(prompt: str, schema: type[BaseModel]) -> BaseModel:
        """Return a fixed populated exposure regardless of prompt."""
        assert schema is TickerExposure
        # The prompt must name the ticker so the builder is asking per-ticker.
        ticker = "AAPL" if "AAPL" in prompt else "MSFT"
        return TickerExposure(
            ticker=ticker,
            sector="Technology",
            commodity_sensitivities=["energy"],
            geographies=["United States"],
            key_customers=["consumers"],
            key_suppliers=["TSMC"],
            regulatory_exposure=["antitrust"],
        )

    built = asyncio.run(
        build_exposure_map(
            ["AAPL", "MSFT"],
            llm_fn=_stub_llm_fn,
            built_at=datetime(2026, 7, 1, 9, 0),
        )
    )

    assert built.watchlist == ["AAPL", "MSFT"]
    assert set(built.exposures) == {"AAPL", "MSFT"}
    # Positive-signal assertion: exposures are actually populated.
    assert built.exposures["AAPL"].sector == "Technology"
    assert built.exposures["MSFT"].key_suppliers == ["TSMC"]
```

- [ ] **Step 6: Run the exposure tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_exposure.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.exposure'`.

- [ ] **Step 7: Create `src/agents/analysts/linkage/exposure.py`**

```python
"""Exposure map — offline builder, loader, and tick-path freshness gate.

The exposure map is a per-ticker artefact describing the economic-links
channels (sector, commodity sensitivities, geographies, key customers and
suppliers, regulatory exposure) the matcher crosses events against.  It is:

  * built **offline** by ``scripts/build_exposure_map.py`` (one deeper-model
    pass per ticker) — NEVER on the tick path (spec §6.3);
  * persisted as a JSON data artefact and loaded read-only each tick;
  * guarded by a freshness cap — a map older than the cap fails the tick
    loudly rather than matching against stale exposures (spec §7).

Look-ahead caveat (spec §9): an LLM-built map on a pre-cutoff backtest window
carries training-knowledge contamination.  That is an accepted, documented
risk of the design, not something this module can prevent; the freshness gate
addresses only staleness, not look-ahead.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from agents.analysts.linkage.llm import LlmFn
from agents.analysts.linkage.schemas import ExposureMap, TickerExposure


class StaleExposureMapError(RuntimeError):
    """Raised when the exposure map is older than the configured cap."""


def _build_exposure_prompt(ticker: str) -> str:
    """Render the per-ticker exposure-extraction prompt.

    Parameters
    ----------
    ticker:
        The ticker to describe.

    Returns
    -------
    str
        A prompt asking for the ticker's economic-links exposure channels.
    """
    return (
        f"You are an equity analyst mapping the economic-links exposures of "
        f"{ticker}. Describe, as concise factual lists, this company's:\n"
        f"- sector (a single short label);\n"
        f"- commodity_sensitivities (raw inputs whose price moves the P&L);\n"
        f"- geographies (countries/regions of material revenue or operations);\n"
        f"- key_customers (named where public);\n"
        f"- key_suppliers (named where public);\n"
        f"- regulatory_exposure (regimes/rules that materially bind it).\n"
        f"Keep each list to the most material items only (at most ~10 each). "
        f"State durable structural facts, not current news."
    )


def load_exposure_map(path: str | Path) -> ExposureMap:
    """Load and validate the exposure-map artefact from ``path``.

    Parameters
    ----------
    path:
        Filesystem path to the JSON artefact.

    Returns
    -------
    ExposureMap
        The validated map.

    Raises
    ------
    FileNotFoundError
        If the artefact is absent — the tick path must not silently proceed
        without a map.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"exposure map artefact not found at {p} — build it offline via "
            f"`PYTHONPATH=src .venv/bin/python -m scripts.build_exposure_map`"
        )

    with p.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    return ExposureMap.model_validate(raw)


def assert_exposure_map_fresh(
    exposure_map: ExposureMap,
    as_of: datetime,
    cap_days: int,
) -> None:
    """Fail loudly when the map is older than ``cap_days`` relative to ``as_of``.

    The age is ``as_of - built_at``.  A backtest tick whose ``as_of`` precedes
    ``built_at`` has a negative age and always passes — the cap protects the
    live path from an un-refreshed map, not the backtest from look-ahead
    (which is a separate, accepted risk; see the module docstring).

    Parameters
    ----------
    exposure_map:
        The loaded map.
    as_of:
        The current tick timestamp.
    cap_days:
        Maximum permitted age in calendar days.

    Returns
    -------
    None

    Raises
    ------
    StaleExposureMapError
        When ``as_of`` is more than ``cap_days`` after ``built_at``.
    """
    if as_of > exposure_map.built_at + timedelta(days=cap_days):
        raise StaleExposureMapError(
            f"exposure map built_at={exposure_map.built_at.isoformat()} is "
            f"stale for as_of={as_of.isoformat()} (cap {cap_days}d) — rebuild "
            f"the map before running this tick"
        )


async def build_exposure_map(
    watchlist: list[str],
    *,
    llm_fn: LlmFn,
    built_at: datetime | None = None,
) -> ExposureMap:
    """Build an exposure map with one LLM pass per watchlist ticker.

    Parameters
    ----------
    watchlist:
        Tickers to describe.
    llm_fn:
        Injectable async structured-output call (see ``llm.make_default_llm_fn``).
    built_at:
        Build timestamp to stamp on the map. Defaults to the wall clock —
        this is an offline build, so wall-clock is correct here.

    Returns
    -------
    ExposureMap
        The assembled map keyed by ticker.
    """
    stamp = built_at or datetime.now()

    exposures: dict[str, TickerExposure] = {}

    # One pass per ticker — deliberately sequential so a partial failure
    # surfaces loudly against a known ticker rather than as an opaque gather.
    for ticker in watchlist:
        prompt   = _build_exposure_prompt(ticker)
        exposure = await llm_fn(prompt, TickerExposure)

        # The model is asked per-ticker; pin the ticker field to the request
        # so a mislabelled response cannot silently key the map wrongly.
        exposures[ticker] = exposure.model_copy(update={"ticker": ticker})

    return ExposureMap(built_at=stamp, watchlist=list(watchlist), exposures=exposures)


def save_exposure_map(exposure_map: ExposureMap, path: str | Path) -> None:
    """Persist the exposure map as pretty-printed JSON, creating parent dirs.

    Parameters
    ----------
    exposure_map:
        The map to write.
    path:
        Destination path.

    Returns
    -------
    None
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # ``mode="json"`` ISO-stringifies ``built_at`` so the artefact is portable.
    with p.open("w", encoding="utf-8") as handle:
        json.dump(exposure_map.model_dump(mode="json"), handle, indent=2)
```

- [ ] **Step 8: Run the exposure tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_exposure.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 9: Create the offline builder script `scripts/build_exposure_map.py`**

```python
"""Offline exposure-map builder (Phase 14 Plan 4).

Builds the per-ticker economic-links exposure map used by the linkage
matcher and writes it to the path configured in ``config/linkage.json``.
This is an OFFLINE tool — it is never invoked on the tick path.  Rebuild it
when the watchlist changes or the map exceeds its weekly staleness cap.

Usage::

    PYTHONPATH=src .venv/bin/python -m scripts.build_exposure_map
    PYTHONPATH=src .venv/bin/python -m scripts.build_exposure_map --tickers AAPL MSFT
    PYTHONPATH=src .venv/bin/python -m scripts.build_exposure_map --out data/linkage/exposure_map.json
"""
from __future__ import annotations

import argparse
import asyncio

from agents.analysts.linkage.exposure import build_exposure_map, save_exposure_map
from agents.analysts.linkage.llm import make_default_llm_fn
from config.linkage import get_linkage_config
from config.models import get_models_config
from orchestrator.stock_picker import get_watchlist


def _make_builder_llm_fn():
    """Construct the default exposure-builder ``llm_fn`` from config.

    Reads the deeper-model ID from ``config/models.json`` and the offline caps
    from ``config/linkage.json::exposure_builder`` — nothing is hardcoded.

    Returns
    -------
    LlmFn
        The production exposure-builder call.
    """
    caps  = get_linkage_config().exposure_builder
    model = get_models_config().linkage_exposure_builder

    return make_default_llm_fn(
        model             = model,
        max_output_tokens = caps.max_output_tokens,
        thinking_level    = caps.thinking_level,
        temperature       = caps.temperature,
        timeout_seconds   = caps.timeout_seconds,
        timeout_retries   = caps.timeout_retries,
        schema_retries    = caps.schema_retries,
    )


def main() -> None:
    """Parse arguments, build the exposure map, and write it to disk."""
    cfg    = get_linkage_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Explicit ticker list; defaults to the configured watchlist.",
    )
    parser.add_argument(
        "--out",
        default=cfg.exposure_map_path,
        help="Output path; defaults to config/linkage.json::exposure_map_path.",
    )
    args = parser.parse_args()

    watchlist = args.tickers or get_watchlist()

    built = asyncio.run(
        build_exposure_map(watchlist, llm_fn=_make_builder_llm_fn())
    )
    save_exposure_map(built, args.out)

    print(f"Built exposure map for {len(built.exposures)} tickers → {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Sanity-check the script imports (no live LLM call)**

Run: `PYTHONPATH=src .venv/bin/python -c "import scripts.build_exposure_map as m; print(m.main.__doc__.splitlines()[0])"`
Expected: prints `Parse arguments, build the exposure map, and write it to disk.` with no import error. (Do **not** run `main()` — it would issue live LLM calls.)

- [ ] **Step 11: Commit**

```bash
git add src/agents/analysts/linkage/llm.py src/agents/analysts/linkage/exposure.py scripts/build_exposure_map.py tests/unit/agents/analysts/linkage/test_llm.py tests/unit/agents/analysts/linkage/test_exposure.py
git commit -m "feat(linkage): add shared LLM seam + offline exposure-map builder and freshness gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Staleness pre-filter stage (macro namespace)

**Design note.** This is the deterministic **volume kill** (spec D4): before any LLM call, each macro article's embedding is compared against the `"macro"` namespace of Plan 2's news-history store; only articles below the shared similarity threshold survive. It also writes two side-channels the registry stage (Task 8) needs — the union of `mentioned_tickers` and the surviving article keys — so events can be enriched without the digester schema carrying free-text IDs.

**Files:**
- Create: `src/agents/analysts/linkage/staleness.py`
- Test: `tests/unit/agents/analysts/linkage/test_staleness.py`

**Interfaces:**
- Consumes: `state["macro_articles"]` (Plan 3, always present — loud `KeyError` if absent); `agents.analysts.news.history.get_news_history_store` with `async staleness(namespace, text) -> float` and `async record(namespace, article_key, text, published_at) -> None` (Plan 2); `config.analysts.get_analysts_config().staleness_similarity_threshold` (Plan 2, top-level float).
- Produces (consumed by Tasks 7, 8):
  - `LinkageStalenessAgent(BaseAgent)` with an optional injectable `store` field.
  - `build_linkage_staleness_agent(*, store=None) -> LinkageStalenessAgent`.
  - State keys `temp:linkage_novel_articles` (list of surviving `MacroArticle` dicts), `temp:linkage_novel_tickers` (sorted `list[str]`, union of survivors' `mentioned_tickers`), `temp:linkage_novel_article_ids` (list of survivors' article `url` keys).

- [ ] **Step 1: Write the failing staleness test**

Create `tests/unit/agents/analysts/linkage/test_staleness.py`:

```python
"""Unit tests for the linkage staleness pre-filter (Phase 14 Plan 4).

A fake news-history store makes the embedding pass deterministic: it reports
a novel article (similarity 0.0) the first time a text is seen and a stale
one (similarity 1.0) once recorded.  We assert the positive signal — the
surviving article list, the ticker union, and the id list — plus the quiet
(all-stale / empty) path.
"""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.linkage.staleness import build_linkage_staleness_agent


class _FakeStore:
    """Deterministic stand-in for Plan 2's NewsHistoryStore.

    ``staleness`` returns 1.0 for any text already recorded in the namespace
    and 0.0 otherwise, so an exact-duplicate article reads as fully stale.
    """

    def __init__(self) -> None:
        self._seen: dict[str, set[str]] = {}

    async def staleness(self, namespace: str, text: str) -> float:
        """Return 1.0 if ``text`` was already recorded, else 0.0."""
        return 1.0 if text in self._seen.get(namespace, set()) else 0.0

    async def record(self, namespace, article_key, text, published_at) -> None:
        """Remember ``text`` under ``namespace`` so a repeat reads as stale."""
        self._seen.setdefault(namespace, set()).add(text)


def _macro_article(url: str, headline: str, tickers: list[str]) -> dict:
    """Build a serialised MacroArticle dict (Plan 3 shape)."""
    return {
        "article": {
            "ticker": tickers[0] if tickers else "",
            "headline": headline,
            "summary": "roundup body",
            "url": url,
            "source": "finnhub",
            "published_at": "2026-02-10T14:00:00",
            "sentiment": None,
            "relevance": None,
        },
        "mentioned_tickers": tickers,
    }


async def _run(agent, state: dict):
    """Drive a BaseAgent stage once and return its single state_delta."""
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="s1",
    )
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv", agent=agent,
    )
    events = [ev async for ev in agent.run_async(ctx)]
    assert len(events) == 1
    return events[0].actions.state_delta


@pytest.mark.asyncio
async def test_novel_articles_survive_and_duplicates_are_dropped():
    """Distinct articles survive; an exact duplicate is filtered as stale."""
    state = {
        "as_of": "2026-02-10T14:00:00",
        "macro_articles": [
            _macro_article("https://n/a", "Chip supply squeeze", ["AAPL", "NVDA"]),
            _macro_article("https://n/a2", "Chip supply squeeze", ["AAPL"]),  # dup text
            _macro_article("https://n/b", "Oil spikes on conflict", ["XOM"]),
        ],
    }
    agent = build_linkage_staleness_agent(store=_FakeStore())

    delta = await _run(agent, state)

    survivors = delta["temp:linkage_novel_articles"]
    # First occurrence of each distinct text survives; the duplicate is dropped.
    assert len(survivors) == 2
    assert delta["temp:linkage_novel_article_ids"] == ["https://n/a", "https://n/b"]
    # Ticker union across survivors, sorted and de-duplicated.
    assert delta["temp:linkage_novel_tickers"] == ["AAPL", "NVDA", "XOM"]


@pytest.mark.asyncio
async def test_empty_macro_stream_is_a_quiet_tick():
    """No macro articles → empty side-channels, still emitted (quiet tick)."""
    state = {"as_of": "2026-02-10T14:00:00", "macro_articles": []}
    agent = build_linkage_staleness_agent(store=_FakeStore())

    delta = await _run(agent, state)

    assert delta["temp:linkage_novel_articles"] == []
    assert delta["temp:linkage_novel_tickers"] == []
    assert delta["temp:linkage_novel_article_ids"] == []
```

- [ ] **Step 2: Run the staleness test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_staleness.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.staleness'`.

- [ ] **Step 3: Create `src/agents/analysts/linkage/staleness.py`**

```python
"""Deterministic staleness pre-filter over the macro stream (Phase 14 Plan 4).

The volume kill before any LLM call (spec D4): each macro article's text is
compared, via embedding similarity, against the ``"macro"`` namespace of
Plan 2's news-history store.  Articles at or above the shared similarity
threshold are stale (Tetlock reversal territory) and dropped; only novel
articles survive to the digester.  Surviving articles are recorded into the
namespace so a near-duplicate on a later tick reads as stale.

Two side-channels are emitted for the registry stage (Task 8): the union of
the survivors' ``mentioned_tickers`` and their article keys.  This is how an
event learns which watchlist tickers its source roundups named and which
articles produced it, without the digester's LLM schema carrying free-text
IDs (which would invite hallucination and a ``max_length`` cap).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from config.analysts import get_analysts_config

_LOGGER = logging.getLogger(__name__)


class LinkageStalenessAgent(BaseAgent):
    """Filter the macro stream to novel articles via embedding staleness."""

    # Optional injectable store (tests pass a fake); ``None`` → the Plan 2
    # per-run singleton resolved at run time.
    store: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Compare each macro article against the ``"macro"`` namespace.

        Novel articles (similarity below the shared threshold) survive and are
        recorded; stale ones are dropped.  Emits the surviving articles plus
        the ticker-union and id side-channels via one ``state_delta`` event.

        Parameters
        ----------
        ctx:
            The ADK invocation context.

        Yields
        ------
        Event
            One event carrying the three ``temp:linkage_novel_*`` keys.
        """
        state = ctx.session.state

        # Trust Plan 3: ``macro_articles`` is always present.  A KeyError here
        # is a loud Plan 3 bug, not a condition we handle.
        macro_articles: list[dict] = state["macro_articles"]

        # Resolve the store lazily so the module imports without Plan 2's
        # singleton being initialised.
        store = self.store
        if store is None:
            from agents.analysts.news.history import get_news_history_store

            store = get_news_history_store()

        threshold = get_analysts_config().staleness_similarity_threshold

        novel_articles: list[dict] = []
        novel_ids:      list[str]  = []
        novel_tickers:  set[str]   = set()
        stale_count = 0

        for macro in macro_articles:
            article = macro["article"]

            # Embed on headline + summary — the same text the per-ticker filter
            # uses, so the shared threshold is calibrated consistently.
            text = f"{article['headline']} {article.get('summary', '')}".strip()
            key  = article["url"]

            similarity = await store.staleness("macro", text)

            # At or above the threshold ⇒ stale (a near-duplicate of something
            # already seen) ⇒ drop.
            if similarity >= threshold:
                stale_count += 1
                continue

            novel_articles.append(macro)
            novel_ids.append(key)
            novel_tickers.update(macro.get("mentioned_tickers", []))

            # Record the survivor so a repeat on a later tick reads as stale.
            # ``published_at`` is the article's own timestamp (a datetime per
            # Plan 2's record signature), parsed from its ISO serialisation.
            await store.record(
                "macro", key, text, datetime.fromisoformat(article["published_at"]),
            )

        if not novel_articles:
            # Quiet tick — logged explicitly and distinguished from a failure.
            _LOGGER.info(
                "linkage staleness: %d macro article(s) all stale/empty — quiet tick",
                stale_count,
            )
        else:
            _LOGGER.info(
                "linkage staleness: %d novel / %d stale macro article(s)",
                len(novel_articles), stale_count,
            )

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "temp:linkage_novel_articles":    novel_articles,
                "temp:linkage_novel_tickers":     sorted(novel_tickers),
                "temp:linkage_novel_article_ids": novel_ids,
            }),
        )


def build_linkage_staleness_agent(*, store: Any = None) -> LinkageStalenessAgent:
    """Construct the staleness pre-filter stage.

    Parameters
    ----------
    store:
        Optional injectable news-history store (tests pass a fake). ``None``
        resolves the Plan 2 per-run singleton at run time.

    Returns
    -------
    LinkageStalenessAgent
        The configured stage.
    """
    return LinkageStalenessAgent(name="LinkageStaleness", store=store)
```

- [ ] **Step 4: Run the staleness test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_staleness.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/linkage/staleness.py tests/unit/agents/analysts/linkage/test_staleness.py
git commit -m "feat(linkage): add deterministic macro staleness pre-filter stage

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Event digester stage (one flash call per tick)

**Design note.** One flash-class call per tick over the surviving novel articles distils them into normalised `LinkageEvent`s. On a **quiet tick** (no novel articles) the LLM is **skipped entirely** — the stage emits an empty digest and logs. A call that *raises* propagates loudly (wrapped by `IsolatedFailureWrapper` in Task 11); a call that *returns empty events* is a valid quiet outcome. This is the first of the two sanctioned flash calls per tick (spec D4).

**Files:**
- Create: `src/agents/analysts/linkage/digester.py`
- Test: `tests/unit/agents/analysts/linkage/test_digester.py`

**Interfaces:**
- Consumes: `state["temp:linkage_novel_articles"]` (Task 6); `schemas.LinkageDigest` (Task 3); `llm.make_default_llm_fn` (Task 5); `config.linkage.get_linkage_config`, `config.models.get_models_config` (Task 1).
- Produces (consumed by Task 8):
  - `LinkageDigesterAgent(BaseAgent)` with an optional injectable `llm_fn` field.
  - `build_linkage_digester_agent(*, llm_fn=None) -> LinkageDigesterAgent`.
  - State key `temp:linkage_digest` — a `LinkageDigest.model_dump(mode="json")` dict (`{"events": [...]}`).

- [ ] **Step 1: Write the failing digester test**

Create `tests/unit/agents/analysts/linkage/test_digester.py`:

```python
"""Unit tests for the linkage event digester (Phase 14 Plan 4).

A stubbed llm_fn makes the call deterministic.  We assert the positive
signal (a normalised event appears in ``temp:linkage_digest``), the schema
is honoured, and the quiet path skips the LLM entirely.
"""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService
from pydantic import BaseModel

from agents.analysts.linkage.digester import build_linkage_digester_agent
from agents.analysts.linkage.schemas import LinkageDigest, LinkageEvent


async def _run(agent, state: dict):
    """Drive a BaseAgent stage once and return its single state_delta."""
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="s1",
    )
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv", agent=agent,
    )
    events = [ev async for ev in agent.run_async(ctx)]
    assert len(events) == 1
    return events[0].actions.state_delta


def _novel(url: str) -> dict:
    """Build one surviving MacroArticle dict."""
    return {
        "article": {
            "ticker": "AAPL", "headline": "Foundry outage in Taiwan",
            "summary": "quake halts a fab", "url": url, "source": "finnhub",
            "published_at": "2026-02-10T14:00:00", "sentiment": None, "relevance": None,
        },
        "mentioned_tickers": ["AAPL", "NVDA"],
    }


@pytest.mark.asyncio
async def test_digester_emits_normalised_events():
    """A non-empty novel set yields a digest of normalised events."""

    async def _stub(prompt: str, schema: type[BaseModel]) -> BaseModel:
        assert schema is LinkageDigest
        return LinkageDigest(events=[
            LinkageEvent(
                summary="Taiwan foundry capacity cut on quake disruption.",
                category="sector",
                entities=["TSMC", "semiconductors"],
                surprise_direction="negative",
                novelty="new",
            )
        ])

    agent = build_linkage_digester_agent(llm_fn=_stub)
    delta = await _run(agent, {"temp:linkage_novel_articles": [_novel("https://n/a")]})

    digest = delta["temp:linkage_digest"]
    assert len(digest["events"]) == 1
    assert digest["events"][0]["category"] == "sector"
    assert digest["events"][0]["surprise_direction"] == "negative"


@pytest.mark.asyncio
async def test_digester_skips_llm_on_quiet_tick():
    """No novel articles → empty digest and the LLM is never called."""

    async def _boom(prompt: str, schema: type[BaseModel]) -> BaseModel:
        raise AssertionError("llm_fn must not be called on a quiet tick")

    agent = build_linkage_digester_agent(llm_fn=_boom)
    delta = await _run(agent, {"temp:linkage_novel_articles": []})

    assert delta["temp:linkage_digest"] == {"events": []}
```

- [ ] **Step 2: Run the digester test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_digester.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.digester'`.

- [ ] **Step 3: Create `src/agents/analysts/linkage/digester.py`**

```python
"""Event digester — distils novel macro articles into normalised events.

One flash-class LLM call per tick (spec D4, call 1 of 2).  The surviving
novel articles from the staleness stage are handed to the model, which
returns a ``LinkageDigest`` of normalised ``LinkageEvent``s: a summary, a
closed-vocab category, entities, a surprise direction, and a novelty label.

Quiet vs failure (spec §7): on a tick with no novel articles the LLM is
**skipped** and an empty digest is emitted (logged as a quiet tick).  A call
that *raises* propagates unchanged so the branch's ``IsolatedFailureWrapper``
records a loud failure; a call that *returns zero events* is a valid quiet
outcome, not an error.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts.linkage.llm import LlmFn
from agents.analysts.linkage.schemas import LinkageDigest

_LOGGER = logging.getLogger(__name__)


def _default_digester_llm_fn() -> LlmFn:
    """Build the production digester ``llm_fn`` from config (no hardcoding)."""
    from agents.analysts.linkage.llm import make_default_llm_fn
    from config.linkage import get_linkage_config
    from config.models import get_models_config

    caps = get_linkage_config().digester

    return make_default_llm_fn(
        model             = get_models_config().linkage_digester,
        max_output_tokens = caps.max_output_tokens,
        thinking_level    = caps.thinking_level,
        temperature       = caps.temperature,
        timeout_seconds   = caps.timeout_seconds,
        timeout_retries   = caps.timeout_retries,
        schema_retries    = caps.schema_retries,
    )


def _build_digester_prompt(novel_articles: list[dict]) -> str:
    """Render the digester prompt over the surviving novel articles.

    Parameters
    ----------
    novel_articles:
        Surviving ``MacroArticle`` dicts from the staleness stage.

    Returns
    -------
    str
        A prompt instructing the model to distil normalised events.
    """
    # One compact line per article keeps the prompt token-lean.
    lines = [
        f"- [{a['article']['source']}] {a['article']['headline']}: "
        f"{a['article'].get('summary', '')}"
        for a in novel_articles
    ]
    body = "\n".join(lines)

    return (
        "You are a macro/sector event digester for an equity strategy.\n"
        "From the market-roundup articles below, distil the distinct, "
        "material events. For each event give:\n"
        "- summary: one sentence, no more than 40 words;\n"
        "- category: exactly one of macro | sector | merger;\n"
        "- entities: the named companies/commodities/regions involved "
        "(at most 12);\n"
        "- surprise_direction: positive | negative | mixed | none "
        "(is this better or worse than expected?);\n"
        "- novelty: new | developing | reiteration.\n"
        "Merge duplicate coverage of the same event into one entry. Do not "
        "invent events not supported by the text.\n\n"
        f"Articles:\n{body}"
    )


class LinkageDigesterAgent(BaseAgent):
    """Distil novel macro articles into a normalised LinkageDigest."""

    # Optional injectable call (tests pass a stub); ``None`` → config default.
    llm_fn: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Call the digester once over novel articles, or skip on a quiet tick.

        Parameters
        ----------
        ctx:
            The ADK invocation context.

        Yields
        ------
        Event
            One event carrying ``temp:linkage_digest``.
        """
        state = ctx.session.state

        # Present because the staleness stage runs first in the same branch.
        novel_articles: list[dict] = state["temp:linkage_novel_articles"]

        if not novel_articles:
            # Quiet tick — skip the LLM entirely (token economics, spec D4).
            _LOGGER.info("linkage digester: no novel articles — quiet tick, LLM skipped")
            yield Event(
                author=self.name,
                invocation_id=ctx.invocation_id,
                actions=EventActions(state_delta={
                    "temp:linkage_digest": {"events": []},
                }),
            )
            return

        llm_fn = self.llm_fn or _default_digester_llm_fn()

        prompt = _build_digester_prompt(novel_articles)

        # A raise here propagates to IsolatedFailureWrapper (loud failure).
        digest: LinkageDigest = await llm_fn(prompt, LinkageDigest)

        _LOGGER.info("linkage digester: distilled %d event(s)", len(digest.events))

        # ``mode="json"`` keeps the dict JSON-safe for the ADK state store.
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "temp:linkage_digest": json.loads(digest.model_dump_json()),
            }),
        )


def build_linkage_digester_agent(*, llm_fn: Any = None) -> LinkageDigesterAgent:
    """Construct the digester stage.

    Parameters
    ----------
    llm_fn:
        Optional injectable async ``llm_fn(prompt, schema)`` (tests pass a
        stub). ``None`` builds the config-driven production default lazily.

    Returns
    -------
    LinkageDigesterAgent
        The configured stage.
    """
    return LinkageDigesterAgent(name="LinkageDigester", llm_fn=llm_fn)
```

- [ ] **Step 4: Run the digester test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_digester.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/linkage/digester.py tests/unit/agents/analysts/linkage/test_digester.py
git commit -m "feat(linkage): add event digester stage (one flash call per tick)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Registry stage — enrich, persist, and load active events

**Design note.** This ADK stage bridges the digester's ticker-free events to the matcher's active-event input. It enriches each `LinkageEvent` with the drift metadata the digester schema deliberately omits — tickers (the tick's novel-article union), direction (the surprise direction), a per-category horizon, the event date (`as_of`), and source article ids — records it into the per-run registry (Task 4), write-throughs a durable audit row transactionally with the tick, then loads the currently-active events for the matcher.

**Event → article provenance is per-tick coarse** (resolved ambiguity): every event distilled on a tick is attributed the *union* of that tick's novel-article tickers and ids, because the digester's LLM schema carries no free-text article ids (avoiding hallucination and a `max_length` cap). The `tickers` field is only a "named in the source roundups" hint; the authoritative ticker linkage is the matcher's exposure-map cross (Task 9), so the coarseness is immaterial to the signal.

**Files:**
- Create: `src/agents/analysts/linkage/registry_stage.py`
- Test: `tests/unit/agents/analysts/linkage/test_registry_stage.py`

**Interfaces:**
- Consumes: `state["temp:linkage_digest"]` (Task 7), `state["temp:linkage_novel_tickers"]`, `state["temp:linkage_novel_article_ids"]` (Task 6); `registry.{RegisteredEvent, make_event_id, get_linkage_registry}` (Task 4); `persistence.save_linkage_event` (Task 4); `config.linkage.get_linkage_config().event_horizon_days` (Task 1); `data.timeguard.resolve_as_of`.
- Produces (consumed by Task 9):
  - `LinkageRegistryAgent(BaseAgent)` with optional `db_session` and injectable `registry` fields.
  - `build_linkage_registry_agent(db_session=None, *, registry=None) -> LinkageRegistryAgent`.
  - State key `temp:linkage_active_events` — a list of active-event dicts `{event_id, summary, category, tickers, direction, event_date (ISO), horizon_days, source_article_ids}`.

- [ ] **Step 1: Write the failing registry-stage test**

Create `tests/unit/agents/analysts/linkage/test_registry_stage.py`:

```python
"""Unit tests for the linkage registry ADK stage (Phase 14 Plan 4)."""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.analysts.linkage.registry import LinkageEventRegistry
from agents.analysts.linkage.registry_stage import build_linkage_registry_agent
from orchestrator.persistence import Base, load_linkage_events


def _session():
    """Build an in-memory SQLite session with the schema created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


async def _run(agent, state: dict):
    """Drive a BaseAgent stage once and return its single state_delta."""
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="s1",
    )
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv", agent=agent,
    )
    events = [ev async for ev in agent.run_async(ctx)]
    assert len(events) == 1
    return events[0].actions.state_delta


def _state_with_one_event() -> dict:
    """Session state carrying one digested sector event."""
    return {
        "as_of": "2026-02-10T14:00:00",
        "temp:linkage_novel_tickers": ["AAPL", "NVDA"],
        "temp:linkage_novel_article_ids": ["https://n/a"],
        "temp:linkage_digest": {
            "events": [
                {
                    "summary": "Taiwan foundry capacity cut on quake disruption.",
                    "category": "sector",
                    "entities": ["TSMC"],
                    "surprise_direction": "negative",
                    "novelty": "new",
                }
            ]
        },
    }


@pytest.mark.asyncio
async def test_registry_stage_enriches_and_activates_event():
    """The digested event is enriched, registered, and emitted as active."""
    registry = LinkageEventRegistry()
    session  = _session()
    agent    = build_linkage_registry_agent(db_session=session, registry=registry)

    delta = await _run(agent, _state_with_one_event())

    active = delta["temp:linkage_active_events"]
    assert len(active) == 1
    event = active[0]
    # Enrichment: tickers union, direction, per-category horizon, event date.
    assert event["tickers"] == ["AAPL", "NVDA"]
    assert event["direction"] == "negative"
    assert event["horizon_days"] == 21          # sector default from config
    assert event["event_date"] == "2026-02-10T14:00:00"
    assert event["source_article_ids"] == ["https://n/a"]

    # Durable audit row written through transactionally with the tick.
    rows = load_linkage_events(session)
    assert len(rows) == 1
    assert rows[0]["category"] == "sector"


@pytest.mark.asyncio
async def test_registry_stage_is_idempotent_across_ticks():
    """Re-digesting the same event on a second tick does not duplicate it."""
    registry = LinkageEventRegistry()
    session  = _session()
    agent    = build_linkage_registry_agent(db_session=session, registry=registry)

    await _run(agent, _state_with_one_event())
    delta = await _run(agent, _state_with_one_event())

    # Still exactly one active event and one audit row.
    assert len(delta["temp:linkage_active_events"]) == 1
    assert len(load_linkage_events(session)) == 1


@pytest.mark.asyncio
async def test_registry_stage_quiet_tick_has_no_active_events():
    """An empty digest yields no active events (quiet tick)."""
    registry = LinkageEventRegistry()
    agent    = build_linkage_registry_agent(db_session=None, registry=registry)

    delta = await _run(agent, {
        "as_of": "2026-02-10T14:00:00",
        "temp:linkage_novel_tickers": [],
        "temp:linkage_novel_article_ids": [],
        "temp:linkage_digest": {"events": []},
    })

    assert delta["temp:linkage_active_events"] == []
```

- [ ] **Step 2: Run the registry-stage test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_registry_stage.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.registry_stage'`.

- [ ] **Step 3: Create `src/agents/analysts/linkage/registry_stage.py`**

```python
"""Registry ADK stage — enrich, persist, and load active events (Phase 14 Plan 4).

Bridges the digester's ticker-free events to the matcher's active-event
input.  For each ``LinkageEvent`` in the tick's digest this stage:

  1. enriches it with drift metadata the digester schema omits — the tick's
     novel-article ticker union, the surprise direction, a per-category
     horizon, the event date (``as_of``), and the source article ids;
  2. records it into the per-run in-memory registry (idempotent by id);
  3. write-throughs a durable ``linkage_events`` audit row, committed
     transactionally with the tick;
  4. loads the currently-active events (those still inside their horizon) and
     emits them for the matcher.

The in-memory registry is the active-events source of truth; the SQLite row
is the inspectable audit trail (see ``registry.py`` for the two-role design).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts.linkage.registry import (
    RegisteredEvent,
    make_event_id,
)
from config.linkage import get_linkage_config
from data.timeguard import resolve_as_of

_LOGGER = logging.getLogger(__name__)


def _serialise_active(event: RegisteredEvent) -> dict:
    """Render an active event as a JSON-safe dict for the matcher.

    ``event_date`` is ISO-stringified so the ADK state store (which cannot
    hold a ``datetime``) accepts it.

    Parameters
    ----------
    event:
        The active registered event.

    Returns
    -------
    dict
        A JSON-safe representation.
    """
    return {
        "event_id":           event.event_id,
        "summary":            event.summary,
        "category":           event.category,
        "tickers":            list(event.tickers),
        "direction":          event.direction,
        "event_date":         event.event_date.isoformat(),
        "horizon_days":       event.horizon_days,
        "source_article_ids": list(event.source_article_ids),
    }


class LinkageRegistryAgent(BaseAgent):
    """Enrich digested events, persist them, and load the active set."""

    db_session: Any = None
    # Optional injectable registry (tests pass a fresh instance); ``None`` →
    # the per-run singleton resolved at run time.
    registry: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Register the tick's events and emit the active set.

        Parameters
        ----------
        ctx:
            The ADK invocation context.

        Yields
        ------
        Event
            One event carrying ``temp:linkage_active_events``.
        """
        state = ctx.session.state

        digest        = state["temp:linkage_digest"]
        novel_tickers = state["temp:linkage_novel_tickers"]
        novel_ids     = state["temp:linkage_novel_article_ids"]

        # Backtest replay clock — coerced from the possibly-ISO state value.
        as_of = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="linkage/registry",
        )

        horizons = get_linkage_config().event_horizon_days

        registry = self.registry
        if registry is None:
            from agents.analysts.linkage.registry import get_linkage_registry

            registry = get_linkage_registry()

        newly_added = 0

        for raw in digest["events"]:
            category = raw["category"]

            # A category with no configured horizon is a loud contract breach.
            horizon = horizons[category]

            event_id = make_event_id(raw["summary"], as_of, category)

            registered = RegisteredEvent(
                event_id           = event_id,
                summary            = raw["summary"],
                category           = category,
                tickers            = list(novel_tickers),
                direction          = raw["surprise_direction"],
                event_date         = as_of,
                horizon_days       = horizon,
                source_article_ids = list(novel_ids),
            )

            # Idempotent: only newly-added events get an audit row written.
            if registry.record(registered) and self.db_session is not None:
                from orchestrator.persistence import save_linkage_event

                save_linkage_event(self.db_session, {
                    "event_id":           event_id,
                    "summary":            registered.summary,
                    "category":           category,
                    "tickers":            registered.tickers,
                    "direction":          registered.direction,
                    "event_date":         as_of,
                    "horizon_days":       horizon,
                    "source_article_ids": registered.source_article_ids,
                })
                newly_added += 1

        # Commit the audit rows transactionally with the tick (spec §7).
        if self.db_session is not None and newly_added:
            self.db_session.commit()

        active = registry.active_events(as_of)

        if not active:
            _LOGGER.info("linkage registry: no active events at %s — quiet tick", as_of.isoformat())
        else:
            _LOGGER.info(
                "linkage registry: %d active event(s) (%d newly added this tick)",
                len(active), newly_added,
            )

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "temp:linkage_active_events": [_serialise_active(e) for e in active],
            }),
        )


def build_linkage_registry_agent(
    db_session: Any = None,
    *,
    registry: Any = None,
) -> LinkageRegistryAgent:
    """Construct the registry stage.

    Parameters
    ----------
    db_session:
        Optional SQLAlchemy session for the durable audit write. ``None`` →
        in-memory-only (registry still tracks active events).
    registry:
        Optional injectable registry (tests pass a fresh instance). ``None`` →
        the per-run singleton.

    Returns
    -------
    LinkageRegistryAgent
        The configured stage.
    """
    return LinkageRegistryAgent(
        name="LinkageRegistry", db_session=db_session, registry=registry,
    )
```

- [ ] **Step 4: Run the registry-stage test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_registry_stage.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/linkage/registry_stage.py tests/unit/agents/analysts/linkage/test_registry_stage.py
git commit -m "feat(linkage): add registry stage — enrich, persist, load active events

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Matcher stage (one flash call per tick)

**Design note.** The second and final flash call per tick (spec D4). It crosses the active events against the exposure map and emits per-ticker `LinkageMatch`es. On a quiet tick (no active events) or a tick with no exposed watchlist tickers, the LLM is **skipped** and an empty match set is emitted (logged). The exposure map is loaded and freshness-gated **at run time, after** the quiet-tick check, so a quiet tick never requires a map on disk, and a stale map fails the tick loudly (spec §7).

**Files:**
- Create: `src/agents/analysts/linkage/matcher.py`
- Test: `tests/unit/agents/analysts/linkage/test_matcher.py`

**Interfaces:**
- Consumes: `state["temp:linkage_active_events"]` (Task 8), `state["tickers"]`; `schemas.LinkageMatchBatch` (Task 3); `exposure.{load_exposure_map, assert_exposure_map_fresh}` (Task 5); `llm.make_default_llm_fn` (Task 5); `config.linkage.get_linkage_config`, `config.models.get_models_config` (Task 1); `data.timeguard.resolve_as_of`.
- Produces (consumed by Task 10):
  - `LinkageMatcherAgent(BaseAgent)` with optional injectable `llm_fn` and `exposure_map` fields.
  - `build_linkage_matcher_agent(*, llm_fn=None, exposure_map=None) -> LinkageMatcherAgent`.
  - State key `temp:linkage_matches` — a `LinkageMatchBatch.model_dump()` dict (`{"matches": [...]}`), filtered to exposed watchlist tickers.

- [ ] **Step 1: Write the failing matcher test**

Create `tests/unit/agents/analysts/linkage/test_matcher.py`:

```python
"""Unit tests for the linkage matcher (Phase 14 Plan 4)."""
from __future__ import annotations

from datetime import datetime

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService
from pydantic import BaseModel

from agents.analysts.linkage.exposure import StaleExposureMapError
from agents.analysts.linkage.matcher import build_linkage_matcher_agent
from agents.analysts.linkage.schemas import (
    ExposureMap,
    LinkageMatch,
    LinkageMatchBatch,
    TickerExposure,
)


def _exposure_map(built_at: datetime) -> ExposureMap:
    """One-ticker exposure map for the matcher."""
    return ExposureMap(
        built_at=built_at,
        watchlist=["AAPL"],
        exposures={
            "AAPL": TickerExposure(
                ticker="AAPL", sector="Technology Hardware",
                commodity_sensitivities=[], geographies=["China"],
                key_customers=[], key_suppliers=["TSMC"],
                regulatory_exposure=[],
            )
        },
    )


def _active_event() -> dict:
    """One active sector event dict (registry-stage output shape)."""
    return {
        "event_id": "evt:1",
        "summary": "Taiwan foundry capacity cut.",
        "category": "sector",
        "tickers": ["NVDA"],
        "direction": "negative",
        "event_date": "2026-02-10T14:00:00",
        "horizon_days": 21,
        "source_article_ids": ["https://n/a"],
    }


async def _run(agent, state: dict):
    """Drive a BaseAgent stage once and return its single state_delta."""
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="s1",
    )
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv", agent=agent,
    )
    events = [ev async for ev in agent.run_async(ctx)]
    assert len(events) == 1
    return events[0].actions.state_delta


@pytest.mark.asyncio
async def test_matcher_emits_per_ticker_matches():
    """Active events × exposure map → a LinkageMatch for the exposed ticker."""

    async def _stub(prompt: str, schema: type[BaseModel]) -> BaseModel:
        assert schema is LinkageMatchBatch
        return LinkageMatchBatch(matches=[
            LinkageMatch(
                ticker="AAPL", lean="bearish", magnitude=0.4, confidence=0.6,
                horizon_days=21, channel="supplier: shares TSMC foundry exposure",
                rationale="Negative sector surprise drifts into AAPL via TSMC.",
                key_factors=["channel:supplier"],
            )
        ])

    agent = build_linkage_matcher_agent(
        llm_fn=_stub, exposure_map=_exposure_map(datetime(2026, 2, 9, 9, 0)),
    )
    delta = await _run(agent, {
        "as_of": "2026-02-10T14:00:00",
        "tickers": ["AAPL", "MSFT"],
        "temp:linkage_active_events": [_active_event()],
    })

    matches = delta["temp:linkage_matches"]["matches"]
    assert len(matches) == 1
    assert matches[0]["ticker"] == "AAPL"
    assert matches[0]["lean"] == "bearish"
    assert matches[0]["horizon_days"] == 21


@pytest.mark.asyncio
async def test_matcher_quiet_when_no_active_events():
    """No active events → empty matches and the LLM is never called."""

    async def _boom(prompt: str, schema: type[BaseModel]) -> BaseModel:
        raise AssertionError("llm_fn must not run on a quiet tick")

    agent = build_linkage_matcher_agent(
        llm_fn=_boom, exposure_map=_exposure_map(datetime(2026, 2, 9)),
    )
    delta = await _run(agent, {
        "as_of": "2026-02-10T14:00:00",
        "tickers": ["AAPL"],
        "temp:linkage_active_events": [],
    })

    assert delta["temp:linkage_matches"] == {"matches": []}


@pytest.mark.asyncio
async def test_matcher_quiet_when_no_exposed_tickers():
    """Active events but no watchlist ticker in the map → empty matches, no LLM."""

    async def _boom(prompt: str, schema: type[BaseModel]) -> BaseModel:
        raise AssertionError("llm_fn must not run when no ticker is exposed")

    agent = build_linkage_matcher_agent(
        llm_fn=_boom, exposure_map=_exposure_map(datetime(2026, 2, 9)),
    )
    delta = await _run(agent, {
        "as_of": "2026-02-10T14:00:00",
        "tickers": ["MSFT"],           # not in the exposure map
        "temp:linkage_active_events": [_active_event()],
    })

    assert delta["temp:linkage_matches"] == {"matches": []}


@pytest.mark.asyncio
async def test_matcher_raises_on_stale_exposure_map():
    """A stale map fails the tick loudly rather than matching against it."""

    async def _stub(prompt: str, schema: type[BaseModel]) -> BaseModel:
        return LinkageMatchBatch(matches=[])

    # Map built 2026-01-01, tick 2026-02-10, cap 7d → stale.
    agent = build_linkage_matcher_agent(
        llm_fn=_stub, exposure_map=_exposure_map(datetime(2026, 1, 1)),
    )
    with pytest.raises(StaleExposureMapError):
        await _run(agent, {
            "as_of": "2026-02-10T14:00:00",
            "tickers": ["AAPL"],
            "temp:linkage_active_events": [_active_event()],
        })
```

- [ ] **Step 2: Run the matcher test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_matcher.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.matcher'`.

- [ ] **Step 3: Create `src/agents/analysts/linkage/matcher.py`**

```python
"""Matcher — cross active events against the exposure map (Phase 14 Plan 4).

The second and final flash call per tick (spec D4).  Active drift events are
crossed against the per-ticker exposure map to produce per-ticker
``LinkageMatch``es: a lean, magnitude, confidence, the drift horizon, the
economic-links channel, and a rationale.

Quiet vs failure (spec §7):
  * no active events, or no exposed watchlist ticker ⇒ the LLM is skipped and
    an empty match set is emitted (logged quiet tick);
  * a stale exposure map ⇒ the tick fails loudly (``StaleExposureMapError``);
  * an LLM raise ⇒ propagates to the branch's ``IsolatedFailureWrapper``.

The exposure map is loaded and freshness-gated at run time, *after* the
quiet-tick check, so a quiet tick never needs a map on disk.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts.linkage.llm import LlmFn
from agents.analysts.linkage.schemas import ExposureMap, LinkageMatchBatch, TickerExposure
from config.linkage import get_linkage_config
from data.timeguard import resolve_as_of

_LOGGER = logging.getLogger(__name__)

# The one quiet-tick payload emitted whenever no match is produced.
_EMPTY_MATCHES = {"matches": []}


def _default_matcher_llm_fn() -> LlmFn:
    """Build the production matcher ``llm_fn`` from config (no hardcoding)."""
    from agents.analysts.linkage.llm import make_default_llm_fn
    from config.models import get_models_config

    caps = get_linkage_config().matcher

    return make_default_llm_fn(
        model             = get_models_config().linkage_matcher,
        max_output_tokens = caps.max_output_tokens,
        thinking_level    = caps.thinking_level,
        temperature       = caps.temperature,
        timeout_seconds   = caps.timeout_seconds,
        timeout_retries   = caps.timeout_retries,
        schema_retries    = caps.schema_retries,
    )


def _render_exposure(ticker: str, exposure: TickerExposure) -> str:
    """Render one ticker's exposure channels as a compact prompt block.

    Parameters
    ----------
    ticker:
        The ticker.
    exposure:
        Its exposure channels.

    Returns
    -------
    str
        A compact multi-field line for the matcher prompt.
    """
    return (
        f"{ticker}: sector={exposure.sector}; "
        f"commodities={exposure.commodity_sensitivities}; "
        f"geographies={exposure.geographies}; "
        f"customers={exposure.key_customers}; "
        f"suppliers={exposure.key_suppliers}; "
        f"regulatory={exposure.regulatory_exposure}"
    )


def _build_matcher_prompt(
    active_events: list[dict],
    exposures: dict[str, TickerExposure],
) -> str:
    """Render the matcher prompt: active events × exposed tickers.

    Parameters
    ----------
    active_events:
        Active-event dicts from the registry stage.
    exposures:
        Exposure channels for the exposed watchlist tickers only.

    Returns
    -------
    str
        A prompt instructing the model to emit per-ticker drift matches.
    """
    event_lines = [
        f"- ({e['category']}, {e['direction']}) {e['summary']} "
        f"[entities/tickers: {e['tickers']}, horizon {e['horizon_days']}d]"
        for e in active_events
    ]
    exposure_lines = [_render_exposure(t, x) for t, x in exposures.items()]

    return (
        "You position an equity book for economic-links DRIFT (Cohen & "
        "Frazzini): an event about one firm/sector/region drifts into the "
        "prices of firms exposed to it over days-to-weeks.\n\n"
        "Active events:\n" + "\n".join(event_lines) + "\n\n"
        "Watchlist exposures:\n" + "\n".join(exposure_lines) + "\n\n"
        "For each watchlist ticker with a genuine exposure to one or more "
        "active events, emit a match with:\n"
        "- ticker (must be one of the watchlist tickers above);\n"
        "- lean: bullish | bearish | neutral (the drift direction for THIS "
        "ticker, which may differ from the event's own direction);\n"
        "- magnitude 0..1 and confidence 0..1;\n"
        "- horizon_days: the trading days you expect the drift to persist "
        "(>=1);\n"
        "- channel: the exposure channel carrying the link (e.g. "
        "'supplier: TSMC');\n"
        "- rationale: one sentence; key_factors: up to 8 short tags.\n"
        "Emit NO match for a ticker with no genuine exposure — do not pad."
    )


class LinkageMatcherAgent(BaseAgent):
    """Cross active events against exposures to emit per-ticker matches."""

    # Optional injectables (tests pass stubs); ``None`` → config/disk defaults.
    llm_fn:       Any = None
    exposure_map: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Emit per-ticker drift matches, or an empty set on a quiet tick.

        Parameters
        ----------
        ctx:
            The ADK invocation context.

        Yields
        ------
        Event
            One event carrying ``temp:linkage_matches``.
        """
        state         = ctx.session.state
        active_events = state["temp:linkage_active_events"]
        tickers       = state.get("tickers", []) or []

        # Quiet tick #1: nothing active — skip the LLM and the map load.
        if not active_events:
            _LOGGER.info("linkage matcher: no active events — quiet tick, LLM skipped")
            yield self._emit(ctx, _EMPTY_MATCHES)
            return

        as_of = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="linkage/matcher",
        )

        cfg = get_linkage_config()

        # Load and freshness-gate the map at run time (after the quiet check).
        exposure_map: ExposureMap = self.exposure_map
        if exposure_map is None:
            from agents.analysts.linkage.exposure import load_exposure_map

            exposure_map = load_exposure_map(cfg.exposure_map_path)

        from agents.analysts.linkage.exposure import assert_exposure_map_fresh

        # Stale map ⇒ raises loudly (spec §7).
        assert_exposure_map_fresh(exposure_map, as_of, cfg.exposure_map_staleness_cap_days)

        exposed = {
            t: exposure_map.exposures[t]
            for t in tickers
            if t in exposure_map.exposures
        }

        # Quiet tick #2: no watchlist ticker is in the map — skip the LLM.
        if not exposed:
            _LOGGER.info("linkage matcher: no exposed watchlist ticker — quiet tick")
            yield self._emit(ctx, _EMPTY_MATCHES)
            return

        llm_fn = self.llm_fn or _default_matcher_llm_fn()

        prompt = _build_matcher_prompt(active_events, exposed)

        # A raise here propagates to IsolatedFailureWrapper (loud failure).
        batch: LinkageMatchBatch = await llm_fn(prompt, LinkageMatchBatch)

        # Defend the contract: drop any match for a ticker outside the exposed
        # set (a hallucinated ticker is a bug, surfaced as a WARNING).
        kept = []
        for match in batch.matches:
            if match.ticker in exposed:
                kept.append(match)
            else:
                _LOGGER.warning(
                    "linkage matcher: dropped match for unexposed ticker %s",
                    match.ticker,
                )

        _LOGGER.info("linkage matcher: %d match(es) across %d exposed ticker(s)",
                     len(kept), len(exposed))

        yield self._emit(ctx, {"matches": [m.model_dump() for m in kept]})

    def _emit(self, ctx: InvocationContext, matches: dict) -> Event:
        """Wrap a matches payload in a single ``temp:linkage_matches`` event.

        Parameters
        ----------
        ctx:
            The invocation context (for author/invocation id).
        matches:
            The ``{"matches": [...]}`` payload.

        Returns
        -------
        Event
            The state-delta event.
        """
        return Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={"temp:linkage_matches": matches}),
        )


def build_linkage_matcher_agent(
    *,
    llm_fn: Any = None,
    exposure_map: Any = None,
) -> LinkageMatcherAgent:
    """Construct the matcher stage.

    Parameters
    ----------
    llm_fn:
        Optional injectable async ``llm_fn`` (tests pass a stub). ``None`` →
        config-driven default.
    exposure_map:
        Optional injectable ``ExposureMap`` (tests pass one to avoid disk).
        ``None`` → loaded from the configured path at run time.

    Returns
    -------
    LinkageMatcherAgent
        The configured stage.
    """
    return LinkageMatcherAgent(
        name="LinkageMatcher", llm_fn=llm_fn, exposure_map=exposure_map,
    )
```

- [ ] **Step 4: Run the matcher test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_matcher.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/linkage/matcher.py tests/unit/agents/analysts/linkage/test_matcher.py
git commit -m "feat(linkage): add matcher stage (one flash call per tick, freshness-gated)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Joiner stage + deterministic feature extractor

**Design note.** The joiner mirrors `NewsJoinerAgent`: it inflates the matcher's `LinkageMatch`es into the canonical durable keys `linkage_verdicts` (a `VerdictBatch`) and `linkage_evidence` (a list of `AnalystEvidence` dumps under analyst name `linkage`). Unlike the news joiner it does **not** synthesise no-data verdicts for unmatched tickers — a sparse linkage tick is expected, and the digest's `_fill_missing` neutral-fills the absent slot downstream. Each verdict carries `horizon_days` straight from the match.

**Files:**
- Create: `src/contract/extractors/linkage.py`
- Create: `src/agents/analysts/linkage/joiner.py`
- Test: `tests/unit/contract/extractors/test_linkage_features.py`
- Test: `tests/unit/agents/analysts/linkage/test_joiner.py`

**Interfaces:**
- Consumes: `state["temp:linkage_matches"]` (Task 9); `contract.evidence.{AnalystVerdict, TickerVerdict, VerdictBatch, AnalystEvidence}`; `data.timeguard.resolve_as_of`.
- Produces (consumed by Tasks 11, 12):
  - `contract.extractors.linkage.extract_linkage_features(match: dict) -> dict[str, float]`.
  - `LinkageJoinerAgent(BaseAgent)`, `build_linkage_joiner_agent() -> LinkageJoinerAgent`.
  - State keys `linkage_verdicts` (`VerdictBatch.model_dump()`) and `linkage_evidence` (`list[AnalystEvidence.model_dump(mode="json")]`).

- [ ] **Step 1: Write the failing feature-extractor test**

Create `tests/unit/contract/extractors/test_linkage_features.py`:

```python
"""Unit test for the deterministic linkage feature extractor."""
from __future__ import annotations

from contract.extractors.linkage import extract_linkage_features


def test_extract_linkage_features_are_populated():
    """A match yields numeric features with the expected signed lean."""
    match = {
        "ticker": "AAPL", "lean": "bearish", "magnitude": 0.4,
        "confidence": 0.6, "horizon_days": 21, "channel": "supplier: TSMC",
        "rationale": "drift via foundry supplier", "key_factors": ["channel:supplier"],
    }

    features = extract_linkage_features(match)

    assert features["magnitude"] == 0.4
    assert features["confidence"] == 0.6
    assert features["horizon_days"] == 21.0
    assert features["lean_sign"] == -1.0            # bearish
    assert features["n_key_factors"] == 1.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_linkage_features.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'contract.extractors.linkage'`.

- [ ] **Step 3: Create `src/contract/extractors/linkage.py`**

```python
"""Deterministic feature extractor for linkage matches (Phase 14 Plan 4).

The matcher already emits a structured ``LinkageMatch``; this extractor lifts
its numeric fields into the flat ``dict[str, float]`` shape ``AnalystEvidence``
carries, so the scoreboard and any downstream analysis see linkage features in
the same form as every other analyst's.
"""
from __future__ import annotations

# Signed direction of the lean — used as a numeric feature for the scoreboard.
_LEAN_SIGN: dict[str, float] = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


def extract_linkage_features(match: dict) -> dict[str, float]:
    """Lift a linkage match's numeric fields into a flat feature dict.

    Parameters
    ----------
    match:
        A ``LinkageMatch.model_dump()`` dict with keys ``lean``, ``magnitude``,
        ``confidence``, ``horizon_days``, and ``key_factors``.

    Returns
    -------
    dict[str, float]
        Numeric features: ``magnitude``, ``confidence``, ``horizon_days``,
        ``lean_sign`` (+1/-1/0), and ``n_key_factors``.
    """
    return {
        "magnitude":     float(match["magnitude"]),
        "confidence":    float(match["confidence"]),
        "horizon_days":  float(match["horizon_days"]),
        "lean_sign":     _LEAN_SIGN[match["lean"]],
        "n_key_factors": float(len(match.get("key_factors", []))),
    }
```

- [ ] **Step 4: Run the extractor test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_linkage_features.py -v`
Expected: PASS. (If `tests/unit/contract/extractors/` lacks an `__init__.py`, create an empty one and `git add` it.)

- [ ] **Step 5: Write the failing joiner test**

Create `tests/unit/agents/analysts/linkage/test_joiner.py`:

```python
"""Unit tests for the linkage joiner (Phase 14 Plan 4)."""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.linkage.joiner import build_linkage_joiner_agent


async def _run(agent, state: dict):
    """Drive a BaseAgent stage once and return its single state_delta."""
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="s1",
    )
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv", agent=agent,
    )
    events = [ev async for ev in agent.run_async(ctx)]
    assert len(events) == 1
    return events[0].actions.state_delta


def _match(ticker: str) -> dict:
    """One LinkageMatch dict (matcher output shape)."""
    return {
        "ticker": ticker, "lean": "bearish", "magnitude": 0.4, "confidence": 0.6,
        "horizon_days": 21, "channel": "supplier: TSMC",
        "rationale": "Drift into the ticker via its foundry supplier.",
        "key_factors": ["channel:supplier"],
    }


@pytest.mark.asyncio
async def test_joiner_builds_durable_linkage_keys():
    """Matches inflate into linkage_verdicts + linkage_evidence."""
    agent = build_linkage_joiner_agent()
    delta = await _run(agent, {
        "tickers": ["AAPL", "MSFT"],
        "tick_id": "t-1",
        "as_of": "2026-02-10T14:00:00",
        "temp:linkage_matches": {"matches": [_match("AAPL")]},
    })

    verdicts = delta["linkage_verdicts"]["verdicts"]
    assert len(verdicts) == 1
    assert verdicts[0]["ticker"] == "AAPL"
    assert verdicts[0]["lean"] == "bearish"
    assert verdicts[0]["horizon_days"] == 21           # carried from the match

    evidence = delta["linkage_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["analyst"] == "linkage"
    assert evidence[0]["features"]["magnitude"] == 0.4  # positive signal


@pytest.mark.asyncio
async def test_joiner_quiet_tick_emits_empty_batch():
    """No matches → empty (but well-formed) durable keys, no no-data padding."""
    agent = build_linkage_joiner_agent()
    delta = await _run(agent, {
        "tickers": ["AAPL", "MSFT"],
        "tick_id": "t-1",
        "as_of": "2026-02-10T14:00:00",
        "temp:linkage_matches": {"matches": []},
    })

    assert delta["linkage_verdicts"]["verdicts"] == []
    assert delta["linkage_evidence"] == []
```

- [ ] **Step 6: Run the joiner test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_joiner.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.joiner'`.

- [ ] **Step 7: Create `src/agents/analysts/linkage/joiner.py`**

```python
"""LinkageJoinerAgent — inflate matcher output into the canonical keys.

Mirrors ``NewsJoinerAgent``: it reads the matcher's ``temp:linkage_matches``
and emits the two durable contract keys the rest of the pipeline consumes —
``linkage_verdicts`` (a ``VerdictBatch``) and ``linkage_evidence`` (a list of
``AnalystEvidence`` dumps under analyst name ``linkage``).

Unlike the news joiner it emits verdicts ONLY for matched tickers.  A linkage
tick is expected to be sparse; the strategist digest's ``_fill_missing``
neutral-fills the ``linkage`` slot for the unmatched tickers downstream, so
padding no-data verdicts here would be redundant.  A tick with no matches is
a valid quiet tick — the durable keys are still emitted, empty.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from contract.evidence import (
    AnalystEvidence,
    AnalystVerdict,
    TickerVerdict,
    VerdictBatch,
)
from contract.extractors.linkage import extract_linkage_features
from data.timeguard import resolve_as_of

_LOGGER = logging.getLogger(__name__)


class LinkageJoinerAgent(BaseAgent):
    """Build linkage_verdicts + linkage_evidence from the matcher output."""

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Inflate each match into a TickerVerdict and an AnalystEvidence row.

        Parameters
        ----------
        ctx:
            The ADK invocation context.

        Yields
        ------
        Event
            One event carrying ``linkage_verdicts`` and ``linkage_evidence``.
        """
        state   = ctx.session.state
        matches = state["temp:linkage_matches"]["matches"]
        tick_id = state.get("tick_id", "unknown")

        recorded_at = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="linkage/joiner",
        )

        verdicts: list[TickerVerdict] = []
        evidence: list[dict]          = []

        for match in matches:
            # Build the analyst verdict (rationale surface; report stays None).
            verdict = AnalystVerdict(
                lean         = match["lean"],
                magnitude    = match["magnitude"],
                confidence   = match["confidence"],
                horizon_days = match["horizon_days"],
                rationale    = match["rationale"],
                key_factors  = match["key_factors"],
                is_no_data   = False,
            )

            verdicts.append(TickerVerdict(ticker=match["ticker"], **verdict.model_dump()))

            ev = AnalystEvidence(
                analyst     = "linkage",
                ticker      = match["ticker"],
                tick_id     = tick_id,
                recorded_at = recorded_at,
                features    = extract_linkage_features(match),
                verdict     = verdict,
            )
            evidence.append(ev.model_dump(mode="json"))

        if not matches:
            _LOGGER.info("linkage joiner: no matches this tick — quiet tick (empty verdicts)")
        else:
            _LOGGER.info("linkage joiner: emitted %d linkage verdict(s)", len(verdicts))

        batch = VerdictBatch(verdicts=verdicts)

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "linkage_verdicts": batch.model_dump(),
                "linkage_evidence": evidence,
            }),
        )


def build_linkage_joiner_agent() -> LinkageJoinerAgent:
    """Construct the linkage joiner stage.

    Returns
    -------
    LinkageJoinerAgent
        The configured stage.
    """
    return LinkageJoinerAgent(name="LinkageJoiner")
```

- [ ] **Step 8: Run the joiner test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_joiner.py -v`
Expected: both tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/contract/extractors/linkage.py src/agents/analysts/linkage/joiner.py tests/unit/contract/extractors/test_linkage_features.py tests/unit/agents/analysts/linkage/test_joiner.py
git commit -m "feat(linkage): add joiner stage + deterministic feature extractor

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Branch assembly + pipeline wiring

**Files:**
- Create: `src/agents/analysts/linkage/agent.py`
- Modify: `src/orchestrator/pipeline.py` (`_build_analyst_pool` signature + linkage branch; `build_pipeline` call site)
- Test: `tests/unit/agents/analysts/linkage/test_branch.py`
- Test: `tests/unit/orchestrator/test_pipeline_has_linkage_branch.py`

**Interfaces:**
- Consumes: all five stage factories (Tasks 6–10); `agents.isolated_failure.IsolatedFailureWrapper`.
- Produces (consumed by Task 15):
  - `build_linkage_branch(*, db_session=None) -> IsolatedFailureWrapper` wrapping `SequentialAgent(name="LinkageAnalystBranch", sub_agents=[staleness, digester, registry, matcher, joiner])`; wrapper name `"LinkageAnalystBranch_isolated"`, `analyst="linkage"`, `ticker="_ALL"`.
  - `_build_analyst_pool(tickers, db_session=None)` now appends the linkage branch to the `AnalystPool` sub-agents.

- [ ] **Step 1: Write the failing branch-assembly test**

Create `tests/unit/agents/analysts/linkage/test_branch.py`:

```python
"""The linkage branch assembles the five stages under an isolation wrapper."""
from __future__ import annotations

from agents.analysts.linkage.agent import build_linkage_branch
from agents.isolated_failure import IsolatedFailureWrapper


def test_branch_is_isolation_wrapped_sequential_of_five_stages():
    """build_linkage_branch → IsolatedFailureWrapper(SequentialAgent[5 stages])."""
    branch = build_linkage_branch(db_session=None)

    assert isinstance(branch, IsolatedFailureWrapper)
    assert branch.name == "LinkageAnalystBranch_isolated"
    assert branch.analyst == "linkage"

    inner = branch.inner
    assert inner.name == "LinkageAnalystBranch"

    stage_names = [s.name for s in inner.sub_agents]
    assert stage_names == [
        "LinkageStaleness",
        "LinkageDigester",
        "LinkageRegistry",
        "LinkageMatcher",
        "LinkageJoiner",
    ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_branch.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.linkage.agent'`.

- [ ] **Step 3: Create `src/agents/analysts/linkage/agent.py`**

```python
"""Linkage analyst branch factory (Phase 14 Plan 4).

Assembles the five linkage stages into one SequentialAgent and wraps the
whole branch in an ``IsolatedFailureWrapper``.  Whole-branch (not per-stage)
isolation is deliberate: the stages are sequentially interdependent (the
registry reads the digest; the matcher reads the active set), so a mid-branch
failure cannot leave a usable partial result.  Containing the failure at the
branch boundary lets the analyst pool and the other three analysts continue —
the strategist digest neutral-fills the absent ``linkage`` slot for that tick
— while ``IsolatedFailureWrapper`` logs a loud structured ``branch_failed``
record (which operators should treat as an alert, e.g. to rebuild a stale
exposure map).
"""
from __future__ import annotations

from typing import Any

from google.adk.agents import SequentialAgent

from agents.analysts.linkage.digester import build_linkage_digester_agent
from agents.analysts.linkage.joiner import build_linkage_joiner_agent
from agents.analysts.linkage.matcher import build_linkage_matcher_agent
from agents.analysts.linkage.registry_stage import build_linkage_registry_agent
from agents.analysts.linkage.staleness import build_linkage_staleness_agent
from agents.isolated_failure import IsolatedFailureWrapper


def build_linkage_branch(*, db_session: Any = None) -> IsolatedFailureWrapper:
    """Construct the isolation-wrapped linkage analyst branch.

    Parameters
    ----------
    db_session:
        Optional SQLAlchemy session forwarded to the registry stage for the
        durable ``linkage_events`` audit write. ``None`` → in-memory-only.

    Returns
    -------
    IsolatedFailureWrapper
        The branch wrapped for whole-branch failure isolation.
    """
    branch = SequentialAgent(
        name="LinkageAnalystBranch",
        sub_agents=[
            build_linkage_staleness_agent(),
            build_linkage_digester_agent(),
            build_linkage_registry_agent(db_session=db_session),
            build_linkage_matcher_agent(),
            build_linkage_joiner_agent(),
        ],
    )

    # ``ticker="_ALL"`` — the branch is watchlist-wide, not per-ticker; the
    # sentinel keeps the failure log's ticker field populated.
    return IsolatedFailureWrapper(
        name="LinkageAnalystBranch_isolated",
        inner=branch,
        analyst="linkage",
        ticker="_ALL",
    )
```

- [ ] **Step 4: Run the branch test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/linkage/test_branch.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing pipeline-wiring test**

Create `tests/unit/orchestrator/test_pipeline_has_linkage_branch.py`:

```python
"""The AnalystPool includes the isolation-wrapped linkage branch."""
from __future__ import annotations

from orchestrator.pipeline import _build_analyst_pool


def test_analyst_pool_includes_linkage_branch():
    """_build_analyst_pool appends the linkage branch to the pool."""
    pool = _build_analyst_pool(["AAPL"], db_session=None)

    sub_names = [s.name for s in pool.sub_agents]
    assert "LinkageAnalystBranch_isolated" in sub_names
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/orchestrator/test_pipeline_has_linkage_branch.py -v`
Expected: FAIL — either a `TypeError` (`_build_analyst_pool` takes no `db_session`) or an `AssertionError` (branch absent).

- [ ] **Step 7: Wire the linkage branch into `src/orchestrator/pipeline.py`**

Change the `_build_analyst_pool` signature and body. Replace:

```python
def _build_analyst_pool(tickers: list[str]):
```

with:

```python
def _build_analyst_pool(tickers: list[str], db_session=None):
```

Add the import alongside the other branch imports inside the function:

```python
    from agents.analysts.linkage.agent import build_linkage_branch
```

Build the branch just before the `return`:

```python
    # Phase 14 Plan 4: the linkage branch is watchlist-wide (not per-ticker
    # fan-out).  It reads state["macro_articles"] (Plan 3), makes at most two
    # flash calls per tick, and writes durable linkage_verdicts /
    # linkage_evidence.  ``db_session`` threads through for the registry's
    # durable audit write.
    linkage_branch = build_linkage_branch(db_session=db_session)
```

and append it to the returned `ParallelAgent` sub-agents:

```python
    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            parallel_deterministic,
            fundamental_branch,
            news_branch,
            linkage_branch,
            # _build_smart_money_analyst(h.smart_money) — shelved (see docstring).
            # Re-enable by re-importing _build_smart_money_analyst above and
            # appending it here once notable_holders / politician trades have
            # working PIT-correct providers.
        ],
    )
```

Finally, thread `db_session` from `build_pipeline`. Replace the call `_build_analyst_pool(tickers)` inside `build_pipeline` with:

```python
            _build_analyst_pool(tickers, db_session),
```

- [ ] **Step 8: Run both wiring tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/orchestrator/test_pipeline_has_linkage_branch.py tests/unit/agents/analysts/linkage/test_branch.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agents/analysts/linkage/agent.py src/orchestrator/pipeline.py tests/unit/agents/analysts/linkage/test_branch.py tests/unit/orchestrator/test_pipeline_has_linkage_branch.py
git commit -m "feat(linkage): assemble the linkage branch and wire it into the analyst pool

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Strategist wiring — evidence writer, context shim, digest weight, prompt

**Design decision — the `DEFAULT_ANALYST_WEIGHTS` dilution trade-off (READ THIS).**
Spec §6.3 mandates adding `linkage` to `contract.digest.DEFAULT_ANALYST_WEIGHTS`. Doing so has a known side-effect that the implementer and reviewer must both accept before merging:

- `contract/digest.py` computes `total_weight = sum(weights.values())` as the denominator for every ticker's aggregate magnitude. Adding a fourth weighted analyst raises the denominator from `3.0` to `4.0`, so on a tick where linkage produces **no** verdict for a ticker (the common case — linkage is inherently sparse), that ticker's `_fill_missing` neutral-fills the `linkage` slot and its aggregate magnitude is scaled by `3/4`. Every unexposed ticker's conviction shrinks ~25%.
- `_fill_missing` also emits a `missing_analyst_slot` WARNING for each neutral-filled analyst, so quiet linkage ticks will log one such warning per unexposed ticker.

This is a **consequential change to shared aggregation maths** (per the house "assume-I'm-wrong / mutual-agreement" rule). Do **not** silently re-tune the other weights to compensate, and do **not** invent a sparse-analyst exemption in `digest.py` as part of this task — that is a separate design decision. The task adds `"linkage": 1.0` exactly as the spec directs, and the **eval gate (Plan 2 → Plan 4) is where the dilution's real cost is measured**. If the scoreboard shows the dilution degrading the surviving analysts, the fix is decided then, with data. Record the trade-off in the commit body so it is not lost.

The `missing_analyst_slot` warning volume is expected and benign here; it is the loud-failure convention working as designed (a neutral-fill is announced, never silent). It is not a bug to suppress.

**Files:**
- Modify: `src/agents/contract/evidence_writer.py` (`_EVIDENCE_KEYS` + docstring)
- Modify: `src/agents/strategist/context_shim.py` (index + per-ticker assignment)
- Modify: `src/contract/digest.py` (`DEFAULT_ANALYST_WEIGHTS`)
- Modify: `src/agents/strategist/prompts.py` (`_RAW_INSTRUCTION`)
- Test: `tests/unit/agents/strategist/test_linkage_wiring.py`

**Interfaces:**
- Consumes: `linkage_evidence` (Task 10); `contract.digest.aggregate_*` (unchanged); `_index_evidence` (existing helper in `context_shim`).
- Produces: linkage evidence persisted under analyst label `linkage`; `per_analyst["linkage"]` present in `TickerEvidence` when a linkage verdict exists; `linkage` a first-class key in `DEFAULT_ANALYST_WEIGHTS`.

- [ ] **Step 1: Write the failing wiring test**

Create `tests/unit/agents/strategist/test_linkage_wiring.py`:

```python
"""Linkage is a first-class strategist input (Phase 14 Plan 4)."""
from __future__ import annotations

from agents.contract.evidence_writer import _EVIDENCE_KEYS
from contract.digest import DEFAULT_ANALYST_WEIGHTS


def test_linkage_is_a_persisted_evidence_key():
    """EvidenceWriter drains linkage_evidence under the 'linkage' label."""
    assert ("linkage_evidence", "linkage") in _EVIDENCE_KEYS


def test_linkage_has_a_default_digest_weight():
    """The digest aggregates linkage as a weighted analyst."""
    assert DEFAULT_ANALYST_WEIGHTS["linkage"] == 1.0


def test_context_shim_indexes_linkage_into_per_analyst():
    """A linkage evidence row surfaces as per_analyst['linkage']."""
    from agents.strategist.context_shim import _index_evidence

    state = {
        "linkage_evidence": [
            {
                "analyst": "linkage", "ticker": "AAPL", "tick_id": "t-1",
                "recorded_at": "2026-02-10T14:00:00",
                "features": {"magnitude": 0.4},
                "verdict": {
                    "lean": "bearish", "magnitude": 0.4, "confidence": 0.6,
                    "horizon_days": 21, "rationale": "supplier drift",
                    "key_factors": ["channel:supplier"], "is_no_data": False,
                },
            }
        ],
    }

    indexed = _index_evidence(state, "linkage_evidence")
    assert "AAPL" in indexed
    assert indexed["AAPL"].analyst == "linkage"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/strategist/test_linkage_wiring.py -v`
Expected: FAIL — `("linkage_evidence", "linkage")` absent from `_EVIDENCE_KEYS`; `KeyError: 'linkage'` on `DEFAULT_ANALYST_WEIGHTS`.

- [ ] **Step 3: Add `linkage` to `_EVIDENCE_KEYS` in `src/agents/contract/evidence_writer.py`**

Replace the tuple:

```python
_EVIDENCE_KEYS = (
    ("technical_evidence", "technical"),
    ("fundamental_evidence", "fundamental"),
    ("news_evidence", "news"),
    ("smart_money_evidence", "smart_money"),
    ("social_evidence", "social"),
)
```

with:

```python
_EVIDENCE_KEYS = (
    ("technical_evidence", "technical"),
    ("fundamental_evidence", "fundamental"),
    ("news_evidence", "news"),
    ("smart_money_evidence", "smart_money"),
    ("social_evidence", "social"),
    # "linkage_evidence" / "linkage" added in Phase 14 Plan 4 — the economic-
    # links drift analyst.  Sparse by design: most ticks write no linkage rows.
    ("linkage_evidence", "linkage"),
)
```

- [ ] **Step 4: Index linkage in `src/agents/strategist/context_shim.py`**

After the line `sm   = _index_evidence(state, "smart_money_evidence")` add:

```python
        # Phase 14 Plan 4: the economic-links drift analyst.  Sparse — present
        # only for tickers the matcher flagged this tick.
        link = _index_evidence(state, "linkage_evidence")
```

After the `smart_money` per-ticker block:

```python
            if t in sm:
                per_analyst["smart_money"] = sm[t]
```

add:

```python
            if t in link:
                per_analyst["linkage"]     = link[t]
```

- [ ] **Step 5: Add `linkage` to `DEFAULT_ANALYST_WEIGHTS` in `src/contract/digest.py`**

Replace:

```python
DEFAULT_ANALYST_WEIGHTS: dict[str, float] = {
    "technical":   1.0,
    "fundamental": 1.0,
    "news":        1.0,
}
```

with:

```python
DEFAULT_ANALYST_WEIGHTS: dict[str, float] = {
    "technical":   1.0,
    "fundamental": 1.0,
    "news":        1.0,
    # "linkage" added in Phase 14 Plan 4 (spec §6.3).  DELIBERATE TRADE-OFF:
    # linkage is a sparse analyst, so on ticks where it produces no verdict for
    # a ticker the aggregate magnitude is diluted by the raised total_weight
    # denominator (3->4) and _fill_missing logs a missing_analyst_slot warning.
    # This is accepted for the eval gate (Plan 2 -> Plan 4): the scoreboard
    # measures whether linkage's signal outweighs the dilution.  Do NOT re-tune
    # the other weights or add a sparse-analyst exemption without agreement.
    "linkage":     1.0,
}
```

- [ ] **Step 6: Describe the linkage stream in `src/agents/strategist/prompts.py`**

In `_RAW_INSTRUCTION`, immediately after the first paragraph of the `## Reading analyst reports` section (the paragraph ending `...which signal you overweighted\nand why.`) insert:

```python
The **linkage** analyst is sparse and appears only for a ticker when a recent
market/sector/merger event maps onto that ticker's economic exposure (a
supplier, customer, sector peer, commodity, or regulatory channel). It targets
the documented drift that follows such events over days-to-weeks, not the
initial move — so a linkage lean is a forward-looking positioning call, not a
reaction to today's price. Its absence for a ticker means "no active linked
event", never "neutral view"; weigh it only when present.
```

- [ ] **Step 7: Run the wiring test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/strategist/test_linkage_wiring.py -v`
Expected: all three tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agents/contract/evidence_writer.py src/agents/strategist/context_shim.py src/contract/digest.py src/agents/strategist/prompts.py tests/unit/agents/strategist/test_linkage_wiring.py
git commit -m "feat(linkage): wire linkage into evidence writer, context shim, digest, and strategist prompt

Adds linkage as a first-class analyst stream. Note the deliberate
DEFAULT_ANALYST_WEIGHTS dilution trade-off (sparse analyst raises the
aggregate denominator) — accepted for the Plan 2 -> Plan 4 eval gate, to
be revisited with scoreboard data, not re-tuned pre-eval.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Scoreboard — linkage as a first-class analyst horizon

**Design decision (resolved ambiguity).** The scoreboard's `primary_horizon_by_analyst` is the single horizon at which an analyst's predictive power is ranked. Economic-links drift is a ~1-month effect (Cohen & Frazzini 2008, spec §2), so linkage's primary horizon is **20** — the closest bucket in `forward_return_horizons_days` (`[1, 5, 20]`) to one month. (An earlier draft tentatively used 5; 20 is the literature-defensible choice and matches the dominant sector channel's horizon.)

**Files:**
- Modify: `config/backtest_settings.json` (`primary_horizon_by_analyst`)
- Modify: `config/README.md` (the `primary_horizon_by_analyst` default line)
- Test: `tests/unit/backtest/test_linkage_scoreboard_horizon.py`

**Interfaces:**
- Consumes: `backtest.settings.get_backtest_settings()` (existing loader).
- Produces: `primary_horizon_by_analyst["linkage"] == 20` in loaded settings.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/backtest/test_linkage_scoreboard_horizon.py`:

```python
"""Linkage ranks at its ~1-month drift horizon (Phase 14 Plan 4)."""
from __future__ import annotations

from backtest.settings import get_backtest_settings


def test_linkage_primary_horizon_is_twenty_days():
    """The scoreboard scores linkage at the 20-day (≈1 month) horizon."""
    settings = get_backtest_settings()
    assert settings.primary_horizon_by_analyst["linkage"] == 20
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_linkage_scoreboard_horizon.py -v`
Expected: FAIL with `KeyError: 'linkage'`.

- [ ] **Step 3: Add the horizon to `config/backtest_settings.json`**

In the `primary_horizon_by_analyst` object, add the `linkage` entry:

```json
  "primary_horizon_by_analyst": {
    "news":        1,
    "fundamental": 20,
    "technical":    5,
    "social":       1,
    "smart_money": 20,
    "linkage":     20
  },
```

- [ ] **Step 4: Update the `config/README.md` default line**

In the `primary_horizon_by_analyst` row, change the trailing default to include linkage and note its rationale:

```
Default: `{"news": 1, "fundamental": 20, "technical": 5, "social": 1, "smart_money": 20, "linkage": 20}` (linkage ranks at the ~1-month economic-links drift horizon).
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_linkage_scoreboard_horizon.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add config/backtest_settings.json config/README.md tests/unit/backtest/test_linkage_scoreboard_horizon.py
git commit -m "feat(linkage): score linkage at its ~1-month drift horizon (20d)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: Driver — reset the linkage registry per window replay

**Design note.** The in-memory `LinkageEventRegistry` (Task 4) is the active-events source of truth. Like Plan 2's news-history store it must be reset at the start of each window replay so drift windows never leak across runs (PIT-correctness). Plan 2 already adds a `reset_news_history_store()` call at the top of `BacktestDriver.run`; this task adds the sibling call. Trust Plan 2's call lands — add ours beside it.

**Files:**
- Modify: `src/backtest/driver.py` (`BacktestDriver.run`, before the tick loop)
- Test: `tests/unit/backtest/test_driver_resets_linkage_registry.py`

**Interfaces:**
- Consumes: `agents.analysts.linkage.registry.reset_linkage_registry` (Task 4).
- Produces: a freshly-reset registry at the start of every `run`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/backtest/test_driver_resets_linkage_registry.py`:

```python
"""BacktestDriver.run resets the linkage registry (PIT-correctness)."""
from __future__ import annotations

import inspect

from backtest import driver


def test_driver_run_resets_the_linkage_registry():
    """run() calls reset_linkage_registry so drift windows don't leak."""
    source = inspect.getsource(driver.BacktestDriver.run)
    assert "reset_linkage_registry()" in source
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_driver_resets_linkage_registry.py -v`
Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Add the reset call in `src/backtest/driver.py`**

Add the import near the top of the module (alongside the other analyst imports):

```python
from agents.analysts.linkage.registry import reset_linkage_registry
```

In `BacktestDriver.run`, immediately before the `for tick in schedule:` loop (next to Plan 2's `reset_news_history_store()` call), add:

```python
        # Phase 14 Plan 4: clear the in-memory linkage event registry so
        # active drift windows never leak across window replays (PIT-correct).
        reset_linkage_registry()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_driver_resets_linkage_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/driver.py tests/unit/backtest/test_driver_resets_linkage_registry.py
git commit -m "feat(linkage): reset the linkage registry per window replay (PIT-correct)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 15: Integration smoke — linkage verdicts appear with positive signal

**Design note (silent-degradation guard, spec §8).** The final task asserts an end-to-end tick produces a **populated** linkage verdict — presence with the right shape, never merely absence of error. It drives the assembled stages with injected stub `llm_fn`s (no live LLM, no network) and a hand-built exposure map, feeding a `macro_articles` fixture guaranteed to survive staleness, digest into one event, match one exposed ticker, and join into one `linkage` verdict. It closes with a full-suite + lint gate.

**Files:**
- Test: `tests/integration/test_linkage_branch_smoke.py`

**Interfaces:**
- Consumes: the five stage factories (Tasks 6–10) and their injectable seams (`llm_fn` / `store` / `exposure_map`).

- [ ] **Step 1: Write the integration smoke test**

Create `tests/integration/test_linkage_branch_smoke.py`:

```python
"""End-to-end linkage branch smoke: a verdict appears with positive signal.

Drives the five stages in sequence over one shared state dict using injected
stubs (no live LLM, no network).  Asserts the durable linkage_verdicts key is
POPULATED with a well-shaped verdict — the silent-degradation guard (spec §8).
"""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.linkage.digester import build_linkage_digester_agent
from agents.analysts.linkage.exposure import ExposureMap
from agents.analysts.linkage.joiner import build_linkage_joiner_agent
from agents.analysts.linkage.matcher import build_linkage_matcher_agent
from agents.analysts.linkage.registry import reset_linkage_registry
from agents.analysts.linkage.registry_stage import build_linkage_registry_agent
from agents.analysts.linkage.schemas import (
    LinkageDigest,
    LinkageMatch,
    LinkageMatchBatch,
)
from agents.analysts.linkage.staleness import build_linkage_staleness_agent


class _FarStore:
    """Staleness store stub — every article is novel (similarity 0.0)."""

    def record(self, namespace: str, text: str, key: str) -> None:
        """No-op record (the smoke test does not exercise persistence)."""

    def staleness(self, namespace: str, text: str) -> float:
        """Return 0.0 so the article always survives the freshness filter."""
        return 0.0


async def _digest_llm(prompt, schema):
    """Return one macro event naming AAPL (bearish surprise)."""
    return LinkageDigest.model_validate({
        "events": [{
            "summary": "New tariffs on imported semiconductors announced.",
            "category": "macro", "entities": ["AAPL"],
            "surprise_direction": "bearish", "novelty": 0.9,
        }],
    })


async def _match_llm(prompt, schema):
    """Map the event onto AAPL via its supplier channel."""
    return LinkageMatchBatch(matches=[LinkageMatch.model_validate({
        "ticker": "AAPL", "lean": "bearish", "magnitude": 0.4,
        "confidence": 0.6, "horizon_days": 20, "channel": "supplier: foundry",
        "rationale": "Tariffs raise AAPL's foundry input costs over weeks.",
        "key_factors": ["channel:supplier"],
    })])


def _exposure_map() -> ExposureMap:
    """A fresh map exposing AAPL on the supplier channel."""
    return ExposureMap.model_validate({
        "built_at": "2026-02-10T00:00:00",
        "exposures": {"AAPL": {"supplier": ["foundry"]}},
    })


async def _drive(agent, state: dict) -> None:
    """Run one stage, folding its state_delta back into the shared state."""
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="smoke", user_id="u", state=state, session_id="s",
    )
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv", agent=agent,
    )
    async for ev in agent.run_async(ctx):
        if ev.actions and ev.actions.state_delta:
            state.update(ev.actions.state_delta)


@pytest.mark.asyncio
async def test_linkage_branch_produces_a_populated_verdict():
    """A macro article drives one non-empty, well-shaped linkage verdict."""
    reset_linkage_registry()

    state: dict = {
        "tickers": ["AAPL", "MSFT"],
        "tick_id": "smoke-1",
        "as_of": "2026-02-10T14:00:00",
        "macro_articles": [{
            "headline": "US imposes new semiconductor tariffs",
            "summary": "Broad tariffs on imported chips take effect next month.",
            "url": "https://example.test/tariffs", "tickers": ["AAPL"],
            "article_id": "art-1",
        }],
    }

    await _drive(build_linkage_staleness_agent(store=_FarStore()), state)
    await _drive(build_linkage_digester_agent(llm_fn=_digest_llm), state)
    await _drive(build_linkage_registry_agent(db_session=None), state)
    await _drive(
        build_linkage_matcher_agent(llm_fn=_match_llm, exposure_map=_exposure_map()),
        state,
    )
    await _drive(build_linkage_joiner_agent(), state)

    verdicts = state["linkage_verdicts"]["verdicts"]
    assert len(verdicts) == 1                     # POSITIVE signal, not absence
    v = verdicts[0]
    assert v["ticker"] == "AAPL"
    assert v["lean"] == "bearish"
    assert v["horizon_days"] == 20
    assert 0.0 < v["magnitude"] <= 1.0

    evidence = state["linkage_evidence"]
    assert evidence[0]["analyst"] == "linkage"
    assert evidence[0]["features"]["magnitude"] == 0.4
```

- [ ] **Step 2: Run the smoke test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_linkage_branch_smoke.py -v`
Expected: PASS. (If it fails, the exact stub field names above are the contract each stage must honour — reconcile the failing stage, do not weaken the assertions.)

- [ ] **Step 3: Full-suite + lint gate**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: the whole suite passes (no regressions from the strategist-wiring or digest-weight changes).

Run: `.venv/bin/python -m ruff check src/ tests/`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_linkage_branch_smoke.py
git commit -m "test(linkage): end-to-end smoke — a linkage verdict appears with positive signal

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review

Run with fresh eyes against the spec (`docs/Phase14-analyst-refactor/specs/analyst-drift-refactor-design.md`, §6.3 + §7 + §8) and the sibling plans.

**1. Spec coverage**

| Spec §6.3 / §7 / §8 requirement | Task |
| --- | --- |
| Staleness pre-filter, `macro` namespace, config threshold | Task 6 |
| Event digester, one flash call/tick, closed-vocab events | Task 7 |
| Exposure map — per-ticker channels, offline build, weekly staleness cap, never on tick path | Task 5 |
| Matcher — one flash call/tick, active events × exposure map, `linkage` `AnalystName`, `horizon`, quiet tick logged | Tasks 2, 9 |
| Event registry — SQLite `(event_id, summary, category, tickers, direction, event_date, horizon_days, source_article_ids)`, expiry past horizon, active-events feed | Tasks 4, 8 |
| Strategist wiring — `context_shim` indexes `linkage_evidence`; `DEFAULT_ANALYST_WEIGHTS` gains `linkage`; prompt describes stream | Task 12 |
| Loud failures; digester/matcher distinguish quiet tick from call failure; exposure-map staleness fails loud | Tasks 5–9 (raises), branch containment Task 11 |
| Registry writes transactional with the tick | Task 8 (commit gated on db_session + newly-added) |
| Emit schemas; no LLM-emitted verdict for unexposed tickers | Tasks 3, 10 |
| D4 token economics — ≤2 flash calls/tick, staleness kills volume, skip LLM on quiet ticks | Tasks 6 (pre-filter), 7 + 9 (quiet-skip) |
| Unit tests (staleness, registry decay, digester/matcher schema, horizon propagation) | Tasks 3–10 |
| Integration smoke — verdicts appear with positive signal | Task 15 |
| Scoreboard treats `linkage` first-class in (ticker, window) clustering | Task 13 |

No gaps. §6.4's rejected alternatives are honoured (no folded-into-news verdict; no per-tick full mapper; exposure facts cached; events persisted not recomputed).

**2. Placeholder scan**

No `TBD` / `TODO` / "similar to Task N" / "add error handling" strings in any step. Every code step carries complete code. The only cross-task references name interfaces fully specified in that task's **Interfaces** block.

**3. Type consistency**

- `llm_fn(prompt, schema) -> BaseModel` — identical async signature across Tasks 5, 7, 9, and the Task 15 stubs.
- `extract_linkage_features(match: dict) -> dict[str, float]` — Task 10 defines; Task 10 joiner + Task 15 consume.
- `LinkageMatch` fields (`ticker`, `lean`, `magnitude`, `confidence`, `horizon_days`, `channel`, `rationale`, `key_factors`) — consistent across schema (Task 3), matcher (Task 9), joiner (Task 10), extractor (Task 10), and smoke stubs (Task 15).
- `ExposureMap` (`built_at`, `exposures`) — Task 5 defines; Tasks 9 + 15 consume.
- Durable keys `linkage_verdicts` (`VerdictBatch`) + `linkage_evidence` (`list[AnalystEvidence]` dumps) — produced Task 10, consumed Tasks 11–13.
- `build_linkage_*_agent` factory names — defined Tasks 6–10, composed Task 11, driven Task 15.
- `reset_linkage_registry` — defined Task 4, called Tasks 14 + 15.

Consistent throughout.

**4. Cross-plan trust (no defensive shims)**

`state["macro_articles"]` (Plan 3) and Plan 2's embedding store / `reset_news_history_store()` are read as guaranteed-present — an absent key is a loud `KeyError`, never a handled branch. Task 14's reset sits beside Plan 2's. No fallbacks for sibling-owned state.

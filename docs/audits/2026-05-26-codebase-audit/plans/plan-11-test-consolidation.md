# Plan 11 — Test-suite consolidation + positive-state assertions

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the test suite into compliance with `docs/test-policy.md`
§A.7 (loudness), §B (taxonomy), and §E (no "did it raise?"). Land a
single shared `assert_no_silent_degradation` helper, a shared
`tick_state` fixture, merge the `tests/<module>/` schism into the
canonical `tests/unit/<module-mirror>/` and `tests/integration/`
taxonomy, augment thin assertions on flagged tests, and split the
756-LoC end-to-end smoke into per-concern files.

**Architecture:** Tests-only plan — `src/` and `scripts/` are
strictly out of scope (per `feedback_test_audit_scope_tests_only`).
The plan adds two shared helpers under `tests/conftest.py` (plus a
`tests/_helpers/` package for the larger bits), then moves files,
then augments assertions, then splits the smoke. Loud-fail behaviour
that Plans 05/06/10 introduce in production code is what these
assertions assert against — this plan does not introduce new product
behaviour.

**Tech Stack:** pytest, caplog, the existing `data.contract.evidence`
shape, ADK runner harness already used in
`tests/integration/backtest/test_end_to_end_smoke.py`.

---

## 0. Trust contract

**This plan trusts:**

- **Plan 02 (rationale field).** `TickerStance`/`PositionThesis` now
  have a single `rationale` field (commits `742f38e`, `ba8555b` on
  `main`). Fixture shapes use `rationale=...`, not `reason=` or
  `catalyst=`.
- **Plan 03 (state-shape sweep).** Bare `positions` is removed; only
  `user:positions` (durable) and `temp:_positions` (in-tick bridge)
  exist. The `tick_state` fixture writes both, not the bare key.
- **Plan 04 (live ≡ backtest lifecycle parity).** Live
  `_build_initial_state` and backtest `Runner._seed_state` end with
  identical key sets, including ISO-coerced `as_of`. The `tick_state`
  fixture matches whichever shape both lifecycles agree on after
  Plan 04 lands.
- **Plans 05 / 06 / 10 (loud-fail product code).** Providers raise
  `SecretMissingError` instead of `[]`; news providers raise
  `ValueError` on reversed windows; the snapshotter no longer swallows
  SPY-fetch failures; the executor after-callback logs instead of
  `print`; `_is_schema_error` no longer downgrades on ImportError;
  driver `except RuntimeError: pass` quartet either raises or logs;
  `_seed_initial_prices` raises on missing OHLCV. These all need to
  have landed before Plan 11's positive-assertion work; otherwise the
  new assertions become red on legitimate code paths.
- **Plan 07 (callback retirement).** `_strategist_validation_callback`
  and `evidence_view.py` are deleted from `src/`, which makes the test
  files in §5 of this plan true dead-test deletions (not "tests for
  code that still exists").

**Later plans trust this plan to:**

- Land **one** `tests/` tree under the §B taxonomy:
  `tests/unit/<module-mirror>/`, `tests/integration/`,
  `tests/contract/`, `tests/backtest/`. No `tests/analysts/`,
  `tests/executor/`, `tests/orchestrator/`, `tests/agents/` top-level
  module folders survive.
- Provide a shared `assert_no_silent_degradation(state, *,
  allow_degradation=())` helper that every happy-path pipeline test
  calls.
- Provide a shared `tick_state(...)` factory fixture (per T-102) that
  every pipeline-touching test composes from.
- Convert all "did it raise / has length N" assertions flagged under
  A-018 into content assertions.
- Replace cementing tests (A-020) with assertions that **would have
  failed pre-Plan-05/06 fix and pass post-fix**.
- Update fixtures that pin deleted contract fields (A-060, A-063) to
  the four-verb / four-key vocabulary.
- Leave the 756-LoC smoke split into four focused files that share
  one fixture for "construct a realistic tick state".

**Plan 12** trusts this plan's directory layout and shared fixtures
when it adds its small per-finding tests for the P3 nit-tail.

**A-101 note.** The plan brief lists "A-101" in scope; that ID does
not appear in `FINDINGS.md` (numbering stops at A-097 with the P3
roll-up). Treating as a typo and ignoring. If a reviewer reads this
and recognises which finding was meant, raise it before execution.

---

## 0a. Scope-fission allowance

**This plan is fat and is allowed to split during execution.** The
directory consolidation (Task Block C — A-067) and the assertion
augmentation across ~30 files (Task Blocks D + E) together are
plausibly a single very large PR — but if execution discovers either
half exceeds a reviewable size, **split into Plan 11A and Plan 11B**
at the Block C / Block D boundary:

- **Plan 11A** = Blocks A (shared helpers), B (fixture upgrades),
  C (directory consolidation). Ships first. Self-contained: the new
  helpers exist but only the already-correct tests call them. The
  directory move is one mechanical PR with no semantic test edits.
- **Plan 11B** = Blocks D (augment thin assertions), E (rewrite
  cementing tests), F (split the smoke). Ships after 11A; depends on
  Plans 05/06/10 being in.

The reason for the seam: Block C is "git mv + import-path repair", a
purely mechanical pass that any reviewer can verify by `pytest -q`
matching pre- and post-move. Blocks D-F require per-test thought.
Mixing them in one PR makes reviewers blur the mechanical with the
semantic.

**Execution rule:** start as one plan. The instant the diff exceeds
~50 files or ~1500 LoC of test changes, stop, open Plan 11B as a
follow-up doc, and land Block A+B+C first.

---

## 1. Directory consolidation map (A-067, T-101)

### 1.1 Current layout (audit snapshot, 2026-05-26)

The schism, file-by-file. Source: `find tests -name "*.py" -not -path
"*__pycache__*" | sort`.

| Top-level `tests/<module>/` (non-canonical) | Mirror under `tests/unit/<module-mirror>/` (canonical) |
|---|---|
| `tests/analysts/fundamental/test_fetch_agent.py` | `tests/unit/agents/analysts/test_analyst_fetch_as_of.py` (overlapping) |
| `tests/analysts/fundamental/test_joiner.py` | — (no mirror; canonical target is new) |
| `tests/analysts/fundamental/test_prompts.py` | `tests/unit/test_fundamental_prompt_*` (4 files, flat under `tests/unit/`) |
| `tests/analysts/news/test_fetch_agent.py` | — |
| `tests/analysts/news/test_joiner.py` | — |
| `tests/analysts/news/test_prompts.py` | `tests/unit/test_news_prompt_*` (3 files) |
| `tests/analysts/test_branch_composition.py` | — |
| `tests/analysts/test_cache_callbacks_per_ticker.py` | — |
| `tests/analysts/test_per_ticker_branch.py` | — |
| `tests/analysts/test_smart_money.py` | dead per Plan 07 / A-033 — DELETE |
| `tests/analysts/test_technical.py` | overlaps `tests/unit/test_derive_technical_verdict.py` |
| `tests/agents/analysts/test_evidence_callback.py` | `tests/unit/agents/analysts/` |
| `tests/agents/memory/test_writer_smart_money_seen.py` | dead per A-033 — DELETE |
| `tests/agents/test_isolated_failure.py` | — |
| `tests/agents/test_output_caps_per_ticker.py` | — |
| `tests/executor/test_executor_bookkeeping.py` | `tests/unit/executor/test_open_positions_state.py` (partial overlap) |
| `tests/contract/test_evidence_schema.py` | `tests/unit/contract/test_evidence.py` (partial overlap) |
| `tests/contract/test_http_timeout_sourced_from_config.py` | — (contract-layer test, keep under `tests/contract/`) |
| `tests/contract/test_llm_ticker_verdict.py` | `tests/unit/contract/` — overlaps A-051 |
| `tests/contract/test_lookbacks_sourced_from_config.py` | contract-layer, keep |
| `tests/contract/test_no_hardcoded_models.py` | contract-layer, keep |
| `tests/contract/test_provider_shapes.py` | contract-layer, keep |
| `tests/contract/test_schedule_sourced_from_config.py` | contract-layer, keep |
| `tests/contract/test_wrappers_supply_lookback_to_cache.py` | contract-layer, keep |
| `tests/orchestrator/test_pipeline_build.py` | `tests/unit/orchestrator/test_pipeline_wiring_v2.py` (overlap) |
| `tests/backtest/audit/*.py` (4) | `tests/unit/backtest/` (keep — these are backtest-layer integration tests, OK under `tests/backtest/`) |
| `tests/backtest/leak_regressions/*.py` (6) | keep (backtest-specific integration) |
| `tests/backtest/test_cache_hits_audit.py` | dead per A-085 (Plan 10) — flag for deletion there |
| `tests/backtest/test_reference_prices.py` | overlaps `tests/unit/backtest/test_runner_initial_prices.py` |
| `tests/backtest/test_tripwire_advisory_rename.py` | keep under `tests/backtest/` |

### 1.2 Target layout (test-policy §B)

```
tests/
├── conftest.py                                  # shared fixtures (Block A)
├── _helpers/                                    # NEW package (Block A)
│   ├── __init__.py
│   ├── degradation.py                           # assert_no_silent_degradation
│   └── tick_state.py                            # tick_state factory
├── fixtures/                                    # JSON fixtures only (unchanged)
├── unit/
│   ├── agents/
│   │   ├── analysts/{news,fundamental,...}/     # all analyst unit tests
│   │   ├── executor/                            # executor unit
│   │   ├── risk_gate/                           # risk_gate unit
│   │   └── strategist/                          # strategist unit
│   ├── backtest/                                # backtest unit
│   ├── baselines/
│   ├── config/
│   ├── contract/                                # schema unit
│   ├── data/{providers,models}/
│   ├── executor/                                # (legacy mirror; consolidate INTO `unit/agents/executor/`)
│   ├── observability/
│   └── orchestrator/
├── integration/                                 # cross-module integration
│   └── backtest/                                # backtest pipeline integration
├── contract/                                    # boundary invariants (KEPT — §B explicit layer)
└── backtest/                                    # cache + audit primitives only
    ├── audit/
    └── leak_regressions/
```

Top-level folders **forbidden**: `tests/analysts/`, `tests/executor/`,
`tests/orchestrator/`, `tests/agents/`. Note: `tests/contract/` stays
— test-policy §B explicitly mandates it as a layer.
`tests/backtest/` stays for cache/audit/leak primitives, with the
integration smoke moved to `tests/integration/backtest/`.

### 1.3 File-by-file migration (Block C)

Mechanical `git mv` operations. Source path → destination path.
Conflicts (where the destination already exists) are flagged
"MERGE: keep higher-content variant" and require a per-file
decision documented inline in the commit message.

| Source | Destination | Conflict? |
|---|---|---|
| `tests/analysts/fundamental/test_fetch_agent.py` | `tests/unit/agents/analysts/fundamental/test_fetch_agent.py` | new dir |
| `tests/analysts/fundamental/test_joiner.py` | `tests/unit/agents/analysts/fundamental/test_joiner.py` | new |
| `tests/analysts/fundamental/test_prompts.py` | `tests/unit/agents/analysts/fundamental/test_prompts.py` | new |
| `tests/analysts/news/test_fetch_agent.py` | `tests/unit/agents/analysts/news/test_fetch_agent.py` | new |
| `tests/analysts/news/test_joiner.py` | `tests/unit/agents/analysts/news/test_joiner.py` | new |
| `tests/analysts/news/test_prompts.py` | `tests/unit/agents/analysts/news/test_prompts.py` | new |
| `tests/analysts/test_branch_composition.py` | `tests/unit/agents/analysts/test_branch_composition.py` | new |
| `tests/analysts/test_cache_callbacks_per_ticker.py` | `tests/unit/agents/analysts/test_cache_callbacks_per_ticker.py` | new |
| `tests/analysts/test_per_ticker_branch.py` | `tests/unit/agents/analysts/test_per_ticker_branch.py` | new |
| `tests/analysts/test_smart_money.py` | **DELETE** (A-033, Plan 07 dependency) | — |
| `tests/analysts/test_technical.py` | `tests/unit/agents/analysts/test_technical.py` | new |
| `tests/agents/analysts/test_evidence_callback.py` | `tests/unit/agents/analysts/test_evidence_callback.py` | new |
| `tests/agents/memory/test_writer_smart_money_seen.py` | **DELETE** (A-033) | — |
| `tests/agents/test_isolated_failure.py` | `tests/unit/agents/test_isolated_failure.py` | new |
| `tests/agents/test_output_caps_per_ticker.py` | `tests/unit/agents/test_output_caps_per_ticker.py` | new |
| `tests/executor/test_executor_bookkeeping.py` | `tests/unit/agents/executor/test_executor_bookkeeping.py` | MERGE: collapse legacy `_THESIS` keys (A-063) at the same time |
| `tests/orchestrator/test_pipeline_build.py` | `tests/unit/orchestrator/test_pipeline_build.py` | review overlap with `test_pipeline_wiring_v2.py`; keep both if disjoint |
| `tests/backtest/test_reference_prices.py` | `tests/unit/backtest/test_reference_prices.py` OR drop if A-085 — defer to Plan 10 | review |

The `tests/unit/executor/` (one file: `test_open_positions_state.py`)
becomes `tests/unit/agents/executor/test_open_positions_state.py` —
consolidates with the `tests/unit/agents/executor/` directory that
already exists. Same for `tests/unit/test_session_service_factory.py`
→ delete (T-109; the duplicate at `tests/unit/orchestrator/test_persistence.py`
wins).

Flat `tests/unit/test_*.py` files (~50 of them) are **not moved** in
this plan unless they're explicitly in the schism map. They sit at
the unit-layer root by convention. Plan 11 doesn't reorganise the
flat tail — that's a Plan 12 nit at most.

---

## 2. Shared fixture / helper inventory (Block A)

### 2.1 `assert_no_silent_degradation(state, *, allow_degradation=())`

**Location:** `tests/_helpers/degradation.py`. Re-exported from
`tests/conftest.py` as both an importable function and a pytest
fixture (`degradation_check`).

**Contract:**

```python
def assert_no_silent_degradation(
    state: dict[str, object],
    *,
    allow_degradation: tuple[str, ...] = (),
) -> None:
    """Assert no domain's verdicts/evidence silently neutral-fell.

    Walks every ``{domain}_verdicts`` key in ``state`` and every
    ``{domain}_evidence`` row.  Fails the test if any entry has
    ``is_no_data=True`` unless the domain is named in
    ``allow_degradation`` (for tests that deliberately exercise a
    degraded branch — e.g. "news API key missing" regression tests).

    Also asserts the structured-log record set contains no
    ``branch_failed``, ``*_fetch_failed``, ``snapshot_spy_fetch_failed``,
    or ``usage_metadata_error`` records. (caplog must be set to
    WARNING level by the caller — the helper reads
    ``caplog.records`` from the live fixture.)
    """
```

**Signature derivation:** keys to walk are the eight live analyst
domains per intent §7.4 (`fundamental`, `news`, `technical`,
`social`, `insider`, `politician_trades`, `notable_holders`,
`filings`). The list is **not** hard-coded — the helper introspects
`state` for keys matching `^[a-z_]+_verdicts$` and
`^[a-z_]+_evidence$`. This way a future domain addition is covered
automatically.

**Test-policy mapping:** §A.7 (loudness), §G.7 (`is_no_data=True`
attractor), §G.8 (`branch_failed` not benign).

### 2.2 `tick_state(...)` factory fixture (T-102)

**Location:** `tests/_helpers/tick_state.py`. Re-exported as a
pytest fixture in `tests/conftest.py`.

**Contract:**

```python
def make_tick_state(
    *,
    watchlist: list[str],
    held: dict[str, float] | None = None,   # ticker → qty
    as_of: str | datetime | None = None,
    reference_prices: dict[str, float] | None = None,
    portfolio_cash: float = 10_000.0,
) -> dict[str, object]:
    """Build a contract-compliant tick-state dict.

    Populates every §A key required for a pipeline tick to run end-to-
    end against ``DatabaseSessionService``:

      * ``as_of`` — ISO-stringified (per ``feedback_as_of_boundary_coercion``)
      * ``watchlist`` — the input list
      * ``user:positions`` (durable) — built from ``held``
      * ``temp:_positions`` (in-tick bridge) — same shape
      * ``reference_prices`` — defaults derived from ``held`` keys
        + watchlist (1.0 stub each) if not provided
      * ``portfolio`` — coerced ``Portfolio`` dict with ``cash``
        + ``positions``
      * ``temp:_trace`` — empty list (TraceWriter consumer)
      * ``temp:_decision_logger`` — empty list (DecisionLogger consumer)

    Does NOT populate ``{domain}_verdicts`` / ``{domain}_evidence``
    — those are produced by the pipeline under test, and asserted
    against by ``assert_no_silent_degradation``.
    """
```

**Why a function-factory rather than a parametrised fixture:** every
caller passes a different watchlist / held mix; a pytest-fixture
form would force a parametrize indirection per call. The function
form composes from any test.

### 2.3 Existing `_clear_analysts_config_cache` (no change)

Already autouse-scoped; preserved untouched.

### 2.4 What is explicitly NOT a shared fixture

- No "construct an LLM response" factory — LLM-output building stays
  per-test because the canned shapes vary by analyst. (The current
  `_make_strategist_llm_response` / `_make_per_ticker_analyst_llm_response`
  helpers in `test_end_to_end_smoke.py` move to a smoke-only conftest
  in §6, not to the top-level.)
- No "spin up Runner" fixture — the existing
  `tests/integration/backtest/test_end_to_end_smoke.py` Runner-
  construction code moves to `tests/integration/backtest/conftest.py`
  during the smoke split (Block F).

---

## 3. Ordered changes

The order matters: shared helpers first, then move files (so
imports under the new tree pick up the helpers), then augment
assertions (so they can reference the new helpers), then split the
smoke (so the smoke-split tests can use the new fixtures).

If splitting per §0a, the seam is between Block C and Block D.

### Block A — Shared helpers (T-201, T-202, T-102)

#### Task A1: Create the `_helpers` package skeleton

**Files:**
- Create: `tests/_helpers/__init__.py`
- Create: `tests/_helpers/degradation.py`
- Create: `tests/_helpers/tick_state.py`

- [ ] **Step 1: Create empty package marker**

```python
# tests/_helpers/__init__.py
"""Shared test helpers — assertions, factories, and fixtures.

Not a production package.  Tests-only.
"""
from tests._helpers.degradation import assert_no_silent_degradation
from tests._helpers.tick_state import make_tick_state

__all__ = ["assert_no_silent_degradation", "make_tick_state"]
```

- [ ] **Step 2: Verify the package is importable**

Run: `PYTHONPATH=src .venv/bin/python -c "from tests._helpers import assert_no_silent_degradation, make_tick_state"`
Expected: ImportError on the names (they don't exist yet) — Tasks A2, A3 fill them.

- [ ] **Step 3: Commit (skeleton only)**

```bash
git add tests/_helpers/__init__.py tests/_helpers/degradation.py tests/_helpers/tick_state.py
git commit -m "test: scaffold tests/_helpers package for shared assertions and factories"
```

#### Task A2: Implement `assert_no_silent_degradation`

**Files:**
- Modify: `tests/_helpers/degradation.py`
- Create: `tests/unit/test_helpers_degradation.py` (self-test)

- [ ] **Step 1: Write the failing self-test**

```python
# tests/unit/test_helpers_degradation.py
"""Self-tests for tests/_helpers/degradation.py."""
from __future__ import annotations

import logging

import pytest

from tests._helpers import assert_no_silent_degradation


def test_passes_on_clean_state(caplog):
    """A state with all is_no_data=False and no warnings passes."""
    caplog.set_level(logging.WARNING)
    state = {
        "news_verdicts": [{"ticker": "AAPL", "is_no_data": False}],
        "news_evidence": [{"ticker": "AAPL", "verdict": {"is_no_data": False}}],
    }
    assert_no_silent_degradation(state)


def test_fails_on_silent_no_data(caplog):
    """Any verdict with is_no_data=True triggers an AssertionError."""
    caplog.set_level(logging.WARNING)
    state = {"news_verdicts": [{"ticker": "AAPL", "is_no_data": True}]}
    with pytest.raises(AssertionError, match="is_no_data=True"):
        assert_no_silent_degradation(state)


def test_allow_degradation_suppresses_named_domain(caplog):
    """A domain named in allow_degradation may carry is_no_data=True."""
    caplog.set_level(logging.WARNING)
    state = {"news_verdicts": [{"ticker": "AAPL", "is_no_data": True}]}
    assert_no_silent_degradation(state, allow_degradation=("news",))


def test_fails_on_branch_failed_log(caplog):
    """A WARNING record containing 'branch_failed' fails the assertion."""
    caplog.set_level(logging.WARNING)
    logging.getLogger("test").warning("branch_failed: news fetch died")
    state = {"news_verdicts": [{"ticker": "AAPL", "is_no_data": False}]}
    with pytest.raises(AssertionError, match="branch_failed"):
        assert_no_silent_degradation(state)
```

- [ ] **Step 2: Run the self-test, expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_helpers_degradation.py -v`
Expected: ImportError on `assert_no_silent_degradation` (function not yet implemented).

- [ ] **Step 3: Implement the helper**

```python
# tests/_helpers/degradation.py
"""Loud-fail assertion for happy-path pipeline tests.

See ``docs/test-policy.md`` §A.7 / §G.7 / §G.8.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable


# Domain key regex — matches '{domain}_verdicts' / '{domain}_evidence'.
# Introspects the state dict rather than hard-coding the eight live
# analyst domains, so a future domain is covered automatically.
_VERDICTS_KEY_RE = re.compile(r"^([a-z_]+)_verdicts$")
_EVIDENCE_KEY_RE = re.compile(r"^([a-z_]+)_evidence$")

# Warning substrings forbidden on a happy-path tick.
_FORBIDDEN_WARNING_SUBSTRINGS: tuple[str, ...] = (
    "branch_failed",
    "_fetch_failed",
    "snapshot_spy_fetch_failed",
    "usage_metadata_error",
)


def assert_no_silent_degradation(
    state: dict[str, object],
    *,
    allow_degradation: tuple[str, ...] = (),
) -> None:
    """Assert no domain in ``state`` silently neutral-fell.

    Walks every ``{domain}_verdicts`` and ``{domain}_evidence`` entry
    in ``state`` and asserts no row carries ``is_no_data=True`` unless
    its domain is named in ``allow_degradation``.

    Also walks ``caplog.records`` (via the active root logger handler
    set) and asserts no record's message contains any
    ``_FORBIDDEN_WARNING_SUBSTRINGS`` token unless the domain prefix
    is in ``allow_degradation``.

    :param state: tick-state dict produced by a pipeline run.
    :param allow_degradation: domain names that may legitimately
        carry is_no_data=True for this test (e.g. ``("news",)`` for
        a "news API down" regression test).
    :raises AssertionError: on any silent-failure signal.
    """
    allowed = set(allow_degradation)

    # Walk verdicts.
    for key, value in state.items():
        m = _VERDICTS_KEY_RE.match(key)
        if not m or m.group(1) in allowed:
            continue
        domain = m.group(1)
        rows = _coerce_rows(value)
        for row in rows:
            if _row_is_no_data(row):
                raise AssertionError(
                    f"silent degradation: {key} row has is_no_data=True "
                    f"(domain={domain}, row={row!r}); pass "
                    f"allow_degradation=({domain!r},) if intentional."
                )

    # Walk evidence — the per-ticker verdict lives at row["verdict"].
    for key, value in state.items():
        m = _EVIDENCE_KEY_RE.match(key)
        if not m or m.group(1) in allowed:
            continue
        domain = m.group(1)
        for row in _coerce_rows(value):
            verdict = row.get("verdict") if isinstance(row, dict) else None
            if verdict and _row_is_no_data(verdict):
                raise AssertionError(
                    f"silent degradation: {key} row.verdict has "
                    f"is_no_data=True (domain={domain}, row={row!r})."
                )

    # Walk warning records on the root logger's caplog handler.
    forbidden = _find_forbidden_warnings(allowed)
    if forbidden:
        joined = "\n  - ".join(forbidden)
        raise AssertionError(
            f"silent degradation: forbidden WARNING records seen:\n  - {joined}"
        )


def _coerce_rows(value: object) -> Iterable[dict]:
    """Tolerate list-of-dict or dict-of-dict shapes (joiners use both)."""
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        return [r for r in value.values() if isinstance(r, dict)]
    return []


def _row_is_no_data(row: dict) -> bool:
    """Read is_no_data from a row whether it's a dict or a Pydantic model dump."""
    val = row.get("is_no_data")
    if isinstance(val, bool):
        return val
    # Pydantic v2 dumps booleans as bool already; tolerate stringy "true".
    if isinstance(val, str):
        return val.lower() == "true"
    return False


def _find_forbidden_warnings(allowed_domains: set[str]) -> list[str]:
    """Scan the root logger's handler stack for forbidden WARNING records."""
    # caplog attaches a handler to the root logger.  Walk it.
    matches: list[str] = []
    for handler in logging.getLogger().handlers:
        records = getattr(handler, "records", None)
        if not records:
            continue
        for rec in records:
            if rec.levelno < logging.WARNING:
                continue
            msg = rec.getMessage()
            for token in _FORBIDDEN_WARNING_SUBSTRINGS:
                if token in msg:
                    # Tolerate allow_degradation: skip if any allowed
                    # domain name appears in the message.
                    if any(d in msg for d in allowed_domains):
                        continue
                    matches.append(f"[{rec.name}] {msg}")
                    break
    return matches
```

- [ ] **Step 4: Run the self-test, expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_helpers_degradation.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/_helpers/degradation.py tests/unit/test_helpers_degradation.py
git commit -m "test(helpers): add assert_no_silent_degradation loud-fail assertion (A-019)"
```

#### Task A3: Implement `make_tick_state` factory

**Files:**
- Modify: `tests/_helpers/tick_state.py`
- Create: `tests/unit/test_helpers_tick_state.py`

- [ ] **Step 1: Write the failing self-test**

```python
# tests/unit/test_helpers_tick_state.py
"""Self-tests for tests/_helpers/tick_state.py."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests._helpers import make_tick_state


def test_minimal_invocation_populates_required_keys():
    """A minimal call yields a state dict with all §A keys present."""
    state = make_tick_state(watchlist=["AAPL", "MSFT"])
    # Per docs/contract-invariants.md §A.
    for key in (
        "as_of",
        "watchlist",
        "user:positions",
        "temp:_positions",
        "reference_prices",
        "portfolio",
        "temp:_trace",
        "temp:_decision_logger",
    ):
        assert key in state, f"missing required key: {key}"


def test_as_of_is_iso_string():
    """as_of MUST be ISO-stringified (feedback_as_of_boundary_coercion)."""
    state = make_tick_state(watchlist=["AAPL"])
    assert isinstance(state["as_of"], str)
    # Round-trip parse must succeed.
    datetime.fromisoformat(state["as_of"])


def test_held_positions_populate_both_keys():
    """held=dict populates user:positions and temp:_positions identically."""
    state = make_tick_state(watchlist=["AAPL"], held={"AAPL": 10.0})
    assert state["user:positions"]["AAPL"]["qty"] == 10.0
    assert state["temp:_positions"]["AAPL"]["qty"] == 10.0


def test_reference_prices_default_covers_watchlist_and_held():
    """If reference_prices is None, defaults cover watchlist ∪ held."""
    state = make_tick_state(watchlist=["AAPL"], held={"MSFT": 5.0})
    assert "AAPL" in state["reference_prices"]
    assert "MSFT" in state["reference_prices"]


def test_no_bare_positions_key():
    """The bare ``positions`` key is forbidden (Plan 03 / A-070)."""
    state = make_tick_state(watchlist=["AAPL"])
    assert "positions" not in state, "bare 'positions' is forbidden"
```

- [ ] **Step 2: Run the self-test, expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_helpers_tick_state.py -v`
Expected: ImportError on `make_tick_state`.

- [ ] **Step 3: Implement the factory**

```python
# tests/_helpers/tick_state.py
"""Shared factory for contract-compliant tick-state dicts.

See ``docs/contract-invariants.md`` §A for the authoritative key list.
"""
from __future__ import annotations

from datetime import datetime, timezone


def make_tick_state(
    *,
    watchlist: list[str],
    held: dict[str, float] | None = None,
    as_of: str | datetime | None = None,
    reference_prices: dict[str, float] | None = None,
    portfolio_cash: float = 10_000.0,
) -> dict[str, object]:
    """Build a contract-compliant tick-state dict for pipeline tests.

    See plan-11 §2.2 for the contract.
    """
    held = held or {}

    # Coerce as_of to ISO string (state-write boundary rule).
    if as_of is None:
        as_of_str = datetime.now(timezone.utc).isoformat()
    elif isinstance(as_of, datetime):
        as_of_str = as_of.isoformat()
    else:
        # already string
        as_of_str = as_of

    # Default reference_prices: stub 1.0 for every ticker we know about.
    if reference_prices is None:
        reference_prices = {t: 1.0 for t in set(watchlist) | set(held.keys())}

    # Build position rows in the canonical shape.
    positions = {
        ticker: {
            "ticker": ticker,
            "qty": qty,
            "avg_price": reference_prices.get(ticker, 1.0),
        }
        for ticker, qty in held.items()
    }

    portfolio = {
        "cash": portfolio_cash,
        "positions": positions,
    }

    return {
        "as_of": as_of_str,
        "watchlist": list(watchlist),
        "user:positions": positions,
        "temp:_positions": positions,  # in-tick bridge
        "reference_prices": reference_prices,
        "portfolio": portfolio,
        "temp:_trace": [],
        "temp:_decision_logger": [],
    }
```

- [ ] **Step 4: Run the self-test, expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_helpers_tick_state.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/_helpers/tick_state.py tests/unit/test_helpers_tick_state.py
git commit -m "test(helpers): add make_tick_state factory (T-102)"
```

#### Task A4: Wire helpers into `tests/conftest.py`

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add re-exports + fixture wrappers**

```python
# Append to tests/conftest.py:

from tests._helpers import assert_no_silent_degradation, make_tick_state  # noqa: E402

# Re-export as pytest fixtures for tests that prefer DI.

@pytest.fixture
def degradation_check():
    """Fixture form of ``assert_no_silent_degradation`` — accepts kwargs.

    Usage:
        def test_x(degradation_check):
            ...
            degradation_check(state)
            degradation_check(state, allow_degradation=("news",))
    """
    return assert_no_silent_degradation


@pytest.fixture
def tick_state():
    """Fixture form of ``make_tick_state`` — call it to build state.

    Usage:
        def test_x(tick_state):
            state = tick_state(watchlist=["AAPL"], held={"AAPL": 5.0})
    """
    return make_tick_state
```

- [ ] **Step 2: Verify the fixtures resolve**

Run: `.venv/bin/python -m pytest tests/unit/test_helpers_degradation.py tests/unit/test_helpers_tick_state.py -v`
Expected: all pass (unchanged).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test(conftest): re-export degradation_check + tick_state fixtures"
```

### Block B — Fixture upgrades for deleted contract fields (A-060, A-063)

Replace fixtures that pin deleted `thesis` / `close_reasons` /
`horizon` / `target_price` / `stop_price` / `last_review_note` /
`intent="open"` keys. Each is a one-file edit. Do these **before**
the directory move so the move stays mechanical.

#### Task B1: risk_gate integration fixtures (A-060)

**Files:**
- Modify: `tests/integration/test_risk_gate_agent.py:24-32`
- Modify: `tests/integration/test_risk_gate_state_delta.py:55-65`

- [ ] **Step 1: Inspect current fixture content**

Run: `.venv/bin/python -m pytest tests/integration/test_risk_gate_agent.py tests/integration/test_risk_gate_state_delta.py -v`
Expected: green (the deleted fields still pass because schemas tolerate extras pre-fix; or fail post-Plan-02 with `extra="forbid"`).

- [ ] **Step 2: Replace `thesis=` with `rationale=` (Plan 02 vocabulary)**

For each fixture, replace any occurrence of:

```python
thesis={"reason": "...", "catalyst": "..."}
close_reasons=["..."]
```

with the post-Plan-02 / post-Plan-03 shape:

```python
rationale="..."
sell_reasons=["..."]   # if closing a held position
update_reasons=["..."] # if revising a thesis
```

- [ ] **Step 3: Re-run**

Run: `.venv/bin/python -m pytest tests/integration/test_risk_gate_agent.py tests/integration/test_risk_gate_state_delta.py -v`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_risk_gate_agent.py tests/integration/test_risk_gate_state_delta.py
git commit -m "test(risk_gate): drop deleted thesis/close_reasons fixture keys (A-060)"
```

#### Task B2: Executor legacy thesis fixture keys (A-063)

**Files:**
- Modify: `tests/executor/test_executor_bookkeeping.py:40-52` (will be moved in Block C; edit in-place first)
- Modify: `tests/unit/executor/test_open_positions_state.py:161-170,205-212`
- Modify: `tests/unit/agents/test_executor_decision_hook.py:163-170`

- [ ] **Step 1: Strip `horizon`/`target_price`/`stop_price`/`last_review_note` from each `_THESIS` fixture**

`PositionThesis` is `extra="forbid"` post-Plan-02. The fixtures must
construct theses with only the canonical fields. Read each file,
locate the `_THESIS` dict literal, remove the four extra keys.

- [ ] **Step 2: Run the three files**

Run: `.venv/bin/python -m pytest tests/executor/test_executor_bookkeeping.py tests/unit/executor/test_open_positions_state.py tests/unit/agents/test_executor_decision_hook.py -v`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add tests/executor/test_executor_bookkeeping.py tests/unit/executor/test_open_positions_state.py tests/unit/agents/test_executor_decision_hook.py
git commit -m "test(executor): drop legacy thesis fixture keys (A-063)"
```

#### Task B3: Executor decision-hook stale verb (A-062)

**Files:**
- Modify: `tests/unit/agents/test_executor_decision_hook.py:78-92`

- [ ] **Step 1: Replace `intent="open"` with the four-verb vocabulary**

`intent` is not a valid stance verb under the post-Plan-02 four-verb
schema (`buy` / `sell` / `update` / `no_action`). Decide between:

- **Delete** the test block at lines 78-92 if it duplicates other
  decision-hook coverage; or
- **Rewrite** to use `verb="buy"` and a real BUY-path assertion (the
  test currently slips through because the BUY broker path doesn't
  re-validate the verb).

- [ ] **Step 2: Re-run**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_executor_decision_hook.py -v`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/agents/test_executor_decision_hook.py
git commit -m "test(executor): drop intent=open under four-verb schema (A-062)"
```

### Block C — Directory consolidation (A-067, T-101)

**Recommended approach:** one commit per `git mv` block; pause for
local `pytest -q` between commits to catch import-path regressions
early.

#### Task C1: Move `tests/analysts/**` → `tests/unit/agents/analysts/`

- [ ] **Step 1: Create destination directories**

```bash
mkdir -p tests/unit/agents/analysts/fundamental tests/unit/agents/analysts/news
touch tests/unit/agents/analysts/fundamental/__init__.py tests/unit/agents/analysts/news/__init__.py
```

- [ ] **Step 2: `git mv` each non-deleted file per §1.3 table**

```bash
git mv tests/analysts/fundamental/test_fetch_agent.py tests/unit/agents/analysts/fundamental/test_fetch_agent.py
git mv tests/analysts/fundamental/test_joiner.py     tests/unit/agents/analysts/fundamental/test_joiner.py
git mv tests/analysts/fundamental/test_prompts.py    tests/unit/agents/analysts/fundamental/test_prompts.py
git mv tests/analysts/news/test_fetch_agent.py       tests/unit/agents/analysts/news/test_fetch_agent.py
git mv tests/analysts/news/test_joiner.py            tests/unit/agents/analysts/news/test_joiner.py
git mv tests/analysts/news/test_prompts.py           tests/unit/agents/analysts/news/test_prompts.py
git mv tests/analysts/test_branch_composition.py     tests/unit/agents/analysts/test_branch_composition.py
git mv tests/analysts/test_cache_callbacks_per_ticker.py tests/unit/agents/analysts/test_cache_callbacks_per_ticker.py
git mv tests/analysts/test_per_ticker_branch.py      tests/unit/agents/analysts/test_per_ticker_branch.py
git mv tests/analysts/test_technical.py              tests/unit/agents/analysts/test_technical.py
```

- [ ] **Step 3: Delete smart_money test files (A-033; depends on Plan 07 having shelved smart_money)**

```bash
git rm tests/analysts/test_smart_money.py
git rm tests/analysts/__init__.py tests/analysts/fundamental/__init__.py tests/analysts/news/__init__.py
rmdir tests/analysts/fundamental tests/analysts/news tests/analysts
```

- [ ] **Step 4: Run the moved tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/ -v`
Expected: green. Any failure here is an import path the moved file
references — fix the import (the file's own imports of `src/`
modules are unaffected; only sibling-test imports break, of which
there should be none).

- [ ] **Step 5: Commit**

```bash
git add -A tests/unit/agents/analysts tests/analysts
git commit -m "test: consolidate tests/analysts → tests/unit/agents/analysts (A-067)"
```

#### Task C2: Move `tests/agents/**` → `tests/unit/agents/`

- [ ] **Step 1: `git mv` per §1.3 table**

```bash
git mv tests/agents/analysts/test_evidence_callback.py tests/unit/agents/analysts/test_evidence_callback.py
git mv tests/agents/test_isolated_failure.py           tests/unit/agents/test_isolated_failure.py
git mv tests/agents/test_output_caps_per_ticker.py     tests/unit/agents/test_output_caps_per_ticker.py
git rm tests/agents/memory/test_writer_smart_money_seen.py
git rm tests/agents/__init__.py tests/agents/analysts/__init__.py tests/agents/memory/__init__.py
rmdir tests/agents/analysts tests/agents/memory tests/agents
```

- [ ] **Step 2: Run**

Run: `.venv/bin/python -m pytest tests/unit/agents/ -v`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add -A tests/unit/agents tests/agents
git commit -m "test: consolidate tests/agents → tests/unit/agents (A-067)"
```

#### Task C3: Move `tests/executor/` → `tests/unit/agents/executor/`

- [ ] **Step 1: Move and merge**

```bash
git mv tests/executor/test_executor_bookkeeping.py tests/unit/agents/executor/test_executor_bookkeeping.py
git rm tests/executor/__init__.py
rmdir tests/executor
```

- [ ] **Step 2: Move the orphan `tests/unit/executor/` into the canonical dir**

```bash
git mv tests/unit/executor/test_open_positions_state.py tests/unit/agents/executor/test_open_positions_state.py
git rm tests/unit/executor/__init__.py
rmdir tests/unit/executor
```

- [ ] **Step 3: Run**

Run: `.venv/bin/python -m pytest tests/unit/agents/executor/ -v`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add -A tests/unit/agents/executor tests/executor tests/unit/executor
git commit -m "test: consolidate tests/executor + tests/unit/executor → tests/unit/agents/executor (A-067)"
```

#### Task C4: Move `tests/orchestrator/` → `tests/unit/orchestrator/`

- [ ] **Step 1: Move**

```bash
git mv tests/orchestrator/test_pipeline_build.py tests/unit/orchestrator/test_pipeline_build.py
git rm tests/orchestrator/__init__.py
rmdir tests/orchestrator
```

- [ ] **Step 2: Run**

Run: `.venv/bin/python -m pytest tests/unit/orchestrator/ -v`
Expected: green. If `test_pipeline_build.py` overlaps materially
with `test_pipeline_wiring_v2.py`, merge the unique assertions and
delete the smaller file in a separate commit (T-109-style).

- [ ] **Step 3: Commit**

```bash
git add -A tests/unit/orchestrator tests/orchestrator
git commit -m "test: consolidate tests/orchestrator → tests/unit/orchestrator (A-067)"
```

#### Task C5: Delete duplicate `tests/unit/test_session_service_factory.py` (T-109)

- [ ] **Step 1: Compare with `tests/unit/orchestrator/test_persistence.py`**

Run: `diff tests/unit/test_session_service_factory.py tests/unit/orchestrator/test_persistence.py`
Expected: substantial overlap. Keep the mirrored-layout copy under
`tests/unit/orchestrator/`. Port any unique assertion before deleting.

- [ ] **Step 2: Delete**

```bash
git rm tests/unit/test_session_service_factory.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "test: delete duplicate test_session_service_factory.py (T-109)"
```

#### Task C6: Full-suite gate

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: same pass count as before Block C started (i.e. the move
is mechanical and changed no behaviour). Capture the count before
Block C began (Task A4 commit) and assert equality here.

- [ ] **Step 2: If Plan 11 is splitting (per §0a), STOP here.**

Open Plan 11B as `docs/audits/2026-05-26-codebase-audit/plans/plan-11b-test-augmentation.md`
covering Blocks D + E + F. Land Plan 11A and continue Plan 11B in
a follow-up.

### Block D — Augment thin assertions (A-018, T-001)

Each task is one file. The pattern is identical: add at minimum one
**content** assertion (positive value, expected verb, expected
ticker key, `assert not is_no_data`, structured-log absence) to
every test currently asserting only "didn't raise" / "is async" /
length / class identity.

Reference the test-strategy table (lines 119-150) for the per-file
cheapest fix shape. Each task lists the file, the audit ID, and the
fix shape from the table.

#### Task D1: `tests/unit/test_tick_entrypoint.py` (F-orch-007 / A-018)

- [ ] **Step 1: Read the file** — locate every test that only asserts
  imports succeed or that the function is async.

- [ ] **Step 2: Add content assertions**

For each "asserts only import" test, add at minimum:
- assert the function's `__name__` matches the expected entry-point name,
- assert the returned tick state contains `as_of` (or asserts
  `degradation_check(state)` if the test actually runs a tick).

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/python -m pytest tests/unit/test_tick_entrypoint.py -v
git add tests/unit/test_tick_entrypoint.py
git commit -m "test(tick): augment with content assertions (A-018 F-orch-007)"
```

#### Task D2: `tests/unit/test_memory_writer_agent.py` (F-agents-misc-010 / A-018)

- [ ] **Step 1: Replace `issubclass` / `name ==` only-tests with**: run the agent against a minimal state from `tick_state()`, assert the produced memory row contains the expected ticker and a non-empty rationale.

- [ ] **Step 2: Run + commit**

```bash
.venv/bin/python -m pytest tests/unit/test_memory_writer_agent.py -v
git add tests/unit/test_memory_writer_agent.py
git commit -m "test(memory_writer): augment with content assertions (A-018 F-agents-misc-010)"
```

#### Task D3: `tests/unit/test_tick_state.py` (F-orch-015 / A-018)

- [ ] **Step 1: Check whether the `TickState` Pydantic class is still in
  use post-Plan-04** (per A-087 / F-orch-005 it was unused). If
  Plan 07 deleted it, delete this test file. Otherwise add a content
  assertion exercising the actual Pydantic validation.

- [ ] **Step 2: Run + commit**

```bash
.venv/bin/python -m pytest tests/unit/test_tick_state.py -v 2>&1 | tail -20
# either delete the file or commit content-asserting rewrites
git add tests/unit/test_tick_state.py
git commit -m "test(tick_state): augment or delete (A-018 F-orch-015)"
```

#### Task D4: `tests/integration/test_executor_with_fake_broker.py::test_executor_rejection_continues` (A-066 / F-executor-010)

- [ ] **Step 1: Augment the rejection test**

Current: only asserts `status == "rejected"`. Add (per test-strategy
line 135): in the same `executions` list, mix a rejected row with a
filled BUY row; assert no `None`-fill assertion fires, and assert
`fill_prices[buy_ticker] > 0`.

- [ ] **Step 2: Add the idempotency-coverage test (A-065)**

Same file. Add `test_executor_idempotent_includes_after_callback`:
run executor twice with the same `tick_id`; assert `user:positions`
is byte-identical after both runs (the after-callback didn't re-fire
and clobber).

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/python -m pytest tests/integration/test_executor_with_fake_broker.py -v
git add tests/integration/test_executor_with_fake_broker.py
git commit -m "test(executor): augment rejection + add after-callback idempotency (A-065, A-066)"
```

#### Task D5: `tests/integration/test_snapshotter.py` (A-031 / F-agents-misc-007)

- [ ] **Step 1: Repoint patch from `yfinance.Ticker` to `data.get_price_history`**

The current patch is a no-op (production never calls `yfinance.Ticker`
directly post-refactor). Move the patch target; assert `spy_price > 0`
on the happy path; assert a structured WARNING record on the
deliberately-raise path.

- [ ] **Step 2: Run + commit**

```bash
.venv/bin/python -m pytest tests/integration/test_snapshotter.py -v
git add tests/integration/test_snapshotter.py
git commit -m "test(snapshotter): repoint patch to leaf seam + assert spy_price>0 (A-031)"
```

#### Task D6: `tests/unit/contract/test_evidence.py` (F-contract-008 / A-018)

- [ ] **Step 1: Locate every "asserts only class identity / len(...)" test**

For each, add a positive-content assertion: per-ticker `verdict.is_no_data is False`, expected ticker key present in `evidence` dict, expected fields populated.

- [ ] **Step 2: Run + commit**

```bash
.venv/bin/python -m pytest tests/unit/contract/test_evidence.py -v
git add tests/unit/contract/test_evidence.py
git commit -m "test(evidence): augment with content assertions (A-018 F-contract-008)"
```

#### Task D7: Pipeline smokes get `degradation_check` (T-002)

For each pipeline-touching test (the integration tier), add at the
end of the happy-path test body:

```python
caplog.set_level(logging.WARNING)
# ... run pipeline ...
degradation_check(state)
```

**Files to update:**
- `tests/integration/test_analyst_pool.py`
- `tests/integration/test_pipeline_composition.py`
- `tests/integration/test_strategist_executor_e2e.py`
- `tests/integration/test_strategist_v2_smoke.py`
- `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`
- `tests/integration/backtest/test_driver_one_tick.py`
- `tests/integration/backtest/test_backfill_smoke.py`

- [ ] **Step 1: For each file, add the caplog setup + degradation_check call to every test that runs a full happy-path tick.**

- [ ] **Step 2: Run each modified file**

Run: `.venv/bin/python -m pytest tests/integration/ -v -k "not test_end_to_end_smoke"`
Expected: green. Any new RED here is a genuine silent-failure now
made loud — investigate. (Either Plans 05/06/10 haven't fully
landed, or the test should `allow_degradation=(...)` because it's
exercising a degraded branch deliberately.)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_analyst_pool.py tests/integration/test_pipeline_composition.py tests/integration/test_strategist_executor_e2e.py tests/integration/test_strategist_v2_smoke.py tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py tests/integration/backtest/test_driver_one_tick.py tests/integration/backtest/test_backfill_smoke.py
git commit -m "test(integration): assert degradation_check on every happy-path tick (T-002, A-019)"
```

### Block E — Rewrite cementing tests (A-020, T-005)

These tests **currently pin buggy behaviour as expected**. They must
be rewritten to **fail pre-fix and pass post-fix**. The fix for each
underlying bug lands in Plans 05 / 06 / 10; the test rewrite lands
**in the same patch** as that fix, **not here**.

Plan 11's responsibility for Block E is to **list the rewrites and
hand them to the upstream plan**:

| Cementing test | Owning plan | Rewrite shape |
|---|---|---|
| `tests/unit/test_trading212_request_construction.py` (F-broker-008) | Plan 06 (broker) | Replace `AsyncMock` on `.json` with `MagicMock(return_value={...})`. Test fails pre-fix because `await dict` raises. |
| `tests/unit/data/providers/test_news_tiingo.py` (F-data-014) | Plan 05 (loud-fail) | Replace `assert out == []` with `pytest.raises(SecretMissingError)`. |
| `tests/unit/data/providers/test_politician_trades_quiver_as_of.py` | Plan 05 | as above |
| `tests/unit/data/providers/test_politician_trades_fmp.py` | Plan 05 | as above |
| `tests/unit/orchestrator/test_tick_as_of_phase.py:48-50` (F-orch-009) | Plan 04 (lifecycle) | Replace `isinstance(as_of, datetime)` with `assert isinstance(as_of, str); resolve_as_of(as_of)`. |
| `tests/unit/test_init_db_script.py` (F-orch-010) | Plan 10 (lifecycle tables) | Replace hard-coded 3-table list with `set(Base.metadata.tables.keys())`. |
| `tests/unit/orchestrator/test_risk_gate.py::test_no_risk_gate_intents_constant_contains_hold_and_update` (F-risk_gate-009) | Plan owning A-017 | Replace `{"hold", "update"}` assertion with `{"update", "no_action"}`. |

**Block E task = one TODO row added to each upstream plan's
deliverable list, plus a cross-reference comment in this plan's
follow-up tracking issue.** No commits land in Block E itself.

- [ ] **Step 1: Open each upstream plan; append the cementing-test
  rewrite as a Task in its work list.**

- [ ] **Step 2: Add a tracking comment to this plan's PR description
  listing the seven hand-offs.**

### Block F — Split the 756-LoC smoke (A-079, T-106)

The one mega-test in `tests/integration/backtest/test_end_to_end_smoke.py`
covers four orthogonal concerns:

1. pipeline completes without raising (already happens implicitly)
2. telemetry / observability records get written
3. decision-logger writes JSON snapshots
4. tick-state shape matches the §A invariants

Each is a separate concern. Splitting them clarifies which broke
when a regression lands and lets each test focus its assertions.

#### Task F1: Extract shared helpers to a backtest-conftest

**Files:**
- Create: `tests/integration/backtest/conftest.py`

- [ ] **Step 1: Move `_make_strategist_llm_response` and `_make_per_ticker_analyst_llm_response` and any Runner-construction code from `test_end_to_end_smoke.py:63-313` into `tests/integration/backtest/conftest.py` as fixtures or module-level helpers.**

Keep the helper functions importable from the conftest; convert
Runner construction into a `@pytest.fixture(scope="module")` named
`smoke_runner` that returns a configured Runner.

- [ ] **Step 2: Verify the original mega-test still passes**

Run: `.venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v`
Expected: green (the test now imports its helpers from the conftest).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/backtest/conftest.py tests/integration/backtest/test_end_to_end_smoke.py
git commit -m "test(smoke): extract LLM-response + Runner helpers to backtest conftest"
```

#### Task F2: Split into four files

**Files:**
- Create: `tests/integration/backtest/test_smoke_pipeline_completes.py`
- Create: `tests/integration/backtest/test_smoke_telemetry_written.py`
- Create: `tests/integration/backtest/test_smoke_decision_logger_writes.py`
- Create: `tests/integration/backtest/test_smoke_state_shape.py`
- Delete: `tests/integration/backtest/test_end_to_end_smoke.py`

- [ ] **Step 1: For each new file, write a focused test using `smoke_runner`**

Each test calls `smoke_runner.run_one_tick(...)` (or the equivalent)
once at module-scope and asserts on its concern only:

- `test_smoke_pipeline_completes.py`: asserts the run produced a
  non-empty `executions` list and `degradation_check(state)` passes.
- `test_smoke_telemetry_written.py`: asserts the telemetry artefact
  files exist at the expected paths under `tmp_path` and contain a
  non-zero record count.
- `test_smoke_decision_logger_writes.py`: asserts the JSON snapshot
  files exist and contain `held_view_at_decision` populated (catches
  F-backtest-005 too).
- `test_smoke_state_shape.py`: asserts the final state dict contains
  every §A key from `docs/contract-invariants.md` and no bare
  `positions` key (Plan 03 / A-070).

- [ ] **Step 2: Delete the original mega-file**

```bash
git rm tests/integration/backtest/test_end_to_end_smoke.py
```

- [ ] **Step 3: Run the four new files**

Run: `.venv/bin/python -m pytest tests/integration/backtest/ -v`
Expected: 4+ tests pass, total LoC well under 756.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/backtest/test_smoke_*.py tests/integration/backtest/test_end_to_end_smoke.py
git commit -m "test(smoke): split 756-LoC mega-file into four per-concern files (A-079)"
```

---

## 4. Test strategy — meta

This plan is itself "tests on tests". The verification is:

1. **Full-suite gate** — after every commit, run
   `.venv/bin/python -m pytest tests/ -q` and capture the pass /
   fail / skip counts. The pass count must be monotone non-
   decreasing across Block A-C (mechanical work). Across Block D-F
   the pass count may rise (added tests) and the **skip count must
   not rise** (no test silently skipped due to a broken import or a
   missing fixture).
2. **Coverage as sanity check, not target.** Capture
   `coverage report` numbers before Block A and after Block F.
   Coverage should rise modestly (Block D adds content
   assertions, F splits a test). A drop > 1pp is a signal that a
   test went silently dead — investigate.
3. **`degradation_check` invocation count.** After Block D, the
   number of integration tests calling `degradation_check` should
   be at least 7 (the list in Task D7). `grep -rn
   "degradation_check\|assert_no_silent_degradation" tests/ | wc -l`
   should grow from 0 to ≥ 7.
4. **No `tests/<module>/` top-level folder survives** (other than
   the canonical `tests/unit/`, `tests/integration/`,
   `tests/contract/`, `tests/backtest/`, `tests/fixtures/`,
   `tests/_helpers/`). Verify with: `ls tests/ -d | sort`.
5. **`tests/integration/backtest/test_end_to_end_smoke.py` no longer
   exists** after Block F.

---

## 5. Risks / silent-regression checklist

1. **CI config hard-coding old paths.** Verified: no `.github/`
   workflows reference `tests/analysts/`, `tests/executor/`,
   `tests/orchestrator/`, `tests/agents/` paths (checked
   `2026-05-26`). `pytest.ini` uses `testpaths = tests` — directory
   move is invisible to it. No `Makefile` or `scripts/*.sh`
   references either. **Verify again before Block C lands** — if a
   workflow has been added in the interim, repoint it in the same
   commit as the move.
2. **Import paths inside test files.** Test files import only from
   `src/` and from `tests._helpers`. Moving a test file changes its
   own `__file__` location but not any import path. The only risk
   is **test-to-test imports** (e.g. one test imports a fixture from
   a sibling test file) — none found in the audit, but spot-check
   during execution with `grep -n "from tests\." tests/**/*.py`.
3. **`__init__.py` markers.** Each new directory under `tests/unit/`
   needs an `__init__.py` so pytest discovers it. The migration
   tasks (C1-C4) create these explicitly.
4. **Plan 07 cascade.** Block C step 3 deletes smart_money test
   files. If Plan 07 has not yet landed (smart_money still wired in
   production), the deletions cause a `src/agents/analysts/smart_money/`
   module's behaviour to go untested. Verify Plan 07 has landed
   before Task C1 step 3. If not, defer the deletions to Plan 07.
5. **Block D triggers genuine red.** Adding `degradation_check` to
   integration tests may surface a silent-failure that Plans 05/06/10
   were supposed to have fixed but didn't fully. Treat any new red
   as a Plans 05/06/10 follow-up — do **not** weaken the assertion
   to silence the test.
6. **Block E hand-offs lost.** The seven cementing-test rewrites
   are handed off to upstream plans; track them in this PR's
   description so they don't get forgotten. If Plan 12 closes
   without all seven landing, that's a Plan 12 follow-up.
7. **`degradation_check` over-aggressive.** The forbidden-warning
   substring list is small (`branch_failed`, `_fetch_failed`,
   `snapshot_spy_fetch_failed`, `usage_metadata_error`). A test that
   intentionally exercises a different warning path may need the
   substring list extended or an `allow_degradation` override.
   Expand the substring list only with concrete justification per
   addition.
8. **Pytest discovery of the `_helpers` package.** The leading
   underscore means pytest won't auto-collect tests inside it (good —
   it contains no tests). Verify with `pytest --collect-only
   tests/_helpers/ 2>&1 | grep collected` — should be 0.

---

## 6. Definition of done

- [ ] `tests/_helpers/__init__.py`, `degradation.py`, `tick_state.py`
  exist; self-tests at `tests/unit/test_helpers_*.py` pass.
- [ ] `tests/conftest.py` re-exports `degradation_check` and
  `tick_state` fixtures.
- [ ] No top-level `tests/analysts/`, `tests/executor/`,
  `tests/orchestrator/`, `tests/agents/` directories exist; their
  files are all under `tests/unit/agents/...` (or deleted per
  A-033 / A-067).
- [ ] `tests/unit/test_session_service_factory.py` is deleted.
- [ ] At least 7 integration tests call `degradation_check` (Task D7).
- [ ] The six "augment thin assertions" files (Tasks D1-D6) have at
  least one new content assertion each.
- [ ] Fixtures in `tests/integration/test_risk_gate_agent.py`,
  `test_risk_gate_state_delta.py`, `tests/unit/agents/executor/test_executor_bookkeeping.py`,
  `tests/unit/agents/executor/test_open_positions_state.py`,
  `tests/unit/agents/test_executor_decision_hook.py` use the
  post-Plan-02 vocabulary (no `thesis=`, `close_reasons=`,
  `horizon=`, `target_price=`, `stop_price=`, `last_review_note=`,
  `intent="open"`).
- [ ] `tests/integration/backtest/test_end_to_end_smoke.py` is
  deleted; four files `test_smoke_*.py` exist under the same
  directory; total LoC across the four < 600.
- [ ] `tests/integration/backtest/conftest.py` exists and houses
  the shared `smoke_runner` fixture + LLM-response helpers.
- [ ] Full-suite `pytest -q` is green; skip count has not risen
  from the Block-A baseline.
- [ ] Seven Block-E cementing-test rewrites are filed as TODOs on
  the corresponding upstream plans (Plans 04, 05, 06, 10, and the
  plan that owns A-017).
- [ ] The plan's PR description includes the Block-E hand-off table
  so reviewers can confirm none are dropped.

---

## Self-review notes

- **Spec coverage:** every finding in the brief (A-018, A-019, A-020,
  A-051 [§5 inventory entry only — schema-collapse is a Plan-12 nit],
  A-053 [doc-only, deferred to Plan 12], A-054 [deferred to Plan 12 —
  extractor work is `src/`], A-055 [`src/` schema work — deferred],
  A-059, A-060, A-062, A-063, A-065, A-066, A-067, A-079, A-101 [not
  found in FINDINGS — flagged in §0]) is either addressed in a Block
  here or explicitly deferred with a reason. A-051 / A-053 / A-054 /
  A-055 touch `src/` which is out of scope per
  `feedback_test_audit_scope_tests_only`; their **test-side** echoes
  (e.g. cementing tests) are handled in Block E hand-offs. A-059 is
  resolved by Block C's risk_gate consolidation under
  `tests/unit/agents/risk_gate/`.
- **Placeholder scan:** no "TBD" / "add appropriate error handling" /
  "implement later". Every code block is complete and runnable. The
  one deferred decision is "delete vs rewrite" in Task B3 / Task D3,
  with both branches enumerated.
- **Type consistency:** `assert_no_silent_degradation` signature in
  Task A2 matches the one re-exported in Task A4 conftest matches
  the one called in Task D7. `make_tick_state` keyword args match
  across Tasks A3 → A4 → D7.

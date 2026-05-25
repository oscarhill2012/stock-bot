# Source audit — src/config/ and src/baselines/

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 10 (`src/config/{__init__,analysts,models,retry_429,risk_gate,schedule,strategist}.py` + `src/baselines/{__init__,spy,equity_curve}.py`)
**Findings:** 0 P0 · 1 P1 · 4 P2 · 2 P3

## Summary

`src/config/` is a clean set of Pydantic-validated JSON loaders, one per
configuration concern, each with a matching `config/*.json` file and a
documented row in `config/README.md` — the C6 surface is satisfied across
the board. The most material finding is a pair of orphaned `StanceCaps`
fields (`close_reason_max_chars` / `trim_reason_max_chars`) the code
itself flags as deprecated but that still ship in `config/strategist.json`
and the loader schema. `src/baselines/` is small and tidy; `spy.py`'s
public API was already pruned in Phase 7 and the surviving
`_metrics_from_series` is exercised only by tests — its docstring still
claims "the reporting layer" consumes it, which is C7 drift. No
contract violations and no silent-failure attractors found in either
subsystem.

## Findings

### Config

#### P1-01 · C1 dead code · `StanceCaps.close_reason_max_chars` and `trim_reason_max_chars` are unused

- **Location:** `src/config/strategist.py:108-109` (schema fields); `config/strategist.json:10-11` (live values); `config/README.md:331-332` (documented but stale).
- **Confidence:** high
- **Description:**
  The two fields are explicitly flagged as **DEPRECATED** in their own
  docstrings (`src/config/strategist.py:93-103`). A `grep -rn` across
  `src/`, `tests/`, and `scripts/` for `close_reason_max_chars` /
  `trim_reason_max_chars` (or any `_STANCE.close_reason*` /
  `stance_caps.close_reason*` access) turns up zero consumers — the only
  hits are the schema definition itself, the docstring marking them
  deprecated, the JSON file populating them, and a unit-test fixture
  (`tests/unit/config/test_strategist_config.py:37-38`) that mirrors the
  JSON. The fields are dead by the rubric definition (definition + JSON
  + test-mirror = no live caller). The intent ("retained for
  backwards-compatible `data.json` loading") referenced in the docstring
  has not aged well — Pydantic accepts them but nothing reads them, so
  the only thing they actually protect against is a typo in a value
  nobody consumes. Filed P1 rather than P2 because the *false*
  documentation in `config/README.md:331-332` actively misleads operators
  into believing the cap takes effect on `TickerStance.close_reason`
  fields that no longer exist.
- **Suggested action:**
  Drop both fields from `StanceCaps` in `src/config/strategist.py`, drop
  them from `config/strategist.json`, drop their rows from
  `config/README.md`, and update the `test_strategist_config.py` fixture
  to omit them. One coordinated PR — no cross-subsystem fan-out.

#### P2-01 · C7 doc/code drift · `config/README.md` documents `decision_caps.updated_thesis_max_chars` but the field is `thesis_max_chars`

- **Location:** `config/README.md:323`; actual schema at `src/config/strategist.py:80`; actual JSON key at `config/strategist.json:5`.
- **Confidence:** high
- **Description:**
  The README row for the strategist decision-cap reads
  `decision_caps.updated_thesis_max_chars`, but the Pydantic field is
  named `thesis_max_chars` (line 80) and the JSON file ships
  `"thesis_max_chars": 800` (line 5). The strategist prompt template at
  `src/agents/strategist/prompts.py:222` references
  `_DECISION.thesis_max_chars` — the code is consistent; only the README
  row is stale. A separate strategist test
  (`tests/unit/config/test_strategist_config.py:24-25`) records a
  matching comment ("field names mirror the real `DecisionCaps` model
  (`thesis_max_chars`, not the plan's draft
  `updated_thesis_max_chars`)") confirming this is a doc-only oversight
  from an earlier rename. C7 because the README is the operator-facing
  surface and the inconsistency is non-trivial to spot when editing
  `strategist.json`.
- **Suggested action:**
  Rename the README row to `decision_caps.thesis_max_chars` and update
  the prose describing what it caps (the standing market thesis emitted
  by the strategist LLM, not "the working hypothesis carried into the
  next tick" — that wording predates the field rename too).

#### P2-02 · C7 doc/code drift · `config/README.md` stance-caps rows describe deleted `close_reason` / `trim_reason` stance fields

- **Location:** `config/README.md:331-332`.
- **Confidence:** high
- **Description:**
  The two rows document `stance_caps.close_reason_max_chars` and
  `stance_caps.trim_reason_max_chars` as capping
  `TickerStance.close_reason` and `TickerStance.trim_reason`. Since
  Spec B Plan 3 (Band 3), the per-stance `close_reason` and
  `trim_reason` fields no longer exist — close/trim narrative flows
  through `stance.reason` and is capped by `rationale_max_chars` (this
  is explicit at `src/config/strategist.py:96-103` and
  `src/agents/strategist/stance_schema.py:8`). C7 drift. This finding is
  paired with P1-01 — if the fields are dropped, the README rows go
  with them.
- **Suggested action:**
  Delete both rows from the `stance_caps` table in `config/README.md` in
  the same PR that drops the schema fields. If the schema fields are
  kept for any "wait, we might revive these" reason, the README rows
  still need to be rewritten to say the caps are inert.

#### P2-03 · C7 doc/code drift · `src/baselines/spy.py` module docstring claims a non-existent caller

- **Location:** `src/baselines/spy.py:3-5`.
- **Confidence:** high
- **Description:**
  The docstring states `_metrics_from_series` is "used by tests and the
  reporting layer". A `grep -rn "_metrics_from_series\|baselines.spy"
  src/ scripts/` finds zero references outside the file itself — the
  only consumers are `tests/unit/test_spy_metrics.py` and
  `tests/unit/baselines/test_spy_metrics_removed.py`. Neither
  `src/backtest/reporting.py` nor any other reporting surface imports
  `baselines.spy`; the next-sentence claim that "reporting.py computes
  its own SPY delta directly from the golden cache" is true and
  contradicts the preceding "used by the reporting layer" phrasing.
  Pure C7 — the helper exists for the tests to keep exercising the
  Phase 7 removal regression. Filed C7 (not C1) because the symbol is
  exercised by tests, so per the rubric this is doc drift not dead
  code.
- **Suggested action:**
  Tighten the docstring to "Used by `tests/unit/test_spy_metrics.py` as
  a regression guard against `spy_metrics`'s Phase 7 removal; no
  production callers." Mentioning the regression test by name keeps the
  intent legible for the next reader.

#### P2-04 · C1 dead code · two unused `_reset_cache` test-hook helpers

- **Location:** `src/config/models.py:141-149` and `src/config/retry_429.py:139-148`.
- **Confidence:** high
- **Description:**
  Both modules define a `_reset_cache()` helper "for test fixtures that
  mutate the JSON file". `grep -rn` across `src/`, `tests/`, `scripts/`
  for `config.models._reset_cache` / `config.retry_429._reset_cache`
  (and the bare-import variants) turns up no callers. The actual reset
  pattern in use across the test suite is direct
  `get_xxx_config.cache_clear()` — see `tests/conftest.py:22-24` and
  `tests/integration/conftest.py:56,62` for `analysts`, and
  `tests/unit/agents/test_llm_retry.py:287-292` for `retry_429`'s
  direct monkeypatch pattern. The matching `_reset_cache` helpers in
  `src/data/config.py` and `src/backtest/settings.py` *are* called from
  tests; the two filed here are the only configured-loader siblings
  that lack a caller. Filed P2 — small, dead-on-arrival hooks, no
  correctness impact.
- **Suggested action:**
  Either delete both helpers (tests can keep using the direct
  `.cache_clear()` pattern), or write the conftest reset that uses
  them — pick one. Net: don't leave dead "test hook only" helpers
  that no test imports.

#### P3-01 · C7 doc/code drift · `src/config/strategist.py` module docstring lists obsolete strategist free-text fields

- **Location:** `src/config/strategist.py:4-5`.
- **Confidence:** medium
- **Description:**
  The docstring opener enumerates the free-text fields the strategist
  produces as "`reasoning`, `thesis`, per-stance `rationale`,
  `catalyst`, `close_reason`, `trim_reason`, and `PositionThesis`
  rationale/notes". `close_reason` and `trim_reason` are no longer
  stance fields (see P1-01 / P2-02), so this opening sentence is
  factually wrong. Pure cosmetic drift in the module docstring;
  medium confidence because I cannot be sure whether someone is
  actively re-using these names in a follow-on plan I cannot see from
  inside this subsystem.
- **Suggested action:**
  Drop `close_reason` and `trim_reason` from the docstring's
  enumeration when P1-01 lands; alternatively rephrase to "the
  free-text fields the strategist persists" without enumerating them.

### Baselines

#### P3-02 · C7 doc/code drift · `docs/Phase1-build/phase1.5-remaining.md` points at `src/scripts/plot_equity.py`

- **Location:** `docs/Phase1-build/phase1.5-remaining.md:5-6,57,71,85` and `docs/Phase1-build/multi-agent-system-design.md:93`.
- **Confidence:** medium
- **Description:**
  Several Phase 1 docs cross-reference `src/scripts/plot_equity.py`,
  but the script lives at `scripts/plot_equity.py` (project root,
  outside `src/`). Phase 1 docs are historical, so this is low-impact,
  but new readers chasing the cross-references hit a dead path.
  Confidence medium — the rubric's C7 reach explicitly covers
  "outdated cross-references between subsystems", but the docs are
  arguably frozen Phase-1 deliverables that shouldn't be edited.
  Flagging so consolidation can decide whether to file alongside other
  Phase-1 doc cleanups.
- **Suggested action:**
  If Phase 1 docs are still expected to be navigable, fix the paths
  to `scripts/plot_equity.py`. If they're frozen, leave them and note
  the convention in `docs/`'s own README. Either way, nothing in
  `src/baselines/` itself needs to move.

## Cross-subsystem notes

- All `src/config/` loaders have a matching `config/*.json` file and a
  matching row in `config/README.md` — the C6 surface from
  `contract-invariants.md` is clean for this subsystem.
- `retry_429.py` has a single caller (`src/agents/llm_retry.py:370-372`)
  via a lazy import inside `RetryingAgentWrapper`. This is appropriate
  single-purpose use; not C3 overabstraction.
- `src/baselines/equity_curve.py::compute_equity_curve` has two real
  callers: `scripts/plot_equity.py:16` and a unit test. It depends on
  `orchestrator.persistence.PortfolioSnapshotRow` — pre-deployment, no
  process is actually writing those rows yet, but the read-side
  helper is exercised in tests so it isn't dead.
- No dependencies on `src/agents/attribution/` or `src/deploy/` from
  either subsystem.

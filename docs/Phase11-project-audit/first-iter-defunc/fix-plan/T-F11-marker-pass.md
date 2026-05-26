# T-F11 — Marker discipline retrofit

**Wave:** 2 (parallel — independent of T-F07 and T-F08)
**Pairs source-audit fix:** none
**Branch:** `fix/T-F11-marker-pass`
**Depends on:** T-F10 (layout sweep) — though the marker pass touches
only files inside `tests/integration/` and is conflict-free with both
T-F07 and T-F08.
**Estimated diff size:** small (one-line additions per file plus a
small policy-doc update)

## Scope

Apply the `integration` and `slow` pytest markers consistently across
`tests/integration/`, so the markers documented in `pytest.ini` and
referenced in `docs/test-policy.md` §C actually behave as specified.

Today, 19 of 20 files under `tests/integration/` carry no marker — only
`tests/integration/test_strategist_v2_smoke.py` and
`tests/integration/backtest/test_backfill_smoke.py` apply
`@pytest.mark.integration`. Per `layout-and-fixtures.md` P1-03 and
P3-01 the consequences are:

- `pytest -m integration` runs 2 of ~27 integration files — useless as a
  selector.
- `pytest -m "not slow"` (the default-fast invocation) still runs every
  integration test including the multi-tick backtest in
  `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`
  — the opposite of what §F documents.

This PR adds `pytestmark = pytest.mark.integration` (module-level) to
every file under `tests/integration/`, and adds the `slow` marker to
the known-heavy tests. No new marker types are introduced — the four
markers (`integration`, `slow`, `contract`, `replay`) already exist in
`pytest.ini`.

### In scope

**1. Add module-level `pytestmark = pytest.mark.integration` to the
19 unmarked `tests/integration/*.py` files.**

The two already-marked files (`test_strategist_v2_smoke.py` and
`backtest/test_backfill_smoke.py`) are skipped — they already have the
marker. The 19 to edit (after T-F10; these are stable paths):

Top-level `tests/integration/`:
- `test_analyst_pool.py`
- `test_evidence_persistence.py`
- `test_evidence_writer.py`
- `test_executor_with_fake_broker.py`
- `test_fundamental_canned_output.py`
- `test_memory_writer_integration.py`
- `test_multi_tick_backtest_produces_diverse_rationale.py`
- `test_namespace_partitioning.py`
- `test_phase2_hydration_from_db_only.py`
- `test_pipeline_composition.py`
- `test_retry_smoke.py`
- `test_risk_gate_agent.py`
- `test_risk_gate_state_delta.py`
- `test_snapshotter.py`
- `test_state_delta_user_prefix_end_to_end.py`
- `test_strategist_minimal_schema_no_retry.py`
- `test_thesis_persistence_round_trip.py`

`tests/integration/backtest/`:
- `test_driver_failure_threshold.py`
- `test_driver_one_tick.py`
- `test_end_to_end_smoke.py`
- `test_fetcher_idempotent.py`
- `test_fresh_run_starts_clean.py`
- `test_strict_mode_aborts_on_missing_as_of.py`

(That's 17 + 6 = 23 candidate files; cross-reference with the audit's
"19 unmarked" count by re-grepping in the worktree. The exact count is
acceptance-checked rather than spec-bound. If a file already carries
the marker or has been deleted by T-F07/T-F08 by the time this PR
dispatches, skip it.)

Insertion pattern (top of file, after existing module docstring + imports
of `pytest`):

```python
import pytest

pytestmark = pytest.mark.integration
```

If the file already imports `pytest`, just add the `pytestmark` line
on its own. If a file already has `pytestmark = pytest.mark.asyncio`
(`asyncio_mode = auto` makes this rare but possible), extend to a
list:

```python
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
```

**2. Tag known-slow tests with `@pytest.mark.slow`.**

Candidates (per `layout-and-fixtures.md` P1-03 / P3-01 and the
test-policy §C "Backtest smoke tests almost always need
slow + integration"):

- `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`
  — multi-tick backtest, the heaviest single test in the suite. Apply
  `pytestmark = [pytest.mark.integration, pytest.mark.slow]`.
- `tests/integration/backtest/test_end_to_end_smoke.py` — full
  driver+runner end-to-end. Apply
  `pytestmark = [pytest.mark.integration, pytest.mark.slow]`.
- `tests/integration/backtest/test_backfill_smoke.py` — backfill the
  golden-cache; already `pytest.mark.integration`. Extend to
  `[pytest.mark.integration, pytest.mark.slow]`.
- `tests/integration/backtest/test_fetcher_idempotent.py` — fetches the
  full provider matrix twice. Apply
  `pytestmark = [pytest.mark.integration, pytest.mark.slow]`.

Conservative scope: do not tag `test_retry_smoke.py` as slow without
measuring wall-clock — per P1-03 it depends on whether the retries
use real timers. If `time.sleep` / `asyncio.sleep` is patched out
(common pattern), the test is fast; if it's not, it's slow. Verify
with `pytest --durations=10 tests/integration/test_retry_smoke.py`
inside the worktree. If wall-clock > 1.0s, tag slow; otherwise leave
integration-only.

**3. Update `docs/test-policy.md` §F.**

Today §F lists three invocations. Add a paragraph clarifying:

```markdown
By default, `pytest tests/` runs all unit + integration + contract
tests. Slow / replay variants are opt-in:

- `pytest tests/ -v` — default fast suite (unit + integration + contract;
  excludes slow + replay).
- `pytest tests/ -v -m "slow or integration"` — include backtest smokes
  and other long-running integration tests.
- `pytest tests/ -v -m "not integration"` — pure-unit run.
- `pytest tests/ -v -m replay` — long historical replay (manual).
- `RUN_LLM_TESTS=1 pytest tests/integration/ -v` — exercise the
  LLM-touching integration tests; these are skip-by-default via
  `pytest.skipif` inside the relevant test bodies (not via the
  marker system).
```

Also update the §C marker table's `slow` row description from
"long-running tests excluded from the default run" to "long-running
tests (>1s wall-clock) excluded from the default run; opt in with
`-m slow`. Apply to backtest smokes and other tests that exercise
the full driver/runner."

Add a one-line note under the §C row for `integration`: "Apply at
module level via `pytestmark = pytest.mark.integration`. Tests under
`tests/integration/` should always carry this marker."

**4. Do NOT modify `pytest.ini`.**

Per the dispatch prompt: "don't add new marker types unless §C
already names them." All four markers already exist in `pytest.ini`
and §C of the policy. Leave `pytest.ini` untouched.

### Out of scope

- Adding `pytest.mark.contract` to the six `tests/contract/*.py` files
  — the audit notes this is a separate consideration; `contract` marker
  hygiene is part of a later cleanup or rides with whichever PR
  touches each file.
- Inverting silent-failure-defending tests — owned by Wave 4 T-F01b.
- Strengthening completion-only assertions — owned by Wave 4 T-F12.
- Deleting test files (none of the integration files are dead).
- Moving files between directories — owned by T-F10.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `layout-and-fixtures.md` P1-03 | 19 unmarked `tests/integration/*.py` | Apply `pytestmark = pytest.mark.integration` |
| `layout-and-fixtures.md` P3-01 | `pytest.ini` `slow` marker | Tag known-heavy tests; clarify policy doc |
| `analysts-deterministic.md` P3-02 | `tests/integration/test_analyst_pool.py` | Apply integration marker |

## Implementation steps

1. **Walk `tests/integration/` and `tests/integration/backtest/`.**
   For each `.py` file (excluding `__init__.py` and `conftest.py`):
   - Read the top of the file to determine current state.
   - If `pytestmark` is absent, add the import and the
     `pytestmark = pytest.mark.integration` line immediately after
     the module docstring + imports.
   - If `pytestmark` is already present as a single mark, extend to a
     list including `pytest.mark.integration` if missing.
2. **Apply `slow` to the four known-heavy tests** listed above. Use
   `pytestmark = [pytest.mark.integration, pytest.mark.slow]`.
3. **Measure `test_retry_smoke.py` wall-clock** (`pytest --durations=10
   tests/integration/test_retry_smoke.py`) — if >1s, add `slow`.
4. **Update `docs/test-policy.md` §C and §F** per the wording in the
   "Out of scope" boundary above.
5. **Verify marker behaviour:**
   - `pytest tests/ -m integration --collect-only -q | wc -l` should
     return roughly the count of integration tests (was 2 files before
     this PR, ~24 files after).
   - `pytest tests/ -m "not slow" --collect-only -q | wc -l` should
     exclude the multi-tick backtest test and the other tagged
     slow tests.
   - `pytest tests/ -m slow --collect-only -q` should list exactly the
     4 (or 5 if retry_smoke is slow) tagged-slow test files.
6. **Run the full suite** (`pytest tests/ -v`) to confirm marker
   additions don't break anything.
7. **Run `ruff check`** to confirm no new lint issues.

## Acceptance criteria

- [ ] Every `tests/integration/*.py` and `tests/integration/backtest/*.py`
  file carries `pytest.mark.integration` (verified by `grep -L
  "pytest.mark.integration" tests/integration/test_*.py
  tests/integration/backtest/test_*.py` returning nothing).
- [ ] The four named slow-tagged tests carry both `integration` and
  `slow` (verified by `grep -l "pytest.mark.slow" tests/integration/`
  returning ≥ 4 files).
- [ ] `pytest -m integration` collects every test under
  `tests/integration/` (no false-negatives).
- [ ] `pytest -m "not slow"` does **not** collect the multi-tick
  backtest test, the end-to-end smoke, the backfill smoke, or the
  fetcher-idempotent test.
- [ ] `pytest.ini` is unchanged (no new marker keys introduced).
- [ ] `docs/test-policy.md` §C and §F reflect the new invocation
  patterns and module-level marker convention.
- [ ] Full `pytest tests/ -v` green.
- [ ] `.venv/bin/python -m ruff check tests/` clean.
- [ ] No new audit findings introduced.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m pytest tests/ -m integration --collect-only -q
.venv/bin/python -m pytest tests/ -m slow --collect-only -q
.venv/bin/python -m pytest tests/ -m "not slow" --collect-only -q | wc -l
.venv/bin/python -m ruff check tests/
grep -L "pytest.mark.integration" tests/integration/test_*.py tests/integration/backtest/test_*.py
```

## Risks and rollbacks

- **Risk:** a test that *was* getting picked up by the default-fast
  run no longer is, breaking developer expectations. Mitigation:
  the markers are additive; nothing is removed from the default
  invocation. Only `slow` excludes (and only when explicitly
  scoped), and the four targets are genuinely slow per audit.
- **Risk:** module-level `pytestmark = [list]` syntax interacts
  oddly with `asyncio_mode = auto` if a file already declares
  `pytestmark = pytest.mark.asyncio`. Mitigation: extend to a list
  rather than overwrite; pytest handles list-form `pytestmark`
  natively.
- **Risk:** the wall-clock estimate for `test_retry_smoke.py` differs
  between the dispatcher's machine and CI. Mitigation: the
  measurement is taken inside the worktree on the project's `.venv`;
  if borderline, prefer leaving as integration-only and let a
  follow-up tighten.
- **Rollback:** discard the feature branch. The marker additions are
  one-line per file; `git revert` is clean.

## Subagent dispatch prompt sketch

> Work on branch `fix/T-F11-marker-pass` in a git worktree. Read
> `docs/Phase11-project-audit/fix-plan/T-F11-marker-pass.md` end-to-end, then skim
> `docs/Phase11-project-audit/test-audit/layout-and-fixtures.md` (P1-03, P3-01) and
> `docs/test-policy.md` §C and §F for context. Apply
> `pytestmark = pytest.mark.integration` to every unmarked file in
> `tests/integration/` (top-level and `backtest/` subdir); tag the
> four named tests as `slow` in addition; measure
> `test_retry_smoke.py` and tag if >1s. Update §C and §F of the
> test-policy doc. Do **not** touch `pytest.ini` — no new marker
> types. Run the verification commands listed in the spec; confirm
> `pytest -m integration` collection includes every integration file
> after the change. Commit as
> `test(markers): apply integration marker uniformly, tag known-slow tests`
> with finding IDs in the body. Push and open the PR. **Do not skip
> hooks. Do not amend.**

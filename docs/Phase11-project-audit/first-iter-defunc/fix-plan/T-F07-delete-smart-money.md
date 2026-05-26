# T-F07 — Delete SmartMoney entirely

**Wave:** 2 (parallel — runs after T-F10 layout sweep merges)
**Pairs source-audit fix:** F5 (SmartMoney deletion)
**Branch:** `fix/T-F07-delete-smart-money`
**Depends on:** T-F10 (layout sweep) — analyst test paths have been
collapsed into `tests/unit/agents/analysts/` before this PR runs.
**Estimated diff size:** large (deletions) / nothing added

## Scope

Delete the SmartMoney analyst end-to-end. Per the strategic decision
recorded in `docs/Phase11-project-audit/fix-plan/README.md` Decision 1 and the source-audit
SUMMARY Open Question 1, SmartMoney is shelved in production
(`src/orchestrator/pipeline.py:88`), carries a P0 contract-key drift
(`analysts-deterministic.md` P0-01), a P1 Rule-1 violation
(`analysts-deterministic.md` P1-01), a Pydantic-shape bug in its
extractor (`contract.md` P1-04), and ~37 tests across four trees
defending dead-in-prod behaviour. Deletion resolves all of these
findings at once. No source-side code-path exists outside the analyst
subpackage that consumes SmartMoney output — verified by audit and by
the fact that the only call site in the pipeline is the commented-out
reference.

This PR is paired source + test in a single atomic landing per
README.md Decision 3: source-fix and test-rewrite ship together.

### In scope

**1. Delete the SmartMoney analyst subpackage.**

- `src/agents/analysts/smart_money/agent.py`
- `src/agents/analysts/smart_money/fetch.py`
- `src/agents/analysts/smart_money/__init__.py`
- The whole `src/agents/analysts/smart_money/` directory.

**2. Delete the SmartMoney contract extractor.**

- `src/contract/extractors/smart_money.py`
- Remove the corresponding import / registration row from
  `src/contract/extractors/__init__.py` (the audit reports no
  registration block, but verify and remove any `from .smart_money
  import …` or `__all__` entry that exists).

**3. Delete the SmartMoneyRaw model.**

- `src/data/models/smart_money.py`
- Per source-audit `data-models-and-top-level.md` P2-08,
  `SmartMoneyRaw` is not re-exported from `src/data/models/__init__.py`,
  so no `__init__.py` edit is needed for the data-models package.
- Verify with `grep -rn "SmartMoneyRaw\|from data.models.smart_money"
  src/ tests/ scripts/` — the only surviving reference after the
  in-scope deletions should be in `docs/` (acceptable; doc references
  to historical commentary are not in scope).

**4. Unwire SmartMoney from the pipeline.**

- `src/orchestrator/pipeline.py:88-89` — delete the two commented-out
  lines that reference `_build_smart_money_analyst`. Confirm no
  import of `_build_smart_money_analyst` survives.
- `src/orchestrator/state.py:80` — delete the
  `smart_money_data: dict[str, Any] | None = None` field per
  source-audit `analysts-deterministic.md` P0-01's "remove the
  corresponding bare field from `orchestrator/state.py:80`".

**5. Drop SmartMoney references from orchestrator persistence and memory.**

- `src/orchestrator/persistence.py:35,50,74` — the
  `smart_money_seen` `Mapped[bool]` column on the memory-row ORM and
  the two read/write sites that reference it. Delete the column from
  the SQLAlchemy model, the writer, and the reader. Note: this is a
  SQLite schema change. Pre-deployment per the user-memory
  `project_stockbot_deployment_state` means no live migration is
  needed; the next `hard_reset` rebuilds the schema. Mention this
  explicitly in the commit body.
- `src/orchestrator/persistence.py:304` — update the docstring
  reference "One of ``technical|fundamental|news|social|smart_money``"
  to drop `smart_money`.

**6. Drop SmartMoney from the memory writer.**

- `src/agents/memory/writer.py` (verify exact filename) — locate the
  reader of `smart_money_seen` and the writer that sets it. Source
  audit cross-reference: the test
  `tests/agents/memory/test_writer_smart_money_seen.py` (post-T-F10
  it lives at `tests/unit/agents/memory/test_writer_smart_money_seen.py`)
  exists *only* because the writer populates that field. Delete the
  populating logic; the memory writer's other branches stay.

**7. Drop SmartMoney from `config/data.json` and the data registry.**

- `config/data.json` — the file does not currently carry a top-level
  `smart_money` provider entry (the `politician_trades` and
  `notable_holders` domains feed SmartMoney indirectly, and they
  remain live for other consumers). Verify by inspection; if a
  `smart_money` key exists, delete it. **Do not touch
  `politician_trades` or `notable_holders` provider config** — both
  domains have other consumers and other deletion considerations
  (the politician-trades domain is intentionally disabled per the
  `project_politician_trades_disabled` memory; that disablement
  stays). The four wired-but-unused domains (`earnings`,
  `analyst_consensus`, `short_interest`, `options`) are deleted by
  **T-F08**, not this PR.
- `src/data/registry.py` — no `smart_money` domain key exists
  (verified: `DOMAINS` does not include it). Nothing to change in
  the registry's `DOMAIN_SHAPES` or `DOMAINS` lists.
- `src/data/__init__.py` — no `get_smart_money(...)` wrapper exists.
  Nothing to remove.

**8. Update `config/README.md`.**

- Remove any rows / paragraphs describing SmartMoney-specific
  defaults (search for `smart_money`, `politician_lookback_days`
  *only if exclusively used by SmartMoney* — verify before deleting;
  if the fundamental analyst or another consumer reads it, keep it).
- Document the deletion at the top of the section listing analysts
  if such a section exists.

**9. Update `docs/contract-invariants.md` §A.**

Per source-audit Theme 5: the doc currently has no rows for
`smart_money_*` keys in §A. The deletion makes this absence permanent
and correct. However, if any row (e.g. `temp:smart_money_data`,
`smart_money_verdicts`, `smart_money_evidence`) was added in
prior fix-plan F1 (the doc-patch PR), remove it. Verify by
`grep -n "smart_money" docs/contract-invariants.md`; remove any
matches.

**10. Delete the ~37 SmartMoney tests across the post-T-F10 tree.**

Post-T-F10 paths (per the T-F10 spec's destination table):

- `tests/unit/agents/analysts/smart_money/test_fetch.py` (8 tests; was
  `tests/unit/test_smart_money_fetch.py`)
- `tests/unit/agents/analysts/smart_money/test_gate.py` (3 tests; was
  `tests/unit/test_smart_money_gate.py`)
- `tests/unit/agents/analysts/smart_money/test_construction.py`
  (5 tests; was `tests/analysts/test_smart_money.py`)
- `tests/unit/contract/extractors/test_smart_money.py` (10 tests)
- `tests/unit/contract/extractors/test_smart_money_verdict.py`
  (7 tests; was `tests/unit/test_derive_smart_money_verdict.py`)
- `tests/unit/data/models/test_smart_money.py` (2 tests)
- `tests/unit/agents/memory/test_writer_smart_money_seen.py` (2 tests;
  was `tests/agents/memory/test_writer_smart_money_seen.py`)

Delete the smart_money-specific subdirectory itself
(`tests/unit/agents/analysts/smart_money/`) after emptying it.
The seven files above are 37 tests; the `analysts-deterministic.md`
P1-01 count of "≈ 37 tests across 7 files" matches.

**11. Touch dependent tests that contain SmartMoney conditionals.**

- `tests/unit/agents/analysts/test_evidence_callback.py` — currently
  parametrises only `analyst="technical"`. Any preparatory SmartMoney
  parametrisation (planned for the fix-rather-than-delete path) must
  not be added. Today's file does not test SmartMoney, so no edit is
  needed. Confirm with `grep -n smart_money
  tests/unit/agents/analysts/test_evidence_callback.py`.
- `tests/integration/test_analyst_pool.py` — asserts
  `len(pool.sub_agents) == 3` (Technical, Social, plus SmartMoney
  *would have been* the third had it not been shelved). Verify the
  current expected count post-deletion is unchanged (the shelve
  means the pool already builds two). Update the assertion if
  needed.
- `tests/contract/test_lookbacks_sourced_from_config.py:285-322` —
  `test_backtest_notable_holders_uses_config_lookback_and_limit` is
  currently `@pytest.mark.skip(reason="notable_holders cache-fill is
  shelved ... unskip together with re-enabling … the SmartMoney
  analyst …")`. Per `analysts-deterministic.md` P2-07, delete this
  test now — SmartMoney is gone and will not unshelve.
- `src/agents/analysts/_common.py` — verify the helper's
  `make_evidence_callback(analyst, …)` does not carry SmartMoney-
  specific branching. If a SmartMoney-only branch survives, delete
  it. The helper itself remains (still used by Technical and Social).

**12. Delete SmartMoney fixture files.**

- `tests/fixtures/contract/smart_money_aapl.json`
- `tests/fixtures/contract/smart_money_no_data.json`

Verify no other test references these JSON files via
`grep -rn "smart_money_aapl\|smart_money_no_data" tests/`.

### Out of scope

- Re-enabling SmartMoney as an analyst (the strategic decision is
  delete, not fix).
- Removing the `notable_holders` or `politician_trades` data domains.
  The data providers stay (other consumers exist or may exist;
  `politician_trades` is intentionally disabled per
  `project_politician_trades_disabled` and remains disabled, not
  removed).
- The four unused data domains (`earnings`, `analyst_consensus`,
  `short_interest`, `options`) — owned by **T-F08**.
- Strategist-side rendering of SmartMoney evidence — the strategist
  prompt does not currently render a SmartMoney section
  (verified by source-audit `agents-strategist.md` — no
  `smart_money` reads). No edit needed.
- The dual `PositionThesis` cleanup (source F7) — owned by **T-F05**.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `analysts-deterministic.md` (source) P0-01 | `smart_money/fetch.py`, `smart_money/agent.py`, `_common.py:98` | bare-key vs `temp:` key drift — closed by deletion |
| `analysts-deterministic.md` (source) P0-02 (test-side) | `tests/.../test_evidence_callback.py` | parametrisation never required — closed by deletion |
| `analysts-deterministic.md` (source) P1-01 | `smart_money/agent.py:153` | Rule 1 `state_delta` violation — closed by deletion |
| `analysts-deterministic.md` (source) P1-02 (SmartMoney half) | `smart_money/agent.py:121` | `as_of` not coerced — closed by deletion |
| `analysts-deterministic.md` (source) P2-01 | `smart_money/agent.py:13-16,105-108` | stale docstring — closed by deletion |
| `analysts-deterministic.md` (source) P2-02, P2-03 (SmartMoney share) | `smart_money/agent.py:168-183, 88-160` | duplicated factory + run-impl — closed by deletion |
| `analysts-deterministic.md` (test) P0-01 | tests of `smart_money_data` key | closed by deletion of tests |
| `analysts-deterministic.md` (test) P0-02 | `test_evidence_callback.py` parametrisation gap | moot post-deletion |
| `analysts-deterministic.md` (test) P1-01 | 37 SmartMoney tests across 7 files | deleted |
| `analysts-deterministic.md` (test) P1-02 | bare-key assertions | files deleted |
| `analysts-deterministic.md` (test) P1-04 | missing `test_smart_money_state_delta.py` | not needed — analyst deleted |
| `analysts-deterministic.md` (test) P1-05 (smart_money half) | `test_smart_money.py` construction-only smoke | deleted |
| `analysts-deterministic.md` (test) P2-02 | `test_smart_money_fetch.py` / `test_smart_money_gate.py` duplicate | both deleted |
| `analysts-deterministic.md` (test) P2-07 | skipped `notable_holders` lookback test | deleted |
| `contract.md` (source) P1-04 | `extract_smart_money_features` `.get()`-on-Pydantic bug | closed by extractor deletion |
| `contract.md` (source) P2-02 | `"filings"`/`"transactions"` alias branches | closed by extractor deletion |
| `contract-package.md` (test) P1-02 | smart_money fixture's dead `"filings"` shape | closed by fixture deletion |
| `contract-package.md` (test) P1-09 | missing T4 test for Pydantic-instance crash | not needed — extractor deleted |
| `data-models-and-top-level.md` (source) P2-08 | `SmartMoneyRaw` not re-exported | closed by model deletion (re-export moot) |

## Implementation steps

1. **Pre-flight grep audit.** Run
   `grep -rn "smart_money\|SmartMoney" src/ tests/ scripts/ config/
   docs/contract-invariants.md` and save the output. The list is the
   work-tracking source — every reference must either be deleted or
   explicitly justified.
2. **Source-side deletions** (commit-able chunk):
   - Delete `src/agents/analysts/smart_money/` (whole directory).
   - Delete `src/contract/extractors/smart_money.py`.
   - Delete `src/data/models/smart_money.py`.
3. **Source-side patches:**
   - Edit `src/orchestrator/pipeline.py`: drop lines 88-89 (the
     commented-out builder reference). Confirm no other line
     references SmartMoney.
   - Edit `src/orchestrator/state.py`: drop the `smart_money_data`
     field at line 80.
   - Edit `src/orchestrator/persistence.py`: drop the
     `smart_money_seen` `Mapped[bool]` column declaration (line 35),
     the writer assignment (line 50), the reader dict entry (line 74),
     and the `smart_money` token in the docstring at line 304.
   - Edit `src/agents/memory/writer.py` (or equivalent) to stop
     populating `smart_money_seen`. Locate via
     `grep -n smart_money_seen src/agents/`.
   - Edit `src/contract/extractors/__init__.py`: remove any
     SmartMoney import/registration (audit reports none, but verify).
   - Run `grep -rn smart_money src/` to confirm no remaining hits in
     code. Doc references in source docstrings are acceptable only if
     they're past-tense ("historic, removed"); active-voice references
     are removed.
4. **Config and doc updates:**
   - `config/data.json` — verify no `smart_money` key exists (none in
     today's file); if any sneaks in via merge, delete.
   - `config/README.md` — delete any SmartMoney mention.
   - `docs/contract-invariants.md` — `grep -n smart_money`; delete any
     active row.
5. **Test deletions** (against post-T-F10 paths):
   - `rm tests/unit/agents/analysts/smart_money/{test_fetch.py,test_gate.py,test_construction.py,__init__.py}`
   - `rmdir tests/unit/agents/analysts/smart_money/`
   - `rm tests/unit/contract/extractors/test_smart_money.py`
   - `rm tests/unit/contract/extractors/test_smart_money_verdict.py`
   - `rm tests/unit/data/models/test_smart_money.py`
   - `rm tests/unit/agents/memory/test_writer_smart_money_seen.py`
6. **Test patches:**
   - `tests/integration/test_analyst_pool.py` — verify the expected
     pool size after deletion (was 2 effective, since SmartMoney was
     shelved; still 2 after this PR).
   - `tests/contract/test_lookbacks_sourced_from_config.py` — delete
     the skipped `test_backtest_notable_holders_uses_config_lookback_and_limit`
     test (lines 285-322).
   - `tests/unit/agents/analysts/test_evidence_callback.py` — confirm
     no SmartMoney parametrisation exists; no edit if clean.
7. **Fixture deletions:**
   - `rm tests/fixtures/contract/smart_money_aapl.json`
   - `rm tests/fixtures/contract/smart_money_no_data.json`
8. **Post-flight grep audit.** Repeat the grep from step 1. The only
   acceptable surviving matches are:
   - Past-tense references in `docs/` (historical commentary).
   - Past-tense references in commit messages (out of scope to edit).
   - **Zero** matches in `src/`, `tests/`, `config/`,
     `docs/contract-invariants.md`.
9. **Run the full suite** (`pytest tests/ -v`) and `ruff check`.
10. Append a `graphify-out/graph_delta.md` entry describing the
    deletion.

## Acceptance criteria

- [ ] `grep -rn "smart_money\|SmartMoney" src/ tests/ config/
  docs/contract-invariants.md` returns no matches outside
  `docs/` historical commentary.
- [ ] Full `pytest tests/ -v` green.
- [ ] `.venv/bin/python -m ruff check src/` clean.
- [ ] `.venv/bin/python -m scripts.backtest_run --window
  baseline-2025-09 --tick-limit 1` runs to completion (single tick;
  confirms the orchestrator still wires correctly without SmartMoney).
- [ ] `tests/fixtures/contract/smart_money_*.json` no longer exist.
- [ ] `src/agents/analysts/smart_money/` and
  `src/contract/extractors/smart_money.py` and
  `src/data/models/smart_money.py` no longer exist.
- [ ] Every finding in the table above is closed (cite by ID in the
  commit body).
- [ ] `graphify-out/graph_delta.md` has an entry dated today
  documenting the deletion.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
grep -rn "smart_money\|SmartMoney" src/ tests/ config/
```

## Risks and rollbacks

- **Risk:** the `smart_money_seen` ORM column removal changes the
  SQLite schema. Pre-deployment per the
  `project_stockbot_deployment_state` memory means no live database
  exists, so no migration is required. The next `hard_reset` /
  `initialise` rebuilds the schema. Mitigation: document in commit
  body; verify by running `scripts.initialise` against a fresh
  `tmp_path` and confirming the new schema is correct.
- **Risk:** the deletion misses a dynamic-string reference (e.g. an
  analyst name constructed at runtime). Mitigation: the
  pre/post-flight grep audit catches every literal mention.
- **Risk:** `tests/integration/test_analyst_pool.py`'s expected pool
  size is wrong. Mitigation: re-run the test as part of the
  full-suite verification.
- **Rollback:** discard the feature branch. `main` is untouched until
  merge. Because every change is a deletion, `git revert` restores
  cleanly.

## Subagent dispatch prompt sketch

> Work on branch `fix/T-F07-delete-smart-money` in a git worktree.
> Depends on T-F10 having merged first — confirm `main` carries the
> post-layout-sweep tree before starting. Read
> `docs/Phase11-project-audit/fix-plan/T-F07-delete-smart-money.md` end-to-end, then read
> `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md`,
> `docs/Phase11-project-audit/test-audit/analysts-deterministic.md`, and
> `docs/Phase11-project-audit/test-audit/contract-package.md` for context. Delete every
> SmartMoney source file, test file, fixture, config row, and doc row
> listed in the spec. Confirm zero surviving references via the
> pre/post-flight grep. Run the full test suite, `ruff check`, and a
> single-tick backtest. Commit as `fix(smart_money): delete shelved
> analyst end-to-end` with finding IDs in the body. Push and open
> the PR. **Do not skip hooks. Do not amend. Do not re-enable
> SmartMoney under any circumstances.**

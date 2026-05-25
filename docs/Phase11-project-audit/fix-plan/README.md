# Fix-plan — audit remediation cycle

**Date:** 2026-05-25
**Origin:** `docs/Phase11-project-audit/source-audit/SUMMARY.md` + `docs/Phase11-project-audit/test-audit/SUMMARY.md`
**Strategic decisions:** locked 2026-05-25 (see "Decisions" below)

This directory holds one spec per fix-PR. Each spec is dispatched to a subagent that works in a git worktree, runs the full test suite, commits on a feature branch, pushes to `origin`, and opens a PR for review.

## Decisions locked

1. **SmartMoney analyst:** delete entirely (~6 source files + ~37 tests).
2. **Unused data domains:** pull all four (`earnings`, `analyst_consensus`, `short_interest`, `options`).
3. **PR pairing:** one PR per source-fix + paired test-rewrite (atomic CI).
4. **Subagent autonomy:** edit + test + commit on feature branch + push + open PR; user reviews diff before merge.
5. **Layout sweep:** one atomic PR (T-F10) before any other work.
6. **Spec C / Phase 2 hydration:** deferred this cycle; orchestrator P0-03 stays open.
7. **Dead test-only seams:** delete source + tests together.
8. **Verification gate:** full `.venv/bin/python -m pytest tests/` per PR before commit.

## Dispatch waves

Waves are sequenced to avoid merge conflicts. Each wave merges to `main` before the next dispatches.

### Wave 1 — serial (foundation)
- **T-F10** layout sweep — ~80 file `git mv`, 0 semantic changes.

### Wave 2 — parallel (mass deletions, non-overlapping)
- **T-F07** delete SmartMoney (paired with source F5)
- **T-F08** pull unused data domains (paired with source F6)
- **T-F11** marker discipline retrofit (independent)

### Wave 3 — parallel (correctness fixes, non-overlapping subsystems)
- **T-F03** lifecycle ADK-tables coverage (paired with source F2)
- **T-F04** live-only bombs: broker `await`, snapshotter, datetime boundary (paired with source F3)
- **T-F06** executor `"positions"` → `user:positions` (paired with source F8)

### Wave 4 — parallel + 1 serial dependency (surfacing pattern)
- **T-F01a** surfacing primitive (new shared helper) — must land first
- **T-F01b** silent-failure inverts (paired with source F4) — depends on T-F01a
- **T-F02** missing surfacing tests for source P0s (paired with source F10 + others)
- **T-F05** strategist cleanup + drop dual `PositionThesis` (paired with source F7)
- **T-F09** contract parallel-fixture cleanup
- **T-F12** completion-only assertion rewrites

## Spec template

```markdown
# T-F<NN> — <title>

**Wave:** <1-4>
**Pairs source-audit fix:** <F<N>, …> or "none"
**Branch:** `fix/T-F<NN>-<slug>`
**Depends on:** <other T-F<N> or "none">
**Estimated diff size:** <small/medium/large>

## Scope

One paragraph. What this PR changes and why.

### In scope
- Bulleted list of concrete changes.

### Out of scope
- Bulleted list of nearby concerns explicitly *not* touched.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `agents-misc.md P0-01` | `tests/integration/test_snapshotter.py` | Invert silent-swallow assertion |
| `agents-misc.md` source P0-01 | `src/agents/snapshot/agent.py` | Raise on SPY fetch failure |

## Implementation steps

1. Step 1 (file:line where helpful).
2. Step 2.
3. …

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in commit body).
- [ ] No new audit findings introduced (subagent should self-audit against the rubric).
- [ ] Graphify delta entry appended if structural changes (new/renamed/moved files).

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1   # if backtest-touching
```

## Risks and rollbacks

- Risk: …
- Rollback: feature branch can be discarded; no `main` impact until merge.

## Subagent dispatch prompt sketch

A short prompt fragment the dispatcher will use for this PR. Includes paths to the
matching `docs/Phase11-project-audit/source-audit/<file>.md` and `docs/Phase11-project-audit/test-audit/<file>.md` reports as
context.
```

## Conventions

- **British English** throughout (colour, behaviour, organisation, analyse, optimise).
- **Comment-heavy code** — every non-trivial function gets a docstring + inline rationale.
- **Whitespace for legibility** — blank lines between logical blocks.
- **No `--no-verify`, no `--amend` on pushed commits, no force-push to `main`.**
- **Commit message format:** `fix(<subsystem>): <one-line subject>` followed by a body that cites finding IDs closed.

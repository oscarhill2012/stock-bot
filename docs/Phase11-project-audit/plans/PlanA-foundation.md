# Plan A — Foundation

**Phase:** 11 — project-audit remediation
**Date:** 2026-05-25
**Sequencing:** first plan in the Phase 11 cycle; precedes Plan B (correctness fixes) and Plan D (silent-failure inverts).
**Owning specs:** `docs/Phase11-project-audit/fix-plan/T-F10-layout-sweep.md`, `T-F11-marker-pass.md`, `T-F07-delete-smart-money.md`, `T-F08-pull-unused-domains.md`.

---

## 1. Plan overview

Plan A is the **Foundation** phase of the audit-remediation cycle: a behaviour-preserving structural tidy-up that reshapes the test tree, applies marker discipline, and removes two large blocks of code that the source audit identified as either shelved (SmartMoney) or wired-but-unconsumed (the four Phase 3 data domains). None of the four PRs in this plan changes runtime decision behaviour — every change is a `git mv`, a marker annotation, or a deletion of code that production has already stopped reading.

Because there is no behaviour change, the safety net for Plan A is three-pronged and absolute:

1. **Full test suite green** (`pytest tests/ -v`) at the tip of every PR.
2. **`ruff check src/` clean** at the tip of every PR.
3. **Backtest output byte-identical** to a baseline captured *before* Plan A's first PR dispatches. The canonical window is `baseline-2025-09` (used by every Plan A spec's verification commands).

If any of those three drift on any PR, that PR is rejected, rolled back, and the cause investigated before the next attempt. There is no "small acceptable diff" allowance — Plan A's whole purpose is to clear noise so that Plan B's behaviour changes are inspectable.

Plan A delivers the foundation Plan B needs: a mirrored test tree, working marker selectors, and a smaller code surface (no SmartMoney, no unused domains) for the correctness fixes to land against.

---

## 2. PRs included

| T-F id | Title | Branch | Sub-wave | Diff size | Source-audit findings closed | Test-audit findings closed |
|---|---|---|---|---|---|---|
| **T-F10** | Layout sweep — collapse parallel mirror trees and move loose unit tests | `fix/T-F10-layout-sweep` | A1 | large file-count / small semantic | none (pure layout) | `layout-and-fixtures.md` P1-01, P1-02, P2-01 through P2-09, `analysts-deterministic.md` P2-01 (test-side) |
| **T-F11** | Marker discipline retrofit — apply `integration` + `slow` markers | `fix/T-F11-marker-pass` | A2 | small | none | `layout-and-fixtures.md` P1-03, P3-01, `analysts-deterministic.md` P3-02 |
| **T-F07** | Delete SmartMoney analyst end-to-end | `fix/T-F07-delete-smart-money` | A2 | large (deletions) | `analysts-deterministic.md` source P0-01, P1-01, P1-02 (SmartMoney half), P2-01, P2-02, P2-03 (SmartMoney share); `contract.md` source P1-04, P2-02; `data-models-and-top-level.md` source P2-08 | `analysts-deterministic.md` test P0-01, P0-02, P1-01, P1-02, P1-04, P1-05 (smart_money half), P2-02, P2-07; `contract-package.md` test P1-02, P1-09 |
| **T-F08** | Pull unused data domains — `earnings`, `analyst_consensus`, `short_interest`, `options` | `fix/T-F08-pull-unused-domains` | A2 | medium (deletions) | `data-models-and-top-level.md` source P1-02, P1-03; `data-providers.md` source P2-03 | `data-models-and-top-level.md` test P1-01, P1-02, P1-04, P1-05, P2-04; `data-providers.md` test P2-04 |

Finding counts cited above are taken verbatim from the spec files and the audit SUMMARYs; the plan does not invent additional totals.

---

## 3. Sequencing

Plan A runs in two sub-waves. The split is dictated by file-path dependencies: T-F10 moves the post-move paths that the three A2 PRs target.

### A1 — serial (foundation of the foundation)

- **T-F10 — layout sweep.** Must merge to `main` before any A2 PR dispatches. T-F10 is one atomic PR consisting entirely of `git mv` operations, a single one-line rename (`test_output_always_six_chars` → `…_for_latency`), conftest deletions, and the conftest scope-down. T-F10's acceptance gate is a strict `pytest --collect-only -q | wc -l` parity check: identical count before and after.

### A2 — parallel (three non-overlapping deletion / annotation PRs)

Once T-F10 is on `main`, the three A2 PRs dispatch in parallel:

- **T-F11 — marker discipline retrofit.** Touches `tests/integration/*.py` and `tests/integration/backtest/*.py` plus `docs/test-policy.md`. Does **not** touch `src/`, `pytest.ini`, or any test outside `tests/integration/`.
- **T-F07 — delete SmartMoney.** Touches `src/agents/analysts/smart_money/` (deletion), `src/contract/extractors/smart_money.py` (deletion), `src/data/models/smart_money.py` (deletion), three orchestrator files (small edits), one memory-writer file, and the SmartMoney tests under `tests/unit/agents/analysts/smart_money/` and related sibling locations.
- **T-F08 — pull unused data domains.** Touches the four model modules, the four provider subpackages, `src/data/registry.py`, `src/data/config.py`, `config/data.json`, `config/README.md`, the matching test files, the legacy Quiver politician-trades swap test, the four conditional branches in `tests/contract/test_provider_shapes.py`, and the rate-limit docstring in `src/data/__init__.py`.

The three A2 PRs are confirmed non-overlapping by inspection of their in-scope file lists in the spec files. T-F11 only edits `tests/integration/` (none of the SmartMoney or unused-domain integration tests live there in a way that T-F07/T-F08 would also touch). T-F07 and T-F08 touch disjoint analyst / data subpackages; the only shared file is `src/data/registry.py`, which T-F07 explicitly notes it does **not** edit (no `smart_money` domain key exists in the registry per the T-F07 spec, step 7).

If a merge conflict surfaces despite this, resolution rule: T-F10 has already merged, so the conflict is between two A2 branches. Whichever A2 PR is reviewed second rebases on `main` and resolves; conflict scope is expected to be at most one `__init__.py` line per overlap.

---

## 4. Pre-flight — backtest baseline capture

Before T-F10 dispatches, capture a baseline backtest run against the canonical window referenced in every Plan A spec's verification block: `baseline-2025-09`. This snapshot is the regression oracle for the whole of Plan A *and* Plan B; both plans assert their backtest output is byte-identical to it.

### Steps

1. Confirm `main` is at the commit that will be the parent of `fix/T-F10-layout-sweep`. Note the SHA in `docs/Phase11-project-audit/baseline/HEAD.txt`.
2. Run the canonical backtest in full (no `--tick-limit`):
   ```bash
   PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
   ```
   *Do not* truncate with `--tick-limit 1` for the baseline. The single-tick form in the spec files is the per-PR smoke test; the baseline is the full-window run.
3. Snapshot the artefacts produced by that run into `docs/Phase11-project-audit/baseline/`:
   - The equity curve file (`equity_curve.csv` or equivalent — whichever name the runner emits).
   - The decisions log (`decisions/` directory of per-tick JSON snapshots, or the rolled-up `decisions.jsonl`).
   - The metrics summary (`metrics.md`).
4. Commit the baseline directory in a single isolated commit on `main` *before* T-F10 dispatches: `chore(audit): capture Plan A backtest baseline for baseline-2025-09 window`.

### Regression oracle contract

Every PR in Plan A must, as part of its acceptance gate, re-run the same backtest invocation and diff the three artefact families against the baseline. The diff must be empty. Any non-empty diff is treated as a behaviour change and rejects the PR (see §5 and §8).

For the per-PR smoke gate, the single-tick form (`--tick-limit 1`) referenced in the T-F07 and T-F08 specs is acceptable: it proves the orchestrator still wires correctly. The full-window byte-identity check is the additional Plan-A-level gate layered on top.

---

## 5. Per-PR safety net

Each subagent must run the following before pushing the branch. *All three* must pass. Failure means fix, re-stage, **new** commit (never `--amend`, never `--no-verify`).

### T-F10 — layout sweep

```bash
.venv/bin/python -m pytest tests/ --collect-only -q | wc -l   # must match the pre-PR count
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/ scripts/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
diff -r <baseline-artefacts> <post-PR-artefacts>             # must produce no output
```

The `--collect-only -q | wc -l` parity check is load-bearing for T-F10 — it is the single most reliable signal that no test was lost or accidentally added during the moves.

### T-F11 — marker pass

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m pytest tests/ -m integration --collect-only -q
.venv/bin/python -m pytest tests/ -m slow --collect-only -q
.venv/bin/python -m pytest tests/ -m "not slow" --collect-only -q | wc -l
.venv/bin/python -m ruff check tests/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
diff -r <baseline-artefacts> <post-PR-artefacts>
```

The marker-collection commands are the spec's own acceptance verification — they prove `pytest -m integration` now selects the full integration set and `-m "not slow"` excludes the tagged-slow ones. The backtest diff is the Plan-A-wide regression check.

### T-F07 — delete SmartMoney

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
grep -rn "smart_money\|SmartMoney" src/ tests/ config/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
diff -r <baseline-artefacts> <post-PR-artefacts>
```

The grep must return no matches outside `docs/` historical commentary (per the T-F07 spec's acceptance gate). The backtest diff must be empty: SmartMoney is shelved in production, so deletion must be a strict no-op.

### T-F08 — pull unused data domains

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
grep -rn "EarningsHistory\|AnalystConsensusBundle\|ShortInterestSnapshot\|OptionContract" src/ tests/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
diff -r <baseline-artefacts> <post-PR-artefacts>
```

Same shape as T-F07. No agent consumes any of the four domains, so the backtest diff must be empty.

### Behaviour-change rule

**Any non-empty diff between the Plan-A baseline artefacts and the post-PR artefacts is grounds for immediate rollback of that PR.** The PR is closed, not merged. The cause is investigated before re-attempting. Plan A trades nothing for the structural tidy-up; if the backtest output drifts, the PR has done more than the spec allows and must not land.

---

## 6. Subagent dispatch protocol

Each fix-PR is dispatched to a single subagent working in an isolated git worktree off `main`. The dispatch prompt for each PR is grounded in:

- The matching spec file under `docs/Phase11-project-audit/fix-plan/` (read end-to-end before any edit).
- The relevant per-subsystem audit reports under `docs/Phase11-project-audit/source-audit/` and `docs/Phase11-project-audit/test-audit/` (for context only; the spec is the source of truth for what to change).
- This plan file (for safety-net and rollback expectations).

### Autonomy boundaries

Each subagent is authorised to:

- Edit any file listed in its spec's "In scope" section.
- Move/delete any file listed in its spec.
- Run the full test suite, `ruff`, and the canonical backtest as many times as needed.
- Commit on its dedicated feature branch (`fix/T-F<NN>-<slug>`).
- Push to `origin`.
- Open the PR via `gh pr create` with a body that cites finding IDs closed and links to the spec file.

Each subagent is **not** authorised to:

- Touch files outside the spec's in-scope list.
- Alter `pytest.ini` (T-F11 explicitly forbids; the others have no business there).
- Skip pre-commit hooks (`--no-verify` is forbidden across the whole plan).
- Amend pushed commits (`--amend` after `git push` is forbidden; if a hook fails on a pushed commit, fix and add a *new* commit).
- Force-push to `main`.
- Merge their own PR. Merge is a human review step.

### Branch naming

`fix/T-F<NN>-<slug>` exactly as listed in §2 — matches the existing project convention.

### On test failure

If `pytest tests/ -v` fails before commit:

1. Diagnose the failure. Do **not** mark it expected or skip the failing test.
2. Fix the cause inside the spec's in-scope file list. If the fix needs an out-of-scope file, the subagent stops and surfaces the conflict — it does not silently widen scope.
3. Re-stage the fix.
4. Create a **new** commit (never `--amend`).
5. Re-run the full safety net.

If `pytest` was already passing locally and then fails on the pre-commit hook (which itself runs `pytest`), the failure is real — the hook is the project's safety net. Same procedure: fix, re-stage, *new* commit. Never `--no-verify`.

### Commit message convention

```
fix(<subsystem>): <one-line subject>

<body — explain the why, cite finding IDs>

Closes: <finding IDs from the spec's Findings closed table>
```

Per `docs/Phase11-project-audit/fix-plan/README.md` §Spec template. British English in subjects and bodies (per project convention).

---

## 7. Acceptance criteria for Plan A as a whole

Plan A is complete when:

- [ ] All four PRs (T-F10, T-F11, T-F07, T-F08) are merged to `main`.
- [ ] The full-window backtest at `baseline-2025-09` produces output byte-identical to the baseline snapshot captured before T-F10 dispatched. Verified by `diff -r` against `docs/Phase11-project-audit/baseline/` returning no output.
- [ ] `pytest tests/ -v` is green on `main`.
- [ ] `ruff check src/ tests/ scripts/` is clean on `main`.
- [ ] No new audit findings have been introduced (subagent self-audit per each spec's acceptance gate, plus user spot-check during PR review).
- [ ] `graphify-out/graph_delta.md` carries a dated entry for T-F10's structural moves (one entry covering the whole layout sweep is acceptable rather than per-file).
- [ ] `graphify-out/graph_delta.md` carries dated entries for T-F07 and T-F08 (each PR appends its own deletion summary per the spec acceptance gates).
- [ ] `docs/test-policy.md` reflects the marker pass (T-F11 §C and §F edits).
- [ ] `config/README.md` reflects the SmartMoney and unused-domain removals (T-F07 step 8, T-F08 step 7).
- [ ] No file remains under the deleted paths (`src/agents/analysts/smart_money/`, `src/contract/extractors/smart_money.py`, `src/data/models/{earnings,analyst_consensus,short_interest,options}.py`, the four provider subpackage directories, `tests/agents/`, `tests/analysts/`, `tests/executor/`, `tests/unit/executor/`, `tests/orchestrator/`, the SmartMoney fixture JSONs).

When all of the above hold, Plan A's foundation is complete and Plan B (correctness fixes) is unblocked.

---

## 8. Risks and rollbacks

### Plan-wide risks

- **Pytest discovery breakage from T-F10.** A mishandled `git mv` can leave a stale `__pycache__/` entry that confuses collection, or an `__init__.py` omission that breaks the import path. *Mitigation:* T-F10's spec mandates a `find tests/ -name __pycache__ -type d -exec rm -rf {} +` sweep before the verification run, and the `--collect-only -q | wc -l` parity check catches any discovery loss.
- **Hidden SmartMoney import in T-F07.** A dynamic-string reference (e.g. an analyst-name lookup built at runtime) could survive the pre-flight grep. *Mitigation:* the post-flight grep audit and the single-tick backtest. If the orchestrator wires SmartMoney via a runtime lookup the backtest will fail to start; the safety net catches it before merge.
- **Unused-domain validator regression in T-F08.** `src/data/__init__.py`'s `_validate_active_providers_are_registered` will raise at import time if `config/data.json` references a domain that the registry no longer knows about. *Mitigation:* T-F08 step 6 deletes the four `config/data.json` provider rows in the same PR as the registry deletion; ordering inside the PR ensures the validator never sees a transient inconsistency between `main`-state config and post-PR registry.
- **Backtest non-determinism.** If the canonical window is not actually deterministic across runs at the baseline commit, the diff oracle is meaningless. *Mitigation:* the baseline-capture step in §4 should be performed twice on the same commit and the two runs diffed. If they differ, the baseline-capture is paused and the non-determinism is investigated before Plan A dispatches.
- **Stale `pytest.ini` interaction with T-F11.** If `pytest.ini`'s marker table is out of sync with `docs/test-policy.md` §C, the policy-doc edit may document markers that pytest doesn't recognise. *Mitigation:* T-F11 explicitly forbids editing `pytest.ini`; the spec's "do not modify `pytest.ini`" rule means the policy doc is the only thing that changes, and the four markers (`integration`, `slow`, `contract`, `replay`) are all already declared in `pytest.ini` per the T-F11 scope statement.

### Rollback paths

- **Pre-merge.** The PR is closed and the branch deleted. `main` is untouched. The next attempt re-dispatches the subagent from a clean worktree with whatever additional guard the failure exposed.
- **Post-merge.** Because every Plan A change is either a `git mv`, a one-line annotation, or a deletion, `git revert <merge-sha>` produces a clean reverse-patch. The revert is committed directly to `main` (no PR ceremony for the revert itself); the underlying PR is then re-opened and re-worked.
- **Baseline drift discovered late.** If a Plan B PR uncovers that an earlier Plan A PR did in fact change behaviour (and the baseline-diff oracle missed it because of, say, an artefact-naming change), the rollback is the same `git revert` path. Plan B halts until Plan A is re-verified.

---

## 9. Open questions and explicit deferrals

Plan A intentionally does **not** address:

- **Silent-failure inverts (Theme A).** All test rewrites that invert assertions defending the silent-failure attractor pattern are owned by **Plan D** (paired with source-audit F4 — the surfacing primitive). See test-audit SUMMARY Theme A: nine tests across six subsystems codify silent-failure as desired. None are touched in Plan A.
- **Lifecycle ADK-tables coverage** (`lifecycle.md` P0-01 through P0-04 + source F2). Owned by **Plan B** wave 3, dispatched as T-F03.
- **Live-only latent bombs** (broker `await`-on-sync, get_portfolio silent-drop, snapshotter cold-start, orchestrator `datetime` boundary). Owned by **Plan B** as T-F04 (paired with source F3).
- **Executor `"positions"` → `user:positions`** bare-key cleanup. Owned by **Plan B** as T-F06 (paired with source F8).
- **Strategist cleanup and dual `PositionThesis` drop.** Owned by **Plan C** as T-F05 (paired with source F7).
- **Contract parallel-fixture cleanup.** Owned by **Plan C** as T-F09.
- **Completion-only assertion rewrites.** Owned by **Plan C** as T-F12.
- **Empty-package and dead-helper sweep** (`src/agents/attribution/`, `src/deploy/`, dead memory helpers, `Broker.position_size`, deprecated stance-caps from `config/strategist.json`). Recommended as a single sweep PR (source-audit "F9" grouping) — not in Plan A. Whether it belongs to Plan C or its own tail Plan is open.
- **Spec C / Phase 2 hydration** (`orchestrator.md` P0-03). Deferred this cycle per `docs/Phase11-project-audit/fix-plan/README.md` Decision 6.
- **§C-Rule 7 boundary decision** (source-audit Theme 4 — pipeline sub-agents writing SQLAlchemy mid-tick). Deferred; needs a strategic call between doc carve-out vs lift-persistence-above-the-pipeline refactor before any plan can own it.
- **`scripts/` audit.** Source-audit Open Question 5 recommends a sibling spec for the four `scripts/` boundary findings. Not in Plan A; not yet assigned to any plan.
- **`docs/contract-invariants.md` drift fix** (source-audit Theme 5, the "F1" doc-patch PR). Should ideally land **before** Plan B so Plan B's PRs are reviewed against a corrected spec, but it does not block Plan A. Recommend dispatching it as a short standalone PR concurrent with T-F10.

### Open question for the user

- **Order of T-F07 vs T-F08 within A2.** The plan treats them as parallel. If review bandwidth is constrained and one must precede the other, the recommendation is T-F08 first (smaller surface, doesn't touch the orchestrator) so that T-F07's review is the longer focused read. Confirm or override.

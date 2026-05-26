# Plan B — Parallel-branch collapse

**Phase:** 11 — project audit remediation
**Wave grouping:** the three PRs Phase 11's dispatch table assigns to Plan B (T-F05, T-F06, T-F09)
**Predecessor:** Plan A (mass deletions + layout sweep + foundational test inverts)
**Successors:** Plan C, Plan D (sequenced after this plan; out of scope here)
**Date drafted:** 2026-05-25

---

## 1. Plan overview

Plan B is the **parallel-branch collapse** phase of the Phase 11 remediation cycle. The codebase has accumulated several spots where two implementations — an older one and a newer one — coexist, with tests pinning the older shape in place. Each PR in Plan B picks the **live (newer) side**, deletes the dormant branch, and migrates or removes the tests that were anchoring the dead code.

Because the live side is what production already executes today, the **runtime behaviour after Plan B should be byte-identical to the behaviour before Plan B**. The safety net is the baseline backtest snapshot captured before Plan A: every Plan B PR re-runs the baseline window and diffs against that snapshot. Any divergence — even a single decimal place — halts the PR for investigation rather than being waved through.

Plan B follows Plan A because Plan A's layout sweep (T-F10) consolidates the test directories that Plan B reshapes, and Plan A's mass deletions (T-F07 SmartMoney, T-F08 unused data domains) remove fixture / extractor sites that would otherwise pollute Plan B's diff. Plan B cannot dispatch until Plan A is fully merged into `main`.

Plan B is more fragile than Plan A. Plan A deleted code that was already unwired or whose deletion was unambiguous. Plan B deletes code that *looks* dead but may still be reached by an unexpected path (a fallback branch, an integration test that goes through the live Runner, a config-driven seam). The baseline-diff gate is the line of defence; the per-PR risk discussion in §9 names the specific concerns per PR.

---

## 2. PRs included

All three PRs touch disjoint subsystems and are dispatchable in parallel within Plan B.

| T-F id | Title                                                                 | Branch                                  | Diff size | Findings closed (source / test) | Live-side choice                                                                                              |
|--------|-----------------------------------------------------------------------|-----------------------------------------|-----------|---------------------------------|---------------------------------------------------------------------------------------------------------------|
| T-F05  | Strategist cleanup, dual `PositionThesis` drop, `evidence_view` delete | `fix/T-F05-strategist-cleanup`          | medium    | 9 source / 7 test               | Canonical `agents.strategist.position_thesis.PositionThesis`; `contract.strategist_prompt` renderer; raise-on-missing-`tick_id`; raise-on-missing-`strategist_decision`. |
| T-F06  | Executor bare `"positions"` → canonical `user:positions`               | `fix/T-F06-executor-positions-key`      | medium    | 4 source / 6 test (+2 new)      | Canonical `state["user:positions"]` written exclusively by `_executor_thesis_writer_callback` (per §C-Rule 1 Spec B).                                                |
| T-F09  | Contract package parallel-fixture cleanup                              | `fix/T-F09-contract-parallel-fixtures`  | medium    | 8 source / 8 test               | Form4Bundle insider-trade shape; `_resolve_bars` reads `raw["price_history"]["bars"]`; `headline_polarity_mean` (shorter name); `aggregate_score` (load-bearing name); `news` key (not `articles`/`news_items`). |

(Counts above are the findings each spec lists in its "Findings closed" table; refer to the spec files for the per-finding breakdown.)

---

## 3. Sequencing

### 3.1 Plan A → Plan B dependency

**Plan B dispatches only after Plan A has fully merged into `main`.** Concrete pre-conditions:

- **T-F10 (layout sweep)** must be on `main`. T-F06 and T-F05 both write against the consolidated post-T-F10 test directory layout (`tests/unit/agents/executor/`, `tests/unit/agents/strategist/`). Dispatching either PR pre-T-F10 means the subagent edits files that are about to move.
- **T-F07 (SmartMoney delete)** must be on `main`. T-F09 explicitly assumes the SmartMoney extractor file, fixture, `_KEYS` entries, and strategist-prompt bullets are gone. T-F09's step 1 is a re-audit of T-F07's merged diff; if any survivor remains, T-F09 defers rather than re-litigating scope.
- **T-F01a (surfacing primitive)** must be on `main`. T-F05's `tick_id`-missing raise and `decision_writer` no-op raise are wrapped by the primitive that T-F01a introduces (`emit_branch_failed` / `emit_feature_warning`).

If any of those three predecessors has not merged, Plan B halts dispatch and surfaces the gap to the user.

### 3.2 Within Plan B

The three PRs touch disjoint subsystems:

- **T-F05** edits `src/agents/strategist/` and `docs/contract-invariants.md`.
- **T-F06** edits `src/agents/executor/`, plus two single-line read sites in `src/agents/strategist/context_shim.py` and `src/backtest/decision_logger.py`.
- **T-F09** edits `src/contract/extractors/`, `src/contract/evidence.py`, and `src/contract/strategist_prompt.py`.

No source file is touched by more than one PR. There is **no internal ordering**. The three PRs dispatch in parallel; whichever subagent finishes first merges first; the others rebase if a trivial conflict surfaces (none expected).

**One coordination point:** T-F05 and T-F09 both touch `tests/unit/agents/strategist/test_evidence_view_missing_report.py` — T-F05 deletes the file after migrating its assertion; T-F09 would otherwise also drop the `raw_text=None` line from that file. Per T-F09's spec text, whichever PR lands second inherits a no-op for that line. The convention is documented inline in both specs; no further sequencing rule needed.

---

## 4. Behaviour-preservation invariant

The defining property of Plan B is that **picking the live side leaves runtime behaviour unchanged**. The invariant has to hold per-PR; this section spells out concretely what "live side" means for each.

### 4.1 T-F05 — Strategist cleanup

The live sides chosen by T-F05 are:

- **Canonical `PositionThesis` class:** `src/agents/strategist/position_thesis.py`. Production `src/` importers already point at this class — the spec confirms only four test files still construct the legacy `agents.strategist.schema.PositionThesis`. Deleting the legacy class is behaviour-preserving for production because nothing in production reads it.
- **Canonical renderer:** `contract.strategist_prompt.render_all_ticker_blocks`. Production rendering already routes through this; `src/agents/strategist/evidence_view.py` has zero `src/` callers. Deleting `evidence_view.py` is behaviour-preserving for production.
- **`tick_id` source:** `state["tick_id"]`. The legacy fallback (`or state.get("recorded_at", "unknown")`) is the silent-failure attractor; the live path always populates `state["tick_id"]` before the strategist runs. The raise replaces the literal `"unknown"` only on a contract-violation path that should not occur in production.
- **`strategist_decision` presence:** the strategist branch always runs in production and always writes `strategist_decision`. The new raise replaces a silent no-op that should not fire on the happy path. **Risk noted in the spec (§Risks):** if a cold-start tick legitimately yields without writing the decision, the raise must downgrade to `emit_branch_failed` + degraded empty-stance write — the spec explicitly authorises that fallback after confirmation against the baseline backtest.
- **Documented canonical instance:** `docs/contract-invariants.md` §C-Rule 1 — updated from the legacy `_strategist_validation_callback` shim to the `StrategistEnricher` BaseAgent + `state_delta` Event, which is the actual production write path.

**Behaviour preservation:** the only runtime change is replacing silent-failure attractors with hard raises on paths that should not fire. The happy-path code is structurally untouched.

### 4.2 T-F06 — Executor `"positions"` key collapse

The live side is **`state["user:positions"]`** — the key written exclusively by `_executor_thesis_writer_callback` via ADK's auto-yielded delta-tracked pattern (per `contract-invariants.md` §C-Rule 1 Spec B clarification). The bare `"positions"` key is the dormant side and is removed entirely:

- Executor stops writing `"positions"` at agent.py:94, 179, 259, 276, 317-324.
- Two external readers migrate from `state["positions"]` to `state["user:positions"]`:
  - `src/agents/strategist/context_shim.py:121-125`
  - `src/backtest/decision_logger.py:335`

**Open question flagged in the spec (§Risks, §Implementation step 4):** within-tick BUY → SELL coordination may previously have relied on the bare-key bridge. The spec instructs the subagent to **first try removing the bridge entirely** and **only reinstate via the `temp:positions_pending` scratch key (Rule-8 observability prefix) if a test exercising real same-tick BUY+SELL on the same ticker fails**. The recommendation is "the bridge is likely vestigial because the after-callback iterates `decision.stances` rather than `state['final_orders']`", but this is not confirmed before implementation. Plan B should treat the bridge as a known open question — the subagent's empirical finding (does any test fail when the bridge is removed?) is the decisive evidence.

The companion source fixes bundled in T-F06 (P1-02 BUY-without-stance surfacing, P1-03 fill-price OR-chain narrowing, P2-01 dead `resolve_broker_call` deletion) all replace silent-degradation paths with loud surfacing or remove fossil code — none of them changes a happy-path output.

**Behaviour preservation:** assuming the bare-key bridge is genuinely vestigial (the empirical question above), the only state change is dropping a duplicate write whose readers all migrate in the same PR. The cross-tick state shape that ADK persists is unchanged because `user:positions` was already the canonical key.

### 4.3 T-F09 — Contract parallel-fixture cleanup

The live sides are documented explicitly in the spec and cited from the source audit:

| Site                                | Live side picked                                                                                | Justification (per spec)                                                                 |
|-------------------------------------|--------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| Fundamental extractor               | Form4Bundle shape (delete flat-list `_insider_aggregates_from_flat`, `_derivative_aggregates`)   | Source audit `contract.md` P1-01 records production `fetch_agent` emits Form4Bundle.     |
| Technical `_resolve_bars`           | `raw["price_history"]["bars"]` (branch 2 of three)                                               | Production writer emits `{"price_history": ph.model_dump()}` where `ph_payload["bars"]` is the list. |
| News alias `headline_polarity_mean` | Keep `headline_polarity_mean`; drop `_7d` suffix                                                 | Spec disposition: "pick the shorter, undated name as the single canonical name; migrate the strategist-prompt consumer." Note: this is the **opposite** end of the alias from what the strategist prompt currently reads (`_7d`), so the migration is from alias-side to primary-side. |
| Social alias `aggregate_score`      | Keep `aggregate_score`; drop `social_aggregate_score`                                            | Spec disposition: "pick the load-bearing name as the single canonical name." This is the alias side (the strategist already reads it), so emission renames from primary → alias. |
| News three-key alternative          | Keep `"news"`; drop `"articles"` and `"news_items"` fallbacks                                    | Source audit P2-03 records production writes `{"news": [...]}`.                          |
| `social_volume_z` key               | Delete entirely (dead — no live writer or reader)                                                | Source audit P2-04.                                                                       |
| `AnalystEvidence.raw_text` field    | Delete entirely (dead — no production writer or reader)                                          | Source audit P2-01.                                                                       |

**Open question on the news alias:** the spec migrates the strategist-prompt consumer at `strategist_prompt.py:276` from `headline_polarity_mean_7d` (which the prompt currently reads) to `headline_polarity_mean`. This is a runtime read-site change — the prompt rendering after Plan B will read a different dict key than today. If both keys store identical values (the audit's premise), behaviour is preserved. **The spec flags this in §Risks:** "the strategist prompt consumer migration silently breaks the rendered prompt if `strategist_prompt.py:276` was reading the alias because the alias survived a previous rename and the primary was never populated." Mitigation per the spec: confirm `tests/unit/contract/test_strategist_prompt_layout.py` exercises the "Mean polarity" bullet content; add the assertion in this PR if it does not. The baseline-backtest diff is the second line of defence.

**Behaviour preservation:** assuming the alias / primary pairs store identical values (audit premise; verified in-PR), and assuming the prompt-content tests catch any rendering regression, the runtime output is unchanged.

---

## 5. Pre-flight: confirm Plan A baseline is still valid

Before any Plan B PR dispatches, re-establish the Plan A safety net against current `main`:

1. **Locate the baseline snapshot** captured before Plan A under `docs/Phase11-project-audit/baseline/` (artefact tree from `scripts.backtest_run --window baseline-2025-09`).
2. **Re-run the baseline backtest** against the post-Plan-A `main`:
   ```bash
   PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
   ```
3. **Diff the new artefact tree against the snapshot.** Expected outcome: byte-identical output (Plan A was supposed to be behaviour-preserving for the same reason Plan B is — deletions and layout moves, no semantic change).
4. **If the diff is empty:** Plan B is clear to dispatch.
5. **If the diff is non-empty:** halt Plan B. The discrepancy is a Plan A issue and must be investigated before Plan B adds further deletions. File an incident against the offending Plan A PR; do not start any Plan B PR until the baseline is reconciled.

This pre-flight is non-negotiable. Plan B uses the same baseline as Plan A's success criterion; if Plan A drifted from baseline silently, every Plan B comparison is invalidated.

---

## 6. Per-PR safety net

Each Plan B PR runs the same three-stage safety net before opening for review:

### 6.1 Test suite

```bash
.venv/bin/python -m pytest tests/ -v
```

The full suite must be green. No `-k` filtering; no skipped tests added in this PR; no `--no-verify` on the commit.

### 6.2 Lint

```bash
.venv/bin/python -m ruff check src/
```

Clean. Any new warnings (e.g. from a deleted-but-still-imported symbol) must be addressed in-PR.

### 6.3 Baseline backtest diff

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
diff -r <new artefact tree> docs/Phase11-project-audit/baseline/<snapshot>
```

**Expected output: byte-identical.** Plan B's behaviour-preservation invariant requires this. If the diff shows any difference — even a single decimal, a re-ordered key in a JSON file, a different timestamp in a log — the PR is rolled back for investigation. **Any diff = rollback.** The subagent does not try to explain or rationalise the diff; the diff itself is grounds for halting the PR until a human reviews.

The rationale: Plan B picks the live side of dual implementations. By definition the dormant side should not be reached at runtime; removing it should not change any output. A diff means either (a) the dormant side was actually reached (the audit was wrong about which side is live), or (b) the PR introduced an incidental regression. Both cases need investigation, not autonomous resolution.

### 6.4 Sub-bullet checks per PR

In addition to the three stages above, each spec lists post-PR grep checks (e.g. T-F06's `grep -rn '"positions"' src/ --include="*.py"` returning zero state-write hits; T-F09's combined grep over the seven deleted symbol names). The subagent runs those greps before pushing and includes the output in the PR description.

---

## 7. Subagent dispatch protocol

Plan B dispatches in the same shape as Plan A.

### 7.1 Per-PR dispatch

For each of T-F05, T-F06, T-F09:

- **Spec path:** `docs/Phase11-project-audit/fix-plan/T-F<NN>-<slug>.md` (the three files referenced in §2).
- **Branch convention:** `fix/T-F<NN>-<slug>` (matches each spec's `Branch:` header).
- **Worktree:** the subagent works in a git worktree off current `main` (Plan A merged in).
- **Autonomy bounds:** edit + run tests + commit + push + open PR. No merge to `main` — the user reviews the diff before merging.
- **No `--no-verify`:** if a pre-commit hook fails, the subagent fixes the underlying issue and creates a new commit. No bypassing.
- **No `--amend`:** every fix after the initial commit is a fresh commit on the feature branch.
- **No force-push to `main`:** never; force-push to the feature branch is allowed if a rebase is needed.

### 7.2 Failure handling

- **Test failure that the spec didn't anticipate:** the subagent surfaces it to the dispatcher rather than papering over it. The spec is the canonical source; if reality disagrees, the spec gets updated, not the test.
- **Baseline-backtest diff:** halt. Roll back the local commits. Surface the diff to the user. Do not push.
- **`resolve_broker_call` or bare-`"positions"` hidden caller found in T-F06:** follow the spec's documented override path (leave the helper in place, downgrade to `_deprecated` warning, file a note). Do not delete on assumption.
- **T-F07 partial-deletion gap found by T-F09's step 1:** halt T-F09 and surface to the user; do not re-litigate T-F07's scope from inside T-F09.

### 7.3 Commit message format

Per the fix-plan README:

```
fix(<subsystem>): <one-line subject>

<body — cite finding IDs closed by short ID, e.g. "Closes agents-strategist.md
P1-01, P1-02, P1-04, P2-01, P2-02, P2-05, P2-06, P3-02; test
strategist.md P0-01, P0-02, P0-03, P1-01, P1-02, P2-01.">

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## 8. Acceptance criteria for Plan B as a whole

Plan B is complete when **all** of the following hold:

- [ ] T-F05, T-F06, T-F09 are all merged into `main`.
- [ ] The baseline backtest against post-Plan-B `main` is byte-identical to the snapshot in `docs/Phase11-project-audit/baseline/`.
- [ ] `grep -rn "from agents.strategist.schema import PositionThesis" src/ tests/` returns no hits.
- [ ] `grep -rn "agents.strategist.evidence_view" src/ tests/` returns no hits.
- [ ] `grep -rn '"positions"' src/ --include="*.py" | grep -v 'user:positions' | grep -v 'temp:positions'` returns no state-write hits.
- [ ] `grep -rn "_insider_aggregates_from_flat\|raw_text\|social_aggregate_score\|headline_polarity_mean_7d\|social_volume_z\|raw\[.articles.\]\|news_items" src/ tests/` returns no hits.
- [ ] Full `pytest tests/` is green on `main`.
- [ ] `ruff check src/` is clean on `main`.
- [ ] Each merged PR has its acceptance-criteria checklist (from its spec) ticked.
- [ ] The `graphify-out/graph_delta.md` carries an entry per PR documenting the deletions and renames.

Plan B's contribution to the wider Phase 11 effort: the named parallel-branch debt in the strategist, executor, and contract subsystems is cleared. Other parallel-branch sites (if any) outside these three PRs remain — see §10.

---

## 9. Risks and rollbacks

Plan B's signature risk is that **"dead-looking code may not be dead".** Each PR carries that risk in a specific shape:

### 9.1 T-F05

- **`decision_writer` raise breaks the backtest** — if any legitimate path yields without writing `strategist_decision` (e.g. a cold-start tick before any analysts emit). The spec's mitigation: confirm against `baseline-2025-09` smoke first; downgrade the raise to `emit_branch_failed` + degraded empty-stance write if a legitimate empty-decision path exists. Plan B's baseline-diff gate catches this.
- **`tick_id`-missing raise breaks tests that build state without it** — every affected test gets `tick_id="t-test"` in the same PR. Mechanical fix.
- **Doc edit breaks `test_invariants_doc_carveout.py` substring matcher** — the spec sequences the doc edit first (step 1) and confirms / widens the substring assertion in the same commit.
- **Rollback:** revert the merge commit on `main`. The PR's eight sub-steps are sequentially staged, so each can be reverted individually if needed.

### 9.2 T-F06

- **Same-tick BUY+SELL coordination genuinely broken by bare-key removal** — if a test exercises real same-tick BUY+SELL on the same ticker. Spec mitigation: empirical first (try removing the bridge, see if tests fail); reinstate via `temp:positions_pending` scratch key (Rule-8 prefix) only if needed. Document the decision in the executor docstring.
- **The after-callback isn't firing in unit tests that bypass the Runner** — the six test rewrites need either a Runner harness or an explicit callback invocation. Spec mitigation: prefer explicit callback invocation; Runner harness only where unit shape genuinely requires it.
- **`resolve_broker_call` has a hidden caller** outside graphify's view (e.g. a script). Spec mitigation: grep `src/`, `scripts/`, `tests/` before deletion. If any non-test caller surfaces, leave the helper in place and downgrade to `_deprecated`.
- **Reader shape mismatch** between `state["positions"]` and `state["user:positions"]` — the audit implies the shapes are identical; spec instructs the subagent to confirm in the diff. The baseline-diff gate is the empirical confirmation.
- **Rollback:** revert the merge commit on `main`. The bare-key removal is reversible by reverting the executor diff; the two reader switches must be reverted in lock-step (single revert commit covers both).

### 9.3 T-F09

- **Strategist prompt consumer migration silently changes the rendered prompt** — the news-alias and social-alias migrations swap one read key for another. If the two keys ever store different values (the audit premise is that they do not), the rendered prompt content changes silently. Spec mitigation: confirm `tests/unit/contract/test_strategist_prompt_layout.py` exercises the "Mean polarity" bullet content; add the assertion in this PR if it does not. Baseline-diff gate is the second line of defence.
- **News fixture rewrite breaks unrelated tests** that glob over `tests/fixtures/contract/`. Spec mitigation: full-suite run is the empirical check; subagent reads any failing test and rewrites the fixture consumer rather than reverting the cleanup.
- **T-F07 did not delete everything T-F09 assumes is gone** — step 1 of T-F09's implementation audits T-F07's merged diff. If smart_money survives, T-F09 defers rather than re-including the smart_money findings.
- **Rollback:** revert the merge commit on `main`. Each of T-F09's six sub-commits is a clean boundary, so partial reverts are possible.

### 9.4 Plan-level rollback

If the baseline-diff gate catches a divergence post-merge (e.g. a CI scheduled run flags it), the answer is:

1. **Revert the offending PR's merge commit on `main`.** No `git reset --hard`, no force-push; a plain revert commit so the history is auditable.
2. **Re-run the baseline backtest** against the reverted `main` to confirm parity is restored.
3. **Re-open the PR's feature branch** for the subagent (or a human) to investigate the diff and re-spec.

No Plan B PR is "merged-and-forgotten". The baseline-diff gate must be re-run on `main` after each merge.

---

## 10. Open questions and explicit deferrals

### 10.1 Open questions

The two live-side decisions that the specs flag as needing in-PR empirical confirmation:

1. **T-F06: is the bare-`"positions"` BUY → SELL bridge actually vestigial?** The spec recommends "try removing the bridge entirely first; reinstate as `temp:positions_pending` only if a test fails that exercises real same-tick BUY+SELL on the same ticker." This is a decision the subagent makes empirically during implementation. If a test fails, the subagent reinstates the bridge under the Rule-8 prefix and documents the decision; if no test fails, the bridge is gone for good.

2. **T-F09 (news-alias migration): does the strategist-prompt content test cover the "Mean polarity" bullet?** The spec instructs the subagent to confirm and to add the assertion in-PR if it does not. The runtime risk is small (the two alias values should be identical) but the test gap is real.

Neither question blocks dispatch — they are resolved during implementation.

### 10.2 Explicit deferrals

The following are out of Plan B's scope and are addressed (or noted) elsewhere:

- **Plan C and Plan D** are the next two phases of Phase 11 remediation. Their scope is outside this document.
- **Other parallel-branch sites in the codebase outside T-F05 / T-F06 / T-F09 are not addressed here.** The source audit's Theme 2 lists several more (e.g. the four unused data domains, the deprecated `StanceCaps` config fields, the dead `agents/memory/` helpers). Some of those are handled by Plan A's T-F07 and T-F08; the rest are out of scope until a follow-up plan picks them up.
- **The `_strategist_validation_callback` legacy shim** (source `agents-strategist.md` P2-04, test `strategist.md` P1-03) is **deliberately deferred** by T-F05's own `Out of scope` block. Five tests drive the shim directly; unwinding intersects with `_patched_build_strategist` in the backtest smokes and needs its own diff review. Filed for a follow-up T-F.
- **Strategist config-key promotion** (source P2-03 — `temperature`, `frequency_penalty`, etc.) — deferred per T-F05's `Out of scope`.
- **The four parallel executor test directories beyond the post-T-F10 consolidation** — owned by T-F10 (already on `main` if Plan A is complete).
- **Layout consolidation of the contract package** — owned by T-F10.
- **T-F09 step 7's "add content guard" sweep** is scoped to the three surviving extractors (fundamental, news, technical); the smart_money extractor is gone by then.
- **Executor source P3-01 cross-subsystem follow-ups** (items 1 and 2 — `contract-invariants.md` `thesis_revision` ⇄ `thesis` drift; `src/agents/strategist/schema.py:138-139` MemoryWriter ownership claim) — belong to the contract-doc patch PR and a strategist PR respectively. Not in Plan B.

---

## Appendix — quick-reference command list per PR

The verification commands each spec lists, collected here for convenience. The subagent runs the full set per PR.

### T-F05

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
grep -rn "from agents.strategist.schema import PositionThesis" src/ tests/
grep -rn "agents.strategist.evidence_view" src/ tests/
```

### T-F06

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
.venv/bin/python -m pytest tests/unit/agents/executor/ tests/integration/test_executor_with_fake_broker.py -v
grep -rn '"positions"' src/ --include="*.py" | grep -v 'user:positions' | grep -v 'temp:positions'
```

### T-F09

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
grep -rn "_insider_aggregates_from_flat\|insider_trades.*list\|raw_text\|social_aggregate_score\|headline_polarity_mean_7d\|social_volume_z\|raw\[.articles.\]\|news_items" src/ tests/
```

### Plan-level (per PR)

```bash
# Behaviour-preservation gate (any diff = rollback)
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
diff -r <new artefact tree> docs/Phase11-project-audit/baseline/<snapshot>
```

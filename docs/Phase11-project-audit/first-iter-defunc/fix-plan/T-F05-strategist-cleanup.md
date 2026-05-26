# T-F05 — Strategist cleanup, dual `PositionThesis` drop, `evidence_view` deletion

**Wave:** 4 (parallel)
**Pairs source-audit fix:** F7 (strategist subsystem cleanup)
**Branch:** `fix/T-F05-strategist-cleanup`
**Depends on:** T-F01a (the surfacing primitive — used for the
`tick_id` fallback raise and the `decision_writer` no-op raise);
T-F10 (layout sweep — three root-level strategist tests move into
`tests/unit/agents/strategist/`).
**Estimated diff size:** medium

## Scope

Collapse the strategist subsystem's accumulated parallel structures
and silent-failure attractors into one canonical shape. Three
threads:

1. **Drop the legacy `agents.strategist.schema.PositionThesis` class**
   (source `agents-strategist.md` P1-01). Production `src/` importers
   already point at the canonical `agents.strategist.position_thesis`;
   only four tests still construct the legacy variant. Migrate the
   tests and delete the legacy class.
2. **Delete `src/agents/strategist/evidence_view.py`** (source
   `agents-strategist.md` P1-02). Production rendering routes through
   `contract.strategist_prompt.render_all_ticker_blocks`; the
   `evidence_view` module has zero `src/` callers. Three test files
   anchor it in place — delete two, migrate the third's defence-in-
   depth assertion onto the live renderer.
3. **Fix two silent-failure attractors:**
   - `tick_id="unknown"` fallback at `src/agents/strategist/enricher.py:176`
     and `src/agents/strategist/decision_writer.py:90`
     (source `agents-strategist.md` P1-04). Replace both with a
     hard raise.
   - `decision_writer` silent-no-op when `strategist_decision` is
     missing at `src/agents/strategist/decision_writer.py:53-57`
     (source `agents-strategist.md` P2-05). Replace with raise.

Plus secondary tidy-ups the source-audit grouped under the same
subsystem: update the `docs/contract-invariants.md` §C-Rule 1
"canonical instance today" paragraph to point at the
`StrategistEnricher` BaseAgent (source P1-03 — doc-side change owned
here because it ships with the strategist refactor); trim
`derivation.py`'s docstring reference to the deleted
`agents.risk_gate.lifecycle` module (source P2-06); delete the
unreachable trailing `yield` in `decision_writer.py:97-99`
(source P3-02).

### In scope

- **Source deletions:**
  - `src/agents/strategist/schema.py` — drop the `PositionThesis`
    class (lines 36-68). Keep `StrategistDecision` / `StrategistLLMDecision`.
  - `src/agents/strategist/evidence_view.py` — delete the whole file.
  - `src/agents/strategist/enricher.py:346-351` — delete the
    `build_strategist_enricher()` zero-arg factory (source P2-02).
  - `src/agents/strategist/derivation.py` — drop the three unused
    `TickContext` fields (`tick_id`, `decision_tag`, `now` at
    lines 113-115) and the unused
    `DerivedFields.decision_tags` accumulator (source P2-01). Keep
    `derive_decision_tag()` itself (still tested).
- **Source raises (replacing silent fallbacks):**
  - `src/agents/strategist/enricher.py:176` — drop the
    `or state.get("recorded_at", "unknown")` chain; replace with
    `tick_id = state["tick_id"]` (let `KeyError` propagate, or wrap
    as `StrategistContractViolation` for symmetry with the other
    raises in the module). Also drop the dead `recorded_at`
    secondary lookup per the source-audit note.
  - `src/agents/strategist/decision_writer.py:90` — same treatment;
    write `tick_id=state["tick_id"]` without the literal-`"unknown"`
    fallback.
  - `src/agents/strategist/decision_writer.py:53-57` — invert the
    `if not raw_decision: return` guard. The strategist branch
    always runs in production; absence at this stage is a
    contract violation. Replace with raise; the surfacing primitive
    (T-F01a) emits the `branch_failed` warning before the raise so
    the run log captures context.
- **Source cleanups:**
  - `src/agents/strategist/decision_writer.py:97-99` — delete the
    unreachable trailing `yield` plus its misleading comment.
  - `src/agents/strategist/derivation.py:16-20` — trim the module
    docstring to drop the `risk_gate.lifecycle` cross-reference per
    source P2-06.
  - `docs/contract-invariants.md` §C-Rule 1 (the "canonical instance
    today" paragraph at approximately lines 244-251) — update to
    describe the `StrategistEnricher` BaseAgent + `state_delta`
    Event as the production write path; either drop the
    `_strategist_validation_callback` example or relabel as the
    legacy test-shim. Source-audit `agents-strategist.md` P1-03
    is filed against the doc per RUBRIC routing rule §2-C7.
- **Test migrations:**
  - The four legacy-`PositionThesis` test sites migrate onto
    `agents.strategist.position_thesis.PositionThesis` (test-audit
    `strategist.md` P1-01):
    - `tests/integration/test_strategist_v2_smoke.py:137`
    - `tests/unit/test_strategist_schema.py:6` (file is also moved
      by T-F10 layout sweep — coordinate)
    - `tests/unit/agents/strategist/test_position_thesis_opened_tick_id.py:6`
    - `tests/unit/agents/strategist/test_strategist_callbacks_v2.py:11`
    Field deltas: `opened_tag` → `opened_tick_id`, `last_review_note`
    → `last_reviewed_decision` + `last_reviewed_reason`,
    `opened_price` becomes required.
  - After migration,
    `tests/unit/agents/strategist/test_position_thesis_opened_tick_id.py`
    is redundant with the canonical `test_position_thesis.py`
    round-trip — delete.
- **Test deletions (evidence_view):**
  - Delete
    `tests/unit/agents/strategist/test_evidence_view.py` (10
    tests).
  - Delete
    `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py`
    (2 tests).
  - Migrate
    `tests/unit/agents/strategist/test_evidence_view_missing_report.py`'s
    `(is_no_data=False, report=None) → placeholder` assertion onto
    `tests/unit/contract/test_strategist_prompt_layout.py` (the
    production renderer), then delete the source file. (Test-audit
    `strategist.md` P1-02 + `contract-package.md` P1-07 confirm the
    keep-this-one-assertion disposition.)
- **New surfacing tests (paired with the raises above):**
  - `tests/unit/agents/strategist/test_enricher.py` — add
    `test_enricher_raises_when_tick_id_missing` (test-audit
    `strategist.md` P0-01).
  - `tests/unit/agents/strategist/test_decision_writer.py` — add
    `test_decision_writer_raises_when_tick_id_missing` (test-audit
    `strategist.md` P0-01).
  - Same file — **invert** the existing
    `test_no_op_without_decision` (lines 88-94) to assert
    `pytest.raises(...)` per test-audit `strategist.md` P0-02. The
    current test ratifies the silent-no-op as the contract; the
    invert is mandatory once the source guard flips.
- **Strengthen weak assertions exposed by the cleanup:**
  - `tests/integration/test_strategist_v2_smoke.py:243-264` —
    strengthen per test-audit `strategist.md` P0-03: assert
    `intent in {"open","hold","trim","close","add","update"}` on
    each stance, assert AAPL held-position stance is *not*
    `intent="open"`, assert `rationale` / `reason` non-empty,
    `caplog` guard against `branch_failed`.
  - `tests/unit/agents/strategist/test_decision_writer.py:97-108`
    (`test_no_op_without_db_session`) — add `events == []`
    assertion per test-audit `strategist.md` P2-01.

### Out of scope

- The `_strategist_validation_callback` legacy shim
  (source `agents-strategist.md` P2-04, test `strategist.md` P1-03).
  The shim's deletion is the *test-only* concern — five tests drive
  it directly, and the source-side disposition is "delete once the
  tests migrate". This is a separate cleanup pass: it intersects with
  the backtest smokes' `_patched_build_strategist` (test P0-04) which
  rebuilds the legacy wiring inside the test, and unwinding that needs
  its own diff review. Defer to a follow-up T-F (file as deferred-
  cleanup if not picked up next cycle).
- Strategist config-key promotion (`temperature`, `frequency_penalty`,
  etc., source `agents-strategist.md` P2-03) — config-convention
  change, deserves its own PR with the matching `config/README.md`
  edit. Defer.
- The `_log_offending_decision` docstring drift (source P3-01) — too
  minor to bundle.
- All contract-extractor parallel-shape cleanups — owned by T-F09.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `agents-strategist.md` source P1-01 | `src/agents/strategist/schema.py:36-68` | Delete legacy `PositionThesis` |
| `agents-strategist.md` source P1-02 | `src/agents/strategist/evidence_view.py` | Delete dead module |
| `agents-strategist.md` source P1-03 | `docs/contract-invariants.md` §C-Rule 1 | Update to point at `StrategistEnricher` |
| `agents-strategist.md` source P1-04 | `src/agents/strategist/enricher.py:176`, `decision_writer.py:90` | Raise on missing `tick_id` |
| `agents-strategist.md` source P2-01 | `src/agents/strategist/derivation.py` | Drop unused `TickContext` fields + `DerivedFields.decision_tags` |
| `agents-strategist.md` source P2-02 | `src/agents/strategist/enricher.py:346-351` | Delete `build_strategist_enricher` factory |
| `agents-strategist.md` source P2-05 | `src/agents/strategist/decision_writer.py:53-57` | Raise on missing `strategist_decision` |
| `agents-strategist.md` source P2-06 | `src/agents/strategist/derivation.py:16-20` | Trim docstring |
| `agents-strategist.md` source P3-02 | `src/agents/strategist/decision_writer.py:97-99` | Delete unreachable trailing `yield` |
| `strategist.md` test P0-01 | `tests/unit/agents/strategist/test_enricher.py`, `test_decision_writer.py` | New `tick_id`-missing raise tests |
| `strategist.md` test P0-02 | `tests/unit/agents/strategist/test_decision_writer.py:88-94` | Invert silent-no-op assertion |
| `strategist.md` test P0-03 | `tests/integration/test_strategist_v2_smoke.py:243-264` | Strengthen stance assertions + caplog |
| `strategist.md` test P1-01 | four sites | Migrate to canonical `PositionThesis` |
| `strategist.md` test P1-02 | three `test_evidence_view*.py` files | Delete two; migrate one assertion |
| `strategist.md` test P2-01 | `tests/unit/agents/strategist/test_decision_writer.py:97-108` | Add `events == []` assertion |
| `contract-package.md` test P2-06 (partial) | `tests/unit/contract/test_invariants_doc_carveout.py` | Carve-out doc updated by P1-03 above; test stays but its substring matches the new wording — confirm in this PR rather than letting the doc edit break the test |

(The legacy-callback test P1-03 / source P2-04 disposition is
*deliberately deferred* — see Out of scope.)

## Implementation steps

1. **Land the doc update first** (`contract-invariants.md` §C-Rule 1
   paragraph). This is the lowest-blast-radius change and unblocks
   the test_invariants_doc_carveout substring matcher.
2. **Delete `evidence_view.py` and migrate the missing-report
   assertion** onto `tests/unit/contract/test_strategist_prompt_layout.py`.
   Delete the two pure-anchor test files. Verify
   `tests/unit/contract/test_strategist_prompt_layout.py` passes with
   the migrated assertion before deleting the third
   `test_evidence_view_missing_report.py`.
3. **Migrate the four legacy-`PositionThesis` test sites** onto the
   canonical class. After migration, delete
   `tests/unit/agents/strategist/test_position_thesis_opened_tick_id.py`
   (redundant). Run `pytest tests/unit/agents/strategist/` green.
4. **Delete the legacy `PositionThesis` class** from
   `src/agents/strategist/schema.py`. Confirm no remaining importers
   (`grep -rn "from agents.strategist.schema import PositionThesis"
   src/ tests/`).
5. **Apply the source raises** (in this order to keep CI green):
   1. Add the surfacing primitive call before each raise
      (T-F01a's `emit_branch_failed` / `emit_feature_warning`).
   2. Flip the `tick_id="unknown"` fallback in `enricher.py:176`.
   3. Flip the `tick_id="unknown"` fallback in `decision_writer.py:90`.
   4. Add the new `test_enricher_raises_when_tick_id_missing` and
      `test_decision_writer_raises_when_tick_id_missing` tests.
   5. Flip the `if not raw_decision: return` guard in
      `decision_writer.py:53-57`.
   6. Invert `test_no_op_without_decision` to assert the raise.
6. **Apply the source cleanups** (low-risk batch):
   - Drop `TickContext.{tick_id,decision_tag,now}` + `DerivedFields.decision_tags`.
   - Delete `build_strategist_enricher()`.
   - Trim `derivation.py` docstring.
   - Delete unreachable trailing `yield`.
7. **Strengthen `test_strategist_v2_smoke.py`** assertions per
   test-audit P0-03.
8. **Run full `pytest tests/`**. Update `graphify-out/graph_delta.md`
   with the deleted files and new test names.

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in
  commit body).
- [ ] `grep -rn "from agents.strategist.schema import PositionThesis"
  src/ tests/` returns no hits.
- [ ] `grep -rn "agents.strategist.evidence_view" src/ tests/` returns
  no hits.
- [ ] No `tick_id.*unknown` literal-string survives in the strategist
  package.
- [ ] Graphify delta entry appended.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
```

## Risks and rollbacks

- **Risk: the `decision_writer` raise breaks the backtest smoke** if
  there is any legitimate path where the strategist branch yields
  without writing `strategist_decision` (e.g. cold-start tick before
  any analysts emit). Mitigation: confirm against
  `baseline-2025-09` smoke first; if a legitimate empty-decision path
  exists, the disposition becomes `emit_branch_failed` + degraded
  empty-stance write rather than raise.
- **Risk: the `tick_id` raise breaks tests that build state without
  it.** Mitigation: every affected test gets `tick_id="t-test"` in
  the same PR; this is the same shape `test_enricher.py` already
  uses, so the fix is mechanical.
- **Risk: the contract-invariants doc edit breaks
  `test_invariants_doc_carveout.py`** substring match. Mitigation:
  step 1 lands the doc edit first; the test's substring assertion
  gets confirmed (or widened to be substring-stable) in the same
  commit.
- **Rollback:** feature branch discardable. The eight sub-changes
  are sequentially staged so each step can be reverted on its own.

## Subagent dispatch prompt sketch

> Implement T-F05 (strategist cleanup) per
> `docs/Phase11-project-audit/fix-plan/T-F05-strategist-cleanup.md`. Context:
> `docs/Phase11-project-audit/source-audit/agents-strategist.md`,
> `docs/Phase11-project-audit/test-audit/strategist.md`,
> `docs/contract-invariants.md` §C-Rule 1,
> `docs/Phase11-project-audit/fix-plan/T-F01-surfacing-primitive-and-inverts.md` for the
> primitive being called before raises,
> `docs/test-policy.md` §A.7. Order matters — see "Implementation
> steps" 1-8. Run the full pytest suite after each step. British
> English throughout. Defer the `_strategist_validation_callback`
> shim deletion (P2-04) per Out of scope.

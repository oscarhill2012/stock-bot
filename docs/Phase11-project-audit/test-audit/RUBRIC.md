# Test-audit rubric

The shared checklist every subsystem test audit follows. The output of
this audit is a written report — no test files are modified in this
workstream. Pruning, strengthening, and reshaping land later under a
separate plan informed by the consolidated findings.

**This audit is source-aware.** It runs after the source audit
(`docs/Phase11-project-audit/source-audit/`). Every subagent is given the matching source-audit
report file (or files) for its area, so it knows:

- Which code is slated for deletion (tests anchoring zombies should be
  deleted, not kept).
- Which P0/P1 source findings are silent-failure attractors and which
  paths therefore need *new or strengthened* surfacing-assertion tests.
- Which "parallel old/new branches" still have tests defending both
  sides — the test-audit recommends which side's tests to drop.

The yardstick documents are:

- `docs/test-policy.md` — target-state for what tests look like, where
  they live, what they may and may not do. The §A hard rules and §E
  anti-patterns are the primary measuring stick.
- `docs/Phase11-project-audit/source-audit/SUMMARY.md` and the individual subsystem files
  under `docs/Phase11-project-audit/source-audit/` — what's broken in the code and what fixes
  are intended.
- `docs/contract-invariants.md` — when reasoning about whether a test
  actually defends a contract invariant.

---

## §1 — Subagent mandate

You are auditing the tests for **one source subsystem only** (e.g. "tests
for `src/agents/strategist/`"). Your scope is **every test file across
the entire `tests/` tree that exercises that source subsystem, regardless
of where in `tests/` that file currently lives**.

This means each subagent does a discovery sweep first:

```bash
grep -rln "from agents.strategist\|src.agents.strategist\|import strategist" tests/ | sort -u
find tests -name "*strategist*" -o -name "*Strategist*" | sort -u
```

Combine the results, dedupe, and list every test file in scope at the
top of your report. Be thorough — tests can live in `tests/unit/`,
`tests/integration/`, `tests/agents/`, `tests/analysts/`, and root-level
`tests/unit/*.py` files. Many subsystems will have tests in three or four
different directories — that itself is a layout finding.

**Read-only.** Do not edit any `.py` file in `tests/`. Do not edit
`src/`, `docs/test-policy.md`, or `docs/Phase11-project-audit/source-audit/*`. The deliverable
is a single Markdown file at `docs/Phase11-project-audit/test-audit/<your-subsystem>.md`
using the schema in §3.

**Source-audit cross-references are MANDATORY.** Before filing any
finding, open the matching `docs/Phase11-project-audit/source-audit/*.md` for your subsystem
and read it. Cite specific source findings by ID (e.g. "P1-03 in
agents-strategist.md") when a test finding stems from one.

**Investigation tools:**

- `Read` for full-file context — do not audit on excerpts.
- `Bash` for grep, find, pytest collection (`pytest tests/path/ --collect-only -q`).
- `Read` of `src/` files **for reference only** — to verify what the
  test claims to exercise actually exists, behaves as the test asserts,
  and isn't slated for deletion per the source audit.
- `graphify-out/` for symbol-level call-graphs when working out whether
  a test target is reachable from anywhere.

---

## §2 — Finding categories

Eight categories, tuned for the test layer. Each finding belongs to
exactly one. Categories C1–C7 are direct test-side analogues of the
source-audit categories; C8 is test-specific.

### T1 — Dead tests (tests of code slated for deletion or already gone)

A test that exercises code the source audit recommends deleting, or
imports a symbol that no longer exists, or asserts on behaviour that has
already been removed. Includes:

- Tests anchoring "test-anchored zombies" (per source-audit Theme 2 — the
  legacy `PositionThesis`, parallel extractor branches, dead memory
  helpers, etc.). If the source audit recommends deletion, the test goes
  with it.
- Tests for empty packages (`src/agents/attribution/`, `src/deploy/`).
- Tests that mock or stub a function whose live callers all went away.
- `@pytest.mark.skip(...)`-decorated tests that have been skipped for
  more than a single commit without an open ticket.

**Disposition:** delete. Note in the suggested action whether the
deletion is conditional on a specific source-fix PR landing.

### T2 — Tests of parallel old/new branches (C2 analogue)

Tests that defend both sides of a source-audit C2 finding. Example: tests
that exercise both the bare-key `"positions"` and the canonical
`user:positions` Spec-B write path. Once the source-audit fix picks one
side, the other side's tests are dead — file them now with a contingent
disposition so they can be pruned in the same PR that lands the C2
collapse.

**Disposition:** delete the loser side after the source-fix PR.

### T3 — Tests that only assert completion (test-policy §A.7 / §E)

The single biggest source of silent regressions per the user memory
`feedback_silent_failures_loud_tests` and `test-policy.md §A.7`. A
passing test is not evidence of correct behaviour — only evidence of
absent exceptions. Common shapes:

- `assert result is not None` and nothing else.
- `assert len(verdicts) == 3` with no per-verdict content assertion.
- "It didn't raise" tests on a path that *should* raise on failure but
  in practice swallows.
- Tests that run a full pipeline tick and don't assert against
  `is_no_data=True`, empty verdict lists, `branch_failed` warnings, or
  the trace dir contents.

**Disposition:** strengthen (add positive-content assertions, or assert
against degradation paths). Do not delete — these were intended to
catch something. If the source path they cover is itself dead, that is
T1 instead.

### T4 — Tests missing for source-audit P0/P1 silent failures (the gap)

The strongest signal the test audit can produce. For every source-audit
P0 or P1 in your assigned area's silent-failure-attractor list (Theme 1
in source SUMMARY.md), check: is there a test that asserts the fix
behaviour? If not, file a T4 finding *naming the source finding* —
this becomes a test that must be written as part of the fix PR.

**Disposition:** add a new test. Sketch the test name and the assertion
shape in the suggested action.

### T5 — Mock at the wrong level (test-policy §A.5)

Tests that monkey-patch above the leaf HTTP boundary — e.g. mocking
`data.providers.registry`, `CachedDataStore`, Pydantic models, or an
analyst's `_run_async_impl` rather than the underlying
`_fetch_company_news` / `_fetch_xbrl_facts` leaf. These bypass type-
and contract-checking and produce green tests that exercise nothing.

**Disposition:** reshape to mock at the leaf seam.

### T6 — Wide-scope monkeypatch / inappropriate state ownership

Tests violating test-policy §A.6 ("Tests own their state"). Common
shapes:

- `monkeypatch.setattr` on an entire class or module rather than a
  single leaf function.
- Tests that read or write the live `config/` tree, `backtests/` tree,
  or the user's home directory.
- Tests that mutate a module-level global another test reads.

**Disposition:** reshape with `tmp_path` / `monkeypatch.setenv` /
narrower setattr.

### T7 — Test-policy hard-rule violations (§A)

Direct deviations from the §A hard rules:

- A.1 — uses real API keys or makes outbound network calls.
- A.2 — writes to the live `backtests/` cache.
- A.3 — runs multi-tick pipelines or uses a non-`baseline-2025-09`
  window.
- A.4 — calls a real LLM without the `RUN_LLM_TESTS=1` gate and the
  `integration` marker.
- A.5 — covered by T5.
- A.6 — covered by T6.
- A.7 — covered by T3 / T4.

Reserve T7 for §A.1–§A.4 (the rules not already given their own
category).

**Disposition:** fix the violation (which usually means the test should
not exist in its current form). If the underlying code is fine and the
test is just badly written, reshape; if the violation is fundamental,
delete.

### T8 — Layout / discoverability / structural

Tests in the wrong directory per test-policy §B; parallel test trees
covering the same source path; conftest fixtures that should be
consolidated; fixture files that aren't used; duplicate tests across
locations; missing markers (`@pytest.mark.slow`, `integration`,
`contract`, `replay`) where the policy requires them.

**Disposition:** move / rename / consolidate / delete. Layout findings
are most useful when filed against a specific path, not in the
abstract.

---

## §3 — Finding schema

One Markdown file per subagent, with this structure. Findings listed in
**severity order** (P0 → P3). Within a severity, no required order.

```markdown
# Test audit — <subsystem path or theme>

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/<file>.md` (and others if relevant)
**Test files in scope:** <count> (full list below)
**Tests collected from those files:** <count> (via `pytest <paths> --collect-only -q`)
**Findings:** <P0 count> P0 · <P1 count> P1 · <P2 count> P2 · <P3 count> P3

## Files in scope

Bulleted list of every test file you considered, grouped by location:

- `tests/unit/agents/<subsystem>/...` — N files
- `tests/integration/...` — N files
- `tests/agents/.../...` — N files (note any layout oddity)
- (etc.)

## Summary

One short paragraph (<= 4 sentences): top 2–3 themes, what the suite
gets right, what it gets wrong, anything the consolidator needs to know.

## Findings

### P0-01 · T4 missing surfacing test · <one-line subject>

- **Location(s):** `tests/unit/agents/<subsystem>/<file>.py` (or "new
  test needed" if T4)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/<file>.md` finding P0-01
- **Confidence:** high
- **Description:**
  One paragraph. What the test does (or doesn't do), why that's
  a problem, what risk it leaves uncovered.
- **Suggested action:**
  One sentence. The shape of the disposition: "delete and reference the
  source-audit PR-F4 surfacing primitive instead", "add a new test
  asserting `caplog` records `branch_failed` is *absent* on the happy
  path", "move to `tests/integration/`", etc.
```

**Finding ID format.** `P<severity>-<NN>` sequential within severity.

**Be specific.** "Strategist tests are weak" is not a finding. "The
single-call assertion in `tests/integration/test_strategist_v2_smoke.py:108`
checks the strategist agent runs without raising, but does not assert
that `state['strategist_decision'].stances` is non-empty — paired with
source-audit `agents-strategist.md` P1-01, this means the legacy
`PositionThesis` shape regression would still pass this test" is a
finding.

---

## §4 — Severity bands

Different to the source rubric. Pick one severity per finding.

| Band | Meaning |
|---|---|
| **P0** | Test currently masks a real source bug (or its absence does so). T3 on a load-bearing path, T4 missing for a source P0/P1 silent-failure attractor, T5 mocking at a level that hides a known C5. Fixing these unblocks the source fix landing safely. |
| **P1** | Test maintains drift risk: T1 dead tests of soon-to-be-deleted code (so they don't block the deletion PR), T2 parallel-branch defenders, T7 hard-rule violations on `tests/integration/` paths. |
| **P2** | Test-policy hygiene without urgency: T6 wide-scope monkeypatch, T8 layout findings, T3 weak assertions on non-load-bearing paths, redundant tests covering the same path multiple ways. |
| **P3** | Cosmetic: naming inconsistencies, missing docstrings on test functions, missing markers, comments-out-of-date. |

Note: a test of dead code (T1) is at most P1, not P0 — the source-audit
PR will sweep the test out alongside the code; the test is not actively
masking anything. P0 is reserved for tests (or test gaps) that *hide*
correctness issues today.

---

## §5 — What to do when you finish

1. Save your report to `docs/Phase11-project-audit/test-audit/<subsystem>.md`. Exact filename
   in your dispatch prompt.
2. Return a short summary in your final message: file path written,
   counts (`P0/P1/P2/P3`), how many test files you walked, how many
   you recommend deleting outright vs strengthening vs reshaping, and
   any cross-subsystem dependencies for the consolidator.
3. Do not commit anything. Consolidation lands the test-audit doc tree
   in one commit at the end.

# Source-audit rubric

The shared checklist every subsystem audit follows. The output of this audit
is a written report — no source code is modified in this workstream. Fix PRs
land later under a separate plan informed by the consolidated findings.

The yardstick documents are:

- `docs/contract-invariants.md` — target-state for what `src/` must satisfy
  at tick boundaries.
- `docs/test-policy.md` — target-state for tests (not in scope here, but
  useful when reasoning about silent-failure attractors §A.7).

These are **target-state**. Where current code deviates, the deviation is a
finding, not a reason to soften the rule.

---

## §1 — Subagent mandate

You are auditing **one subsystem only** (the package path will be passed in).

**Read-only.** Do not edit any `.py` file in `src/`. Do not edit
`contract-invariants.md`. The deliverable is a single Markdown file at
`docs/Phase11-project-audit/source-audit/<your-subsystem>.md` using the schema in §3.

**Investigation tools you should use:**

- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph_delta.md` for
  structural maps (which symbols depend on which, where dead-looking code
  actually has callers, community hubs that reveal load-bearing files).
- `Read` for full-file context — do not audit on file excerpts.
- `Bash` for grep, find, and call-graph queries (`grep -rn "symbol_name"
  src/ tests/ scripts/` to verify "dead" code really has no callers).
- `Read` of `tests/` files **for reference only** — to check whether a
  suspected dead path is actually exercised by a test. Tests are not in
  audit scope; you do not file findings against them.

**Confidence bands.** Every finding carries a confidence (`high` /
`medium` / `low`). A `medium` finding is something that looks wrong but
might have a non-obvious reason you can't see from inside one subsystem.
A `low` finding is a hunch worth investigating but not yet evidence. The
consolidation pass will reconcile cross-subsystem `medium` findings.

---

## §2 — Finding categories

Seven categories. Each finding belongs to exactly one (pick the most
specific). If a finding genuinely spans two, file it under the higher-
severity one and mention the second in the description.

### C1 — Dead code

Functions, classes, methods, modules, imports, config keys, or commented-
out blocks that have **no live callers** anywhere in `src/`, `tests/`, or
`scripts/`.

Includes:

- Empty packages (e.g. `src/deploy/`, `src/agents/attribution/`).
- Scripts under `scripts/` no longer invoked by any documented workflow.
  (Exception: `scripts/replay_backtest.py` is a manual tool the user runs
  himself — see `feedback_test_audit_scope_tests_only` in user memory; do
  not flag it as dead.)
- `if False:` blocks, unreachable `return` paths, branches whose
  guard can never be true.
- Imports that were necessary for a removed code path and are now unused.

**Verify before filing.** `grep -rn "symbol_name" src/ tests/ scripts/
config/`. If the only references are the definition and one self-import,
file it. If a test exercises it, it is not dead — note the test
reference in the finding and downgrade or drop.

### C2 — Parallel old/new branches

Two implementations of the same concept coexisting in `src/`, where one
is the "current" approach and the other is a leftover from a prior shape.
Common shapes:

- Legacy + new field on a Pydantic model with both code paths reading
  both fields ("derived legacy fields" patterns).
- Two functions doing the same thing with different signatures, both
  called from different places.
- Old batched analyst vs new per-ticker analyst infrastructure surviving
  in parallel.
- Old retry/error-handling utility surviving alongside a new one.

**Verify before filing.** Identify both implementations explicitly
(file:line for each), and identify which call sites use which. If one
implementation is genuinely abandoned (no callers), that is C1 dead
code, not C2.

### C3 — Overabstraction

Indirection that buys nothing concrete. Common shapes:

- Single-implementation `Protocol` or ABC with one concrete subclass and
  no near-term plan for a second.
- Wrapper classes that only forward to another class.
- Factory functions with one product.
- Layers introduced "for future flexibility" that have not been needed.

**Exception — Rule 7 architectural seams.** The pipeline-vs-lifecycle
split (`contract-invariants.md §C-Rule 7`) and the broker / provider /
persistence interfaces are load-bearing by contract even if currently
single-implementation. Do not flag interfaces required by the contract
for backtest ⇄ live symmetry as overabstraction. When in doubt,
flag with `low` confidence and let consolidation decide.

### C4 — Contract violations

Direct deviations from `docs/contract-invariants.md`. For each finding,
cite the specific §A row, §B phase, or §C rule violated. Common shapes:

- §C-Rule 1: state writes that bypass `state_delta` (and are not covered
  by the in-tick callback carve-out or the auto-yielded delta-tracked
  callback path).
- §C-Rule 4: ParallelAgent branches sharing an `output_key`.
- §C-Rule 5: `LoopAgent` without `max_iterations` and without a sub-agent
  that escalates.
- §C-Rule 7: pipeline agents reading the persistence layer, broker, or a
  provider directly mid-tick (rather than from `state`).
- §B-Phase 2: cross-tick fields seeded with empty values rather than
  read from persistence.
- §A: a field with no documented row, or a row whose stated owner does
  not match the writer in code.

### C5 — Silent-failure attractors

Code paths that swallow errors into `is_no_data=True`, empty lists,
`neutral` verdicts, `branch_failed` warnings, or default values without
raising or surfacing the failure to the contract surface. Per
`test-policy §A.7` and the `feedback_silent_failures_loud_tests` memory,
this is the repo's recurring bug class.

Common shapes:

- `except Exception:` catching too broadly and returning a benign value.
- `return None` / `return []` on a path where the caller cannot
  distinguish "no data" from "fetch failed".
- `is_no_data=True` set on an error path that should raise.
- Logged-but-not-propagated warnings on the happy path.

**Cross-reference.** When you find one of these, check whether a test
asserts the surfacing behaviour. If not, note it — the test audit will
care.

### C6 — Config-convention violations

Per `.claude/CLAUDE.md` "Configuration Convention": values hardcoded in
`src/` that should live in `config/*.json`, or config keys not documented
in `config/README.md`. Watch for:

- Magic numbers (timeouts, retry counts, thresholds, window sizes).
- Hardcoded ticker lists, model names, prompt fragments.
- Hardcoded provider URLs, paths, or endpoints.

Skip findings for values that are genuinely structural (e.g. the literal
key strings of a Pydantic model field) — those are not config.

### C7 — Doc/code drift

Comments, docstrings, and inline references whose claims no longer match
the code. Includes:

- `contract-invariants.md` §A rows or note text that references a class
  or method that has moved, been renamed, or been removed.
- File-header comments describing an architecture the file no longer
  has.
- Outdated cross-references between subsystems.
- TODOs that reference incidents long resolved.

When you find drift in `contract-invariants.md` itself, file the finding
with `subsystem: docs/contract-invariants` (not the code subsystem you
were auditing) so consolidation can route it correctly. Do NOT edit the
doc.

---

## §3 — Finding schema

One Markdown file per subsystem, with this exact structure. Findings
listed in **severity order** (P0 → P3). Within a severity, no required
order — group however reads best.

```markdown
# Source audit — <subsystem path>

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** <count>
**Findings:** <P0 count> P0 · <P1 count> P1 · <P2 count> P2 · <P3 count> P3

## Summary

One short paragraph (<= 4 sentences): what this subsystem does, the
top 2–3 themes from the findings, and any cross-subsystem dependencies
the consolidation pass needs to know about.

## Findings

### P0-01 · C4 contract violation · <one-line subject>

- **Location:** `src/.../file.py:NN` (and other call sites if relevant)
- **Confidence:** high
- **Description:**
  One paragraph. What is happening, why it is wrong, what rule/concern
  it violates. Cite the contract section if C4 (e.g. "§C-Rule 1").
- **Suggested action:**
  One sentence. The shape of the fix, not the diff. Examples:
  "Inline the wrapper into its single caller and delete `_helper.py`."
  "Replace the direct `ctx.session.state[k] = v` with the auto-yielded
  delta-tracked write pattern (see §C-Rule 1 sub-section)."

### P0-02 · C5 silent-failure attractor · <subject>
…

### P1-01 · C1 dead code · <subject>
…
```

**Finding ID format.** `P<severity>-<NN>` where NN is sequential within
the severity band. So `P0-01`, `P0-02`, then `P1-01`, etc.

**Be specific.** "Strategist has dead code" is not a finding. "The
`_legacy_clamp` helper at `src/agents/strategist/agent.py:412` has no
callers; the only references are its definition and a unit test at
`tests/agents/strategist/test_legacy_clamp.py` that asserts its
existence (not its behaviour)" is a finding.

**Suggested action is a sketch, not a commitment.** Consolidation may
re-shape it, and the fix-plan workstream will reconcile suggestions
that conflict across subsystems.

---

## §4 — Severity bands

Pick one severity per finding. Severity reflects the cost of leaving the
finding unaddressed, not the size of the fix.

| Band | Meaning |
|---|---|
| **P0** | Correctness bug, contract violation that can produce wrong outputs at runtime, or silent-failure attractor on a load-bearing path. Must be fixed before any further restructuring; almost certainly the source of a real bug or near-miss. |
| **P1** | Code-health hazard with no current bug evidence but high regression risk. Includes active dead branches that still execute, parallel old/new branches one bad merge from divergence, and silent-failure attractors on degraded paths. Fix in the next pass. |
| **P2** | Tidy-up that improves the codebase without urgency. Empty packages, single-implementation `Protocol`s with no contract justification, doc/code drift on internal comments, config violations on cosmetic values. Batch these into one or two cleanup PRs. |
| **P3** | Cosmetic. Comment typos, dead imports, TODO-stale-by-months. Land alongside other PRs touching the same files; do not cut dedicated PRs. |

**Confidence vs severity.** A `low`-confidence P0 is a thing — flag it,
the consolidation pass will either upgrade the confidence after looking
across subsystems, or downgrade the severity if context resolves the
concern.

---

## §5 — What to do when you finish

1. Save your report to `docs/Phase11-project-audit/source-audit/<subsystem>.md` (e.g.
   `docs/Phase11-project-audit/source-audit/agents-strategist.md`). One file per dispatched
   subsystem; the exact filename is given in your dispatch prompt.
2. Return a short summary in your final message: file path written,
   counts (`P0/P1/P2/P3`), one-sentence headline of the worst finding,
   and any cross-subsystem dependencies the consolidator needs to know.
3. Do not commit anything. Consolidation lands the audit doc tree in
   one commit at the end.

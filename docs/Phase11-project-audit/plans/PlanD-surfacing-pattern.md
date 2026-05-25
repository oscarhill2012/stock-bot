# Plan D — Surfacing pattern

**Phase:** 11 (project audit, remediation cycle)
**Wave alignment:** test/source-fix wave 4 (parallel-with-internal-dependency)
**Depends on:** Plan C fully merged into `main`.
**Specs owned:**

- `docs/Phase11-project-audit/fix-plan/T-F01-surfacing-primitive-and-inverts.md`
  (split into T-F01a + T-F01b per the spec's own recommendation)
- `docs/Phase11-project-audit/fix-plan/T-F02-missing-surfacing-tests.md`
- `docs/Phase11-project-audit/fix-plan/T-F12-completion-only-rewrites.md`


## 1. Plan overview

Plan D is the **surfacing pattern** phase of the Phase 11 remediation
cycle and the deepest behaviour change in it. The source-audit catalogued
a single recurring bug class — Theme 1, *silent-failure attractors on
load-bearing paths* — across roughly nine sites where the production
code swallows an upstream failure into a defensible-looking neutral
payload (`is_no_data=True`, `branch_failed`, `except Exception:
continue`, `spy_price = 0.0`, bare `return`). The test-audit then
identified Theme A — *tests that codify the silent-failure attractor as
correct behaviour* — at nine matching sites. Plan D delivers a shared
`surface_failure()` primitive, inverts the silent-failure sites to
either raise or emit a `branch_failed` warning with a bounded degraded
payload, and rewrites the tests that defended the swallow so they
assert the new surfacing contract.

The defining principle of this plan, taken from the user-global memory
`feedback_silent_failures_loud_tests`, is **prefer raises over
null/empty/neutral, assert positive signals not just completion**. The
audit treated the nine defending tests as part of the bug — pinning a
silent degradation in place is functionally the same as introducing it.

The backtest output **will** change once Plan D lands. That is the
point of the plan, not a side-effect to be feared. Several analyst
branches that previously returned `is_no_data=True` on a fetch
exception will now raise; some strategist ticks that previously traded
through degraded evidence will now produce empty `final_orders`; the
equity curve and the trade ledger will diverge from the post-Plan-C
baseline. The verification approach is therefore unusual: a clean
`pytest` run is necessary but not sufficient. Every diff line in the
backtest artefacts produced after a Plan D PR must be traceable to a
specific silent-failure-attractor finding from the audit. If a line
changes that nobody can attribute, the PR halts for investigation.


## 2. The surfacing principle

> **Prefer raises over null/empty/neutral. Assert positive signals,
> not just completion.**
> — `feedback_silent_failures_loud_tests`, user memory

The audit's framing of Theme A in
`docs/Phase11-project-audit/test-audit/SUMMARY.md` is unambiguous: the
test suite encodes the silent-failure attractor pattern as correct
behaviour in at least nine distinct places. Source-audit Theme 1 is
mirrored exactly by Theme A; every source-side fix in F4 lands inside
a regression test that currently asserts the broken behaviour is
desired. The test rewrites are co-required, not optional — a source
surfacing change without the paired test rewrite either fails CI
against the defending test, or (worse) ships green because the test
was deleted instead of rewritten and the regression net dies with the
old shape.

The shared primitive (`emit_feature_warning` and `emit_branch_failed`,
or whatever module-internal name T-F01a settles on; the rest of this
plan calls it the *surfacing primitive*) gives every inverted site one
canonical way to surface a failure. Two dispositions exist:

1. **Raise** — the failure is a contract violation; the tick must not
   complete with a degraded payload. Snapshotter SPY-fetch, executor
   BUY-without-matching-stance, executor fill-price dead-key, RiskGate
   falsy `strategist_decision`, RiskGate closing-without-`close_reason`.

2. **Warn + bounded degraded payload** — the failure is genuinely
   recoverable but must be visible. EDGAR `except Exception: continue`
   row drops, Finnhub social-sentiment 403 path, observability
   `_trace_maybe` swallow, `terminal_log` `usage_metadata` swallow,
   per-ticker fetch-callback exception swallows, fundamental/news
   per-provider fetch swallows.

Each disposition is fixed in the spec; the subagent does not get to
re-decide it. The test for every inverted site asserts the chosen
disposition positively (`pytest.raises(...)` for the raise paths,
`caplog` records carrying `branch_failed=True` for the warn paths) —
never "the function returned" or "the function did not crash".


## 3. PRs included

| T-F id | Title | Branch | Sub-wave | Diff size | Source findings closed | Test findings closed | Depends on |
|---|---|---|---|---|---|---|---|
| **T-F01a** | Surfacing primitive (+ pilot at `_trace_maybe`) | `fix/T-F01a-surfacing-primitive` | D1 | small / medium | `observability.md` P1-02 (pilot only) | `observability.md` P0-01 (pilot only) | Plan C merged |
| **T-F01b** | Silent-failure inverts — batches A–D | `fix/T-F01b-silent-failure-inverts` | D2 | large | F4 sweep: `agents-analysts-llm.md` P1-01/P1-02, `agents-analysts-deterministic.md` P1-04, `agents-misc.md` P0-01 + P1-03, `data-providers.md` P1-01/P1-02/P1-03/P1-04, `agents-executor.md` P1-02/P1-03, `observability.md` P1-02 (terminal_log half) | `agents-misc.md` P0-01, `analysts-llm.md` P0-01 + P0-02, `analysts-deterministic.md` P0-03, `data-providers.md` P0-01 + P0-02 + P0-03, `executor.md` P0-01 + P0-02, `agents-misc.md` P1-03 | T-F01a |
| **T-F02** | Missing surfacing tests | `fix/T-F02-missing-surfacing-tests` | D2 | small | `backtest.md` P0-01 (source-side rename `as_of_date` → `filed_at`); `orchestrator.md` P0-02 (source-side except-narrow) | `backtest.md` P0-01 (new firing test), `orchestrator.md` P0-01 (3 scenarios), `orchestrator.md` P0-04 (caplog + event-count guard), `broker.md` P1-01 (new file pinning T-F04 contract) | T-F01a, Plan B's T-F04 (broker shape choice) |
| **T-F12** | Completion-only assertion rewrites | `fix/T-F12-completion-only-rewrites` | D2 | small / medium | `risk-gate.md` source change at `agent.py:45-47` (paired source+test) | `risk-gate.md` P0-01 + P0-02, `lifecycle.md` P0-03 + P0-04 (contingent), `executor.md` P0-03 + P3-01 (contingent), `backtest.md` P1-02 | T-F01a, T-F03, T-F06 |

Counts: T-F01b closes the largest cluster (9 source-side P0/P1 findings,
9 test-side P0/P1 findings). Across Plan D as a whole: **~12 source-side
findings** and **~16 test-side findings** are closed. The exact totals
depend on the contingent strikes T-F12 performs against T-F03 / T-F06.


## 4. Sequencing

### Plan C dependency

Plan D does not dispatch until Plan C is fully merged into `main`. Plan
C's deliverables (the strategist v2 smoke strengthening, the dual
`PositionThesis` drop, and the `"positions"` → `user:positions` rewrite)
either touch files Plan D also touches (executor source + tests) or
remove parallel branches that would otherwise force Plan D to fork its
inverts twice. Dispatching Plan D against a pre-Plan-C `main` would
produce avoidable merge conflicts and re-open issues Plan C resolved.

### D1 → D2 dependency

T-F01a must merge before T-F01b dispatches. The inverts in T-F01b each
import the primitive that T-F01a creates; a parallel dispatch would
have T-F01b's subagent inventing a stub primitive or vendoring a copy.
T-F01a is intentionally small (the primitive itself + paired pilot at
`src/observability/trace.py:168-174` and
`tests/unit/test_trace_writer_exception_logging.py:33-66`) so the merge
gate is short.

T-F02 and T-F12 also depend on T-F01a — every test they add asserts on
the primitive's `branch_failed=True` log marker. They do **not** depend
on T-F01b; their assertions sit against the primitive itself, not
against any of the inverted sites Batch A–D touches.

### D2 parallelism

Within D2, T-F01b, T-F02, and T-F12 are non-overlapping and dispatch
in parallel:

- T-F01b touches `src/agents/{analysts/fundamental,analysts/news,analysts/technical,analysts/social,snapshot,executor,memory}/`,
  `src/observability/terminal_log.py`, `src/data/providers/{edgar,finnhub}.py`,
  and the paired test files under `tests/unit/agents/…` and
  `tests/integration/test_snapshotter.py` / `…executor_with_fake_broker.py`.

- T-F02 touches `src/backtest/audit/{telemetry,upstream_verifier}.py`,
  `src/orchestrator/tick.py:260-270`, and the new test files under
  `tests/backtest/audit/`, `tests/unit/orchestrator/`,
  `tests/unit/broker/`, and the integration suite extension.

- T-F12 touches `src/agents/risk_gate/agent.py:45-47` (paired
  source+test for the falsy `strategist_decision` raise) plus four
  test-only strengthenings.

No two of these specs co-edit a file. Concurrent dispatch is safe.

### Sequencing diagram

```
Plan C merged
      │
      ▼
   T-F01a  ── primitive + pilot (D1, serial)
      │
      ├──► T-F01b   ── batches A → B → C → D (D2, parallel)
      ├──► T-F02    ── 4 sub-changes (D2, parallel)
      └──► T-F12    ── risk-gate pair + 4 strengthenings (D2, parallel)
```


## 5. Inventory of silent-failure sites to invert

T-F01b batches the application sites by source-audit subsystem so a
`git log --oneline` reads cleanly. The four batches and the sites in
each, every site anchored to a finding ID:

### Batch A — snapshotter + observability + memory writer

| # | Site | Disposition | Finding ID (source) | Finding ID (test) |
|---|---|---|---|---|
| A1 | `src/agents/snapshot/agent.py:60-74` (SPY-fetch swallow → `spy_price = 0.0`) | **Raise** | `agents-misc.md` P0-01 | `agents-misc.md` P0-01 (`tests/integration/test_snapshotter.py:47-83`) |
| A2 | `src/observability/terminal_log.py:403-410` (`usage_metadata` bare `pass`) | **Warn** | `observability.md` P1-02 | (no dedicated defending test; the inversion adds a new caplog assertion) |
| A3 | `src/agents/memory/writer.py` (`decision.get("decision_tag", "unknown")` fallback) | **Warn** (retain literal-string fallback for now; T-F05 owns deletion) | `agents-misc.md` P1-03 | `agents-misc.md` P1-03 (`tests/unit/agents/memory/test_writer.py`) |

Note: the `_trace_maybe` site at `src/observability/trace.py:168-174`
is the **T-F01a pilot** and lands in D1, not in Batch A.

### Batch B — data providers (EDGAR + Finnhub)

| # | Site | Disposition | Finding ID (source) | Finding ID (test) |
|---|---|---|---|---|
| B1 | `src/data/providers/edgar.py:288` (filings `except Exception: continue`) | **Warn** | `data-providers.md` P1-01 | `data-providers.md` P0-01 (new assertion in `tests/unit/data/providers/test_edgar_filings.py`) |
| B2 | `src/data/providers/edgar.py:334` (insider-trades `except Exception: continue`) | **Warn** | `data-providers.md` P1-02 | `data-providers.md` P0-02 (new assertion in `tests/unit/data/providers/test_edgar_insider_trades.py`) |
| B3 | `src/data/providers/edgar.py:636-637` (notable-holders block) | **Warn** | `data-providers.md` P1-03 | (no dedicated defending test; the `notable_holders` *firing* test is owned by T-F02 via the `filed_at` rename) |
| B4 | `src/data/providers/edgar.py:698` (residual EDGAR swallow noted in spec) | **Warn** | `data-providers.md` P1-04 | (no dedicated defending test) |
| B5 | `src/data/providers/finnhub.py:79-86` (Finnhub social-sentiment swallow on 403) | **Warn** | `data-providers.md` P1-04 (per T-F01 spec line 76) | `data-providers.md` P0-03 (`tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py` — strengthen near-empty `assert result is not None`) |

### Batch C — analysts (fundamental + news + technical + social)

| # | Site | Disposition | Finding ID (source) | Finding ID (test) |
|---|---|---|---|---|
| C1 | `src/agents/analysts/fundamental/fetch_agent.py:118-163` (three try/except blocks around `get_company_ratios`, `get_company_filings`, `get_insider_trades`) | **Warn** per failing provider; keep partial-payload degradation | `agents-analysts-llm.md` P1-01 | `analysts-llm.md` P0-02 (`tests/unit/.../fundamental/test_fetch_degrades_on_provider_error.py`) |
| C2 | `src/agents/analysts/news/fetch_agent.py:80-86` (try/except around `get_stock_news`) | **Warn** | `agents-analysts-llm.md` P1-02 | `analysts-llm.md` P0-01 (`tests/unit/.../news/test_fetch_degrades_on_provider_error.py`) |
| C3 | `src/agents/analysts/technical/fetch_callbacks.py` (per-ticker exception swallows in fetch callback chain) | **Warn** | `agents-analysts-deterministic.md` P1-04 (technical half) | `analysts-deterministic.md` P0-03 (technical share of `tests/unit/test_social_fetch.py:117-136`-shaped tests) |
| C4 | `src/agents/analysts/social/fetch_callbacks.py` (equivalent per-ticker swallow) | **Warn** | `agents-analysts-deterministic.md` P1-04 (social half) | `analysts-deterministic.md` P0-03 (`tests/unit/test_social_fetch.py:117-136`) |

### Batch D — executor

| # | Site | Disposition | Finding ID (source) | Finding ID (test) |
|---|---|---|---|---|
| D1 | `src/agents/executor/agent.py:124-179` (BUY-without-matching-stance silent path) | **Raise** (contract violation: an order without an open-intent stance must not reach the broker) | `agents-executor.md` P1-02 | `executor.md` P0-01 (`tests/integration/test_executor_with_fake_broker.py`, the BUY-without-matching-stance case) |
| D2 | `src/agents/executor/agent.py:379-390` (fill-price OR-chain dead-key fallback) | **Raise** on missing key (tighten the lookup) | `agents-executor.md` P1-03 | `executor.md` P0-02 (`tests/integration/test_executor_with_fake_broker.py`, fill-price test) |

### Site count

- **Pilot (T-F01a, D1):** 1 site (`_trace_maybe`).
- **Batch A:** 3 sites.
- **Batch B:** 5 sites (four EDGAR + one Finnhub).
- **Batch C:** 4 sites across four agents.
- **Batch D:** 2 sites in one file.

**Total: 15 named application sites across T-F01a + T-F01b.**

That is a higher count than the audit summary's "roughly nine sites"
framing because the T-F01 spec enumerates EDGAR by individual line
number (four sites) and counts fundamental's three try/except blocks
separately from news's one. Conceptually it remains nine subsystems
(snapshotter, observability trace, observability terminal_log, memory
writer, EDGAR, Finnhub, fundamental analyst, news analyst, technical
analyst, social analyst, executor) — close enough to the audit's "~9"
that the framing holds.

### Sites the spec mentions but does not precisely locate

- The fundamental analyst's three try/except blocks at
  `fetch_agent.py:118-163` are cited as a range; the subagent must read
  the file to identify the three discrete blocks. T-F01b's Batch C
  carries the responsibility for resolving the line numbers at
  implementation time.

- The technical / social per-ticker fetch-callback swallows are cited
  only by file path (`fetch_callbacks.py` in each subtree) and a
  forward reference to "the line numbers source-audit
  `agents-analysts-deterministic.md` P1-04 cites". The subagent must
  cross-read `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md`
  P1-04 for the exact lines.

- Batch B includes `edgar.py:698` (a fourth EDGAR site) per the T-F01
  spec's `In scope` enumeration; it is anchored to `data-providers.md`
  P1-04 but the spec does not gloss the surrounding context.


## 6. Pre-flight — snapshot the *current* backtest output

Before Plan D's first PR dispatches, capture the post-Plan-C backtest
output as the reference baseline for "what changed in Plan D". The
existing Plan A baseline is unsuitable because Plan B and Plan C have
already shifted the equity curve and trade ledger in ways unrelated
to surfacing — diffing Plan D against Plan A would mix three plans'
changes into one review.

### Procedure

1. Confirm Plan C is fully merged into `main` and the working tree is
   clean.

2. Run the canonical smoke window:

   ```
   PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
   ```

   then the full window referenced by Plan A / B / C if they used a
   longer one — match whatever Plan A's snapshot covered, so the
   diffs are like-for-like.

3. Copy the resulting artefact tree (equity curve, metrics.md,
   decision-logger JSON snapshots, traces) into
   `docs/Phase11-project-audit/baseline/post-plan-c/`. Preserve the
   directory layout the backtest runner produced; do not flatten.

4. Add a one-line `README.md` inside `post-plan-c/` recording the
   `git rev-parse HEAD` commit hash and the date the snapshot was
   taken. This is the only Plan-D artefact under `baseline/` that
   prose, not data.

5. Commit `post-plan-c/` as a single commit *before* dispatching any
   Plan D PR. Plan D's per-PR verification diffs against this commit's
   tree.

### Why this matters

Every PR in Plan D potentially changes the backtest output. Without a
fixed reference taken at the right git revision, "the equity curve
changed" is ambiguous between "Plan D did it" and "Plan B/C did it and
nobody noticed". The post-Plan-C snapshot collapses that ambiguity.


## 7. Per-PR verification

Each Plan D PR satisfies the standard Phase 11 acceptance gate (full
`pytest tests/` green, `ruff check src/` clean, every finding cited in
the commit body). Plan D adds two further gates:

### Gate 1 — positive-assertion check on every new test

Every test introduced or modified by a Plan D PR must assert positively
on the surfacing contract. The audit explicitly flagged
completion-only tests as the anti-pattern this plan exists to fix; a
new test that merely calls the inverted code and asserts "no exception
raised" or `result is not None` reproduces the bug class one layer up.

Concretely:

- For raise-disposition sites (snapshotter SPY, executor BUY-without-
  stance, executor fill-price dead-key, RiskGate falsy decision,
  RiskGate closing-without-close_reason): the test asserts
  `pytest.raises(<specific exception>)` and matches at minimum the
  message prefix.

- For warn-disposition sites (Batches A–C minus snapshotter; T-F02's
  `notable_holders` test; T-F02's broker `get_portfolio` test if T-F04
  picks warn): the test calls `caplog.set_level(logging.WARNING)` and
  asserts `any(getattr(r, "branch_failed", False) for r in
  caplog.records)` together with at least one positive-content check
  (the agent name or feature key carried in the record's structured
  fields).

The T-F01b reviewer's checklist explicitly includes: *did any new test
assert only "the function returned"? If yes, request changes.*

### Gate 2 — backtest diff fully attributable

After running the smoke window the PR's commit-message verification
block prescribes:

1. Diff the new artefact tree against
   `docs/Phase11-project-audit/baseline/post-plan-c/`.

2. For every changed file (equity curve CSV row, decision-logger JSON
   snapshot, trace event, metrics.md cell), cite the silent-failure-
   attractor finding that explains the change. The expected shape of
   the citation is `"snapshot 2025-09-12T13:30: equity drops to 0.0
   → A1 raise (agents-misc.md P0-01)"` — i.e. file, what changed,
   which finding ID caused it.

3. If a line changed that nobody can attribute to a finding, **halt
   the PR**. Either the inversion is wider in effect than the spec
   anticipated (in which case escalate to the user before merging) or
   an unrelated regression slipped in (in which case investigate).

Both gates apply uniformly to T-F01a, T-F01b, T-F02, and T-F12.

### Per-PR notes

- **T-F01a:** the primitive itself ships with its own unit tests under
  `tests/unit/agents/_common/test_surfacing.py` (per the spec); the
  pilot inversion at `_trace_maybe` ships with its rewritten test.
  The backtest diff for T-F01a is expected to be **empty** — the
  pilot site is on the trace-writer error path, which the canonical
  smoke window does not normally exercise. An empty diff is the
  correct outcome and should be recorded as such.

- **T-F01b:** the largest expected backtest diff in the plan. The
  Snapshotter SPY raise (A1) alone is enough to halt the smoke window
  if `baseline-2025-09` has empty SPY rows in the cache; the spec's
  own risk section names this. The Batch order (A → B → C → D) means
  Batch A surfaces this risk first; if Batch A breaks the smoke, halt
  before fanning out the remaining batches and discuss with the user.

- **T-F02:** the `notable_holders` rename is the only change with a
  predictable backtest effect (the leak detector starts firing where
  it previously didn't). The `run_once` except-narrow is on the
  orchestrator's outer error path; canonical smoke window should not
  exercise it. The end-to-end caplog guard runs at test time only;
  no backtest effect.

- **T-F12:** the RiskGate raise on falsy `strategist_decision` will
  fire if the smoke window has any tick where the strategist branch
  legitimately doesn't run yet (cold-start). The spec calls this
  risk out and prescribes fallback to a `branch_failed` + empty-
  orders disposition if the smoke fails. The four test-only
  strengthenings produce no backtest diff.


## 8. Subagent dispatch protocol

### Per-PR dispatch shape

Each PR is dispatched to its own subagent in its own worktree, per the
Phase 11 fix-plan README. The subagent receives:

- The spec path:
  - T-F01a / T-F01b: `docs/Phase11-project-audit/fix-plan/T-F01-surfacing-primitive-and-inverts.md`
  - T-F02: `docs/Phase11-project-audit/fix-plan/T-F02-missing-surfacing-tests.md`
  - T-F12: `docs/Phase11-project-audit/fix-plan/T-F12-completion-only-rewrites.md`
- Context paths: the relevant `source-audit/*.md` and `test-audit/*.md`
  reports the spec cites, plus `docs/test-policy.md` §A.7 and §E,
  plus the user memory `feedback_silent_failures_loud_tests`.
- The post-Plan-C baseline path
  (`docs/Phase11-project-audit/baseline/post-plan-c/`) for diffing.

### Branch naming

- T-F01a: `fix/T-F01a-surfacing-primitive`
- T-F01b: `fix/T-F01b-silent-failure-inverts`
- T-F02: `fix/T-F02-missing-surfacing-tests`
- T-F12: `fix/T-F12-completion-only-rewrites`

T-F01a and T-F01b each get their own branch — they are dispatched as
separate PRs with a serial merge dependency. The T-F01 spec's branch
field lists both shapes; Plan D commits to the split.

### Autonomy envelope

The subagent edits source + tests, runs the full pytest suite + ruff,
runs the canonical backtest smoke, commits on the feature branch,
pushes to `origin`, and opens the PR. The user reviews the diff and
the backtest-diff attribution before merging.

### Failure handling

- **No `--no-verify`.** Pre-commit hook failures are investigated and
  fixed in a new commit; the offending commit is not re-pushed with
  hooks skipped.
- **No `--amend` on pushed commits.** If a fix is needed after the PR
  is open, a fresh commit lands on top.
- **No force-push to `main`.** Standard project rule.
- **Halt on un-attributable backtest diff.** Per Gate 2 above — the
  subagent stops and reports to the user rather than guessing.


## 9. Acceptance criteria for Plan D as a whole

- [ ] Plan C is merged to `main` and the post-Plan-C baseline is
      committed at `docs/Phase11-project-audit/baseline/post-plan-c/`.

- [ ] **T-F01a** is merged: the surfacing primitive exists at the
      agreed path (`src/agents/_common/surfacing.py` or the
      contract-side alternative); its own unit tests are green; the
      `_trace_maybe` pilot inversion + its rewritten test are merged.

- [ ] **T-F01b** is merged: every silent-failure site enumerated in §5
      (Batches A–D) now either raises or emits a `branch_failed`
      warning via the primitive; every paired defending test now
      asserts the new contract positively (raise or `caplog`).

- [ ] **T-F02** is merged: the four missing surfacing tests
      (`notable_holders` firing, `run_once` 3-scenario, broker
      `get_portfolio` warn/raise, end-to-end caplog + event-count
      guard) exist and pass; the `as_of_date` → `filed_at` source
      rename and the `run_once` except-narrow are merged.

- [ ] **T-F12** is merged: the RiskGate falsy-decision raise +
      paired tests are merged; the four test-only strengthenings
      (lifecycle CLI ×2, executor idempotency, backtest driver one-
      tick) are merged or explicitly struck as redundant against
      T-F03 / T-F06 (with the strike recorded in the T-F12 commit
      body).

- [ ] **Full `pytest tests/`** is green on `main` after every Plan D
      merge.

- [ ] **`ruff check src/`** is clean after every Plan D merge.

- [ ] **Re-grep audit:** the remaining `except Exception:` + `continue`
      / `pass` pairs in `src/` are intentional and each carries a
      per-site comment naming why surfacing was rejected. (Per T-F01b's
      acceptance criterion.)

- [ ] **Backtest diff against post-Plan-C:** every changed line in the
      smoke-window artefacts is attributable to a specific silent-
      failure-attractor finding, recorded in the merge-time commit
      body or PR description.

- [ ] `graphify-out/graph_delta.md` carries entries for the new
      `_common/surfacing` module, the new test files (T-F02 ×3,
      T-F12 ×1), and any test files renamed during the inversion.


## 10. Risks and rollbacks

### Risk: a surfacing change cascades into a real backtest failure

This is the central, expected risk. A site that previously returned a
neutral payload now raises; the strategist that previously traded
through degraded evidence now blocks; the executor that previously
silently dropped an order now refuses to submit it. The equity curve
diverges. The trade ledger shrinks. The diff against the post-Plan-C
baseline is large.

**This is the intended shape of the change.** The plan's premise is
that the previous outputs were *wrong*, produced by code that was
silently degrading on failure. The new outputs are *correct* in the
sense that they no longer mask upstream failures behind plausible-
looking neutral data.

Mitigation is human review, not code: each Plan D PR's backtest diff
goes to the user for explicit sign-off before merge. The attribution
requirement (Gate 2, §7) makes the review tractable — the user is not
asked "is this diff acceptable?" in the abstract but "is the
attribution chain for every change line believable?".

### Risk: a surfacing change breaks the smoke window outright

Most acute for the Snapshotter SPY raise (A1) and the RiskGate falsy-
decision raise (T-F12). If the canonical window's cache has empty SPY
rows, or if any tick legitimately runs before the strategist branch
wires in, the smoke fails completely (not just diverges).

Mitigation per spec:

- T-F01a / T-F01b: confirm SPY price coverage in the cache before
  flipping; if absent, T-F08 (data-domain pull) or a cache backfill
  must land first. If T-F01b discovers this only at smoke-run time,
  halt the PR and escalate.

- T-F12: empirical check via the smoke command before committing the
  RiskGate raise; if the smoke fails, switch the disposition to
  `branch_failed` + empty-orders yield and rewrite the two new
  tests to assert the warn shape instead.

### Risk: a test rewrite over-tightens the contract

A test that asserts `pytest.raises(StrategistContractViolation,
match="exact long message")` is brittle if the exception message is
refactored. Each new test in Plan D uses minimal-but-specific message
matching (prefix, not full string) to keep the regression-guard
strong without coupling to message wording.

### Rollback

Per-PR: revert the feature branch's commits via a fresh revert PR.
The batched-commit structure inside T-F01b means a single batch can
be reverted without losing the primitive itself.

Whole-plan: revert in dependency order (T-F12 → T-F02 → T-F01b →
T-F01a). The post-Plan-C baseline directory should be retained even
if Plan D is rolled back, as a historical record.


## 11. Open questions and explicit deferrals

### Silent-failure sites Plan D does **not** fix

- **Strategist `tick_id="unknown"` fallback** (`agents-strategist.md`
  P1-04). Owned by Plan C / T-F05. Plan D does not touch it.

- **Strategist `decision_writer` silent no-op** (`agents-strategist.md`
  P2-05). Owned by Plan C / T-F05.

- **SmartMoney silent-failure sites.** Deleted whole by T-F07 in an
  earlier wave; Plan D does not see them.

- **The four wired-but-unused data domains** (`earnings`,
  `analyst_consensus`, `short_interest`, `options`) — pulled by T-F08
  in an earlier wave.

### Defended-bug tests outside Plan D's PRs

- **`_build_initial_state` empty-seed contract violation**
  (`orchestrator.md` test P0-03). Spec C / Phase 2 hydration is
  deferred this cycle per the fix-plan README Decision 6. The test
  stays as it is; Plan D does **not** add the cross-tick survival
  test.

- **`_dispatch_app_name` broker-mode-routing test**
  (`orchestrator.md` test P1-04). Owned by T-F04 (live-only bombs),
  which lands in an earlier wave.

### Theme A debt that survives Plan D

After Plan D merges, the following defended-swallow shapes are still
*possible* in the codebase but have not been individually catalogued
as audit findings:

- Any new `except Exception: continue` / `pass` introduced after the
  audit snapshot. T-F01b's re-grep acceptance criterion catches the
  ones present today; future regressions are caught only if a reviewer
  spots them.

- The `MemoryWriter.decision_tag` literal-string itself stays in the
  codebase after Plan D — T-F05 (in Plan C) may drop the field
  entirely after dropping the dual `PositionThesis`. If so, the
  surfacing call Plan D adds in Batch A3 also goes; coordinate at
  merge time.

### Decisions deferred to user at Plan D dispatch time

1. **Primitive location.** T-F01 spec offers two homes:
   `src/agents/_common/surfacing.py` or `src/contract/surfacing.py`.
   The plan does not pre-commit to one; the T-F01a subagent picks at
   implementation time and records the rationale in the commit body.
   The user may override on PR review.

2. **Broker `get_portfolio` disposition** (raise vs warn). Decided by
   T-F04 in an earlier wave; T-F02's test in Plan D mirrors whatever
   T-F04 chose. If T-F04 has not landed by Plan D's dispatch time,
   T-F02 dispatch is held.

3. **T-F12 strikes against T-F03 / T-F06.** The lifecycle CLI ×2 and
   executor idempotency strengthenings may already be covered by
   T-F03 / T-F06 work. The T-F12 subagent reads those sibling specs
   at implementation time and records the strike decision in the
   commit body.

# Plan C — Live-only correctness

**Date:** 2026-05-25
**Phase:** Phase 11 — project audit remediation
**Wave:** 3 (correctness fixes, non-overlapping subsystems)
**Depends on:** Plan B fully merged to `main`
**Status:** drafted, awaiting dispatch


## 1. Plan overview

Plan C is the **live-only correctness** stage of the Phase 11 remediation
cycle. The bot has a small cluster of latent bugs that simply do not fire
under the existing test surface: the backtest harness uses `FakeBroker`,
synthetic ADK session state, and a tighter coercion path on the boundary
between Python and the ADK persistence layer. The first time we point
the bot at the Trading 212 paper account — or at a real ADK
`DatabaseSessionService` against a long-lived database — each of these
bombs detonates on tick one. We are pre-deployment today, and that is the
*right* moment to defuse them. Plan C ships two PRs (T-F03 and T-F04)
that close the matching `lifecycle.md`, `broker.md`, `agents-misc.md`
and `orchestrator.md` audit findings, paired with the test rewrites that
codify the new behaviour as the spec.

Unlike Plans A and B, **Plan C deliberately changes behaviour**. Today,
several of the paths under audit silently no-op, swallow exceptions, or
return partial data. After Plan C they raise, log loudly, or coerce
correctly. The backtest output should be unaffected (these are live-only
bugs by design — see §4 for the per-site argument), but if any output
*does* drift, every changed line must be explainable from the
behaviour-change inventory in §4 and confirmed under the per-PR
verification protocol in §6. Drift without explanation is a regression.


## 2. PRs included

| T-F id | Title                                | Branch                                | Diff size | Source-audit findings closed | Test-audit findings closed |
|--------|--------------------------------------|---------------------------------------|-----------|------------------------------|----------------------------|
| T-F03  | Lifecycle ADK-tables coverage        | `fix/T-F03-lifecycle-adk-tables`      | medium    | `lifecycle.md` P0-01, P0-02  | `lifecycle.md` P0-01, P0-02, P0-03, P0-04 |
| T-F04  | Live-only correctness bombs          | `fix/T-F04-live-only-bombs`           | medium    | `broker.md` P1-01, P1-02; `agents-misc.md` source P0-01; `orchestrator.md` source P0-03 | `broker.md` P0-01, P1-01; `agents-misc.md` test P0-01, P0-03, P0-04; `orchestrator.md` test P0-02 |

T-F03 closes **6 findings** (2 source + 4 test). T-F04 closes
**10 findings** (4 source + 6 test). The two PRs touch disjoint
subsystems — lifecycle vs broker + snapshotter + orchestrator state
seed — and are safe to dispatch in parallel.


## 3. Sequencing

Plan B must be merged before Plan C dispatches. Spec B is the source of
the canonical Spec-B clarification (cross-tick `user:positions` /
`user:thesis` live in ADK `DatabaseSessionService` tables). T-F03 makes
the lifecycle pre-flight and hard-reset paths agree with that
clarification; if Plan B has not landed, the ADK-table extension would
race a moving target.

Within Plan C itself the two PRs are independent and dispatch in
parallel:

```
main (post-Plan B)
   │
   ├── fix/T-F03-lifecycle-adk-tables   (subagent A)
   │
   └── fix/T-F04-live-only-bombs        (subagent B)
```

Both PRs merge directly to `main` once reviewed. No internal sequencing
between them; no shared files. T-F04 has a soft dependency on T-F10
(the layout sweep) for the `tests/unit/broker/` and
`tests/unit/orchestrator/` mirror directories — verified to exist post
Wave 1 — but Plan C does not block on T-F10 because both new test files
in T-F04 are *additions*, not moves.


## 4. Behaviour-change inventory

This section enumerates, per PR, the specific user-visible behaviour
transitions Plan C introduces. Each entry is the contract against which
the verification step in §6 measures backtest drift. If a line of
backtest output changes and that change cannot be traced to one of the
entries below, treat it as a regression and stop.

### 4.1 T-F03 — lifecycle ADK-tables coverage

#### Behaviour change 1 — `_check_live_tables_empty` now refuses stale ADK session state

- **Before:** `src/lifecycle/initialise.py:_check_live_tables_empty`
  counts rows in only three legacy ORM tables (`buffer_entries`,
  `trade_log`, `portfolio_snapshots`). A "fresh boot" succeeds against
  a database whose ADK `user_states` / `sessions` / `events` /
  `app_states` tables still hold the prior run's `user:positions` /
  `user:thesis` book. The new session silently hydrates the stale
  thesis state at Phase 2.
- **After:** the pre-flight discovers the four ADK table names via
  `DatabaseSessionService`'s SQLAlchemy metadata (no hardcoded literal
  list) and raises `NonEmptyTablesError` listing every non-empty table
  — distinguishing "legacy" from "ADK session" in the message so the
  user knows whether `scripts.hard_reset` is the right escape hatch.
- **Source-audit ref:** `lifecycle.md` source P0-01
  (`src/lifecycle/initialise.py:21,74-89,146`).
- **Live vs backtest:** live-only. The backtest harness builds its own
  ephemeral session per window and does not interact with
  `_check_live_tables_empty`.

#### Behaviour change 2 — `hard_reset` now truncates and archives ADK session tables

- **Before:** `src/lifecycle/hard_reset.py:_truncate_live` deletes only
  the three legacy tables; `_archive_postgres` only `CREATE TABLE … AS
  SELECT` for the same three. After a hard reset the ADK
  `user_states` / `sessions` / `events` rows survive, and the next
  `initialise` → `run_once` cycle silently hydrates the prior run's
  thesis book. SQLite gets the archive incidentally via `VACUUM INTO`,
  but the truncate is still incomplete; Postgres misses both archive
  *and* truncate.
- **After:** `_truncate_live` also deletes from each ADK table
  discovered via the shared metadata helper, scoped by
  `app_name LIKE 'StockBot-%'` where the column exists (verify
  per-table; fall back to unscoped DELETE with a comment if not).
  `_archive_postgres` adds matching `CREATE TABLE … AS SELECT` calls
  for each ADK table. SQLite's `VACUUM INTO` remains unchanged — a
  one-line comment notes that it already captures the whole file.
- **Source-audit ref:** `lifecycle.md` source P0-02
  (`src/lifecycle/hard_reset.py:17,46-70,73-80`).
- **Live vs backtest:** live-only. The backtest never invokes
  `hard_reset`.

#### Behaviour change 3 — table-name discovery is metadata-driven

- **Before:** both `_check_live_tables_empty` and `hard_reset` carry a
  duplicated module-level `_STOCKBOT_TABLES` tuple of literal strings.
- **After:** the ADK table list is discovered by introspecting
  `DatabaseSessionService`'s SQLAlchemy metadata (or the underlying
  engine via `sqlalchemy.inspect(...).get_table_names()` filtered
  against the metadata). A shared helper (suggested location
  `src/lifecycle/_adk_tables.py`) is the single source of truth, so
  the two call sites cannot drift. Hardcoded ADK-table literals are
  forbidden — the helper's docstring states this explicitly.
- **Source-audit ref:** `lifecycle.md` source P0-01, P0-02 jointly.
- **Live vs backtest:** structural; visible to neither path's output
  unless the helper raises on the ADK import path (see §9 risks).

#### Behaviour change 4 — eight new sub-tests assert the new contract loudly

- **Before:** zero coverage of ADK-table emptiness or truncation;
  `test_initialise_cli.py::test_main_calls_initialise` and
  `test_hard_reset_cli.py::test_yes_flag_skips_prompt` were
  completion-only (`rc == 0` and substring `"Archived"`).
- **After:** four new ADK-empty sub-tests in `test_initialise.py`
  (one per ADK table) assert `NonEmptyTablesError` is raised with a
  message naming the offending table. Four new ADK-truncate sub-tests
  in `test_hard_reset.py` (one per ADK table) seed via the canonical
  ADK API, run `hard_reset`, and assert post-reset row count is `0`
  and the archive contains the seeded row. The two CLI happy-path
  tests gain positive-content assertions (anchor row exists, archive
  files exist, meta JSON parses).
- **Test-audit ref:** `lifecycle.md` test P0-01, P0-02, P0-03, P0-04.

### 4.2 T-F04 — live-only correctness bombs

T-F04 defuses four distinct bombs. Each bomb is enumerated below with
its before/after and a live-vs-backtest argument.

#### Bomb 1 — `Trading212Broker` awaits a synchronous `httpx.Response.json()`

- **Before:** `src/broker/trading212.py:58, 77, 92, 100` all run the
  pattern `data = await resp.json() if callable(getattr(resp, "json",
  None)) else resp.json()`. `callable(...)` is True for both sync and
  async callables, so the `await` branch is *always* taken in
  production. `httpx.Response.json` is synchronous and returns a dict;
  `await <dict>` raises `TypeError: object dict can't be used in
  'await' expression` on every `submit_market` / `position_size` /
  `get_portfolio` call.
- **After:** the conditional is dropped; the four sites call
  `data = resp.json()` directly. The unit tests in
  `tests/unit/test_trading212_request_construction.py` are reshaped to
  use real-shape sync mocks (`Mock(json=Mock(return_value={...}))`)
  with `client.post` remaining `AsyncMock` — the *method* is async,
  the *response* is not. Each happy-path test gains
  `resp.json.assert_called_once()` to lock the "called without await"
  contract.
- **Source-audit ref:** `broker.md` P1-01 (lines `:58, :77, :92, :100`);
  test-audit `broker.md` P0-01 (lines `:11-17` and `:37-41`).
- **Live vs backtest:** live-only. The backtest uses `FakeBroker`
  exclusively; `Trading212Broker` is never instantiated in any
  backtest path.

#### Bomb 2 — `Trading212Broker.get_portfolio` silently drops unknown instrument codes

- **Before:** `src/broker/trading212.py:104-113` builds the live
  portfolio with `if code not in rev: continue`. Combined with the
  caller-side wiring of `Trading212Broker(..., instrument_map={})` in
  `src/orchestrator/tick.py`, `scripts/initialise.py`, and
  `scripts/trace_tick.py` (a cross-subsystem note in the broker audit
  §Summary), the live `get_portfolio` returns cash plus an empty
  positions list. RiskGate, Strategist, and Snapshotter all proceed
  treating the bot as flat — no warning, no raise, no `is_no_data`
  signal. Zero existing test coverage.
- **After:** the silent `continue` becomes a loud surfacing path. The
  recommended (and spec-default) shape is to raise `BrokerRejection`
  (or a dedicated `UnknownInstrumentError`) naming the offending code
  and pointing at the incomplete `instrument_map`. The fallback
  warning shape (`logger.warning(..., kind="unknown_instrument")` plus
  a post-call counter) is acceptable if the subagent has a stronger
  argument. Whichever shape is chosen, the new test file
  `tests/unit/broker/test_trading212_get_portfolio.py` adds two tests:
  one happy-path asserting known codes round-trip, and one
  `_raises_on_unknown_instrument_code` (or `_warns_`, matched to the
  chosen shape).
- **Source-audit ref:** `broker.md` P1-02 (lines `:104-113`);
  test-audit `broker.md` P1-01.
- **Live vs backtest:** live-only. Same `FakeBroker`-only backtest
  reasoning as Bomb 1. The cross-subsystem caller-side wiring fix
  (`instrument_map={}` in callers) is explicitly out of scope per the
  spec — the loud surfacing introduced here is the *forcing function*
  for that follow-up wiring fix.

#### Bomb 3 — Snapshotter swallows every SPY-fetch failure into `spy_price = 0.0`

- **Before:** `src/agents/snapshot/agent.py:60-74` wraps the SPY price
  fetch in `try / except Exception: spy_price = 0.0`. Any provider
  error, timeout, missing-bar condition — anything — flat-lines the
  equity curve to a `spy_price` of zero while the pipeline-completion
  check still passes. The existing integration test
  (`tests/integration/test_snapshotter.py`) actively codifies this as
  *desired* behaviour: a test named
  `test_snapshotter_accepts_iso_string_as_of` asserts the silent
  degrade to `spy_price=0.0` on `get_price_history` raising. The test
  is one of the rare cases where the test suite is the bug, not just
  blind to it.
- **After:** the swallow is dropped. Preferred shape: remove the
  `try/except` entirely and let the exception propagate to the
  driver's pipeline-completion guard at `src/backtest/driver.py:608`.
  Acceptable fallback: narrow to a specific `(ProviderError,
  asyncio.TimeoutError)` set, set `spy_price = None`, and have
  `save_portfolio_snapshot` reject `None` loudly. The invariant: no
  code path produces `spy_price = 0.0`. The defending test is
  inverted: it now asserts the exception propagates (or, if the
  loud-log shape is chosen, asserts `caplog` records a
  `kind="spy_fetch_failed"` WARNING and no snapshot row is written).
  A new happy-path test pins the positive content
  (`snap["spy_price"] == 470.0`). The wall-clock leakage test mock is
  reshaped from `sys.modules["yfinance"]` injection to
  `monkeypatch.setattr("data.get_price_history", ...)` at the leaf
  seam.
- **Source-audit ref:** `agents-misc.md` source P0-01
  (lines `:60-74`); test-audit `agents-misc.md` P0-01, P0-03, P0-04.
- **Live vs backtest:** *primarily* live-only, but with a caveat the
  backtest re-run check in §6 must catch. The backtest's SPY data
  feed is cache-backed and deterministic, so under healthy conditions
  the swallow never fires and the backtest output is unaffected. If
  the cache *does* have a hole — or if a cache miss is silently
  treated as a fetch failure by the snapshotter today — flipping the
  swallow to a raise would surface that defect as a backtest
  regression. That is the exposure to confirm in §6 step 4.

#### Bomb 4 — Live `run_once` writes a raw `datetime` into `create_session(state=...)`

- **Before:** `src/orchestrator/tick.py:148` populates the initial
  state dict with `as_of` as a raw `datetime` object. Subsequent
  `create_session(state=initial_state)` at `:242-247` hands the dict
  to `DatabaseSessionService`, which JSON-serialises it for the
  `state` column. `datetime` is not JSON-serialisable; the call
  raises `TypeError: Object of type datetime is not JSON
  serializable` on every live tick. The backtest driver at
  `src/backtest/driver.py:494-499` already ISO-coerces; the live path
  was simply never given the same fix. Zero existing regression test.
  User memory `feedback_as_of_boundary_coercion` calls this out as
  mandatory: "every read of `state["as_of"]` uses `resolve_as_of`,
  every datetime write to state ISO-stringifies first".
- **After:** the `as_of` datetime is ISO-coerced via the existing
  `resolve_as_of` helper before it enters `_build_initial_state`'s
  output. Strongly preferred: extract a shared
  `_seed_state_for_adk(state)` helper (suggested location
  `src/orchestrator/persistence.py` or a new
  `src/orchestrator/_state_coercion.py`) and call it from both
  `tick.py:148` *and* `backtest/driver.py:494-499` so the two
  lifecycles share one coercion path. The new test file
  `tests/unit/orchestrator/test_tick_initial_state_json_safe.py`
  provides two regression tests: a `json.dumps(...)` round-trip and
  a real `DatabaseSessionService` `create_session` round-trip against
  an in-memory SQLite URL.
- **Source-audit ref:** `orchestrator.md` source P0-03 (line `:148`);
  test-audit `orchestrator.md` P0-02.
- **Live vs backtest:** live-only. The backtest already coerces at
  the equivalent site (`backtest/driver.py:494-499`). If the helper
  extraction unifies the two paths, the backtest re-run check in §6
  must confirm the unified helper still produces the byte-identical
  coerced state the inline version was producing.


## 5. Pre-flight — confirm Plan B baseline still holds

Before dispatching either subagent, the dispatcher re-runs the Plan B
baseline backtest and confirms byte-identical output against the
artefacts captured in `docs/Phase11-project-audit/baseline/`. This step
is shared with Plans A and B and exists to make later drift attribution
unambiguous: if the baseline already drifts before Plan C touches
anything, Plan C is not responsible for it.

Commands:

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
diff -r docs/Phase11-project-audit/baseline/ runs/baseline-2025-09/
```

The dispatcher records the result in the dispatch log. If the diff is
non-empty, Plan C is paused pending root-cause analysis — do not start
either subagent on a moving baseline.


## 6. Per-PR verification

Each subagent is responsible for running its own verification before
commit. The dispatcher repeats the same checks against the pushed branch
before merging. Both layers are required.

### 6.1 T-F03 verification

1. Full test suite green:
   ```bash
   .venv/bin/python -m pytest tests/ -v
   ```
2. Lint clean:
   ```bash
   .venv/bin/python -m ruff check src/
   ```
3. Focused lifecycle run includes the eight new sub-tests and shows
   them passing:
   ```bash
   .venv/bin/python -m pytest tests/unit/lifecycle/ -v
   ```
4. **New tests are the spec of the new behaviour.** The four
   ADK-empty sub-tests must assert `NonEmptyTablesError` is raised
   (not "no exception"). The four ADK-truncate sub-tests must assert
   `COUNT(*) == 0` post-reset *and* archive-contains-row positively
   — not just "function returned". Completion-only assertions in any
   of the eight tests are a Plan C failure.
5. Backtest re-run:
   ```bash
   PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
   diff -r docs/Phase11-project-audit/baseline/ runs/baseline-2025-09/
   ```
   **Expected diff: none.** T-F03 changes the pre-flight and hard-reset
   paths; the backtest invokes neither. If any line diffs, every
   changed line must trace back to an entry in §4.1. A drift that
   cannot be explained from §4.1 is a regression — stop and
   diagnose.
6. Graphify delta entry appended for the new helper module (if added)
   and the seven moved test files.

### 6.2 T-F04 verification

1. Full test suite green:
   ```bash
   .venv/bin/python -m pytest tests/ -v
   ```
2. Lint clean:
   ```bash
   .venv/bin/python -m ruff check src/
   ```
3. Focused live-only run includes the new and reshaped tests and shows
   them passing:
   ```bash
   .venv/bin/python -m pytest tests/unit/broker/ \
       tests/integration/test_snapshotter.py \
       tests/unit/orchestrator/test_tick_initial_state_json_safe.py -v
   ```
4. **New tests are the spec of the new behaviour.** Each of the four
   bombs must have at least one positive-assertion test:
   - Bomb 1: `resp.json.assert_called_once()` (not awaited).
   - Bomb 2: `pytest.raises(BrokerRejection, match="UNKNOWN_XX_EQ")`
     *or* `caplog` records the WARNING — whichever shape the source
     side chose. A test that asserts "function returned without
     raising" is insufficient.
   - Bomb 3: `pytest.raises(...)` on the SPY fetch failure path,
     *plus* `snap["spy_price"] == 470.0` on the happy path. The
     inverted test must assert surfacing, not the old "silent degrade
     to `0.0`" contract.
   - Bomb 4: `json.dumps(_build_initial_state(...))` succeeds, *and*
     a real `DatabaseSessionService.create_session` round-trip
     succeeds with `state["as_of"]` returning as an ISO string.
5. Backtest re-run:
   ```bash
   PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09
   diff -r docs/Phase11-project-audit/baseline/ runs/baseline-2025-09/
   ```
   **Expected diff: none.** All four bombs are live-only by the
   per-bomb argument in §4.2. The two paths most likely to surface
   a hidden backtest dependency are Bomb 3 (snapshotter SPY swallow
   — if the cache has holes today they have been silently zeroed) and
   Bomb 4 (the helper-extraction unification — if `_seed_state_for_adk`
   produces a different coerced shape from the inline driver code, the
   backtest seed state changes). If any line diffs, every changed line
   must trace back to an entry in §4.2. State this explicitly in the
   PR description: "backtest output is byte-identical" or "backtest
   output diff explained by §4.2 entries X, Y".
6. Graphify delta entry appended for the new files
   (`test_trading212_get_portfolio.py`,
   `test_tick_initial_state_json_safe.py`, and the optional
   `_state_coercion.py` if extracted).


## 7. Subagent dispatch protocol

The dispatch protocol mirrors Plans A and B and is repeated here for
self-contained execution. Each subagent operates in a dedicated git
worktree.

For each PR:

1. **Spec path.** The subagent is given the path to its spec file —
   `docs/Phase11-project-audit/fix-plan/T-F03-lifecycle-adk-tables.md`
   or `docs/Phase11-project-audit/fix-plan/T-F04-live-only-bombs.md` —
   and instructed to read it *and* the relevant audit reports in full
   before touching code.
2. **Branch naming.** `fix/T-F<NN>-<slug>` exactly as in the spec.
3. **Autonomy.** Edit + run tests + commit on the feature branch + push
   to `origin` + open a PR via `gh pr create`. The user reviews the
   diff before merge; the subagent does not merge.
4. **Failure handling.** If `pytest tests/` fails or `ruff check src/`
   fails, the subagent diagnoses and fixes — no `--no-verify`, no
   `--amend` on pushed commits, no force-push to `main`. A new commit
   is made for each fix. If the failure root-causes to a finding the
   spec did not anticipate, the subagent surfaces it to the dispatcher
   rather than silently expanding scope.
5. **Commit message format.** `fix(<subsystem>): <one-line subject>`
   followed by a body that cites every finding ID closed (e.g.
   `Closes lifecycle.md source P0-01, P0-02; test P0-01, P0-02, P0-03,
   P0-04.`).
6. **Shell convention.** Bash commands run from the project root
   directly — no `cd "/home/oscarhill2012/Documents/Repository/StockBot" && ...`
   prefixes. The harness is already rooted there and the compound form
   breaks the permission allowlist.
7. **Self-audit.** Before commit, the subagent walks the rubric
   checklist (`docs/Phase11-project-audit/source-audit/RUBRIC.md` and
   the test-audit equivalent) looking for new silent-failure
   attractors, mock-at-wrong-level patterns, or completion-only
   assertions introduced by its own changes. Particular focus for
   Plan C: the new metadata helper in T-F03 (C5 attractor risk on ADK
   import-path drift) and the new surfacing path in T-F04 Bomb 2
   (must not introduce a new swallow downstream).


## 8. Acceptance criteria for Plan C

Plan C is complete when **all** of the following hold:

- [ ] T-F03 and T-F04 are both merged to `main` via the standard
  PR-review process.
- [ ] All 16 findings in §2 (6 from T-F03 + 10 from T-F04) are closed
  and cited by ID in the merged commit bodies.
- [ ] All four live-only bombs in §4.2 are defused with passing
  positive-assertion tests (not completion-only).
- [ ] The eight new T-F03 sub-tests and the two new T-F04 test files
  exist, sit in their mirrored layout positions, and pass under
  `pytest tests/`.
- [ ] The shared metadata helper in T-F03 is the single source of
  truth for ADK table-name discovery — no hardcoded ADK-table
  literals remain in `src/lifecycle/`. Confirm by `grep`.
- [ ] If T-F04 chose the recommended `_seed_state_for_adk` helper
  extraction, both `src/orchestrator/tick.py:148` and
  `src/backtest/driver.py:494-499` route through it.
- [ ] Post-merge baseline backtest matches
  `docs/Phase11-project-audit/baseline/` byte-for-byte, *or* any drift
  is documented in the relevant PR description with every changed
  line traced to a §4 entry.
- [ ] Graphify deltas appended for new files / moved files in both
  PRs.
- [ ] No new audit findings introduced (subagent self-audit + dispatcher
  cross-check).


## 9. Risks and rollbacks

The main risk of Plan C is exactly the inverse of its goal: by flipping
silent-failure paths into raising paths, we may discover the silent
failure was actually being reached in the backtest harness too. If so,
the new raises break the backtest in a way the existing tests never
defended against. This is not a regression in the formal sense — it is
a previously-masked defect surfaced by the new contract — but it
*looks* like a regression in CI.

Concrete exposure points:

- **T-F03 risk — ADK import path drift.** The metadata-discovery
  helper depends on the `google-adk` package exposing its SQLAlchemy
  `Base` (or equivalent) at a stable import path. If the package's
  internal layout changes between versions, the helper breaks.
  Mitigation: the helper raises a clear error (not a silent empty
  list) on import failure, and the four ADK-empty + four ADK-truncate
  tests catch it on the next `pytest` run.
- **T-F03 risk — moving seven test files.** The `git mv` into
  `tests/unit/lifecycle/` could surface dormant import errors from
  fixtures relying on relative paths. Mitigation: run
  `pytest --collect-only` before and after the move; the collected-
  test count must be identical.
- **T-F04 Bomb 2 risk — `get_portfolio` raise breaks pre-existing
  tests that pass `instrument_map={}`.** By design — that is the
  forcing function for the cross-subsystem caller-side wiring fix.
  But if any existing test passes `instrument_map={}` and calls
  `get_portfolio`, it will need a minimal seeded `instrument_map`.
  Mitigation: `grep -rn "instrument_map" tests/` first, list affected
  tests in the PR description, patch them in-pass or escalate.
- **T-F04 Bomb 3 risk — snapshotter exception propagation cascades.**
  If the driver's pipeline-completion guard at
  `src/backtest/driver.py:608` does not actually handle the
  propagated SPY exception cleanly, multiple downstream tests fail.
  Mitigation: fall back to the loud-log + reject-on-`None` shape and
  surface via `save_portfolio_snapshot` rejection instead of raw
  propagation, if propagation proves too disruptive.
- **T-F04 Bomb 4 risk — `resolve_as_of` signature drift.** The
  helper's existing signature may not match the inline coercion at
  `driver.py:494-499`. Verify before extracting
  `_seed_state_for_adk`; if signatures cannot be reconciled, flag to
  the dispatcher and ship the live-side coercion without the helper
  extraction.

**Treatment of "surfaced bug" outcomes.** Per the audit reports, the
silent failures in scope are themselves findings. If Plan C surfaces a
backtest-side instance of one of these defects, the dispatcher records
it as a new finding rather than backing Plan C out. The Plan C PR may
still merge if the surfaced defect has a known fix path; otherwise the
dispatcher pauses Plan C and triages.

**Rollback.** Each PR is a single feature branch; rollback is `git
revert <merge-sha>` on `main`. The metadata helper in T-F03 is
additive — reverting it leaves the legacy three-table list intact and
matches pre-PR behaviour. The four source fixes in T-F04 are
independently reversible within a single revert commit.


## 10. Open questions and explicit deferrals

The following are intentionally **not** in Plan C scope. Recording them
here so the dispatcher does not absorb them into the wave and so the
next planning cycle picks them up cleanly.

- **Spec C / Phase 2 hydration is deferred this cycle.** Per
  fix-plan/README.md decision 6, the hydration of `memory_buffer` and
  `day_digest` from persistence (the canonical reading of §C-Rule 7
  for those fields) is *not* tackled here. Orchestrator source P0-01
  (empty seed of those fields) remains open after Plan C lands and is
  the lead item for Spec C / Phase 2 when that work is sequenced.
- **Orchestrator P0-02 (`BaseException` swallow) is deferred.** It
  is a large-blast change with its own missing-test gap (three
  regression tests sketched in `orchestrator.md` test P0-01). T-F04
  intentionally does not touch it; it gets its own PR.
- **Cross-subsystem caller-side `instrument_map={}` wiring** in
  `src/orchestrator/tick.py`, `scripts/initialise.py`, and
  `scripts/trace_tick.py` is **not** fixed by T-F04. T-F04's Bomb 2
  raise is the forcing function — the wiring fix belongs to a
  separate PR (likely a future `scripts/` audit follow-up).
- **Lifecycle source P1-01** (dead `broker_mode` / `watchlist`
  kwargs), **P1-02** (`_check_env` missing `DATABASE_URL` /
  `STOCKBOT_ENV`), and **P1-03** (`_archive_sqlite` partial-failure
  recovery) are all deferred. Same goes for the lifecycle test-side
  P1-01..P1-04, P2-01..P2-04, P3-01 polish items.
- **Broker P2-01 / P2-02** (dead `position_size` method and its
  docstring drift) and broker test P2-01..P3-01 (layout polish,
  weak-positive assertions on base-URL constants, opaque `_make_ctx`
  MagicMock) are deferred.
- **Snapshotter P0-02** (cold-start anchors not in §A) and P1-01..P1-04
  (broker / price-provider reads inside the pipeline, MemoryWriter
  cross-tick reads) are deferred — different fix patterns, likely
  paired with Spec C hydration work.
- **Live-side `_seed_state_for_adk` helper extraction.** Marked
  "strongly recommended" in the T-F04 spec but allowed as an
  optional improvement. If the subagent ships only the inline
  coercion at `tick.py:148`, the live-only bomb is still defused;
  the helper extraction becomes a follow-up cleanup PR. The
  dispatcher should explicitly note in the PR description whether
  the helper landed or not — the answer affects whether
  `backtest/driver.py:494-499` was touched.

The next planning cycle (post-Plan C) should pick up Spec C / Phase 2
hydration as the lead item, since orchestrator P0-03 — the JSON-safety
coercion — is the only one of the three orchestrator P0s that Plan C
closes. P0-01 and P0-02 remain open and are the explicit blockers for
declaring the orchestrator audit closed.

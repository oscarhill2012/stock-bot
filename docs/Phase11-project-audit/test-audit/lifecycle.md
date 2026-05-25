# Test audit — `src/lifecycle/`

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/lifecycle.md`
**Test files in scope:** 7 (full list below)
**Tests collected from those files:** 16 (via `pytest tests/unit/test_initialise.py tests/unit/test_initialise_cli.py tests/unit/test_hard_reset.py tests/unit/test_hard_reset_cli.py tests/unit/test_lifecycle_initialise.py tests/unit/test_init_db_script.py tests/unit/test_scheduler_yaml.py --collect-only -q`)
**Findings:** 4 P0 · 4 P1 · 4 P2 · 1 P3

## Files in scope

All seven tests live loose under `tests/unit/`. None mirror the source
layout — there is no `tests/unit/lifecycle/` folder despite the package
under audit being a self-contained, four-file subsystem under
`src/lifecycle/`. Two further files (`test_init_db_script.py`,
`test_scheduler_yaml.py`) exercise lifecycle-adjacent code: the schema
seeder (`scripts/init_db.py`) and the Cloud Scheduler YAML
(`deploy/scheduler.yaml`).

- `tests/unit/` (lifecycle-proper) — 5 files
  - `tests/unit/test_initialise.py`
  - `tests/unit/test_initialise_cli.py`
  - `tests/unit/test_lifecycle_initialise.py` (the *heuristics* check
    only — odd one-test file)
  - `tests/unit/test_hard_reset.py`
  - `tests/unit/test_hard_reset_cli.py`
- `tests/unit/` (lifecycle-adjacent) — 2 files
  - `tests/unit/test_init_db_script.py`
  - `tests/unit/test_scheduler_yaml.py`

No tests for `src/lifecycle/scheduler.py` directly — the two CLI/library
tests monkey-patch it but no test asserts the production behaviour
(subprocess invocation).

## Summary

The lifecycle suite is small and well-scoped: every public function in
the package has at least one happy-path test plus one or two negative
paths, no live network calls, no live DB writes. The dominant problem
is **table-list parochialism** — every test that asserts emptiness or
truncation hardcodes the same three legacy ORM tables that the source
already mishandles (P0-01, P0-02 in `docs/Phase11-project-audit/source-audit/lifecycle.md`),
so the tests pass green on databases where the canonical Spec-B
cross-tick state (`user_state` / `sessions` / `events` /
`app_states`) is dirty. Two related secondary themes: (a) every
`initialise()` caller in the suite still passes the now-dead
`broker_mode` and `watchlist` kwargs (source P1-01), so deleting
those kwargs in source will be blocked by these tests until they
move; (b) the suite has zero coverage of the `DATABASE_URL` /
`STOCKBOT_ENV` checks that source P1-02 wants added — a gap, but one
the source fix will own creating. Layout-wise the files sit loose in
`tests/unit/` rather than `tests/unit/lifecycle/`, and the lone
`test_lifecycle_initialise.py` file (one test, just heuristics)
overlaps confusingly with `test_initialise.py`.

## Findings

### P0-01 · T4 missing surfacing test · `_check_live_tables_empty` does not assert ADK session tables are checked

- **Location(s):** new test needed; `tests/unit/test_initialise.py` would
  be the natural home (next to `test_refuses_on_non_empty_tables`).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P0-01.
- **Confidence:** high
- **Description:**
  The only "tables empty" negative test
  (`tests/unit/test_initialise.py:71` `test_refuses_on_non_empty_tables`)
  seeds a row into the *legacy* `portfolio_snapshots` table and asserts
  `NonEmptyTablesError`. There is no test that seeds a row into the ADK
  `user_states` / `sessions` / `events` / `app_states` tables — the
  surface that since Spec B holds the cross-tick `user:positions` /
  `user:thesis` book. The current `_STOCKBOT_TABLES` tuple ignores those
  tables, so `_check_live_tables_empty` returns clean on a database where
  the prior run's thesis book is still resident — and a freshly
  "initialised" bot will silently hydrate stale thesis state at Phase 2.
  This is the most important T4 in scope: no existing test would catch
  the regression the source audit flagged P0, so the source fix needs a
  new test written alongside it.
- **Suggested action:**
  Add `test_refuses_on_non_empty_adk_user_state` (and a matching variant
  for `sessions`/`events`) that pre-populates a row via
  `DatabaseSessionService.append_event(...)` on the same DB URL the
  initialise call uses, and asserts `NonEmptyTablesError` carrying a
  message that names the ADK table.

### P0-02 · T4 missing surfacing test · `hard_reset` does not assert ADK session tables are truncated

- **Location(s):** new test needed; `tests/unit/test_hard_reset.py`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P0-02.
- **Confidence:** high
- **Description:**
  `tests/unit/test_hard_reset.py::test_archive_creates_file_and_truncates_live`
  (line 37) is the load-bearing truncation test. It seeds *only* a
  legacy `portfolio_snapshots` row, runs `hard_reset`, and then asserts
  `s.query(PortfolioSnapshotRow).count() == 0` (line 67) and that the
  archive file contains the row (line 74). It does not seed or assert
  on the ADK `user_states` / `sessions` / `events` / `app_states`
  tables. Per the source audit, `_truncate_live` only deletes from the
  three legacy ORM tables — so this test passes on a code path that
  leaves the ADK thesis book intact across the reset. Worse: the
  meta-row-counts assertion `meta["row_counts"]["portfolio_snapshots"]
  == 1` (line 61) is structured to silently survive the eventual fix —
  it doesn't even check `meta["row_counts"]` has no other keys, so a
  source-side broadening could land without changing this assertion.
- **Suggested action:**
  Add `test_truncates_adk_session_tables_too` that seeds via
  `DatabaseSessionService.create_session(...)` and
  `append_event(...)` (so all four `sessions` / `events` /
  `user_states` / `app_states` rows materialise), runs `hard_reset`,
  and asserts every one of those tables has zero rows post-reset.
  Pair with a positive archive-contains-them assertion.

### P0-03 · T3 completion-only · `test_main_calls_initialise` asserts only `rc == 0`

- **Location(s):** `tests/unit/test_initialise_cli.py:37`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P1-01,
  P1-02.
- **Confidence:** high
- **Description:**
  The single CLI test in `test_initialise_cli.py` runs `cli.main_async`
  with a full argv list, monkey-patches the broker and SPY fetch, and
  asserts `rc == 0` (line 37). It does not assert that the anchor row
  was written, that the watchlist file contents were parsed correctly,
  or that the underlying `initialise()` was even called with the
  expected kwargs. A regression that, say, silently drops the broker
  cash check (or skips `_check_heuristics`) would still produce `rc == 0`
  and pass this test. Per `docs/test-policy.md §A.7` and §E (`"It didn't
  raise, therefore it works"`), this is exactly the shape the policy
  flags. Filing P0 because the CLI is the *only* path that actually
  reads the watchlist file from disk — the body of `initialise()`
  ignores `watchlist` per source P1-01 — so all watchlist-shape
  validation lives in the CLI and there is currently no test asserting
  on it.
- **Suggested action:**
  Add positive assertions after `rc == 0`: assert the anchor row exists
  with `tick_id == "init"`, assert captured stdout contains the
  "Wrote anchor snapshot" line (via `capsys`), and add a sibling test
  that gives the CLI a malformed watchlist JSON and asserts `rc == 1`
  with the appropriate stderr message.

### P0-04 · T3 completion-only · `test_yes_flag_skips_prompt` only checks for "Archived" in stdout

- **Location(s):** `tests/unit/test_hard_reset_cli.py:35`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P0-02
  (compounds — fixing P0-02 will not surface a regression here unless
  this test gains stronger assertions).
- **Confidence:** high
- **Description:**
  The only happy-path CLI test for `hard_reset` (line 35) runs the CLI
  with `--yes`, captures stdout, and asserts `"Archived"` appears
  somewhere in it. It does not assert the live DB is empty, that the
  archive file exists on disk, that the meta JSON was written, or that
  the scheduler was paused. A future refactor that prints `"Archived"`
  but skips the truncate step entirely would pass this test. Combined
  with P0-02 above, the load-bearing happy-path test for the entire
  hard-reset workflow is `test_archive_creates_file_and_truncates_live`
  in the library test file — the CLI layer is effectively unguarded.
- **Suggested action:**
  After `cli.main([...])`, re-open the live DB and assert every table
  the CLI claims to truncate is empty; assert
  `(archive_dir / "*.db").exists()`; assert the meta JSON exists and
  parses. Pull the same assertions used in
  `test_archive_creates_file_and_truncates_live`.

### P1-01 · T1 dead-kwarg tests · five tests stub `broker_mode`/`watchlist` kwargs that source P1-01 wants to delete

- **Location(s):** `tests/unit/test_initialise.py:52-55, 96-99, 116-119,
  134-137`; `tests/unit/test_initialise_cli.py:34-36` (the `--watchlist`
  argv).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P1-01.
- **Confidence:** high
- **Description:**
  Every call to `initialise(...)` in `test_initialise.py` passes
  `broker_mode="paper", watchlist=["AAPL"]` — kwargs the source audit
  recommends deleting because `initialise()`'s body never references
  them. The CLI test (line 35) similarly passes `--watchlist
  config/watchlist.json` and `--broker-mode paper` purely to satisfy the
  signature. When the source fix lands these tests will fail at the
  `initialise(...)` call site with `TypeError: unexpected keyword
  argument`. None of the tests assert anything about the watchlist or
  broker mode value — they're plumbing-only — so they can be deleted
  from the call sites in the same PR that removes the kwargs.
- **Suggested action:**
  In the source-fix PR that drops the kwargs from `initialise()`,
  drop the same kwargs from these four call sites. The CLI test needs
  a watchlist file on disk only because the CLI itself reads the file
  before calling `initialise()` — leave the `--watchlist` argv intact
  but switch the assertions to checking the file was actually parsed
  (per P0-03 suggestion).

### P1-02 · T3 + T4 · No coverage of `_check_heuristics` happy path or the heuristics→DB→anchor ordering

- **Location(s):** `tests/unit/test_lifecycle_initialise.py:12-26`
  (only test in that file).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P3-02
  (step-numbering doc drift compounds the readability problem).
- **Confidence:** medium
- **Description:**
  `test_lifecycle_initialise.py` contains exactly one test — a negative
  path: malformed `analyst_heuristics.json` raises `JSONDecodeError`
  via `_check_heuristics()` directly (not via `initialise()`). There
  is no test that:
  (a) `_check_heuristics` is invoked from `initialise()`'s body (i.e.
      a malformed file raises through the full `initialise()` call,
      not just the helper), and
  (b) `_check_heuristics` runs *before* `_check_live_tables_empty` (the
      source comment says "fail fast before any DB or broker work"
      — important when a partial bring-up has an empty DB and a broken
      heuristics file). Today nothing asserts that ordering.
- **Suggested action:**
  Move `test_check_heuristics_raises_on_malformed_config` into
  `test_initialise.py` (where every other negative-path test lives —
  see P2-02 below), and add a sibling test that monkey-patches
  `_check_live_tables_empty` to record call order vs.
  `_check_heuristics` and asserts heuristics ran first.

### P1-03 · T8 layout · `test_lifecycle_initialise.py` overlaps `test_initialise.py` with one orphan test

- **Location(s):** `tests/unit/test_lifecycle_initialise.py` (whole file).
- **Source-audit cross-ref:** n/a (test-policy §B and §D).
- **Confidence:** high
- **Description:**
  The file holds exactly one test (`test_check_heuristics_raises_on_malformed_config`).
  Its filename suggests it's the canonical lifecycle-initialise test
  file, but the actual canonical file is `test_initialise.py`. A reader
  hunting for "where do lifecycle init tests live" finds both, and
  has to read both to discover the one-vs-four split. Per test-policy
  §B unit tests should mirror the source tree
  (`tests/unit/lifecycle/test_initialise.py`); the split into two
  loose files compounds that layout drift. The one test belongs
  alongside `test_refuses_on_missing_env_var` and friends in the same
  file.
- **Suggested action:**
  Fold the single test from `test_lifecycle_initialise.py` into
  `test_initialise.py` (renaming for consistency:
  `test_refuses_on_malformed_heuristics`) and delete the orphan file.
  Pair with the layout move proposed in P2-02.

### P1-04 · T6 wide-scope monkeypatch · `monkeypatch.setattr(scheduler, "resume_job"/"pause_job", lambda: None)`

- **Location(s):** `tests/unit/test_initialise.py:44`,
  `tests/unit/test_initialise_cli.py:29`,
  `tests/unit/test_hard_reset.py:43-44, 86, 112`,
  `tests/unit/test_hard_reset_cli.py:41`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P2-03
  (the docstrings claim "no-op shim under tests" but the functions
  are *not* no-ops — these monkeypatches are what makes them no-ops).
- **Confidence:** medium
- **Description:**
  Six tests monkey-patch `scheduler.resume_job` / `pause_job` to
  `lambda name: None`. Per test-policy §A.6 and §E ("Wide-scope
  `monkeypatch.setattr` on a class") this is appropriate scope —
  the function is the seam — but the bigger T6 concern is what's
  *missing*: there is no test of the scheduler module itself that
  asserts `subprocess.run` gets called with the right `gcloud` argv.
  The combined effect is that the only thing keeping the live tests
  from calling `gcloud` for real is the monkey-patch — a future refactor
  that removes the patch (or moves it to a fixture) without inserting
  an equivalent guard would surface as the test trying to invoke
  `gcloud` in CI. Filing P1 because this compounds source P2-03's
  doc-drift problem (the docstrings *say* the functions no-op under
  tests, so a refactor reading the docstrings might remove the patch
  thinking it's redundant).
- **Suggested action:**
  Add `tests/unit/lifecycle/test_scheduler.py` (under the proposed
  layout move) with two tests that monkey-patch
  `subprocess.run` (the actual leaf) and assert
  `pause_job("foo")` / `resume_job("foo")` call it with the expected
  argv. Then the existing higher-level monkey-patches are auxiliary,
  not load-bearing.

### P2-01 · T3 weak-positive assertion · `test_archive_creates_file_and_truncates_live` checks only `.suffix == ".db"`

- **Location(s):** `tests/unit/test_hard_reset.py:55, 61, 67-68, 74`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P1-03
  (`_archive_sqlite` half-failure attractor — no test today guards
  the "partial archive, full truncate" failure mode).
- **Confidence:** medium
- **Description:**
  The happy-path archive test asserts the archive file exists
  (line 55), has suffix `.db` (line 56), and that its
  `portfolio_snapshots` count is 1 (line 74). It does not open the
  archive and verify schema integrity (`PRAGMA integrity_check`),
  does not assert the archive table list matches live, and does not
  exercise the "archive corrupts mid-VACUUM" failure path that source
  P1-03 calls out. So the source-fix that adds "verify archive
  integrity before truncate" lands without a single regression test
  to anchor against. Filing P2 because the source bug itself is
  P1, not P0 — but the test gap is real.
- **Suggested action:**
  Add `test_archive_integrity_verified_before_truncate` that uses
  `monkeypatch.setattr` on `_archive_sqlite` to write a deliberately
  short / truncated file, then asserts `hard_reset` raises *before*
  `_truncate_live` runs (assert the live DB still has the seeded
  row afterwards).

### P2-02 · T8 layout · seven lifecycle files loose in `tests/unit/`

- **Location(s):** `tests/unit/test_initialise.py`,
  `test_initialise_cli.py`, `test_lifecycle_initialise.py`,
  `test_hard_reset.py`, `test_hard_reset_cli.py`,
  `test_init_db_script.py`, `test_scheduler_yaml.py`.
- **Source-audit cross-ref:** n/a (test-policy §B mirror-the-source-tree).
- **Confidence:** high
- **Description:**
  Per `docs/test-policy.md §B` unit tests "Live under `tests/unit/`
  mirroring the source tree (e.g. `src/agents/news/fetch.py` →
  `tests/unit/agents/news/test_fetch.py`)". Lifecycle is the only
  subsystem of comparable size that has no mirror directory —
  the seven files sit at the top level of `tests/unit/`. Compounded
  by `test_lifecycle_initialise.py` (P1-03 above) splitting the
  canonical lifecycle-init coverage across two files.
- **Suggested action:**
  Create `tests/unit/lifecycle/` and move
  `test_initialise.py`, `test_hard_reset.py`,
  `test_lifecycle_initialise.py`'s one test (per P1-03). Also create
  `tests/unit/scripts/` (or fold under `tests/unit/lifecycle/` as the
  CLI is the lifecycle-script layer) for the CLI/script tests
  (`test_initialise_cli.py`, `test_hard_reset_cli.py`,
  `test_init_db_script.py`). The `test_scheduler_yaml.py` test
  belongs under `tests/unit/deploy/` since it audits
  `deploy/scheduler.yaml`, not `src/lifecycle/scheduler.py`.

### P2-03 · T4 missing surfacing test · No test for `_check_env` against `DATABASE_URL`

- **Location(s):** new test needed.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P1-02.
- **Confidence:** medium
- **Description:**
  `_REQUIRED_ENV` covers only `TRADING212_API_KEY` and `FINNHUB_API_KEY`.
  Source P1-02 wants `DATABASE_URL` (gated on `STOCKBOT_ENV=prod`)
  added. `test_refuses_on_missing_env_var` (line 104) only deletes the
  two existing keys. When the source fix expands `_REQUIRED_ENV`, no
  existing test will fail to confirm the fix actually surfaces the
  new key. Filing P2 because the source finding is P1 not P0 — the
  bug surfaces as "Phase 1 invariant defaulted into a Phase 2
  runtime crash", noisy but not silent.
- **Suggested action:**
  In the source-fix PR for P1-02, add a parametrised version of
  `test_refuses_on_missing_env_var` that deletes each required env
  var in turn and asserts `EnvVarMissingError` mentions the deleted
  one by name.

### P2-04 · T3 incidental coverage · `test_init_db_creates_all_tables` hardcodes the same three legacy tables

- **Location(s):** `tests/unit/test_init_db_script.py:10` (the
  `EXPECTED_TABLES` constant).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/lifecycle.md` P0-01,
  P0-02 (both anchor on the same three-table inventory).
- **Confidence:** high
- **Description:**
  `EXPECTED_TABLES = {"buffer_entries", "trade_log",
  "portfolio_snapshots"}` is the same triplet that lives at the top
  of `src/lifecycle/initialise.py` (line 21) and
  `src/lifecycle/hard_reset.py` (line 17). The test uses
  `EXPECTED_TABLES.issubset(tables)` (not `==`), so it does not
  *forbid* extra tables — meaning the test will keep passing when ADK
  schemas are created — but it also does not *require* the ADK
  tables. If `init_db` (or any callee of `create_all`) ever drops
  ADK schema creation, this test is silent. Filing P2 because
  `init_db` is intentionally schema-only and ADK creates its own
  tables on `DatabaseSessionService.__init__`; today the gap is
  defensible. But the three-table parochialism in the test mirrors
  the source bug, so it gets noted.
- **Suggested action:**
  Either expand `EXPECTED_TABLES` once the source-fix for P0-01/P0-02
  defines the canonical "StockBot persistence surface", or document
  in a test-level comment that `init_db` covers only the legacy ORM
  tables and ADK creates its own on first `DatabaseSessionService`
  invocation. The latter is probably correct; the comment is the
  cheaper fix.

### P3-01 · T8 cosmetic · `tests/unit/test_scheduler_yaml.py` audits a deploy file, not a source module

- **Location(s):** `tests/unit/test_scheduler_yaml.py:8`.
- **Source-audit cross-ref:** n/a.
- **Confidence:** high
- **Description:**
  The file tests `deploy/scheduler.yaml` — three contract-style
  assertions on a YAML config file. It is correctly auditing a
  config-shape boundary, but it lives loose in `tests/unit/` next to
  source-tree tests, with no marker. Per test-policy §B and §C this
  is a `contract` test in spirit (asserts on YAML structure /
  invariants, not runtime values) and would fit better under
  `tests/contract/` with `@pytest.mark.contract`. Filing P3 because
  the test works and is cheap; this is naming/discoverability only.
- **Suggested action:**
  Move to `tests/contract/test_scheduler_yaml.py` with
  `@pytest.mark.contract`. Or, if other deploy YAMLs accrete tests,
  consolidate under `tests/contract/deploy/`.

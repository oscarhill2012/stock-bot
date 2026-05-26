# Source audit — `src/lifecycle/`

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 4 (`__init__.py`, `scheduler.py`, `initialise.py`, `hard_reset.py`)
**Findings:** 2 P0 · 3 P1 · 3 P2 · 2 P3

## Summary

`src/lifecycle/` houses the *boot / shutdown* shim for the live deployment
target: `initialise.py` performs the once-per-process Phase 1 pre-flight
(env, schema, broker reachability, anchor snapshot, Cloud Scheduler resume);
`hard_reset.py` archives-and-truncates the StockBot ORM tables; `scheduler.py`
is a 20-line `gcloud` wrapper so tests can monkey-patch. Per the contract
this package owns the §B Phase 1 invariant set — Phase 2 / Phase 4 per-tick
work lives in `src/orchestrator/tick.py` and the backtest driver, not here,
so most of §C-Rule 7 lands cleanly. The two load-bearing findings concern
**incomplete coverage of the persistence surface**: both `initialise._check_live_tables_empty`
and `hard_reset._STOCKBOT_TABLES` ignore the ADK `DatabaseSessionService`
tables (`sessions`, `user_states`, `app_states`, `events`) that since Spec B
hold the cross-tick `user:positions` / `user:thesis` rows — so a fresh-boot
check passes against stale thesis state, and a hard reset leaves the prior
run's thesis book intact. Beyond that the package is small, clean, and
correctly delineated from the per-tick lifecycle. Consolidation needs to
know that Phase 2 / Phase 4 invariants land in `src/orchestrator/tick.py`
and `src/backtest/driver.py`, not in this subsystem — audits of those files
own the rest of the Lifecycle contract surface.

## Findings

### P0-01 · C4 contract violation · `_check_live_tables_empty` ignores ADK session tables that hold the thesis book

- **Location:** `src/lifecycle/initialise.py:21` (`_STOCKBOT_TABLES` tuple) and `src/lifecycle/initialise.py:74-89` (`_check_live_tables_empty`); compounded by `src/lifecycle/initialise.py:146` calling it.
- **Confidence:** high
- **Description:**
  The Phase 1 pre-flight asserts "live tables empty" by counting rows
  in three legacy ORM tables: `buffer_entries`, `trade_log`,
  `portfolio_snapshots`. Per `contract-invariants.md` §E and the Spec B
  clarification of §C-Rule 7, the *canonical* cross-tick state for
  `state["user:positions"]` and `state["user:thesis"]` lives in ADK's
  `DatabaseSessionService` tables (`sessions`, `user_states`,
  `app_states`, `events`) — exactly the surface that must be wiped
  between runs to avoid cross-contamination. The current check passes
  on a database where the ADK `user_state` table still carries the prior
  run's thesis book, which means a freshly-initialised bot will be
  hydrated at Phase 2 with stale `user:positions` / `user:thesis`
  values — silently inheriting another run's state. This is the
  §B Phase 1 invariant "Persistence layer ready" interpreted too
  narrowly, and the §B Phase 2 "Cross-tick fields are populated from
  their `Persistence` source" invariant unintentionally broken at run
  start. The same script's `_resolve_default_db_url` (in
  `scripts/initialise.py:30`) targets `sqlite:///data/stockbot.db`,
  the very DB that also hosts the ADK session tables when
  `DATABASE_URL` is set to it — so this is reachable, not theoretical.
- **Suggested action:**
  Either extend `_STOCKBOT_TABLES` to cover the ADK
  `DatabaseSessionService` tables (`sessions`, `events`, `user_states`,
  `app_states`) or — preferred — add an explicit `_check_adk_session_state_empty`
  step that runs `SELECT COUNT(*)` against those tables and raises
  `NonEmptyTablesError` with a message pointing at `scripts.hard_reset`.

### P0-02 · C4 contract violation · `hard_reset` does not archive or truncate ADK session tables

- **Location:** `src/lifecycle/hard_reset.py:17` (`_STOCKBOT_TABLES`),
  `src/lifecycle/hard_reset.py:46-70` (`_archive_sqlite` / `_archive_postgres`),
  `src/lifecycle/hard_reset.py:73-80` (`_truncate_live`).
- **Confidence:** high
- **Description:**
  The hard-reset workflow's stated purpose is "archive then truncate
  all StockBot state" so that `initialise` can write a clean anchor and
  the next run starts from zero (the script docstring at
  `src/lifecycle/hard_reset.py:1` says exactly that). For SQLite the
  whole file is `VACUUM INTO`-archived, so the *archive* arguably
  captures the ADK session tables incidentally — but the *truncate
  step* (`_truncate_live`, line 73-80) deletes only the same three
  legacy tuple of tables. Result: after a hard reset the ADK
  `user_states` / `sessions` / `events` tables still hold the prior
  run's `user:positions` / `user:thesis` rows. On the next `initialise`
  → `run_once` cycle the new session's `app_name` (`StockBot-paper` /
  `StockBot-live` per `orchestrator/tick.py:25-54`) collides with the
  prior run's, and ADK silently hydrates the stale thesis on
  `create_session`. This is a contract violation by erasure of the
  precondition `_check_live_tables_empty` is supposed to guard against
  (P0-01 above). For Postgres the situation is worse: `_archive_postgres`
  only `CREATE TABLE … AS SELECT * FROM public.<t>` for the three
  legacy tables, so the ADK state is neither archived nor truncated.
  Either the live thesis book survives or, if a user later manually
  truncates ADK tables, the archive is incomplete and cannot be
  replayed.
- **Suggested action:**
  Treat the ADK persistence surface as part of "StockBot state":
  extend the truncate path to clear the ADK session tables filtered
  by `app_name LIKE 'StockBot-%'`, and extend `_archive_postgres` to
  copy those tables into the archive schema. SQLite's `VACUUM INTO`
  already captures them, but the truncate step still needs to wipe
  them post-archive.

### P1-01 · C3 overabstraction · `initialise()` accepts `broker_mode` and `watchlist` it never uses

- **Location:** `src/lifecycle/initialise.py:126-134` (signature),
  `src/lifecycle/initialise.py:135-166` (body).
- **Confidence:** high
- **Description:**
  The `initialise()` coroutine declares `broker_mode: str` and
  `watchlist: list[str]` as required keyword arguments, and
  `scripts/initialise.py:64-71` dutifully reads the watchlist file and
  passes both through — but the function body never references either
  parameter. The watchlist is loaded in the script purely to satisfy
  this dead kwarg (and to print its length at line 84). This is the
  "indirection that buys nothing concrete" shape from §C3: callers
  are forced to compute values that go unused. It is also a latent
  Phase 1 contract drift — the contract says Phase 1 wires "the active
  watchlist for this tick" into config, but the actual watchlist read
  for ticks happens in `orchestrator.stock_picker.get_watchlist()`
  called per-tick (`orchestrator/tick.py:195`), not at boot. The
  passed-in `watchlist` is verified-exists by `scripts/initialise.py`
  but the loader at boot does nothing with the file contents.
- **Suggested action:**
  Either drop both kwargs from `initialise()` (and `InitResult.scheduler_job`
  consumers' caller side accordingly), or — if the intent was that
  Phase 1 should validate the watchlist *config* — wire a real check
  (e.g. assert tickers are non-empty, schema-valid). The current shape
  is the worst of both: signature implies validation, body does none.

### P1-02 · C4 contract violation · `_check_env` only covers two of the keys the live tick actually requires

- **Location:** `src/lifecycle/initialise.py:20` (`_REQUIRED_ENV`),
  `src/lifecycle/initialise.py:54-57` (`_check_env`).
- **Confidence:** medium
- **Description:**
  `_REQUIRED_ENV = ("TRADING212_API_KEY", "FINNHUB_API_KEY")`. Per
  `docs/contract-invariants.md` §B Phase 1, "configuration loaded
  (tickers, runtime settings)" and "provider implementations wired"
  must hold before any tick runs. The live providers exposed through
  `src/data/providers/` reach into the environment at fetch time —
  `data.providers.news.finnhub` is `FINNHUB_API_KEY`, but the
  contract also covers a `DATABASE_URL` (read by
  `orchestrator.persistence.make_session_service:464` and required for
  Spec B's ADK persistence) and `STOCKBOT_ENV` (read by
  `scripts/initialise.py:24`). The current `_check_env` would pass on
  a production deployment that is missing `DATABASE_URL`, and the
  failure would only surface on the first tick when ADK tries to
  create the session. That is a Phase 1 invariant defaulted into a
  Phase 2 runtime crash — the contract specifies Phase 1 is where
  this must be caught.
- **Suggested action:**
  Add `DATABASE_URL` (gated on `STOCKBOT_ENV=prod`) and any other
  env keys live providers actually require to `_REQUIRED_ENV`, or
  source the list from a single config table so the check stays
  in sync. The `scripts/initialise.py` helper `_resolve_default_db_url`
  has the logic; lift it into the lifecycle layer.

### P1-03 · C5 silent-failure attractor · `_archive_sqlite` opens raw `sqlite3` without recovery on partial failure

- **Location:** `src/lifecycle/hard_reset.py:46-57` (`_archive_sqlite`).
- **Confidence:** medium
- **Description:**
  `_archive_sqlite` opens a raw `sqlite3.connect(src)` and runs
  `VACUUM INTO`. If `VACUUM INTO` fails partway (disk full, permission
  error, target file already exists at the OS level despite the
  pre-check), the `finally: conn.close()` runs but the partial file
  at `archive_path` may already exist on disk. The downstream
  `_truncate_live` (called next in `hard_reset:111`) then executes
  unconditionally — wiping the live DB even though the archive is
  corrupt or partial. The check at line 48 (`if archive_path.exists():
  raise`) only fires *before* the VACUUM begins; nothing rolls back a
  half-written archive. The function is also using a literal SQL
  f-string `f"VACUUM INTO '{archive_path.as_posix()}'"` — safe here
  because `archive_path` is a Pathlib path built from `archive_dir`
  + a timestamp, but the same pattern appears in C5 territory
  (path traversal via `archive_dir`). For a hard-reset workflow the
  cost of a half-written archive is unrecoverable user state.
- **Suggested action:**
  Wrap archive + truncate in a "verify archive integrity then
  truncate" sequence — re-open the archive file, count rows in each
  expected table, and only proceed to `_truncate_live` if the
  per-table row count matches the `_row_counts(db_url)` snapshot
  taken at step 2. On mismatch, raise and leave live tables intact.

### P2-01 · C1 dead code · `src/lifecycle/__init__.py` is empty (zero bytes)

- **Location:** `src/lifecycle/__init__.py` (0 lines).
- **Confidence:** high
- **Description:**
  The package's `__init__.py` is empty. That is conventional and not
  itself a finding — but combined with the absence of any public
  re-exports it means downstream callers (e.g. tests doing `from
  lifecycle import scheduler`) reach the submodule directly. No
  package-level docstring exists describing what "lifecycle" means
  in this codebase — and given §C-Rule 7 makes "lifecycle" a
  load-bearing word in the contract, a one-paragraph docstring
  explaining that this package owns Phase 1 only (and Phase 2/4
  live in `orchestrator/tick.py` + `backtest/driver.py`) would
  short-circuit future confusion. Filed as C1 rather than C7
  because the file is *empty*, not *drifted*.
- **Suggested action:**
  Add a module docstring to `__init__.py` (e.g. "Phase 1 boot /
  Phase 4 archive helpers — per-tick lifecycle work lives in
  `orchestrator.tick` and `backtest.driver`, see
  `docs/contract-invariants.md` §B").

### P2-02 · C6 config-convention violation · hardcoded paths and magic numbers

- **Location:** `scripts/initialise.py:30` (`"sqlite:///data/stockbot.db"`),
  `scripts/hard_reset.py:26` (same string),
  `scripts/hard_reset.py:41` (`"data/archives"` default),
  `src/lifecycle/initialise.py:92` (`tolerance: float = 1.0` in
  `_check_broker_cash`).
- **Confidence:** medium
- **Description:**
  The default SQLite path `data/stockbot.db` is duplicated across two
  scripts; the archive directory default `data/archives` and the
  broker-cash tolerance `$1.00` are inline magic constants. Per
  `.claude/CLAUDE.md` "Configuration Convention", these belong in a
  `config/*.json` file with a `config/README.md` entry. Two of these
  (the DB URL strings) are duplicated literally — if the default DB
  location ever moves, the two scripts can drift. The tolerance is
  subtler: `_check_broker_cash` rejects the run if broker cash differs
  by more than $1; that threshold is a calibration knob (e.g. an
  international account in GBP would want a different tolerance), and
  the contract has no opinion either way, but the value should live
  in `config/`. Filing as P2 because none of these are wrong-output
  hazards — they are tidy-up.
- **Suggested action:**
  Add a `config/lifecycle.json` (or fold into an existing settings
  file) with `default_db_url`, `archive_dir`, `broker_cash_tolerance`;
  delete the duplicated strings; update `config/README.md`.

### P2-03 · C7 doc/code drift · `scheduler.py` docstring claims "no-op under tests" but is not

- **Location:** `src/lifecycle/scheduler.py:8` and `src/lifecycle/scheduler.py:16`.
- **Confidence:** high
- **Description:**
  Both `pause_job` and `resume_job` docstrings claim "No-op shim under
  tests." The functions are not no-ops under tests — they call
  `subprocess.run(["gcloud", ...], check=True)` unconditionally. Tests
  achieve the no-op behaviour by `monkeypatch.setattr(scheduler,
  "pause_job", lambda name: None)` (see `tests/unit/test_hard_reset.py:42-43`).
  The docstring describes the *intent* under tests, not the *code* —
  whoever reads the source file is told the function does nothing in
  tests, but the function itself has no test-mode awareness. A future
  refactor that removed the monkey-patch in a test fixture would call
  `gcloud` for real in CI and surface as an obscure failure.
- **Suggested action:**
  Rewrite the docstrings to describe what the function actually does
  ("Pause a Cloud Scheduler job via the `gcloud` CLI; tests
  monkey-patch this function to skip the subprocess call").

### P3-01 · C5 cosmetic · f-string SQL pattern with hardcoded table list

- **Location:** `src/lifecycle/hard_reset.py:39`, `src/lifecycle/hard_reset.py:68`,
  `src/lifecycle/hard_reset.py:79`, `src/lifecycle/initialise.py:83`.
- **Confidence:** high
- **Description:**
  Four sites build SQL strings via f-string interpolation
  (`f"SELECT COUNT(*) FROM {t}"`, `f"DELETE FROM {t}"`,
  `f'CREATE TABLE "{schema}"."{t}" AS SELECT * FROM public."{t}"'`).
  The interpolated values come from the hardcoded module-level
  `_STOCKBOT_TABLES` tuple, so there is no current SQL-injection
  risk. The pattern is still a smell — a future refactor that
  parameterises `_STOCKBOT_TABLES` from config (see P2-02 suggestion)
  would silently turn this into a real injection risk if not refactored
  to use `sqlalchemy.text` parameter binding or table-name quoting.
  Filed P3 because the current code is safe and rewriting it now is
  pre-emptive.
- **Suggested action:**
  When the table list is moved to config (P2-02), refactor to use
  SQLAlchemy reflection (`MetaData.reflect`) or explicit
  identifier-quoting via `sqlalchemy.sql.quoted_name`.

### P3-02 · C7 cosmetic doc drift · `initialise.py` step-numbered comments do not match the contract phases

- **Location:** `src/lifecycle/initialise.py:135-159` (numbered comments
  `# 1. Env`, `# 1b. Analyst heuristics config`, `# 2. Schema seed`, …).
- **Confidence:** medium
- **Description:**
  The body of `initialise()` annotates its steps `1`, `1b`, `2`, … `7`.
  The contract names these `§B Phase 1` invariants. A reader cross-referencing
  the contract has to mentally map "step 1" → "the env-vars portion
  of Phase 1". A docstring/comment referencing `§B Phase 1` directly
  (and noting that Phase 2/4 are in `orchestrator/tick.py`) would
  remove that lookup cost. Cosmetic, but the §C7 spirit applies:
  the comments are a parallel taxonomy to the canonical one. The
  module docstring at line 1 (`"""initialise — pre-flight, anchor
  snapshot, scheduler resume."""`) could also be expanded to point
  at the contract section.
- **Suggested action:**
  Replace the `# 1. … # 2. …` numbering with a single docstring
  paragraph mapping the function to `§B Phase 1` invariants
  (env / persistence / providers / broker reachability / anchor /
  scheduler resume). Land alongside any other PR touching this file.

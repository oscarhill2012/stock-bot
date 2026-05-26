# T-F03 — Lifecycle ADK-tables coverage

**Wave:** 3
**Pairs source-audit fix:** F2
**Branch:** `fix/T-F03-lifecycle-adk-tables`
**Depends on:** T-F10 (layout sweep — provides `tests/unit/lifecycle/` mirror)
**Estimated diff size:** medium

## Scope

Bring `src/lifecycle/initialise.py:_check_live_tables_empty` and
`src/lifecycle/hard_reset.py` into line with the actual persistence surface.
Since Spec B, the canonical cross-tick `user:positions` / `user:thesis`
rows live in ADK `DatabaseSessionService` tables (`sessions`,
`user_states`, `app_states`, `events`) — neither the pre-flight emptiness
check nor the hard-reset truncate path touches them today, so a "fresh"
boot silently inherits the previous run's thesis book, and a "hard reset"
leaves it in place. Rewrite the source paths to discover the ADK table
names from `DatabaseSessionService`'s SQLAlchemy metadata (not a hardcoded
literal list) and rewrite the lifecycle test suite to actually exercise
both paths against seeded ADK rows.

### In scope

- **Source — `src/lifecycle/initialise.py`:**
  - Extend `_check_live_tables_empty` so it counts rows in the ADK
    `DatabaseSessionService` tables (`sessions`, `user_states`,
    `app_states`, `events`) in addition to the three legacy ORM tables
    (`buffer_entries`, `trade_log`, `portfolio_snapshots`).
  - Determine the ADK table names by introspecting
    `DatabaseSessionService`'s SQLAlchemy metadata — do **not**
    hardcode the four string literals. Acceptable sources:
    `session_service.db_engine` → `sqlalchemy.inspect(engine).get_table_names()`
    filtered against the metadata, or reflecting from the ADK
    `Base.metadata` if exposed.
  - Raise `NonEmptyTablesError` (existing class) with a message that
    names the offending table(s) — at minimum distinguishing "legacy"
    from "ADK session" so the user knows whether `scripts.hard_reset`
    is the right escape hatch.
  - Closes `lifecycle.md` source P0-01.
- **Source — `src/lifecycle/hard_reset.py`:**
  - Extend `_truncate_live` so it deletes rows from the same ADK tables
    discovered via metadata introspection, scoped by
    `app_name LIKE 'StockBot-%'` so a shared DB cannot leak truncation
    into a sibling app.
  - Extend `_archive_postgres` so it `CREATE TABLE … AS SELECT * FROM
    public.<adk_table>` for each ADK table (SQLite's `VACUUM INTO`
    already captures the whole file, so no change needed there — but
    confirm in a comment).
  - Keep the metadata-driven table-name lookup symmetric with the
    `initialise.py` change above (extract a shared helper in
    `src/lifecycle/_adk_tables.py` or similar — naming up to the
    subagent, but the helper must be the single source of truth).
  - Closes `lifecycle.md` source P0-02.
- **Tests — move + rewrite (test-side P0-01..P0-04):**
  - All seven lifecycle test files move into `tests/unit/lifecycle/`
    (post-T-F10 — confirm the directory already exists before
    moving). Files to move:
    `tests/unit/test_initialise.py`,
    `tests/unit/test_initialise_cli.py`,
    `tests/unit/test_lifecycle_initialise.py`,
    `tests/unit/test_hard_reset.py`,
    `tests/unit/test_hard_reset_cli.py`,
    `tests/unit/test_init_db_script.py`,
    `tests/unit/test_scheduler_yaml.py`.
  - `test_initialise.py` — add **four** new sub-tests under a clear
    section header asserting `_check_live_tables_empty` rejects
    non-empty ADK tables (one each for `sessions`, `user_states`,
    `app_states`, `events`). Each seeds a single row via the canonical
    ADK API (`DatabaseSessionService.create_session(...)` +
    `append_event(...)` as appropriate) and asserts
    `NonEmptyTablesError` is raised with a message naming the
    offending table. Closes test P0-01.
  - `test_hard_reset.py` — add **four** new sub-tests asserting
    `hard_reset` truncates each ADK table (`sessions`, `user_states`,
    `app_states`, `events`). Each test seeds via the ADK API, runs
    `hard_reset`, and asserts the table count is `0` post-reset, and
    that the archive (SQLite file or Postgres archive schema) does
    contain the row. Closes test P0-02.
  - `test_initialise_cli.py::test_main_calls_initialise` — strengthen
    past `rc == 0`: after `cli.main_async` returns, assert (a) the
    anchor row exists in the DB with `tick_id == "init"`, (b) captured
    stdout contains the "Wrote anchor snapshot" line via `capsys`,
    (c) add a sibling test seeded with malformed watchlist JSON that
    asserts `rc == 1` and the expected stderr message. Closes test
    P0-03.
  - `test_hard_reset_cli.py::test_yes_flag_skips_prompt` — strengthen
    past substring-matching `"Archived"`: re-open the live DB and
    assert every truncatable table is empty post-CLI; assert the
    archive file exists on disk; assert the meta JSON exists and
    parses; reuse the assertions from
    `test_archive_creates_file_and_truncates_live`. Closes test P0-04.

### Out of scope

- Source P1-01 (`broker_mode`/`watchlist` dead kwargs) — leave the
  kwargs in place; the dead-kwarg cleanup belongs to a sibling PR.
- Source P1-02 (`_check_env` missing `DATABASE_URL`/`STOCKBOT_ENV`) —
  separate finding; do not retrofit here.
- Source P1-03 (`_archive_sqlite` partial-failure recovery) — defer.
- Source P2-01, P2-02, P2-03, P3-01, P3-02 (cosmetic / config /
  docstring drift) — defer.
- Test P1-01..P1-04 (dead-kwargs, `test_lifecycle_initialise.py`
  orphan, monkeypatch shape) — defer to a follow-up cleanup PR.
- Test P2-01..P2-04, P3-01 (layout polish, optional tests) — defer.
- Spec C (Phase 2 hydration of `memory_buffer` / `day_digest`) is
  explicitly deferred this cycle.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `lifecycle.md` source P0-01 | `src/lifecycle/initialise.py:21,74-89,146` | Extend `_check_live_tables_empty` to count rows in ADK session tables via metadata introspection. |
| `lifecycle.md` source P0-02 | `src/lifecycle/hard_reset.py:17,46-70,73-80` | Truncate ADK session tables in `_truncate_live`; archive them in `_archive_postgres`; share a metadata-driven helper with `initialise.py`. |
| `lifecycle.md` test P0-01 | `tests/unit/lifecycle/test_initialise.py` (post-move) | Add 4 sub-tests asserting `NonEmptyTablesError` for each ADK table. |
| `lifecycle.md` test P0-02 | `tests/unit/lifecycle/test_hard_reset.py` (post-move) | Add 4 sub-tests asserting ADK tables are truncated by `hard_reset`. |
| `lifecycle.md` test P0-03 | `tests/unit/lifecycle/test_initialise_cli.py` (post-move) | Strengthen CLI happy-path assertion beyond `rc == 0`; add malformed-watchlist negative test. |
| `lifecycle.md` test P0-04 | `tests/unit/lifecycle/test_hard_reset_cli.py` (post-move) | Strengthen CLI happy-path assertion beyond substring-matching "Archived". |

## Implementation steps

1. **Pre-flight (read-only):** confirm `tests/unit/lifecycle/` exists
   post-T-F10. If not, abort and flag to the dispatcher.
2. **Discover ADK table names via metadata.** Add a private helper
   (suggested location: `src/lifecycle/_adk_tables.py`) that takes a
   `DatabaseSessionService` instance (or its underlying engine) and
   returns the set of ADK-owned table names. Strategy: import the
   ADK SQLAlchemy `Base` (e.g. `from google.adk.sessions.database_session_service
   import Base as _AdkBase` — verify exact import path against the
   installed `google-adk` package) and read `Base.metadata.tables.keys()`.
   Fallback: `sqlalchemy.inspect(engine).get_table_names()` filtered
   against the legacy `_STOCKBOT_TABLES` tuple. Comment the chosen
   approach with a docstring explaining why hardcoded literals are
   forbidden.
3. **Update `src/lifecycle/initialise.py`:**
   - Add a new module-level helper `_check_adk_session_state_empty(db_url)`
     that calls the metadata helper, opens an engine, counts rows in
     each ADK table, and raises `NonEmptyTablesError` listing
     non-empty ones.
   - Invoke it from `_check_live_tables_empty` (or from the same call
     site at line 146) after the existing legacy-table check. Order
     matters: failing the legacy check first is fine; the message
     just needs to distinguish.
4. **Update `src/lifecycle/hard_reset.py`:**
   - In `_truncate_live`, after the existing legacy `DELETE FROM <t>`
     loop, iterate the ADK table list returned by the metadata helper
     and execute `DELETE FROM <adk_table> WHERE app_name LIKE
     'StockBot-%'`. Note: `events` and `app_states` may not carry
     `app_name` directly — verify each ADK table's columns from the
     metadata and scope the DELETE accordingly (e.g. join on
     `sessions.id` for `events` if needed). Where a clean scoped
     delete isn't possible, fall back to unscoped `DELETE` and
     comment the trade-off.
   - In `_archive_postgres`, after the existing legacy
     `CREATE TABLE … AS SELECT` loop, repeat for each ADK table.
     SQLite's `VACUUM INTO` already captures everything; add a
     one-line comment to that effect.
   - In `_archive_sqlite`, no semantic change needed — leave the
     `VACUUM INTO` as-is.
5. **Move test files into `tests/unit/lifecycle/`** via `git mv` so
   history is preserved. Update any test-internal imports that
   reference the old loose-root paths (unlikely, but grep first).
6. **Write the four new ADK-empty tests** in `test_initialise.py`
   (post-move). Helper fixture: a tmp sqlite DB URL plus a
   `DatabaseSessionService` constructed against it; seed one row via
   `await svc.create_session(...)` (for `sessions` + `app_states` +
   `user_states`) or `await svc.append_event(...)` (for `events`).
   Then assert `initialise(...)` raises `NonEmptyTablesError`.
7. **Write the four new ADK-truncate tests** in `test_hard_reset.py`
   (post-move). Same seeding helper; run `hard_reset(...)`; reflect
   the live DB post-reset and assert each ADK table has `COUNT(*) == 0`;
   assert the archive contains the seeded row (open the
   `.db` archive file and query, mirroring the existing
   `test_archive_creates_file_and_truncates_live`).
8. **Strengthen `test_initialise_cli.py::test_main_calls_initialise`:**
   - After `rc == 0`, open the live DB via SQLAlchemy and assert an
     anchor row with `tick_id == "init"` exists in
     `portfolio_snapshots` (the table `_write_anchor` writes to —
     verify in `src/lifecycle/initialise.py`).
   - Assert `"Wrote anchor snapshot"` appears in `capsys.readouterr().out`.
   - Add a new sibling test that writes a deliberately-malformed
     watchlist JSON to a tmp path, points the CLI at it via the
     `--watchlist` argv, and asserts `rc == 1` and a clear error
     message on stderr.
9. **Strengthen `test_hard_reset_cli.py::test_yes_flag_skips_prompt`:**
   - After `cli.main([...])` returns, re-open the live DB and assert
     every truncatable table (legacy + ADK) has zero rows.
   - Assert `(archive_dir / "*.db").exists()` (or the Postgres
     equivalent if applicable in the test config).
   - Assert the meta JSON file exists at the expected path and parses
     as JSON.
   - Lift the assertion helpers from
     `test_archive_creates_file_and_truncates_live` so the two tests
     share the same shape.
10. **Run the full suite** and verify green. Self-audit for new
    silent-failure surfaces introduced by the metadata helper (e.g.
    if the ADK import path changes, fail loudly — do not return an
    empty list).
11. **Append a graphify delta entry** noting the new
    `_adk_tables.py` helper (if added) and the moved test files.

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in
  commit body).
- [ ] No new audit findings introduced (subagent should self-audit
  against the rubric — particularly C5 silent-failure attractors in
  the new metadata helper).
- [ ] Graphify delta entry appended (new helper module + moved test
  files).
- [ ] ADK table names are sourced from SQLAlchemy metadata, not from
  hardcoded literals. Confirm by reading the implementation and
  noting the source of the table-name list in the commit body.
- [ ] All four new ADK-empty tests fail loudly (not silently) when
  the source helper is reverted (verify by manual flip on a scratch
  branch — optional but recommended).

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
.venv/bin/python -m pytest tests/unit/lifecycle/ -v   # focused
```

## Risks and rollbacks

- **Risk — ADK import path drift:** the metadata-discovery helper
  depends on the `google-adk` package exposing its SQLAlchemy `Base`
  at a stable import path. If the package's internal layout changes,
  the helper breaks. Mitigation: the helper raises a clear error
  (not a silent empty list) on import failure, and the four new
  tests would catch the breakage on the next `pytest` run.
- **Risk — `app_name LIKE 'StockBot-%'` scoping:** if a shared DB
  hosts other apps, scoping is important; if it doesn't, the scope
  clause is harmless. Validate the column exists on each ADK table
  before adding the predicate.
- **Risk — moving the seven test files** could surface dormant
  import errors (e.g. test fixtures relying on relative paths). Run
  `pytest --collect-only` before and after the move and confirm the
  collected-test count is identical.
- **Rollback:** feature branch can be discarded; no `main` impact
  until merge. The metadata helper is additive — reverting it leaves
  the legacy three-table list intact and matches pre-PR behaviour.

## Subagent dispatch prompt sketch

> Implement T-F03 from `docs/Phase11-project-audit/fix-plan/T-F03-lifecycle-adk-tables.md`.
> Read `docs/Phase11-project-audit/source-audit/lifecycle.md` and `docs/Phase11-project-audit/test-audit/lifecycle.md`
> in full first — these document the findings being closed.
> Source side: bring `_check_live_tables_empty` and `_truncate_live`
> into coverage of the ADK `DatabaseSessionService` tables, with the
> table names discovered via SQLAlchemy metadata (never hardcoded).
> Test side: move the seven lifecycle test files into
> `tests/unit/lifecycle/` (verify T-F10 has landed first), then add
> the four ADK-empty + four ADK-truncate sub-tests and strengthen the
> two CLI happy-path tests as detailed in the spec. Full
> `.venv/bin/python -m pytest tests/` must pass green before commit.
> Shell convention: never prepend `cd "/home/.../StockBot" && ...`
> to bash commands.

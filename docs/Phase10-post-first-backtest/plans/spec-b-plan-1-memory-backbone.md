# Spec B — Plan 1 — Memory backbone (rewrite 2026-05-23)

This plan implements the *persistence and writer* half of
`docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md`:

- ADK `user:`-prefixed state as the persistence layer for the
  cross-tick thesis book.
- `DatabaseSessionService` symmetric across live and backtest.
- The new `PositionThesis` model and the two new stance verbs
  (`hold`, `update`) wired into the strategist's output schema.
- **Executor's `after_agent_callback`** as the writer-of-record for
  `state["user:positions"]` and `state["user:thesis"]`.  ADK auto-
  yields a state-delta event from the callback's delta-tracked writes;
  `DatabaseSessionService` persists the keys to the `user_state`
  table.
- Runtime observability handles (`TraceWriter`, `DecisionLogger`)
  rebadged under the `temp:` prefix so the new
  `DatabaseSessionService` SQLAlchemy JSON path doesn't trip on
  non-serialisable objects.

The strategist-surface half (prompt template, cold-start vs
incremental framing, held-view evolution columns, D3 carry-forward
removal) is sequenced separately as Plan 2 — see
`docs/Phase10-post-first-backtest/plans/spec-b-plan-2-strategist-surface.md`.

This rewrite supersedes the predecessor that misread §C-Rule 3 and
routed `user:positions` / `user:thesis` through an extended
MemoryWriter.  The misreading and the resulting architecture are
discussed in the change log at the bottom of this file.

---

## Coordination with Plan 2 (strategist surface)

Plan 1 and Plan 2 are independent in code but share the schema
extension (`TickerStance.intent` gains `hold` and `update`).  Plan 1
owns the schema change; Plan 2 reads the new enum members and renders
them in the prompt / validates them in `derivation.py`.

Sequencing:

1. Plan 1 Band 3 lands the schema additions (`PositionThesis`, the
   `intent` enum extension, optional stance fields, and the
   `updated_thesis` → `thesis: str | None` rename on
   `StrategistDecision`).
   Plan 2 cannot start until this lands — its prompt/validation work
   depends on the new vocabulary.
2. Plan 1 Bands 0–2 and 4–5 are orthogonal to Plan 2 and may be
   merged independently.
3. Plan 2's strategist-surface work merges into a tree where Plan 1
   Band 3 has already landed; Plan 2 does not modify the schema.

If Plan 2 lands first by accident (it cannot, because the schema
predicate is unsatisfied), the strategist would emit unknown intent
values into `TickerStance` and fail Pydantic validation.  Treat this
as a hard ordering.

---

## File map

Files this plan touches.  Path · status · why.

| Path | Status | Why |
|------|--------|-----|
| `docs/contract-invariants.md` | edit | §A row repaints; §C-Rule 1/2/7 amendments (Band 0). |
| `docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md` | already edited 2026-05-23 | Spec amended in this same rewrite session (drives Bands 0 / 4). |
| `src/agents/executor/_verb_dispatch.py` | new | Executor-private pure helpers `resolve_broker_call`, `apply_stance_to_thesis` (Band 4). |
| `src/agents/executor/agent.py` | edit | Add `after_agent_callback`; reshape `_run_async_impl` to use `resolve_broker_call`; drop bare-key `state["positions"]` yield (Band 4). |
| `src/agents/strategist/position_thesis.py` | new | `PositionThesis` Pydantic model (Band 3). |
| `src/agents/strategist/schema.py` | edit | Rename `updated_thesis: str` → `thesis: str \| None` on `StrategistDecision` (Band 3). |
| `src/agents/strategist/stance_schema.py` | edit | Extend `TickerStance.intent` enum with `hold`, `update`; add optional verb-conditional fields (Band 3). |
| `src/agents/strategist/prompts.py`, `src/config/strategist.py`, `src/backtest/decision_logger.py`, `src/agents/strategist/agent.py` | edit | `updated_thesis` → `thesis` rename sweep; config field `updated_thesis_max_chars` → `thesis_max_chars`; LLM-facing prompt fragment updated (Band 3). |
| `src/agents/memory/writer.py` | edit | Drop the pre-spec bare-key `thesis` write at lines 167 + 187; rename the `decision.updated_thesis` read to `decision.thesis` (Bands 3, 4); leave `memory_buffer` / `day_digest` paths untouched. |
| `src/orchestrator/persistence.py` | edit | Parameterise `make_session_service(db_url=…)` (Band 2). |
| `src/orchestrator/tick.py` | edit | Drop bare-key `positions` / `thesis` seeds from `_build_initial_state`; mode-dispatch `app_name` (Band 2). |
| `src/orchestrator/pipeline.py` | edit | Verb-aware risk-gate skip rule (Band 4). |
| `src/backtest/driver.py` | edit | Switch session-service factory; rename `_trace` / `_decision_logger` to `temp:` prefix; move handle injection to direct mutation post-`create_session` (Bands 1–2). |
| `src/backtest/runner.py` | edit | Wire per-run sqlite path; delete `runs/<run-id>/session.sqlite` on `--fresh` (Band 2). |
| `src/observability/trace.py` | edit | Rename read sites for the new `temp:_trace` / `temp:_decision_logger` keys (Band 1). |
| `src/agents/analysts/**/fetch*.py`, `src/agents/strategist/{agent.py,context_shim.py}`, `src/agents/executor/agent.py` | edit | ~12 read sites of `state["_trace"]` → `state["temp:_trace"]` and `state["_decision_logger"]` → `state["temp:_decision_logger"]` (Band 1). |
| `tests/fixtures/position_thesis_v1.json` | new | Frozen V1 wire shape for the schema-evolution test (Band 3). |
| `tests/unit/agents/strategist/test_position_thesis.py` | new | `PositionThesis` round-trip / enum-validation / V1-fixture tests (Band 3). |
| `tests/unit/agents/strategist/test_ticker_stance_validation.py` | new | Per-verb required-field validator tests (Band 3). |
| `tests/unit/agents/executor/test_verb_dispatch.py` | new | `resolve_broker_call` and `apply_stance_to_thesis` pure-function tests (Band 4). |
| `tests/unit/agents/executor/test_thesis_writer_callback.py` | new | `_executor_thesis_writer_callback` callback tests (Band 4). |
| `tests/unit/agents/executor/test_executor.py` | edit | Update `_run_async_impl` yield assertions — `executions` + `last_executed_tick_id` only; drop `state["positions"]` expectations (Band 4). |
| `tests/unit/orchestrator/test_risk_gate.py` | edit | Verb-aware skip rule tests (Band 4). |
| `tests/integration/backtest/test_end_to_end_smoke.py` | edit | Re-green against `DatabaseSessionService`; verify `_trace` / `_decision_logger` no longer in persisted state (Bands 1–2). |
| `tests/integration/test_thesis_persistence_round_trip.py` | new | DB round-trip of `user:positions` across sessions (Band 5). |
| `tests/integration/test_namespace_partitioning.py` | new | Paper vs live `app_name` isolation (Band 5). |
| `tests/integration/test_phase2_hydration_from_db_only.py` | new | Re-instantiated session service hydrates from DB row (Band 5). |
| `tests/integration/test_state_delta_user_prefix_end_to_end.py` | new | Minimal pipeline → after-callback auto-yield → DB persistence (Band 5). |
| `graphify-out/graph_delta.md` | append | Dated entry per "Implementation notes / graphify-out delta" in the spec (Band 5 close-out). |

Not touched in this plan (deferred — see "Out of scope"):

- `src/agents/strategist/derivation.py` — D3 carry-forward removal
  lives in Plan 2.
- `src/agents/strategist/derivation.py` — `validate_lifecycle_contract`
  cleanup (separate refactor PR).
- Strategist `new_positions` field deletion (separate refactor PR).
- `src/agents/strategist/prompts.py` — cold-start / incremental
  templates land in Plan 2.

---

## Implementation order

Seven bands, sequenced.  Each band ends green
(`pytest tests/ -v`, `ruff check src/`, `mypy src/`).  No band depends
on a later band's work for its green-bar.

| Band | Theme | Why this order |
|------|-------|----------------|
| 0 | Contract amendments | All later bands rely on the amended invariants — landing them first removes "is this conformant?" friction in code review. |
| 1 | Runtime observability handles → `temp:` | Must precede Band 2; otherwise the DatabaseSessionService switch breaks the backtest smoke test on `TraceWriter` serialisation. |
| 2 | Persistence wiring (DatabaseSessionService + `app_name` dispatch) | Bands 3+ need a real persistence backend for their integration tests. |
| 3 | Schema additions (`PositionThesis`, new intents, `thesis` rename) | Band 4's Executor callback imports the schema. |
| 4 | Executor as writer of `user:positions` / `user:thesis` | Depends on Bands 1–3.  The architectural centre of the plan. |
| 5 | Test migration + integration tests | Green-bar pass for Bands 0–4; graphify delta. |
| 6 | Surface cleanup (Plan 2 prerequisites) | Deletes `new_positions`, `validate_lifecycle_contract`, `risk_gate/lifecycle.py`; relocates `StrategistContractViolation`.  Lands after Band 5 so the green-bar already covers the writer-collapse before legacy surface goes. |

Each band is one PR (or, locally, one logical commit).  Bands 3 and 4
are the most substantive; Bands 0, 1, 5 and 6 are small / mechanical.

---

## Band 0 — Contract amendments

**Goal.** Make `docs/contract-invariants.md` reflect Spec B's
persistence model and observability-handle rebadging so the rest of
the plan can cite the amended doc, not the pre-Spec-B doc.

**Why first.** All later bands write code that the amended invariants
sanction.  Landing the amendments first lets reviewers verify each
subsequent diff against a stable reference.

### Task 0.1 — §A schema row repaints

In `docs/contract-invariants.md` find the `positions` and `thesis`
rows in the §A "Field schema" table (today around lines 76–79).
Replace with the amended rows per the spec's "Contract amendments /
§A schema" section.

The amended cells (verbatim from the spec):

- Row `state["user:positions"]`:
  - **Owner**: `Executor's after_agent_callback†`
  - **Lifetime**: `cross-tick (user-scoped)`
  - **Source**: `ADK DatabaseSessionService user_state table, keyed by (app_name, user_id)`
  - **Refresh**: `Phase 2: implicit ADK merge into the fresh session. Phase 4: callback writes via ctx.state["user:positions"] = ...; ADK's _handle_after_agent_callback auto-yields a state-delta event; DatabaseSessionService.append_event persists it.`
- Row `state["user:thesis"]`: analogous (see spec for exact wording).

Add the `†` footnote below the table — verbatim from the spec.

**Sanity check.** Grep `state\["positions"\]` and
`state\["thesis"\]` across `docs/`.  Both should now have only one
authoritative definition (the §A row) plus glosses elsewhere that
match.

### Task 0.2 — §C-Rule 1 amendment

Add a new sub-section under §C-Rule 1, after the existing in-tick
callback carve-out, titled "Auto-yielded delta-tracked callback writes
(added 2026-05-23, Spec B)".

Body: verbatim from the spec's "Contract amendments / §C-Rule 1" prose
block.  Key claims to preserve:

- `ctx.state[key] = value` writes are delta-tracked
  (`google/adk/sessions/state.py:48-52` — `__setitem__` writes to both
  `_value` and `_delta`).
- ADK's `_handle_after_agent_callback`
  (`google/adk/agents/base_agent.py:489-544`) auto-yields a
  state-delta event from the accumulated delta after the callback
  returns — the `state.has_delta()` check + yield is at lines 538-544.
- That event is ingested by `SessionService.append_event`;
  `DatabaseSessionService` persists `app:` / `user:`-prefixed keys.
- The existing in-tick carve-out covers *direct dict reference
  mutation* (e.g. Strategist's validation callback mutating
  `decision.target_weights = …` on the in-state Pydantic object), not
  delta-tracked `ctx.state[key]` writes.
- Cross-tick `user:`-prefixed writes via this auto-yield path are
  conformant with Rule 1 by construction — the persistence event is
  emitted by ADK on the callback's behalf, not absent.

### Task 0.3 — §C-Rule 2 amendment (`temp:_trace` registration)

Add a new sub-section under §C-Rule 2 titled "Runtime observability
handles ride on `temp:`" (verbatim wording from the spec's
"Contract amendments / §C-Rule 2" block).

Update Rule 2's "Concrete invocation-scoped keys" list (if present —
otherwise add it as a paragraph in the new sub-section) to include
`temp:_trace` and `temp:_decision_logger` alongside the existing
`temp:held_positions_view` / `temp:strategist_mode` etc.

### Task 0.4 — §C-Rule 7 clarification

Add the verbatim §C-Rule 7 clarification paragraph from the spec.  Key
claim: ADK `user:`-prefixed keys *are* the persistence layer for the
StockBot pipeline; reading them from state at Phase 2 and writing via
state-delta at Phase 4 IS the lifecycle pattern Rule 7 anticipates;
no separate hydrator / persister agent is required.

### Task 0.5 — §E persistence-layer table update

In `docs/contract-invariants.md` §E (Cross-session persistence),
update the rows for `positions` and `thesis` to reflect that ADK
`user_state` is now their persistence layer (rather than the
"deferred / followup-design" status they currently hold).  Other §E
rows (`memory_buffer`, `day_digest`) remain unchanged — they stay
followup-design until Spec C lands.

### Band 0 acceptance criteria

- `grep -n "writer-of-record" docs/contract-invariants.md` returns
  references that name **Executor's `after_agent_callback`**, not
  MemoryWriter.
- §A `positions` row reads `state["user:positions"]` with the
  amended owner column.
- §A `thesis` row reads `state["user:thesis"]` with the amended
  owner column.
- §C-Rule 1 has the new sub-section explaining auto-yielded delta-
  tracked writes.
- §C-Rule 2 has the new sub-section registering `temp:_trace` and
  `temp:_decision_logger`.
- §C-Rule 7 has the clarification paragraph.
- §E reflects that `user:positions` / `user:thesis` persistence is
  resolved by ADK user_state (no separate followup-design row).
- `pytest tests/ -v` and `ruff check src/` still green (no code
  changes in Band 0).

### Band 0 commit message

```
docs(contract-invariants): amend §A/§C for Spec B user-state writer

§A: state["positions"] → state["user:positions"], owner = Executor's
after_agent_callback. state["thesis"] → state["user:thesis"], same owner.

§C-Rule 1: clarify that delta-tracked callback writes are auto-yielded
by ADK as state-delta events (base_agent.py:489-544); the in-tick
carve-out covers reference mutation, not these writes.

§C-Rule 2: register temp:_trace and temp:_decision_logger as
invocation-scoped observability handles.

§C-Rule 7: clarify that ADK user_state IS the persistence layer for
user:-prefixed keys; no separate hydrator/persister agent required.

§E: mark positions/thesis persistence as resolved by Spec B.
```

---

## Band 1 — Runtime observability handles → `temp:`

**Goal.** Move the non-serialisable `TraceWriter` and `DecisionLogger`
handles from bare-key state into the `temp:` namespace so they ride
through one tick in `session.state` but never reach the persistence
serialiser.

**Why before Band 2.** Band 2 switches the backtest from
`InMemorySessionService` to `DatabaseSessionService`.  SQLAlchemy's
JSON serialiser cannot round-trip a `TraceWriter`; if Band 2 lands
first the smoke test breaks.

### Task 1.1 — Sweep read sites and rename to `temp:` prefix

Run the following grep to enumerate every read of the two keys:

```bash
grep -rn 'state\["_trace"\]\|state\["_decision_logger"\]\|state\.get("_trace")\|state\.get("_decision_logger")' src/ tests/
```

Expected hits (verified 2026-05-23 — re-run the grep on the day of
implementation if the tree has drifted):

Production read sites:

- `src/agents/analysts/cache_callbacks.py:254`
- `src/agents/analysts/smart_money/agent.py:155`
- `src/agents/analysts/smart_money/fetch.py:137`
- `src/agents/analysts/fundamental/fetch_agent.py:203`
- `src/agents/analysts/technical/fetch.py:92`
- `src/agents/analysts/technical/agent.py:131`
- `src/agents/analysts/social/fetch.py:79`
- `src/agents/analysts/social/agent.py:124`
- `src/agents/analysts/news/fetch_agent.py:114`
- `src/agents/executor/agent.py:195` (comment), `:199` (docstring), `:202` (read site for `_decision_logger`)
- `src/agents/strategist/context_shim.py:173` (comment) — accompanying read needs verifying
- `src/agents/strategist/agent.py:266` (comment) — accompanying read needs verifying
- `src/observability/trace.py:157`, `:249` (actual reads); lines 127, 134, 225 are docstring/comment references that also reword.

Test sites:

- `tests/unit/test_trace_writer_exception_logging.py:36`

For each hit, do the literal rename:

- `state["_trace"]` → `state["temp:_trace"]`
- `state["_decision_logger"]` → `state["temp:_decision_logger"]`
- `state.get("_trace")` → `state.get("temp:_trace")`
- `state.get("_decision_logger")` → `state.get("temp:_decision_logger")`

No accessor helpers, no module-level globals — the rename is the
whole change (per the project's "scale process to task size"
preference).

### Task 1.2 — Move the driver's seed point

`src/backtest/driver.py` currently seeds the handles via:

```python
state["_trace"] = tw                 # ~line 216
state["_decision_logger"] = self._dl # ~line 225
adk_session = await session_service.create_session(
    ...,
    state=dict(state),               # ~line 458-463
    ...,
)
```

This will not work after the rename: `temp:`-prefixed keys passed
through the `state=` seed dict to `create_session` are silently
discarded by `extract_state_delta`
(`google/adk/sessions/_session_util.py:48`).  Confirm with a 3-line
repro script during implementation if there's any doubt.

Replace with:

```python
# Build the seed dict for ADK's create_session.  Any temp:-prefixed
# key here would be discarded by extract_state_delta — observability
# handles are injected post-create_session instead (see below).
seed_state = {
    k: v
    for k, v in state.items()
    if not k.startswith("temp:")
}

adk_session = await session_service.create_session(
    ...,
    state=seed_state,
    ...,
)

# Inject runtime observability handles directly on the live session
# dict.  ADK keeps them in session.state for the duration of this
# invocation, but extract_state_delta / _trim_temp_delta_state strip
# them from any persisted event delta — they never touch the DB.
adk_session.state["temp:_trace"]           = tw
adk_session.state["temp:_decision_logger"] = self._dl
```

Document the discarding gotcha with an inline comment so the next
contributor doesn't try to "tidy up" by moving the handles back into
the seed.

### Task 1.3 — Live `tick.py` (if applicable)

`src/orchestrator/tick.py` currently does **not** inject the handles
(observability lives in the backtest path today — confirm with a
grep).  If a future live-instrumentation change needs them, follow
the same pattern.  No change required in Band 1.

### Task 1.4 — Update existing tests for the rename

Sweep `tests/`:

```bash
grep -rn 'state\["_trace"\]\|state\["_decision_logger"\]' tests/
```

Rename the same way (literal key string).  No semantic test changes —
just the key name.  Confirm fixture builders / mocks that synthesise
`_trace` / `_decision_logger` also rename.

### Band 1 acceptance criteria

- `grep -rn 'state\["_trace"\]\|state\.get("_trace")' src/ tests/`
  returns zero hits.
- Same for `_decision_logger`.
- `pytest tests/ -v` green.  The backtest end-to-end smoke test
  (`tests/integration/backtest/test_end_to_end_smoke.py`) still passes
  on `InMemorySessionService` (Band 2 swaps that).
- `ruff check src/` green.

### Band 1 commit message

```
refactor: rebadge _trace / _decision_logger under temp: prefix

ADK's temp: prefix is invocation-scoped and stripped from persisted
event deltas (extract_state_delta + _trim_temp_delta_state).  Renaming
the two observability handles from bare keys to temp:_trace and
temp:_decision_logger keeps them addressable inside a tick while
making them invisible to any future persistence backend (Band 2 swaps
the backtest to DatabaseSessionService).

Driver seed-point moves from state=... into create_session(...) to
direct mutation of adk_session.state[...] after create_session
returns, because temp:-prefixed keys in the seed dict are silently
discarded by ADK's extract_state_delta.

§C-Rule 2 amendment (Band 0) registers the two keys.
```

---

## Band 2 — Persistence wiring

**Goal.** Replace `InMemorySessionService` with `DatabaseSessionService`
across both backtest and live, parameterise the factory, mode-dispatch
`app_name`, and drop bare-key `positions` / `thesis` seeds.

**Why now.** Bands 3+ rely on a real persistence backend to verify
that `user:`-prefixed writes round-trip.  Band 1 has already cleaned
the non-serialisable handles, so this switch lands without regression.

### Task 2.1 — Parameterise `make_session_service()`

In `src/orchestrator/persistence.py`, find the existing
`make_session_service()` factory.  Change its signature to accept an
optional `db_url: str | None`:

```python
def make_session_service(
    db_url: str | None = None,
) -> BaseSessionService:
    """Construct a session service for the current process.

    Parameters
    ----------
    db_url
        Optional SQLAlchemy-style DB URL.  When ``None``, falls back
        to the ``DATABASE_URL`` environment variable (live path).
        When supplied, used directly (backtest passes a
        ``sqlite+aiosqlite:///runs/<run-id>/session.sqlite`` URL).

    Returns
    -------
    BaseSessionService
        A configured ``DatabaseSessionService``.  In-memory mode is
        no longer supported by this factory — tests that want an
        in-memory database pass ``sqlite+aiosqlite:///:memory:``.
    """

    resolved = db_url or os.environ.get("DATABASE_URL")

    if not resolved:
        raise RuntimeError(
            "make_session_service: no db_url and no DATABASE_URL env"
        )

    return DatabaseSessionService(db_url=resolved)
```

Add a unit test in `tests/unit/orchestrator/test_persistence.py`
covering the three branches (explicit URL, env-fallback, both missing
→ `RuntimeError`).

### Task 2.2 — Mode-dispatch `app_name` in `tick.py`

In `src/orchestrator/tick.py:179, 217` (verify line numbers — they
may drift), the hardcoded `app_name="StockBot"` is replaced by a
mode-dispatched value:

```python
def _dispatch_app_name(broker_mode: BrokerMode) -> str:
    """Return the ADK app_name for the current broker mode.

    Parameters
    ----------
    broker_mode
        ``BrokerMode.LIVE`` or ``BrokerMode.PAPER`` — read from the
        broker layer configuration.

    Returns
    -------
    str
        ``"StockBot-live"`` or ``"StockBot-paper"``.  These values
        partition the ADK user_state table so paper and live
        portfolios cannot share thesis rows.  Backtest uses a third
        value, ``f"StockBot-backtest-{window.id}"``, set in the
        backtest driver / runner — tick.py does not handle that path.
    """

    match broker_mode:
        case BrokerMode.LIVE:
            return "StockBot-live"
        case BrokerMode.PAPER:
            return "StockBot-paper"
        case _:
            raise ValueError(f"Unsupported broker mode: {broker_mode!r}")
```

Wire the dispatch helper into both `Runner` constructions
(approximately lines 179, 217).  Keep `user_id="stockbot"` unchanged.

Update the obsolete `docs/todo-fixes.md` item 2.5.3 comment at lines
67–69 of `tick.py` to point at Spec B
(`docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md`)
instead of the old design doc.

### Task 2.3 — Drop bare-key `positions` / `thesis` from `_build_initial_state`

In `src/orchestrator/tick.py:91–116` (`_build_initial_state`), remove
the lines:

```python
"positions": {},
"thesis":    "",
```

ADK's user_state merge populates `state["user:positions"]` and
`state["user:thesis"]` automatically when the fresh session is
created.  Add an inline comment noting that the pre-Spec-B seed has
moved to ADK user_state and citing the spec.

### Task 2.4 — Update `TickState` typing

`src/orchestrator/state.py` carries a `TickState` typed dict (or
Pydantic model — verify).  Remove the `positions` and `thesis`
entries — they have migrated to user scope and are addressed via
prefixed keys directly on `session.state`, not on `TickState`.

If anything reads `TickState.positions` after this change, the type
checker will fail loudly.  That is the point — those read sites need
to migrate to `session.state["user:positions"]`.

### Task 2.5 — Switch backtest driver

In `src/backtest/driver.py`, find the existing factory call (likely
`InMemorySessionService(...)`) and replace with a call to
`make_session_service(db_url=…)` using the per-run sqlite URL:

```python
db_url           = f"sqlite+aiosqlite:///runs/{run_id}/session.sqlite"
session_service  = make_session_service(db_url=db_url)
```

Set the `app_name` to `f"StockBot-backtest-{window.id}"` (the same
template the spec specifies).  `user_id` stays `"stockbot"` — per-run
isolation comes from the per-run sqlite file plus the per-window
`app_name`, not from `user_id` variation.

Remove the post-tick `state.update(dict(updated.state))` carry at
lines ~251–253 *for the `positions` key*.  Other keys in that carry
are reviewed during implementation: keep what is genuinely per-process
pipeline state; migrate to `temp:` if they ought never persist; drop
if they are dead.  Document each decision in the diff.

### Task 2.6 — `--fresh` cleanup in the runner

In `src/backtest/runner.py`, the existing `--fresh` flag deletes
`runs/<run-id>/` (or some subset of it).  Confirm that
`runs/<run-id>/session.sqlite` is in the deletion scope; if not, add
it.  Re-running a window with `--fresh` MUST start from an empty
`user_state` row — otherwise prior-run thesis leaks into the new run.

Add a regression test in
`tests/integration/backtest/test_fresh_run_starts_clean.py`:

1. Run a one-tick mock backtest with a stub LLM that opens AVGO.
2. Verify `runs/<run-id>/session.sqlite` exists.
3. Re-run the same window with `--fresh`.
4. Assert `state["user:positions"]` is empty at the start of tick 1.

### Task 2.7 — Update existing driver tests

Sweep `tests/integration/backtest/`:

```bash
grep -rn "InMemorySessionService" tests/
```

Replace mocks with a real `DatabaseSessionService` pointed at
`sqlite+aiosqlite:///:memory:`.  Cleaner test fixture; matches the
production path.

### Band 2 acceptance criteria

- `make_session_service()` accepts `db_url` and falls back to
  `DATABASE_URL`; unit test covers all three branches.
- `tick.py` dispatches `app_name` to `"StockBot-live"` /
  `"StockBot-paper"`; no more hardcoded `"StockBot"`.
- `_build_initial_state` no longer seeds `positions` / `thesis`.
- `TickState` no longer carries `positions` / `thesis` fields.
- Backtest uses `DatabaseSessionService` with the per-window
  `app_name`.
- `--fresh` deletes `runs/<run-id>/session.sqlite`.
- `tests/integration/backtest/test_end_to_end_smoke.py` is green
  against `DatabaseSessionService`.
- `pytest tests/ -v` green.

### Band 2 commit message

```
feat(persistence): switch to DatabaseSessionService + namespace partition

Parameterises make_session_service(db_url=…) for symmetric live and
backtest paths.  Backtest uses per-run sqlite at
runs/<run-id>/session.sqlite; live falls back to DATABASE_URL.

app_name is now mode-dispatched: "StockBot-live" / "StockBot-paper" for
the two broker modes, "StockBot-backtest-{window.id}" for backtest.
Paper and live user_state rows are structurally disjoint.

_build_initial_state no longer seeds positions / thesis bare keys —
ADK's user_state merge populates user:positions / user:thesis on
session create.

--fresh rerun deletes runs/<run-id>/session.sqlite so re-running a
window cannot inherit prior-run thesis.
```

---

## Band 3 — Schema additions (slim)

**Goal.** Land the `PositionThesis` model, the new `intent` enum
members (`hold`, `update`), the verb-conditional `TickerStance`
fields, and rename `StrategistDecision.updated_thesis: str` to
`thesis: str | None` (explicit `None` = carry the prior persisted
thesis forward) — with the schema-evolution discipline (frozen V1
fixture) in place.

**Slim.** No shared `_verb_dispatch.py` module in this band; that
lives inside Executor's package (Band 4).  No deletion of the
`new_positions` field or `validate_lifecycle_contract` cleanup —
those land in Band 6, after Band 5's green-bar already covers the
writer collapse.

### Task 3.1 — New `PositionThesis` model

Create `src/agents/strategist/position_thesis.py` with the model
defined in the spec's "Schema — `PositionThesis`" section.  Verbatim
copy is fine — the spec's code block is implementation-grade.  Key
points to verify line by line during implementation:

- `Field(...)` descriptions match the spec verbatim (LLM reads these
  in JSON-schema form).
- `Literal["intraday", "swing", "long_term"]` on `horizon`.
- `Literal["open", "add", "trim", "hold", "update"]` on
  `last_reviewed_decision`.
- Both timestamps use `datetime` (UTC by convention — document in the
  docstring).
- The immutable-after-open fields (`opened_at`, `opened_price`,
  `rationale`) carry the "immutable" assertion in their `Field`
  descriptions — Invariant 3 is documented at the schema level.

### Task 3.2 — Extend `TickerStance.intent`

`TickerStance` lives in `src/agents/strategist/stance_schema.py`
(verified 2026-05-23 — `StrategistDecision` lives separately in
`schema.py`).  Add two new members to the `intent` enum/Literal:
`hold` and `update`.

Add the verb-conditional fields with the semantics from the spec's
"Strategist output schema" section:

```python
class TickerStance(BaseModel):
    """One stance per active ticker, emitted by the strategist.

    The strategist emits exactly one stance per ticker in scope.  The
    set of *active* tickers (held positions plus any flat watchlist
    tickers the strategist chooses to open) is determined per tick.
    See contract-invariants.md §A and the Spec B held-view rendering
    for how this set is constructed.
    """

    ticker: str = Field(..., description="...")

    intent: Literal["open", "add", "trim", "close", "hold", "update"] = Field(
        ...,
        description="Stance verb.  See Spec B §'Stance vocabulary'.",
    )

    weight: float | None = Field(
        None,
        description=(
            "Post-stance portfolio weight in [0, 1].  Required for "
            "open/add/trim.  Ignored for close/hold/update."
        ),
    )

    # ---- verb-conditional fields -----------------------------------

    reason: str | None = Field(
        None,
        description=(
            "Required for hold/trim/update — the 'what's changed since "
            "opening' articulation.  Ignored for open/add/close."
        ),
    )

    target_price: float | None = Field(None, description="...")
    stop_price:   float | None = Field(None, description="...")
    catalyst:     str   | None = Field(None, description="...")

    horizon: Literal["intraday", "swing", "long_term"] | None = Field(
        None,
        description=(
            "Required on open (seeds the PositionThesis); optional on "
            "add/update; ignored on trim/close/hold."
        ),
    )

    rationale: str | None = Field(
        None,
        description=(
            "Required on open (FROZEN at entry — Invariant 3).  "
            "Ignored on add/trim/close/hold/update."
        ),
    )
```

**Field validators.** Per the spec's verb-conditional table (under
"Validation rules"), add a Pydantic `model_validator(mode="after")`
that rejects stances missing required fields.  Six match cases — one
per verb.  Reject messages should name the violated rule and (where
possible) suggest the alternative verb.

### Task 3.3 — Rename `StrategistDecision.updated_thesis` → `thesis: str | None`

`StrategistDecision` already carries an `updated_thesis: str` field
(read by `src/agents/memory/writer.py:158, 160` as today's carry-
forward write path).  Spec B's after-callback reads more cleanly with
an explicit nullable: rename to `thesis: str | None`, with `None` as
the explicit "carry forward" sentinel.

The naming distinction (locked 2026-05-23 with user):

- **Prompt-level (verb, action)**: "Update your thesis if your view
  has shifted — emit the new text under `thesis`, or omit to carry
  the standing thesis forward."  The verb "update" lives in the
  instructions to the LLM.
- **Schema field (noun)**: `decision.thesis: str | None`.  Optional.
- **Config bound (noun + cap)**: `thesis_max_chars` (renamed from
  `updated_thesis_max_chars`).
- **Persisted state (noun, scoped)**: `state["user:thesis"]`.

Other `StrategistDecision` fields stay untouched in this band — no
deletion of `target_weights`, `close_reasons`, `trim_reasons` (the
existing Strategist `_strategist_validation_callback` continues to
derive them from `stances`).  `new_positions` is deleted in **Band 6**.

```python
class StrategistDecision(BaseModel):
    """Top-level strategist output for one tick."""

    stances: list[TickerStance] = Field(default_factory=list)

    thesis: str | None = Field(
        None,
        description=(
            "Optional standing market thesis update.  When non-null, "
            "Executor's after_agent_callback writes the new text to "
            "state['user:thesis'].  When None, the prior user:thesis "
            "is carried forward."
        ),
        max_length=_schema_cap(_DECISION.thesis_max_chars),
    )

    # ---- existing legacy fields (unchanged this band) --------------
    # target_weights, close_reasons, trim_reasons stay as the
    # Strategist validation callback wrote them today.  new_positions
    # is deleted in Band 6 (after Executor's after-callback writes
    # user:positions, the field becomes redundant).
    ...
```

**Rename surface (~25 hits across ~12 files — sweep with
`grep -rn "updated_thesis" src/ tests/`).**

Production:

- `src/agents/strategist/schema.py:90` — field definition (this task)
- `src/agents/memory/writer.py:158, 160` — read sites (Band 4 drops
  these in the `thesis` write deletion; Band 3 just renames the dict
  key + attribute access)
- `src/agents/strategist/prompts.py:129, 156` — LLM-facing field
  description + `{{DECISION_THESIS_MAX}}` placeholder source
- `src/agents/strategist/agent.py:61, 73, 78` — docstring + log
  format string + log argument
- `src/config/strategist.py:3, 71, 72, 77` — config field name
  `updated_thesis_max_chars` → `thesis_max_chars`

Backtest:

- `src/backtest/decision_logger.py:173, 342, 349` — JSON snapshot
  field name `"updated_thesis"` → `"thesis"`

Tests (~7 files, ~15 hits — all literal `updated_thesis="…"`
constructor args and dict keys):

- `tests/integration/test_memory_writer_integration.py`
- `tests/integration/test_risk_gate_agent.py`
- `tests/integration/test_risk_gate_state_delta.py`
- `tests/integration/backtest/test_end_to_end_smoke.py`
- `tests/unit/test_strategist_schema.py`
- `tests/unit/agents/strategist/test_decision_schema_v2.py`
- `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`

**Prompt template change.** `src/agents/strategist/prompts.py:129`
today reads:

```
- updated_thesis (decision-level): ≤{{DECISION_THESIS_MAX}} chars.
```

Replace with:

```
- thesis (decision-level, optional): ≤{{DECISION_THESIS_MAX}} chars.
  Emit the new standing thesis text only if your view has shifted;
  omit/null to carry the existing standing thesis forward.
```

This is the only LLM-facing rewording in Band 3 — the new framing
matches the "noun is the noun" naming choice.

### Task 3.4 — Frozen V1 fixture + schema-evolution test

Create `tests/fixtures/position_thesis_v1.json` containing one
canonical serialised `PositionThesis` (every field populated).  This
is the "frozen V1 wire shape" the spec calls for.

Add a test in
`tests/unit/agents/strategist/test_position_thesis.py`:

```python
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.strategist.position_thesis import PositionThesis


FIXTURE_PATH = (
    Path(__file__).parents[3]
    / "fixtures" / "position_thesis_v1.json"
)


def test_position_thesis_round_trips_through_json():
    """Round-trip a populated row through model_dump / model_validate."""

    fixture = json.loads(FIXTURE_PATH.read_text())
    thesis  = PositionThesis.model_validate(fixture)

    restored = PositionThesis.model_validate_json(thesis.model_dump_json())
    assert restored == thesis


def test_position_thesis_horizon_validates_enum():
    """A bad horizon value must raise ValidationError."""

    fixture = json.loads(FIXTURE_PATH.read_text())
    fixture["horizon"] = "bogus"

    with pytest.raises(ValidationError):
        PositionThesis.model_validate(fixture)


def test_position_thesis_v1_frozen_payload_deserialises():
    """The V1 wire shape MUST deserialise with the current code.

    Adding a new field is OK if and only if it has a default.  This
    test is the gate: if you add a field without a default, the
    fixture stops deserialising and you get a loud failure at PR
    time.
    """

    fixture = json.loads(FIXTURE_PATH.read_text())
    thesis  = PositionThesis.model_validate(fixture)

    # Spot-check immutable fields survived round-trip.
    assert thesis.opened_price > 0
    assert thesis.rationale != ""
```

### Task 3.5 — Stance validator tests

In a new file
`tests/unit/agents/strategist/test_ticker_stance_validation.py`,
cover the per-verb required-field rules:

- `open` requires `weight`, `target_price`, `stop_price`, `catalyst`,
  `horizon`, `rationale` — missing any → `ValidationError`.
- `add` requires `weight` only.
- `trim` requires `weight` and `reason`.
- `close` requires nothing other than `ticker`/`intent`.
- `hold` requires `reason`.
- `update` requires `reason` plus at least one of `target_price`,
  `stop_price`, `catalyst`, `horizon`.

One test per verb plus one parametrised test for the missing-field
matrix.

### Band 3 acceptance criteria

- `PositionThesis` importable from
  `agents.strategist.position_thesis`.
- `TickerStance.intent` accepts `hold`, `update` plus the existing
  four verbs.
- `StrategistDecision.updated_thesis` is renamed to
  `thesis: str | None`; `grep -rn "updated_thesis" src/ tests/` returns
  zero hits.
- `tests/fixtures/position_thesis_v1.json` exists.
- `pytest tests/unit/agents/strategist/test_position_thesis.py
  tests/unit/agents/strategist/test_ticker_stance_validation.py -v`
  all green.
- `pytest tests/ -v` green overall (Bands 0–2 not regressed).
- `ruff check src/` and `mypy src/` green.

### Band 3 commit message

```
feat(strategist): add PositionThesis + hold/update intents + rename updated_thesis → thesis

PositionThesis lives in src/agents/strategist/position_thesis.py with
the field set from Spec B §Schema.  Round-trips through JSON via
model_dump / model_validate.  Frozen V1 fixture at
tests/fixtures/position_thesis_v1.json gates schema evolution: any
field added without a default breaks the fixture deserialise test.

TickerStance.intent enum gains hold and update.  Verb-conditional
fields (reason, target_price, stop_price, catalyst, horizon, rationale)
land with model_validator(mode="after") rejecting stances missing
required fields per the Spec B validation table.

StrategistDecision.updated_thesis (str) is renamed to thesis: str | None.
Explicit None means "carry the prior user:thesis forward".  Executor's
after_agent_callback (Band 4) consumes the new field for the user:thesis
write.  Config field updated_thesis_max_chars is renamed to
thesis_max_chars; decision_logger.py serialises the renamed key.  A
~25-hit sweep across src/ and tests/ rewrites every reference.

target_weights / close_reasons / trim_reasons stay as today —
Strategist's existing in-tick validation callback continues to derive
them.  new_positions is deleted in Band 6 once Plan 2's surface
rewrite has merged.
```

---

## Band 4 — Executor as writer of `user:positions` / `user:thesis`

**Goal.** The architectural centre of the plan.  Land the Executor-
private verb-dispatch helpers, attach the `after_agent_callback` that
assembles and writes `user:positions` / `user:thesis`, drop the
Executor's bare-key `state["positions"]` yield, drop MemoryWriter's
bare-key `thesis` yield, and add the verb-aware risk-gate skip rule.

**Why now.** Bands 0–3 have set up the contract amendments, the
persistence backend, and the schema vocabulary.  Band 4 is where the
new persistence-bearing event actually starts flowing.

### Task 4.1 — Executor-private `_verb_dispatch.py`

Create `src/agents/executor/_verb_dispatch.py` with two pure
functions.

`resolve_broker_call`:

```python
"""Verb-dispatch helpers shared between Executor's run loop and its
after_agent_callback writer.

Both functions are pure — no state mutation, no I/O.  Living inside
the executor package keeps the verb semantics in exactly one place
under the agent that owns both the broker dispatch and the
persistence write.
"""

from datetime import datetime
from typing import Final

from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema  import TickerStance
from broker.calls import BrokerCall  # adapt to actual import path


# Verbs that never produce a broker call.
_NO_TRADE_INTENTS: Final[frozenset[str]] = frozenset({"hold", "update"})


def resolve_broker_call(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
) -> BrokerCall | None:
    """Map a stance to the broker call it requires.

    Parameters
    ----------
    stance
        The risk-gated stance from the strategist.
    prior_row
        The existing ``PositionThesis`` for this ticker (``None`` if
        the ticker is flat).  Required to compute the delta on
        ``add`` / ``trim`` and to size the ``close`` against the
        currently-held weight.

    Returns
    -------
    BrokerCall | None
        ``None`` for ``hold`` and ``update`` (no broker dispatch).
        Otherwise the appropriate ``BrokerCall`` — ``buy`` for
        ``open`` / ``add`` (to ``stance.weight``); ``sell`` for
        ``trim`` (down to ``stance.weight``) and ``close`` (to zero).
    """

    if stance.intent in _NO_TRADE_INTENTS:
        return None

    # ... existing broker-call construction logic (currently lives
    # inside executor/agent.py — lift verbatim and adapt to take
    # prior_row as a parameter) ...
```

`apply_stance_to_thesis`:

```python
def apply_stance_to_thesis(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
    fill_price: float | None,
    tick_id: str,
    as_of: datetime,
) -> PositionThesis | None:
    """Compute the new PositionThesis row for one ticker after a stance.

    Parameters
    ----------
    stance
        The risk-gated stance.
    prior_row
        Existing ``PositionThesis`` (``None`` if ticker was flat).
    fill_price
        Actual fill price from Executor's broker call.  Used as
        ``opened_price`` on ``open`` and to size ``add`` / ``trim``.
        ``None`` for ``hold`` / ``update`` (no broker call ran).
    tick_id
        Identifier for the current tick (for ``last_reviewed_at`` /
        ``opened_tick_id``).
    as_of
        Tick timestamp (UTC) for ``last_reviewed_at`` and
        ``opened_at``.

    Returns
    -------
    PositionThesis | None
        ``None`` when the stance is ``close`` (caller must drop the
        ticker from the new positions dict).  Otherwise the updated
        row.

    Notes
    -----
    Invariant 3: ``rationale`` is FROZEN at open.  ``add`` / ``trim``
    / ``hold`` / ``update`` MUST NOT mutate it.  Tests in
    ``test_verb_dispatch.py`` codify this.
    """

    match stance.intent:

        case "open":
            assert prior_row is None,         "open against held ticker — caller bug"
            assert fill_price is not None,    "open without fill — caller bug"

            return PositionThesis(
                ticker            = stance.ticker,
                opened_at         = as_of,
                opened_tick_id    = tick_id,
                opened_price      = fill_price,
                weight            = stance.weight,
                target_price      = stance.target_price,
                stop_price        = stance.stop_price,
                catalyst          = stance.catalyst,
                horizon           = stance.horizon,
                rationale         = stance.rationale,
                last_reviewed_at  = as_of,
                last_reviewed_decision = "open",
                last_reviewed_reason   = stance.rationale,
            )

        case "add":
            # Weight bump.  Preserve rationale; refresh review fields.
            ...

        case "trim":
            # Weight reduction.  Preserve rationale; refresh review fields.
            ...

        case "close":
            return None  # caller drops the ticker

        case "hold":
            # Review-only update — preserve every commitment field.
            ...

        case "update":
            # Mutate target_price / stop_price / catalyst / horizon
            # where supplied; preserve rationale; refresh review.
            ...
```

Add 10 unit tests in
`tests/unit/agents/executor/test_verb_dispatch.py` — one per
acceptance bullet in the spec's testing section:

- `test_resolve_broker_call_open_returns_buy_to_weight`
- `test_resolve_broker_call_close_returns_sell_all`
- `test_resolve_broker_call_hold_returns_none`
- `test_resolve_broker_call_update_returns_none`
- `test_apply_stance_open_seeds_new_position_with_fill_price`
- `test_apply_stance_hold_touches_review_fields_only`
- `test_apply_stance_update_mutates_target_stop_catalyst_horizon`
- `test_apply_stance_update_does_not_mutate_rationale`
- `test_apply_stance_close_returns_none_signalling_deletion`
- `test_apply_stance_add_preserves_rationale`

### Task 4.2 — Implement `_executor_thesis_writer_callback`

In `src/agents/executor/agent.py`, add the after-callback module-level
function below the `Executor` class:

```python
def _executor_thesis_writer_callback(callback_context):
    """Assemble user:positions / user:thesis from this tick's stances + fills.

    Runs after Executor's ``_run_async_impl`` has yielded its
    broker-effect ``state_delta`` (``executions``,
    ``last_executed_tick_id``).  Reads the just-emitted executions,
    the strategist decision, and the prior ``user:positions`` already
    merged into session state at Phase 2.  Writes the new
    ``user:positions`` and ``user:thesis`` via delta-tracked
    ``ctx.state[key] = value``; ADK's ``_handle_after_agent_callback``
    (base_agent.py:489-544) then auto-yields a state-delta Event from
    the accumulated delta, which the runner ingests through
    ``SessionService.append_event``.  ``DatabaseSessionService``
    persists ``user:``-prefixed keys to the ``user_state`` table.

    See contract-invariants.md §C-Rule 1 amendment (2026-05-23) for
    why this auto-yield path is conformant with Rule 1.

    Returns ``None`` — no re-prompt content (Rule 3).
    """

    state = callback_context.state

    # ---- decision + executions (this tick's outputs) ---------------

    decision   = state["strategist_decision"]
    executions = {
        row["stance"]["ticker"]: row
        for row in state.get("executions", [])
    }

    # ---- prior persisted thesis book (Phase 2 merge) ---------------
    # Shallow copy so we can mutate without affecting the merged dict
    # ADK keeps around for the in-tick view.
    prior_positions: dict[str, dict] = dict(state.get("user:positions", {}))
    new_positions:   dict[str, dict] = dict(prior_positions)

    for stance in decision.stances:

        ticker     = stance.ticker
        fill_price = (executions.get(ticker) or {}).get("fill_price")

        prior_row = (
            PositionThesis.model_validate(prior_positions[ticker])
            if ticker in prior_positions else None
        )

        new_row = apply_stance_to_thesis(
            stance,
            prior_row  = prior_row,
            fill_price = fill_price,
            tick_id    = state["tick_id"],
            as_of      = state["as_of"],
        )

        if new_row is None:
            # close — drop the ticker
            new_positions.pop(ticker, None)
        else:
            new_positions[ticker] = new_row.model_dump(mode="json")

    # ---- thesis carry-forward (explicit re-write) ------------------

    new_thesis = (
        decision.thesis
        if decision.thesis is not None
        else state.get("user:thesis", "")
    )

    # ---- delta-tracked writes — ADK auto-yields the event ----------

    state["user:positions"] = new_positions
    state["user:thesis"]    = new_thesis

    return None
```

Wire it into the constructor.  The class is `ExecutorAgent` (factory:
`build_executor`); the existing constructor takes a `broker` and an
optional `db_session`:

```python
class ExecutorAgent(BaseAgent):
    """..."""

    def __init__(
        self,
        *,
        broker,
        db_session = None,
        name = "executor",
    ):

        super().__init__(
            name = name,
            after_agent_callback = _executor_thesis_writer_callback,
        )

        self._broker     = broker
        self._db_session = db_session


def build_executor(broker, db_session=None) -> ExecutorAgent:
    """Factory used by the pipeline builder to wire in the broker
    and DB session.  Unchanged by Spec B except that the constructed
    ExecutorAgent now registers the after-callback above.
    """

    return ExecutorAgent(broker=broker, db_session=db_session)
```

### Task 4.3 — Refactor `_run_async_impl` to call `resolve_broker_call`

Find the existing broker-dispatch code path in Executor's
`_run_async_impl` and replace the inline verb-dispatch logic with a
call to `resolve_broker_call(...)`.  Keep the `executions` /
`last_executed_tick_id` yield exactly as today.

**Drop the bare-key `state["positions"]` write.** Today's Executor
emits `state_delta={"positions": ..., "executions": ...}` or similar
— grep for `"positions"` in `state_delta=` and `Event(actions=...)`
constructions inside `executor/agent.py`.  After Band 4, the
broker-effect event yields `executions` and `last_executed_tick_id`
only.

### Task 4.4 — Drop MemoryWriter's bare-key `thesis` write

MemoryWriter lives at `src/agents/memory/writer.py` (verified
2026-05-23 — *not* `src/agents/memory_writer/agent.py`).  Today it
writes `"thesis"` in two places:

- Line 167: `state["thesis"] = new_thesis` (in-process mutation).
- Line 187: `"thesis": new_thesis` inside the `EventActions(state_delta={...})`
  yield.

Remove both.  The `decision.thesis` read at lines 157-160 also goes
— the after-callback (Task 4.2) is now the sole consumer of
`decision.thesis`.  Leave the `memory_buffer` / `day_digest` writes
untouched; they remain MemoryWriter's responsibility (Spec C
territory).

Update any existing MemoryWriter unit tests that asserted the
presence of the `thesis` key — drop those assertions / rename to
target the new `user:thesis` writer (Band 5 picks up the test
migration).

### Task 4.5 — Verb-aware risk-gate skip rule

`src/orchestrator/pipeline.py` hosts the risk gate (or wherever the
gate currently lives — confirm via grep).  Add the skip rule:

```python
# Hold and update are no-trade stances — risk caps are irrelevant.
# They pass through unchanged and the executor's _run_async_impl
# skips broker dispatch for them (resolve_broker_call returns None).
_NO_RISK_GATE_INTENTS = frozenset({"hold", "update"})


def _apply_risk_gate(
    decision: StrategistDecision,
    caps: RiskCaps,
) -> StrategistDecision:
    """Apply risk caps to trading stances; pass hold/update through.

    Parameters
    ----------
    decision
        The strategist's pre-gate decision (may include caps to clip).
    caps
        Project-level risk caps loaded from config.

    Returns
    -------
    StrategistDecision
        The gated decision.  Hold and update stances are returned
        unchanged; open/add/trim/close stances are clipped against
        MIN_HELD_WEIGHT / MAX_POSITION_WEIGHT / CASH_FLOOR_WEIGHT /
        MAX_DELTA_PER_TICKER / MAX_TOTAL_TURNOVER.
    """

    gated: list[TickerStance] = []

    for stance in decision.stances:

        if stance.intent in _NO_RISK_GATE_INTENTS:
            gated.append(stance)
            continue

        gated.append(_clip_to_caps(stance, caps))

    return decision.model_copy(update={"stances": gated})
```

Test cases (`tests/unit/orchestrator/test_risk_gate.py`):

- `test_risk_gate_passes_hold_through_unchanged`.
- `test_risk_gate_passes_update_through_unchanged`.
- `test_risk_gate_caps_open_at_max_position_weight`.
- `test_risk_gate_caps_add_at_max_delta_per_ticker`.

### Task 4.6 — Callback unit tests

In `tests/unit/agents/executor/test_thesis_writer_callback.py`, cover
the eight scenarios from the spec's testing section:

```python
import pytest
from datetime import datetime, timezone

from google.adk.agents.callback_context import CallbackContext

from agents.executor.agent import _executor_thesis_writer_callback
from agents.strategist.position_thesis import PositionThesis
from agents.strategist.schema        import StrategistDecision
from agents.strategist.stance_schema import TickerStance


def _make_callback_context(state: dict) -> CallbackContext:
    """Build a CallbackContext whose state is a delta-tracked State
    initialised from the given dict.

    Internal helper — production code constructs the context through
    ADK; tests construct it directly to exercise the callback in
    isolation.
    """

    # ... use the State / CallbackContext fixtures ADK exposes for
    # unit testing; if they're not first-class, build a tiny
    # delta-tracking wrapper around dict that records writes ...
    ...


def test_callback_assembles_new_positions_from_open_stance():
    """An open stance with a fill seeds a new PositionThesis row."""

    state = {
        "tick_id": "t-1",
        "as_of":   datetime(2026, 5, 23, tzinfo=timezone.utc),

        "strategist_decision": StrategistDecision(
            stances = [
                TickerStance(
                    ticker        = "AVGO",
                    intent        = "open",
                    weight        = 0.10,
                    target_price  = 1200.0,
                    stop_price    = 950.0,
                    catalyst      = "Q3 earnings",
                    horizon       = "swing",
                    rationale     = "AI capex thesis intact",
                ),
            ],
            thesis          = None,
        ),

        "executions": [
            {"stance": {"ticker": "AVGO"}, "fill_price": 1023.50},
        ],

        "user:positions": {},
        "user:thesis":    "",
    }

    ctx = _make_callback_context(state)
    _executor_thesis_writer_callback(ctx)

    written = ctx.state["user:positions"]
    assert "AVGO" in written
    assert PositionThesis.model_validate(written["AVGO"]).opened_price == 1023.50


def test_callback_writes_register_in_state_delta():
    """After the callback returns, state has a non-empty delta containing
    both user:positions and user:thesis.

    This is the assertion that proves ADK will auto-yield a state-delta
    event for the writes — see contract-invariants.md §C-Rule 1
    amendment.
    """

    state = {...}  # same shape as above
    ctx = _make_callback_context(state)

    _executor_thesis_writer_callback(ctx)

    assert ctx.state.has_delta()
    delta = ctx._event_actions.state_delta
    assert "user:positions" in delta
    assert "user:thesis"    in delta


def test_callback_returns_none_no_reprompt():
    """Callback returns None — Rule 3 conformance."""

    state = {...}
    ctx = _make_callback_context(state)
    assert _executor_thesis_writer_callback(ctx) is None


# ... five more tests covering carry-forward thesis, overwrite thesis,
# close-deletes-ticker, hold-touches-review-only, fill-price used as
# opened_price ...
```

If `CallbackContext` is awkward to construct directly, an alternative
is to drive the callback via a one-tick ADK `Runner` against an
in-memory session service and inspect `session_service.get_session()`
afterwards.  Whichever is cleaner — the assertions don't change.

### Band 4 acceptance criteria

- `src/agents/executor/_verb_dispatch.py` exists with both helpers.
- Executor's `__init__` registers
  `after_agent_callback=_executor_thesis_writer_callback`.
- Executor's `_run_async_impl` no longer yields a `positions` key in
  any `state_delta`.
- MemoryWriter no longer yields a bare-key `thesis`.
- Risk gate passes `hold` / `update` through unchanged.
- All Band 4 unit tests green.
- `tests/integration/backtest/test_end_to_end_smoke.py` green.
- `pytest tests/ -v` green overall.

### Band 4 commit message

```
feat(executor): writer-of-record for user:positions / user:thesis

Executor's after_agent_callback now assembles the new user:positions
and user:thesis dicts from the just-emitted fills, the strategist's
stances, and the prior persisted user:positions.  Writes land via
delta-tracked ctx.state[…] = … ; ADK's _handle_after_agent_callback
auto-yields a state-delta event from the accumulated delta, and
DatabaseSessionService persists the user:-prefixed keys to the
user_state table.  See contract-invariants.md §C-Rule 1 amendment for
the conformance reasoning.

src/agents/executor/_verb_dispatch.py (new) hosts the pure verb-
dispatch helpers resolve_broker_call and apply_stance_to_thesis.
Private to the executor package — Executor's run loop and after-
callback are the only consumers.

Executor's _run_async_impl now yields only executions /
last_executed_tick_id.  MemoryWriter's bare-key thesis write is
dropped (thesis is now user:thesis, owned by the Executor after-
callback).

Risk gate passes hold / update through unchanged; existing caps apply
to open/add/trim/close as today.
```

---

## Band 5 — Test migration + integration tests

**Goal.** Migrate every pre-existing test that asserts against
bare-key `state["positions"]` or `state["thesis"]` to the new
`user:`-prefixed keys, add the new integration tests called for by
the spec, and append the graphify-out delta entry.

### Task 5.1 — Bulk migrate `state["positions"]` → `state["user:positions"]`

Sweep:

```bash
grep -rn 'state\["positions"\]\|state\["thesis"\]' tests/ src/
```

For each test-side hit, rename the literal-string key.  No semantic
change.

For each src-side hit, check whether the read site has already moved
in Bands 2/4 — any remaining src-side hit is a bug.

### Task 5.2 — Migrate driver tests to `DatabaseSessionService`

`tests/integration/backtest/test_driver*.py` (and any sibling file)
that today mocks `InMemorySessionService` — replace with a real
`DatabaseSessionService` pointed at `sqlite+aiosqlite:///:memory:`.
Read the post-tick state via `session_service.get_session(...)` and
assert against that, not against any in-process dict.

### Task 5.3 — `test_thesis_persistence_round_trip.py`

```python
async def test_thesis_persistence_round_trips_across_sessions():
    """Writing user:positions in session A and reading in session B
    (same app_name + user_id) reproduces the value.

    Verifies ADK DatabaseSessionService merges user_state into every
    new session for the same (app_name, user_id) pair — the Phase 2
    'implicit hydration' step the spec relies on.
    """

    svc = DatabaseSessionService(db_url="sqlite+aiosqlite:///:memory:")

    # Session A: write a position thesis via a stub Executor callback.
    session_a = await svc.create_session(
        app_name = "StockBot-test",
        user_id  = "stockbot",
        state    = {"tick_id": "t-1"},
    )
    session_a.state["user:positions"] = {
        "AVGO": {
            "ticker":         "AVGO",
            "weight":         0.10,
            "opened_at":      "2026-05-23T00:00:00+00:00",
            # ... remaining fields ...
        },
    }
    await svc.append_event(
        session_a,
        Event(
            invocation_id = "iv-1",
            author        = "test",
            actions       = EventActions(state_delta={
                "user:positions": session_a.state["user:positions"],
            }),
        ),
    )

    # Session B: fresh session for the same (app_name, user_id).
    session_b = await svc.create_session(
        app_name = "StockBot-test",
        user_id  = "stockbot",
        state    = {"tick_id": "t-2"},
    )

    assert "AVGO" in session_b.state["user:positions"]
```

### Task 5.4 — `test_namespace_partitioning.py`

Two sessions, same `user_id`, different `app_name`
(`StockBot-paper` vs `StockBot-live`).  Write to one; assert the other
sees an empty `user:positions`.

### Task 5.5 — `test_phase2_hydration_from_db_only.py`

Process A writes `user:positions` via the same flow as Task 5.3.
Tear down the `DatabaseSessionService` instance.  Instantiate a
**fresh** `DatabaseSessionService` against the same sqlite file.
Process B creates a new session for the same `(app_name, user_id)`.
Assert `state["user:positions"]` is the value process A wrote.  This
catches any leftover in-process state from polluting the assertion —
the value MUST come from the DB row.

### Task 5.6 — `test_state_delta_user_prefix_end_to_end.py`

Wire a minimal pipeline: a stub `LlmAgent` that emits a fixed
`StrategistDecision` (one `open` stance, optional `thesis`),
a noop risk gate, and the real `Executor` with its
`after_agent_callback`.  Run one tick against an in-memory
`DatabaseSessionService`.  After the tick, fetch the session via
`session_service.get_session(...)` and assert
`session.state["user:positions"]` and `session.state["user:thesis"]`
hold the expected values.

This is the integration test that proves the auto-yielded event from
`_handle_after_agent_callback` actually flows through
`append_event` and lands in the `user_state` table.

### Task 5.7 — Smoke test (live)

`tests/integration/backtest/test_end_to_end_smoke.py` is already
edited in Band 2 to run on `DatabaseSessionService`.  Add an assertion
after the smoke run:

```python
session = await session_service.get_session(
    app_name   = f"StockBot-backtest-{window.id}",
    user_id    = "stockbot",
    session_id = last_tick_session_id,
)
assert isinstance(session.state["user:positions"], dict)

# Spot-check at least one open occurred during the smoke window:
assert len(session.state["user:positions"]) >= 1
```

### Task 5.8 — Append the `graph_delta.md` entry

After all of Band 5 is green, append the dated entry to
`graphify-out/graph_delta.md` per the spec's "Implementation notes /
graphify-out delta" section (verbatim copy is fine).  Key entries:

- New modules: `src/agents/executor/_verb_dispatch.py`,
  `src/agents/strategist/position_thesis.py`.
- New function: `_executor_thesis_writer_callback` in
  `agents.executor.agent`.
- New `TickerStance.intent` members: `hold`, `update`.
- New call edges (per spec).
- Removed call edges (per spec).
- State-key migrations: `positions` → `user:positions`, `thesis`
  → `user:thesis`, `_trace` → `temp:_trace`, `_decision_logger`
  → `temp:_decision_logger`.

If the delta exceeds ~200 lines, flag and ask the user to run
`/graphify . --update`.

### Band 5 acceptance criteria

- `grep -rn 'state\["positions"\]\|state\["thesis"\]' tests/ src/`
  returns zero hits.
- All five new integration tests green.
- Backtest smoke test green and asserts `user:positions` non-empty.
- `pytest tests/ -v` green overall.
- `ruff check src/` and `mypy src/` green.
- `graphify-out/graph_delta.md` has the dated Spec B entry.

### Band 5 commit message

```
test: migrate to user:-prefixed state keys + integration tests

Bulk-renames state["positions"] and state["thesis"] to their
user:-prefixed forms across tests/.  Backtest driver tests now run on
a real DatabaseSessionService against an in-memory sqlite.

New integration tests:
- test_thesis_persistence_round_trip — cross-session round-trip of
  user:positions for the same (app_name, user_id).
- test_namespace_partitioning — paper vs live app_name disjoint.
- test_phase2_hydration_from_db_only — fresh session service hydrates
  from the DB row, not leftover in-process state.
- test_state_delta_user_prefix_end_to_end — minimal pipeline →
  Executor.after_agent_callback auto-yield → DB persistence.

Backtest smoke test asserts user:positions is populated after the run.

graphify-out/graph_delta.md updated.
```

---

## Band 6 — Surface cleanup (Plan 2 prerequisites)

**Goal.** Land the small mechanical deletions Plan 2 expects from
Plan 1.  Each is a few lines of diff; bundling them here means Plan 2
can be implemented as-written rather than carrying defensive shims
or open coordination questions.

**Why now.** "Co-planned specs trust each other" — Plan 2 already
imports `StrategistContractViolation` from a post-Band-6 location and
expects `new_positions` to be gone.  Leaving these deferred forces
Plan 2 into defensive workarounds and leaves latent legacy surface.

### Task 6.1 — Move `StrategistContractViolation` to `derivation.py`

Today `StrategistContractViolation` lives at
`src/agents/risk_gate/lifecycle.py`.  Plan 2 Task 4 Pass 1.5 is the
only production caller; the function `validate_lifecycle_contract`
in the same file is called only from tests.

Move the exception class to `src/agents/strategist/derivation.py`
(append it to that module's class definitions; preserve the
existing message format and any docstring).  Update the lone
production import site to read:

```python
from agents.strategist.derivation import StrategistContractViolation
```

Sweep test files for the old import:

```bash
grep -rn "from agents.risk_gate.lifecycle import" tests/
```

Rewrite each as `from agents.strategist.derivation import …`.

### Task 6.2 — Delete `validate_lifecycle_contract` + `risk_gate/lifecycle.py`

With `StrategistContractViolation` relocated, the
`validate_lifecycle_contract` function is the only remaining symbol
in `lifecycle.py`.  Plan 2's coordination note at line 1673 records
the audit: it was only ever called from tests, and Plan 1 Task 3.2's
verb-conditional `model_validator(mode="after")` on `TickerStance`
enforces the same invariant at schema-parse time.  Delete the
function and the file:

```bash
git rm src/agents/risk_gate/lifecycle.py
```

Drop the test imports that referenced `validate_lifecycle_contract`
(they should now be either redundant — the same invariant is
exercised through `TickerStance` validation — or refactored to
construct a bad `TickerStance` and assert `ValidationError`).

### Task 6.3 — Delete `StrategistDecision.new_positions`

In `src/agents/strategist/schema.py` (the same module Band 3
edited), delete the `new_positions` field.  Sweep for read sites:

```bash
grep -rn "new_positions" src/ tests/
```

Expected production hits:

- `src/agents/strategist/agent.py` — the in-tick validation
  callback (`_strategist_validation_callback`) currently sets
  `decision.new_positions = [...]` around line 233-236.  Drop the
  assignment.
- `src/agents/strategist/derivation.py` — `DerivedFields` carries
  a parallel `new_positions: list` (verify and drop).
- Anywhere `derive_legacy_fields` constructs a `PositionThesis(...)`
  for the `if action == "open":` arm — remove the constructor and
  the arm.  The opening thesis is now seeded by Executor's after-
  callback (Band 4 Task 4.2) from `apply_stance_to_thesis(...)`,
  using the fill price from `executions[].fill_price` — not from
  derivation-time data.

Test hits: drop assertions that read `decision.new_positions`; if
a test was asserting that opening produced a row in that list,
migrate the assertion to `state["user:positions"]` (Band 5
already covers this migration for the broader `positions` key).

### Task 6.4 — Drop the open-arm constructor from `derive_legacy_fields`

The `if action == "open":` branch in `derive_legacy_fields` today
constructs a `PositionThesis(...)` row with the legacy field set
(`opened_tag`, `last_review_note` — see Plan 2 line 1660).  With
Band 4's after-callback owning thesis assembly, this branch is dead.

Remove the entire `if action == "open":` arm.  The downstream loop
that writes `target_weights` and `decision_tags` runs unconditionally
for every emitted stance (this matches what Plan 2 Task 4 Step 3
expects — see Plan 2 line 1235).

### Band 6 acceptance criteria

- `git ls-files src/agents/risk_gate/lifecycle.py` returns nothing.
- `grep -rn "validate_lifecycle_contract" src/ tests/` returns zero
  hits.
- `grep -rn "new_positions" src/ tests/` returns zero hits (or only
  hits in deferred-feature comments that name-check the historical
  field).
- `grep -rn "from agents.risk_gate.lifecycle" src/ tests/` returns
  zero hits.
- `StrategistContractViolation` is importable from
  `agents.strategist.derivation`.
- `pytest tests/ -v` green.
- `ruff check src/` and `mypy src/` green.

### Band 6 commit message

```
refactor: drop legacy surface superseded by Spec B writer

With Executor's after_agent_callback as the writer-of-record for
user:positions (Band 4), several pre-Spec-B surfaces become dead
code and are deleted in this band so Plan 2 can import cleanly:

- StrategistContractViolation relocates from agents.risk_gate.lifecycle
  to agents.strategist.derivation (its only production caller).
- agents/risk_gate/lifecycle.py is deleted entirely — its sole
  remaining function validate_lifecycle_contract was test-only and
  is superseded by the verb-conditional model_validator on
  TickerStance (Band 3).
- StrategistDecision.new_positions is deleted — the after-callback
  is now the authoritative source of "what got opened this tick"
  via state["user:positions"].
- derive_legacy_fields drops its if action == "open": arm; the
  PositionThesis(...) constructor inside that arm is no longer needed
  (the after-callback assembles thesis rows from fill prices, not
  derivation-time data).

These are the deletions Plan 2 already trusts have landed (see
docs/Phase10-post-first-backtest/plans/spec-b-plan-2-strategist-surface.md
"Coordination notes" section).  Bundling them here keeps the two
plans self-consistent.
```

---

## Out of scope (deferred to follow-on changes)

These items came up during the Spec B critique but are deliberately
*not* included in this plan.  Each is small and orthogonal; bundling
them here would inflate the PR diff and obscure the architectural
change.

### Deferred — Strategist surface cleanup (`target_weights`, `close_reasons`, `trim_reasons`)

`StrategistDecision` retains `target_weights`, `close_reasons`,
`trim_reasons` after Spec B.  The existing in-tick validation
callback still derives them from `stances`.  Eventual deletion (once
all downstream consumers have migrated to reading `stances` directly)
belongs in a follow-up refactor PR.

`new_positions` deletion is **not** deferred — see Band 6.
`validate_lifecycle_contract` deletion is **not** deferred — see
Band 6.

### Deferred — Plan 2 (strategist surface)

The cold-start / incremental prompt framing, the held-view evolution
columns, and the D3 carry-forward removal live in Plan 2.  Plan 2
**depends on** Band 3's schema additions but otherwise has no
coupling with Plan 1.

### Deferred — Spec D (PIT-correctness leak audit)

PIT-correctness work follows the data-fill spec
(`project_backtest_pit_correctness_deferred` in memory).  Not in
scope here.

### Deferred — Reconciliation auto-heal

Invariant 2 says drift between `user:positions` and broker portfolio
is logged but not auto-healed.  A follow-on spec defines
reconciliation semantics.  Not in scope here.

---

## 2026-05-23 — Plan 1 rewrite (notes for future-me)

The predecessor plan was based on a misreading of
`docs/contract-invariants.md` §C-Rule 3.  The misreading claimed
"callbacks cannot yield events"; the rule actually forbids callbacks
from re-prompting (returning `Content` from `before_*_callback`s).
ADK's `_handle_after_agent_callback`
(`google/adk/agents/base_agent.py:489-544`) explicitly auto-yields a
state-delta `Event` when `callback_context.state.has_delta()` after
the callback returns.

The misreading led to:

- A "writer-of-record split" architecture where Strategist decided
  but MemoryWriter wrote `user:positions`.
- A cross-package `src/agents/_verb_dispatch.py` shared module so
  both Executor and MemoryWriter could call into the verb logic.
- An extension of MemoryWriter to assemble `user:positions` from
  Executor's fills — which required reading `state["executions"]`
  in MemoryWriter, awkwardly making MemoryWriter depend on
  Executor's output.

Collapsing the writer back into Executor's `after_agent_callback`:

- Eliminates the cross-package shared module — verb dispatch is
  Executor-private.
- Eliminates the MemoryWriter → Executor data dependency — the
  callback reads `state["executions"]` from the same agent that
  wrote them.
- Reduces the agent count touched by Spec B from two
  (Executor + MemoryWriter) to one (Executor) — MemoryWriter is
  left in place for Spec C.
- Lowers the PR's blast radius without changing the spec's
  user-visible behaviour.

The collapse was triggered during the Band 1 cancellation: the
backtest smoke broke under `DatabaseSessionService` because the
driver seeded a non-serialisable `TraceWriter` into `session.state`.
Investigating that bug surfaced the auto-yielded event mechanism in
ADK, which in turn surfaced the misreading of Rule 3.

The runtime-state fix and the writer-collapse landed in the same
rewrite because they share a root cause: both were
"contract-conformant code paths" we hadn't fully understood in ADK.

Plan 2 is untouched by this rewrite — its prompt-surface work was
always independent of the writer location.

---

## Self-review checklist

Before opening the Band 0 PR:

- [ ] All five amended sections in `docs/contract-invariants.md`
      (§A two rows, §C-Rules 1/2/7, §E) carry the dated 2026-05-23
      marker.
- [ ] No band depends on a later band's code for its green-bar.
- [ ] Every new file in the file map has a `# Purpose:` docstring
      naming the spec section it serves.
- [ ] Every test in Band 5 verifies a finding from the spec's
      "Testing" section (no orphan tests; no missing coverage).
- [ ] `graphify-out/graph_delta.md` entry exists and is dated.
- [ ] No backwards-compatibility shims for the renamed bare-keys —
      `state["positions"]` and `state["_trace"]` are gone outright.

Before opening the Band 4 PR specifically:

- [ ] `_executor_thesis_writer_callback` returns `None` (Rule 3
      conformance, asserted by
      `test_callback_returns_none_no_reprompt`).
- [ ] `callback_context.state.has_delta()` is `True` after the
      callback runs (asserted by
      `test_callback_writes_register_in_state_delta`).
- [ ] The auto-yielded event is observable via `session.events` and
      carries the `user:`-prefixed keys (asserted by
      `test_state_delta_user_prefix_end_to_end`).
- [ ] The `_verb_dispatch.py` helpers are pure (no `await`, no
      module-level state, no I/O).

---

## Execution handoff

When you start a fresh session to execute this plan:

1. Verify the working tree is clean and on `main`.
2. Confirm the spec file
   (`docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md`)
   reflects the 2026-05-23 amendments (§A row repaints, §C-Rule 1
   "Auto-yielded delta-tracked callback writes" sub-section, §C-Rule
   2 "Runtime observability handles ride on `temp:`" sub-section,
   Architecture / "Writer responsibilities" table naming Executor).
3. Open Band 0 first — it is doc-only and unlocks reviewer context
   for everything that follows.
4. Land Bands 1 → 5 sequentially.  Each band lands green.

If you hit a surprise — for example, ADK's `CallbackContext`
construction in tests turns out to be awkward, or
`DatabaseSessionService.create_session` strips a key you didn't
expect — pause and write down the surprise before working around it.
This plan exists because the predecessor rewrite did the opposite,
and we are still paying for it.

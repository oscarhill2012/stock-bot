# Spec B Plan 1 — Memory Backbone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the persistence infrastructure, schema, and writer-of-record split that lets `state["user:positions"]` and `state["user:thesis"]` survive across ticks in both live and backtest, with the verb-dispatch helpers and MemoryWriter / Executor reshape required by Spec B.

**Architecture:** Switch live and backtest from in-memory sessions to ADK's `DatabaseSessionService` and mode-dispatch `app_name` so paper / live / backtest occupy disjoint `user_state` rows. Introduce pure verb-dispatch helpers (`resolve_broker_call`, `apply_stance_to_thesis`) consumed by both Executor (broker calls only) and MemoryWriter (cross-tick `user:positions` / `user:thesis` assembly). Extend `TickerStance` with the six lifecycle verbs and verb-conditional fields, and freeze a V1 JSON fixture so schema evolution stays disciplined.

**Tech Stack:** Python 3.12, Pydantic v2, Google ADK 1.34 (`DatabaseSessionService`, `EventActions(state_delta=...)`), SQLAlchemy 2 + aiosqlite, pytest, ruff. British English throughout, comment-heavy code, docstrings on every public function.

---

## Coordination with Plan 2 (strategist surface)

Plan 1 owns the **memory backbone** — persistence wiring, schema model, verb dispatch, writer-of-record refactor. Plan 2 owns the **strategist surface** — prompt template, context-shim `temp:strategist_mode` injection, held-view rewrite, derivation D3 carry-forward removal, integration tests asserting prompt diversity across ticks.

The two plans share three load-bearing files. Plan 1 lands the schema edits first; Plan 2 consumes them.

| Shared file | Plan 1 contribution | Plan 2 contribution |
|---|---|---|
| `src/agents/strategist/stance_schema.py` | Adds `intent` enum (six verbs), per-stance verb-conditional fields, and the verb-aware `model_validator`. | Reads the schema; uses it inside the strategist prompt + derivation refactor. |
| `src/agents/strategist/schema.py` | Adds `thesis_revision: str \| None` to `StrategistDecision`; deletes the colliding pre-existing `PositionThesis` class (lines 36-68) after migrating import sites to the new `position_thesis.py` module; **also drops the `new_positions: dict[str, PositionThesis]` field — Spec B makes MemoryWriter the writer-of-record for `user:positions`, so the derived dict on the decision is redundant (Task 8b)**. | Consumes `thesis_revision` in the prompt + cold-start / incremental framing. |
| `src/agents/strategist/derivation.py` | Updates `derive_legacy_fields` import path for the moved `PositionThesis` (Task 8); **also removes the `PositionThesis(...)` constructor block and the `new_positions` field from `DerivedFields` — the strategist no longer has an honest `opened_price` to stamp pre-fill, and MemoryWriter assembles `user:positions` from stances + executions (Task 8b)**. | Deletes the D3 carry-forward block at lines 254-271; adds the "stance required per held" post-condition. |

**Ordering:** Plan 1 should land Band 3 (schema + verb dispatch) before Plan 2 starts work on derivation / prompt — the verb enum is a precondition. The two plans can otherwise be implemented and tested concurrently. The integration tests in Plan 1 Band 5 (memory-backbone integration) and Plan 2 (prompt-diversity integration) are independent and can be authored in parallel.

---

## File Map

### Created

| Path | Responsibility |
|---|---|
| `src/agents/strategist/position_thesis.py` | New Pydantic v2 `PositionThesis` model — the V1 thesis-book row shape (immutable rationale, mutable commitments, review trail). Replaces the colliding model in `schema.py`. |
| `src/agents/_verb_dispatch.py` | Pure functions `resolve_broker_call(stance, prior_row) -> BrokerCall \| None` and `apply_stance_to_thesis(stance, prior_row, fill_price, tick_id, as_of) -> PositionThesis \| None`. Imported by Executor and MemoryWriter so verb semantics live in exactly one place. |
| `tests/fixtures/position_thesis_v1.json` | Frozen V1 wire-shape fixture; loaded by the schema-evolution test. |
| `tests/unit/agents/strategist/test_position_thesis.py` | Round-trip, enum validation, and V1 fixture deserialisation tests. |
| `tests/unit/agents/test_verb_dispatch.py` | Coverage for the pure verb-dispatch helpers (no agent wiring). |
| `tests/unit/agents/memory_writer/test_memory_writer.py` | Coverage for MemoryWriter's new `user:positions` + `user:thesis` assembly. |
| `tests/unit/agents/executor/test_executor_state_delta_keys.py` | Asserts Executor's `state_delta` carries only `executions` + `last_executed_tick_id` (no `positions` / `user:positions` / `user:thesis`). |
| `tests/unit/orchestrator/test_make_session_service.py` | Coverage for the parameterised `make_session_service()` (dev / prod / backtest dispatch). |
| `tests/integration/test_thesis_persistence_round_trip.py` | End-to-end: write `user:positions` via `state_delta` in session A; read it back from a fresh session B for the same `(app_name, user_id)`. |
| `tests/integration/test_namespace_partitioning.py` | Two sessions with the same `user_id` but different `app_name` see disjoint `user:positions`. |
| `tests/integration/test_phase2_hydration_from_db_only.py` | Tear down the SessionService in-process; instantiate a fresh one against the same SQLite file; assert `user:positions` is the value the prior process wrote. |

### Modified

| Path | Change |
|---|---|
| `src/orchestrator/persistence.py` | Parameterise `make_session_service(db_url: str \| None = None)` so backtest can inject a per-run SQLite path; dev / prod dispatch preserved as defaults. |
| `src/orchestrator/tick.py` | Mode-dispatch `app_name` (`"StockBot-live"` / `"StockBot-paper"`); drop `positions` and `thesis` seeds from `_build_initial_state`; update the obsolete 2.5.3 comment at lines 67-69. |
| `src/backtest/driver.py` | Switch from `InMemorySessionService` to a session service injected by the caller; set `app_name=f"StockBot-backtest-{window_id}"`, `user_id="stockbot"`; delete `state.update(dict(updated.state))` carry. |
| `src/backtest/runner.py` | Build a per-run `DatabaseSessionService` pointed at `runs/<run-id>/session.sqlite`; delete the file when `--fresh` is requested; pass the service into `Driver`; drop `positions` / `thesis` from the initial-state dict. |
| `scripts/backtest_run.py` | Wire `--fresh` so it deletes `runs/<run-id>/session.sqlite` if it exists. |
| `src/agents/strategist/stance_schema.py` | Add `intent: Literal["open","add","trim","close","hold","update"]`; add per-stance `reason: str \| None`; rewrite the `_require_lifecycle_hints_on_nonzero` validator to dispatch on verb instead of weight. |
| `src/agents/strategist/schema.py` | Delete the pre-existing `PositionThesis` (lines 36-68); **delete the `new_positions: dict[str, PositionThesis]` field on `StrategistDecision` (Task 8b — MemoryWriter is the writer-of-record for `user:positions`)**; add `thesis_revision: str \| None` to `StrategistDecision`; update imports to point at `agents.strategist.position_thesis`. |
| `src/agents/strategist/derivation.py` | Update `PositionThesis` import to the new module path (Task 8); **remove the `PositionThesis(...)` constructor block inside `derive_legacy_fields` and drop `new_positions` from `DerivedFields` (Task 8b)**. (D3 carry-forward removal belongs to Plan 2.) |
| `src/agents/strategist/agent.py` | **Delete the `decision.new_positions = derived.new_positions` line in `_after_validation` (Task 8b) — the field no longer exists on `StrategistDecision`.** |
| `src/agents/risk_gate/lifecycle.py` | **Delete the file entirely (Task 8b) — `validate_lifecycle_contract` is dead in production (no agent calls it) and is made redundant by `TickerStance`'s verb-conditional `model_validator` once Task 9 lands.** |
| `src/agents/memory/writer.py` | Extend to write `user:positions` (via `apply_stance_to_thesis`) and `user:thesis` (passthrough of `thesis_revision`); drop the bare-key `thesis` write. |
| `src/agents/executor/agent.py` | Remove `state["positions"]` direct mutation (line 192) and the `positions` key from the yielded `state_delta` (line 229); keep `executions` + `last_executed_tick_id` only. |
| `src/agents/risk_gate/agent.py` | Verb-aware skip rule: `hold` and `update` stances pass through unchanged; only `open`/`add`/`trim`/`close` are clamped. |
| `src/orchestrator/state.py` | Remove `positions` and `thesis` from `TickState` (now `user:`-scoped, lives outside the Pydantic mirror). |
| `docs/contract-invariants.md` | Apply §A row amendments (`positions` → `user:positions`, `thesis` → `user:thesis` with writer-of-record footnote); add §C-Rule 7 clarification paragraph. |
| `tests/integration/test_executor_with_fake_broker.py` | Migrate `state["positions"]` assertions to `state["user:positions"]`. |
| `tests/integration/test_strategist_v2_smoke.py` | Same migration. |
| `tests/unit/orchestrator/test_tick_initial_state.py` | Drop the `positions == {}` and `thesis == ""` seed assertions. |
| `tests/unit/backtest/test_driver_portfolio_refresh.py` | Migrate the `state["positions"]` propagation assertion to `user:positions` via the `DatabaseSessionService`. |
| `tests/unit/executor/test_open_positions_state.py` | Re-target onto MemoryWriter's `user:positions` assembly rather than Executor's `state["positions"]` write. |
| `tests/executor/test_executor_bookkeeping.py` | Same — partial-trim and full-close assertions move to `state["user:positions"]`. |

---

## Implementation Order

Five bands. Each band is a complete vertical slice — schema + tests + commit — so the codebase is green at every boundary. Plan 2's prompt / context-shim work can begin once Band 3 lands.

- **Band 1 — Contract amendments.** Land the doc edits first so subsequent tasks reference the amended contract.
- **Band 2 — Persistence wiring.** Parameterise `make_session_service`, mode-dispatch `app_name`, switch backtest to `DatabaseSessionService`, drop the bare-key seeds. Codebase still uses `state["positions"]` after this band — only the persistence shell has moved.
- **Band 3 — Schema + pure helpers.** New `PositionThesis`, `_verb_dispatch.py`, extended `TickerStance` with verb enum + verb-conditional fields, `thesis_revision`. Retires the now-redundant `new_positions` plumbing from derivation / schema / strategist callback and deletes the dead `validate_lifecycle_contract` (Task 8b). Frozen V1 fixture + schema-evolution test. Pure-Python only — no agent wiring yet.
- **Band 4 — Writer-of-record refactor.** MemoryWriter writes `user:positions` / `user:thesis`; Executor stops writing `positions`; risk-gate gets verb-aware skip rule. Bare-key `state["positions"]` is gone after this band.
- **Band 5 — Test migration.** Migrate the six pre-existing test files from `state["positions"]` to `state["user:positions"]`; migrate driver tests from `InMemorySessionService` mocking to the parameterised real-DB path.

---

## Band 1 — Contract amendments

### Task 1: Amend `docs/contract-invariants.md` §A schema rows

**Files:**
- Modify: `docs/contract-invariants.md` (the §A table near line 76-79; the footer paragraph near line 89-93)

- [ ] **Step 1: Rewrite the `positions` and `thesis` rows under `user:`-prefixed names**

Replace the existing rows (lines 76 + 79) with:

```markdown
| `user:positions` | Strategist (decides) / MemoryWriter (writer-of-record) † | **cross-tick** (user-scoped) | ADK `DatabaseSessionService` `user_state` table, keyed by `(app_name, user_id)` | Phase 2 (read via implicit ADK merge), Phase 4 (write via MemoryWriter `state_delta`) | ADK user_state — see §E. | The *thesis book*. Per-position entry rationale + exit basis. Distinct from `portfolio` (broker truth) — `user:positions` is strategist intent. |
| `user:thesis` | Strategist (decides) / MemoryWriter (writer-of-record) † | **cross-tick** (user-scoped) | ADK `DatabaseSessionService` `user_state` table, keyed by `(app_name, user_id)` | Phase 2 (read via implicit ADK merge), Phase 4 (write via MemoryWriter `state_delta` when `thesis_revision` non-null, else carry-forward) | ADK user_state — see §E. | Strategist's standing market thesis. |
```

The `memory_buffer` and `day_digest` rows are NOT touched in this spec — they migrate to `user:` scope in Spec C.

- [ ] **Step 2: Add the writer-of-record footnote**

Add directly below the §A table (before the existing "The four cross-tick rows…" paragraph):

```markdown
† *Strategist's `LlmAgent` reasons about and produces the thesis content
through its output schema, but cannot itself yield the persistence event
(§C-Rule 3 forbids callbacks from yielding events; `LlmAgent`s route their
entire output blob through a single `output_key`).  MemoryWriter is the
BaseAgent that emits the `state_delta`; it assembles `user:positions` by
applying Strategist's stance verbs to the prior dict plus Executor's fill
data, and passes `user:thesis` through from Strategist's
`thesis_revision` field.*
```

- [ ] **Step 3: Update the "four cross-tick rows" paragraph**

The existing paragraph at line 89-93 enumerates `positions`, `memory_buffer`, `day_digest`, `thesis`. Update the field names so it reads:

```markdown
The four cross-tick rows (`user:positions`, `memory_buffer`, `day_digest`,
`user:thesis`) all depend on the persistence subsystem described in §E.
…
```

- [ ] **Step 4: Commit**

```bash
git add docs/contract-invariants.md
git commit -m "$(cat <<'EOF'
docs(contract): rename positions/thesis rows to user:-prefixed (Spec B Band 1)

Spec B repaints the two thesis-book rows under ADK's user:-prefix and
names MemoryWriter as the writer-of-record.  The footnote captures the
Strategist-decides / MemoryWriter-writes split that follows from
§C-Rule 3 (callbacks cannot yield) + LlmAgent output_key semantics.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 2: Add §C-Rule 7 clarification paragraph

**Files:**
- Modify: `docs/contract-invariants.md` (§C-Rule 7 block, lines 328-345)

- [ ] **Step 1: Append the clarification paragraph**

After the existing "Implication:" sentence on line 344, add:

```markdown
**ADK `user:`-prefixed clarification (Spec B).**  ADK
`user:`-prefixed keys are the persistence layer for the StockBot
pipeline.  Reading them via state IS the lifecycle pattern Rule 7
anticipates — the `DatabaseSessionService` provides the persistence
boundary that pipeline agents do not need to cross directly.  Pipeline
agents read `user:`-prefixed keys from state at Phase 2 and write them
via `state_delta` at Phase 4; ADK persists the writes to the
`user_state` table on event ingestion.  No separate "Phase 2 hydrator"
or "Phase 4 persister" agent is required.
```

- [ ] **Step 2: Commit**

```bash
git add docs/contract-invariants.md
git commit -m "$(cat <<'EOF'
docs(contract): clarify §C-Rule 7 for ADK user:-prefixed persistence (Spec B Band 1)

The DatabaseSessionService user_state table IS the persistence layer
Rule 7 anticipates; pipeline agents reading user:-prefixed keys from
state are already conforming.  No separate hydrator / persister agent
is needed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Band 2 — Persistence wiring

### Task 3: Parameterise `make_session_service()` for per-run SQLite injection

**Files:**
- Modify: `src/orchestrator/persistence.py` (lines 426-447)
- Test: `tests/unit/orchestrator/test_make_session_service.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/orchestrator/test_make_session_service.py
"""make_session_service parameterisation tests.

The factory must support three dispatch modes:
1. Explicit ``db_url`` argument — used by backtest to point at a per-run
   SQLite file under ``runs/<run-id>/session.sqlite``.
2. ``STOCKBOT_ENV=prod`` — reads ``DATABASE_URL`` env var (Postgres in
   deploy).
3. Default dev path — ``./data/stockbot.db`` (preserved for back-compat).
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from google.adk.sessions import DatabaseSessionService

from orchestrator.persistence import make_session_service


def test_make_session_service_accepts_explicit_db_url(tmp_path: Path) -> None:
    """Caller-supplied db_url short-circuits the env-var dispatch."""

    # Build the URL the backtest runner will pass — an aiosqlite path
    # pointing at a fresh per-run SQLite file.
    explicit = f"sqlite+aiosqlite:///{tmp_path / 'session.sqlite'}"

    service = make_session_service(db_url=explicit)

    assert isinstance(service, DatabaseSessionService)


def test_make_session_service_prod_uses_database_url_env(tmp_path: Path) -> None:
    """STOCKBOT_ENV=prod + DATABASE_URL set returns a service against that URL."""

    target = f"sqlite+aiosqlite:///{tmp_path / 'prod.sqlite'}"

    with mock.patch.dict(
        os.environ,
        {"STOCKBOT_ENV": "prod", "DATABASE_URL": target},
        clear=False,
    ):
        service = make_session_service()

    assert isinstance(service, DatabaseSessionService)


def test_make_session_service_dev_default_points_at_local_sqlite(tmp_path: Path) -> None:
    """No args + STOCKBOT_ENV unset (or 'dev') returns the local dev SQLite."""

    with mock.patch.dict(os.environ, {"STOCKBOT_ENV": "dev"}, clear=False), \
         mock.patch("orchestrator.persistence.Path") as mock_path:

        # Re-route the dev data directory into tmp_path so the test doesn't
        # write to the real ./data/ folder.
        mock_path.return_value = tmp_path

        service = make_session_service()

    assert isinstance(service, DatabaseSessionService)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_make_session_service.py -v
```

Expected: FAIL — `make_session_service()` currently takes no arguments, so the `db_url=` kwarg raises `TypeError`.

- [ ] **Step 3: Parameterise `make_session_service()`**

Replace the existing function in `src/orchestrator/persistence.py:426-447` with:

```python
def make_session_service(db_url: str | None = None):
    """Return a DatabaseSessionService for the requested storage backend.

    Dispatch precedence (highest first):
    1. Explicit ``db_url`` argument — used by the backtest runner to
       point ADK at a per-run SQLite file under
       ``runs/<run-id>/session.sqlite``.  Live code paths leave this
       ``None`` and fall through to env-var dispatch.
    2. ``STOCKBOT_ENV=prod`` — reads ``DATABASE_URL`` env var
       (Postgres in deploy).  Raises ``RuntimeError`` if unset.
    3. Default dev path — ``./data/stockbot.db`` (created on demand).

    Args:
        db_url: Optional aiosqlite / Postgres URL.  When supplied, all
            env-var dispatch is skipped.

    Returns:
        A ``DatabaseSessionService`` bound to the chosen URL.
    """

    from google.adk.sessions import DatabaseSessionService

    # Caller-supplied URL wins outright — backtest path.
    if db_url is not None:
        return DatabaseSessionService(db_url=db_url)

    env = os.environ.get("STOCKBOT_ENV", "dev").lower()

    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "STOCKBOT_ENV=prod requires DATABASE_URL to be set."
            )
        return DatabaseSessionService(db_url=url)

    # dev — aiosqlite driver required by DatabaseSessionService (uses an
    # async engine internally).  The data directory is created on demand
    # so a fresh checkout works without manual setup.
    from pathlib import Path

    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    return DatabaseSessionService(
        db_url=f"sqlite+aiosqlite:///{data_dir.absolute()}/stockbot.db",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_make_session_service.py -v
```

Expected: PASS — all three dispatch modes return a `DatabaseSessionService`.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/persistence.py tests/unit/orchestrator/test_make_session_service.py
git commit -m "$(cat <<'EOF'
feat(persistence): parameterise make_session_service for backtest per-run SQLite (Spec B Band 2)

Adds an explicit ``db_url`` kwarg so the backtest runner can point ADK
at ``runs/<run-id>/session.sqlite`` without needing env-var trickery.
Live code paths (tick.py, paper, prod) keep their existing behaviour —
the env-var dispatch is now the fall-through, not the only path.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 4: Mode-dispatch `app_name` in `tick.py` and drop bare-key seeds

**Files:**
- Modify: `src/orchestrator/tick.py` (lines 60-116, 179, 188, 217, 67-69 comment)
- Modify: `tests/unit/orchestrator/test_tick_initial_state.py` (drop the `positions == {}` / `thesis == ""` assertions)

- [ ] **Step 1: Update `tests/unit/orchestrator/test_tick_initial_state.py`**

Drop the assertions referencing the removed seeds:

```python
# tests/unit/orchestrator/test_tick_initial_state.py — remove the two lines:
#     assert state["positions"] == {}
#     assert state.get("thesis") == ""
# Keep the surviving assertions (tick_id, tickers, portfolio, as_of, etc.).
```

Add a new assertion that the seed dict does NOT contain the migrated keys:

```python
def test_initial_state_omits_user_scoped_keys() -> None:
    """positions and thesis are user-scoped now — Phase 2 must NOT seed them.

    ADK's DatabaseSessionService merges the user_state row for
    (app_name, user_id) into the returned state dict; seeding bare-key
    ``positions`` / ``thesis`` here would shadow that merge with an empty
    value.  Verifies the removal at lines 60-116 of tick.py.
    """

    # ... existing fixture set-up that calls _build_initial_state ...

    assert "positions" not in state, (
        "Phase 2 must not seed bare-key 'positions' — Spec B routes the "
        "thesis book through ADK user_state under 'user:positions'."
    )
    assert "thesis" not in state, (
        "Phase 2 must not seed bare-key 'thesis' — Spec B routes the "
        "market thesis through ADK user_state under 'user:thesis'."
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_tick_initial_state.py::test_initial_state_omits_user_scoped_keys -v
```

Expected: FAIL — `_build_initial_state` still seeds `"positions": {}` and `"thesis": ""`.

- [ ] **Step 3: Drop the seeds and mode-dispatch `app_name`**

Edit `src/orchestrator/tick.py`:

In `_build_initial_state` (lines 91-116), remove the four bare-key seeds (`memory_buffer`, `day_digest`, `thesis`, `positions`) — but leave `memory_buffer` / `day_digest` for now, those migrate in Spec C. Final dict:

```python
    return {
        "tick_id": tick_id,
        # Phase 2 lifecycle handshake — the live builder is the single
        # authoritative writer of ``as_of`` and ``tick_phase``.
        "as_of":      datetime.now(tz=UTC),
        "tick_phase": "live",
        "tickers": tickers,
        # ``memory_buffer`` / ``day_digest`` stay bare-keyed until Spec C
        # migrates them to ``user:`` scope.  ``positions`` and ``thesis``
        # have moved to ADK's user_state — read via the implicit merge
        # at session creation, NOT seeded here (Spec B Band 2).
        "memory_buffer": [],
        "day_digest": "",
        "portfolio": portfolio.model_dump(mode="json"),
        "reference_prices": {
            sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
        },
    }
```

Update the obsolete 2.5.3 comment at lines 67-69:

```python
    """Build the initial pipeline state for one live tick.

    Reads the live portfolio from the broker, fetches reference prices,
    and seeds the Phase 2 lifecycle keys (``tick_id``, ``as_of``,
    ``tick_phase``) plus the cross-tick fields the pipeline expects.

    Cross-tick thesis state (``user:positions``, ``user:thesis``) is
    NOT seeded here.  ADK's ``DatabaseSessionService.create_session()``
    merges the ``user_state`` row for the mode-dispatched
    ``(app_name, user_id)`` pair into the returned state dict — that is
    the persistence read.  See ``docs/Phase10-post-first-backtest/specs/
    foundational-thesis-memory.md`` (Spec B) and ``docs/contract-
    invariants.md`` §C-Rule 7.

    Args:
        broker: Any broker implementing ``get_portfolio() -> Portfolio``.
        tick_id: The unique identifier string for this tick.
        tickers: The list of watchlist ticker symbols for this tick.

    Returns:
        A dict containing all keys the pipeline expects at startup,
        including a JSON-serialisable portfolio snapshot under
        ``"portfolio"`` and a wall-clock UTC ``as_of`` datetime under
        ``"as_of"`` (tick_phase is the literal string ``"live"``).
    """
```

Mode-dispatch `app_name`. Add a helper just below the import block (around line 21):

```python
def _resolve_app_name(broker) -> str:
    """Return the ADK ``app_name`` for the current broker mode.

    Paper / live brokers must hit disjoint ``user_state`` rows so a
    practice-account thesis never leaks into a live decision (and vice
    versa).  ``broker.mode`` is the Trading 212 broker's existing flag;
    FakeBroker (used in tests) has no ``mode`` and falls back to a
    dedicated ``"StockBot-test"`` app name.

    Args:
        broker: A Broker implementation; reads ``broker.mode`` if
            present.

    Returns:
        One of ``"StockBot-live"``, ``"StockBot-paper"``, or
        ``"StockBot-test"``.
    """

    mode = getattr(broker, "mode", None)

    if mode == "live":
        return "StockBot-live"

    if mode == "paper":
        return "StockBot-paper"

    # Fallback for FakeBroker / unit tests / anything without a mode flag.
    return "StockBot-test"
```

Then replace the three hardcoded `"StockBot"` references at lines 179, 188, 217 with `app_name`:

```python
    session_service = make_session_service()
    app_name        = _resolve_app_name(broker)
    runner = Runner(
        agent=pipeline,
        app_name=app_name,
        session_service=session_service,
    )

    initial_state = await _build_initial_state(broker, tick_id, tickers)
    adk_session = await session_service.create_session(
        app_name=app_name,
        user_id="stockbot",
        state=initial_state,
    )

    # ... events loop unchanged ...

    updated = await session_service.get_session(
        app_name=app_name,
        user_id="stockbot",
        session_id=adk_session.id,
    )
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_tick_initial_state.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the broader live smoke / tick suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/ tests/integration/test_run_once_smoke.py -v
```

Expected: PASS — no other tests reference the removed seeds yet (they migrate in Band 5).

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/tick.py tests/unit/orchestrator/test_tick_initial_state.py
git commit -m "$(cat <<'EOF'
feat(tick): mode-dispatch app_name + drop user-scoped seeds (Spec B Band 2)

* _build_initial_state no longer seeds positions / thesis — ADK's
  user_state merge populates state["user:positions"] / state["user:thesis"]
  at session creation.
* _resolve_app_name(broker) routes paper / live / test brokers to
  disjoint user_state rows so a practice-account thesis cannot leak
  into a live decision.
* The obsolete docs/todo-fixes.md item 2.5.3 comment is updated to
  point at the Spec B foundational-thesis-memory document.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 5: Switch backtest driver to a caller-injected session service

**Files:**
- Modify: `src/backtest/driver.py` (lines 28, 446, 449, 459-460, 496, 508-509, 513)

- [ ] **Step 1: Update the driver to accept an injected session service**

Replace the `InMemorySessionService` import at line 28 — `DatabaseSessionService` is imported lazily inside the factory instead, so the driver does not import either directly.

In the `Driver.run` method (around line 418-513), replace the hardcoded `InMemorySessionService()` instantiation with a class-level argument:

```python
# In Driver.__init__ (or wherever the class is defined), accept the
# session_service as a constructor argument:

class Driver:
    """Tick-loop driver — runs the live pipeline once per scheduled tick.

    ...
    Args:
        broker: FakeBroker (cache-backed) for backtest replay.
        session_service: ADK SessionService used to create / merge per-tick
            sessions.  Passed by the runner so it can wire a per-run
            DatabaseSessionService backed by ``runs/<run-id>/session.sqlite``.
        app_name: ADK ``app_name`` used for the user_state partition.
            Backtest sets this to ``f"StockBot-backtest-{window_id}"``.
    ...
    """

    def __init__(
        self,
        *,
        broker,
        session_service,
        app_name: str,
        # ... existing kwargs ...
    ) -> None:
        self._broker          = broker
        self._session_service = session_service
        self._app_name        = app_name
        # ... existing assignments ...
```

In the per-tick body (around lines 446-513), replace the four hardcoded `"backtest"` / `"backtest"` references:

```python
        # Previously: session_service = InMemorySessionService()
        # Now uses the injected service; user_id stays "stockbot" so the
        # backtest user_state row keys off (self._app_name, "stockbot").
        session_service = self._session_service
        app_name        = self._app_name
        user_id         = "stockbot"

        adk_session = await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            state=tick_state,
        )

        # ... runner.run_async(...) unchanged but parameterised on user_id ...

        updated = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=adk_session.id,
        )

        # REMOVED: state.update(dict(updated.state))
        # ``user:positions`` / ``user:thesis`` survive the tick via the
        # user_state row that DatabaseSessionService persists on event
        # ingestion.  Bare-key carry was the InMemorySessionService
        # workaround and is no longer needed.
```

- [ ] **Step 2: Update the comment at lines 251-253**

Replace the existing comment block about `state["positions"]` propagation with:

```python
            # Cross-tick thesis state (``user:positions`` / ``user:thesis``)
            # is now persisted by ADK's DatabaseSessionService into the
            # ``user_state`` table at event-ingestion time.  Each tick reads
            # the row back via the implicit merge at create_session(); the
            # previous in-memory ``state.update`` carry has been removed.
            # See Spec B and docs/contract-invariants.md §A.
```

- [ ] **Step 3: Run the driver smoke tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/ tests/integration/test_backtest_smoke.py -v
```

Expected: most tests fail with constructor signature mismatches — this is intentional. The test migration happens in Band 5; for now we accept the breakage and continue building. Capture the failing test names; they become Band 5's worklist.

- [ ] **Step 4: Commit**

```bash
git add src/backtest/driver.py
git commit -m "$(cat <<'EOF'
feat(backtest): inject session service + app_name into Driver (Spec B Band 2)

* Driver constructor now takes session_service and app_name kwargs so
  the runner can wire a per-run DatabaseSessionService against
  runs/<run-id>/session.sqlite and a per-window app_name.
* The bare-key state.update(dict(updated.state)) carry is removed —
  user:positions and user:thesis ride ADK's user_state table now.
* Driver tests are intentionally left red — they migrate in Band 5
  alongside the test-suite shift to state["user:positions"].

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 6: Wire per-run SQLite + `--fresh` deletion in the runner

**Files:**
- Modify: `src/backtest/runner.py` (around lines 445-475 for the Driver wiring, 514-534 for the seed dict)
- Modify: `scripts/backtest_run.py` (add `--fresh` flag handling)

- [ ] **Step 1: Add per-run session service construction in `runner.py`**

In the run loop (around line 445-475), build the session service alongside the existing `db_session`:

```python
            # Existing trade-log DB session — unchanged.
            engine     = make_engine(f"sqlite:///{run_dir / 'db.sqlite'}")
            create_all(engine)
            Session    = sessionmaker(bind=engine)
            db_session = Session()

            # New per-run ADK session service — DatabaseSessionService
            # pointed at ``session.sqlite`` inside the same run dir.  Each
            # window gets its own user_state row keyed by app_name; each
            # run within a window writes into a fresh sqlite file when
            # ``--fresh`` is passed (see CLI handling below).
            session_db_path = run_dir / "session.sqlite"
            session_service = make_session_service(
                db_url=f"sqlite+aiosqlite:///{session_db_path.absolute()}",
            )

            app_name = f"StockBot-backtest-{window.id}"
```

Pass both into the Driver:

```python
            driver = Driver(
                broker          = fake_broker,
                session_service = session_service,
                app_name        = app_name,
                # ... existing kwargs ...
                db_session      = db_session,
            )
```

- [ ] **Step 2: Drop `positions` / `thesis` from the seed dict (lines 514-534)**

```python
            state: dict = {
                "tickers":          wl_filtered,
                "portfolio":        portfolio.model_dump(mode="json"),
                # ``memory_buffer`` / ``day_digest`` stay bare-keyed until
                # Spec C migrates them to ``user:`` scope.  ``positions``
                # and ``thesis`` have moved to ADK's user_state — read via
                # the implicit merge at session creation, NOT seeded here.
                "memory_buffer":    [],
                "day_digest":       "",
                "reference_prices": {
                    sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
                },
            }
```

- [ ] **Step 3: Add `--fresh` SQLite deletion in `runner.py`**

In the runner's main entry (the function that takes `fresh: bool` from the CLI), before the run loop builds the session service:

```python
            # ``--fresh`` resets the per-run thesis store so a re-run cannot
            # inherit the prior run's user_state row.  Per the user's
            # destructive-ops feedback memory, this deletion is gated on
            # the explicit CLI flag — never the default.
            if fresh and session_db_path.exists():
                logger.info(
                    "removing existing session.sqlite (--fresh): %s",
                    session_db_path,
                )
                session_db_path.unlink()
```

- [ ] **Step 4: Wire `--fresh` into `scripts/backtest_run.py`**

Add or confirm the CLI flag exists and propagates into the runner:

```python
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Delete runs/<run-id>/session.sqlite before the run so ADK's "
            "user_state starts empty.  Without this flag, re-running the "
            "same window resumes from the prior thesis book."
        ),
    )
```

Pass it through to whatever runner function the CLI calls.

- [ ] **Step 5: Run the runner smoke test**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_backtest_smoke.py -v
```

Expected: still failing (Band 5 cleanup pending) but no NEW failures introduced by the runner edits — the failures should match the set captured in Task 5 Step 3.

- [ ] **Step 6: Commit**

```bash
git add src/backtest/runner.py scripts/backtest_run.py
git commit -m "$(cat <<'EOF'
feat(backtest): per-run DatabaseSessionService + --fresh deletion (Spec B Band 2)

* runner.py builds a DatabaseSessionService pointed at
  runs/<run-id>/session.sqlite per run and threads it into the Driver.
* app_name is set to f"StockBot-backtest-{window.id}" so different
  windows occupy disjoint user_state rows.
* --fresh deletes the per-run session.sqlite up-front so a re-run of
  the same window cannot inherit prior thesis state.
* Seed dict no longer contains positions / thesis — ADK user_state
  hydrates them on create_session().

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Band 3 — Schema + pure helpers

### Task 7: Add the new `PositionThesis` model

**Files:**
- Create: `src/agents/strategist/position_thesis.py`
- Test: `tests/unit/agents/strategist/test_position_thesis.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/agents/strategist/test_position_thesis.py
"""V1 PositionThesis schema — round-trip, enum validation, frozen fixture.

The frozen fixture under ``tests/fixtures/position_thesis_v1.json`` is the
schema-evolution lock: any future change that breaks reading this fixture
must either preserve a Pydantic field alias or graduate the schema to a
typed SQL table (see Spec B "Future work").
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.strategist.position_thesis import PositionThesis

_FIXTURE = Path(__file__).parents[3] / "fixtures" / "position_thesis_v1.json"


def _sample_thesis_dict() -> dict:
    """Return a minimal valid PositionThesis payload (every required field set)."""

    return {
        "ticker":                 "AVGO",
        "opened_at":              "2025-09-15T13:30:00+00:00",
        "opened_tick_id":         "tick-20250915T133000-deadbeef",
        "opened_price":           175.42,
        "weight":                 0.05,
        "target_price":           200.0,
        "stop_price":             160.0,
        "catalyst":               "AI accelerator revenue guide next quarter",
        "horizon":                "swing",
        "rationale":              "AI-cycle demand + steady FCF; entry on pullback.",
        "last_reviewed_at":       "2025-09-15T13:30:00+00:00",
        "last_reviewed_decision": "open",
        "last_reviewed_reason":   "Initial entry.",
    }


def test_position_thesis_round_trips_through_json() -> None:
    """model_dump → json.dumps → json.loads → model_validate restores the same model."""

    original  = PositionThesis(**_sample_thesis_dict())
    dumped    = original.model_dump(mode="json")
    rehydrate = PositionThesis.model_validate(json.loads(json.dumps(dumped)))

    assert rehydrate == original


def test_position_thesis_horizon_validates_enum() -> None:
    """horizon outside the closed vocabulary raises ValidationError."""

    bad = _sample_thesis_dict() | {"horizon": "forever"}

    with pytest.raises(ValidationError):
        PositionThesis(**bad)


def test_position_thesis_last_reviewed_decision_validates_enum() -> None:
    """last_reviewed_decision must be one of {open, add, trim, hold, update}.

    'close' is rejected — close deletes the row, so the row's
    last_reviewed_decision can never be 'close'.
    """

    bad = _sample_thesis_dict() | {"last_reviewed_decision": "close"}

    with pytest.raises(ValidationError):
        PositionThesis(**bad)


def test_position_thesis_v1_frozen_payload_deserialises() -> None:
    """The checked-in V1 fixture must deserialise cleanly with the current schema.

    Schema-evolution discipline: any change to PositionThesis that breaks
    this assertion must add a Pydantic field alias bridging the old name
    (or graduate to typed SQL tables) before merging.
    """

    payload = json.loads(_FIXTURE.read_text())

    thesis = PositionThesis.model_validate(payload)

    # Smoke-check a handful of fields; the round-trip test above covers
    # exhaustive shape equality.
    assert thesis.ticker        == "AVGO"
    assert thesis.horizon       in {"intraday", "swing", "long_term"}
    assert isinstance(thesis.opened_at, datetime)
    assert thesis.opened_at.tzinfo is not None
```

- [ ] **Step 2: Write the frozen V1 fixture**

```bash
mkdir -p tests/fixtures
```

Create `tests/fixtures/position_thesis_v1.json`:

```json
{
  "ticker": "AVGO",
  "opened_at": "2025-09-15T13:30:00+00:00",
  "opened_tick_id": "tick-20250915T133000-deadbeef",
  "opened_price": 175.42,
  "weight": 0.05,
  "target_price": 200.0,
  "stop_price": 160.0,
  "catalyst": "AI accelerator revenue guide next quarter",
  "horizon": "swing",
  "rationale": "AI-cycle demand + steady FCF; entry on pullback.",
  "last_reviewed_at": "2025-09-15T13:30:00+00:00",
  "last_reviewed_decision": "open",
  "last_reviewed_reason": "Initial entry."
}
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_position_thesis.py -v
```

Expected: FAIL — `agents.strategist.position_thesis` does not exist yet.

- [ ] **Step 4: Implement the `PositionThesis` model**

Create `src/agents/strategist/position_thesis.py`:

```python
"""PositionThesis — the V1 thesis-book row shape.

Persisted as a value inside ``state["user:positions"]`` (keyed by
ticker).  Round-trips through ADK's session state via ``model_dump()``
/ ``model_validate()`` at the persistence boundary.

This model replaces the colliding ``PositionThesis`` previously declared
in ``agents.strategist.schema`` (deleted in the same PR).  The legacy
shape carried optional ``opened_price`` to absorb a bug in the prior
executor; Spec B's MemoryWriter now stamps ``opened_price`` from the
broker fill before persisting, so the field is required.

Field lifecycle
---------------
- ``opened_at``, ``opened_tick_id``, ``opened_price`` are written once
  when the position is opened and are immutable thereafter.
- ``weight`` is mutated by MemoryWriter on every ``add`` / ``trim``.
- ``target_price``, ``stop_price``, ``catalyst``, ``horizon`` are
  mutable via the ``update`` stance (no trade) or any other stance
  that supplies them.
- ``rationale`` is FROZEN at open.  If the underlying thesis genuinely
  changes the right action is ``close`` then ``open``.
- ``last_reviewed_at`` and ``last_reviewed_decision`` track the most
  recent tick that touched this row.
- ``last_reviewed_reason`` is persisted for the audit trail but is NOT
  rendered into the next tick's prompt (Spec B Principle 2).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PositionThesis(BaseModel):
    """One row of the strategist's thesis book.

    See module docstring for field lifecycle rules.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    ticker: str = Field(
        ...,
        description="Ticker symbol, e.g. 'AVGO'.",
    )

    # ── Entry record (immutable after open) ──────────────────────────────────
    opened_at: datetime = Field(
        ...,
        description=(
            "Timestamp (UTC, tz-aware) of the tick on which the position "
            "was opened.  Matches the ``state['as_of']`` convention in "
            "``docs/contract-invariants.md`` §A."
        ),
    )

    opened_tick_id: str = Field(
        ...,
        description="Tick identifier captured at open time, for traceability.",
    )

    opened_price: float = Field(
        ...,
        gt=0.0,
        description=(
            "Fill price recorded by the executor at open and forwarded to "
            "MemoryWriter as ``fill_price``.  Always positive — a zero or "
            "negative price would crash the held-view's pct-from-entry "
            "computation."
        ),
    )

    # ── Current sizing (mutated by add / trim) ───────────────────────────────
    weight: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Current portfolio weight in [0, 1].",
    )

    # ── Commitments (mutable via 'update' stance) ────────────────────────────
    target_price: float | None = Field(
        None,
        description="Optional price level at which the thesis is confirmed.",
    )

    stop_price: float | None = Field(
        None,
        description="Optional price level below which the thesis is invalidated.",
    )

    catalyst: str | None = Field(
        None,
        description="Free-form text describing the event that confirms the thesis.",
    )

    horizon: Literal["intraday", "swing", "long_term"] = Field(
        ...,
        description="Time horizon over which the thesis is expected to play out.",
    )

    # ── Entry rationale (FROZEN at open) ─────────────────────────────────────
    rationale: str = Field(
        ...,
        description=(
            "The strategist's reasoning at the moment of opening the position. "
            "Immutable for the lifetime of the position — if the underlying "
            "thesis changes, the right action is close + reopen."
        ),
    )

    # ── Review trail ─────────────────────────────────────────────────────────
    last_reviewed_at: datetime = Field(
        ...,
        description="Timestamp of the most recent tick whose stance touched this row.",
    )

    # 'close' is intentionally absent — close deletes the row, so the
    # surviving row can never carry 'close' as its last review decision.
    last_reviewed_decision: Literal["open", "add", "trim", "hold", "update"] = Field(
        ...,
        description=(
            "Stance verb that produced the most recent review.  Set to "
            "'open' on initial entry (the row's lifetime begins with the "
            "open stance, which counts as the first review).  Never "
            "'close'."
        ),
    )

    last_reviewed_reason: str = Field(
        ...,
        description=(
            "The strategist's 'what's changed since opening' articulation on "
            "the most recent review.  Persisted to the audit trail; NOT "
            "rendered back into the next tick's prompt."
        ),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_position_thesis.py -v
```

Expected: PASS — all four tests green.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/position_thesis.py tests/unit/agents/strategist/test_position_thesis.py tests/fixtures/position_thesis_v1.json
git commit -m "$(cat <<'EOF'
feat(strategist): add V1 PositionThesis model + frozen fixture (Spec B Band 3)

Introduces the new PositionThesis at agents.strategist.position_thesis
with required opened_price (post-S2-fix: MemoryWriter stamps the fill
price before persisting, so the field can be required).  The frozen
fixture at tests/fixtures/position_thesis_v1.json is the schema-
evolution lock — any future change that breaks reading it must add a
field alias or graduate to typed SQL tables.

The pre-existing colliding PositionThesis in schema.py is removed in
the next commit alongside the import-site migrations.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 8: Delete the colliding `PositionThesis`; migrate import sites

**Files:**
- Modify: `src/agents/strategist/schema.py` (delete lines 36-68; update imports)
- Modify: `src/agents/strategist/derivation.py` (update `PositionThesis` import path)
- Modify: any other source files that import `PositionThesis` from `agents.strategist.schema`

- [ ] **Step 1: Find all import sites**

```bash
grep -rn "from agents.strategist.schema import" /home/oscarhill2012/Documents/Repository/StockBot/src/ /home/oscarhill2012/Documents/Repository/StockBot/tests/
grep -rn "agents.strategist.schema.PositionThesis" /home/oscarhill2012/Documents/Repository/StockBot/src/ /home/oscarhill2012/Documents/Repository/StockBot/tests/
```

Expected output: a short list of files importing `PositionThesis` from `schema.py`. Capture the list — these are the call sites to update.

- [ ] **Step 2: Update each import to point at the new module**

For every match above, change:

```python
from agents.strategist.schema import PositionThesis  # old
```

to:

```python
from agents.strategist.position_thesis import PositionThesis  # new
```

`StrategistDecision` still lives in `schema.py`, so leave the `StrategistDecision` portion of any combined import untouched:

```python
# OK to leave a combined import like:
from agents.strategist.schema import StrategistDecision
from agents.strategist.position_thesis import PositionThesis
```

- [ ] **Step 3: Delete the legacy `PositionThesis` class in `schema.py`**

Remove lines 36-68 of `src/agents/strategist/schema.py` (the entire pre-existing `PositionThesis` class). Replace with an explanatory comment:

```python
# PositionThesis used to live here.  Spec B moved it to its own module
# (``agents.strategist.position_thesis``) and tightened the schema so
# opened_price is required.  See docs/Phase10-post-first-backtest/specs/
# foundational-thesis-memory.md.
```

Update the top-of-file imports to bring in the moved model so `StrategistDecision.new_positions: dict[str, PositionThesis]` still resolves at this commit boundary:

```python
from agents.strategist.position_thesis import PositionThesis
```

Note: this import becomes unused after Task 8b deletes the `new_positions` field. Task 8b Step 3 removes it. Adding it here keeps the codebase importable between the two commits.

- [ ] **Step 4: Run the full unit test suite to verify nothing imports the old path**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit -v --tb=short
```

Expected: any test that imports from the old path fails with ImportError. Fix each call site found by grep until the suite reaches PASS for unrelated reasons.

Note: derivation tests that exercise an **open** stance will still fail at this step because the constructor inside `derive_legacy_fields` (lines 220-242 of the live code) passes legacy kwargs (`opened_tag`, `last_review_note`) and omits the new required fields (`weight`, `opened_price`, `last_reviewed_decision`, `last_reviewed_reason`). That constructor is removed by Task 8b immediately below — leave those failures in place for now.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/schema.py src/agents/strategist/derivation.py
git commit -m "$(cat <<'EOF'
refactor(strategist): migrate PositionThesis imports to dedicated module (Spec B Band 3)

Deletes the legacy PositionThesis in schema.py and points every
import site at agents.strategist.position_thesis.PositionThesis.
StrategistDecision.new_positions still references PositionThesis at
this commit — the field itself is removed in Task 8b immediately
following.  The new import in schema.py is therefore short-lived but
required to keep the codebase importable between the two commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 8b: Retire `new_positions` plumbing and the dead lifecycle check

**Why this task exists.** Pre-Spec-B, the strategist's after-callback constructed a `PositionThesis` row for every fresh open and stuffed it into `StrategistDecision.new_positions`; the Executor then re-stamped `opened_price` after the BUY fill. Spec B moves the writer-of-record to MemoryWriter, which assembles `user:positions` directly from stances + `executions[].fill_price`. That makes three pieces of legacy plumbing redundant — and *broken*, because the constructor in `derivation.py` still passes the old kwargs (`opened_tag`, `last_review_note`) and omits the new required fields (`weight`, `opened_price`, `last_reviewed_decision`, `last_reviewed_reason`). This task removes that plumbing in one atomic edit so the codebase is internally consistent before Task 9 lands the schema validator.

A secondary cleanup rides on the same task: `agents.risk_gate.lifecycle.validate_lifecycle_contract` consumed `new_positions` to enforce "no open without thesis". A grep shows it is **only called from tests** (`tests/unit/test_position_lifecycle.py`) — no agent uses it. Task 9's verb-conditional `model_validator` on `TickerStance` enforces the same invariant at schema-parse time, making the runtime check fully redundant. We delete the function plus its test file.

**Files:**
- Modify: `src/agents/strategist/derivation.py` (remove `PositionThesis(...)` constructor block + `new_positions` from `DerivedFields`)
- Modify: `src/agents/strategist/schema.py` (delete `new_positions` field on `StrategistDecision`)
- Modify: `src/agents/strategist/agent.py` (delete `decision.new_positions = derived.new_positions` line)
- Delete: `src/agents/risk_gate/lifecycle.py`
- Delete: `tests/unit/test_position_lifecycle.py`
- Modify: any tests / fixtures that reference `decision.new_positions` or `strategist_decision["new_positions"]` (the test-migration grep at Step 5 finds them)

- [ ] **Step 1: Grep all `new_positions` consumers and capture the surface**

```bash
grep -rn "new_positions\|validate_lifecycle_contract" /home/oscarhill2012/Documents/Repository/StockBot/src/ /home/oscarhill2012/Documents/Repository/StockBot/tests/
```

Expected: a small set of hits — derivation.py, schema.py, agent.py, the Executor's BUY-time read at `src/agents/executor/agent.py:88` (Task 13 already deletes this — leave it for Task 13), plus a handful of test files. Capture the list; every match outside `src/agents/executor/agent.py` is in scope for this task.

- [ ] **Step 2: Remove the `PositionThesis(...)` constructor and `new_positions` from derivation**

Edit `src/agents/strategist/derivation.py`:

1. Drop the `new_positions` field from the `DerivedFields` dataclass docstring + declaration (lines ~104-121). Remove the corresponding line from the `derive_legacy_fields` docstring (the "``new_positions`` fires only on ``open``…" bullet).

2. Inside `derive_legacy_fields`, delete the `new_positions: dict[str, PositionThesis] = {}` initialiser, the entire `if action == "open":` block that constructs a `PositionThesis`, and `new_positions=new_positions` from the final `DerivedFields(...)` return.

3. The `if action == "open":` branch still has work to do — `target_weights` and `decision_tags` are already written above. So the block collapses cleanly: just delete the `if`/`elif` arm whose body is the constructor; the `elif action == "close" / "trim"` arms stay.

4. Remove the now-unused `PositionThesis` import at the top of the file.

5. Update the module / function docstrings: the opening paragraph mentions `new_positions` in the list of four derived dicts. Rewrite as: "populate `StrategistDecision.target_weights` / `close_reasons` / `trim_reasons` from the LLM-emitted `stances`" (three dicts, not four). The "Active-stances model" comment block stays untouched — it's about Pass 2 carry-forward, which is Plan 2's territory.

- [ ] **Step 3: Drop `new_positions` from `StrategistDecision`**

Edit `src/agents/strategist/schema.py`:

1. Delete the `new_positions: dict[str, PositionThesis] = Field(default_factory=dict)` line.

2. Update the class docstring — change "fills in `target_weights` / `new_positions` / `close_reasons` / `trim_reasons`" to "fills in `target_weights` / `close_reasons` / `trim_reasons`".

3. The `from agents.strategist.position_thesis import PositionThesis` import (added in Task 8 Step 3) is now unused — remove it.

- [ ] **Step 4: Drop the assignment in the strategist after-callback**

Edit `src/agents/strategist/agent.py`. Locate the `decision.new_positions = derived.new_positions` line (around line 234, immediately after `decision.target_weights = derived.target_weights`). Delete that single line. The lines above and below stay.

- [ ] **Step 5: Delete `agents.risk_gate.lifecycle` and its test**

```bash
rm /home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/lifecycle.py
rm /home/oscarhill2012/Documents/Repository/StockBot/tests/unit/test_position_lifecycle.py
```

Verify no other code imports the module:

```bash
grep -rn "from agents.risk_gate.lifecycle\|risk_gate.lifecycle import" /home/oscarhill2012/Documents/Repository/StockBot/src/ /home/oscarhill2012/Documents/Repository/StockBot/tests/
```

Expected: zero hits. Plan 2 Task 4 imports `StrategistContractViolation` from this module — that import must be redirected. Move `StrategistContractViolation` to `src/agents/strategist/derivation.py` (where the only remaining caller — Plan 2's Pass 1.5 — lives):

```python
# In src/agents/strategist/derivation.py, near the top:

class StrategistContractViolation(RuntimeError):
    """Strategist failed to honour position-lifecycle invariants.

    Raised by ``derive_legacy_fields`` when the LLM omits a stance for a
    pre-tick held ticker (Spec B / D3 — see Plan 2 Task 4).
    """
```

Then update Plan 2 Task 4 Step 3's import (it currently reads `from agents.risk_gate.lifecycle import StrategistContractViolation`) to `from agents.strategist.derivation import StrategistContractViolation`. (Coord note 6 below tracks this hand-off.)

- [ ] **Step 6: Update test fixtures that reference `new_positions`**

For every test file the Step 1 grep flagged outside `src/`, remove `new_positions` from the `strategist_decision` payload:

```python
# tests/integration/test_memory_writer_integration.py
# tests/integration/test_risk_gate_agent.py
# tests/integration/test_risk_gate_state_delta.py
# tests/unit/agents/test_executor_decision_hook.py
# tests/unit/agents/strategist/test_decision_schema_v2.py
# tests/unit/agents/strategist/test_strategist_callbacks_v2.py
# tests/unit/agents/strategist/test_derivation.py
```

Each one carries a `"new_positions": {…}` entry inside a `strategist_decision` dict or asserts against `decision.new_positions` / `decided["new_positions"]`. Remove the entry (and any matching assertion). Where a test asserts a freshly opened position's `opened_tick_id` (e.g. `test_strategist_callbacks_v2.py:215`), the equivalent post-Spec-B assertion belongs in `tests/unit/agents/memory_writer/test_memory_writer.py` (Task 12) — note the move, don't try to preserve it here.

`tests/integration/test_executor_with_fake_broker.py:107` passes a `thesis_from_strategist` PositionThesis through `new_positions["AAPL"]`. The test exists to assert "Executor stamps `opened_price` from the fill price". Under Spec B, that assertion moves to MemoryWriter — delete the test or migrate it to `tests/unit/agents/memory_writer/test_memory_writer.py` (Task 12 already covers this case in `test_memory_writer_uses_executor_fill_price_for_opened_price`). If migrated to MemoryWriter, this file's test can be deleted; capture the choice in the commit message.

- [ ] **Step 7: Run the full unit suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit -v --tb=short
```

Expected: green, modulo Band 4 / 5 work still pending (MemoryWriter, Executor reshape, risk-gate verb-skip — those tests don't yet exist, so they can't be red). If any currently-failing test mentions `new_positions` or `validate_lifecycle_contract`, return to Step 6 and finish the migration.

- [ ] **Step 8: Commit**

```bash
git add \
  src/agents/strategist/derivation.py \
  src/agents/strategist/schema.py \
  src/agents/strategist/agent.py \
  tests/integration/test_memory_writer_integration.py \
  tests/integration/test_risk_gate_agent.py \
  tests/integration/test_risk_gate_state_delta.py \
  tests/integration/test_executor_with_fake_broker.py \
  tests/unit/agents/test_executor_decision_hook.py \
  tests/unit/agents/strategist/test_decision_schema_v2.py \
  tests/unit/agents/strategist/test_strategist_callbacks_v2.py \
  tests/unit/agents/strategist/test_derivation.py
git rm \
  src/agents/risk_gate/lifecycle.py \
  tests/unit/test_position_lifecycle.py
git commit -m "$(cat <<'EOF'
refactor(strategist): retire new_positions + dead lifecycle check (Spec B Band 3)

Spec B makes MemoryWriter the writer-of-record for user:positions —
it assembles the dict from stances + executions[].fill_price every
tick.  That makes the strategist after-callback's construction of
StrategistDecision.new_positions (via a PositionThesis instance in
derivation.py) redundant.

The legacy constructor was also broken under the new PositionThesis
schema introduced in Task 7 — it passed opened_tag / last_review_note
(neither exist on the new model) and omitted the new required fields
(weight, opened_price, last_reviewed_decision, last_reviewed_reason).
Removing the constructor is the only honest fix; the strategist runs
before the order fills and has no opened_price to stamp.

Also deletes agents.risk_gate.lifecycle entirely.  Its
validate_lifecycle_contract was only ever called from tests; Task 9's
verb-conditional model_validator on TickerStance enforces the same
"open stances carry commitment fields" invariant at schema-parse
time, so the runtime check is now redundant.  StrategistContractViolation
moves to derivation.py where its only remaining caller (Plan 2 Task 4
Pass 1.5) lives.

Test fixtures across seven files drop the now-removed
strategist_decision["new_positions"] entry.  The freshly-opened
opened_tick_id assertion previously in test_strategist_callbacks_v2
moves to tests/unit/agents/memory_writer/test_memory_writer.py
(Task 12) where it belongs post-split.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 9: Extend `TickerStance` with `intent` enum + verb-conditional fields

**Files:**
- Modify: `src/agents/strategist/stance_schema.py`
- Test: `tests/unit/agents/strategist/test_stance_schema.py` (new or extended)

- [ ] **Step 1: Write the failing tests**

Create or extend `tests/unit/agents/strategist/test_stance_schema.py`:

```python
"""TickerStance verb-conditional validation tests (Spec B).

The schema gains an ``intent`` enum (six verbs) and verb-conditional
field requirements per the spec's validation table:

| Verb     | Required fields                                                          |
|----------|--------------------------------------------------------------------------|
| open     | weight, target_price, stop_price, catalyst, horizon, rationale           |
| add      | weight                                                                   |
| trim     | weight, reason                                                           |
| close    | (none)                                                                   |
| hold     | reason                                                                   |
| update   | reason, plus at least one of target_price/stop_price/catalyst/horizon    |
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance


def _open_payload(**overrides):
    """Return a valid 'open' stance payload; overrides win."""
    base = {
        "ticker":            "AAPL",
        "intent":            "open",
        "preferred_weight":  0.05,
        "conviction":        0.7,
        "rationale":         "Strong iPhone cycle + services growth.",
        "horizon":           "swing",
        "target_price":      200.0,
        "stop_price":        160.0,
        "catalyst":          "Holiday quarter guide.",
    }
    base.update(overrides)
    return base


def test_open_requires_full_commitment_block() -> None:
    """open without target_price raises ValidationError."""

    bad = _open_payload(target_price=None)

    with pytest.raises(ValidationError):
        TickerStance(**bad)


def test_hold_requires_reason() -> None:
    """hold without a reason raises ValidationError."""

    bad = {
        "ticker":           "AAPL",
        "intent":           "hold",
        "preferred_weight": 0.05,
        "conviction":       0.6,
        "rationale":        "carry-forward",
        # no `reason`
    }

    with pytest.raises(ValidationError):
        TickerStance(**bad)


def test_update_requires_reason_and_at_least_one_commitment_field() -> None:
    """update with a reason but no mutated commitments raises ValidationError."""

    bad = {
        "ticker":           "AAPL",
        "intent":           "update",
        "preferred_weight": 0.05,
        "conviction":       0.6,
        "rationale":        "carry-forward",
        "reason":           "Catalyst slipped a quarter.",
        # no target / stop / catalyst / horizon supplied
    }

    with pytest.raises(ValidationError):
        TickerStance(**bad)


def test_close_accepts_zero_weight_with_no_commitments() -> None:
    """close passes validation when weight=0 and no commitment block."""

    ok = {
        "ticker":           "AAPL",
        "intent":           "close",
        "preferred_weight": 0.0,
        "conviction":       0.6,
        "rationale":        "Stop tagged.",
        "close_reason":     "Stop level breached intraday.",
    }

    TickerStance(**ok)  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_stance_schema.py -v
```

Expected: FAIL — `intent` and `reason` fields are absent; the existing validator gates on weight, not verb.

- [ ] **Step 3: Add the `intent` enum + per-stance `reason` + verb-aware validator**

Edit `src/agents/strategist/stance_schema.py`:

```python
"""TickerStance — the strategist's per-ticker decision substrate.

Spec B adds an ``intent`` enum (six verbs) so the executor can dispatch
on an explicit decision rather than inferring action from
(prior_weight, new_weight).  Verb-conditional validation lives in the
``_validate_verb_conditional_fields`` model-validator below.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from config.strategist import get_strategist_config

_cfg        = get_strategist_config()
_STANCE     = _cfg.stance_caps
_schema_cap = _cfg.schema_cap


# Closed vocabulary of lifecycle verbs.  Imported by _verb_dispatch.py
# and the MemoryWriter / Executor so the canonical list lives here.
StanceVerb = Literal["open", "add", "trim", "close", "hold", "update"]


class TickerStance(BaseModel):
    """One stance per watchlist ticker per strategist tick.

    See module docstring; Spec B is the controlling document.
    """

    ticker: str

    # Lifecycle verb — set by the LLM on every stance.  Drives broker-call
    # dispatch (Executor) and PositionThesis assembly (MemoryWriter).
    intent: StanceVerb

    # Target portfolio weight — bounded fraction of total portfolio value.
    preferred_weight: float = Field(ge=0.0, le=1.0)

    # Synthesised conviction after weighing all analyst signals.
    conviction: float = Field(ge=0.0, le=1.0)

    # Brief justification used by the LLM for the strategist's own audit
    # trail.  Kept short — full chain-of-thought lives in the LLM call
    # itself, not the schema.
    rationale: str = Field(max_length=_schema_cap(_STANCE.rationale_max_chars))

    # 'what's changed since opening' articulation — required for the
    # review verbs (trim / hold / update) per Spec B Principle 2.
    reason: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.rationale_max_chars),
        description=(
            "Review prose required on trim / hold / update.  Persisted "
            "to PositionThesis.last_reviewed_reason but NOT rendered "
            "back into the next tick's prompt."
        ),
    )

    # Commitment block — required on open, optional on add / update.
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price:   float | None = None
    catalyst: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.catalyst_max_chars),
    )

    # Legacy reason fields — kept for backwards compatibility with the
    # post-S2 lifecycle validator in risk_gate.  Populated by the LLM
    # on close / trim stances; ignored by Executor (which dispatches on
    # intent now).
    close_reason: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.close_reason_max_chars),
    )
    trim_reason: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.trim_reason_max_chars),
    )

    @model_validator(mode="after")
    def _validate_verb_conditional_fields(self) -> "TickerStance":
        """Enforce the verb-conditional field requirements (Spec B).

        | Verb   | Required fields                                       |
        |--------|-------------------------------------------------------|
        | open   | target_price, stop_price, catalyst, horizon, rationale|
        | add    | (preferred_weight is always present)                  |
        | trim   | reason                                                |
        | close  | (none — close_reason is enforced by risk_gate)        |
        | hold   | reason                                                |
        | update | reason, AND at least one of target_price /            |
        |        | stop_price / catalyst / horizon                       |

        Field-presence only — delta magnitudes are the risk_gate's
        concern, not the validator's.
        """

        # ── open: full commitment block required ────────────────────────
        if self.intent == "open":
            missing = [
                name
                for name, value in (
                    ("horizon",      self.horizon),
                    ("target_price", self.target_price),
                    ("stop_price",   self.stop_price),
                    ("catalyst",     self.catalyst),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"Stance for {self.ticker} has intent='open' but is "
                    f"missing required commitment fields: {missing}.  An "
                    f"open stance must seed PositionThesis with the full "
                    f"commitment block (horizon, target_price, stop_price, "
                    f"catalyst, rationale)."
                )
            return self

        # ── trim / hold: review prose required ──────────────────────────
        if self.intent in ("trim", "hold"):
            if not self.reason:
                raise ValueError(
                    f"Stance for {self.ticker} has intent='{self.intent}' "
                    f"but no 'reason' — review verbs must articulate "
                    f"what has changed since opening."
                )
            return self

        # ── update: reason AND at least one mutated commitment field ────
        if self.intent == "update":
            if not self.reason:
                raise ValueError(
                    f"Stance for {self.ticker} has intent='update' but no "
                    f"'reason' — update verbs must articulate what has "
                    f"changed since opening."
                )
            mutated = any(
                v is not None
                for v in (self.target_price, self.stop_price,
                          self.catalyst,     self.horizon)
            )
            if not mutated:
                raise ValueError(
                    f"Stance for {self.ticker} has intent='update' but no "
                    f"commitment field is set.  An update with no "
                    f"target_price / stop_price / catalyst / horizon "
                    f"mutation should be 'hold' instead."
                )
            return self

        # ── add / close: no extra validation beyond field presence ──────
        return self
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_stance_schema.py -v
```

Expected: PASS — all four new tests green, plus any pre-existing tests in the same file (they may need fixture updates to add `intent` to existing payloads; resolve as part of this commit).

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/stance_schema.py tests/unit/agents/strategist/test_stance_schema.py
git commit -m "$(cat <<'EOF'
feat(strategist): add intent enum + verb-conditional validation to TickerStance (Spec B Band 3)

* Adds the six-verb StanceVerb literal (open / add / trim / close /
  hold / update).
* Adds optional per-stance `reason` field for the trim / hold / update
  review prose.
* Replaces the weight-gated validator with a verb-aware one that
  enforces the Spec B validation table (field-presence only — delta
  magnitudes stay the risk_gate's concern).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 10: Add `thesis_revision` to `StrategistDecision`

**Files:**
- Modify: `src/agents/strategist/schema.py`
- Test: extend `tests/unit/agents/strategist/test_stance_schema.py` or add new file

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agents/strategist/test_stance_schema.py` (or new `test_strategist_decision.py`):

```python
def test_strategist_decision_accepts_optional_thesis_revision() -> None:
    """thesis_revision is optional; defaults to None for carry-forward semantics."""

    from agents.strategist.schema import StrategistDecision

    minimal = {
        "stances":        [],
        "target_weights": {},
        "decision_tag":   "carry_forward",
        "reasoning":      "No change.",
        "updated_thesis": "",  # legacy field, kept for back-compat
        "confidence":     0.5,
    }

    d = StrategistDecision(**minimal)

    assert d.thesis_revision is None


def test_strategist_decision_round_trips_thesis_revision_when_set() -> None:
    """thesis_revision set to a non-empty string survives model_dump / validate."""

    from agents.strategist.schema import StrategistDecision

    payload = {
        "stances":          [],
        "target_weights":   {},
        "decision_tag":     "regime_shift",
        "reasoning":        "Macro pivot detected.",
        "updated_thesis":   "",
        "confidence":       0.55,
        "thesis_revision":  "Macro regime tilting hawkish; trim cyclicals.",
    }

    d = StrategistDecision(**payload)
    restored = StrategistDecision.model_validate(d.model_dump())

    assert restored.thesis_revision == payload["thesis_revision"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_stance_schema.py -k thesis_revision -v
```

Expected: FAIL — `thesis_revision` is not a field of `StrategistDecision`.

- [ ] **Step 3: Add `thesis_revision` to `StrategistDecision`**

In `src/agents/strategist/schema.py`, inside the `StrategistDecision` class, add:

```python
    # ── Optional market-thesis revision (Spec B) ──────────────────────────
    # MemoryWriter consumes this: when non-null, writes the new value to
    # ``state["user:thesis"]``; when null, the prior thesis is carried
    # forward by re-writing it to the same event payload (explicit
    # carry rather than absence).  Distinct from ``updated_thesis``
    # (legacy field used by the pre-Spec-B MemoryWriter and kept for
    # back-compat — it will be retired in Spec C).
    thesis_revision: str | None = Field(
        default=None,
        max_length=_schema_cap(_DECISION.updated_thesis_max_chars),
        description=(
            "Optional revision of the standing market thesis.  When "
            "non-null, MemoryWriter writes the value to "
            "``state['user:thesis']`` via state_delta."
        ),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_stance_schema.py -k thesis_revision -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/schema.py tests/unit/agents/strategist/test_stance_schema.py
git commit -m "$(cat <<'EOF'
feat(strategist): add optional thesis_revision to StrategistDecision (Spec B Band 3)

MemoryWriter consumes thesis_revision: non-null overwrites
state["user:thesis"] via state_delta; null carries the prior thesis
forward.  Distinct from the legacy updated_thesis field, which stays
in place for back-compat until Spec C retires it.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 11: Implement the verb-dispatch helpers

**Files:**
- Create: `src/agents/_verb_dispatch.py`
- Test: `tests/unit/agents/test_verb_dispatch.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/agents/test_verb_dispatch.py
"""Verb-dispatch helpers — pure-function coverage (no agent wiring).

resolve_broker_call: stance -> BrokerCall | None
apply_stance_to_thesis: stance + prior_row + fill_price -> PositionThesis | None
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents._verb_dispatch import (
    BrokerCall,
    apply_stance_to_thesis,
    resolve_broker_call,
)
from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance


# ── Fixtures ────────────────────────────────────────────────────────────────

_AS_OF = datetime(2025, 9, 16, 13, 30, tzinfo=UTC)
_TICK  = "tick-20250916T133000-cafef00d"


def _prior_thesis() -> PositionThesis:
    """A held position used as ``prior_row`` for non-open verbs."""
    return PositionThesis(
        ticker="AAPL",
        opened_at=datetime(2025, 9, 15, 13, 30, tzinfo=UTC),
        opened_tick_id="tick-20250915T133000-deadbeef",
        opened_price=175.42,
        weight=0.05,
        target_price=200.0,
        stop_price=160.0,
        catalyst="Holiday quarter guide.",
        horizon="swing",
        rationale="Strong iPhone cycle.",
        last_reviewed_at=datetime(2025, 9, 15, 13, 30, tzinfo=UTC),
        last_reviewed_decision="open",
        last_reviewed_reason="Initial entry.",
    )


def _stance(intent: str, **overrides) -> TickerStance:
    """Build a TickerStance with sensible defaults; overrides win."""
    base = {
        "ticker":           "AAPL",
        "intent":           intent,
        "preferred_weight": 0.05,
        "conviction":       0.7,
        "rationale":        "test stance",
    }
    if intent == "open":
        base.update({
            "horizon":      "swing",
            "target_price": 200.0,
            "stop_price":   160.0,
            "catalyst":     "Holiday quarter guide.",
        })
    if intent in ("trim", "hold"):
        base.update({"reason": "Test review."})
    if intent == "update":
        base.update({"reason": "Test review.", "target_price": 210.0})
    if intent == "close":
        base.update({"preferred_weight": 0.0, "close_reason": "stop hit"})
    base.update(overrides)
    return TickerStance(**base)


# ── resolve_broker_call ─────────────────────────────────────────────────────

def test_resolve_broker_call_open_returns_buy_to_weight() -> None:
    call = resolve_broker_call(_stance("open"), prior_row=None)
    assert call is not None
    assert call.action == "BUY"
    assert call.target_weight == 0.05


def test_resolve_broker_call_close_returns_sell_all() -> None:
    call = resolve_broker_call(
        _stance("close"), prior_row=_prior_thesis(),
    )
    assert call is not None
    assert call.action == "SELL"
    assert call.target_weight == 0.0


def test_resolve_broker_call_hold_returns_none() -> None:
    assert resolve_broker_call(
        _stance("hold"), prior_row=_prior_thesis(),
    ) is None


def test_resolve_broker_call_update_returns_none() -> None:
    assert resolve_broker_call(
        _stance("update"), prior_row=_prior_thesis(),
    ) is None


# ── apply_stance_to_thesis ──────────────────────────────────────────────────

def test_apply_stance_open_seeds_new_position_with_fill_price() -> None:
    row = apply_stance_to_thesis(
        _stance("open"),
        prior_row=None,
        fill_price=176.10,
        tick_id=_TICK,
        as_of=_AS_OF,
    )
    assert row is not None
    assert row.opened_price          == 176.10
    assert row.last_reviewed_decision == "open"
    assert row.rationale              == "test stance"


def test_apply_stance_hold_touches_review_fields_only() -> None:
    prior = _prior_thesis()
    row = apply_stance_to_thesis(
        _stance("hold"),
        prior_row=prior,
        fill_price=None,
        tick_id=_TICK,
        as_of=_AS_OF,
    )
    # Identity preserved.
    assert row.opened_price == prior.opened_price
    assert row.rationale    == prior.rationale
    # Review fields mutated.
    assert row.last_reviewed_at      == _AS_OF
    assert row.last_reviewed_decision == "hold"
    assert row.last_reviewed_reason   == "Test review."


def test_apply_stance_update_mutates_target_stop_catalyst_horizon() -> None:
    prior = _prior_thesis()
    row = apply_stance_to_thesis(
        _stance("update"),
        prior_row=prior,
        fill_price=None,
        tick_id=_TICK,
        as_of=_AS_OF,
    )
    assert row.target_price == 210.0          # mutated by stance
    assert row.stop_price   == prior.stop_price  # untouched
    assert row.rationale    == prior.rationale   # FROZEN (Invariant 3)


def test_apply_stance_update_does_not_mutate_rationale() -> None:
    prior = _prior_thesis()
    row = apply_stance_to_thesis(
        _stance("update", rationale="ATTEMPTED REWRITE"),
        prior_row=prior,
        fill_price=None,
        tick_id=_TICK,
        as_of=_AS_OF,
    )
    assert row.rationale == prior.rationale, (
        "Invariant 3: rationale is frozen at open and must NOT be mutated "
        "by an update stance."
    )


def test_apply_stance_close_returns_none_signalling_deletion() -> None:
    row = apply_stance_to_thesis(
        _stance("close"),
        prior_row=_prior_thesis(),
        fill_price=180.0,
        tick_id=_TICK,
        as_of=_AS_OF,
    )
    assert row is None


def test_apply_stance_add_preserves_rationale() -> None:
    prior = _prior_thesis()
    row = apply_stance_to_thesis(
        _stance("add", preferred_weight=0.08),
        prior_row=prior,
        fill_price=178.0,
        tick_id=_TICK,
        as_of=_AS_OF,
    )
    assert row.rationale == prior.rationale
    assert row.weight    == 0.08
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_verb_dispatch.py -v
```

Expected: FAIL — `agents._verb_dispatch` does not exist.

- [ ] **Step 3: Implement the helpers**

Create `src/agents/_verb_dispatch.py`:

```python
"""Verb-dispatch helpers — canonical verb→effect mapping (Spec B).

Two pure functions:

- ``resolve_broker_call(stance, prior_row)`` → ``BrokerCall | None``
  The Executor calls this to decide what to send to the broker.
  Returns ``None`` for the no-trade verbs (``hold`` / ``update``).

- ``apply_stance_to_thesis(stance, prior_row, fill_price, tick_id, as_of)``
  → ``PositionThesis | None``
  MemoryWriter calls this to assemble the post-tick ``user:positions``
  dict.  Returns ``None`` for the ``close`` verb (signals row deletion).

Both functions are pure — no I/O, no state mutation, no agent wiring.
Lives outside ``agents/strategist`` because it is shared by Executor
and MemoryWriter, neither of which is strategist-internal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from agents.strategist.position_thesis import PositionThesis
from agents.strategist.stance_schema import TickerStance


@dataclass(frozen=True)
class BrokerCall:
    """A target-weight broker instruction emitted by ``resolve_broker_call``.

    The Executor translates a BrokerCall into a concrete broker
    ``submit_market`` call by computing the share delta from the current
    portfolio weight.  Kept as a thin record so the verb-dispatch
    helpers stay broker-agnostic.

    Attributes:
        ticker: Symbol the instruction applies to.
        action: ``"BUY"`` or ``"SELL"``.  ``BUY`` covers open / add;
            ``SELL`` covers trim / close.
        target_weight: Desired portfolio weight in ``[0, 1]`` after the
            call clears.  ``0.0`` means a full close.
    """

    ticker:        str
    action:        Literal["BUY", "SELL"]
    target_weight: float


def resolve_broker_call(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
) -> BrokerCall | None:
    """Map a stance to the broker call it requires (``None`` for no-trade).

    Pure function — no state mutation, no I/O.  Executor uses this to
    decide what to dispatch; MemoryWriter does not call it.

    Args:
        stance: The TickerStance emitted by the strategist (post-risk-gate).
        prior_row: The PositionThesis row (if any) currently held for
            ``stance.ticker``.  ``None`` when the ticker is flat.

    Returns:
        A BrokerCall for the four trading verbs (open / add / trim /
        close); ``None`` for the two no-trade verbs (hold / update).

    Raises:
        ValueError: if the stance is inconsistent with its precondition
            (e.g. close on a flat ticker, open on a held ticker).  Caller
            (Executor) is expected to surface these as a retryable LLM
            validation error.
    """

    verb = stance.intent

    # ── No-trade verbs short-circuit ─────────────────────────────────────────
    if verb in ("hold", "update"):
        return None

    # ── open: must be flat ───────────────────────────────────────────────────
    if verb == "open":
        if prior_row is not None:
            raise ValueError(
                f"open stance on {stance.ticker}: already held "
                f"(use 'add' to increase weight)."
            )
        return BrokerCall(
            ticker        = stance.ticker,
            action        = "BUY",
            target_weight = stance.preferred_weight,
        )

    # ── add / trim / close: must be held ─────────────────────────────────────
    if prior_row is None:
        raise ValueError(
            f"{verb} stance on {stance.ticker}: ticker is flat "
            f"(use 'open' to enter)."
        )

    if verb == "add":
        return BrokerCall(
            ticker        = stance.ticker,
            action        = "BUY",
            target_weight = stance.preferred_weight,
        )

    if verb == "trim":
        return BrokerCall(
            ticker        = stance.ticker,
            action        = "SELL",
            target_weight = stance.preferred_weight,
        )

    if verb == "close":
        return BrokerCall(
            ticker        = stance.ticker,
            action        = "SELL",
            target_weight = 0.0,
        )

    # Unreachable — Literal exhausted above; defensive guard for future
    # verb additions.
    raise ValueError(f"unknown stance.intent: {verb!r}")


def apply_stance_to_thesis(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
    fill_price: float | None,
    tick_id: str,
    as_of: datetime,
) -> PositionThesis | None:
    """Map a stance + fill data to the new ``PositionThesis`` row.

    Pure function.  MemoryWriter calls this once per stance and assembles
    the new ``state["user:positions"]`` dict from the results.

    Args:
        stance: The TickerStance emitted by the strategist.
        prior_row: The existing PositionThesis row for this ticker, or
            ``None`` when the ticker was flat.
        fill_price: The fill price from Executor's broker call, used to
            stamp ``opened_price`` on ``open`` and to leave existing
            ``opened_price`` untouched on add / trim.  ``None`` for
            hold / update (no broker call ran).
        tick_id: Current tick identifier; written to ``opened_tick_id``
            on open.
        as_of: Tick clock; written to ``opened_at`` (open) and
            ``last_reviewed_at`` (every verb that touches the row).

    Returns:
        - ``open``:   new PositionThesis seeded from the stance + fill_price.
        - ``add``:    prior_row with ``weight`` updated; review fields touched.
        - ``trim``:   prior_row with ``weight`` updated; review fields touched.
        - ``hold``:   prior_row with review fields touched; nothing else changes.
        - ``update``: prior_row with target / stop / catalyst / horizon
                      conditionally mutated (only fields supplied by the
                      stance are touched); review fields touched.
        - ``close``:  ``None`` (signals the caller to delete the row).

    Raises:
        ValueError: if the stance is inconsistent with its precondition
            (mirrors ``resolve_broker_call``).
    """

    verb = stance.intent

    # ── close: signal deletion ───────────────────────────────────────────────
    if verb == "close":
        return None

    # ── open: seed a brand-new row ───────────────────────────────────────────
    if verb == "open":
        if prior_row is not None:
            raise ValueError(
                f"open stance on {stance.ticker}: already held."
            )
        if fill_price is None:
            raise ValueError(
                f"open stance on {stance.ticker}: fill_price is required "
                f"to seed opened_price."
            )

        # Open is the row's first review — last_reviewed_decision='open'.
        return PositionThesis(
            ticker                  = stance.ticker,
            opened_at               = as_of,
            opened_tick_id          = tick_id,
            opened_price            = fill_price,
            weight                  = stance.preferred_weight,
            target_price            = stance.target_price,
            stop_price              = stance.stop_price,
            catalyst                = stance.catalyst,
            horizon                 = stance.horizon,
            rationale               = stance.rationale,
            last_reviewed_at        = as_of,
            last_reviewed_decision  = "open",
            last_reviewed_reason    = "Initial entry.",
        )

    # ── add / trim / hold / update: prior_row required ───────────────────────
    if prior_row is None:
        raise ValueError(
            f"{verb} stance on {stance.ticker}: ticker is flat."
        )

    # Field-by-field copy so we never accidentally mutate the input row.
    new_fields: dict = prior_row.model_dump()

    # Every non-close verb touches the review trail.
    new_fields["last_reviewed_at"]       = as_of
    new_fields["last_reviewed_decision"] = verb
    new_fields["last_reviewed_reason"]   = stance.reason or ""

    if verb in ("add", "trim"):
        # Weight is mutated; commitments stay frozen unless the LLM
        # supplies them (treated as 'add + update' shorthand).
        new_fields["weight"] = stance.preferred_weight

    if verb == "update" or verb == "add":
        # Conditional commitment mutation — only fields the LLM supplied
        # overwrite their prior values.  Rationale is NEVER mutated
        # (Invariant 3); the schema permits stance.rationale to be set
        # for the strategist's own audit trail but the executor ignores
        # the value here.
        if stance.target_price is not None:
            new_fields["target_price"] = stance.target_price
        if stance.stop_price   is not None:
            new_fields["stop_price"]   = stance.stop_price
        if stance.catalyst     is not None:
            new_fields["catalyst"]     = stance.catalyst
        if stance.horizon      is not None:
            new_fields["horizon"]      = stance.horizon

    return PositionThesis.model_validate(new_fields)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_verb_dispatch.py -v
```

Expected: PASS — all eleven tests green.

- [ ] **Step 5: Commit**

```bash
git add src/agents/_verb_dispatch.py tests/unit/agents/test_verb_dispatch.py
git commit -m "$(cat <<'EOF'
feat(agents): add verb-dispatch helpers shared by Executor + MemoryWriter (Spec B Band 3)

resolve_broker_call(stance, prior_row) -> BrokerCall | None — Executor uses
this to dispatch the broker call (or skip for hold / update).

apply_stance_to_thesis(stance, prior_row, fill_price, tick_id, as_of)
-> PositionThesis | None — MemoryWriter uses this to assemble the
post-tick state["user:positions"] dict.  Returns None on close
(signals deletion).

Both functions are pure — no I/O, no state mutation, no agent wiring.
Rationale is never mutated (Invariant 3).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Band 4 — Writer-of-record refactor

### Task 12: Extend MemoryWriter to write `user:positions` + `user:thesis`

**Files:**
- Modify: `src/agents/memory/writer.py`
- Test: `tests/unit/agents/memory_writer/test_memory_writer.py` (new — note the directory rename for clarity vs the source path)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/agents/memory_writer/test_memory_writer.py
"""MemoryWriter — user:positions + user:thesis assembly (Spec B).

Reads:
  - state["strategist_decision"] for stances + thesis_revision
  - state["executions"] for Executor's fill prices
  - prior state["user:positions"] and state["user:thesis"]

Yields one Event with state_delta carrying the new user:positions and
user:thesis (plus the pre-Spec-B memory_buffer / day_digest, which stay
bare-keyed until Spec C).
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

# ... fixture helpers ...


@pytest.mark.asyncio
async def test_memory_writer_assembles_new_positions_from_open_stance(
    memory_writer_fixture,
):
    """An open stance produces a new user:positions[ticker] entry stamped
    with the executor's fill price."""

    state = memory_writer_fixture(
        stances=[{
            "ticker":          "AAPL",
            "intent":          "open",
            "preferred_weight":0.05,
            "conviction":      0.7,
            "rationale":       "Strong iPhone cycle.",
            "horizon":         "swing",
            "target_price":    200.0,
            "stop_price":      160.0,
            "catalyst":        "Holiday quarter guide.",
        }],
        thesis_revision=None,
        prior_positions={},
        prior_thesis="",
        executions=[{
            "stance":     {"ticker": "AAPL"},
            "fill_price": 176.10,
        }],
    )

    event = await _run_memory_writer(state)

    new_positions = event.actions.state_delta["user:positions"]
    assert "AAPL" in new_positions
    assert new_positions["AAPL"]["opened_price"] == 176.10
    assert new_positions["AAPL"]["last_reviewed_decision"] == "open"


@pytest.mark.asyncio
async def test_memory_writer_uses_executor_fill_price_for_opened_price(
    memory_writer_fixture,
):
    """opened_price comes from executions[].fill_price, NOT from the stance."""
    # ... see above pattern; assert opened_price == executions[0]["fill_price"]


@pytest.mark.asyncio
async def test_memory_writer_carries_forward_user_thesis_when_revision_null(
    memory_writer_fixture,
):
    """When thesis_revision is None, user:thesis keeps its prior value."""

    state = memory_writer_fixture(
        stances=[],
        thesis_revision=None,
        prior_positions={},
        prior_thesis="Holding existing macro thesis.",
        executions=[],
    )

    event = await _run_memory_writer(state)

    assert (
        event.actions.state_delta["user:thesis"]
        == "Holding existing macro thesis."
    )


@pytest.mark.asyncio
async def test_memory_writer_overwrites_user_thesis_when_revision_non_null(
    memory_writer_fixture,
):
    """When thesis_revision is set, MemoryWriter writes the new value."""

    state = memory_writer_fixture(
        stances=[],
        thesis_revision="Hawkish macro pivot detected.",
        prior_positions={},
        prior_thesis="Old thesis.",
        executions=[],
    )

    event = await _run_memory_writer(state)

    assert (
        event.actions.state_delta["user:thesis"]
        == "Hawkish macro pivot detected."
    )


@pytest.mark.asyncio
async def test_memory_writer_close_deletes_ticker_from_user_positions(
    memory_writer_fixture,
):
    """A close stance removes the ticker from the new user:positions dict."""
    # ... seed prior_positions with AAPL; emit a close stance; assert
    # "AAPL" not in event.actions.state_delta["user:positions"]


@pytest.mark.asyncio
async def test_memory_writer_hold_only_touches_review_fields(
    memory_writer_fixture,
):
    """A hold stance leaves identity / commitment fields untouched and only
    bumps last_reviewed_*."""
    # ... seed prior_positions with AAPL row; emit hold stance with reason;
    # assert opened_price unchanged, last_reviewed_decision == "hold".


@pytest.mark.asyncio
async def test_memory_writer_emits_single_state_delta_with_both_keys(
    memory_writer_fixture,
):
    """MemoryWriter must yield exactly one Event carrying both keys.

    Per Spec B "Crash recovery": cross-tick state is all-or-nothing per
    tick, so the two writes must ride a single state_delta.
    """

    state = memory_writer_fixture(
        stances=[],
        thesis_revision="X",
        prior_positions={},
        prior_thesis="",
        executions=[],
    )

    event = await _run_memory_writer(state)

    assert "user:positions" in event.actions.state_delta
    assert "user:thesis"    in event.actions.state_delta
```

Implement the `memory_writer_fixture` factory + `_run_memory_writer` helpers as test fixtures at the top of the file. Keep them small.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/memory_writer/test_memory_writer.py -v
```

Expected: FAIL — MemoryWriter currently writes bare-key `thesis`, doesn't touch `user:positions`, and doesn't consume `thesis_revision`.

- [ ] **Step 3: Extend MemoryWriter**

Edit `src/agents/memory/writer.py`. Inside `MemoryWriter._run_async_impl`, after the existing buffer / digest logic, add:

```python
        # ── Spec B: assemble user:positions + user:thesis ─────────────────
        # MemoryWriter is the writer-of-record for the cross-tick thesis
        # book (see docs/contract-invariants.md §A footnote).  Strategist
        # decides via stances + thesis_revision; we apply the verb-
        # dispatch helper to each stance and emit a single state_delta
        # carrying both keys.

        from agents._verb_dispatch import apply_stance_to_thesis
        from agents.strategist.position_thesis import PositionThesis

        prior_positions: dict[str, dict] = state.get("user:positions", {}) or {}
        prior_thesis:    str             = state.get("user:thesis", "") or ""

        # Index executions by ticker so we can look up the fill price for
        # the stance we are about to apply.  Executor's executions list
        # is keyed by the stance's ticker in the same order the LLM
        # emitted them; index for O(1) lookup.
        executions_by_ticker: dict[str, Any] = {}
        for ex in state.get("executions", []):
            t = (ex.get("stance") or {}).get("ticker")
            if t:
                executions_by_ticker[t] = ex

        # Start from a shallow copy of the prior dict so we can mutate
        # in-place.  Stances drive insertions / updates / deletions.
        new_positions: dict[str, dict] = dict(prior_positions)

        stances_raw = (
            decision.get("stances") if isinstance(decision, dict)
            else decision.stances
        ) or []

        for stance_raw in stances_raw:
            from agents.strategist.stance_schema import TickerStance
            stance = (
                TickerStance.model_validate(stance_raw)
                if isinstance(stance_raw, dict)
                else stance_raw
            )

            prior_row = (
                PositionThesis.model_validate(prior_positions[stance.ticker])
                if stance.ticker in prior_positions
                else None
            )

            fill_price = None
            ex = executions_by_ticker.get(stance.ticker)
            if ex is not None:
                fill_price = ex.get("fill_price")

            new_row = apply_stance_to_thesis(
                stance,
                prior_row  = prior_row,
                fill_price = fill_price,
                tick_id    = state.get("tick_id", ""),
                as_of      = entry_ts,
            )

            if new_row is None:
                # close — delete the row.
                new_positions.pop(stance.ticker, None)
            else:
                new_positions[stance.ticker] = new_row.model_dump(mode="json")

        # thesis_revision passthrough — non-null overwrites; null carries
        # the prior thesis forward (explicit re-write so the event payload
        # is always complete).
        thesis_revision = (
            decision.get("thesis_revision") if isinstance(decision, dict)
            else getattr(decision, "thesis_revision", None)
        )
        new_user_thesis = thesis_revision if thesis_revision is not None else prior_thesis
```

Then replace the existing yielded Event so it carries the new keys (and drops the bare `thesis` write — that becomes `user:thesis`):

```python
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                # Bare-keyed experiential memory — stays unchanged until
                # Spec C migrates it.
                "memory_buffer": memory_buffer_payload,
                "day_digest":    updated_digest,
                # ── Spec B: cross-tick thesis state ──────────────────────
                "user:positions": new_positions,
                "user:thesis":    new_user_thesis,
            }),
        )
```

Remove the line `state["thesis"] = new_thesis` and the bare `"thesis": new_thesis` entry from the state_delta — the legacy key is fully replaced.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/memory_writer/test_memory_writer.py -v
```

Expected: PASS — all seven tests green.

- [ ] **Step 5: Commit**

```bash
git add src/agents/memory/writer.py tests/unit/agents/memory_writer/test_memory_writer.py
git commit -m "$(cat <<'EOF'
feat(memory): MemoryWriter writes user:positions + user:thesis (Spec B Band 4)

MemoryWriter is now the writer-of-record for the cross-tick thesis
book.  It reads strategist stances + executor fills + the prior
user-scoped state, applies _verb_dispatch.apply_stance_to_thesis per
stance, and emits a single state_delta carrying both keys (plus the
pre-Spec-B memory_buffer / day_digest, which stay bare-keyed until
Spec C).  Crash recovery is all-or-nothing per tick because the writes
ride one event.

The bare-key `thesis` write is removed — its consumers migrate in
Plan 2 (the strategist context shim) and Band 5 (the test suite).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 13: Reshape Executor to broker calls only

**Files:**
- Modify: `src/agents/executor/agent.py`
- Test: `tests/unit/agents/executor/test_executor_state_delta_keys.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/agents/executor/test_executor_state_delta_keys.py
"""Executor state_delta hygiene (Spec B Band 4).

After Spec B's writer-of-record split, Executor's yielded state_delta
must carry only ``executions`` and ``last_executed_tick_id`` — never
``positions``, ``user:positions``, or ``user:thesis``.  Those are
MemoryWriter's responsibility.
"""
from __future__ import annotations

import pytest

# ... fixture helpers (FakeBroker, minimal state, etc.) ...


@pytest.mark.asyncio
async def test_executor_state_delta_carries_only_executions_and_idempotency(
    executor_fixture,
):
    """Executor's yielded Event must NOT carry positions / user:positions / user:thesis."""

    state, executor = executor_fixture(
        orders=[("AAPL", "BUY", 10)],
    )

    events = [e async for e in executor._run_async_impl(_ctx_for(state))]

    assert len(events) == 1
    delta = events[0].actions.state_delta

    assert set(delta.keys()) == {"executions", "last_executed_tick_id"}, (
        f"Executor must not touch user-scoped keys; got {set(delta.keys())}"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/executor/test_executor_state_delta_keys.py -v
```

Expected: FAIL — Executor currently yields `positions` in its state_delta.

- [ ] **Step 3: Remove the `positions` write from Executor**

Edit `src/agents/executor/agent.py`:

Delete line 192:
```python
        state["positions"]              = positions
```

Delete the inner `positions` lookup and mutation block (lines 53, 86-95, 107-120, 174-178 — every site that reads or writes `positions` in the executor). The Executor no longer needs the in-tick position book; MemoryWriter assembles it downstream from stances + executions.

The Executor's per-stance loop simplifies to: dispatch the broker call (via `_verb_dispatch.resolve_broker_call`), capture the fill, record the execution. The trade-log SELL handler currently lives in Executor — it stays for now (the SELL → trade-log handshake needs the prior thesis dict which Executor cannot reach post-split). Migrating it cleanly is out of scope for this band; mark it with a TODO referencing Spec C / the eventual lifecycle-hooks cleanup:

```python
            # TODO(Spec C / 2.5.4): the SELL → trade-log handshake reads
            # the prior thesis dict from state["positions"] today.  After
            # Spec B Band 4 the prior dict has moved to
            # state["user:positions"]; switch the read accordingly here
            # but keep the trade-log emit in Executor (it depends on the
            # broker portfolio quantity, which only Executor knows in
            # real time).  Drop the residual state["positions"] read
            # path entirely.
            prior_positions = state.get("user:positions", {}) or {}
```

Replace every `state["positions"]` read in the SELL block with `prior_positions` (the local snapshot above).

Update the yielded Event:

```python
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "executions":            executions,
                "last_executed_tick_id": tick_id,
                # NB: positions / user:positions are NOT written here.
                # MemoryWriter is the writer-of-record — see Spec B Band 4.
            }),
        )
```

Update the existing comment block around lines 210-224 (the "Cross-tick propagation" rationale) to reflect that Executor no longer carries cross-tick state; the comment moves to MemoryWriter where the responsibility now lives.

- [ ] **Step 4: Run the new test to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/executor/test_executor_state_delta_keys.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/executor/agent.py tests/unit/agents/executor/test_executor_state_delta_keys.py
git commit -m "$(cat <<'EOF'
refactor(executor): broker-calls-only — drop positions writes (Spec B Band 4)

Executor's state_delta now carries only executions and
last_executed_tick_id.  The position-book assembly that used to ride
Executor's yield moves to MemoryWriter (writer-of-record per Spec B).

The trade-log SELL handshake stays in Executor — it depends on the
broker portfolio quantity, which only Executor knows in real time.
It now reads state["user:positions"] instead of state["positions"]
for the prior thesis dict; a TODO marks the residual cross-coupling
for the eventual lifecycle-hooks cleanup (Spec C / 2.5.4).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 14: Add verb-aware skip rule to the risk gate

**Files:**
- Modify: `src/agents/risk_gate/agent.py`
- Test: `tests/unit/agents/risk_gate/test_risk_gate_verb_skip.py` (new or extended existing)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/agents/risk_gate/test_risk_gate_verb_skip.py
"""Risk gate verb-aware skip (Spec B Band 4).

hold and update stances pass through unchanged; open / add / trim / close
stances are still subject to the existing clamps.
"""
from __future__ import annotations

import pytest

# ... fixtures ...


@pytest.mark.asyncio
async def test_risk_gate_passes_hold_through_unchanged(risk_gate_fixture):
    """A hold stance is not clamped — its target_weight reaches the executor
    unchanged regardless of MAX_POSITION_WEIGHT."""
    # ... arrange a stance with intent=hold and weight > MAX_POSITION_WEIGHT
    # (impossible normally but synthetic for the test); assert
    # state["strategist_decision"].target_weights[ticker] is the original
    # value after the gate runs.


@pytest.mark.asyncio
async def test_risk_gate_passes_update_through_unchanged(risk_gate_fixture):
    """update is no-trade; the gate must not clamp it."""


@pytest.mark.asyncio
async def test_risk_gate_caps_open_at_max_position_weight(risk_gate_fixture):
    """An open stance proposing more than MAX_POSITION_WEIGHT is clamped down."""
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/risk_gate/test_risk_gate_verb_skip.py -v
```

Expected: FAIL — risk gate currently clamps all stances unconditionally.

- [ ] **Step 3: Add the verb-aware skip rule**

Edit `src/agents/risk_gate/agent.py`. Inside `_run_async_impl`, before the `apply_constraints(proposed, current_weights)` call:

```python
        # Spec B: hold and update are no-trade verbs — they articulate a
        # review or a thesis mutation but do not move capital.  Separate
        # the trading stances from the no-trade stances so the
        # constraint loop only sees stances the broker will actually
        # touch; this keeps cash-floor / turnover bookkeeping honest and
        # prevents a hold from spuriously triggering a clamp record.
        stances_by_ticker = {s.ticker: s for s in decision.stances}

        trading_weights:  dict[str, float] = {}
        no_trade_weights: dict[str, float] = {}

        for t, w in proposed.items():
            stance = stances_by_ticker.get(t)
            if stance is not None and stance.intent in ("hold", "update"):
                no_trade_weights[t] = w
            else:
                trading_weights[t] = w

        # Apply hard constraints to the trading subset only.
        clamps = apply_constraints(trading_weights, current_weights)

        # Re-merge the no-trade stances into the final weights dict — they
        # pass through at their original value.  Order is irrelevant
        # because the keys are disjoint by construction.
        proposed = {**trading_weights, **no_trade_weights}
```

Update the lifecycle-check loop (lines 77-84) to operate on the same `trading_weights` subset — hold / update stances never trigger a close-without-reason error because they never close.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/risk_gate/test_risk_gate_verb_skip.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/risk_gate/agent.py tests/unit/agents/risk_gate/test_risk_gate_verb_skip.py
git commit -m "$(cat <<'EOF'
feat(risk_gate): verb-aware skip — hold / update pass through (Spec B Band 4)

hold and update stances are no-trade verbs: they articulate a review
or a thesis mutation but do not move capital.  The risk gate now
separates the trading subset (open / add / trim / close) from the
no-trade subset and only applies hard constraints to the former.  This
keeps cash-floor / turnover bookkeeping honest and prevents spurious
clamp records on hold stances.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 15: Drop `positions` + `thesis` from `TickState`

**Files:**
- Modify: `src/orchestrator/state.py` (lines 82-86)

- [ ] **Step 1: Remove the migrated fields from the Pydantic mirror**

In `src/orchestrator/state.py`, the `TickState` model currently declares:

```python
    memory_buffer: list[Any]  = Field(default_factory=list)
    day_digest: str           = ""
    thesis: str               = ""
    positions: dict[str, Any] = Field(default_factory=dict)
    last_executed_tick_id: str | None = None
```

Replace with:

```python
    # ``memory_buffer`` / ``day_digest`` are still bare-keyed in state —
    # they migrate to ``user:`` scope in Spec C.  ``thesis`` and
    # ``positions`` have moved to ADK's user_state (read via state under
    # the ``user:`` prefix, NOT through this Pydantic mirror).  See
    # docs/contract-invariants.md §A and Spec B Band 4.
    memory_buffer: list[Any]  = Field(default_factory=list)
    day_digest: str           = ""
    last_executed_tick_id: str | None = None
```

- [ ] **Step 2: Run the orchestrator unit suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/ -v
```

Expected: PASS — no test references `TickState.positions` or `TickState.thesis`. If something fails, fix the call site to read from `state["user:positions"]` directly (bypassing the Pydantic mirror) and continue.

- [ ] **Step 3: Commit**

```bash
git add src/orchestrator/state.py
git commit -m "$(cat <<'EOF'
refactor(state): drop positions / thesis from TickState (Spec B Band 4)

Both fields have moved to ADK's user_state under the user:-prefix.
Pipeline agents read them from state directly (state["user:positions"]
/ state["user:thesis"]) rather than through the Pydantic mirror.
memory_buffer / day_digest stay in TickState until Spec C migrates
them.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Band 5 — Test migration + integration tests

### Task 16: Migrate the six pre-existing test files from `state["positions"]` → `state["user:positions"]`

**Files:**
- Modify: `tests/integration/test_executor_with_fake_broker.py`
- Modify: `tests/integration/test_strategist_v2_smoke.py`
- Modify: `tests/unit/executor/test_open_positions_state.py`
- Modify: `tests/executor/test_executor_bookkeeping.py`
- Modify: `tests/unit/backtest/test_driver_portfolio_refresh.py`

- [ ] **Step 1: Audit every grep hit**

```bash
grep -n 'state\["positions"\]' /home/oscarhill2012/Documents/Repository/StockBot/tests/
```

For each file, classify the hit:

| Hit pattern | Resolution |
|---|---|
| Read-side assertion (`assert "AAPL" in state["positions"]`) | Rewrite to read `state["user:positions"]`; the upstream pipeline now writes that key via MemoryWriter. |
| Direct seed (`state["positions"] = {...}`) | Rewrite to seed `state["user:positions"]` and route through a real `DatabaseSessionService` if the test exercises cross-tick persistence; in-process tests that just need the key can seed it directly in the dict. |
| Comment mentioning `state["positions"]` | Update prose to `state["user:positions"]`. |
| Test asserting Executor writes `positions` | The assertion is now wrong (Executor no longer writes); move it to a MemoryWriter test or delete it. |

- [ ] **Step 2: Apply the migrations**

For each file in the list above, replace the bare-key references with the user-scoped names. Where a test previously asserted Executor's `state["positions"]` write, point the assertion at MemoryWriter's `state["user:positions"]` write instead (the test fixtures may need a MemoryWriter invocation appended to the pipeline-under-test).

- [ ] **Step 3: Run each migrated test file individually**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_executor_with_fake_broker.py -v
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_strategist_v2_smoke.py -v
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/executor/test_open_positions_state.py -v
PYTHONPATH=src .venv/bin/python -m pytest tests/executor/test_executor_bookkeeping.py -v
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_driver_portfolio_refresh.py -v
```

Expected: each PASSes individually.

- [ ] **Step 4: Run the full unit + integration suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v
```

Expected: PASS — no remaining `state["positions"]` hits.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_executor_with_fake_broker.py tests/integration/test_strategist_v2_smoke.py tests/unit/executor/test_open_positions_state.py tests/executor/test_executor_bookkeeping.py tests/unit/backtest/test_driver_portfolio_refresh.py
git commit -m "$(cat <<'EOF'
test: migrate state["positions"] assertions to state["user:positions"] (Spec B Band 5)

Five test files exercise the executor / memory-writer / driver around
the position book.  Every assertion has been re-pointed at
state["user:positions"] (MemoryWriter's writer-of-record key) and the
ones that asserted Executor wrote positions have been moved or
deleted — Executor no longer touches the key.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 17: Migrate driver tests from `InMemorySessionService` to real `DatabaseSessionService`

**Files:**
- Modify: `tests/unit/backtest/test_driver_portfolio_refresh.py` (and any other driver tests under `tests/unit/backtest/`)

- [ ] **Step 1: Identify driver tests that mock `InMemorySessionService`**

```bash
grep -rn 'InMemorySessionService' /home/oscarhill2012/Documents/Repository/StockBot/tests/
```

- [ ] **Step 2: Replace mocks with a real in-memory `DatabaseSessionService`**

For each match, replace the mock construction with:

```python
from orchestrator.persistence import make_session_service

# In-memory aiosqlite — disappears at test teardown, no file written.
session_service = make_session_service(
    db_url="sqlite+aiosqlite:///:memory:",
)
```

Update the Driver constructor calls to pass `session_service=session_service` and `app_name="StockBot-test"` (the test app namespace).

- [ ] **Step 3: Run the migrated tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/ -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/backtest/
git commit -m "$(cat <<'EOF'
test(backtest): migrate driver tests to real DatabaseSessionService (Spec B Band 5)

InMemorySessionService mocks are replaced with an in-memory aiosqlite
DatabaseSessionService via make_session_service(db_url="sqlite+aiosqlite:///:memory:")
so the driver tests exercise the same persistence path the runner uses
in production.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 18: Add memory-backbone integration tests

**Files:**
- Create: `tests/integration/test_thesis_persistence_round_trip.py`
- Create: `tests/integration/test_namespace_partitioning.py`
- Create: `tests/integration/test_phase2_hydration_from_db_only.py`

- [ ] **Step 1: Write `test_thesis_persistence_round_trip.py`**

```python
"""End-to-end: user:positions survives session teardown + re-creation.

Spin up a DatabaseSessionService against an in-memory SQLite, create a
session for (app_name, user_id), write state["user:positions"] via
state_delta, close the session, create a NEW session for the SAME
(app_name, user_id), assert state["user:positions"] arrives populated.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from google.adk.events import Event, EventActions

from orchestrator.persistence import make_session_service


@pytest.mark.asyncio
async def test_user_positions_round_trips_across_sessions(tmp_path: Path):
    """The user_state row persists across session_id boundaries."""

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'session.sqlite'}"
    service = make_session_service(db_url=db_url)

    app_name = "StockBot-test-round-trip"
    user_id  = "stockbot"

    # ── Session A: write ─────────────────────────────────────────────────
    session_a = await service.create_session(
        app_name=app_name, user_id=user_id, state={},
    )

    payload = {"AAPL": {"ticker": "AAPL", "weight": 0.05}}

    await service.append_event(session_a, Event(
        author="test",
        invocation_id="inv-1",
        actions=EventActions(state_delta={"user:positions": payload}),
    ))

    # ── Session B: read ──────────────────────────────────────────────────
    session_b = await service.create_session(
        app_name=app_name, user_id=user_id, state={},
    )

    assert session_b.state.get("user:positions") == payload, (
        "Phase 2 hydration: ADK must merge the user_state row into the "
        "fresh session's state dict."
    )
```

- [ ] **Step 2: Write `test_namespace_partitioning.py`**

```python
"""Two app_names + one user_id → disjoint user_state rows.

Spec B namespace partitioning: paper / live / backtest occupy disjoint
user_state rows so a thesis written under one app_name never leaks
into another.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from google.adk.events import Event, EventActions

from orchestrator.persistence import make_session_service


@pytest.mark.asyncio
async def test_paper_and_live_user_states_are_disjoint(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'session.sqlite'}"
    service = make_session_service(db_url=db_url)

    user_id = "stockbot"

    # ── Write to the paper app_name ──────────────────────────────────────
    paper_session = await service.create_session(
        app_name="StockBot-paper", user_id=user_id, state={},
    )
    await service.append_event(paper_session, Event(
        author="test",
        invocation_id="inv-paper",
        actions=EventActions(state_delta={
            "user:positions": {"AAPL": {"weight": 0.05}},
        }),
    ))

    # ── Read from the live app_name ──────────────────────────────────────
    live_session = await service.create_session(
        app_name="StockBot-live", user_id=user_id, state={},
    )

    assert live_session.state.get("user:positions", {}) == {}, (
        "Live and paper must occupy disjoint user_state rows; the paper "
        "write must not appear in the live session."
    )
```

- [ ] **Step 3: Write `test_phase2_hydration_from_db_only.py`**

```python
"""Phase 2 hydration is purely DB-mediated — no in-process state survives.

Process A writes user:positions via state_delta; the in-memory
DatabaseSessionService is torn down; a NEW service instance is built
against the same SQLite file; process B creates a session and observes
the value.  No leftover in-process state can contribute — only the DB
row.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from google.adk.events import Event, EventActions

from orchestrator.persistence import make_session_service


@pytest.mark.asyncio
async def test_user_positions_survives_service_teardown(tmp_path: Path):
    db_path = tmp_path / "session.sqlite"
    db_url  = f"sqlite+aiosqlite:///{db_path}"
    payload = {"AAPL": {"ticker": "AAPL", "weight": 0.05}}

    # ── Process A: write, then drop service reference ────────────────────
    service_a = make_session_service(db_url=db_url)
    session_a = await service_a.create_session(
        app_name="StockBot-test-hydration", user_id="stockbot", state={},
    )
    await service_a.append_event(session_a, Event(
        author="test",
        invocation_id="inv-A",
        actions=EventActions(state_delta={"user:positions": payload}),
    ))
    del service_a, session_a

    # ── Process B: new service against the same file ─────────────────────
    service_b = make_session_service(db_url=db_url)
    session_b = await service_b.create_session(
        app_name="StockBot-test-hydration", user_id="stockbot", state={},
    )

    assert session_b.state.get("user:positions") == payload, (
        "Cross-process persistence: a brand-new SessionService instance "
        "must observe the same user_state row written by the previous one."
    )
```

- [ ] **Step 4: Run all three integration tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_thesis_persistence_round_trip.py tests/integration/test_namespace_partitioning.py tests/integration/test_phase2_hydration_from_db_only.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_thesis_persistence_round_trip.py tests/integration/test_namespace_partitioning.py tests/integration/test_phase2_hydration_from_db_only.py
git commit -m "$(cat <<'EOF'
test(integration): memory-backbone persistence + namespace tests (Spec B Band 5)

Three end-to-end tests covering the load-bearing guarantees of Spec B:

* round-trip: user:positions written in session A appears in session B
  for the same (app_name, user_id).
* namespace partitioning: paper and live app_names occupy disjoint
  user_state rows.
* hydration from DB only: a brand-new SessionService instance against
  the same SQLite file observes the prior process's write.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 19: Final green-bar pass + graphify delta

**Files:**
- Modify: `graphify-out/graph_delta.md` (append a dated Spec B Plan 1 entry)

- [ ] **Step 1: Run the full test suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -v
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/
```

Expected: PASS on tests; clean on ruff.

- [ ] **Step 2: Append the graph delta entry**

Add to `graphify-out/graph_delta.md`:

```markdown
## 2026-05-23 — Spec B Plan 1 (memory backbone)

### New modules
- `src/agents/strategist/position_thesis.py` — exports `PositionThesis`
  (Pydantic v2 model; opened_price required; six-verb closed-vocab
  ``last_reviewed_decision``).
- `src/agents/_verb_dispatch.py` — exports `BrokerCall`,
  `resolve_broker_call(stance, *, prior_row) -> BrokerCall | None`,
  `apply_stance_to_thesis(stance, *, prior_row, fill_price, tick_id,
  as_of) -> PositionThesis | None`.

### Schema additions
- `agents.strategist.stance_schema.TickerStance.intent: StanceVerb`
  with six values (open / add / trim / close / hold / update).
- `agents.strategist.stance_schema.TickerStance.reason: str | None`.
- `agents.strategist.schema.StrategistDecision.thesis_revision: str | None`.

### Schema deletions
- `agents.strategist.schema.PositionThesis` (moved to its own module).
- `orchestrator.state.TickState.positions` (migrated to user_state).
- `orchestrator.state.TickState.thesis` (migrated to user_state).

### New call edges
- `agents.memory.writer.MemoryWriter._run_async_impl` →
  `agents._verb_dispatch.apply_stance_to_thesis`.
- `agents.executor.agent.Executor._run_async_impl` →
  `agents._verb_dispatch.resolve_broker_call` (queued for Plan 2 if
  the SELL-handler refactor lands there instead).

### Removed call edges
- `agents.executor.agent.Executor._run_async_impl` no longer writes
  `state["positions"]` or yields it in its state_delta.
- `agents.memory.writer.MemoryWriter._run_async_impl` no longer
  writes bare-key `thesis` — the key migrates to `user:thesis`.

### State-key migrations
- `state["positions"]` → `state["user:positions"]`.
- `state["thesis"]` → `state["user:thesis"]`.

### Persistence wiring
- `orchestrator.persistence.make_session_service` gains an explicit
  `db_url` kwarg; backtest passes a per-run aiosqlite URL.
- `orchestrator.tick._resolve_app_name(broker)` dispatches the ADK
  `app_name` by broker mode (paper / live / test).
- `backtest.driver.Driver` now takes `session_service` and `app_name`
  constructor kwargs instead of building `InMemorySessionService`
  in-place.
```

- [ ] **Step 3: Commit (do NOT `git add graphify-out/`)**

The `graphify-out/` directory is gitignored per `.claude/CLAUDE.md`. The delta entry is informational only — it does not get committed. If the delta exceeds ~200 lines after this addition, flag it for the user and propose running `/graphify . --update`.

```bash
# No git add for graphify-out/ — verify before commit.
git status | grep -v graphify-out/
git diff --stat HEAD~1 HEAD
```

Expected: only `docs/contract-invariants.md`, `src/`, `tests/`, `scripts/` listed.

---

## Self-review checklist

Before handing the plan off, run the spec coverage / placeholder / type-consistency pass.

### Spec coverage

| Spec section | Plan task(s) |
|---|---|
| Contract amendments §A | Task 1 |
| Contract amendments §C-Rule 7 | Task 2 |
| `make_session_service` parameterisation | Task 3 |
| `tick.py` seeds + `app_name` dispatch | Task 4 |
| `driver.py` `DatabaseSessionService` switch | Task 5 |
| `runner.py` per-run SQLite + `--fresh` | Task 6 |
| `PositionThesis` model | Task 7 |
| Schema collision resolution | Task 8 |
| `TickerStance` `intent` + verb-conditional fields | Task 9 |
| `thesis_revision` field | Task 10 |
| `_verb_dispatch.py` shared helpers | Task 11 |
| MemoryWriter `user:positions` / `user:thesis` assembly | Task 12 |
| Executor reshape to broker-only | Task 13 |
| Risk-gate verb-aware skip | Task 14 |
| `TickState` field removal | Task 15 |
| Test migration (`state["positions"]` → `state["user:positions"]`) | Task 16 |
| Driver test mock → real `DatabaseSessionService` | Task 17 |
| Round-trip / namespace / hydration integration tests | Task 18 |
| Graph delta + green-bar verification | Task 19 |

Out of scope here (owned by Plan 2 or deferred):
- Held-view evolution-columns rewrite — Plan 2.
- `temp:strategist_mode` injection — Plan 2.
- D3 carry-forward removal in `derivation.py` — Plan 2 (Plan 1 only updates the `PositionThesis` import in `derivation.py` in Task 8).
- Strategist prompt template + cold-start / incremental framing — Plan 2.
- `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py` — Plan 2.
- Spec C (`memory_buffer` / `day_digest` migration).

### Placeholder scan

No `TBD`, `TODO` (other than the explicit Spec C / 2.5.4 cross-reference in Task 13), `implement later`, or "fill in details" markers.

### Type consistency

- `BrokerCall(target_weight: float, action: Literal["BUY","SELL"], ticker: str)` — used identically in Task 11 (implementation) and Tasks 13/16 (tests).
- `apply_stance_to_thesis(stance, *, prior_row, fill_price, tick_id, as_of)` — same kwargs in Task 11, Task 12, Task 18.
- `make_session_service(db_url: str | None = None)` — same signature in Task 3 (implementation), Task 6 (caller), Task 17 (test fixture).
- `PositionThesis.last_reviewed_decision` is `Literal["open","add","trim","hold","update"]` everywhere (no `"close"` member — close deletes the row).
- `StanceVerb` is `Literal["open","add","trim","close","hold","update"]` (six values; `last_reviewed_decision` is the five-value subset).

---

## Execution handoff

Plan complete and saved to `docs/Phase10-post-first-backtest/plans/spec-b-plan-1-memory-backbone.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task with two-stage review between tasks. Plan 2's subagent (running concurrently) coordinates around the shared files via the table at the top of this plan.

**2. Inline Execution** — Run tasks in-session via `superpowers:executing-plans` with checkpoints at each band boundary.

Which approach?

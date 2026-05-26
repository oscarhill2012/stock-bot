# Plan 04 — Lifecycle Parity (live ↔ backtest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live and backtest tick lifecycles structurally identical at session-creation time — same plugin install path for observability handles, same ISO-string `as_of` shape in `state`, and same `_STOCKBOT_TABLES` set derived from the ORM — so every later plan can be written against one harness.

**Architecture:** Extract the duplicated "build runner / seed session" logic from `src/orchestrator/tick.py` and `src/backtest/driver.py` into a shared `src/orchestrator/lifecycle_runner.py` helper that always installs a `HandleInjectorPlugin` (via `BasePlugin.before_run_callback`) and always ISO-coerces datetime values in the seed dict. Replace the hand-maintained `_STOCKBOT_TABLES` tuple in `src/lifecycle/{initialise,hard_reset}.py` with one derived from `Base.metadata.tables.keys()`.

**Tech Stack:** Python 3.12, Google ADK (`google-adk`), SQLAlchemy 2.x `DeclarativeBase`, pytest + `pytest-asyncio`.

**Findings addressed:** A-009 (ISO-coerce live `as_of`), A-010 (install `HandleInjectorPlugin` in live), A-011 (`_STOCKBOT_TABLES` from `Base.metadata`), A-047 (cross-lifecycle parity — supersedes A-010 framing). All four IDs verified in `docs/audits/2026-05-26-codebase-audit/FINDINGS.md` lines 80–99 and 335–339.

---

## Trust contract

**This plan trusts that Plans 01–03 have landed:**
- **Plan 01** has deleted `BufferEntryRow` from `src/orchestrator/persistence.py` (and `tests/unit/test_buffer_persistence.py`), so after Plan 01 the `Base.metadata.tables.keys()` set contains exactly `{"trade_log", "ticker_stances", "portfolio_snapshots", "analyst_evidence", "ticker_evidence"}` — five tables, none of them `buffer_entries`. If Plan 01 has not landed, **stop and fix Plan 01 first** — do not paper over by hand-listing the stale set.
- **Plan 01** has also deleted `scripts/trace_tick.py` (per intent §8.4, resolves A-012). That removes the only remaining production caller of the bare-key `_trace` install pattern, so the only mutation site this plan still needs to worry about is `src/backtest/driver.py:545-561` (which already uses the plugin) and `src/orchestrator/tick.py:243-247` (which has no plugin at all).
- **Plan 03** has supplied a canonical `Portfolio.from_state_value` classmethod plus the agreed shape of `state["portfolio"]` (and the renamed `temp:executor_positions_bridge` key). Plan 04 does not touch the portfolio shape — it only touches `as_of`, observability handles, and the table list. If Plan 03 has changed the `as_of` semantics in any consumer, reconcile in-pass rather than adding a defensive shim.

**Later plans trust this plan to land:**
- **Plan 05** (risk-gate / executor handoff), **Plan 10** (backtest hygiene) and **Plan 11** (test-suite consolidation) all assume `state["as_of"]` is an ISO string on both lifecycles, that `temp:_trace` and `temp:_decision_logger` are always installed via the plugin (never via post-`create_session` mutation), and that `STOCKBOT_TABLES` matches `Base.metadata`. They will not branch on lifecycle.

---

## Lifecycle map

The shape of the divergence today, and what must be true after this plan:

| Concern | Live (`src/orchestrator/tick.py`) — today | Backtest (`src/backtest/driver.py`) — today | After this plan (both) |
|---|---|---|---|
| `state["as_of"]` write | Raw `datetime.now(tz=UTC)` at `tick.py:148` — `DatabaseSessionService` then JSON-serialises it on the next `get_session`, masking the divergence at consumer call sites. | `(v.isoformat() if isinstance(v, _dt) else v)` comprehension at `driver.py:545-550`. | ISO-8601 string written by `_iso_coerce_state` in the shared helper. Every consumer reads via `resolve_as_of(state.get("as_of"), …)`. |
| `tick_phase` write | `"live"` literal at `tick.py:149`. | Schedule's `"open"`/`"close"` at `driver.py`. | Unchanged — both lifecycles continue to be sole authoritative writers of their own phase value. |
| `HandleInjectorPlugin` install | **None.** `Runner` constructed at `tick.py:230-234` with no `plugins=…`. `TraceWriter` / `DecisionLogger` never installed, so every `state.get("temp:_trace")` lookup in agents returns `None` (silent no-op). | Constructed with `plugins=[handle_injector]` at `driver.py:517-527`. | Both build `Runner` via `build_runner(...)` in `src/orchestrator/lifecycle_runner.py`. Plugin is **always** installed; passing `trace_writer=None, decision_logger=None` makes it a structural no-op but keeps the install path symmetric. |
| Seed-state filtering | None — raw dict from `_build_initial_state` passed to `create_session`. | Strips `temp:`-prefixed keys; ISO-coerces datetimes. | Both call `build_seed_state(state)` which strips `temp:` and ISO-coerces datetimes. |
| `_STOCKBOT_TABLES` | Hand-maintained tuple of 3 names in `src/lifecycle/initialise.py:21` (and dup in `hard_reset.py:17`). Preflight passes on stale rows in the 3 missing tables; `hard_reset` leaves them un-truncated on Postgres. | Same list, same bug. | Derived once from `Base.metadata.tables.keys()` in `src/lifecycle/_tables.py`; both `initialise.py` and `hard_reset.py` import it. |
| Post-`create_session` mutation | None today (no plugin, no handles). | None — the plugin path is precisely the fix. | **Forbidden.** Any future `adk_session.state["temp:_…"] = …` after `create_session` is a regression; flagged by a lint test (see Task 7). |

---

## File structure

**New files:**
- `src/orchestrator/lifecycle_runner.py` — `build_runner(...)`, `build_seed_state(...)`, `iso_coerce_state(...)` helpers. Both `tick.py` and `driver.py` use it.
- `src/lifecycle/_tables.py` — single source of truth for `STOCKBOT_TABLES`, derived from `Base.metadata`.

**Modified files:**
- `src/orchestrator/tick.py` — `_build_initial_state` writes `as_of` as ISO string; `run_once` delegates to `lifecycle_runner.build_runner`.
- `src/backtest/driver.py` — replace the ad-hoc seed-coerce comprehension + plugin construction with calls into `lifecycle_runner`.
- `src/lifecycle/initialise.py` — replace tuple at line 21 with `from lifecycle._tables import STOCKBOT_TABLES`.
- `src/lifecycle/hard_reset.py` — replace tuple at line 17 with the same import.
- `tests/unit/orchestrator/test_tick_as_of_phase.py` — rewrite assertion at lines 48-50 to expect ISO string, not `datetime` instance.
- `tests/unit/test_init_db_script.py` — derive `EXPECTED_TABLES` from `Base.metadata`, not from the hand-listed `{"buffer_entries", "trade_log", "portfolio_snapshots"}` literal at line 10.

**New tests:**
- `tests/unit/orchestrator/test_lifecycle_runner.py` — helper-level unit tests for `iso_coerce_state` and `build_runner`.
- `tests/integration/test_lifecycle_parity.py` — runs one mocked tick through both `run_once` (live) and `Driver.run_tick` (backtest) and asserts identical `state`-key shapes.
- `tests/unit/orchestrator/test_handle_injector_install.py` — proves the plugin survives `DatabaseSessionService` rehydration (the regression A-010 was designed to prevent).
- `tests/unit/test_no_post_create_session_temp_mutation.py` — AST lint test: forbids `state["temp:_…"] = …` assignments in any file that also calls `create_session`.

---

## Ordered changes

Rollout order is **plugin first, then `as_of`, then tables**:
1. The plugin gives both lifecycles a single working handle-install pathway.
2. Once both lifecycles share that pathway, switching live `as_of` from `datetime` to ISO string is a one-line change inside `_build_initial_state` plus one test rewrite — no consumer changes required because every consumer already calls `resolve_as_of`, which accepts both shapes (see `src/data/timeguard.py:126-143`).
3. Tables come last because the rewrite of `tests/unit/test_init_db_script.py` is a pure-test change with no production blast radius.

---

## Task 1 — Extract `iso_coerce_state` helper (pure function, TDD)

**Files:**
- Create: `src/orchestrator/lifecycle_runner.py`
- Test: `tests/unit/orchestrator/test_lifecycle_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/orchestrator/test_lifecycle_runner.py
"""Unit tests for the shared lifecycle runner helpers.

These cover the pure helpers (``iso_coerce_state``, ``build_seed_state``)
that both ``orchestrator.tick`` and ``backtest.driver`` rely on for
seed-state preparation.  ``build_runner`` itself is covered by the
integration parity test in ``tests/integration/test_lifecycle_parity.py``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from orchestrator.lifecycle_runner import build_seed_state, iso_coerce_state


def test_iso_coerce_state_converts_datetime_to_iso_string() -> None:
    """A naive-tz-aware ``datetime`` becomes its ISO-8601 string form."""

    dt = datetime(2026, 5, 26, 14, 30, tzinfo=UTC)
    out = iso_coerce_state({"as_of": dt, "tick_id": "tick-001"})

    assert out["as_of"] == dt.isoformat()
    assert out["tick_id"] == "tick-001"


def test_iso_coerce_state_leaves_non_datetime_values_untouched() -> None:
    """Strings, ints, lists, and dicts must pass through unchanged."""

    payload = {
        "tickers":     ["AAPL", "MSFT"],
        "portfolio":   {"cash": 1000.0, "positions": {}},
        "tick_phase":  "live",
        "as_of":       "2026-05-26T14:30:00+00:00",  # already-string passthrough
    }

    out = iso_coerce_state(payload)

    assert out == payload


def test_build_seed_state_strips_temp_prefixed_keys() -> None:
    """``temp:``-prefixed keys must not survive into ``create_session``."""

    payload = {
        "tick_id":              "tick-002",
        "as_of":                datetime(2026, 5, 26, tzinfo=UTC),
        "temp:_trace":          object(),     # observability handle
        "temp:_obs_news_call":  {"foo": 1},   # observability scratch
    }

    out = build_seed_state(payload)

    assert "tick_id" in out
    assert "as_of"   in out
    assert isinstance(out["as_of"], str), "as_of must be ISO-coerced en route"
    assert all(not k.startswith("temp:") for k in out), (
        f"temp: keys leaked into seed state: {[k for k in out if k.startswith('temp:')]}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_lifecycle_runner.py -v`
Expected: `ModuleNotFoundError: No module named 'orchestrator.lifecycle_runner'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/orchestrator/lifecycle_runner.py
"""Shared lifecycle-runner helpers used by both live and backtest ticks.

Live and backtest both build a per-tick ``Runner`` against the same
pipeline, against an ADK session whose ``state`` is JSON-serialised by
``DatabaseSessionService``.  This module owns the two invariants both
lifecycles must satisfy *identically* at session-creation time:

1. ``temp:``-prefixed keys must be stripped from the seed dict (ADK
   strips them anyway during persistence; passing them through silently
   wastes the round-trip and gives a false sense that the handle has
   been installed).
2. ``datetime`` values must be ISO-coerced (``DatabaseSessionService``
   serialises via ``json.dumps``, which raises on raw ``datetime``).

It also owns the canonical place to construct the ``Runner`` with
``HandleInjectorPlugin`` so both lifecycles share one install path.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def iso_coerce_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``state`` with ``datetime`` values ISO-stringified.

    Parameters
    ----------
    state:
        The raw seed dict produced by the lifecycle's ``_build_initial_state``
        (live) or per-tick state builder (backtest).  May contain
        ``datetime`` values under any key — most commonly ``"as_of"``.

    Returns
    -------
    dict[str, Any]
        A shallow copy of ``state`` where every ``datetime`` value has been
        replaced with its ``.isoformat()`` string.  All other values pass
        through unchanged.

    Notes
    -----
    Consumers downstream (``data.timeguard.resolve_as_of``) accept either
    ``datetime`` or ISO ``str``, so passing through pre-stringified values
    is safe.  We do **not** recurse into nested dicts — the only datetime
    fields we own at this layer are top-level (``as_of``); nested data
    structures are model_dump'd to JSON-safe shapes by their respective
    writers.
    """

    # Shallow copy with per-value coercion — keeps the helper pure (no
    # mutation of the caller's dict) and trivially testable.
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in state.items()
    }


def build_seed_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitised seed dict suitable for ``create_session(state=…)``.

    Strips ``temp:``-prefixed keys (ADK discards them at persistence time
    anyway; per-invocation handles like ``temp:_trace`` are injected by
    :class:`observability.handle_injector_plugin.HandleInjectorPlugin`'s
    ``before_run_callback`` instead) and ISO-coerces datetime values via
    :func:`iso_coerce_state`.

    Parameters
    ----------
    state:
        The raw per-tick state dict produced by either lifecycle's
        initial-state builder.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable dict safe to pass to
        ``DatabaseSessionService.create_session(state=…)``.
    """

    # Strip first, then coerce — strip is by key (cheap), coerce walks
    # values (slightly costlier).  Order doesn't matter for correctness
    # but this minimises the work iso_coerce_state does.
    stripped = {k: v for k, v in state.items() if not k.startswith("temp:")}
    return iso_coerce_state(stripped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_lifecycle_runner.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/lifecycle_runner.py tests/unit/orchestrator/test_lifecycle_runner.py
git commit -m "feat(lifecycle): extract iso_coerce_state + build_seed_state helpers"
```

---

## Task 2 — Add `build_runner` to `lifecycle_runner` (always installs plugin)

**Files:**
- Modify: `src/orchestrator/lifecycle_runner.py`
- Test: `tests/unit/orchestrator/test_handle_injector_install.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/orchestrator/test_handle_injector_install.py
"""Regression test for A-010 / A-047 — proves ``HandleInjectorPlugin``
is installed via ``Runner(plugins=…)`` and survives ``DatabaseSessionService``
rehydration.

The bug this test pins: prior to this plan, the live lifecycle never
installed the plugin, so every ``state.get("temp:_trace")`` lookup in
agents returned ``None`` (silent no-op).  Post-fix, both lifecycles use
``build_runner``, which always constructs the plugin.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator.lifecycle_runner import build_runner


def test_build_runner_always_installs_handle_injector_plugin() -> None:
    """Even when both handles are ``None``, the plugin must still be
    registered so the install path is structurally identical to the
    backtest path."""

    pipeline = MagicMock(name="pipeline")
    session_service = MagicMock(name="session_service")

    runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-test",
        session_service = session_service,
        trace_writer    = None,
        decision_logger = None,
    )

    # The runner's plugin list must contain exactly one
    # HandleInjectorPlugin instance (other plugins may be added in
    # future, but the handle injector is mandatory).
    from observability.handle_injector_plugin import HandleInjectorPlugin

    injectors = [p for p in runner.plugins if isinstance(p, HandleInjectorPlugin)]
    assert len(injectors) == 1, (
        f"build_runner must install exactly one HandleInjectorPlugin; "
        f"got {len(injectors)} (plugins: {[type(p).__name__ for p in runner.plugins]})"
    )


def test_build_runner_passes_handles_through_to_plugin() -> None:
    """When handles are supplied, the plugin must hold them by closure
    for ``before_run_callback`` to install onto the live invocation state."""

    pipeline = MagicMock(name="pipeline")
    session_service = MagicMock(name="session_service")
    tw = MagicMock(name="trace_writer")
    dl = MagicMock(name="decision_logger")

    runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-test",
        session_service = session_service,
        trace_writer    = tw,
        decision_logger = dl,
    )

    from observability.handle_injector_plugin import HandleInjectorPlugin

    injector = next(p for p in runner.plugins if isinstance(p, HandleInjectorPlugin))
    assert injector._trace_writer is tw
    assert injector._decision_logger is dl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_handle_injector_install.py -v`
Expected: `ImportError: cannot import name 'build_runner' from 'orchestrator.lifecycle_runner'`.

- [ ] **Step 3: Add `build_runner` to the helper module**

Append to `src/orchestrator/lifecycle_runner.py`:

```python
from typing import Any as _Any  # noqa: E402  (kept local to imports block when refactoring)


def build_runner(
    *,
    agent:           _Any,
    app_name:        str,
    session_service: _Any,
    trace_writer:    _Any | None = None,
    decision_logger: _Any | None = None,
    extra_plugins:   list[_Any] | None = None,
) -> _Any:
    """Construct an ADK ``Runner`` with ``HandleInjectorPlugin`` always installed.

    Both live (``orchestrator.tick.run_once``) and backtest
    (``backtest.driver.Driver.run_tick``) must build their per-tick
    runner through this helper so the observability-handle install
    pathway is structurally identical.  The plugin is registered even
    when both ``trace_writer`` and ``decision_logger`` are ``None`` —
    in that case its ``before_run_callback`` is a no-op, but the install
    path stays symmetric and future handles only need to be wired here.

    Parameters
    ----------
    agent:
        The root pipeline agent (typically a ``SequentialAgent``).
    app_name:
        ADK app_name partition.  Live uses ``"StockBot-{live,paper}"``;
        backtest uses ``"StockBot-backtest-{window_key}"``.
    session_service:
        Either an ``InMemorySessionService`` (tests) or a
        ``DatabaseSessionService`` (live / backtest).
    trace_writer:
        Optional :class:`observability.trace.TraceWriter`.  When ``None``
        the plugin does not install ``state["temp:_trace"]``.
    decision_logger:
        Optional :class:`backtest.decision_logger.DecisionLogger`.  When
        ``None`` the plugin does not install ``state["temp:_decision_logger"]``.
    extra_plugins:
        Optional list of additional ``BasePlugin`` instances appended
        after the handle injector.  Defaults to no extra plugins.

    Returns
    -------
    google.adk.Runner
        A ``Runner`` ready to ``run_async`` against a session created on
        ``session_service``.

    Notes
    -----
    Direct ``adk_session.state["temp:_…"] = …`` mutation *after*
    ``create_session`` is silently discarded by ADK (the runner calls
    ``get_session`` again, which rebuilds state from persisted storage
    and strips ``temp:`` keys).  This helper is the *only* sanctioned
    way to wire observability handles into a tick.
    """

    # Deferred import — keeps the module import-light for tests that
    # mock the ADK Runner entirely (and avoids forcing google-adk at
    # tooling-import time).
    from google.adk import Runner

    from observability.handle_injector_plugin import HandleInjectorPlugin

    # Always construct the plugin, even when both handles are None —
    # the install path must be structurally identical across lifecycles.
    handle_injector = HandleInjectorPlugin(
        trace_writer    = trace_writer,
        decision_logger = decision_logger,
    )

    plugins = [handle_injector]
    if extra_plugins:
        plugins.extend(extra_plugins)

    return Runner(
        agent           = agent,
        app_name        = app_name,
        session_service = session_service,
        plugins         = plugins,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_handle_injector_install.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/lifecycle_runner.py tests/unit/orchestrator/test_handle_injector_install.py
git commit -m "feat(lifecycle): add build_runner helper that always installs HandleInjectorPlugin"
```

---

## Task 3 — Switch live `run_once` to `build_runner` + ISO-coerce `as_of`

**Files:**
- Modify: `src/orchestrator/tick.py:148` (the `as_of` literal) and `src/orchestrator/tick.py:229-247` (Runner + create_session block)
- Modify: `tests/unit/orchestrator/test_tick_as_of_phase.py:46-50` (rewrite cementing assertion)

- [ ] **Step 1: Rewrite the cementing test to expect ISO string**

Edit `tests/unit/orchestrator/test_tick_as_of_phase.py`. Replace the assertion block at lines 46-57 (everything from `# ``as_of`` must be present` through the wall-clock window check) with:

```python
    # ``as_of`` must be present as an ISO-8601 string.  Plan 04 mandates
    # ISO-coercion at the live state-boundary — ``DatabaseSessionService``
    # cannot persist raw datetime objects, and parity with the backtest
    # lifecycle requires both writers to emit the same shape.  Consumers
    # call ``data.timeguard.resolve_as_of`` which round-trips the string
    # back to a tz-aware ``datetime``.
    assert "as_of" in state, "live builder must seed state['as_of']"
    as_of_raw = state["as_of"]
    assert isinstance(as_of_raw, str), (
        f"as_of must be ISO-stringified at the state boundary; "
        f"got {type(as_of_raw).__name__!r} = {as_of_raw!r}"
    )

    # Parse back and confirm tz-aware UTC + within the wall-clock window
    # the test captured.  Five seconds of slack covers any in-process drift.
    from datetime import timedelta as _td
    as_of = datetime.fromisoformat(as_of_raw)
    assert as_of.tzinfo is not None, "as_of must be timezone-aware"
    assert as_of.utcoffset() == _td(0), "as_of must be in UTC"
    assert before - _td(seconds=5) <= as_of <= after + _td(seconds=5), (
        f"as_of {as_of} must be within wall-clock window [{before}, {after}]"
    )
```

- [ ] **Step 2: Run the test to verify it now fails against the current code**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_tick_as_of_phase.py -v`
Expected: FAIL on `isinstance(as_of_raw, str)` — current code writes a `datetime`.

- [ ] **Step 3: ISO-coerce the live `as_of` write**

Edit `src/orchestrator/tick.py`. Replace line 148:

```python
        "as_of":      datetime.now(tz=UTC),
```

with:

```python
        # ISO-stringified at the state boundary — DatabaseSessionService
        # JSON-serialises state and cannot persist raw datetime objects.
        # Parity invariant: backtest writes the same shape; every consumer
        # reads via ``data.timeguard.resolve_as_of`` which round-trips the
        # string back to a tz-aware ``datetime``.  See plan 04.
        "as_of":      datetime.now(tz=UTC).isoformat(),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_tick_as_of_phase.py -v`
Expected: PASS.

- [ ] **Step 5: Switch `run_once` to use `build_runner` + `build_seed_state`**

Edit `src/orchestrator/tick.py`. Replace the block at lines 229-247 (from `session_service = make_session_service()` through the `create_session` call) with:

```python
    from orchestrator.lifecycle_runner import build_runner, build_seed_state

    session_service = make_session_service()

    # Parity: build the runner through the shared helper so the
    # HandleInjectorPlugin is always installed on the same code path
    # the backtest driver uses.  Live currently has no TraceWriter or
    # DecisionLogger wired in (both default to None) — the plugin
    # registers as a structural no-op, but the install pathway is
    # symmetric with the backtest driver so future handle wiring lands
    # in exactly one place.
    runner = build_runner(
        agent           = pipeline,
        app_name        = _app_name,
        session_service = session_service,
        trace_writer    = None,
        decision_logger = None,
    )

    # Create a fresh session with the minimal state every tick needs.
    # Portfolio is seeded from the broker so the strategist's held-view
    # callback renders real holdings on the very first tick.
    # Cross-tick state (user:positions, user:thesis) is NOT seeded here —
    # ADK's user_state merge hydrates it from the DB row on session create
    # (Spec B: docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md).
    initial_state = await _build_initial_state(broker, tick_id, tickers)
    adk_session = await session_service.create_session(
        app_name = _app_name,
        user_id  = "stockbot",
        # build_seed_state strips temp: keys and ISO-coerces datetimes —
        # parity with backtest.driver.Driver.run_tick.
        state    = build_seed_state(initial_state),
    )
```

- [ ] **Step 6: Run the tick test suite to verify nothing regressed**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/ tests/unit/test_tick_entrypoint.py -v`
Expected: all pass. If any test mocks `Runner` and now also needs to mock `build_runner`, fix the mock target inline (the typical fix is `patch("orchestrator.tick.build_runner")` instead of `patch("orchestrator.tick.Runner")`).

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/tick.py tests/unit/orchestrator/test_tick_as_of_phase.py
git commit -m "fix(tick): ISO-coerce as_of and install HandleInjectorPlugin in live lifecycle (A-009, A-010)"
```

---

## Task 4 — Switch backtest driver to `build_runner` + `build_seed_state`

**Files:**
- Modify: `src/backtest/driver.py:505-561` (the seed/runner block)

- [ ] **Step 1: Replace the duplicated runner-construction + seed-coerce block**

Edit `src/backtest/driver.py`. Replace the block from line 505 (`session_service = make_session_service(db_url=self._session_db_url)`) through line 561 (the end of the `create_session` call) with:

```python
        from orchestrator.lifecycle_runner import build_runner, build_seed_state

        # One shared session service instance per tick — backed by the
        # per-run SQLite file so user-scoped state (user:positions,
        # user:thesis) persists across ticks within this run.
        session_service = make_session_service(db_url=self._session_db_url)

        # Parity: build the runner through the shared helper.  The
        # HandleInjectorPlugin is the *only* sanctioned way to install
        # per-invocation observability handles — direct mutation of
        # ``adk_session.state`` after ``create_session`` is silently
        # discarded by ADK (see src/observability/handle_injector_plugin.py).
        runner = build_runner(
            agent           = pipeline,
            app_name        = app_name,
            session_service = session_service,
            trace_writer    = tw,
            decision_logger = self._dl,
        )

        # Use a UUID suffix to guarantee session uniqueness even if the
        # deterministic tick_id is the same across driver instances (e.g. in
        # parallel test processes).
        session_id = f"{state['tick_id']}-{uuid.uuid4().hex[:8]}"

        # build_seed_state strips temp:-prefixed keys (ADK discards them
        # at persistence time anyway; handles are injected by the plugin)
        # and ISO-coerces datetime values (DatabaseSessionService
        # serialises via json.dumps).
        adk_session = await session_service.create_session(
            app_name   = app_name,
            user_id    = "stockbot",
            state      = build_seed_state(state),
            session_id = session_id,
        )
```

Also remove the now-unused `from observability.handle_injector_plugin import HandleInjectorPlugin` import at `src/backtest/driver.py:33` (the helper imports it internally). Confirm with `grep -n HandleInjectorPlugin src/backtest/driver.py` — only the comment reference should remain. If the comment reference is the only remaining mention, leave the comment in place but drop the import.

- [ ] **Step 2: Run the backtest driver unit + integration tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_driver_one_tick.py tests/integration/backtest/test_end_to_end_smoke.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/backtest/driver.py
git commit -m "refactor(backtest): route driver through shared build_runner / build_seed_state helpers"
```

---

## Task 5 — Add the parity integration test

**Files:**
- Create: `tests/integration/test_lifecycle_parity.py`

- [ ] **Step 1: Write the parity test**

```python
# tests/integration/test_lifecycle_parity.py
"""Cross-lifecycle parity — A-047.

Runs the same minimal pipeline through both the live (``orchestrator.tick``)
and backtest (``backtest.driver``) entry points with a stubbed broker and
asserts the resulting session state has the same key shape: ``as_of`` is an
ISO string on both, ``tick_phase`` is present on both, and the
``HandleInjectorPlugin`` is installed on both runners.

This test is the structural canary for plans 05, 06, and 10 — they all
assume one harness, and this test fails fast the moment either lifecycle
drifts.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.lifecycle_runner import build_runner, build_seed_state


@pytest.mark.asyncio
async def test_live_seed_state_has_iso_as_of_and_no_temp_keys() -> None:
    """Live ``_build_initial_state`` → ``build_seed_state`` round-trip must
    produce an ISO ``as_of`` string and no ``temp:``-prefixed keys."""

    from orchestrator.tick import _build_initial_state

    broker = MagicMock()
    portfolio = MagicMock()
    portfolio.model_dump.return_value = {"cash": 0.0, "positions": {}}
    broker.get_portfolio = AsyncMock(return_value=portfolio)

    with patch(
        "orchestrator.tick._fetch_reference_prices",
        new=AsyncMock(return_value={}),
    ):
        raw_state = await _build_initial_state(
            broker, tick_id="tick-parity-001", tickers=["AAPL"],
        )

    seed = build_seed_state(raw_state)

    assert isinstance(seed["as_of"], str), (
        f"live seed as_of must be ISO string; got {type(seed['as_of']).__name__}"
    )
    # Round-trip parses cleanly.
    parsed = datetime.fromisoformat(seed["as_of"])
    assert parsed.tzinfo is not None
    assert seed.get("tick_phase") == "live"
    assert all(not k.startswith("temp:") for k in seed)


def test_backtest_seed_state_has_iso_as_of_and_no_temp_keys() -> None:
    """The backtest per-tick state built by the driver feeds through the
    same ``build_seed_state`` and must yield identically-shaped output."""

    # Build a minimal driver-style state dict (the driver builds richer
    # ones, but the boundary helper only cares about ``as_of`` shape +
    # ``temp:`` stripping).
    raw_state = {
        "tick_id":        "tick-parity-002",
        "as_of":          datetime(2026, 5, 26, 14, 30, tzinfo=UTC),
        "tick_phase":     "open",
        "tickers":        ["AAPL"],
        "temp:_trace":    object(),  # would be installed by plugin instead
    }

    seed = build_seed_state(raw_state)

    assert isinstance(seed["as_of"], str)
    parsed = datetime.fromisoformat(seed["as_of"])
    assert parsed.tzinfo is not None
    assert seed.get("tick_phase") == "open"
    assert all(not k.startswith("temp:") for k in seed)


def test_both_lifecycles_install_handle_injector_plugin() -> None:
    """Whichever code path constructs the runner (live or backtest), the
    HandleInjectorPlugin must end up in ``runner.plugins``."""

    from observability.handle_injector_plugin import HandleInjectorPlugin

    pipeline = MagicMock(name="pipeline")
    session_service = MagicMock(name="session_service")

    live_runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-live",
        session_service = session_service,
        trace_writer    = None,
        decision_logger = None,
    )

    bt_runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-backtest-xyz",
        session_service = session_service,
        trace_writer    = MagicMock(name="tw"),
        decision_logger = MagicMock(name="dl"),
    )

    assert any(isinstance(p, HandleInjectorPlugin) for p in live_runner.plugins)
    assert any(isinstance(p, HandleInjectorPlugin) for p in bt_runner.plugins)
```

- [ ] **Step 2: Run the parity test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_lifecycle_parity.py -v`
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_lifecycle_parity.py
git commit -m "test(lifecycle): add cross-lifecycle parity integration test (A-047)"
```

---

## Task 6 — Derive `STOCKBOT_TABLES` from `Base.metadata`

**Files:**
- Create: `src/lifecycle/_tables.py`
- Modify: `src/lifecycle/initialise.py:21` (replace tuple literal with import)
- Modify: `src/lifecycle/hard_reset.py:17` (replace tuple literal with import)
- Modify: `tests/unit/test_init_db_script.py:10` (replace hand-listed set with `Base.metadata`-derived set)

- [ ] **Step 1: Write the failing test (rewrite of the cementing one)**

Replace the entire contents of `tests/unit/test_init_db_script.py` with:

```python
# tests/unit/test_init_db_script.py
"""init_db creates *all* StockBot ORM tables, and `_STOCKBOT_TABLES`
matches `Base.metadata` exactly (A-011 regression).

Historically this test hand-listed three table names and silently
agreed with the buggy lifecycle tuple.  Plan 04 derives the expected
set from ``Base.metadata.tables.keys()`` directly so a future ORM
table can never silently fall out of preflight / hard_reset coverage.
"""
from __future__ import annotations

from sqlalchemy import inspect

from lifecycle._tables import STOCKBOT_TABLES
from orchestrator.persistence import Base, make_engine
from scripts.init_db import init_db


def test_stockbot_tables_set_matches_orm_metadata_exactly() -> None:
    """The lifecycle table set MUST equal ``Base.metadata.tables.keys()``
    — any drift means preflight / hard_reset silently misses an ORM table."""

    assert set(STOCKBOT_TABLES) == set(Base.metadata.tables.keys()), (
        f"STOCKBOT_TABLES drifted from Base.metadata: "
        f"only in tuple = {set(STOCKBOT_TABLES) - set(Base.metadata.tables.keys())}; "
        f"only in metadata = {set(Base.metadata.tables.keys()) - set(STOCKBOT_TABLES)}"
    )


def test_init_db_creates_every_orm_table(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    # The script must create every ORM table — derived expectation,
    # not a hand-maintained literal.
    assert set(Base.metadata.tables.keys()).issubset(tables)


def test_init_db_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    init_db(f"sqlite:///{db_path}")  # second run must not raise
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables.keys()).issubset(tables)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_init_db_script.py -v`
Expected: `ModuleNotFoundError: No module named 'lifecycle._tables'`.

- [ ] **Step 3: Create the `_tables` module**

```python
# src/lifecycle/_tables.py
"""Single source of truth for the StockBot table list used by
``lifecycle.initialise._check_live_tables_empty`` and
``lifecycle.hard_reset._row_counts`` / ``_archive_*`` / ``_truncate_live``.

Derived from ``Base.metadata.tables.keys()`` so any ORM table added or
removed in ``src/orchestrator/persistence.py`` is automatically picked
up by both preflight and hard-reset — closing the A-011 silent-failure
where a hand-maintained tuple let preflight pass on stale rows in
ORM tables not listed in the tuple.
"""
from __future__ import annotations

from orchestrator.persistence import Base


# Tuple (not a set) so iteration order is stable and the archive /
# truncate operations process tables in the same deterministic order
# every run.  ``Base.metadata.tables`` is an ``immutabledict`` whose
# ordering reflects ORM-declaration order in persistence.py.
STOCKBOT_TABLES: tuple[str, ...] = tuple(Base.metadata.tables.keys())
```

- [ ] **Step 4: Swap `initialise.py` and `hard_reset.py` to import the shared tuple**

Edit `src/lifecycle/initialise.py`. Replace line 21:

```python
_STOCKBOT_TABLES = ("buffer_entries", "trade_log", "portfolio_snapshots")
```

with:

```python
from lifecycle._tables import STOCKBOT_TABLES as _STOCKBOT_TABLES
```

Edit `src/lifecycle/hard_reset.py`. Replace line 17:

```python
_STOCKBOT_TABLES = ("buffer_entries", "trade_log", "portfolio_snapshots")
```

with:

```python
from lifecycle._tables import STOCKBOT_TABLES as _STOCKBOT_TABLES
```

(Keeping the `_STOCKBOT_TABLES` alias preserves every call site in both files — no further edits needed.)

- [ ] **Step 5: Run the test to verify it now passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_init_db_script.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the lifecycle test suite to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "lifecycle or hard_reset or initialise" -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/lifecycle/_tables.py src/lifecycle/initialise.py src/lifecycle/hard_reset.py tests/unit/test_init_db_script.py
git commit -m "fix(lifecycle): derive STOCKBOT_TABLES from Base.metadata (A-011)"
```

---

## Task 7 — Lint test: forbid post-`create_session` `temp:` mutation

**Files:**
- Create: `tests/unit/test_no_post_create_session_temp_mutation.py`

This is the structural canary for the failure mode A-010 was created to fix. It scans `src/` and `scripts/` for any module that calls `create_session` and then assigns to a `temp:`-prefixed state key after the call. The only sanctioned install path is the plugin.

- [ ] **Step 1: Write the lint test**

```python
# tests/unit/test_no_post_create_session_temp_mutation.py
"""Lint: no module may mutate ``state["temp:_…"]`` after calling
``create_session`` — A-010 / A-047 regression guard.

ADK strips ``temp:``-prefixed keys at persistence time, and the runner
re-fetches the session for every invocation.  Any post-``create_session``
mutation onto a ``temp:`` key is therefore silently discarded.  The only
sanctioned install path is :class:`HandleInjectorPlugin`'s
``before_run_callback``.

The lint walks every ``.py`` file under ``src/`` and ``scripts/`` and:
1. Skips files that do not call ``create_session``.
2. In files that do, searches for ``Subscript`` assignments whose key
   is a string literal starting with ``"temp:"``.  Any such assignment
   that appears in source order *after* the first ``create_session``
   call is a lint failure.

This catches the trace_tick.py-style bug (Plan 01 deleted that file,
but the lint must keep landing).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Project roots — both source and scripts. tests/ is excluded; fixtures
# may legitimately mutate temp: state to set up an arrange step.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts")


def _calls_create_session(tree: ast.AST) -> list[int]:
    """Return line numbers of every ``create_session`` call in ``tree``."""

    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name: str | None = None
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            if name == "create_session":
                lines.append(node.lineno)
    return sorted(lines)


def _temp_key_assignments(tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``(lineno, key)`` for every ``something["temp:…"] = …`` assignment."""

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # Catches both ``state["temp:_x"] = y`` (Assign) and augmented
        # forms; we keep it simple and only check plain Assign.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                    key = target.slice.value
                    if isinstance(key, str) and key.startswith("temp:"):
                        out.append((node.lineno, key))
    return out


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


@pytest.mark.parametrize("path", _iter_py_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT)))
def test_no_temp_assignment_after_create_session(path: Path) -> None:
    """No `temp:`-prefixed assignment may follow a ``create_session`` call
    in the same module.  The sanctioned install path is
    ``HandleInjectorPlugin.before_run_callback``."""

    # HandleInjectorPlugin is the *one* module allowed to assign to
    # ``state["temp:_…"]`` — and it does so inside before_run_callback,
    # never after a create_session call (it doesn't call create_session
    # at all).  Exclude it explicitly so the lint doesn't flag itself.
    if path.name == "handle_injector_plugin.py":
        return

    src = path.read_text()
    tree = ast.parse(src, filename=str(path))

    cs_lines = _calls_create_session(tree)
    if not cs_lines:
        return  # No create_session in this file → nothing to guard.

    first_cs_line = cs_lines[0]
    offenders = [
        (ln, key) for (ln, key) in _temp_key_assignments(tree) if ln > first_cs_line
    ]

    assert not offenders, (
        f"{path.relative_to(PROJECT_ROOT)} mutates temp:-prefixed state "
        f"after create_session (line {first_cs_line}); ADK silently "
        f"discards these. Use HandleInjectorPlugin instead. "
        f"Offending assignments: {offenders}"
    )
```

- [ ] **Step 2: Run the lint**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_no_post_create_session_temp_mutation.py -v`
Expected: all parametrised cases pass. If a case fails, it's pointing at a real regression — fix the offending module rather than weakening the lint.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_no_post_create_session_temp_mutation.py
git commit -m "test(lifecycle): lint that forbids post-create_session temp: mutation (A-010 regression guard)"
```

---

## Task 8 — Full-suite smoke + ruff

- [ ] **Step 1: Run ruff on touched files**

Run: `.venv/bin/python -m ruff check src/orchestrator/tick.py src/orchestrator/lifecycle_runner.py src/backtest/driver.py src/lifecycle/initialise.py src/lifecycle/hard_reset.py src/lifecycle/_tables.py`
Expected: no findings.

- [ ] **Step 2: Run the full test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x`
Expected: all pass. If any test fails because it patches `Runner` directly or seeds a `datetime` `as_of` into state and reads it back as a `datetime`, fix the test inline (the fix is always: patch `build_runner`, or read `as_of` through `resolve_as_of`).

- [ ] **Step 3: Update `docs/audits/2026-05-26-codebase-audit/plans/STATUS.md` (if present)**

If a STATUS.md exists in the plans directory, append a line marking plan 04 complete. If not, skip.

- [ ] **Step 4: Commit (only if any test fixups were required)**

```bash
git add tests/
git commit -m "test: fix patch targets and as_of shapes for lifecycle parity"
```

---

## Test strategy

Three layers of coverage, each with a clear failure signature:

1. **Helper-level unit tests** (`tests/unit/orchestrator/test_lifecycle_runner.py`, `test_handle_injector_install.py`) — fast, pure-function checks that `iso_coerce_state`, `build_seed_state`, and `build_runner` do what the contracts say. Failure here means a logic bug in the helper itself.

2. **Cross-lifecycle parity integration test** (`tests/integration/test_lifecycle_parity.py`) — runs the same boundary through both lifecycles and asserts identical shape. Failure here means the two lifecycles have drifted again — fix the drifting lifecycle before anything else, do not relax the assertion.

3. **AST lint** (`tests/unit/test_no_post_create_session_temp_mutation.py`) — catches the failure mode that motivated `HandleInjectorPlugin` in the first place. Any future PR that tries to revive the bare-key install pattern fails this lint at CI time.

The cementing tests (`test_tick_as_of_phase.py`, `test_init_db_script.py`) are rewritten in the same patches as their underlying bug fixes (per the test-policy note in A-020).

## Risks & silent-regression checklist

- **Test mocks that target `Runner` directly.** Before this plan, tests mock `Runner` at `orchestrator.tick.Runner` or `backtest.driver.Runner`. After, those modules import `Runner` only transitively through `lifecycle_runner.build_runner`. Tests that mock the wrong target will silently let the real `Runner` run (and likely hit ADK construction errors). **Action:** when running the full suite in Task 8, grep failing test output for `patch("orchestrator.tick.Runner")` and similar — rewrite as `patch("orchestrator.lifecycle_runner.Runner")` or, preferably, `patch("orchestrator.tick.build_runner")`.

- **Fixtures that seed `state={"as_of": datetime(…)}` directly and then read it back.** Listed by the earlier grep:
  - `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py:159` (`as_of_start`)
  - `tests/analysts/fundamental/test_fetch_agent.py:92, 183, 238`
  - `tests/analysts/news/test_fetch_agent.py:40, 85, 118`

  These all go through the analyst fetch agents, which already read `state.get("as_of")` via `resolve_as_of` — which round-trips both shapes. They should keep working. **Flag, don't fix unless they break.** If they do break, the fix is either to ISO-stringify in the fixture or to confirm the consumer calls `resolve_as_of`.

- **`HandleInjectorPlugin._trace_writer is None` is OK, but silent failure to install the plugin is not.** Be alert: if `build_runner` is bypassed by a future code path that constructs `Runner` directly, the plugin is gone and every `state.get("temp:_trace")` returns `None`. The parity test covers existing call sites; the lint covers the post-`create_session` regression; a third defence (forbidding `from google.adk import Runner` outside `lifecycle_runner.py`) is **out of scope** for this plan but worth a Plan-10 follow-up entry.

- **Postgres `hard_reset` blast radius increased.** With `_STOCKBOT_TABLES` now five names instead of three, `hard_reset` will `DELETE FROM` two more tables (`ticker_stances`, `analyst_evidence`, `ticker_evidence`) on Postgres. This is exactly the intended behaviour (it was the bug), but if any operator is relying on cross-table-survival semantics for those tables, surface this in the commit body.

- **Per CLAUDE.md memory note: never mutate `adk_session.state["temp:_*"]` after `create_session`.** The lint in Task 7 enforces this; do not weaken it.

- **Raise loudly on missing `as_of`.** `resolve_as_of` already raises `AsOfRequiredError` when strict mode is on and `as_of` is missing. This plan does not touch that path — but if any of the test rewrites above tempts you to swap a `raise` for a wall-clock fallback inside a consumer, **stop**: that violates the cross-cutting "prefer raising over silent degradation" rule.

- **British English everywhere.** New module docstrings use British spelling (`behaviour`, `serialise`, `synchronise`).

## Definition of done

All items must be true:

- [ ] `src/orchestrator/lifecycle_runner.py` exists with `iso_coerce_state`, `build_seed_state`, and `build_runner` — covered by `tests/unit/orchestrator/test_lifecycle_runner.py` and `test_handle_injector_install.py`.
- [ ] `src/orchestrator/tick.py:148` writes `as_of` as an ISO string; `run_once` constructs its `Runner` via `build_runner` and seeds the session via `build_seed_state`.
- [ ] `src/backtest/driver.py` builds its `Runner` via `build_runner` and seeds via `build_seed_state` — the ad-hoc dict comprehension at the old line 545-550 is gone.
- [ ] `src/lifecycle/_tables.py` exists; `initialise.py` and `hard_reset.py` import `STOCKBOT_TABLES` from it; no hand-maintained tuple of table names remains in either file.
- [ ] `tests/integration/test_lifecycle_parity.py` passes — proves both lifecycles emit ISO `as_of` and install the plugin.
- [ ] `tests/unit/test_no_post_create_session_temp_mutation.py` passes for every `.py` file under `src/` and `scripts/`.
- [ ] `tests/unit/orchestrator/test_tick_as_of_phase.py` asserts the ISO-string shape (not raw `datetime`).
- [ ] `tests/unit/test_init_db_script.py` derives its expected set from `Base.metadata`, not a hand-listed literal.
- [ ] `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x` is green.
- [ ] `.venv/bin/python -m ruff check src/` reports no new findings on the touched files.
- [ ] Findings A-009, A-010, A-011, A-047 reconciled and marked addressed in the audit FINDINGS index (if a tracker file exists; if not, the commit messages reference each ID by name).

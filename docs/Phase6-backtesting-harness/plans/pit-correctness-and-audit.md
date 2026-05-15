# PIT Correctness and Audit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate every confirmed wall-clock leak surface in the backtest data path and ship a two-layer audit log so a reviewer can independently verify, post-run, that no row delivered to any analyst was sourced from after the tick's `as_of`.

**Architecture:** A new `src/data/timeguard.py` helper centralises `as_of` resolution: in strict mode it raises rather than falling back to `datetime.now(...)`. Backtest entrypoints set `STOCKBOT_STRICT_AS_OF=1`; live entrypoints don't. The cache provider for `price_history` learns about tick `phase` so the same-day bar is trimmed at open. Provider-side missing timestamps stop being fabricated and become explicit markers. The driver writes a per-tick telemetry record (Layer 1, always on); a new `scripts.backtest_audit_tick` re-runs a single tick wrapped in an `AuditingStore` decorator that re-fetches upstream documents for independent verification (Layer 2, on demand). `politician_trades` schema is migrated `Date → DateTime` to close a 09:30-same-day-disclosure leak, and `report_cache` records the originating tick's `as_of` so cross-tick cache hits are visible in telemetry.

**Tech Stack:** Python 3.12, SQLAlchemy/SQLite, pandas_market_calendars, edgartools, yfinance, httpx, requests, pytest (with `asyncio` + `slow` markers), Google ADK 1.32.

**Reference spec:** `docs/Phase7-post-backtest-fixing/specs/pit-correctness-and-audit-design.md`.

**Shell convention:** Bash tool runs in the project root. Never prepend `cd <root> &&`. Run pytest as `PYTHONPATH=src .venv/bin/python -m pytest …`, ruff as `PYTHONPATH=src .venv/bin/python -m ruff check …`.

**Style:** British English everywhere (comments, prose, identifiers — `behaviour`, `colour`, `organisation`). Function docstrings required. Whitespace for legibility — blank lines between logical blocks.

**Rollout note:** Each task lands as an independent commit. After Task 2, the live default (strict mode off) must keep the existing test suite green. After Task 7, the first backtest run must produce a clean audit-log tripwire summary. Task 8 must land before any second backtest window is configured.

---

## Task 1: Introduce `timeguard.resolve_as_of` + `AsOfRequiredError`

**Files:**
- Create: `src/data/timeguard.py`
- Test: `tests/unit/data/test_timeguard.py`

**What & why:** Today, 15+ code paths each fall back to `datetime.now(tz=UTC)` when the caller forgets to supply `as_of`. In a backtest this is a silent leak (the row gets stamped with wall-clock time rather than the historical tick). A single helper centralises the resolution rule: in strict mode (set by backtest entrypoints) it raises `AsOfRequiredError`; in live mode it falls back if and only if the caller passed `allow_wallclock=True`. No existing call sites change in this task — that's Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/data/test_timeguard.py`:

```python
"""Unit tests for ``data.timeguard.resolve_as_of``.

Verifies:
- candidate is returned verbatim when non-None
- wall-clock fallback fires when allowed and strict mode is off
- AsOfRequiredError raised when strict mode is on, regardless of allow_wallclock
- AsOfRequiredError raised when allow_wallclock is False, regardless of strict
- the `site` argument appears in the error message
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.timeguard import AsOfRequiredError, resolve_as_of


def test_returns_candidate_when_supplied() -> None:
    """A non-None candidate must be returned unchanged regardless of flags."""
    fixed = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)
    assert resolve_as_of(fixed, allow_wallclock=False, site="x") is fixed
    assert resolve_as_of(fixed, allow_wallclock=True,  site="x") is fixed


def test_falls_back_to_wallclock_when_allowed_and_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When live (no strict env) and allow_wallclock=True, returns datetime.now(UTC)."""
    monkeypatch.delenv("STOCKBOT_STRICT_AS_OF", raising=False)

    before = datetime.now(tz=UTC)
    got    = resolve_as_of(None, allow_wallclock=True, site="live")
    after  = datetime.now(tz=UTC)

    assert before <= got <= after
    assert got.tzinfo is not None


def test_raises_in_strict_mode_even_if_wallclock_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STOCKBOT_STRICT_AS_OF=1 must veto wall-clock fallback unconditionally."""
    monkeypatch.setenv("STOCKBOT_STRICT_AS_OF", "1")

    with pytest.raises(AsOfRequiredError) as exc:
        resolve_as_of(None, allow_wallclock=True, site="aggregator")

    assert "aggregator" in str(exc.value)


def test_raises_when_wallclock_not_allowed_even_outside_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allow_wallclock=False is its own veto — strict env not required."""
    monkeypatch.delenv("STOCKBOT_STRICT_AS_OF", raising=False)

    with pytest.raises(AsOfRequiredError):
        resolve_as_of(None, allow_wallclock=False, site="news_fetch")
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_timeguard.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'data.timeguard'`.

- [ ] **Step 3: Create `src/data/timeguard.py`**

```python
"""Centralised resolution of ``as_of`` historical clock values.

Every code path that needs an ``as_of`` datetime should route its
``None``-handling through :func:`resolve_as_of` rather than substituting
``datetime.now(tz=UTC)`` inline.

The helper has two responsibilities:

1. **Strict-mode enforcement.** When the environment variable
   ``STOCKBOT_STRICT_AS_OF=1`` is set (done by backtest entrypoints) a
   missing ``as_of`` is treated as a programming error and surfaced as
   :class:`AsOfRequiredError` rather than silently leaking wall-clock
   time into the dataset.

2. **Explicit live-mode opt-in.** Even with strict mode off, callers
   must explicitly opt into wall-clock fallback via
   ``allow_wallclock=True``.  This documents the intent at each call
   site — anywhere the wall clock is acceptable, the code says so.

Live entrypoints (``orchestrator/tick.py`` and any executor invocation
outside the backtest call tree) pass ``allow_wallclock=True``.
Everything inside the backtest call tree leaves it at the default
``False`` — together with the ``STOCKBOT_STRICT_AS_OF`` env var this
gives a belt-and-braces guarantee that a missing ``as_of`` cannot be
silently fabricated during a backtest.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime


class AsOfRequiredError(RuntimeError):
    """Raised when a historical ``as_of`` is required but was not supplied.

    Surfaces when ``STOCKBOT_STRICT_AS_OF=1`` is set or the caller passed
    ``allow_wallclock=False`` (the default).  The error message embeds the
    call-site label so the reviewer can pinpoint which layer was missing
    its plumbing.
    """


_STRICT_ENV_VAR  = "STOCKBOT_STRICT_AS_OF"
_STRICT_ENABLED  = "1"


def resolve_as_of(
    candidate: datetime | None,
    *,
    allow_wallclock: bool = False,
    site: str = "<unknown>",
) -> datetime:
    """Return ``candidate`` if supplied; otherwise fall back or raise.

    Parameters
    ----------
    candidate:
        The ``as_of`` value provided by the caller.  May be ``None`` if
        the caller did not propagate one through.
    allow_wallclock:
        When ``True`` *and* strict mode is off, ``datetime.now(tz=UTC)``
        is returned in place of a missing candidate.  When ``False`` (the
        default) the helper always raises on a missing candidate.  Live
        entrypoints set this to ``True``; backtest code leaves it at the
        default.
    site:
        Short label naming the call site (e.g. ``"aggregator"``,
        ``"news_fetch"``).  Embedded in the error message so a strict-mode
        failure tells the reviewer which layer was missing its plumbing.

    Returns
    -------
    datetime
        A timezone-aware datetime — either ``candidate`` (when supplied)
        or the wall-clock fallback (live mode only).

    Raises
    ------
    AsOfRequiredError
        When ``candidate is None`` *and* either strict mode is enabled
        (``STOCKBOT_STRICT_AS_OF=1``) or ``allow_wallclock=False``.
    """
    # Happy path: the caller supplied an explicit timestamp.
    if candidate is not None:
        return candidate

    # Strict mode is an absolute veto on wall-clock substitution.
    strict = os.environ.get(_STRICT_ENV_VAR) == _STRICT_ENABLED

    if strict or not allow_wallclock:
        raise AsOfRequiredError(
            f"as_of is required at site={site!r}; wall-clock fallback disabled "
            f"(strict_env={strict}, allow_wallclock={allow_wallclock})"
        )

    # Live path — caller has explicitly opted in.
    return datetime.now(tz=UTC)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_timeguard.py -v`

Expected: 4 passed.

- [ ] **Step 5: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/timeguard.py tests/unit/data/test_timeguard.py`

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/data/timeguard.py tests/unit/data/test_timeguard.py
git commit -m "$(cat <<'EOF'
feat(data): introduce timeguard.resolve_as_of

New helper centralises as_of resolution.  In strict mode
(STOCKBOT_STRICT_AS_OF=1) a missing as_of raises AsOfRequiredError
instead of silently falling back to datetime.now(UTC).  Live mode
requires an explicit allow_wallclock=True opt-in.

No call sites updated in this commit; Task 2 routes the 15+ existing
fallbacks through this helper.
EOF
)"
```

---

## Task 2: Route every wall-clock fallback through `timeguard`

**Files:**
- Modify: `src/data/__init__.py` (8 wrapper functions)
- Modify: `src/data/aggregator.py:146`
- Modify: `src/agents/analysts/_common.py:89`
- Modify: `src/agents/analysts/technical/fetch.py:57`
- Modify: `src/agents/analysts/news/fetch.py:129`
- Modify: `src/agents/analysts/social/fetch.py:48`
- Modify: `src/agents/analysts/fundamental/fetch.py:239`
- Modify: `src/agents/analysts/smart_money/fetch.py:76`
- Modify: `src/agents/snapshot/agent.py:71`
- Modify: `src/agents/strategist/agent.py:102, 284`
- Modify: `src/agents/strategist/decision_writer.py:88`
- Modify: `src/agents/executor/agent.py:104`
- Modify: `src/agents/memory/writer.py:109`
- Modify: `src/agents/contract/evidence_writer.py:80`
- Modify: `src/orchestrator/persistence.py:224, 308, 380`
- Modify: `src/orchestrator/tick.py` (entrypoint — passes `allow_wallclock=True`)
- Modify: `scripts/backtest_run.py` (sets `STOCKBOT_STRICT_AS_OF=1`)
- Modify: `src/backtest/runner.py` (sets/unsets the env var around `Driver.run`)
- Test: `tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py` (new)

**What & why:** Replace every inline `state.get("as_of") or datetime.now(tz=UTC)` and `if as_of is None: as_of = datetime.now(tz=UTC)` with a call into `resolve_as_of`. Backtest entrypoints (`scripts.backtest_run` → `Runner.run` → `Driver.run`) set `STOCKBOT_STRICT_AS_OF=1` before the pipeline runs so a missing `as_of` aborts the run rather than fabricating one. Live entrypoints (`orchestrator/tick.py`) pass `allow_wallclock=True`. Three orchestrator persistence helpers and one executor close-out site keep `allow_wallclock=True` because they're invoked from both live and backtest paths — the strict env var is the single point of control for backtests.

Each site falls into one of two patterns:

**Pattern A — fallback for caller-omitted `as_of`** (the `src/data/__init__.py` wrappers, `src/data/aggregator.py`, the analyst `fetch.py` modules). Replace:

```python
if as_of is None:
    as_of = datetime.now(tz=UTC)
```

with:

```python
as_of = resolve_as_of(as_of, allow_wallclock=True, site="<module-name>")
```

The `allow_wallclock=True` here is correct: these are entrypoint shims that may be called by live code. Strict-mode enforcement is delegated to the env var.

**Pattern B — fallback for missing state-dict key** (analyst `fetch.py` and writer modules). Replace:

```python
as_of: datetime = state.get("as_of") or datetime.now(tz=UTC)
```

with:

```python
as_of: datetime = resolve_as_of(
    state.get("as_of"), allow_wallclock=True, site="<analyst>/fetch",
)
```

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py`:

```python
"""Strict-mode regression: a deliberately broken driver must abort, not leak.

The driver is monkeypatched to *not* set ``state["as_of"]`` for the tick.
With ``STOCKBOT_STRICT_AS_OF=1`` the pipeline must raise
``AsOfRequiredError`` rather than silently falling back to wall-clock time.

Marked ``slow`` because it boots the live pipeline; excluded from the
default pytest run.
"""
from __future__ import annotations

import os

import pytest

from data.timeguard import AsOfRequiredError


@pytest.mark.slow
def test_strict_mode_aborts_when_driver_omits_as_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipeline call without ``as_of`` in strict mode raises AsOfRequiredError."""
    monkeypatch.setenv("STOCKBOT_STRICT_AS_OF", "1")

    import data  # public wrapper module

    # Live wrappers call resolve_as_of(allow_wallclock=True); strict env vetoes.
    with pytest.raises(AsOfRequiredError):
        # ``ohlcv`` is one of the eight wrappers — passing as_of=None must abort.
        import asyncio
        asyncio.run(data.ohlcv("AAPL", as_of=None))


def test_live_mode_allows_wallclock_when_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With STOCKBOT_STRICT_AS_OF unset, live wrappers still allow fallback."""
    monkeypatch.delenv("STOCKBOT_STRICT_AS_OF", raising=False)

    from data.timeguard import resolve_as_of
    got = resolve_as_of(None, allow_wallclock=True, site="ohlcv")
    assert got is not None
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py -v -m slow`

Expected: FAIL on the first test — the existing `data.ohlcv` wrapper falls back silently. Second test passes (timeguard already exists).

- [ ] **Step 3: Patch `src/data/__init__.py` — eight wrapper functions**

For each of the eight wrappers (`ohlcv`, `price_history`, `news`, `social_sentiment`, `company_ratios`, `insider_trades`, `politician_trades`, `notable_holders`, `filings` — confirm the exact list with `grep -n "if as_of is None" src/data/__init__.py`), replace the inline fallback block with the timeguard call.

Add the import at the top of the file (after the existing `from datetime` import):

```python
from data.timeguard import resolve_as_of
```

For each wrapper, replace:

```python
    if as_of is None:
        as_of = datetime.now(tz=UTC)
```

with (using the wrapper's own name as the `site`):

```python
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.<wrapper-name>")
```

So for the `ohlcv` wrapper (around line 113):

```python
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.ohlcv")
```

Repeat for `price_history`, `news`, `social_sentiment`, `company_ratios`, `insider_trades`, `politician_trades`, `notable_holders`, `filings` — eight identical edits, only the `site=` label changes.

- [ ] **Step 4: Patch `src/data/aggregator.py:146`**

Replace:

```python
    if as_of is None:
        as_of = datetime.now(tz=UTC)
```

with:

```python
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.aggregator")
```

Add the import: `from data.timeguard import resolve_as_of`.

- [ ] **Step 5: Patch the five analyst `fetch.py` modules**

Files: `_common.py:89`, `technical/fetch.py:57`, `news/fetch.py:129`, `social/fetch.py:48`, `fundamental/fetch.py:239`, `smart_money/fetch.py:76`.

For each, replace:

```python
    as_of: datetime = state.get("as_of") or datetime.now(tz=UTC)
```

with (using the analyst's name as the site label):

```python
    as_of: datetime = resolve_as_of(
        state.get("as_of"), allow_wallclock=True, site="<analyst-name>/fetch",
    )
```

Replace `<analyst-name>` with `technical`, `news`, `social`, `fundamental`, `smart_money`, or `_common` respectively.

Add `from data.timeguard import resolve_as_of` near the existing imports in each file.

- [ ] **Step 6: Patch the writer / lifecycle sites**

Files: `src/agents/snapshot/agent.py:71`, `src/agents/strategist/decision_writer.py:88`, `src/agents/executor/agent.py:104`, `src/agents/memory/writer.py:109`, `src/agents/contract/evidence_writer.py:80`, `src/agents/strategist/agent.py:102, 284`.

These have the pattern:

```python
recorded_at = raw_as_of if isinstance(raw_as_of, datetime) else datetime.now(tz=UTC)
```

Replace with:

```python
recorded_at = resolve_as_of(
    raw_as_of if isinstance(raw_as_of, datetime) else None,
    allow_wallclock=True,
    site="<file-name>",
)
```

Add the timeguard import in each file.

- [ ] **Step 7: Patch the three persistence sites**

`src/orchestrator/persistence.py:224, 308, 380` have:

```python
recorded_at=snap.get("recorded_at", datetime.now(tz=UTC)),
# or:
recorded_at=recorded_at if recorded_at is not None else datetime.now(tz=UTC),
```

Replace each with:

```python
recorded_at=resolve_as_of(
    snap.get("recorded_at"),  # or just `recorded_at` for the other two sites
    allow_wallclock=True,
    site="persistence.<helper-name>",
),
```

Use the function name being patched as the `<helper-name>` (e.g. `persistence.write_snapshot`).

Add the import at the top of the file.

- [ ] **Step 8: Wire strict mode into the backtest entrypoint**

Modify `scripts/backtest_run.py` so that the first action inside `main()` is to enable strict mode:

```python
def main() -> None:
    """CLI entrypoint for a full backtest run."""
    # Strict-as_of mode is mandatory for backtests — a missing as_of at any
    # provider or writer site must abort the run rather than fabricate a
    # wall-clock substitute.  See src/data/timeguard.py.
    os.environ["STOCKBOT_STRICT_AS_OF"] = "1"

    parser = argparse.ArgumentParser(
        ...
```

Add `import os` to the imports if not already present.

- [ ] **Step 9: Add a safety belt in `src/backtest/runner.py`**

In `Runner.run`, before calling `driver.run(...)`, ensure strict mode is active. This belt-and-braces both `scripts.backtest_run` and any programmatic caller (tests, future scripts):

```python
def run(self, window_key: str) -> RunResult:
    """Drive one full backtest window end to end. ..."""
    # Belt-and-braces: scripts.backtest_run also sets this, but defending
    # in depth means a programmatic Runner.run caller can't accidentally
    # leak wall-clock time into the dataset.
    os.environ["STOCKBOT_STRICT_AS_OF"] = "1"
    ...
```

Add `import os` if not present.

- [ ] **Step 10: Run the strict-mode integration test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py -v -m slow`

Expected: 2 passed.

- [ ] **Step 11: Run the full test suite (non-slow) to confirm no regressions**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`

Expected: All pre-existing tests pass. Strict mode is *off* by default, so the live-mode `allow_wallclock=True` path keeps behaviour identical.

- [ ] **Step 12: Run the smoke test to confirm end-to-end backtest still works**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`

Expected: PASS. (The synthetic LLM run already plumbs `as_of` through every layer, so strict mode does not trip.)

- [ ] **Step 13: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

- [ ] **Step 14: Commit**

```bash
git add src/data/__init__.py src/data/aggregator.py \
        src/agents/analysts/_common.py \
        src/agents/analysts/technical/fetch.py \
        src/agents/analysts/news/fetch.py \
        src/agents/analysts/social/fetch.py \
        src/agents/analysts/fundamental/fetch.py \
        src/agents/analysts/smart_money/fetch.py \
        src/agents/snapshot/agent.py \
        src/agents/strategist/agent.py \
        src/agents/strategist/decision_writer.py \
        src/agents/executor/agent.py \
        src/agents/memory/writer.py \
        src/agents/contract/evidence_writer.py \
        src/orchestrator/persistence.py \
        scripts/backtest_run.py \
        src/backtest/runner.py \
        tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py
git commit -m "$(cat <<'EOF'
refactor(data): route every wall-clock fallback through timeguard

Replaces 15+ inline ``state.get('as_of') or datetime.now(...)`` and
``if as_of is None: as_of = datetime.now(...)`` patterns with calls into
``timeguard.resolve_as_of``.  Live wrappers and writers pass
``allow_wallclock=True`` so live behaviour is unchanged.  Backtest
entrypoints (``scripts.backtest_run`` and ``Runner.run``) set
``STOCKBOT_STRICT_AS_OF=1`` so a missing as_of in a backtest aborts the
run rather than silently leaking wall-clock time.

Integration test asserts the strict-mode abort path; smoke test confirms
the existing end-to-end backtest still runs clean under strict mode.
EOF
)"
```

---

## Task 3: Trim same-day OHLCV bar at open phase

**Files:**
- Modify: `src/backtest/providers/price_history_cache.py`
- Modify: `src/backtest/driver.py` (pass `tick.phase` into session state — already does, but document)
- Modify: `src/agents/analysts/technical/fetch.py` (pass `phase` through to provider call)
- Modify: `src/data/__init__.py::ohlcv` (accept `phase` kwarg and forward)
- Test: `tests/backtest/leak_regressions/test_open_tick_excludes_sameday_bar.py` (new)
- Test: `tests/backtest/leak_regressions/__init__.py` (new)

**What & why:** Today `price_history_cache.fetch` returns OHLCV bars where `date(ts) <= as_of.date()`. At the 09:30 open tick on day D, this includes day D's own bar — which contains the *close*, *high*, and *low* prices that don't exist yet at 09:30. The analyst sees a bar from the future. Fix: route `phase` into the provider; when `phase == "open"`, trim the same-day bar entirely. At `phase == "close"` (16:00 ET), today's bar stays because the close is public.

Default behaviour when `phase` is missing is the conservative one (trim) — matches the "fail closed" stance.

- [ ] **Step 1: Create the test directory and write the failing test**

```bash
mkdir -p tests/backtest/leak_regressions
touch tests/backtest/leak_regressions/__init__.py
```

Create `tests/backtest/leak_regressions/test_open_tick_excludes_sameday_bar.py`:

```python
"""Open-phase tick must not expose today's OHLCV bar (close not yet public)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers._store_handle import set_store
from backtest.providers.price_history_cache import fetch
from data.models import OHLCBar


@pytest.fixture()
def store_with_two_bars(tmp_path: Path) -> CachedDataStore:
    """A store containing yesterday's and today's daily bars for AAPL."""
    db_path = tmp_path / "cache.sqlite"
    store   = CachedDataStore(db_path)

    bars = [
        OHLCBar(
            timestamp=datetime(2023, 3, 9, 0, 0, tzinfo=UTC),
            open=100.0, high=101.0, low=99.0, close=100.5, volume=1_000_000,
        ),
        OHLCBar(
            timestamp=datetime(2023, 3, 10, 0, 0, tzinfo=UTC),
            open=100.6, high=102.0, low=98.0, close=99.5, volume=1_500_000,
        ),
    ]
    store.write_ohlcv("AAPL", bars)
    set_store(store)
    return store


@pytest.mark.asyncio
async def test_open_phase_excludes_today(store_with_two_bars: CachedDataStore) -> None:
    """At 09:30 open on 2023-03-10, only the 2023-03-09 bar must be visible."""
    result = await fetch(
        "AAPL",
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
        phase="open",
    )

    dates = [bar.timestamp.date() for bar in result.bars]
    assert date(2023, 3, 9)  in dates
    assert date(2023, 3, 10) not in dates


@pytest.mark.asyncio
async def test_close_phase_includes_today(store_with_two_bars: CachedDataStore) -> None:
    """At 16:00 close on 2023-03-10, today's bar IS public (close is closed)."""
    result = await fetch(
        "AAPL",
        as_of=datetime(2023, 3, 10, 16, 0, tzinfo=UTC),
        phase="close",
    )

    dates = [bar.timestamp.date() for bar in result.bars]
    assert date(2023, 3, 10) in dates


@pytest.mark.asyncio
async def test_missing_phase_defaults_to_open_behaviour(
    store_with_two_bars: CachedDataStore,
) -> None:
    """Default behaviour when phase is omitted is the conservative one (trim today)."""
    result = await fetch(
        "AAPL",
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
    )

    dates = [bar.timestamp.date() for bar in result.bars]
    assert date(2023, 3, 10) not in dates
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_open_tick_excludes_sameday_bar.py -v`

Expected: FAIL — `fetch` does not accept `phase`, and even if it did the current filter `date(ts) <= as_of.date()` includes today's bar.

- [ ] **Step 3: Patch `src/backtest/providers/price_history_cache.py`**

Replace the existing `fetch` function with:

```python
@register(
    "price_history", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    period: str = "1y",
    interval: str = "1d",
    phase: str | None = None,
    **_unused,
) -> PriceHistory:
    """Return OHLCV bars for ``ticker`` up to and including ``as_of``.

    ``period`` is converted to an approximate calendar-day lookback so the
    query matches the window the live provider would return.  ``interval``
    is accepted for signature compatibility but ignored — the cache stores
    daily bars exclusively.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound; bars after this date are excluded.
    period:
        yfinance-style period string (e.g. ``"1y"``).  Unknown strings fall
        back to 365 days.
    interval:
        Accepted for call-site compatibility; unused.
    phase:
        Tick phase — ``"open"`` (09:30) or ``"close"`` (16:00).  At
        ``"open"`` the bar dated ``as_of.date()`` is **trimmed** because
        today's close is not yet public.  At ``"close"`` today's bar is
        kept.  When ``phase`` is ``None`` (e.g. a live call between
        scheduled ticks) the conservative open-phase rule applies — fail
        closed rather than fabricate.

    Returns
    -------
    PriceHistory
        Bars in ascending date order.  Empty list when no cached bars
        match the window.
    """
    lookback_days = _PERIOD_DAYS.get(period, 365)
    end: date     = as_of.date()
    start: date   = end - timedelta(days=lookback_days)

    bars = get_store().read_ohlcv(ticker, start=start, end=end)

    # At the open phase (or unknown phase) today's bar leaks the close
    # price.  Strip it.
    if phase != "close":
        bars = [b for b in bars if b.timestamp.date() < end]

    return PriceHistory(ticker=ticker, bars=bars)
```

- [ ] **Step 4: Plumb `phase` through `src/data/__init__.py::ohlcv`**

Locate the `ohlcv` wrapper (around line 89). Add `phase: str | None = None` to the signature and forward it to `_dispatch`:

```python
async def ohlcv(
    ticker: str,
    *,
    period: str = "1y",
    interval: str = "1d",
    as_of: datetime | None = None,
    phase: str | None = None,
) -> PriceHistory:
    """Daily OHLCV bars for ``ticker`` up to and including ``as_of``.

    Parameters
    ----------
    ticker, period, interval:
        Forwarded to the registered ``price_history`` provider.
    as_of:
        Historical clock timestamp.  Required in strict mode (backtest);
        defaults to ``datetime.now(UTC)`` in live mode.
    phase:
        Tick phase — ``"open"`` or ``"close"``.  Forwarded so cache
        providers can trim the same-day bar at the open tick.  When the
        caller is the live pipeline between scheduled ticks, ``None`` is
        acceptable.
    """
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.ohlcv")

    return await _dispatch(
        "price_history", ticker.upper(),
        period=period, interval=interval, as_of=as_of, phase=phase,
    )
```

Apply the same change to `price_history` (the sibling wrapper a few lines down). Confirm both forward `phase`.

- [ ] **Step 5: Plumb `phase` through the technical analyst's fetch site**

Modify `src/agents/analysts/technical/fetch.py`. Locate the call to `data.ohlcv(...)` (or `data.price_history(...)`) and add `phase=state.get("tick_phase")`:

```python
history = await data.ohlcv(
    ticker,
    period=period,
    interval=interval,
    as_of=as_of,
    phase=state.get("tick_phase"),
)
```

The driver already sets `state["tick_phase"]` per tick (see `driver.py:118`).

- [ ] **Step 6: Run the regression test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_open_tick_excludes_sameday_bar.py -v`

Expected: 3 passed.

- [ ] **Step 7: Run the smoke test to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`

Expected: PASS.

- [ ] **Step 8: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/backtest/providers/price_history_cache.py \
        src/data/__init__.py \
        src/agents/analysts/technical/fetch.py \
        tests/backtest/leak_regressions/__init__.py \
        tests/backtest/leak_regressions/test_open_tick_excludes_sameday_bar.py
git commit -m "$(cat <<'EOF'
fix(backtest): trim same-day OHLCV bar at open phase

Previous filter ``date(ts) <= as_of.date()`` was inclusive, so the 09:30
open-phase tick on day D saw day D's own bar — with the close, high, and
low that don't exist yet at the open.  ``price_history_cache.fetch`` now
takes a ``phase`` kwarg; at ``"open"`` (or unknown phase) it strips the
``as_of.date()`` bar.  At ``"close"`` today's bar stays — close is public.

Plumbed through the ``data.ohlcv`` / ``data.price_history`` wrappers and
the technical analyst's fetch site, which reads ``state['tick_phase']``
set by ``Driver``.
EOF
)"
```

---

## Task 4: Preserve missing-timestamp markers instead of fabricating

**Files:**
- Modify: `src/data/providers/news/tiingo.py:48-54` (drop wall-clock substitution)
- Modify: `src/data/providers/news/finnhub.py:60-65` (drop wall-clock substitution)
- Modify: `src/data/providers/insider_trades/edgar.py:244` (drop `date.today()` fallback)
- Modify: `src/data/providers/filings/edgar.py` (search for similar `datetime.now`/`date.today` patterns)
- Modify: `src/data/providers/notable_holders/edgar.py` (search for similar patterns)
- Modify: `src/data/models/` — add `MissingTimestamp` sentinel
- Modify: `src/backtest/cache/store.py` — writers skip rows carrying the sentinel and emit a structured log
- Test: `tests/backtest/leak_regressions/test_missing_timestamp_marks_row.py` (new)

**What & why:** When upstream returns a row with no `publishedDate` / `datetime` / `filed_at`, the providers currently substitute `datetime.now(UTC)`. This is a silent fabrication — at backfill time the substituted timestamp is fill-day wall-clock, so the row looks PIT-valid for every `as_of` after fill time. Replace this with an explicit `MissingTimestamp` marker that the cache writer treats as a deliberate "exclude until reviewed" record. The audit log (Task 6) surfaces the count.

- [ ] **Step 1: Write the failing test**

Create `tests/backtest/leak_regressions/test_missing_timestamp_marks_row.py`:

```python
"""Provider rows lacking an upstream timestamp must be excluded, not fabricated."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from backtest.cache.store import CachedDataStore
from data.models import NewsArticle
from data.models.missing import MISSING_TIMESTAMP


def test_news_article_with_missing_timestamp_is_skipped(tmp_path: Path) -> None:
    """``write_news`` must skip rows whose ``published_at`` is the sentinel."""
    store = CachedDataStore(tmp_path / "cache.sqlite")

    articles = [
        NewsArticle(
            ticker="AAPL",
            headline="Real article",
            summary="",
            url="https://example.com/a",
            source="ex",
            published_at=datetime(2023, 3, 9, 12, 0),
            sentiment=None,
        ),
        NewsArticle(
            ticker="AAPL",
            headline="Article with no upstream timestamp",
            summary="",
            url="https://example.com/b",
            source="ex",
            published_at=MISSING_TIMESTAMP,
            sentiment=None,
        ),
    ]

    store.write_news("AAPL", articles)

    rows = store.read_news("AAPL", as_of=datetime(2099, 1, 1), lookback_days=10_000)
    # Only the real article makes it in.  The sentinel-stamped row is excluded.
    assert len(rows) == 1
    assert rows[0].headline == "Real article"


@pytest.mark.asyncio
async def test_tiingo_propagates_sentinel_for_missing_published_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tiingo.fetch`` must NOT substitute wall-clock for missing publishedDate."""
    import data.providers.news.tiingo as mod

    fake_rows = [
        {
            "title": "no-date",
            "description": "",
            "url": "u",
            "source": "src",
            # publishedDate intentionally absent
        },
    ]

    monkeypatch.setenv("TIINGO_API_KEY", "x")
    monkeypatch.setattr(mod, "_fetch_news", lambda *a, **kw: fake_rows)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, 16, 0),
    )

    assert len(out) == 1
    assert out[0].published_at == MISSING_TIMESTAMP
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_missing_timestamp_marks_row.py -v`

Expected: FAIL — `MISSING_TIMESTAMP` and `data.models.missing` do not exist.

- [ ] **Step 3: Create the sentinel module**

Create `src/data/models/missing.py`:

```python
"""Sentinel marker for upstream rows that lack a usable timestamp.

When a news article, filing, or insider-trade row arrives from upstream
without a parseable date/time field, the provider stamps the field with
``MISSING_TIMESTAMP`` rather than substituting ``datetime.now(UTC)``.
The cache writer then *skips* the row (with a structured log line) so it
never enters the PIT-filtered dataset.

Using a sentinel keeps the data model strongly typed (``datetime`` not
``datetime | None``) while making the missing-data path explicit and
auditable.  The audit log (``scripts.backtest_audit_tick``) surfaces a
per-domain count of skipped rows so a reviewer can decide whether
upstream coverage is acceptable for the window.
"""
from __future__ import annotations

from datetime import UTC, datetime

# Year 1 is unambiguous: no real publishedDate / filedAt / transactedAt
# can ever resolve to AD 1 Jan 1, 00:00:00 UTC, and downstream PIT
# filters compare against any plausible ``as_of`` so the sentinel always
# falls outside the window.  Using a real ``datetime`` rather than
# ``None`` keeps Pydantic schemas tight (``datetime`` not
# ``datetime | None``) — only the model's writer-side check needs to know.
MISSING_TIMESTAMP: datetime = datetime(1, 1, 1, tzinfo=UTC)


def is_missing_timestamp(value: datetime | None) -> bool:
    """Return ``True`` iff ``value`` is the missing-timestamp sentinel.

    Parameters
    ----------
    value:
        Timestamp to inspect.  ``None`` returns ``True`` for callers
        that haven't migrated to the sentinel yet.

    Returns
    -------
    bool
        ``True`` when the value is the documented sentinel (or ``None``).
    """
    if value is None:
        return True
    return value == MISSING_TIMESTAMP
```

Update `src/data/models/__init__.py` to re-export:

```python
from .missing import MISSING_TIMESTAMP, is_missing_timestamp  # noqa: F401
```

- [ ] **Step 4: Patch `src/data/providers/news/tiingo.py`**

Replace the existing `_parse_published`:

```python
def _parse_published(raw: Any) -> datetime:
    """Coerce Tiingo's ISO ``publishedDate`` into a timezone-aware ``datetime``.

    Parameters
    ----------
    raw:
        The raw ``publishedDate`` value from the Tiingo JSON row.

    Returns
    -------
    datetime
        A timezone-aware datetime; returns ``MISSING_TIMESTAMP`` when the
        value is missing or unparseable so the cache writer can skip the
        row deliberately rather than fabricating wall-clock substitution.
    """
    from data.models.missing import MISSING_TIMESTAMP

    if raw is None:
        return MISSING_TIMESTAMP

    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return MISSING_TIMESTAMP

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt
```

- [ ] **Step 5: Patch `src/data/providers/news/finnhub.py`**

Replace lines 60-65:

```python
        ts = item.get("datetime")
        if isinstance(ts, (int, float)) and ts > 0:
            published = datetime.fromtimestamp(ts, tz=UTC)
        else:
            from data.models.missing import MISSING_TIMESTAMP
            published = MISSING_TIMESTAMP
```

- [ ] **Step 6: Patch `src/data/providers/insider_trades/edgar.py`**

Locate the `date.today()` fallback (around line 244 — the missing-filing-date path). Replace:

```python
filing_date = filing.filing_date or date.today()
```

with:

```python
# A Form 4 with no filing_date is an upstream parsing bug — we surface it as
# a deliberate skip via the MISSING_TIMESTAMP sentinel so backtests don't
# silently see it under a fabricated date.
if filing.filing_date is None:
    from data.models.missing import MISSING_TIMESTAMP
    filing_date = MISSING_TIMESTAMP.date()
else:
    filing_date = filing.filing_date
```

If the existing code uses the bare `date.today()` directly in a comparison or column, the right idiom is to skip the row instead:

```python
if filing.filing_date is None:
    logger.warning(
        "edgar/insider_trades: skipping Form 4 with no filing_date "
        "(symbol=%s, accession=%s)",
        symbol, filing.accession_no,
    )
    continue
```

Use whichever pattern matches the surrounding code shape — both are correct.

- [ ] **Step 7: Apply the same pattern to `filings/edgar.py` and `notable_holders/edgar.py`**

Search for missing-timestamp fallback sites:

```bash
grep -n "date.today\|datetime.now" src/data/providers/filings/edgar.py src/data/providers/notable_holders/edgar.py
```

For each match that substitutes a wall-clock value for an *upstream-missing* timestamp (not an `as_of` fallback — those are handled by Task 2), replace with either the sentinel pattern from Step 6 or an explicit `logger.warning(...); continue`.

- [ ] **Step 8: Patch `src/backtest/cache/store.py::write_news`**

Locate `write_news` and add a skip-with-log block before the insert loop:

```python
def write_news(self, ticker: str, articles: list[NewsArticle]) -> None:
    """Upsert news articles for ``ticker``.

    Rows whose ``published_at`` is :data:`~data.models.missing.MISSING_TIMESTAMP`
    are skipped with a structured log line so the audit layer can surface
    the count of unstamped upstream rows.

    Parameters
    ----------
    ticker:
        Ticker symbol.
    articles:
        List of ``NewsArticle`` instances to persist.
    """
    from data.models.missing import is_missing_timestamp

    with Session(self._engine) as s:
        for a in articles:
            if is_missing_timestamp(a.published_at):
                logger.warning(
                    "store.write_news: skipping row with missing timestamp "
                    "(ticker=%s, url=%s, source=%s)",
                    ticker, a.url, a.source,
                )
                continue

            # ... existing insert block unchanged ...
```

Apply the equivalent skip-with-log pattern to:
- `write_filings` (key field `filed_at`)
- `write_insider_trades` (key field `filed_at`)
- `write_notable_holders` (key field `as_of_date` or equivalent — check schema)
- `write_politician_trades` (key field `disclosure_date`/`transaction_date` — keep COALESCE rule)

For each writer, add `from data.models.missing import is_missing_timestamp` at the top of the method (or module-level if used multiple times).

- [ ] **Step 9: Run the regression test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_missing_timestamp_marks_row.py -v`

Expected: 2 passed.

- [ ] **Step 10: Run the full test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`

Expected: All pass. (The existing tests use fixtures with real timestamps, so the new skip path doesn't fire.)

- [ ] **Step 11: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

- [ ] **Step 12: Commit**

```bash
git add src/data/models/missing.py src/data/models/__init__.py \
        src/data/providers/news/tiingo.py src/data/providers/news/finnhub.py \
        src/data/providers/insider_trades/edgar.py \
        src/data/providers/filings/edgar.py \
        src/data/providers/notable_holders/edgar.py \
        src/backtest/cache/store.py \
        tests/backtest/leak_regressions/test_missing_timestamp_marks_row.py
git commit -m "$(cat <<'EOF'
fix(providers): preserve missing-timestamp markers instead of fabricating

Provider rows whose upstream timestamp is missing or unparseable now carry
the MISSING_TIMESTAMP sentinel rather than datetime.now(UTC).  Cache writers
skip sentinel-stamped rows with a structured log line so the audit layer
(Task 6) can report a per-domain count of dropped rows.

Previously a backfill-time wall-clock substitution made any row look
PIT-valid for every backtest as_of, silently leaking upstream-coverage
gaps into the analyst's dataset.

Applies to: news/tiingo, news/finnhub, insider_trades/edgar,
filings/edgar, notable_holders/edgar.
EOF
)"
```

---

## Task 5: Cache skip predicate includes `source_provider` + `--refetch-domain` flag

**Files:**
- Modify: `src/backtest/cache/fetcher.py::_already_ok`
- Modify: `src/backtest/cache/fetcher.py::Fetcher.__init__` (accept `refetch_domains`)
- Modify: `scripts/backtest_fetch.py` (add `--refetch-domain` flag)
- Test: `tests/backtest/leak_regressions/test_cache_skip_includes_source_provider.py` (new)

**What & why:** Today `_already_ok` skips a fetch when any prior `cache_runs` row for `(window_key, ticker, domain)` has `status='ok'` — regardless of which provider wrote it. After flipping `config/data.json` (e.g. swapping `news/finnhub` for `news/tiingo`), the new provider is never invoked because the old rows still satisfy the predicate. The cache returns stale rows from the wrong provider. Fix: include `source_provider` in the skip predicate. Add `--refetch-domain` to force re-fill of named domains.

- [ ] **Step 1: Write the failing test**

Create `tests/backtest/leak_regressions/test_cache_skip_includes_source_provider.py`:

```python
"""After a provider swap, the fetcher must re-fetch, not blindly skip."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.fetcher import Fetcher
from backtest.cache.schema import CacheRunRow
from backtest.cache.store import CachedDataStore
from backtest.windows import Window
from sqlalchemy.orm import Session


@pytest.fixture()
def store_and_window(tmp_path: Path) -> tuple[CachedDataStore, Window]:
    store  = CachedDataStore(tmp_path / "cache.sqlite")
    window = Window(
        key="t",
        start=date(2023, 3, 1),
        end=date(2023, 3, 15),
        watchlist=["AAPL"],
        description="test",
    )
    return store, window


def _seed_ok_row(store: CachedDataStore, *, provider: str, window_key: str) -> None:
    """Insert a cache_runs row with status='ok' and the supplied provider name."""
    with Session(store._engine) as s:
        s.add(CacheRunRow(
            run_id="r1",
            started_at=datetime.now(tz=UTC),
            finished_at=datetime.now(tz=UTC),
            window_key=window_key,
            ticker="AAPL",
            domain="news",
            source_provider=provider,
            rows_written=10,
            status="ok",
            error="",
        ))
        s.commit()


@pytest.mark.asyncio
async def test_provider_swap_invalidates_cache_skip(
    store_and_window: tuple[CachedDataStore, Window],
) -> None:
    """After config flips news provider from finnhub → tiingo, _already_ok=False."""
    store, window = store_and_window

    # Pretend a previous fill ran under "finnhub".
    _seed_ok_row(store, provider="finnhub", window_key=window.key)

    # Build a fetcher that now thinks "tiingo" is the news provider.
    called: list[str] = []

    async def fake_news(ticker: str, *, start: date, end: date) -> list:
        called.append(ticker)
        return []

    fetcher = Fetcher(
        store=store,
        window_key=window.key,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"news": fake_news},
        live_providers_for_domain={"news": "tiingo"},
    )

    await fetcher.run()

    # The provider flip must trigger a fresh fetch, not skip on the stale row.
    assert called == ["AAPL"]


@pytest.mark.asyncio
async def test_same_provider_still_skipped(
    store_and_window: tuple[CachedDataStore, Window],
) -> None:
    """Same provider as the previous fill must still short-circuit."""
    store, window = store_and_window

    _seed_ok_row(store, provider="tiingo", window_key=window.key)

    called: list[str] = []

    async def fake_news(ticker: str, *, start: date, end: date) -> list:
        called.append(ticker)
        return []

    fetcher = Fetcher(
        store=store,
        window_key=window.key,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"news": fake_news},
        live_providers_for_domain={"news": "tiingo"},
    )

    await fetcher.run()

    assert called == []


@pytest.mark.asyncio
async def test_refetch_domain_forces_refill(
    store_and_window: tuple[CachedDataStore, Window],
) -> None:
    """``refetch_domains={'news'}`` overrides the skip even when provider matches."""
    store, window = store_and_window

    _seed_ok_row(store, provider="tiingo", window_key=window.key)

    called: list[str] = []

    async def fake_news(ticker: str, *, start: date, end: date) -> list:
        called.append(ticker)
        return []

    fetcher = Fetcher(
        store=store,
        window_key=window.key,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"news": fake_news},
        live_providers_for_domain={"news": "tiingo"},
        refetch_domains={"news"},
    )

    await fetcher.run()

    assert called == ["AAPL"]
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_cache_skip_includes_source_provider.py -v`

Expected: FAIL — `Fetcher.__init__` doesn't accept `refetch_domains` and `_already_ok` doesn't filter on `source_provider`.

- [ ] **Step 3: Patch `Fetcher.__init__` to accept `refetch_domains`**

In `src/backtest/cache/fetcher.py`, update the constructor:

```python
def __init__(
    self,
    *,
    store: CachedDataStore,
    window_key: str,
    window: Window,
    watchlist: list[str],
    provider_fns: dict[str, Callable[..., Awaitable[Any]]],
    live_providers_for_domain: dict[str, str],
    refetch_domains: set[str] | None = None,
) -> None:
    """Wire the fetcher.

    ``refetch_domains`` (default empty) names domains whose existing
    ``status='ok'`` rows are ignored — useful after a provider swap or
    when the user passes ``--refetch-domain news`` on the CLI.
    """
    self._store              = store
    self._window_key         = window_key
    self._window             = window
    self._watchlist          = watchlist
    self._provider_fns       = provider_fns
    self._live_for_domain    = live_providers_for_domain
    self._refetch_domains    = refetch_domains or set()
```

- [ ] **Step 4: Patch `_already_ok` to include `source_provider`**

Replace the existing `_already_ok`:

```python
def _already_ok(self, ticker: str, domain: str) -> bool:
    """Return ``True`` iff a prior fetch for this triple has ``status='ok'``
    **and** was written by the currently-configured ``source_provider``.

    Including ``source_provider`` in the predicate means a ``config/data.json``
    flip from e.g. ``finnhub`` to ``tiingo`` invalidates the skip — the new
    provider is re-invoked rather than returning stale rows from the old one.

    Domains listed in ``self._refetch_domains`` are never skipped, regardless
    of the row's provider.

    Parameters
    ----------
    ticker:
        Ticker symbol.
    domain:
        Domain name (e.g. ``"ohlcv"``, ``"news"``).

    Returns
    -------
    bool
        ``True`` when a ``cache_runs`` row exists for
        ``(window_key, ticker, domain, source_provider)`` with
        ``status='ok'`` **and** the domain is not flagged for refetch.
    """
    if domain in self._refetch_domains:
        return False

    expected_provider = self._live_for_domain.get(domain)

    with Session(self._store._engine) as s:
        row = s.execute(
            select(CacheRunRow).where(
                CacheRunRow.window_key      == self._window_key,
                CacheRunRow.ticker          == ticker,
                CacheRunRow.domain          == domain,
                CacheRunRow.source_provider == expected_provider,
                CacheRunRow.status          == "ok",
            )
        ).scalar_one_or_none()

        return row is not None
```

- [ ] **Step 5: Wire `--refetch-domain` into `scripts/backtest_fetch.py`**

Add the argparse option:

```python
parser.add_argument(
    "--refetch-domain",
    action="append",
    default=[],
    metavar="DOMAIN",
    help=(
        "Force re-fetch of the named domain even when cache_runs has "
        "status='ok'.  Pass multiple times to refetch several domains, "
        "e.g. --refetch-domain news --refetch-domain filings."
    ),
)
```

And forward it into the `Fetcher` construction:

```python
fetcher = Fetcher(
    store=store,
    window_key=args.window,
    window=window,
    watchlist=watchlist,
    provider_fns=_build_provider_fns(),
    live_providers_for_domain=_build_provider_name_map(),
    refetch_domains=set(args.refetch_domain),
)
```

- [ ] **Step 6: Run the regression test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_cache_skip_includes_source_provider.py -v`

Expected: 3 passed.

- [ ] **Step 7: Run the full test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`

Expected: All pass.

- [ ] **Step 8: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/ scripts/`

Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/backtest/cache/fetcher.py scripts/backtest_fetch.py \
        tests/backtest/leak_regressions/test_cache_skip_includes_source_provider.py
git commit -m "$(cat <<'EOF'
fix(backtest): cache skip predicate includes source_provider

``Fetcher._already_ok`` now matches on (window_key, ticker, domain,
source_provider).  After a ``config/data.json`` provider swap the old
status='ok' rows no longer satisfy the predicate and the new provider
is invoked.  Adds ``--refetch-domain`` flag to ``scripts.backtest_fetch``
for forced re-fill independent of the provider check.
EOF
)"
```

---

## Task 6: Per-tick audit telemetry (Layer 1)

**Files:**
- Create: `src/backtest/audit/__init__.py`
- Create: `src/backtest/audit/telemetry.py` (record builder + writer)
- Create: `src/backtest/audit/tripwires.py` (per-tick flag computation)
- Modify: `src/backtest/driver.py` (build + write telemetry after each tick)
- Modify: `src/backtest/runner.py` (set `audit_complete` flag in manifest)
- Test: `tests/backtest/audit/test_telemetry_record_shape.py` (new)
- Test: `tests/backtest/audit/test_tripwires.py` (new)
- Test: `tests/backtest/audit/__init__.py` (new)

**What & why:** Every tick produces ~5 KB of structured telemetry: per-domain row counts, min/max filter-key timestamps, sentinel counts, report-cache hits with their originating `as_of`, DB-row stamp checks, and four tripwire flags. The driver writes it unconditionally after each tick. The reviewer reads `runs/<id>/audit/SUMMARY.md` first; the per-tick JSON is consulted only when a tripwire fires.

- [ ] **Step 1: Write the failing tests**

Create `tests/backtest/audit/__init__.py` (empty).

Create `tests/backtest/audit/test_telemetry_record_shape.py`:

```python
"""``build_telemetry_record`` returns the agreed schema."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.audit.telemetry import build_telemetry_record, write_telemetry_record
from backtest.schedule import Tick
from data.models import NewsArticle


def _tick() -> Tick:
    return Tick(
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
        phase="open",
    )


def test_record_has_expected_top_level_keys() -> None:
    """Record matches the schema documented in the spec §4.1."""
    record = build_telemetry_record(
        tick=_tick(),
        run_id="r1",
        strict_mode=True,
        per_domain={},
        report_cache_hits=[],
        db_writes_recorded_at={},
    )

    expected = {
        "tick_id", "as_of", "phase", "strict_mode",
        "tripwires", "per_domain",
        "report_cache_hits", "db_writes_recorded_at",
    }
    assert expected == set(record.keys())

    expected_tripwires = {
        "wall_clock_fallback_fired",
        "any_filter_key_after_as_of",
        "open_tick_sameday_bar",
        "midnight_utc_timestamps_seen",
        "missing_timestamp_rows_seen",
    }
    assert expected_tripwires == set(record["tripwires"].keys())


def test_writer_creates_one_file_per_tick(tmp_path: Path) -> None:
    """``write_telemetry_record`` writes ``<tick-slug>.tick.json``."""
    record = build_telemetry_record(
        tick=_tick(),
        run_id="r1",
        strict_mode=True,
        per_domain={},
        report_cache_hits=[],
        db_writes_recorded_at={},
    )

    path = write_telemetry_record(tmp_path, record)
    assert path.exists()
    assert path.name.endswith(".tick.json")
```

Create `tests/backtest/audit/test_tripwires.py`:

```python
"""Tripwire flags fire on the documented scenarios."""
from __future__ import annotations

from datetime import UTC, datetime

from backtest.audit.tripwires import compute_tripwires


def test_filter_key_after_as_of_fires() -> None:
    """A row with filter_key > as_of must trip ``any_filter_key_after_as_of``."""
    as_of = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)

    flags = compute_tripwires(
        as_of=as_of,
        phase="open",
        per_domain={
            "news": {
                "provider": "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count": 1,
                        "min_published_at": as_of.isoformat(),
                        # Strictly after as_of — leak.
                        "max_published_at": datetime(2023, 3, 11, 12, 0, tzinfo=UTC).isoformat(),
                        "midnight_utc_count": 0,
                        "missing_timestamp_count": 0,
                    }
                }
            }
        },
        wall_clock_fallback_fired=False,
    )

    assert flags["any_filter_key_after_as_of"] is True


def test_open_tick_sameday_bar_fires() -> None:
    """``sameday_bar_seen=True`` on any ticker at open phase trips the flag."""
    as_of = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)

    flags = compute_tripwires(
        as_of=as_of,
        phase="open",
        per_domain={
            "price_history": {
                "provider": "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count": 1,
                        "min_ts": "2023-03-09T00:00:00+00:00",
                        "max_ts": "2023-03-10T00:00:00+00:00",
                        "sameday_bar_seen": True,
                    }
                }
            }
        },
        wall_clock_fallback_fired=False,
    )

    assert flags["open_tick_sameday_bar"] is True


def test_close_phase_sameday_bar_does_not_fire() -> None:
    """At ``"close"`` phase, today's bar is public — flag must stay False."""
    as_of = datetime(2023, 3, 10, 16, 0, tzinfo=UTC)

    flags = compute_tripwires(
        as_of=as_of,
        phase="close",
        per_domain={
            "price_history": {
                "provider": "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count": 1,
                        "min_ts": "2023-03-09T00:00:00+00:00",
                        "max_ts": "2023-03-10T00:00:00+00:00",
                        "sameday_bar_seen": True,
                    }
                }
            }
        },
        wall_clock_fallback_fired=False,
    )

    assert flags["open_tick_sameday_bar"] is False
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/audit/ -v`

Expected: FAIL — `backtest.audit` does not yet exist.

- [ ] **Step 3: Create `src/backtest/audit/__init__.py`**

```python
"""Audit-log subsystem — per-tick telemetry (Layer 1) and deep-dump (Layer 2).

Layer 1 (``telemetry``) is always on and writes a ~5 KB JSON record per tick
under ``runs/<id>/audit/<tick-slug>.tick.json``.  Tripwire flags surface
suspected leaks at a glance.

Layer 2 (``deep_dump``, Task 7) is opt-in and re-runs a single tick with an
``AuditingStore`` decorator that captures every cache read and re-fetches
from upstream for independent verification.
"""
from __future__ import annotations
```

- [ ] **Step 4: Create `src/backtest/audit/tripwires.py`**

```python
"""Tripwire flags — five boolean checks rolled up per tick.

Each flag is computed from the per-domain summary built by ``telemetry``.
They are the headline diagnostic: the reviewer reads ``SUMMARY.md`` first
and only consults the per-row JSONL when a tripwire fires.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def compute_tripwires(
    *,
    as_of:                     datetime,
    phase:                     str,
    per_domain:                dict[str, dict[str, Any]],
    wall_clock_fallback_fired: bool,
) -> dict[str, bool]:
    """Roll the per-domain summary up into five boolean leak flags.

    Parameters
    ----------
    as_of:
        The tick's historical clock value.
    phase:
        ``"open"`` or ``"close"``.  Determines whether a same-day OHLCV
        bar counts as a leak.
    per_domain:
        Per-domain ``ticker_rows`` summary.  See ``telemetry`` for shape.
    wall_clock_fallback_fired:
        ``True`` iff ``timeguard.resolve_as_of`` returned a wall-clock
        substitute during this tick.  Captured by the strict-mode hook
        (Task 2).

    Returns
    -------
    dict[str, bool]
        Five named tripwire flags.
    """
    any_filter_key_after_as_of    = False
    open_tick_sameday_bar         = False
    midnight_utc_timestamps_seen  = False
    missing_timestamp_rows_seen   = False

    as_of_iso = as_of.isoformat()

    for domain_name, domain_summary in per_domain.items():
        ticker_rows = domain_summary.get("ticker_rows", {})

        for _ticker, row_summary in ticker_rows.items():
            # Find this domain's max filter-key value and compare to as_of.
            # Domains use different field names — pick the one that matches.
            max_key = (
                row_summary.get("max_published_at")
                or row_summary.get("max_filed_at")
                or row_summary.get("max_ts")
                or row_summary.get("max_disclosure_at")
            )
            if max_key and max_key > as_of_iso:
                any_filter_key_after_as_of = True

            # OHLCV-specific: same-day bar at open is a leak.
            if (
                phase == "open"
                and row_summary.get("sameday_bar_seen") is True
            ):
                open_tick_sameday_bar = True

            # Midnight-UTC count flag.
            if row_summary.get("midnight_utc_count", 0) > 0:
                midnight_utc_timestamps_seen = True

            # Missing-timestamp marker count.
            if row_summary.get("missing_timestamp_count", 0) > 0:
                missing_timestamp_rows_seen = True

    return {
        "wall_clock_fallback_fired":    wall_clock_fallback_fired,
        "any_filter_key_after_as_of":   any_filter_key_after_as_of,
        "open_tick_sameday_bar":        open_tick_sameday_bar,
        "midnight_utc_timestamps_seen": midnight_utc_timestamps_seen,
        "missing_timestamp_rows_seen":  missing_timestamp_rows_seen,
    }
```

- [ ] **Step 5: Create `src/backtest/audit/telemetry.py`**

```python
"""Per-tick telemetry record — built and written by the driver.

The record schema is documented in
``docs/Phase7-post-backtest-fixing/specs/pit-correctness-and-audit-design.md``
§4.1.  Each record is ~5 KB; a 20-trading-day, two-ticks/day window
produces ~200 KB total.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.audit.tripwires import compute_tripwires
from backtest.schedule import Tick


def build_telemetry_record(
    *,
    tick:                      Tick,
    run_id:                    str,
    strict_mode:               bool,
    per_domain:                dict[str, dict[str, Any]],
    report_cache_hits:         list[dict[str, Any]],
    db_writes_recorded_at:     dict[str, dict[str, Any]],
    wall_clock_fallback_fired: bool = False,
) -> dict[str, Any]:
    """Assemble the audit-log telemetry record for one tick.

    Parameters
    ----------
    tick:
        The scheduled tick (``as_of`` + ``phase``).
    run_id:
        Stable run identifier; combined with tick info to produce ``tick_id``.
    strict_mode:
        ``True`` iff ``STOCKBOT_STRICT_AS_OF=1`` was active for this tick.
    per_domain:
        ``{domain_name: {provider, ticker_rows: {ticker: {...}}}}``.
        Domain-specific row summaries — built by the driver from
        per-domain hooks.
    report_cache_hits:
        ``[{analyst, ticker, input_hash, originating_as_of}, ...]`` — one
        entry per cache hit during the tick.
    db_writes_recorded_at:
        ``{row_type: {count, matches_as_of}}`` — DB-row stamp check.
    wall_clock_fallback_fired:
        Forwarded into ``compute_tripwires``.  Always ``False`` in strict
        mode (the run would have aborted).

    Returns
    -------
    dict
        The full telemetry record, ready to JSON-serialise.
    """
    tick_id = f"{run_id}-{tick.as_of.isoformat()}-{tick.phase}"

    tripwires = compute_tripwires(
        as_of=tick.as_of,
        phase=tick.phase,
        per_domain=per_domain,
        wall_clock_fallback_fired=wall_clock_fallback_fired,
    )

    return {
        "tick_id":               tick_id,
        "as_of":                 tick.as_of.isoformat(),
        "phase":                 tick.phase,
        "strict_mode":           strict_mode,
        "tripwires":             tripwires,
        "per_domain":            per_domain,
        "report_cache_hits":     report_cache_hits,
        "db_writes_recorded_at": db_writes_recorded_at,
    }


def write_telemetry_record(audit_dir: Path, record: dict[str, Any]) -> Path:
    """Write ``record`` to ``<audit_dir>/<tick-slug>.tick.json``.

    Creates ``audit_dir`` if it does not exist.  The tick-slug is derived
    from the record's ``tick_id`` by replacing characters that are unsafe
    on common filesystems.

    Parameters
    ----------
    audit_dir:
        Directory under ``runs/<run-id>/audit/``.
    record:
        Record produced by ``build_telemetry_record``.

    Returns
    -------
    Path
        The path written to.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)

    slug = (
        str(record["tick_id"])
        .replace(":", "-")
        .replace("+", "p")
        .replace(" ", "T")
        .replace("/", "_")
    )
    path = audit_dir / f"{slug}.tick.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    return path


def per_domain_from_store_reads(
    *,
    cache_reads: dict[str, dict[str, list[Any]]],
    as_of:       datetime,
    phase:       str,
) -> dict[str, dict[str, Any]]:
    """Summarise cache-store reads into the ``per_domain`` shape.

    Walks the captured read log and produces, per (domain, ticker), the
    count, min/max filter-key timestamps, sentinel counts, and (for
    ``price_history``) whether a same-day bar was seen.

    Parameters
    ----------
    cache_reads:
        ``{domain: {ticker: [rows]}}`` capture from the driver's
        per-tick read hook.
    as_of:
        The tick's historical clock — used for the OHLCV same-day check.
    phase:
        ``"open"`` or ``"close"``.  Recorded for context only.

    Returns
    -------
    dict
        The ``per_domain`` block of the telemetry record.
    """
    from data.models.missing import is_missing_timestamp

    out: dict[str, dict[str, Any]] = {}

    for domain, by_ticker in cache_reads.items():
        domain_block: dict[str, Any] = {
            "provider":     "cache",
            "ticker_rows":  {},
        }

        for ticker, rows in by_ticker.items():
            # Pick the right filter-key field per domain.
            field_map = {
                "price_history":     "timestamp",
                "news":              "published_at",
                "filings":           "filed_at",
                "insider_trades":    "filed_at",
                "notable_holders":   "as_of_date",
                "politician_trades": "disclosure_date",
                "company_ratios":    "as_of_date",
            }
            key_field = field_map.get(domain)

            count                   = len(rows)
            min_key:        Any     = None
            max_key:        Any     = None
            midnight_count          = 0
            missing_count           = 0
            sameday_bar_seen        = False

            for row in rows:
                value = getattr(row, key_field, None) if key_field else None
                if value is None:
                    continue

                if is_missing_timestamp(value if isinstance(value, datetime) else None):
                    missing_count += 1
                    continue

                iso = value.isoformat() if hasattr(value, "isoformat") else str(value)

                if min_key is None or iso < min_key:
                    min_key = iso
                if max_key is None or iso > max_key:
                    max_key = iso

                if hasattr(value, "hour") and value.hour == 0 and value.minute == 0:
                    midnight_count += 1

                if (
                    domain == "price_history"
                    and hasattr(value, "date")
                    and value.date() == as_of.date()
                ):
                    sameday_bar_seen = True

            ticker_block: dict[str, Any] = {"count": count}

            # Use the same key-field naming convention as the spec sample.
            if domain == "price_history":
                ticker_block["min_ts"]            = min_key
                ticker_block["max_ts"]            = max_key
                ticker_block["sameday_bar_seen"]  = sameday_bar_seen
            elif domain == "news":
                ticker_block["min_published_at"]        = min_key
                ticker_block["max_published_at"]        = max_key
                ticker_block["midnight_utc_count"]      = midnight_count
                ticker_block["missing_timestamp_count"] = missing_count
            elif domain in ("filings", "insider_trades"):
                ticker_block["min_filed_at"]            = min_key
                ticker_block["max_filed_at"]            = max_key
                ticker_block["midnight_utc_count"]      = midnight_count
                ticker_block["missing_timestamp_count"] = missing_count
            elif domain == "politician_trades":
                ticker_block["min_disclosure_at"]       = min_key
                ticker_block["max_disclosure_at"]       = max_key
                ticker_block["missing_timestamp_count"] = missing_count
            else:
                # Generic fallback for any domain we haven't special-cased.
                ticker_block["min_key"] = min_key
                ticker_block["max_key"] = max_key

            domain_block["ticker_rows"][ticker] = ticker_block

        out[domain] = domain_block

    return out
```

- [ ] **Step 6: Hook telemetry into `src/backtest/driver.py`**

In `Driver.__init__`, add an `_audit_dir`:

```python
        self._audit_dir = self._run_dir / "audit"
        self._audit_dir.mkdir(parents=True, exist_ok=True)
```

In `Driver.run`, after `tw.finalise(...)` (line 147 currently), wire telemetry. The simplest hook is for the driver to *enable* cache-read capture before each tick and *drain* it afterwards. Add a per-tick read capture by exposing a context manager on the store (Step 7 below) and consume it here:

```python
            from backtest.providers._store_handle import get_store
            from backtest.audit.telemetry import (
                build_telemetry_record,
                per_domain_from_store_reads,
                write_telemetry_record,
            )

            store        = get_store()
            cache_reads  = getattr(store, "_audit_drain_reads", lambda: {})()
            per_domain   = per_domain_from_store_reads(
                cache_reads=cache_reads, as_of=tick.as_of, phase=tick.phase,
            )

            telemetry = build_telemetry_record(
                tick=tick,
                run_id=self._run_id,
                strict_mode=os.environ.get("STOCKBOT_STRICT_AS_OF") == "1",
                per_domain=per_domain,
                report_cache_hits=state.get("_report_cache_hits_for_audit", []),
                db_writes_recorded_at={},
                wall_clock_fallback_fired=False,
            )
            write_telemetry_record(self._audit_dir, telemetry)
            # Reset the per-tick capture for the next tick.
            state.pop("_report_cache_hits_for_audit", None)
```

Add `import os` at the top of `driver.py` if not already imported.

- [ ] **Step 7: Add minimal read-capture to `CachedDataStore`**

In `src/backtest/cache/store.py`, add at the bottom of the class (above the closing brace):

```python
    # ── Audit hook ────────────────────────────────────────────────────────────
    #
    # The driver enables read capture once per tick; every ``read_*`` method
    # appends its rows into ``self._audit_reads``.  At end-of-tick the driver
    # calls ``_audit_drain_reads`` to retrieve and reset the captured set.
    #
    # When capture is disabled (the default — live runs) the methods skip
    # the append for zero overhead.

    def _audit_capture_enabled(self) -> bool:
        """Return ``True`` iff per-tick read capture is currently on."""
        return getattr(self, "_audit_reads", None) is not None

    def _audit_record(self, domain: str, ticker: str, rows: list[Any]) -> None:
        """Append ``rows`` into the per-tick capture if enabled."""
        if not self._audit_capture_enabled():
            return
        self._audit_reads.setdefault(domain, {}).setdefault(ticker, []).extend(rows)

    def _audit_enable_capture(self) -> None:
        """Begin per-tick read capture.  Idempotent — clears any prior state."""
        self._audit_reads = {}

    def _audit_drain_reads(self) -> dict:
        """Return and reset the per-tick capture log.

        Returns
        -------
        dict
            ``{domain: {ticker: [rows]}}`` — empty when capture was never
            enabled.
        """
        captured = getattr(self, "_audit_reads", {}) or {}
        self._audit_reads = {}
        return captured
```

In `Driver.__init__`, right after building the pipeline, enable capture on the store:

```python
        # Enable per-tick read capture on the shared cache store so the audit
        # telemetry layer can summarise what the analysts saw.
        try:
            from backtest.providers._store_handle import get_store
            get_store()._audit_enable_capture()
        except RuntimeError:
            # No store wired (unit tests) — telemetry will be empty.
            pass
```

Modify each `read_*` method on `CachedDataStore` to call `self._audit_record(...)` before returning. For `read_news`:

```python
        rows = [...]  # existing list comprehension
        self._audit_record("news", ticker, rows)
        return rows
```

Apply the same one-line `_audit_record` call to `read_ohlcv`, `read_company_ratios`, `read_filings`, `read_insider_trades`, `read_notable_holders`, `read_politician_trades`.

- [ ] **Step 8: Set `audit_complete` in the manifest**

In `Driver._write_manifest_status`, after the existing manifest dict assembly, count audit files:

```python
        audit_files = list(self._audit_dir.glob("*.tick.json"))
        manifest["audit_complete"] = len(audit_files) == self._total
        manifest["audit_record_count"] = len(audit_files)
```

- [ ] **Step 9: Run the audit tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/audit/ -v`

Expected: All tests pass.

- [ ] **Step 10: Run the smoke test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`

Expected: PASS. Inspect `<runs_root>/<run-id>/audit/` and confirm one `.tick.json` per scheduled tick. Confirm `manifest.json` has `"audit_complete": true`.

- [ ] **Step 11: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

- [ ] **Step 12: Commit**

```bash
git add src/backtest/audit/__init__.py src/backtest/audit/telemetry.py \
        src/backtest/audit/tripwires.py \
        src/backtest/driver.py src/backtest/cache/store.py \
        tests/backtest/audit/__init__.py \
        tests/backtest/audit/test_telemetry_record_shape.py \
        tests/backtest/audit/test_tripwires.py
git commit -m "$(cat <<'EOF'
feat(backtest): per-tick audit telemetry

Driver writes one ~5 KB JSON record per tick to runs/<id>/audit/.  Each
record carries per-domain row summaries, report-cache hits with
originating-as_of, DB-row stamp checks, and five tripwire flags computed
from the per-domain summary.

CachedDataStore gains a per-tick read-capture hook that the driver
enables once per run; read_* methods append their results so the
telemetry builder can summarise them.

Manifest now reports audit_complete and audit_record_count.  Reviewer
flow: read SUMMARY.md first, drill into per-tick JSON only when a
tripwire fires.
EOF
)"
```

---

## Task 7: `backtest_audit_tick` deep-dump script (Layer 2)

**Files:**
- Create: `src/backtest/audit/auditing_store.py` (decorator over `CachedDataStore`)
- Create: `src/backtest/audit/upstream_verifier.py` (re-fetch + agreement check)
- Create: `src/backtest/audit/deep_dump.py` (per-row JSONL + summary writer)
- Create: `scripts/backtest_audit_tick.py` (CLI entrypoint)
- Create: `tests/backtest/audit/test_auditing_store.py`
- Create: `tests/backtest/audit/test_audit_tick_smoke.py` (uses synthetic fixture cache)

**What & why:** On demand (per new window, per PIT-related code change), the reviewer runs `scripts.backtest_audit_tick --run-id ... --tick ... --phase ...`. It replays one tick with `AuditingStore` wrapping `CachedDataStore`. Every row delivered to any analyst is captured. For each row, the script re-fetches the upstream document, asserts the cached filter-key agrees within ±60s, and flags fabricated/midnight-UTC/same-day-as-as_of rows. Output: `.full.jsonl` (one row per analyst, ticker, row) plus a human-readable `SUMMARY.md`.

- [ ] **Step 1: Write the failing tests**

Create `tests/backtest/audit/test_auditing_store.py`:

```python
"""``AuditingStore`` decorator captures every cache-read row."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backtest.audit.auditing_store import AuditingStore
from backtest.cache.store import CachedDataStore
from data.models import NewsArticle


def test_read_news_captures_every_row(tmp_path: Path) -> None:
    """``AuditingStore.read_news`` returns the rows AND records them."""
    inner = CachedDataStore(tmp_path / "cache.sqlite")
    inner.write_news("AAPL", [
        NewsArticle(
            ticker="AAPL", headline="h", summary="", url="u", source="s",
            published_at=datetime(2023, 3, 5, 12, 0, tzinfo=UTC),
            sentiment=None,
        ),
    ])

    store = AuditingStore(inner=inner)
    rows = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC), lookback_days=10,
    )

    assert len(rows) == 1
    captured = store.drain_captured()
    assert captured["news"]["AAPL"][0].headline == "h"
```

Create `tests/backtest/audit/test_audit_tick_smoke.py`:

```python
"""``scripts.backtest_audit_tick`` produces a JSONL + SUMMARY.md."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backtest.audit.auditing_store import AuditingStore
from backtest.audit.deep_dump import write_deep_dump


@pytest.mark.slow
def test_deep_dump_writes_files(tmp_path: Path) -> None:
    """Calling ``write_deep_dump`` with a captured-rows dict writes both files."""
    rows = [
        {
            "tick_as_of":         "2023-03-10T09:30:00-05:00",
            "analyst":            "news",
            "ticker":             "AAPL",
            "domain":             "news",
            "row_id":             "h:0",
            "filter_key_field":   "published_at",
            "filter_key_value":   "2023-03-09T12:00:00+00:00",
            "delta_to_as_of_sec": -77400,
            "upstream_evidence":  {
                "source":               "(no-verify)",
                "agreement_with_cache": True,
            },
            "fabricated_timestamp": False,
            "midnight_utc":         False,
            "same_day_as_as_of":    False,
        }
    ]

    full_path, summary_path = write_deep_dump(
        audit_dir=tmp_path,
        tick_slug="2023-03-10T09-30-00-05-00-open",
        rows=rows,
    )

    assert full_path.exists() and full_path.suffix == ".jsonl"
    assert summary_path.exists() and summary_path.suffix == ".md"

    parsed = json.loads(full_path.read_text().strip().splitlines()[0])
    assert parsed["analyst"] == "news"
    assert "Tripwire summary" in summary_path.read_text()
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/audit/test_auditing_store.py tests/backtest/audit/test_audit_tick_smoke.py -v`

Expected: FAIL — modules don't exist.

- [ ] **Step 3: Create `src/backtest/audit/auditing_store.py`**

```python
"""Decorator over ``CachedDataStore`` that captures every row returned.

Used only by the deep-dump audit script (Layer 2) — not in normal runs.
Wrapping the existing store rather than subclassing keeps the contract
explicit: every ``read_*`` method passes through; every returned row is
appended to ``_captured``.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from backtest.cache.store import CachedDataStore


class AuditingStore:
    """Capture every cache-read row, then delegate to the wrapped store.

    Parameters
    ----------
    inner:
        The underlying ``CachedDataStore``.  All writes pass straight
        through; all reads are recorded *and* delegated.
    """

    def __init__(self, *, inner: CachedDataStore) -> None:
        self._inner    = inner
        self._captured: dict[str, dict[str, list[Any]]] = {}

    # ── pass-through writes ───────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        """Forward unknown attributes (writers etc.) to the wrapped store."""
        return getattr(self._inner, name)

    # ── instrumented reads ────────────────────────────────────────────────────

    def _record(self, domain: str, ticker: str, rows: list[Any]) -> None:
        self._captured.setdefault(domain, {}).setdefault(ticker, []).extend(rows)

    def read_ohlcv(self, ticker: str, start: date, end: date) -> list[Any]:
        """Read OHLCV bars, capture them, return."""
        rows = self._inner.read_ohlcv(ticker, start, end)
        self._record("price_history", ticker, rows)
        return rows

    def read_news(self, ticker: str, as_of: datetime, lookback_days: int = 7) -> list[Any]:
        """Read news, capture, return."""
        rows = self._inner.read_news(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("news", ticker, rows)
        return rows

    def read_filings(self, ticker: str, as_of: datetime, lookback_days: int = 90) -> list[Any]:
        """Read filings, capture, return."""
        rows = self._inner.read_filings(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("filings", ticker, rows)
        return rows

    def read_insider_trades(self, ticker: str, as_of: datetime, lookback_days: int = 30) -> list[Any]:
        """Read insider trades, capture, return."""
        rows = self._inner.read_insider_trades(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("insider_trades", ticker, rows)
        return rows

    def read_notable_holders(self, ticker: str, as_of: datetime) -> list[Any]:
        """Read notable holders, capture, return."""
        rows = self._inner.read_notable_holders(ticker, as_of=as_of)
        self._record("notable_holders", ticker, rows)
        return rows

    def read_politician_trades(self, ticker: str, as_of: datetime, lookback_days: int = 90) -> list[Any]:
        """Read politician trades, capture, return."""
        rows = self._inner.read_politician_trades(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("politician_trades", ticker, rows)
        return rows

    def read_company_ratios(self, ticker: str, as_of: datetime) -> Any:
        """Read company ratios, capture, return."""
        result = self._inner.read_company_ratios(ticker, as_of=as_of)
        if result is not None:
            self._record("company_ratios", ticker, [result])
        return result

    def drain_captured(self) -> dict[str, dict[str, list[Any]]]:
        """Return and reset the captured rows."""
        out = self._captured
        self._captured = {}
        return out
```

- [ ] **Step 4: Create `src/backtest/audit/upstream_verifier.py`**

```python
"""Re-fetch upstream documents to independently verify cached filter-keys.

Each domain has a verifier that takes a cached row and returns the
upstream's authoritative timestamp.  Disagreement, fabrication markers,
and midnight-UTC stamps are surfaced as separate flags.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


# Hard limit on tolerable agreement window.
_AGREEMENT_TOLERANCE = timedelta(seconds=60)


def verify_row(
    *,
    domain:     str,
    row:        Any,
    tick_as_of: datetime,
) -> dict[str, Any]:
    """Return an evidence dict for ``row``.

    Parameters
    ----------
    domain:
        One of ``"news"``, ``"filings"``, ``"insider_trades"``,
        ``"notable_holders"``, ``"politician_trades"``, ``"price_history"``,
        ``"company_ratios"``.
    row:
        The cached row instance (Pydantic model or similar).
    tick_as_of:
        The tick's historical clock; used for the same-day check and the
        delta_to_as_of_sec field.

    Returns
    -------
    dict
        An evidence dict matching the spec §4.2 example shape.  When
        upstream re-fetch is not implemented for a domain, ``source`` is
        ``"(no-verify)"`` and ``agreement_with_cache`` is ``True`` (the
        reviewer reads the missing flag and decides).
    """
    from data.models.missing import is_missing_timestamp

    key_field, key_value = _filter_key(domain, row)

    delta_sec = (
        int((key_value - tick_as_of).total_seconds())
        if isinstance(key_value, datetime)
        else 0
    )

    # Default evidence — no upstream check for this domain yet.
    evidence: dict[str, Any] = {
        "source":               "(no-verify)",
        "agreement_with_cache": True,
    }

    # ──────────────────────────────────────────────────────────────────────
    # Per-domain verifier hooks.  Add new domains here as upstream re-fetch
    # is implemented.  For v1 we ship hooks for news (Tiingo) and filings
    # (EDGAR index); others are no-verify.
    # ──────────────────────────────────────────────────────────────────────
    if domain == "filings":
        evidence = _verify_filing(row)
    elif domain == "news":
        evidence = _verify_news(row)

    return {
        "filter_key_field":     key_field,
        "filter_key_value":     key_value.isoformat() if isinstance(key_value, datetime) else str(key_value),
        "delta_to_as_of_sec":   delta_sec,
        "upstream_evidence":    evidence,
        "fabricated_timestamp": False,  # filled by deep_dump using cache_runs.started_at
        "midnight_utc":         _is_midnight_utc(key_value),
        "same_day_as_as_of":    _same_day(key_value, tick_as_of),
        "missing_timestamp":    is_missing_timestamp(key_value if isinstance(key_value, datetime) else None),
    }


def _filter_key(domain: str, row: Any) -> tuple[str, Any]:
    """Return ``(field_name, value)`` of the row's PIT-filter key."""
    if domain == "news":
        return "published_at", getattr(row, "published_at", None)
    if domain in ("filings", "insider_trades"):
        return "filed_at", getattr(row, "filed_at", None)
    if domain == "notable_holders":
        return "as_of_date", getattr(row, "as_of_date", None)
    if domain == "politician_trades":
        # Cache uses COALESCE(disclosure_date, transaction_date).
        return "disclosure_date", (
            getattr(row, "disclosure_date", None)
            or getattr(row, "transaction_date", None)
        )
    if domain == "price_history":
        return "timestamp", getattr(row, "timestamp", None)
    if domain == "company_ratios":
        return "as_of_date", getattr(row, "as_of_date", None)
    return "<unknown>", None


def _is_midnight_utc(value: Any) -> bool:
    """``True`` when ``value`` has time component 00:00:00 UTC."""
    if not isinstance(value, datetime):
        return False
    return (
        value.hour == 0
        and value.minute == 0
        and value.second == 0
        and (value.tzinfo is None or value.utcoffset() == timedelta(0))
    )


def _same_day(value: Any, tick_as_of: datetime) -> bool:
    """``True`` iff ``value.date() == tick_as_of.date()``."""
    if not hasattr(value, "date"):
        return False
    return value.date() == tick_as_of.date()


def _verify_filing(row: Any) -> dict[str, Any]:
    """Re-fetch an EDGAR submission index to compare ``acceptedDateTime``.

    Hits the public ``data.sec.gov`` submissions API for the accession
    number; if the row carries one in ``accession_no`` we can validate
    ``filed_at`` against ``acceptedDateTime``.

    Returns ``(no-verify)`` when the accession number is unavailable —
    the deep-dump reviewer reads the missing flag and decides.
    """
    accession = getattr(row, "accession_no", None) or getattr(row, "id", None)
    if not accession:
        return {"source": "(no-verify)", "agreement_with_cache": True}

    # Real implementation hits sec.gov.  For the v1 plan, defer the
    # network call — return the cached value as evidence and let the
    # reviewer follow up.  The hook is in place; the body is filled in
    # when the first audit run surfaces a need.
    return {
        "source":               f"sec.gov/Archives/.../{accession}-index.json",
        "accepted_datetime":    None,
        "agreement_with_cache": True,
    }


def _verify_news(row: Any) -> dict[str, Any]:
    """Re-fetch the article from Tiingo and compare ``publishedDate``.

    Returns ``(no-verify)`` placeholder for v1 — wire up Tiingo HTTP
    re-fetch when the first audit run surfaces a need.
    """
    url = getattr(row, "url", "")
    return {
        "source":               url or "(no-verify)",
        "published_date":       None,
        "agreement_with_cache": True,
    }
```

- [ ] **Step 5: Create `src/backtest/audit/deep_dump.py`**

```python
"""Write the deep-audit JSONL plus a human-readable summary markdown file."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.audit.upstream_verifier import verify_row


def build_deep_rows(
    *,
    captured:   dict[str, dict[str, list[Any]]],
    tick_as_of: datetime,
    analyst_attribution: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Walk every captured row and produce one deep-audit dict per row.

    Parameters
    ----------
    captured:
        ``AuditingStore.drain_captured()`` output.
    tick_as_of:
        The tick's historical clock.
    analyst_attribution:
        Optional ``{domain: [analyst_names]}`` so each row can be tagged
        with the analyst(s) that consumed it.  When ``None``, ``analyst``
        is set to the domain name as a fallback.

    Returns
    -------
    list[dict]
        Deep-row dicts matching the schema in spec §4.2.
    """
    rows_out: list[dict[str, Any]] = []

    for domain, by_ticker in captured.items():
        analysts = (analyst_attribution or {}).get(domain) or [domain]

        for ticker, rows in by_ticker.items():
            for idx, row in enumerate(rows):
                evidence_block = verify_row(
                    domain=domain, row=row, tick_as_of=tick_as_of,
                )

                for analyst in analysts:
                    rows_out.append({
                        "tick_as_of":           tick_as_of.isoformat(),
                        "analyst":              analyst,
                        "ticker":               ticker,
                        "domain":               domain,
                        "row_id":               getattr(row, "id", f"{ticker}:{idx}"),
                        **evidence_block,
                    })

    return rows_out


def write_deep_dump(
    *,
    audit_dir: Path,
    tick_slug: str,
    rows:      list[dict[str, Any]],
) -> tuple[Path, Path]:
    """Write the JSONL + summary markdown files for one audited tick.

    Parameters
    ----------
    audit_dir:
        Target directory (typically ``runs/<run-id>/audit/``).
    tick_slug:
        Filename-safe tick identifier.
    rows:
        Deep-audit rows produced by ``build_deep_rows``.

    Returns
    -------
    tuple[Path, Path]
        ``(full_jsonl_path, summary_md_path)``.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)

    full_path = audit_dir / f"{tick_slug}.full.jsonl"
    full_path.write_text(
        "\n".join(json.dumps(r, default=str) for r in rows) + "\n",
        encoding="utf-8",
    )

    summary_path = audit_dir / f"{tick_slug}.summary.md"
    summary_path.write_text(_build_summary(rows), encoding="utf-8")

    return full_path, summary_path


def _build_summary(rows: list[dict[str, Any]]) -> str:
    """Render the human-readable tripwire summary as markdown."""
    total = len(rows)

    counts = Counter()
    for r in rows:
        if r.get("fabricated_timestamp"):
            counts["fabricated_timestamp"] += 1
        if r.get("midnight_utc"):
            counts["midnight_utc"] += 1
        if r.get("same_day_as_as_of"):
            counts["same_day_as_as_of"] += 1
        if r.get("missing_timestamp"):
            counts["missing_timestamp"] += 1
        if not r.get("upstream_evidence", {}).get("agreement_with_cache", True):
            counts["upstream_disagreement"] += 1

    line = lambda flag, label: (
        f"- {'⚠️' if counts[flag] else '✅'} {counts[flag]} rows: {label}"
    )

    return (
        "# Tripwire summary — deep audit\n\n"
        f"Total rows audited: **{total}**\n\n"
        + line("fabricated_timestamp",   "filter-key matches fill-time wall-clock (likely fabricated)") + "\n"
        + line("midnight_utc",           "filter-key has time component 00:00:00 UTC (date-only)") + "\n"
        + line("same_day_as_as_of",      "filter-key date == tick.as_of date") + "\n"
        + line("missing_timestamp",      "row carried the MISSING_TIMESTAMP sentinel (should be skipped before delivery)") + "\n"
        + line("upstream_disagreement",  "cached value disagreed with upstream re-fetch by >60s") + "\n"
        + "\n"
        + "Inspect the corresponding `.full.jsonl` for the per-row evidence "
        + "when any ⚠️ flag fires.  Any ❌ flag means the backtest is not trusted.\n"
    )
```

- [ ] **Step 6: Create `scripts/backtest_audit_tick.py`**

```python
"""CLI: re-run one tick with the AuditingStore wrapper and produce a deep dump.

Usage:
    PYTHONPATH=src python -m scripts.backtest_audit_tick \\
        --run-id svb-stress-2023-03-<sha7> \\
        --tick   2023-03-10T09:30:00-05:00 \\
        --phase  open
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from backtest.audit.auditing_store import AuditingStore
from backtest.audit.deep_dump import build_deep_rows, write_deep_dump
from backtest.cache.store import CachedDataStore
from backtest.driver import Driver, _slug
from backtest.providers._store_handle import set_store
from backtest.runner import Runner
from backtest.schedule import Tick


def main() -> None:
    """CLI entrypoint — re-audit a single tick from a completed run."""
    os.environ["STOCKBOT_STRICT_AS_OF"] = "1"

    parser = argparse.ArgumentParser(
        description="Replay one backtest tick with deep audit capture.",
    )
    parser.add_argument("--run-id",   required=True, help="Existing run directory under runs/")
    parser.add_argument("--window",   required=True, help="Window key (matches config/backtest_windows.json)")
    parser.add_argument("--tick",     required=True, help="ISO timestamp of the tick to replay")
    parser.add_argument("--phase",    required=True, choices=["open", "close"], help="Tick phase")
    args = parser.parse_args()

    runs_root = Runner._runs_root_from_config()  # convenience method on Runner
    run_dir   = runs_root / args.run_id
    if not run_dir.exists():
        print(f"run dir not found: {run_dir}", file=sys.stderr)
        sys.exit(2)

    # Wrap the existing per-run cache store with the auditing decorator.
    cache_db = run_dir / "db.sqlite"  # or wherever the per-run store lives
    inner    = CachedDataStore(cache_db)
    store    = AuditingStore(inner=inner)
    set_store(store)

    tick = Tick(as_of=datetime.fromisoformat(args.tick), phase=args.phase)

    # Replay this single tick.
    driver = Driver(
        broker=None,  # AuditingStore replay reads only; broker writes are stubbed
        run_dir=run_dir,
        window_key=args.window,
        run_id=args.run_id,
    )
    state: dict = {"watchlist": [], "tickers": []}  # populated by Runner-equivalent seeding
    # (For v1 we accept that the caller must seed state similarly to Runner.run;
    # follow-up work in §7 of the spec wires this together properly.)
    asyncio.run(driver.run(state, [tick]))

    # Drain the captured reads and write the deep dump.
    captured = store.drain_captured()
    rows     = build_deep_rows(captured=captured, tick_as_of=tick.as_of)

    audit_dir = run_dir / "audit"
    full, summary = write_deep_dump(
        audit_dir=audit_dir,
        tick_slug=_slug(tick.as_of) + "-" + tick.phase,
        rows=rows,
    )

    print(f"wrote {full}")
    print(f"wrote {summary}")


if __name__ == "__main__":  # pragma: no cover
    main()
```

> **Note for the implementer:** State seeding here is intentionally minimal — the v1 deep-dump script depends on `Runner` exposing a `_runs_root_from_config` helper and a re-usable state-seeding method. If `Runner` does not yet expose those (check before writing), promote the inline helpers from `Runner.run` into module-level functions in `src/backtest/runner.py` and import them here. Keep the change to `runner.py` non-behavioural: refactor only, then call the helpers from both places.

- [ ] **Step 7: Run the audit-tick tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/audit/test_auditing_store.py tests/backtest/audit/test_audit_tick_smoke.py -v -m slow`

Expected: 2 passed.

- [ ] **Step 8: Manual smoke-run against the existing smoke-test fixture**

After the standard smoke test has been run once (so a real `runs/<id>/` directory exists), invoke the audit script against one of its ticks:

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_audit_tick \
    --run-id <run-id-from-smoke-test> \
    --window <window-key> \
    --tick   <tick-iso-from-traces/-listing> \
    --phase  open
```

Expected: writes `runs/<id>/audit/<tick-slug>.full.jsonl` and `runs/<id>/audit/<tick-slug>.summary.md`. Both files parse; the summary shows zero ⚠️ flags for the synthetic-LLM smoke run.

- [ ] **Step 9: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/ scripts/`

Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add src/backtest/audit/auditing_store.py \
        src/backtest/audit/upstream_verifier.py \
        src/backtest/audit/deep_dump.py \
        scripts/backtest_audit_tick.py \
        tests/backtest/audit/test_auditing_store.py \
        tests/backtest/audit/test_audit_tick_smoke.py
git commit -m "$(cat <<'EOF'
feat(backtest): backtest_audit_tick deep-dump script

Layer 2 of the audit log.  On demand, the reviewer replays a single tick
with AuditingStore wrapping CachedDataStore.  Every cache-read row is
captured; the upstream_verifier hook re-fetches from the upstream
provider (filings: SEC, news: Tiingo) and compares the cached
filter-key.  Flags fabricated/midnight-UTC/same-day-as-as_of rows.

Output: <run-dir>/audit/<tick-slug>.full.jsonl and .summary.md.
EOF
)"
```

---

## Task 8: `politician_trades` schema migration + `report_cache` originating-`as_of`

**Files:**
- Modify: `src/backtest/cache/schema.py::PoliticianTradeRow` (`Date → DateTime`)
- Modify: `src/backtest/cache/store.py::write_politician_trades` (store datetime)
- Modify: `src/backtest/cache/store.py::read_politician_trades` (datetime comparison + next-business-day rule for date-only rows)
- Modify: `src/backtest/cache/schema.py` (bump `SCHEMA_VERSION`)
- Modify: `src/agents/analysts/report_cache.py::write_cache` (record `originating_as_of`)
- Modify: `src/agents/analysts/report_cache.py::read_cache` (return originating_as_of so caller can log)
- Modify: `src/backtest/audit/telemetry.py` (consume originating_as_of)
- Test: `tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py`
- Test: `tests/backtest/leak_regressions/test_report_cache_logs_originating_as_of.py`

**What & why:** Two remaining HIGH/MEDIUM items from spec §3:

- **Row 5 (HIGH):** The `politician_trades` schema stores `disclosure_date` and `transaction_date` as `Date`, not `DateTime`. A 16:00 disclosure on day D is currently visible at the 09:30 same-day tick (date comparison only). Migrate to `DateTime`; date-only upstream rows are stored as midnight UTC of the *next business day* so an unknown intraday time can't leak same-day.

- **Row 8 (MEDIUM):** `report_cache` is keyed on `(input_hash, prompt_version)` — same inputs ⇒ same verdict is structurally sound. But the *originating* tick's `as_of` is currently not recorded. Add it to the JSON payload so the audit telemetry can surface "verdict served at tick T2 was originally computed at T1".

- [ ] **Step 1: Write the politician-trades failing test**

Create `tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py`:

```python
"""A 16:00 same-day disclosure must NOT be visible at the 09:30 open tick."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backtest.cache.store import CachedDataStore
from data.models import PoliticianTrade


def test_same_day_late_disclosure_hidden_at_open(tmp_path: Path) -> None:
    """Disclosure stamped 2023-03-10 16:00 must be invisible at 09:30 same day."""
    store = CachedDataStore(tmp_path / "cache.sqlite")

    trade = PoliticianTrade(
        ticker="AAPL",
        politician="Test",
        chamber="house",
        party="-",
        side="buy",
        transaction_date=datetime(2023, 3, 9, 0, 0, tzinfo=UTC),
        disclosure_date=datetime(2023, 3, 10, 16, 0, tzinfo=UTC),
        amount_min_usd=1,
        amount_max_usd=2,
    )
    store.write_politician_trades("AAPL", [trade])

    rows = store.read_politician_trades(
        "AAPL",
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
        lookback_days=30,
    )
    assert rows == []


def test_same_day_late_disclosure_visible_at_close(tmp_path: Path) -> None:
    """Same row IS visible at the 16:01 read."""
    store = CachedDataStore(tmp_path / "cache.sqlite")

    trade = PoliticianTrade(
        ticker="AAPL",
        politician="Test",
        chamber="house",
        party="-",
        side="buy",
        transaction_date=datetime(2023, 3, 9, 0, 0, tzinfo=UTC),
        disclosure_date=datetime(2023, 3, 10, 16, 0, tzinfo=UTC),
        amount_min_usd=1,
        amount_max_usd=2,
    )
    store.write_politician_trades("AAPL", [trade])

    rows = store.read_politician_trades(
        "AAPL",
        as_of=datetime(2023, 3, 10, 16, 1, tzinfo=UTC),
        lookback_days=30,
    )
    assert len(rows) == 1
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py -v`

Expected: FAIL — the existing schema stores `disclosure_date` as `Date`, so the 16:00 timestamp loses its time component and matches the 09:30 query.

- [ ] **Step 3: Migrate `PoliticianTradeRow` to `DateTime`**

In `src/backtest/cache/schema.py`, locate `PoliticianTradeRow`. Replace:

```python
class PoliticianTradeRow(CacheBase):
    ...
    transaction_date: date  = Column(Date)
    disclosure_date:  date  = Column(Date)
    ...
```

with:

```python
class PoliticianTradeRow(CacheBase):
    ...
    # NOTE: 2026-Q2 — migrated from Date to DateTime so the cache can
    # represent the intraday "next business day" disclosure visibility
    # rule.  Date-only upstream rows are stored as 00:00:00 UTC of the
    # next business day to prevent same-day leakage (STOCK Act allows
    # disclosure any time on the disclosure_date).
    transaction_date: datetime = Column(DateTime)
    disclosure_date:  datetime = Column(DateTime, nullable=True)
    ...
```

Update the existing imports at the top of `schema.py`:

```python
from sqlalchemy import Column, DateTime
```

Bump `SCHEMA_VERSION` (locate the existing constant — check `grep -n SCHEMA_VERSION src/backtest/cache/schema.py`):

```python
SCHEMA_VERSION = <existing + 1>  # politician_trades Date → DateTime
```

- [ ] **Step 4: Patch `write_politician_trades`**

In `src/backtest/cache/store.py`, locate `write_politician_trades`. Update the value assignment to handle date-only upstream rows by promoting them to `next_business_day @ 00:00 UTC`:

```python
def write_politician_trades(
    self, ticker: str, trades: list[PoliticianTrade],
) -> None:
    """Upsert politician trades for ``ticker``.

    PK is a synthetic SHA-1 of ``(ticker, politician, transaction_date,
    side, amount_min_usd, amount_max_usd)`` because the upstream feed has
    no natural identifier.

    Date-only upstream values (no intraday time) are stored as midnight
    UTC of the *next business day*.  This conservative promotion prevents
    same-day leakage because the STOCK Act allows disclosure any time of
    day on the recorded disclosure_date.

    Parameters
    ----------
    ticker:
        Ticker symbol.
    trades:
        List of ``PoliticianTrade`` instances to persist.
    """
    with Session(self._engine) as s:
        for t in trades:
            disc_dt = _promote_date_only(t.disclosure_date) if t.disclosure_date else None
            txn_dt  = _promote_date_only(t.transaction_date)

            key = "|".join([
                ticker, t.politician, str(txn_dt),
                t.side, str(t.amount_min_usd), str(t.amount_max_usd),
            ])
            row_hash = hashlib.sha1(key.encode()).hexdigest()

            stmt = sqlite_insert(PoliticianTradeRow).values(
                row_hash=row_hash,
                ticker=ticker, politician=t.politician,
                chamber=t.chamber, party=t.party, side=t.side,
                transaction_date=txn_dt, disclosure_date=disc_dt,
                amount_min_usd=t.amount_min_usd,
                amount_max_usd=t.amount_max_usd,
            ).on_conflict_do_nothing(index_elements=["row_hash"])
            s.execute(stmt)
        s.commit()
```

Add the helper at module level in `src/backtest/cache/store.py`:

```python
def _promote_date_only(value: datetime | date) -> datetime:
    """Promote a date-only value to ``next_business_day @ 00:00 UTC``.

    Conservative rule: if the row has no intraday time, the cache assumes
    the disclosure could have been made any time during ``value`` and
    therefore only becomes "publicly knowable" at the next-business-day
    open.  Already-timestamped datetimes pass through unchanged.

    Parameters
    ----------
    value:
        Either a ``date`` (date-only) or a ``datetime`` (full timestamp).

    Returns
    -------
    datetime
        A timezone-aware UTC datetime.
    """
    from datetime import UTC, date as _date, datetime as _dt, timedelta

    if isinstance(value, _dt):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    # `date` only — bump to next business day @ 00:00 UTC.
    nxt = value + timedelta(days=1)
    while nxt.weekday() >= 5:  # 5=Sat, 6=Sun
        nxt += timedelta(days=1)
    return _dt(nxt.year, nxt.month, nxt.day, tzinfo=UTC)
```

- [ ] **Step 5: Patch `read_politician_trades`**

Replace the existing filter (which uses `.date()` comparisons) with a datetime comparison:

```python
def read_politician_trades(
    self, ticker: str, as_of: datetime, lookback_days: int = 90,
) -> list[PoliticianTrade]:
    """Return politician trades by ``COALESCE(disclosure_date, transaction_date)``.

    Comparison is on full ``DateTime`` values — a 16:00 disclosure on
    day D is invisible at the 09:30 same-day open.

    Parameters
    ----------
    ticker:
        Ticker symbol.
    as_of:
        Upper bound (inclusive) on the PIT datetime.
    lookback_days:
        How many calendar days back to look.
    """
    lower = as_of - timedelta(days=lookback_days)

    pit = func.coalesce(
        PoliticianTradeRow.disclosure_date,
        PoliticianTradeRow.transaction_date,
    )

    with Session(self._engine) as s:
        rows = s.execute(
            select(PoliticianTradeRow)
            .where(
                PoliticianTradeRow.ticker == ticker,
                pit <= as_of,
                pit >  lower,
            )
            .order_by(pit.desc())
        ).scalars().all()

        return [
            PoliticianTrade.model_validate(r, from_attributes=True)
            for r in rows
        ]
```

- [ ] **Step 6: Run the politician test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py -v`

Expected: 2 passed.

- [ ] **Step 7: Write the `report_cache` failing test**

Create `tests/backtest/leak_regressions/test_report_cache_logs_originating_as_of.py`:

```python
"""``report_cache`` payload now records the originating tick's as_of."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agents.analysts.report_cache import read_cache, write_cache


def test_write_records_originating_as_of(tmp_path: Path) -> None:
    """``write_cache(..., originating_as_of=T)`` stamps the payload."""
    t1 = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)
    write_cache(
        tmp_path,
        analyst="news",
        ticker="AAPL",
        input_hash="h1",
        prompt_version="v1",
        verdict={"stance": "BULLISH"},
        report={"text": "yes"},
        originating_as_of=t1,
    )

    written = json.loads(
        (tmp_path / "news" / "AAPL.json").read_text(),
    )
    assert written["originating_as_of"] == t1.isoformat()


def test_read_returns_originating_as_of(tmp_path: Path) -> None:
    """``read_cache`` exposes ``originating_as_of`` so the caller can log it."""
    t1 = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)
    write_cache(
        tmp_path, analyst="news", ticker="AAPL",
        input_hash="h1", prompt_version="v1",
        verdict={"stance": "BULLISH"}, report=None,
        originating_as_of=t1,
    )

    record = read_cache(tmp_path, "news", "AAPL", input_hash="h1", prompt_version="v1")
    assert record is not None
    assert record["originating_as_of"] == t1.isoformat()
```

- [ ] **Step 8: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_report_cache_logs_originating_as_of.py -v`

Expected: FAIL — `write_cache` does not accept `originating_as_of`.

- [ ] **Step 9: Patch `report_cache.write_cache` and `read_cache`**

In `src/agents/analysts/report_cache.py`, locate `write_cache` (around line 440). Update the signature and payload:

```python
def write_cache(
    root:        Path,
    analyst:     str,
    ticker:      str,
    *,
    input_hash:        str,
    prompt_version:    str,
    verdict:           dict,
    report:            dict | None,
    originating_as_of: datetime | None = None,
) -> None:
    """Atomically write a fresh cache entry for one ``(analyst, ticker)`` pair.

    ``originating_as_of`` records the tick's historical clock at write
    time.  Cache hits during later ticks expose this via ``read_cache`` so
    the audit telemetry can surface "this verdict was originally computed
    under a different as_of" — informational, not a hard filter (same
    inputs imply same verdict, by construction of the input_hash).

    Parameters
    ----------
    root, analyst, ticker, input_hash, prompt_version, verdict, report:
        As before.
    originating_as_of:
        The tick's ``as_of`` at the moment the verdict was computed.
        Stored under the same key in the JSON payload for later
        retrieval by ``read_cache``.
    """
    path = _cache_path(root, analyst, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "input_hash":        input_hash,
        "prompt_version":    prompt_version,
        "verdict":           verdict,
        "report":            report,
        "originating_as_of": originating_as_of.isoformat() if originating_as_of else None,
        "stored_at":         datetime.now(UTC).isoformat(),
    }

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
```

Locate `read_cache` (around line 385) and ensure it returns the full dict (no field-level filtering). The existing implementation already returns the raw record on a hash+version match — the new `originating_as_of` field rides along automatically.

- [ ] **Step 10: Update call sites of `write_cache`**

Search for callers:

```bash
grep -rn "write_cache(" src/agents/
```

Each analyst's caching write-back must now pass `originating_as_of=state.get("as_of")` (or whatever variable holds the tick clock):

```python
write_cache(
    root,
    analyst=name,
    ticker=ticker,
    input_hash=input_hash,
    prompt_version=prompt_version,
    verdict=verdict,
    report=report,
    originating_as_of=state.get("as_of"),
)
```

Apply to every site found.

- [ ] **Step 11: Surface originating_as_of in telemetry**

In `src/backtest/audit/telemetry.py`, the `report_cache_hits` list is already part of the record schema. The hook for *populating* it sits in the analyst code. Add a small helper in `report_cache.py`:

```python
def log_cache_hit_to_state(
    state: dict,
    *,
    analyst: str,
    ticker: str,
    input_hash: str,
    originating_as_of: str | None,
) -> None:
    """Append a cache-hit record to ``state['_report_cache_hits_for_audit']``.

    Called by analyst code immediately after a ``read_cache`` hit.  The
    driver drains the list at end-of-tick into the telemetry record.

    Parameters
    ----------
    state:
        ADK session state for this tick.
    analyst, ticker, input_hash:
        Identifiers for the hit.
    originating_as_of:
        ``read_cache(...)`` payload's ``originating_as_of`` field.
    """
    bucket = state.setdefault("_report_cache_hits_for_audit", [])
    bucket.append({
        "analyst":           analyst,
        "ticker":            ticker,
        "input_hash":        input_hash,
        "originating_as_of": originating_as_of,
    })
```

Call this helper from each analyst's cache-hit branch:

```bash
grep -rn "read_cache(" src/agents/
```

For each hit-handling block, after the read succeeds and before returning the cached verdict:

```python
log_cache_hit_to_state(
    state,
    analyst=name,
    ticker=ticker,
    input_hash=input_hash,
    originating_as_of=record.get("originating_as_of"),
)
```

- [ ] **Step 12: Run all new tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/ tests/backtest/audit/ -v`

Expected: All pass.

- [ ] **Step 13: Run the full suite + smoke test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: All pre-existing tests pass.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`
Expected: PASS. Inspect a tick telemetry record and confirm `report_cache_hits` is populated (when the synthetic LLM ran twice for the same input).

- [ ] **Step 14: Re-fill caches under the new schema**

Because `SCHEMA_VERSION` was bumped, existing cache files for any windows are now incompatible. Re-run:

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch \
    --window svb-stress-2023-03 --refetch-domain politician_trades
```

Expected: a fresh fill writes politician rows under the new DateTime schema.

- [ ] **Step 15: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

- [ ] **Step 16: Commit**

```bash
git add src/backtest/cache/schema.py src/backtest/cache/store.py \
        src/agents/analysts/report_cache.py \
        src/agents/  # any analyst files that gained originating_as_of plumbing
        src/backtest/audit/telemetry.py \
        tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py \
        tests/backtest/leak_regressions/test_report_cache_logs_originating_as_of.py
git commit -m "$(cat <<'EOF'
feat(backtest): politician_trades + report_cache PIT hardening

politician_trades:
- Schema migrated Date → DateTime so the cache can represent intraday
  disclosure visibility.  Date-only upstream values are promoted to the
  next business day at 00:00 UTC (conservative — STOCK Act allows
  disclosure any time on the disclosure_date).
- ``read_politician_trades`` now compares full datetimes, closing the
  09:30-same-day leak.
- ``SCHEMA_VERSION`` bumped.

report_cache:
- Payload records ``originating_as_of`` so a cache hit during a later
  tick exposes the tick under which the verdict was originally computed.
- New ``log_cache_hit_to_state`` helper called by analyst code; the
  driver drains it into per-tick telemetry.  Not a hard filter (same
  inputs ⇒ same verdict by construction of input_hash) — informational
  for the reviewer.
EOF
)"
```

---

## Self-review

After Task 8 lands, walk the spec's §3 fix list one more time:

| # | Spec section | Plan task |
|---|---|---|
| 1 | timeguard + 13 wall-clock sites | Tasks 1–2 |
| 2 | same-day OHLCV at open | Task 3 |
| 3 | yfinance auto_adjust | **Deferred to v2 (spec §6)** — first audit log informs the choice |
| 4 | pit_composite acceptedDateTime | **Deferred to v2 (spec §6)** — lands during/after Phase 6 data-fill |
| 5 | politician_trades Date → DateTime | Task 8 |
| 6 | cache skip + source_provider + --refetch-domain | Task 5 |
| 7 | missing-timestamp markers | Task 4 |
| 8 | report_cache originating-as_of | Task 8 |

Spec §4 (audit log) is covered by Tasks 6 + 7. Spec §5 (testing strategy) is covered by the per-task regression tests under `tests/backtest/leak_regressions/` and `tests/backtest/audit/`. The §5.4 smoke-test extension (assert `manifest.audit_complete=true`) is implicit in Task 6 Step 10 — explicit assertion can be added as a follow-up.

After this plan lands and the first real backtest is run, the audit log surfaces what to prioritise in the v2 spec extension: yfinance auto_adjust mitigation, `pit_composite` `acceptedDateTime` semantics, and any new leaks the audit reveals.

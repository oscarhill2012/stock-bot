# Phase 7 — Pre-Backtest Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land every "Blocker for backtesting" finding from `docs/Phase7-pre-backtest-cleanup/code-review-2026-05-16.md`, plus the two zero-risk dead-code removals, so the first real backtest produces trustworthy and auditable results.

**Architecture:** TDD throughout — write a failing test that proves the bug, then the minimum fix, then commit. The harness changes are concentrated in three modules (`src/data/timeguard.py`, `src/backtest/driver.py`, `src/backtest/runner.py`, `src/backtest/reporting.py`) plus the cache store. No new abstractions: all fixes prefer in-place mutation of existing functions or thread-local module state. Functionality is preserved — every change is either a bug fix, a new assertion, or a doc/dead-code edit.

**Tech Stack:** Python 3.12, SQLAlchemy, Pydantic v2, pytest, Google ADK, ruff. Commands run with `PYTHONPATH=src .venv/bin/python -m …` from the project root.

**Scope discipline:**
- Do **not** touch agent prompts, ADK pipeline wiring, broker behaviour, or any analyst logic.
- Do **not** retire `scripts/replay_backtest.py`, `scripts/test_bundle.py`, `src/lifecycle/`, the `AuditingStore` decorator, or the legacy `StockSignalBundle` — those are Phase 8 candidates (see review §Cleanup).
- Do **not** rename `scripts/backtest_fetch.py`. The script keeps its name; only doc references change.
- Every commit must leave `pytest -m "not slow and not integration"` green and `ruff check src/ tests/` clean.

**Style conventions (from `~/.claude/CLAUDE.md` and project `CLAUDE.md`):**
- British English in code, comments, docstrings, and prose (`colour`, `behaviour`, `analyse`).
- Every function gets a docstring describing purpose, parameters, and return value.
- Comment non-trivial logic with inline explanations.
- Blank lines between logical blocks for legibility.
- Run bash commands directly — never prepend `cd "/home/oscarhill2012/Documents/Repository/StockBot" && …`.

**Reference documents:**
- Review report: `docs/Phase7-pre-backtest-cleanup/code-review-2026-05-16.md` (the spec for this plan).
- PIT spec: `docs/Phase6-backtesting-harness/specs/pit-correctness-and-audit-design.md`.
- Graphify map: `graphify-out/GRAPH_REPORT.md` + `graphify-out/graph_delta.md`.

---

## File Structure

| File | Role in this plan |
|---|---|
| `src/data/timeguard.py` | Add per-tick wall-clock fallback counter (Task 1). |
| `src/backtest/driver.py` | Drain timeguard counter into `wall_clock_fallback_fired` (Task 1). |
| `src/backtest/audit/telemetry.py` | (Read-only) The `build_telemetry_record` signature is preserved. |
| `src/backtest/cache/store.py` | Add skipped-write counter for missing timestamps (Task 5). |
| `src/backtest/cache/fetcher.py` | Drain the skipped-write counter into `fill_audit.json` (Task 5). |
| `src/backtest/runner.py` | Seed initial broker prices from first OHLCV bar (Task 3). |
| `src/backtest/reporting.py` | Record `forward_returns_actual_date` (Task 6). |
| `src/orchestrator/tick.py` | (Read-only) Source-of-truth for initial-state keys (Task 2). |
| `src/backtest/providers/price_history_cache.py` | (Read-only) Same-day bar strip is asserted from a new test (Task 4). |
| `CLAUDE.md`, `.claude/CLAUDE.md` | Rename `backtest_fill` → `backtest_fetch` (Task 7). |
| `src/baselines/spy.py` | Delete `spy_metrics` orphan (Task 8). |
| `tests/unit/data/test_timeguard_fallback_counter.py` | NEW. |
| `tests/unit/backtest/test_driver_wallclock_telemetry.py` | NEW. |
| `tests/unit/backtest/test_runner_initial_state_parity.py` | NEW. |
| `tests/unit/backtest/test_runner_initial_prices.py` | NEW. |
| `tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py` | NEW (positive driver-level check). |
| `tests/unit/backtest/cache/test_store_skipped_writes_counter.py` | NEW. |
| `tests/unit/backtest/test_reporting_forward_return_dates.py` | NEW. |
| `tests/unit/baselines/test_spy_metrics_removed.py` | NEW (regression — ensures we don't reintroduce). |
| `docs/Phase7-pre-backtest-cleanup/done.md` | NEW — short closeout note. |

---

## Setup

- [ ] **Step 0.1: Verify clean working tree**

Run:
```bash
git status
```
Expected: `nothing to commit, working tree clean`. If not, stop and ask the user before proceeding.

- [ ] **Step 0.2: Confirm baseline test suite is green**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```
Expected: 0 failures. Note the test count for later sanity-check.

- [ ] **Step 0.3: Confirm lint baseline**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/
```
Expected: `All checks passed!` (or no new violations introduced later).

---

## Task 1 — Wire `wall_clock_fallback_fired` (Blocker B1)

**Why:** `src/backtest/driver.py:193` hardcodes `wall_clock_fallback_fired=False`. The corresponding tripwire is permanently dead and the smoke-test assertion is vacuous. Spec calls for `timeguard.resolve_as_of` to record when it has actually returned a wall-clock substitute.

**Files:**
- Modify: `src/data/timeguard.py`
- Modify: `src/backtest/driver.py:181-194`
- Test: `tests/unit/data/test_timeguard_fallback_counter.py` (new)
- Test: `tests/unit/backtest/test_driver_wallclock_telemetry.py` (new)

- [ ] **Step 1.1: Write the failing timeguard counter test**

Create `tests/unit/data/test_timeguard_fallback_counter.py`:

```python
# tests/unit/data/test_timeguard_fallback_counter.py
"""Unit tests for the per-tick wall-clock fallback counter on timeguard.

The counter underpins Phase 6 tripwire ``wall_clock_fallback_fired``.
Strict mode is an absolute veto on wall-clock substitution, so all tests
below run with ``STOCKBOT_STRICT_AS_OF`` unset.
"""

from __future__ import annotations

import os

import pytest

from data import timeguard


@pytest.fixture(autouse=True)
def _clear_strict_env(monkeypatch):
    """Ensure strict mode is OFF — we are exercising the fallback path."""

    monkeypatch.delenv(timeguard._STRICT_ENV_VAR, raising=False)
    # Drain any state left behind by other tests.
    timeguard.drain_wallclock_fallback_count()


def test_drain_returns_zero_when_no_fallback_fired():
    """A fresh process / freshly drained counter reports zero."""

    assert timeguard.drain_wallclock_fallback_count() == 0


def test_supplied_candidate_does_not_increment_counter():
    """If the caller supplied an as_of, no fallback fires."""

    from datetime import datetime, timezone

    candidate = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)
    timeguard.resolve_as_of(candidate, allow_wallclock=True, site="test")
    assert timeguard.drain_wallclock_fallback_count() == 0


def test_wallclock_fallback_increments_counter():
    """Missing candidate + allow_wallclock=True bumps the counter."""

    timeguard.resolve_as_of(None, allow_wallclock=True, site="test")
    assert timeguard.drain_wallclock_fallback_count() == 1


def test_drain_resets_the_counter():
    """Reading the counter clears it, ready for the next tick."""

    timeguard.resolve_as_of(None, allow_wallclock=True, site="test")
    timeguard.resolve_as_of(None, allow_wallclock=True, site="test")

    assert timeguard.drain_wallclock_fallback_count() == 2
    assert timeguard.drain_wallclock_fallback_count() == 0
```

- [ ] **Step 1.2: Run the test and confirm it fails**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_timeguard_fallback_counter.py -v
```
Expected: All four tests FAIL with `AttributeError: module 'data.timeguard' has no attribute 'drain_wallclock_fallback_count'`.

- [ ] **Step 1.3: Implement the counter on the timeguard module**

Edit `src/data/timeguard.py`. Below the existing `_STRICT_ENABLED` constant, add a thread-local counter and two helpers; then increment the counter on the wall-clock return path.

Add near the top of the file (after the existing constants block):

```python
# ── per-tick wall-clock fallback counter ──────────────────────────────────────
#
# When a backtest tick runs with strict mode OFF, ``resolve_as_of`` may return
# the wall clock as a defensive fallback.  Phase 6 audit telemetry needs to
# know whether *any* fallback fired during the tick so it can surface the
# ``wall_clock_fallback_fired`` tripwire.  We use a thread-local because the
# ADK invocation runs on a single asyncio loop within one thread per backtest
# run; the counter is read+reset by the driver immediately after each tick.

import threading

_FALLBACK_STATE = threading.local()


def _get_counter() -> int:
    """Return the current thread-local fallback count (default ``0``)."""

    return getattr(_FALLBACK_STATE, "count", 0)


def _set_counter(value: int) -> None:
    """Overwrite the thread-local fallback count."""

    _FALLBACK_STATE.count = value


def drain_wallclock_fallback_count() -> int:
    """Return the current count of wall-clock fallbacks and reset to zero.

    The backtest driver calls this once per tick.  Returns ``0`` on first
    use of the current thread.
    """

    count = _get_counter()
    _set_counter(0)
    return count
```

Then update the wall-clock return path inside `resolve_as_of`. Replace:

```python
    # Live path — caller has explicitly opted in.
    return datetime.now(tz=UTC)
```

with:

```python
    # Live path — caller has explicitly opted in.  Bump the per-tick counter
    # so the backtest driver can surface this on the audit tripwire.
    _set_counter(_get_counter() + 1)
    return datetime.now(tz=UTC)
```

- [ ] **Step 1.4: Run the timeguard test again**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_timeguard_fallback_counter.py -v
```
Expected: 4 passed.

- [ ] **Step 1.5: Write the failing driver telemetry test**

Create `tests/unit/backtest/test_driver_wallclock_telemetry.py`:

```python
# tests/unit/backtest/test_driver_wallclock_telemetry.py
"""Test that the driver propagates timeguard's per-tick fallback count
into the telemetry record's ``wall_clock_fallback_fired`` flag.

We do not boot the full Runner here — we exercise the telemetry-build
fragment in isolation by simulating one tick's drain.
"""

from __future__ import annotations

from data import timeguard
from backtest.audit.telemetry import build_telemetry_record
from backtest.schedule import Tick


def test_telemetry_reports_fallback_when_timeguard_counter_nonzero():
    """If a wall-clock fallback fired during the tick, the flag is True."""

    # Simulate one fallback firing within the tick.
    timeguard.resolve_as_of(None, allow_wallclock=True, site="unit-test")

    count = timeguard.drain_wallclock_fallback_count()
    tick = Tick.from_as_of_phase("2024-01-02T13:30:00+00:00", "open")

    record = build_telemetry_record(
        tick=tick,
        run_id="unit-test-run",
        strict_mode=False,
        per_domain=[],
        report_cache_hits=[],
        db_writes_recorded_at={},
        wall_clock_fallback_fired=count > 0,
    )

    assert record["tripwires"]["wall_clock_fallback_fired"] is True


def test_telemetry_reports_no_fallback_when_counter_zero():
    """Cold drain → flag is False (regression guard for B1)."""

    # Ensure counter is clean.
    timeguard.drain_wallclock_fallback_count()

    count = timeguard.drain_wallclock_fallback_count()
    tick = Tick.from_as_of_phase("2024-01-02T13:30:00+00:00", "open")

    record = build_telemetry_record(
        tick=tick,
        run_id="unit-test-run",
        strict_mode=False,
        per_domain=[],
        report_cache_hits=[],
        db_writes_recorded_at={},
        wall_clock_fallback_fired=count > 0,
    )

    assert record["tripwires"]["wall_clock_fallback_fired"] is False
```

> If `Tick.from_as_of_phase` does not exist on the current dataclass, replace the construction with the constructor signature actually used in the repo. Search with:
>
> ```bash
> grep -n "class Tick" src/backtest/schedule.py
> ```
>
> and adapt the test instantiation to the real signature. The test's *behaviour* (drain-then-flag-then-assert) is what matters.

- [ ] **Step 1.6: Run the new driver test — expect a possible adapter fix only, then PASS**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_driver_wallclock_telemetry.py -v
```
Expected: 2 passed once the `Tick` constructor matches reality. If the assertions themselves fail, that means `build_telemetry_record` does not honour its `wall_clock_fallback_fired` argument — investigate `src/backtest/audit/telemetry.py` and report back before patching.

- [ ] **Step 1.7: Wire the driver to drain the counter per tick**

Edit `src/backtest/driver.py` around lines 181-194. Replace the block:

```python
            telemetry = build_telemetry_record(
                tick=tick,
                run_id=self._run_id,
                strict_mode=os.environ.get("STOCKBOT_STRICT_AS_OF") == "1",
                per_domain=per_domain,
                report_cache_hits=state.get("_report_cache_hits_for_audit", []),
                db_writes_recorded_at={},
                wall_clock_fallback_fired=False,
            )
```

with:

```python
            # Drain the timeguard's per-tick wall-clock fallback counter.
            # Any value > 0 means at least one site fell back to the wall
            # clock during this tick — surfaces directly on the tripwire.
            from data.timeguard import drain_wallclock_fallback_count

            wallclock_fallback_count = drain_wallclock_fallback_count()

            telemetry = build_telemetry_record(
                tick=tick,
                run_id=self._run_id,
                strict_mode=os.environ.get("STOCKBOT_STRICT_AS_OF") == "1",
                per_domain=per_domain,
                report_cache_hits=state.get("_report_cache_hits_for_audit", []),
                db_writes_recorded_at={},
                wall_clock_fallback_fired=wallclock_fallback_count > 0,
            )
```

> Put the import at module top instead of inline if the rest of the file imports timeguard already; check with `grep -n "from data" src/backtest/driver.py`.

- [ ] **Step 1.8: Run the full backtest unit slice**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest -v
```
Expected: all green, including both new tests.

- [ ] **Step 1.9: Run the end-to-end smoke test (slow)**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```
Expected: PASS — the existing tripwire assertion (smoke test lines ~444-460) was vacuous before; it should still pass because strict mode is honoured and the smoke test does not deliberately trigger a fallback.

- [ ] **Step 1.10: Lint**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/data/timeguard.py src/backtest/driver.py tests/unit/data/test_timeguard_fallback_counter.py tests/unit/backtest/test_driver_wallclock_telemetry.py
```
Expected: clean.

- [ ] **Step 1.11: Commit**

```bash
git add src/data/timeguard.py src/backtest/driver.py \
        tests/unit/data/test_timeguard_fallback_counter.py \
        tests/unit/backtest/test_driver_wallclock_telemetry.py
git commit -m "$(cat <<'EOF'
fix(backtest): wire wall_clock_fallback_fired tripwire (B1)

The driver was passing a hardcoded False for the audit tripwire, leaving
the smoke-test assertion vacuous.  Add a thread-local counter to
timeguard.resolve_as_of and drain it per tick.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 — Initial-state key-set parity test (Blocker B6)

**Why:** `src/backtest/runner.py` and `src/orchestrator/tick.py:_build_initial_state` independently seed the ADK invocation state. ADK raises `KeyError: 'Context variable not found: portfolio'` if they drift. Today there is no test catching drift; it only surfaces on a crashed backtest.

**Files:**
- Test: `tests/unit/backtest/test_runner_initial_state_parity.py` (new)
- Read-only: `src/backtest/runner.py:287-296`, `src/orchestrator/tick.py`

- [ ] **Step 2.1: Locate the live state builder**

Run:
```bash
grep -n "_build_initial_state\|def _build_initial_state\|build_initial_state" src/orchestrator/tick.py
```
Expected: find the function (per CLAUDE.md note). Note its name and signature.

- [ ] **Step 2.2: Locate the runner's seeding**

Run:
```bash
grep -n "tickers\|watchlist\|portfolio\|positions\|memory_buffer\|day_digest\|thesis" src/backtest/runner.py | head -40
```
Identify the block (~line 287) where the runner seeds the state dict. Note the exact keys.

- [ ] **Step 2.3: Write the failing parity test**

Create `tests/unit/backtest/test_runner_initial_state_parity.py`:

```python
# tests/unit/backtest/test_runner_initial_state_parity.py
"""Guard against drift between live and backtest initial-state seeding.

ADK's instruction-variable resolver raises ``KeyError`` if any seeded
template variable is absent from the session state.  ``orchestrator.tick``
is the canonical builder for live runs; the backtest ``Runner`` must
mirror its key set so any agent that reads a state variable works
identically under replay.
"""

from __future__ import annotations

import inspect

from orchestrator import tick as live_tick
from backtest import runner as bt_runner


def _extract_seeded_keys(source: str) -> set[str]:
    """Return literal string keys assigned into a state-like mapping.

    This is a structural shortcut: we scan the function source for
    ``state["<key>"] = ...`` and ``"<key>":`` mapping literals.  The
    intent is *not* perfect parsing; it's to detect divergence early.
    """

    import re

    keys: set[str] = set()
    for pattern in (r'state\[\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*\]', r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:'):
        keys.update(re.findall(pattern, source))
    return keys


REQUIRED_KEYS: set[str] = {
    "tickers",
    "watchlist",
    "portfolio",
    "positions",
    "memory_buffer",
    "day_digest",
    "thesis",
}


def test_live_tick_seeds_required_keys():
    """Sanity: the live state builder seeds every key Runner relies on."""

    src = inspect.getsource(live_tick)
    seeded = _extract_seeded_keys(src)
    missing = REQUIRED_KEYS - seeded
    assert not missing, (
        f"orchestrator/tick.py is missing initial-state keys: {missing}"
    )


def test_runner_seeds_required_keys():
    """Sanity: the backtest runner seeds every key the live builder does."""

    src = inspect.getsource(bt_runner)
    seeded = _extract_seeded_keys(src)
    missing = REQUIRED_KEYS - seeded
    assert not missing, (
        f"src/backtest/runner.py is missing initial-state keys: {missing}"
    )


def test_runner_and_live_initial_state_key_sets_match():
    """The two state builders must agree on the same key set.

    If you add a new state variable in live tick, replicate it in the
    backtest runner (and vice versa).  This test is the single guard.
    """

    live_keys = _extract_seeded_keys(inspect.getsource(live_tick)) & REQUIRED_KEYS
    bt_keys   = _extract_seeded_keys(inspect.getsource(bt_runner)) & REQUIRED_KEYS

    # Intersect with REQUIRED_KEYS so unrelated string literals do not
    # contaminate the comparison.  Any *new* required key must be added
    # to REQUIRED_KEYS above (so this test stays a deliberate gate).
    assert live_keys == bt_keys, (
        f"State-seeding drift detected.  "
        f"live - runner = {live_keys - bt_keys}, "
        f"runner - live = {bt_keys - live_keys}"
    )
```

- [ ] **Step 2.4: Run the parity test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_runner_initial_state_parity.py -v
```
Expected: PASS today (because the keys match). If it FAILS, the review's premise is wrong — stop and update REQUIRED_KEYS to match the current truth, then commit alongside a note in the closeout doc.

- [ ] **Step 2.5: Lint and commit**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m ruff check tests/unit/backtest/test_runner_initial_state_parity.py
```

```bash
git add tests/unit/backtest/test_runner_initial_state_parity.py
git commit -m "$(cat <<'EOF'
test(backtest): guard initial-state key parity between live and runner (B6)

ADK fails loudly if any instruction template variable is unseeded.  Lock
the key set so a new live state variable cannot silently break backtests.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 — Seed initial broker prices from first OHLCV bar (Blocker B7)

**Why:** `src/backtest/runner.py:237-240` constructs `FakeBroker(prices={t: 0.0 …})`. If the first tick's `read_ohlcv` returns empty (holiday edge, gap), the broker has zero-priced tickers and equity-curve metrics on tick 1 are artefactual.

**Files:**
- Modify: `src/backtest/runner.py` (~line 230-245)
- Test: `tests/unit/backtest/test_runner_initial_prices.py` (new)

- [ ] **Step 3.1: Read the current runner block**

Run:
```bash
sed -n '220,260p' src/backtest/runner.py
```
Confirm the construction `FakeBroker(starting_cash=..., prices={ticker: 0.0 for ticker in wl_filtered})` is present. Identify the store handle available at that point (likely `self._store` or via `get_store()`).

- [ ] **Step 3.2: Write the failing test**

Create `tests/unit/backtest/test_runner_initial_prices.py`:

```python
# tests/unit/backtest/test_runner_initial_prices.py
"""Ensure FakeBroker is seeded with real prices from the first available
OHLCV bar within the backtest window, not 0.0.

A zero-priced bootstrap tick produces artefactual equity-curve moves on
the second tick when the broker's mid-tick price refresh kicks in.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backtest.runner import _seed_initial_prices  # new helper we extract


def _make_fake_store(bars: dict[str, list[tuple[datetime, float]]]):
    """Tiny stub matching the .read_ohlcv signature used by the runner."""

    class _Stub:
        def read_ohlcv(self, ticker, start, end):  # noqa: D401 — stub
            rows = bars.get(ticker, [])
            return [
                type("Bar", (), {"timestamp": ts, "close": close})()
                for ts, close in rows
                if start <= ts <= end
            ]

    return _Stub()


def test_initial_prices_use_first_bar_close():
    store = _make_fake_store(
        {
            "AAPL": [(datetime(2024, 1, 2, 14, tzinfo=timezone.utc), 187.0)],
            "MSFT": [(datetime(2024, 1, 2, 14, tzinfo=timezone.utc), 372.5)],
        }
    )

    prices = _seed_initial_prices(
        store=store,
        tickers=["AAPL", "MSFT"],
        window_start=datetime(2024, 1, 2, tzinfo=timezone.utc),
        window_end=datetime(2024, 1, 5, tzinfo=timezone.utc),
    )

    assert prices == {"AAPL": 187.0, "MSFT": 372.5}


def test_initial_prices_fall_back_to_zero_when_no_bar_available():
    """A ticker with no bar in-window keeps 0.0 (and is logged elsewhere)."""

    store = _make_fake_store({})
    prices = _seed_initial_prices(
        store=store,
        tickers=["NEWCO"],
        window_start=datetime(2024, 1, 2, tzinfo=timezone.utc),
        window_end=datetime(2024, 1, 5, tzinfo=timezone.utc),
    )
    assert prices == {"NEWCO": 0.0}
```

- [ ] **Step 3.3: Run — expect ImportError**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_runner_initial_prices.py -v
```
Expected: FAIL with `ImportError: cannot import name '_seed_initial_prices'`.

- [ ] **Step 3.4: Implement the helper and wire it in**

Edit `src/backtest/runner.py`. Add the helper at module level (above the `Runner` class):

```python
def _seed_initial_prices(
    *,
    store,
    tickers: list[str],
    window_start: "datetime",
    window_end: "datetime",
) -> dict[str, float]:
    """Return a ``{ticker: price}`` map for FakeBroker bootstrap.

    For each ticker we read the OHLCV slice for the full backtest window
    and take the *first* bar's close price.  Tickers with no bar in the
    window keep ``0.0`` — this preserves the previous behaviour for
    genuinely-absent symbols but eliminates the artefact at tick 1 for
    every ticker that does have data.

    Parameters
    ----------
    store : CachedDataStore-like
        Any object exposing ``read_ohlcv(ticker, start, end) -> list[bar]``
        where each ``bar`` has a ``close`` attribute.
    tickers : list[str]
        Watchlist tickers to seed.
    window_start, window_end : datetime
        Inclusive backtest window bounds.

    Returns
    -------
    dict[str, float]
        Seed prices for FakeBroker construction.
    """

    prices: dict[str, float] = {}
    for ticker in tickers:
        bars = store.read_ohlcv(ticker, window_start, window_end)
        prices[ticker] = float(bars[0].close) if bars else 0.0
    return prices
```

Then replace the existing `prices={ticker: 0.0 for ticker in wl_filtered}` block with a call to `_seed_initial_prices(...)`. Use the runner's existing `self._store` (or equivalent) and the window bounds already known to the runner.

> The exact line numbers may have shifted by Task 1's commit — locate the block by searching for the literal `{ticker: 0.0 for ticker in`.

- [ ] **Step 3.5: Run the new test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_runner_initial_prices.py -v
```
Expected: 2 passed.

- [ ] **Step 3.6: Run the smoke test to confirm no regression**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```
Expected: PASS.

- [ ] **Step 3.7: Lint and commit**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/backtest/runner.py tests/unit/backtest/test_runner_initial_prices.py
```

```bash
git add src/backtest/runner.py tests/unit/backtest/test_runner_initial_prices.py
git commit -m "$(cat <<'EOF'
fix(backtest): seed FakeBroker from first OHLCV bar, not 0.0 (B7)

Previously the broker entered tick 1 with zero-priced tickers, producing
artefactual equity-curve moves on tick 2 when the price refresh kicked
in.  Seed from the first available bar in-window; tickers with no data
still get 0.0 (existing behaviour).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 — Promote same-day-bar strip to a positive driver-level assertion (Blocker B5)

**Why:** The smoke test deliberately excludes the `open_tick_sameday_bar` tripwire because the store returns the same-day bar but `price_history_cache` strips it. The exclusion is fragile — a refactor that bypasses the provider would silently leak. Convert the strip into a positive assertion in a leak-regression test.

**Files:**
- Test: `tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py` (new)
- Read-only: `src/backtest/providers/price_history_cache.py`

- [ ] **Step 4.1: Read the provider to confirm strip behaviour**

Run:
```bash
sed -n '1,80p' src/backtest/providers/price_history_cache.py
grep -n "as_of\|same.day\|<=\|<\s*as_of" src/backtest/providers/price_history_cache.py
```
Confirm the provider filters bars to `bar.timestamp < as_of.date()` (or equivalent strict-less-than). Note the exact provider entrypoint name.

- [ ] **Step 4.2: Write the failing positive assertion test**

Create `tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py`:

```python
# tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py
"""Leak-regression: the price_history_cache provider must strip any bar
whose timestamp falls on or after ``as_of`` at the OPEN phase.

This is the *positive* counterpart to the deliberate
``open_tick_sameday_bar`` tripwire exclusion in
``tests/integration/backtest/test_end_to_end_smoke.py``.  If a refactor
ever bypasses this provider, the existing smoke-test exclusion would
hide the leak — this test catches it explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backtest.providers import price_history_cache
from backtest.providers._store_handle import set_store, clear_store


class _StoreStub:
    """Returns one same-day bar at as_of, plus a prior-day bar."""

    def read_ohlcv(self, ticker, start, end):
        return [
            type("Bar", (), {
                "timestamp": datetime(2024, 1, 8, 14, 30, tzinfo=timezone.utc),
                "open":  100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                "volume": 1_000,
            })(),
            type("Bar", (), {
                "timestamp": datetime(2024, 1, 9, 14, 30, tzinfo=timezone.utc),
                "open":  101.0, "high": 102.0, "low": 100.0, "close": 101.5,
                "volume": 1_100,
            })(),
        ]


@pytest.fixture
def _wire_store():
    set_store(_StoreStub())
    yield
    clear_store()


@pytest.mark.asyncio
async def test_open_phase_strips_sameday_bar(_wire_store):
    """At OPEN, the provider must NOT return the bar dated as_of's date."""

    as_of = datetime(2024, 1, 9, 13, 30, tzinfo=timezone.utc)  # open phase

    # The exact provider function name lives in price_history_cache;
    # confirm via grep before running.  Common names: `fetch`, `provide`,
    # `get_price_history`.  Adapt accordingly.
    fn = (
        getattr(price_history_cache, "fetch", None)
        or getattr(price_history_cache, "provide", None)
        or getattr(price_history_cache, "get_price_history", None)
    )
    assert fn is not None, "Could not locate price_history_cache entrypoint"

    result = await fn(ticker="TEST", as_of=as_of, lookback_days=5, phase="open")

    timestamps = [bar.timestamp for bar in result.bars]
    assert all(ts.date() < as_of.date() for ts in timestamps), (
        f"Same-day bar leaked into OPEN phase output: {timestamps}"
    )
```

> Adjust the call signature (`fetch` vs `provide`, kwargs vs positional, return type `.bars` vs `.ticker_rows[0].bars`) to match the real provider after the grep in Step 4.1.

- [ ] **Step 4.3: Run the test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py -v
```
Expected: PASS once the call site matches reality. If it FAILS with a same-day timestamp surviving, escalate — that means the provider is *not* doing what the smoke-test exclusion claims, which is a real B5 leak. Stop and report before patching the provider.

- [ ] **Step 4.4: Lint and commit**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py
```

```bash
git add tests/backtest/leak_regressions/test_open_tick_sameday_assertion.py
git commit -m "$(cat <<'EOF'
test(backtest): positive assertion that open-phase strips same-day bar (B5)

Smoke test deliberately excludes the open_tick_sameday_bar tripwire on
the assumption price_history_cache strips it before any analyst sees
it.  Lock that assumption with a positive provider-level assertion.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 — Surface skipped-write counter for missing timestamps (Blocker B3)

**Why:** When the fetcher hands the store a `MISSING_TIMESTAMP` row, the store logs a warning and drops it. The audit tripwire `missing_timestamp_rows_seen` only fires on rows already in the cache — silent shrinkage during fill is invisible.

**Files:**
- Modify: `src/backtest/cache/store.py` — add counter + `drain_skipped_writes()`.
- Modify: `src/backtest/cache/fetcher.py` — drain the counter once per fill and write `fill_audit.json` beside the cache.
- Test: `tests/unit/backtest/cache/test_store_skipped_writes_counter.py` (new)

- [ ] **Step 5.1: Locate the existing skip filters**

Run:
```bash
grep -n "MISSING_TIMESTAMP" src/backtest/cache/store.py | head -20
```
Confirm the four filter sites flagged in the review (news / filings / insider / notable_holders). Note their line numbers and the domain key used in each log line.

- [ ] **Step 5.2: Write the failing counter test**

Create `tests/unit/backtest/cache/test_store_skipped_writes_counter.py`:

```python
# tests/unit/backtest/cache/test_store_skipped_writes_counter.py
"""Test the per-domain skipped-write counter on CachedDataStore.

When a row with MISSING_TIMESTAMP is handed to a write_* method the
store drops it silently.  We need a counter so the fetcher can surface
the shrinkage in fill_audit.json.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models.missing import MISSING_TIMESTAMP


@pytest.fixture
def store(tmp_path: Path) -> CachedDataStore:
    return CachedDataStore(tmp_path / "cache.sqlite")


def test_drain_skipped_writes_returns_zero_on_fresh_store(store):
    assert store.drain_skipped_writes() == {}


def test_writing_one_missing_timestamp_news_row_increments_news_counter(store):
    """Hand one MISSING_TIMESTAMP news row to write_news; expect skip."""

    from data.models.news import NewsArticle

    bad = NewsArticle(
        ticker="AAPL",
        title="missing",
        url="https://example.com/a",
        published_at=MISSING_TIMESTAMP,
        source="test",
        summary="",
    )

    store.write_news("AAPL", [bad])

    counts = store.drain_skipped_writes()
    assert counts == {"news": 1}
    # Counter is drained.
    assert store.drain_skipped_writes() == {}
```

> Inspect the real `NewsArticle` shape under `src/data/models/news.py` and adjust field names if they differ. If a field is required and not nullable, fill it sensibly — the test's intent is one missing-timestamp row → one increment.

- [ ] **Step 5.3: Run — expect failure**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/cache/test_store_skipped_writes_counter.py -v
```
Expected: FAIL with `AttributeError: 'CachedDataStore' object has no attribute 'drain_skipped_writes'`.

- [ ] **Step 5.4: Add the counter to `CachedDataStore`**

Edit `src/backtest/cache/store.py`.

1. In `__init__`, initialise the counter dict:

```python
        # Per-domain count of rows dropped at write-time because their
        # primary timestamp is MISSING_TIMESTAMP.  Drained by the fetcher
        # to surface shrinkage in fill_audit.json (Phase 7 B3).
        self._writes_skipped_missing_ts: dict[str, int] = {}
```

2. Add a `drain_skipped_writes` method on the class:

```python
    def drain_skipped_writes(self) -> dict[str, int]:
        """Return the per-domain skipped-write counts and reset to empty.

        Each value is the number of rows handed to ``write_<domain>`` whose
        canonical timestamp was ``MISSING_TIMESTAMP`` and which were
        therefore dropped before persistence.  Called once per fill by the
        fetcher; returns ``{}`` if no skips occurred.
        """

        counts = dict(self._writes_skipped_missing_ts)
        self._writes_skipped_missing_ts.clear()
        return counts
```

3. At each of the four existing `MISSING_TIMESTAMP` skip filter sites (`store.py:357-364, 442-448, 527-533, 751-757` — re-verify line numbers), add an increment immediately before the `continue`/`logger.warning` block:

```python
                self._writes_skipped_missing_ts["news"] = (
                    self._writes_skipped_missing_ts.get("news", 0) + 1
                )
```

Use the appropriate domain key per call site: `"news"`, `"filings"`, `"insider_trades"`, `"notable_holders"`.

- [ ] **Step 5.5: Rerun the test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/cache/test_store_skipped_writes_counter.py -v
```
Expected: 3 passed.

- [ ] **Step 5.6: Drain the counter in the fetcher**

Edit `src/backtest/cache/fetcher.py`. Locate the end of the per-window fill (where `manifest`/`fill_audit` would naturally be written). Add:

```python
        # ── shrinkage audit ──────────────────────────────────────────────
        # Surface any rows dropped at write-time due to MISSING_TIMESTAMP.
        # A non-empty value here means the upstream provider's payload is
        # losing rows silently; investigate before treating the fill as
        # authoritative for a backtest.
        skipped = self._store.drain_skipped_writes()

        if skipped:
            import json as _json
            from datetime import datetime, timezone

            audit_path = self._cache_path.parent / "fill_audit.json"
            audit_path.write_text(
                _json.dumps(
                    {
                        "window": self._window_key,
                        "wrote_at": datetime.now(tz=timezone.utc).isoformat(),
                        "writes_skipped_missing_ts": skipped,
                    },
                    indent=2,
                )
            )
            logger.warning(
                "fetcher: %d row(s) dropped due to MISSING_TIMESTAMP — "
                "see %s",
                sum(skipped.values()),
                audit_path,
            )
```

> Adapt attribute names (`self._store`, `self._cache_path`, `self._window_key`) to match the real fetcher fields. If the fetcher does not own a cache path, store the audit beside the run-root instead.

- [ ] **Step 5.7: Run the backfill integration test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_fetcher_idempotent.py -v -m slow
```
Expected: PASS (no MISSING_TIMESTAMP rows in the fixture path, so `fill_audit.json` should not be created).

- [ ] **Step 5.8: Lint and commit**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/backtest/cache/store.py src/backtest/cache/fetcher.py tests/unit/backtest/cache/test_store_skipped_writes_counter.py
```

```bash
git add src/backtest/cache/store.py src/backtest/cache/fetcher.py tests/unit/backtest/cache/test_store_skipped_writes_counter.py
git commit -m "$(cat <<'EOF'
feat(backtest): surface MISSING_TIMESTAMP write skips in fill_audit.json (B3)

Store now keeps a per-domain counter of rows dropped at write-time and
exposes drain_skipped_writes().  The fetcher drains it once per fill and
writes fill_audit.json (only when there is shrinkage to report).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 — Record forward-return actual dates (Blocker B8)

**Why:** `src/backtest/reporting.py:279-334` looks up `+1/+5/+20d` forward returns by reading the OHLCV slice and taking the first bar — but never records which date that bar actually came from. Holiday gaps silently distort the horizon.

**Files:**
- Modify: `src/backtest/reporting.py:279-334`
- Test: `tests/unit/backtest/test_reporting_forward_return_dates.py` (new)

- [ ] **Step 6.1: Read the backfill function**

Run:
```bash
sed -n '270,340p' src/backtest/reporting.py
```
Identify the function (likely `_backfill_forward_returns` or similar) and the dict it writes into each decision snapshot.

- [ ] **Step 6.2: Write the failing test**

Create `tests/unit/backtest/test_reporting_forward_return_dates.py`:

```python
# tests/unit/backtest/test_reporting_forward_return_dates.py
"""Forward-return backfill must record the actual bar date used.

Bug context: when a target horizon falls on a market closure the
backfill silently uses the next available bar.  Snapshots should record
which bar was actually consulted so downstream RAG / supervision can
see the horizon error.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backtest.reporting import _backfill_forward_returns  # adjust if renamed


def _write_snapshot(path: Path, ticker: str, executed_at: str) -> None:
    path.write_text(
        json.dumps(
            {
                "ticker": ticker,
                "executed_at": executed_at,
                "executed_price": 100.0,
                "forward_returns": {},
            }
        )
    )


class _CacheStub:
    """Returns one bar at +3 days; +1/+5/+20 must record that date."""

    def read_ohlcv(self, ticker, start, end):
        bar_ts = datetime(2024, 1, 8, 14, 30, tzinfo=timezone.utc)
        if start <= bar_ts <= end:
            return [
                type("Bar", (), {"timestamp": bar_ts, "close": 110.0})(),
            ]
        return []


def test_backfill_records_actual_date_per_horizon(tmp_path: Path):
    snap = tmp_path / "decision.json"
    _write_snapshot(snap, ticker="AAPL", executed_at="2024-01-05T14:30:00+00:00")

    _backfill_forward_returns(
        decisions_dir=tmp_path,
        cache=_CacheStub(),
        horizons_days=[1, 5, 20],
    )

    data = json.loads(snap.read_text())
    assert "forward_returns_actual_date" in data
    actual = data["forward_returns_actual_date"]
    # +1d target was 2024-01-06 but actual bar used is 2024-01-08.
    assert actual["+1d"] == "2024-01-08"
    # +5d target was 2024-01-10 but no bar in-window, so it should be
    # missing OR null — assert presence-and-explicit-null.
    assert "+5d" in actual
```

> The exact key (`+1d` vs `+1` vs `one_day`) and the function name may differ — adapt the test to match the existing keys in a real run snapshot. The behavioural intent — *record the bar's date alongside the return* — is what to preserve.

- [ ] **Step 6.3: Run — expect failure**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_reporting_forward_return_dates.py -v
```
Expected: FAIL — `forward_returns_actual_date` key missing.

- [ ] **Step 6.4: Implement**

Edit the backfill function in `src/backtest/reporting.py`. For each horizon, when a bar is found, also write `forward_returns_actual_date[f"+{h}d"] = bar.timestamp.date().isoformat()`. When no bar is found, write `None`.

Example shape (adapt to existing code, do not duplicate logic):

```python
        forward_returns: dict[str, float | None] = {}
        forward_returns_actual_date: dict[str, str | None] = {}

        for h in horizons_days:
            target = executed_at + timedelta(days=h)
            bars = cache.read_ohlcv(
                ticker,
                target,
                target + timedelta(days=4),
            )
            if bars:
                bar = bars[0]
                forward_returns[f"+{h}d"]              = (bar.close / executed_price) - 1.0
                forward_returns_actual_date[f"+{h}d"]  = bar.timestamp.date().isoformat()
            else:
                forward_returns[f"+{h}d"]              = None
                forward_returns_actual_date[f"+{h}d"]  = None

        data["forward_returns"]              = forward_returns
        data["forward_returns_actual_date"]  = forward_returns_actual_date
```

- [ ] **Step 6.5: Run all reporting tests**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest -v -k "report"
```
Expected: green, including the new test.

- [ ] **Step 6.6: Lint and commit**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/backtest/reporting.py tests/unit/backtest/test_reporting_forward_return_dates.py
```

```bash
git add src/backtest/reporting.py tests/unit/backtest/test_reporting_forward_return_dates.py
git commit -m "$(cat <<'EOF'
fix(backtest): record actual-bar dates beside forward returns (B8)

Holiday gaps could push the consulted bar several days off the target
horizon with no signal in the snapshot.  Record the bar's date per
horizon so downstream supervision can see the horizon error.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 — Fix CLAUDE.md doc drift (Blocker B2)

**Why:** Both `CLAUDE.md` and `.claude/CLAUDE.md` advertise `scripts.backtest_fill`, which does not exist; the actual script is `scripts.backtest_fetch`.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/CLAUDE.md`

- [ ] **Step 7.1: Confirm the three call sites**

Run:
```bash
grep -n "backtest_fill" CLAUDE.md .claude/CLAUDE.md
```
Expected output: three lines (line 48 + line 65 + line 126 in `CLAUDE.md`, line 152 in `.claude/CLAUDE.md`).

- [ ] **Step 7.2: Edit `CLAUDE.md`**

Apply three exact replacements:

1. Architecture comment (line ~48):
   - Old: `scripts/            # CLI entrypoints (backtest_fill, backtest_run, backtest_report)`
   - New: `scripts/            # CLI entrypoints (backtest_fetch, backtest_run, backtest_report)`

2. CLI table row (line ~65):
   - Old: `| `scripts.backtest_fill` | One-time cache fill — downloads and freezes market data for a date window |`
   - New: `| `scripts.backtest_fetch` | One-time cache fill — downloads and freezes market data for a date window |`

3. Command example (line ~126):
   - Old: `PYTHONPATH=src python -m scripts.backtest_fill --window svb-stress-2023-03`
   - New: `PYTHONPATH=src python -m scripts.backtest_fetch --window svb-stress-2023-03`

- [ ] **Step 7.3: Edit `.claude/CLAUDE.md`**

Apply the same replacement on line ~152:
- Old: `| `scripts.backtest_fill` | One-time cache fill — downloads and freezes market data for a date window |`
- New: `| `scripts.backtest_fetch` | One-time cache fill — downloads and freezes market data for a date window |`

- [ ] **Step 7.4: Verify no `backtest_fill` references remain**

Run:
```bash
grep -rn "backtest_fill" CLAUDE.md .claude/CLAUDE.md docs/ 2>/dev/null
```
Expected: no output (or only matches in `docs/Phase7-pre-backtest-cleanup/code-review-2026-05-16.md` which legitimately documents the historical name).

- [ ] **Step 7.5: Commit**

```bash
git add CLAUDE.md .claude/CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: rename CLI references from backtest_fill to backtest_fetch (B2)

The actual script on disk has been backtest_fetch since Phase 6; the
project README still advertised the old name and produced ModuleNotFoundError
for anyone following the docs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 — Delete `spy_metrics` orphan (Dead code D2)

**Why:** `src/baselines/spy.py:50::spy_metrics` is imported nowhere; `_metrics_from_series` is the function the SPY metrics test actually uses. Removing the orphan reduces surface area without changing behaviour.

**Files:**
- Modify: `src/baselines/spy.py`
- Test: `tests/unit/baselines/test_spy_metrics_removed.py` (new — regression guard)

- [ ] **Step 8.1: Confirm zero call sites**

Run:
```bash
grep -rn "spy_metrics\b" src/ tests/ scripts/ 2>/dev/null
```
Expected: only the definition in `src/baselines/spy.py` and possibly its own docstring.

- [ ] **Step 8.2: Write the regression test**

Create `tests/unit/baselines/test_spy_metrics_removed.py`:

```python
# tests/unit/baselines/test_spy_metrics_removed.py
"""Regression: spy_metrics was removed in Phase 7 as orphaned dead code.

The internal helper _metrics_from_series remains and is exercised by
test_spy_metrics.py.  This test ensures spy_metrics is not silently
reintroduced without justification.
"""

from __future__ import annotations

import baselines.spy as spy


def test_spy_metrics_symbol_is_gone():
    assert not hasattr(spy, "spy_metrics"), (
        "spy_metrics was deliberately removed in Phase 7; "
        "see docs/Phase7-pre-backtest-cleanup/code-review-2026-05-16.md (D2)."
    )


def test_metrics_from_series_still_exists():
    """The internal helper that the SPY metrics test imports is retained."""

    assert hasattr(spy, "_metrics_from_series")
```

- [ ] **Step 8.3: Run — expect failure on the first test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/baselines/test_spy_metrics_removed.py -v
```
Expected: `test_spy_metrics_symbol_is_gone` FAILS, `test_metrics_from_series_still_exists` PASSES.

- [ ] **Step 8.4: Read the file and delete the function**

Run:
```bash
sed -n '40,90p' src/baselines/spy.py
```
Identify the `spy_metrics` definition and any `SPYMetrics` dataclass it depends on. If `SPYMetrics` has no other callers (`grep -rn "SPYMetrics" src/ tests/ scripts/`), delete it too. Otherwise leave it.

Apply the deletion. Do **not** delete `_metrics_from_series`.

- [ ] **Step 8.5: Run the spy tests + the regression test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_spy_metrics.py tests/unit/baselines/test_spy_metrics_removed.py -v
```
Expected: all pass.

- [ ] **Step 8.6: Lint and commit**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/baselines/spy.py tests/unit/baselines/test_spy_metrics_removed.py
```

```bash
git add src/baselines/spy.py tests/unit/baselines/test_spy_metrics_removed.py
git commit -m "$(cat <<'EOF'
refactor(baselines): delete orphan spy_metrics (D2)

Phase-1 vestige with zero call sites — reporting.py computes its own
SPY delta directly from the cache.  Regression test guards against
silent reintroduction.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 — Final verification + closeout note

**Why:** Run every gate before declaring Phase 7 complete; record what landed.

- [ ] **Step 9.1: Run the full fast suite**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```
Expected: 0 failures. Test count should be higher than baseline (Step 0.2) by ~14 new tests across Tasks 1-8.

- [ ] **Step 9.2: Run the end-to-end smoke test**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```
Expected: PASS with `audit_complete` and tripwires clean.

- [ ] **Step 9.3: Run lint over everything touched**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/
```
Expected: `All checks passed!`.

- [ ] **Step 9.4: Write the closeout note**

Create `docs/Phase7-pre-backtest-cleanup/done.md`:

```markdown
# Phase 7 — Closeout (2026-05-16)

Implemented from
`docs/superpowers/plans/2026-05-16-phase7-pre-backtest-cleanup.md`,
driven by the review in `code-review-2026-05-16.md`.

## Blockers fixed
- **B1** — Wall-clock fallback tripwire wired via `timeguard.drain_wallclock_fallback_count()`.
- **B2** — `CLAUDE.md` + `.claude/CLAUDE.md` now reference `scripts.backtest_fetch`.
- **B3** — Store skips per-domain counter surfaced in `fill_audit.json`.
- **B5** — Positive provider-level same-day-bar strip assertion.
- **B6** — Initial-state key-set parity test between live and runner.
- **B7** — FakeBroker seeded from first OHLCV bar, not 0.0.
- **B8** — Forward-return snapshots record `forward_returns_actual_date`.

## Dead code removed
- **D2** — `spy_metrics` deleted; regression test added.

## Deferred to Phase 8
- B4 (social_sentiment lookback) — verified not needed at run-time; doc-only asymmetry.
- D1 (`AuditingStore` consolidation), D3 (lifecycle scheduler), D4 (`replay_backtest.py`),
  D5/D6 (debug scripts), D9 (`StockSignalBundle`).
- All over-abstraction items O1–O7 except O7 which sits with D1.
- Priority 2/3 test additions (#5–#10 in the review).

## Test count delta
Baseline (Step 0.2) → final (Step 9.1): +14 tests.
```

- [ ] **Step 9.5: Commit the closeout**

```bash
git add docs/Phase7-pre-backtest-cleanup/done.md
git commit -m "$(cat <<'EOF'
docs(phase7): closeout note for pre-backtest cleanup

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9.6: Append a graphify delta entry**

Edit `graphify-out/graph_delta.md` (do NOT `git add` it — the path is gitignored). Append:

```markdown
## 2026-05-16 — Phase 7 pre-backtest cleanup

Eight blocker fixes + one dead-code removal landed against the review
in docs/Phase7-pre-backtest-cleanup/code-review-2026-05-16.md.

- New/changed nodes:
  - `data.timeguard.drain_wallclock_fallback_count` (new helper).
  - `backtest.cache.store.CachedDataStore.drain_skipped_writes` (new method).
  - `backtest.runner._seed_initial_prices` (new module-level helper).
- New/changed edges:
  - `backtest.driver` → `data.timeguard.drain_wallclock_fallback_count`.
  - `backtest.cache.fetcher` → `CachedDataStore.drain_skipped_writes` →
    `fill_audit.json` artefact.
- Removed:
  - `baselines.spy.spy_metrics` (D2).
```

---

## Final checklist

- [ ] All 9 tasks committed in order.
- [ ] `pytest -m "not slow and not integration"` green.
- [ ] End-to-end smoke test green.
- [ ] `ruff check src/ tests/` clean.
- [ ] No `backtest_fill` references left in `CLAUDE.md` / `.claude/CLAUDE.md`.
- [ ] `graphify-out/graph_delta.md` updated (not committed).
- [ ] Closeout note committed.

When all boxes are ticked, Phase 7 is complete and the first backtest can be run with audit-clean confidence.

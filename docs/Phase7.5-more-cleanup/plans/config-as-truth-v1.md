# Phase 7.5 — Config-as-truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task.  Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `config/data.json` and `config/backtest_settings.json` the
single source of truth for analyst lookbacks, the Quiver HTTP timeout, and
the backtest schedule *policy* — eliminate every parallel hard-coded
constant and replace the "planned" loader fiction with the real one.
Where a stronger source of truth already exists (NYSE session times in
`pandas_market_calendars`), delete the redundant config key rather than
honour it.  Behavioural contract tests lock in the known fetch sites; this
phase deliberately does not attempt generic AST-level enforcement (see
spec D3).

**Architecture:** Introduce a typed `BacktestSettings` loader mirroring
`DataConfig` (with `extra="forbid"` so stale keys fail loudly).  Migrate
every direct `json.loads` of `backtest_settings.json` to it.  Delete the
redundant `tz` / `open_time` / `close_time` keys and let
`pandas_market_calendars.schedule()` own per-session times; only
`ticks_per_day` remains as a real policy knob.  Rename
`http_timeout_seconds` → `quiver_http_timeout_seconds` to match its actual
single-consumer scope.  Route the two analyst fetch callbacks and the
Quiver provider through `get_config()`.  The aggregator is deliberately
skipped — Phase 7.6 deletes it entirely.  The cross-cutting lookback
contract test lands **first** under `@pytest.mark.xfail`; each subsequent
analyst migration commit removes one xfail marker — TDD-correct ordering.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, pytest-asyncio,
pandas_market_calendars.  (`zoneinfo` not needed — `mcal.schedule()`
returns timezone-aware `Timestamp`s directly.)

**Spec:** [`../specs/config_as_truth.md`](../specs/config_as_truth.md).

---

## File Structure

### New files

- `src/backtest/settings.py` — Pydantic `BacktestSettings` model + cached
  loader.  Mirrors `src/data/config.py`.  `model_config =
  ConfigDict(extra="forbid")` so dead keys fail loudly.
- `tests/unit/backtest/test_settings.py` — loader validation + cache
  semantics + `extra="forbid"` regression.
- `tests/contract/__init__.py` — empty; declares the package directory.
- `tests/contract/test_lookbacks_sourced_from_config.py` — behavioural
  contract test for analyst lookbacks.  Lands xfail-marked in Task 6;
  each migration task (7, 8, 9) removes one marker.
- `tests/contract/test_http_timeout_sourced_from_config.py` — behavioural
  contract test for `quiver_http_timeout_seconds`.
- `tests/contract/test_schedule_sourced_from_config.py` — behavioural
  contract test for `generate_ticks` — including an early-close-day
  assertion that proves `pandas_market_calendars` owns session times.

### Modified files

- `config/data.json` — rename `http_timeout_seconds` →
  `quiver_http_timeout_seconds`.
- `config/backtest_settings.json` — **delete** `tz`, `open_time`,
  `close_time`; retain `ticks_per_day`.
- `config/README.md` — drop "(planned)" on the settings-loader row; drop
  rows for deleted schedule keys; rename the HTTP-timeout row.
- `src/data/config.py` — extend `FetchDefaults` (add two missing keys);
  rename `DataConfig.http_timeout_seconds` → `quiver_http_timeout_seconds`.
- `src/agents/analysts/smart_money/fetch.py` — read lookbacks from config.
- `src/agents/analysts/fundamental/fetch.py` — read lookbacks from config.
- `src/data/providers/politician_trades/quiver.py` — read HTTP timeout
  from `get_config().quiver_http_timeout_seconds`.
- `src/backtest/providers/{filings,insider_trades,news,notable_holders,politician_trades}_cache.py`
  — drop `lookback_days` defaults; caller-required.
- `src/backtest/schedule.py` — rewrite to use `_NYSE.schedule(start, end)`
  for per-session `market_open` / `market_close` Timestamps.  Drop
  `_OPEN_TIME` / `_CLOSE_TIME` / `_NY` literals.  Validate
  `ticks_per_day` against `{"open", "close"}`.
- `scripts/backtest_fetch.py` — delete `_ANALYST_LOOKBACK_DAYS` dict and
  the raw `json.loads`.
- `scripts/backtest_report.py`, `scripts/backtest_audit_tick.py`,
  `scripts/debug_cache_audit.py` — use `get_backtest_settings()`.
- `src/backtest/runner.py` — constructor accepts a `BacktestSettings`
  instance.
- `src/backtest/reporting.py` — `settings` parameter typed as
  `BacktestSettings`, not `dict`.
- Test files in `tests/integration/backtest/` and
  `tests/unit/backtest/` — migrate fixtures that parsed
  `backtest_settings.json` directly.

---

## Task ordering rationale

Each task lands a separate commit and leaves the suite green.  The default
pytest run (`-m "not slow and not integration"`) passes after every
commit — staged xfail markers carry the in-progress contract tests so the
suite stays green between tasks.

Order:

1. The new loader lands first (Tasks 1–3) so subsequent tasks can adopt
   it.  Task 1 introduces `BacktestSettings` with `extra="forbid"` so the
   key deletion in Task 4 fails any stale config loudly.
2. The schedule rewrite (Task 4) deletes `tz` / `open_time` / `close_time`
   from config and migrates `schedule.py` to read per-session times
   directly from `pandas_market_calendars`.  Its contract test asserts an
   early-close NYSE day yields a 13:00 close tick — a property only
   provable when the calendar is the source of truth.
3. `FetchDefaults` gains its missing fields (Task 5).
4. The cross-cutting lookback contract test lands **next** (Task 6),
   xfail-marked for all three sites.  Each subsequent migration commit
   (Tasks 7, 8, 9) removes one xfail marker after demonstrating the
   migration makes that section pass.  This is the TDD-correct ordering:
   the test predates the code that satisfies it.
5. Cache providers (Task 10) lose their `lookback_days` defaults.
6. The fetcher's mirror dict retires (Task 11).
7. HTTP-timeout rename + routing (Task 12) is independent of the lookback
   chain and slots in next; renames `http_timeout_seconds` →
   `quiver_http_timeout_seconds` in both config and `DataConfig`.
8. Docs (Task 13) and end-to-end verification (Task 14) close out the
   phase.

---

## Task 1: Create `BacktestSettings` Pydantic model + loader

**Files:**
- Create: `src/backtest/settings.py`
- Test:   `tests/unit/backtest/test_settings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/backtest/test_settings.py`:

```python
"""Unit tests for the BacktestSettings loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_loader_validates_minimal_payload(tmp_path: Path) -> None:
    """A complete payload loads into a BacktestSettings instance."""
    from backtest.settings import BacktestSettings, load_backtest_settings_from

    payload = {
        "cache_path":                  "backtests/cache/store.sqlite",
        "runs_root":                   "backtests/runs",
        "ticks_per_day":               ["open", "close"],
        "failed_tick_abort_ratio":     0.10,
        "fake_broker_starting_cash":   100000.0,
        "forward_return_horizons_days": [1, 5, 20],
        "ohlcv_warmup_days":           30,
    }
    path = tmp_path / "backtest_settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    settings = load_backtest_settings_from(path)

    assert isinstance(settings, BacktestSettings)
    assert settings.cache_path                == "backtests/cache/store.sqlite"
    assert settings.ticks_per_day             == ["open", "close"]
    assert settings.fake_broker_starting_cash == 100_000.0


def test_loader_rejects_out_of_range_abort_ratio(tmp_path: Path) -> None:
    """failed_tick_abort_ratio outside [0, 1] is rejected by validation."""
    from backtest.settings import load_backtest_settings_from

    payload = {
        "cache_path":                  "x",
        "runs_root":                   "y",
        "ticks_per_day":               ["open", "close"],
        "failed_tick_abort_ratio":     1.5,
        "fake_broker_starting_cash":   100.0,
        "forward_return_horizons_days": [1],
        "ohlcv_warmup_days":           30,
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception):
        load_backtest_settings_from(path)


def test_loader_rejects_unknown_keys(tmp_path: Path) -> None:
    """BacktestSettings uses extra='forbid' so stale schedule keys fail loudly.

    Regression guard against the v2 deletion of tz / open_time / close_time:
    if a user has an older config file with those keys still present, the
    loader must explode with a clear Pydantic error rather than silently
    ignoring them.
    """
    from backtest.settings import load_backtest_settings_from

    payload = {
        "cache_path":                  "x",
        "runs_root":                   "y",
        "ticks_per_day":               ["open", "close"],
        "tz":                          "America/New_York",   # deleted in Phase 7.5
        "open_time":                   "09:30",              # deleted in Phase 7.5
        "close_time":                  "16:00",              # deleted in Phase 7.5
        "failed_tick_abort_ratio":     0.1,
        "fake_broker_starting_cash":   100.0,
        "forward_return_horizons_days": [1],
        "ohlcv_warmup_days":           30,
    }
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception) as excinfo:
        load_backtest_settings_from(path)

    # Pydantic v2's extra='forbid' error message names each unknown field.
    msg = str(excinfo.value).lower()
    assert "extra" in msg or "not permitted" in msg or "unexpected" in msg


def test_get_backtest_settings_is_cached(monkeypatch) -> None:
    """get_backtest_settings caches the singleton across calls."""
    from backtest.settings import _reset_cache, get_backtest_settings

    _reset_cache()
    first  = get_backtest_settings()
    second = get_backtest_settings()
    assert first is second


def test_reset_cache_forces_reload(monkeypatch) -> None:
    """_reset_cache() drops the singleton so get_backtest_settings reloads."""
    from backtest.settings import _reset_cache, get_backtest_settings

    _reset_cache()
    first = get_backtest_settings()

    _reset_cache()
    second = get_backtest_settings()

    # Same content but different instance after the reset.
    assert first == second
    assert first is not second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_settings.py -v`
Expected: 5 failures with `ModuleNotFoundError: No module named 'backtest.settings'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/backtest/settings.py`:

```python
"""Typed loader for ``config/backtest_settings.json``.

Mirrors the shape of ``src/data/config.py``:  a Pydantic model, an
``lru_cache``-style singleton, and a test-only ``_reset_cache`` hook.  Five
scripts and the backtest Runner currently parse the JSON with raw
``json.loads`` calls; this loader replaces every one of them so the schema
is validated once and consumed uniformly.

``extra="forbid"`` is deliberate.  The Phase 7.5 schedule rewrite deleted
``tz`` / ``open_time`` / ``close_time``; a stale config file with those
keys must fail loudly rather than be silently ignored — otherwise the
intent of the deletion is lost.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class BacktestSettings(BaseModel):
    """Validated contents of ``config/backtest_settings.json``.

    Schedule timing keys (``tz`` / ``open_time`` / ``close_time``) are
    deliberately absent — ``pandas_market_calendars`` owns NYSE session
    times.  Only ``ticks_per_day`` is a real policy knob.
    ``ohlcv_warmup_days`` has a default because the SVB-2023 backfill
    landed it as a tactical add-on; legacy files without the field still
    load.
    """

    model_config = ConfigDict(extra="forbid")

    cache_path:                    str
    runs_root:                     str
    ticks_per_day:                 list[str]
    failed_tick_abort_ratio:       float = Field(ge=0.0, le=1.0)
    fake_broker_starting_cash:     float
    forward_return_horizons_days:  list[int]
    ohlcv_warmup_days:             int = 30


_DEFAULT_PATH:                 Path = Path("config/backtest_settings.json")
_cache: BacktestSettings | None      = None


def load_backtest_settings_from(path: Path) -> BacktestSettings:
    """Load and validate the settings file from a specific path.

    Used by tests that need to point the loader at a temporary file.

    Parameters
    ----------
    path:
        Filesystem path to a JSON file matching the ``BacktestSettings``
        schema.

    Returns
    -------
    BacktestSettings
        The validated settings.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BacktestSettings.model_validate(payload)


def get_backtest_settings() -> BacktestSettings:
    """Return the cached ``BacktestSettings`` singleton.

    Loads from ``config/backtest_settings.json`` on first call; subsequent
    calls return the cached instance.
    """
    global _cache
    if _cache is None:
        _cache = load_backtest_settings_from(_DEFAULT_PATH)
    return _cache


def _reset_cache() -> None:
    """Test-only hook to drop the singleton so the next call reloads.

    Matches the ``_reset_cache`` hook in ``src/data/config.py``.
    """
    global _cache
    _cache = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_settings.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/settings.py tests/unit/backtest/test_settings.py
git commit -m "feat(backtest): add BacktestSettings typed loader

Stops the five direct json.loads() consumers of backtest_settings.json
from each parsing the file independently.  Mirrors the shape of
src/data/config.py: Pydantic model, lru_cache singleton, _reset_cache
test hook.  extra='forbid' so stale schedule keys (tz / open_time /
close_time, deleted in Task 4) fail loudly on load."
```

---

## Task 2: Migrate `Runner` to use `BacktestSettings`

**Files:**
- Modify: `src/backtest/runner.py:170-210`
- Modify: `src/backtest/reporting.py:50-63`
- Test:   `tests/unit/backtest/test_runner_sigint.py:61` (fixture update)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/backtest/test_settings.py`:

```python
def test_runner_accepts_backtest_settings_instance(tmp_path: Path, monkeypatch) -> None:
    """Runner.__init__ accepts an injected BacktestSettings instance."""
    from backtest.runner import Runner
    from backtest.settings import BacktestSettings

    # Point the runner's CWD-relative paths into a tmp_path sandbox so it
    # does not touch real config files.
    settings = BacktestSettings(
        cache_path                   = str(tmp_path / "store.sqlite"),
        runs_root                    = str(tmp_path / "runs"),
        ticks_per_day                = ["open", "close"],
        failed_tick_abort_ratio      = 0.1,
        fake_broker_starting_cash    = 100_000.0,
        forward_return_horizons_days = [1, 5, 20],
        ohlcv_warmup_days            = 30,
    )

    # Provide minimal windows + watchlist files the constructor still loads.
    windows_path   = tmp_path / "windows.json"
    watchlist_path = tmp_path / "watchlist.json"
    windows_path.write_text(
        '{"smoke": {"start": "2024-01-02", "end": "2024-01-03", "notes": ""}}',
        encoding="utf-8",
    )
    watchlist_path.write_text('{"tickers": ["AAPL"]}', encoding="utf-8")

    runner = Runner(
        settings       = settings,
        windows_path   = windows_path,
        watchlist_path = watchlist_path,
    )
    assert runner._settings is settings
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_settings.py::test_runner_accepts_backtest_settings_instance -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'settings'` (or similar — the constructor currently takes `settings_path`).

- [ ] **Step 3: Update the Runner constructor**

Edit `src/backtest/runner.py`, replacing the `__init__` signature and body at lines 182–192:

```python
    def __init__(
        self,
        *,
        settings:       BacktestSettings | None = None,
        windows_path:   Path                    = Path("config/backtest_windows.json"),
        watchlist_path: Path                    = Path("config/watchlist.json"),
    ) -> None:
        """Load config files; defer actual run setup to ``.run()``.

        Parameters
        ----------
        settings:
            Optional pre-loaded ``BacktestSettings``.  When ``None``, the
            singleton from ``backtest.settings.get_backtest_settings()`` is
            used.  Tests inject a sandboxed instance here.
        windows_path:
            Path to ``config/backtest_windows.json``.
        watchlist_path:
            Path to ``config/watchlist.json``.
        """
        from backtest.settings import get_backtest_settings

        self._settings  = settings if settings is not None else get_backtest_settings()
        self._windows   = load_windows(Path(windows_path))
        self._watchlist = json.loads(Path(watchlist_path).read_text())["tickers"]
```

Also update the `_runs_root_from_config` helper at lines 194–210:

```python
    @staticmethod
    def _runs_root_from_config() -> Path:
        """Return ``runs_root`` from the active backtest settings.

        Convenience helper for scripts that need to locate an existing run
        directory without constructing a full ``Runner`` instance.
        """
        from backtest.settings import get_backtest_settings

        return Path(get_backtest_settings().runs_root)
```

Add the import at the top of the file (after existing imports):

```python
from backtest.settings import BacktestSettings
```

Anywhere the body referenced `self._settings["key"]` (dict access), replace
with `self._settings.key` (attribute access).  Grep `self._settings\[` in
`src/backtest/runner.py` to find every site.

- [ ] **Step 4: Update `reporting.py`**

Edit `src/backtest/reporting.py:50–63`, replacing the `settings` parameter
annotation:

```python
def report(run_dir: Path, settings: "BacktestSettings") -> None:
    """Generate ``report/equity_curve.png`` and ``report/metrics.md``; backfill forwards.

    Reads portfolio snapshots from the run's ``db.sqlite``, writes an equity
    curve PNG and a Markdown metrics file, then walks ``decisions/`` to
    backfill forward returns from the golden cache.

    Parameters
    ----------
    run_dir:
        Root directory for the run (contains ``db.sqlite``, ``decisions/``, etc.).
    settings:
        Validated ``BacktestSettings`` instance.  Required attributes:
        ``cache_path`` and ``forward_return_horizons_days``.
    """
```

Then in the body, replace `settings["cache_path"]` /
`settings["forward_return_horizons_days"]` with attribute access.

Add the import at the top:

```python
from backtest.settings import BacktestSettings
```

- [ ] **Step 5: Run targeted tests to verify**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/ -v`
Expected: All tests pass, including the new `test_runner_accepts_backtest_settings_instance`.

If `tests/unit/backtest/test_runner_sigint.py` still constructs `Runner`
with the old kwargs, update that fixture too (lines around 61):

```python
    from backtest.settings import load_backtest_settings_from
    settings_path = tmp_path / "backtest_settings.json"
    settings_path.write_text(json.dumps({...same payload as before...}))
    settings = load_backtest_settings_from(settings_path)
    runner = Runner(settings=settings, ...)
```

- [ ] **Step 6: Commit**

```bash
git add src/backtest/runner.py src/backtest/reporting.py tests/unit/backtest/test_settings.py tests/unit/backtest/test_runner_sigint.py
git commit -m "refactor(backtest): route Runner/reporting through BacktestSettings

Runner.__init__ now accepts a BacktestSettings instance; defaults to the
get_backtest_settings() singleton when omitted.  reporting.report's
'settings' parameter is typed as BacktestSettings instead of dict.

Test fixtures that constructed Runner with a settings_path or a raw dict
are migrated to the new injection point."
```

---

## Task 3: Migrate the four scripts to `get_backtest_settings()`

**Files:**
- Modify: `scripts/backtest_fetch.py:407` (only the `json.loads` line for now; the `_ANALYST_LOOKBACK_DAYS` dict is Task 10)
- Modify: `scripts/backtest_report.py:44`
- Modify: `scripts/backtest_audit_tick.py:88`
- Modify: `scripts/debug_cache_audit.py:451`

- [ ] **Step 1: Add a smoke test that imports each script**

Add to `tests/unit/backtest/test_settings.py`:

```python
def test_each_script_uses_get_backtest_settings() -> None:
    """Every CLI script consuming backtest_settings.json goes through the loader."""
    import ast
    from pathlib import Path

    targets = [
        "scripts/backtest_fetch.py",
        "scripts/backtest_report.py",
        "scripts/backtest_audit_tick.py",
        "scripts/debug_cache_audit.py",
    ]

    for path_str in targets:
        source = Path(path_str).read_text(encoding="utf-8")
        tree   = ast.parse(source, path_str)

        # Reject any direct json.loads of backtest_settings.json — the
        # loader is the only sanctioned consumer.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "loads"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                # Walk the argument tree for the offending literal.
                src_segment = ast.get_source_segment(source, node) or ""
                assert "backtest_settings.json" not in src_segment, (
                    f"{path_str}: direct json.loads(backtest_settings.json) "
                    "found — use get_backtest_settings() instead."
                )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_settings.py::test_each_script_uses_get_backtest_settings -v`
Expected: FAIL — every script still uses `json.loads(...backtest_settings.json...)`.

- [ ] **Step 3: Migrate `scripts/backtest_fetch.py`**

Replace line 407:

```python
    settings  = json.loads(Path("config/backtest_settings.json").read_text())
```

with:

```python
    from backtest.settings import get_backtest_settings
    settings = get_backtest_settings()
```

Update line 421 (warm-up days):

```python
    # Read warm-up days from settings; the loader's default of 30 covers
    # RSI(14), ATR(14), and pct_change_20d's longest lookback.
    warmup_days: int = settings.ohlcv_warmup_days
```

Update line 423:

```python
    store = CachedDataStore(Path(settings.cache_path))
```

- [ ] **Step 4: Migrate `scripts/backtest_report.py`**

Replace line 44–45:

```python
    settings = json.loads(Path("config/backtest_settings.json").read_text())
    run_dir  = Path(settings["runs_root"]) / args.run_id
```

with:

```python
    from backtest.settings import get_backtest_settings
    settings = get_backtest_settings()
    run_dir  = Path(settings.runs_root) / args.run_id
```

And on line 50:

```python
    report(run_dir, settings)
```

is unchanged — `report()` now accepts the typed instance directly.

- [ ] **Step 5: Migrate `scripts/backtest_audit_tick.py`**

Replace lines 87–89:

```python
    import json
    settings   = json.loads(Path("config/backtest_settings.json").read_text())
    cache_path = Path(settings["cache_path"])
```

with:

```python
    from backtest.settings import get_backtest_settings
    settings   = get_backtest_settings()
    cache_path = Path(settings.cache_path)
```

- [ ] **Step 6: Migrate `scripts/debug_cache_audit.py`**

This script supports a `--config-dir` override, so it cannot use the
singleton blindly.  Replace lines 451–452:

```python
    settings  = _load_json(config_dir / "backtest_settings.json")
    windows   = _load_json(config_dir / "backtest_windows.json")
```

with:

```python
    from backtest.settings import load_backtest_settings_from
    settings  = load_backtest_settings_from(config_dir / "backtest_settings.json")
    windows   = _load_json(config_dir / "backtest_windows.json")
```

And replace every subsequent `settings["cache_path"]` etc. in the function
body with attribute access (`settings.cache_path`).

- [ ] **Step 7: Run the contract test to verify**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_settings.py -v`
Expected: All pass.

Smoke-run each script's `--help` to confirm they still import cleanly:

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_report --help
PYTHONPATH=src .venv/bin/python -m scripts.backtest_audit_tick --help
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --help
PYTHONPATH=src .venv/bin/python -m scripts.debug_cache_audit --help
```

Expected: Each prints argparse help, exit code 0.

- [ ] **Step 8: Commit**

```bash
git add scripts/backtest_fetch.py scripts/backtest_report.py scripts/backtest_audit_tick.py scripts/debug_cache_audit.py tests/unit/backtest/test_settings.py
git commit -m "refactor(scripts): route backtest_settings.json through typed loader

Replaces four parallel json.loads() consumers with get_backtest_settings()
(or load_backtest_settings_from for the script that supports a
--config-dir override).  Contract test asserts no future direct json.loads
of backtest_settings.json sneaks back in."
```

---

## Task 4: Delete redundant schedule keys; rewrite `schedule.py` to read session times from `pandas_market_calendars`

**Rationale.**  Honouring `tz` / `open_time` / `close_time` would silently
break PIT alignment on every early-close NYSE day (the calendar already
knows about them; a config key cannot).  Delete the redundant keys and
let the calendar own session times.  Only `ticks_per_day` remains as a
policy knob.

**Files:**
- Modify: `config/backtest_settings.json`
- Modify: `src/backtest/schedule.py` (full rewrite)
- Create: `tests/contract/__init__.py` (if missing)
- Create: `tests/contract/test_schedule_sourced_from_config.py`

- [ ] **Step 1: Ensure `tests/contract/` exists**

```bash
mkdir -p tests/contract
touch tests/contract/__init__.py
```

- [ ] **Step 2: Write the failing contract test**

Create `tests/contract/test_schedule_sourced_from_config.py`:

```python
"""Contract test: generate_ticks reads session times from pandas_market_calendars.

Two properties under test:

1. The early-close NYSE day 2024-11-29 (day after Thanksgiving) yields a
   close tick at 13:00 ET, not 16:00 ET — proving the calendar is the
   source of truth, not any module-level constant.
2. Flipping settings.ticks_per_day from ["open", "close"] to just ["open"]
   halves the tick count for any given range — proving ticks_per_day is
   the only schedule knob that flows through.

A third test asserts unsupported phase values raise ValueError so typos
fail loudly.
"""
from __future__ import annotations

from datetime import date

from backtest.settings import BacktestSettings


def _make_settings(*, ticks_per_day: list[str]) -> BacktestSettings:
    """Build a sandboxed BacktestSettings for a single test."""
    return BacktestSettings(
        cache_path                   = "x",
        runs_root                    = "y",
        ticks_per_day                = ticks_per_day,
        failed_tick_abort_ratio      = 0.1,
        fake_broker_starting_cash    = 100.0,
        forward_return_horizons_days = [1],
        ohlcv_warmup_days            = 30,
    )


def test_early_close_day_yields_thirteen_hundred_close(monkeypatch) -> None:
    """Day-after-Thanksgiving NYSE close is 13:00 ET — proves calendar is the source of truth."""
    from backtest import schedule, settings as bs_mod

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["open", "close"]))

    # 2024-11-29 is the Friday after Thanksgiving — NYSE early close 13:00 ET.
    ticks = schedule.generate_ticks(date(2024, 11, 29), date(2024, 11, 29))
    close_tick = next(t for t in ticks if t.phase == "close")

    # tz-aware: convert to NY local to assert the wall-clock hour.
    from zoneinfo import ZoneInfo
    ny_close = close_tick.as_of.astimezone(ZoneInfo("America/New_York"))
    assert ny_close.hour   == 13, f"expected 13:00 ET early close, got {ny_close.hour:02d}:{ny_close.minute:02d}"
    assert ny_close.minute == 0


def test_ticks_per_day_open_only_halves_tick_count(monkeypatch) -> None:
    """Flipping ticks_per_day to just ['open'] halves the count vs ['open','close']."""
    from backtest import schedule, settings as bs_mod

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["open", "close"]))
    full = schedule.generate_ticks(date(2024, 1, 2), date(2024, 1, 12))

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["open"]))
    open_only = schedule.generate_ticks(date(2024, 1, 2), date(2024, 1, 12))

    assert len(open_only) * 2 == len(full)
    assert all(t.phase == "open" for t in open_only)


def test_unsupported_phase_raises(monkeypatch) -> None:
    """A typo or unimplemented phase in ticks_per_day raises ValueError."""
    import pytest

    from backtest import schedule, settings as bs_mod

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["opening", "close"]))

    with pytest.raises(ValueError, match="unsupported ticks_per_day"):
        schedule.generate_ticks(date(2024, 1, 2), date(2024, 1, 2))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_schedule_sourced_from_config.py -v`
Expected: 3 failures — the current `generate_ticks` hard-codes `16:00` so
the early-close test fails with `expected 13:00 ET early close, got 16:00`.

- [ ] **Step 4: Delete the redundant keys from `config/backtest_settings.json`**

Edit `config/backtest_settings.json`.  Remove `tz`, `open_time`,
`close_time`.  The file should become:

```json
{
  "cache_path":            "backtests/cache/store.sqlite",
  "runs_root":             "backtests/runs",
  "ticks_per_day":         ["open", "close"],
  "failed_tick_abort_ratio": 0.10,
  "fake_broker_starting_cash": 100000.0,
  "forward_return_horizons_days": [1, 5, 20],
  "ohlcv_warmup_days": 30
}
```

Verify the loader still accepts it:

```bash
PYTHONPATH=src .venv/bin/python -c "from backtest.settings import get_backtest_settings, _reset_cache; _reset_cache(); print(get_backtest_settings())"
```

Expected: no exception; prints a `BacktestSettings(...)` repr without
`tz` / `open_time` / `close_time`.

- [ ] **Step 5: Rewrite `src/backtest/schedule.py`**

Replace the file's contents with:

```python
"""Tick-schedule generator — driven by pandas_market_calendars.

Yields ``Tick(as_of, phase)`` pairs over NYSE business days in a date
range.  For each session the configured ``ticks_per_day`` policy decides
whether to emit the open tick, the close tick, or both.

**Session times come from the calendar, not from config.**
``pandas_market_calendars`` already owns NYSE session times — including
early-close days (day-after-Thanksgiving 13:00 ET, Christmas Eve 13:00
ET, etc.).  Letting a user override ``open_time`` or ``close_time`` via
config would silently break PIT alignment on those days, so the keys are
absent from ``BacktestSettings`` by design.

Calendar choice (``NYSE``) is hardcoded for the same reason multi-calendar
support is out of scope: every consumer of ``pandas_market_calendars`` in
the harness would otherwise need a calendar identifier plumbed through,
and there is no plausible non-NYSE use case before live deploy.  The only
configurable schedule surface is ``ticks_per_day`` (which subset of
``{"open", "close"}`` to emit).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import pandas_market_calendars as mcal

from backtest.settings import get_backtest_settings

Phase = Literal["open", "close"]

# NYSE calendar — calendar choice is hardcoded by design (see module
# docstring).  Cached at import time because the calendar object is
# stateless and stable for the process lifetime.
_NYSE = mcal.get_calendar("NYSE")

# Phases this generator knows how to emit.  Any deviation in
# settings.ticks_per_day raises at run time rather than silently emitting
# the wrong cadence.
_SUPPORTED_PHASES: frozenset[str] = frozenset({"open", "close"})


@dataclass(frozen=True)
class Tick:
    """One scheduled tick — timezone-aware ``as_of`` plus phase tag."""

    as_of: datetime
    phase: Phase


def generate_ticks(start: date, end: date) -> list[Tick]:
    """Return ticks for every NYSE session in ``[start, end]``.

    Session times come from ``pandas_market_calendars.schedule()``, which
    handles holidays and early-close days correctly by construction.
    The configured ``ticks_per_day`` (a subset of ``{"open", "close"}``)
    decides which phases to emit per session.

    Parameters
    ----------
    start, end:
        Inclusive date range.

    Returns
    -------
    list[Tick]
        Ticks in chronological order.

    Raises
    ------
    ValueError
        If ``settings.ticks_per_day`` contains any value outside
        ``{"open", "close"}``.
    """
    settings = get_backtest_settings()

    # Validate supported phases up front — fail loudly rather than emit a
    # cadence the rest of the harness does not understand.  Set difference
    # (rather than equality) lets us accept ["open"] or ["close"] alone.
    requested_phases = set(settings.ticks_per_day)
    if not requested_phases.issubset(_SUPPORTED_PHASES):
        raise ValueError(
            f"unsupported ticks_per_day={settings.ticks_per_day!r}; "
            f"supported phases are {sorted(_SUPPORTED_PHASES)!r}."
        )

    # schedule() returns a DataFrame indexed by date with 'market_open'
    # and 'market_close' columns of tz-aware pandas Timestamps.  For
    # early-close days, 'market_close' is set to that day's actual close
    # time (e.g. 13:00 ET on day-after-Thanksgiving).
    sched = _NYSE.schedule(start_date=start, end_date=end)

    ticks: list[Tick] = []
    for _, row in sched.iterrows():
        if "open" in requested_phases:
            ticks.append(Tick(row["market_open"].to_pydatetime(), "open"))
        if "close" in requested_phases:
            ticks.append(Tick(row["market_close"].to_pydatetime(), "close"))

    ticks.sort(key=lambda t: t.as_of)
    return ticks
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_schedule_sourced_from_config.py -v`
Expected: 3 passed.

Run the wider schedule-touching suite to catch regressions:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "schedule or generate_ticks" -v
```

Expected: All tests pass.  If anything else in the suite reads
`_OPEN_TIME` / `_CLOSE_TIME` / `_NY` it will surface here; replace those
references with the new public API (just `generate_ticks` and `Tick`).

- [ ] **Step 7: Commit**

```bash
git add config/backtest_settings.json src/backtest/schedule.py tests/contract/__init__.py tests/contract/test_schedule_sourced_from_config.py
git commit -m "refactor(backtest): delete tz/open_time/close_time; let calendar own session times

The honoured-keys approach would silently break PIT alignment on every
early-close NYSE day — pandas_market_calendars already owns the correct
session times.  Deletes the redundant keys from
config/backtest_settings.json (BacktestSettings.extra='forbid' so any
stale copy fails loudly) and rewrites src/backtest/schedule.py to call
_NYSE.schedule() per session.

ticks_per_day stays as a real policy knob (open vs close vs both);
unsupported phase sets raise ValueError explicitly.  Contract test
asserts the day-after-Thanksgiving close tick is 13:00 ET, proving the
calendar is the source of truth."
```

---

## Task 5: Promote `earnings_lookback_quarters` and `short_interest_lookback_days` to `FetchDefaults`

**Files:**
- Modify: `src/data/config.py:34-43`
- Test:   `tests/unit/data/test_config.py` (add assertions)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/data/test_config.py`:

```python
def test_fetch_defaults_includes_earnings_and_short_interest(tmp_path) -> None:
    """FetchDefaults exposes earnings_lookback_quarters and short_interest_lookback_days."""
    from data.config import FetchDefaults, load_config_from

    payload = {
        "providers": {
            "price_history": "yfinance",
            "company_ratios": "pit_composite",
            "news": "alpha_vantage",
            "social_sentiment": "finnhub",
            "insider_trades": "edgar",
            "politician_trades": "fmp",
            "notable_holders": "edgar",
            "filings": "edgar",
            "earnings": "finnhub",
            "analyst_consensus": "yfinance",
            "short_interest": "finra",
            "options": "yfinance",
        },
        "defaults": {
            "news_lookback_days":            7,
            "insider_lookback_days":         30,
            "politician_lookback_days":      90,
            "notable_holder_lookback_days":  180,
            "notable_holder_limit":          20,
            "history_period":                "1y",
            "history_interval":              "1d",
            "filings_per_form":              3,
            "include_filing_excerpts":       True,
            "earnings_lookback_quarters":    4,
            "short_interest_lookback_days":  90,
        },
        "http_timeout_seconds": 15.0,
    }
    path = tmp_path / "data.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    cfg = load_config_from(path)
    assert isinstance(cfg.defaults, FetchDefaults)
    assert cfg.defaults.earnings_lookback_quarters    == 4
    assert cfg.defaults.short_interest_lookback_days  == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_config.py -v`
Expected: FAIL on `AssertionError` or Pydantic rejecting an unknown field.

- [ ] **Step 3: Extend `FetchDefaults`**

Edit `src/data/config.py:34-43`:

```python
class FetchDefaults(BaseModel):
    """Per-domain fetch defaults shared by every provider in this project.

    Every field mirrors a key in ``config/data.json:defaults``.  Adding a
    field here without also adding the key to ``data.json`` is fine — the
    default takes over — but removing a field while the JSON still declares
    it will raise a Pydantic validation error at boot.
    """

    news_lookback_days:           int  = 7
    insider_lookback_days:        int  = 30
    politician_lookback_days:     int  = 90
    notable_holder_lookback_days: int  = 180
    notable_holder_limit:         int  = 20
    history_period:               str  = "1y"
    history_interval:             str  = "1d"
    filings_per_form:             int  = 3
    include_filing_excerpts:      bool = True
    earnings_lookback_quarters:   int  = 4
    short_interest_lookback_days: int  = 90
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_config.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/data/config.py tests/unit/data/test_config.py
git commit -m "feat(data): promote earnings and short_interest lookback defaults

config/data.json already declared earnings_lookback_quarters and
short_interest_lookback_days, but FetchDefaults dropped them on load.
Adding them to the Pydantic model so get_config().defaults exposes
every key the JSON file lists."
```

---

## Task 6: Land the cross-cutting lookback contract test (xfail-staged)

**Rationale.**  TDD: the test predates the code that satisfies it.  We
write the contract test for both analyst sites now, marked with
`@pytest.mark.xfail(strict=True, reason="awaiting Task N migration")` so
the default pytest run stays green.  Tasks 7 and 8 each remove one xfail
marker after demonstrating their migration makes the assertion pass.

The aggregator (`get_stock_signal_bundle`) is deliberately not tested —
Phase 7.6 deletes it entirely.  Adding a contract row here would
churn (land, then immediately delete).

**Files:**
- Create: `tests/contract/test_lookbacks_sourced_from_config.py`

- [ ] **Step 1: Write the contract test (both xfail-marked)**

Create `tests/contract/test_lookbacks_sourced_from_config.py`:

```python
"""Contract test: every analyst lookback comes from get_config().defaults.

Patches the data-config singleton with sentinel lookback values and asserts
both analyst fetch callbacks propagate them to the provider layer.
Catches any regression where a module re-introduces a hardcoded constant
or a literal default.

Currently two tests are xfail-marked.  Tasks 7 and 8 each remove one:

- Task 7 removes the smart_money xfail (after migrating that module).
- Task 8 removes the fundamental xfail (after migrating that module).

The aggregator (get_stock_signal_bundle) is deliberately not tested
here — Phase 7.6 deletes the function entirely.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data.config import DataConfig, FetchDefaults


# Sentinel values chosen to be:
#   - distinct from every plausible production value (so a stale literal
#     fails loudly rather than coincidentally matching),
#   - small enough to satisfy any non-negative-int validator the providers
#     may grow later.
SENTINEL_NEWS              = 991
SENTINEL_INSIDER           = 993
SENTINEL_POLITICIAN        = 995
SENTINEL_NOTABLE_HOLDER    = 997


def _sentinel_config() -> DataConfig:
    """Build a DataConfig whose lookback fields are unique sentinels."""
    return DataConfig(
        providers={
            "price_history":      "yfinance",
            "company_ratios":     "pit_composite",
            "news":               "alpha_vantage",
            "social_sentiment":   "finnhub",
            "insider_trades":     "edgar",
            "politician_trades":  "fmp",
            "notable_holders":    "edgar",
            "filings":            "edgar",
            "earnings":           "finnhub",
            "analyst_consensus":  "yfinance",
            "short_interest":     "finra",
            "options":            "yfinance",
        },
        defaults=FetchDefaults(
            news_lookback_days           = SENTINEL_NEWS,
            insider_lookback_days        = SENTINEL_INSIDER,
            politician_lookback_days     = SENTINEL_POLITICIAN,
            notable_holder_lookback_days = SENTINEL_NOTABLE_HOLDER,
            notable_holder_limit         = 20,
            history_period               = "1y",
            history_interval             = "1d",
            filings_per_form             = 3,
            include_filing_excerpts      = True,
            earnings_lookback_quarters   = 4,
            short_interest_lookback_days = 90,
        ),
        # Task 12 renames this field to ``quiver_http_timeout_seconds``;
        # update this line in lockstep with the Task 12 edits.  The value
        # is incidental — these tests only exercise the lookback path.
        http_timeout_seconds = 15.0,
    )


@pytest.mark.xfail(strict=True, reason="awaiting Task 7 (smart_money) migration")
@pytest.mark.asyncio
async def test_smart_money_fetch_uses_config_lookbacks(monkeypatch) -> None:
    """smart_money_fetch_callback forwards config sentinels to its providers."""
    from agents.analysts.smart_money import fetch as smart_money_fetch
    from data import config as data_config_mod

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, int] = {}

    async def fake_politicians(ticker, *, lookback_days, as_of):
        captured["politician"] = lookback_days
        return []

    async def fake_holders(ticker, *, lookback_days, as_of):
        captured["holder"] = lookback_days
        return []

    monkeypatch.setattr(smart_money_fetch, "get_public_figure_trades", fake_politicians)
    monkeypatch.setattr(smart_money_fetch, "get_notable_holders",      fake_holders)

    class FakeCtx:
        state = {"tickers": ["AAPL"], "as_of": datetime.now(timezone.utc)}

    await smart_money_fetch.smart_money_fetch_callback(FakeCtx())

    assert captured["politician"] == SENTINEL_POLITICIAN
    assert captured["holder"]     == SENTINEL_NOTABLE_HOLDER


@pytest.mark.xfail(strict=True, reason="awaiting Task 8 (fundamental) migration")
@pytest.mark.asyncio
async def test_fundamental_fetch_uses_config_insider_lookback(monkeypatch) -> None:
    """fundamental_fetch_callback forwards the config insider sentinel."""
    from agents.analysts.fundamental import fetch as fundamental_fetch
    from data import config as data_config_mod
    from data.models import Form4Bundle

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, int] = {}

    async def fake_insider(ticker, *, lookback_days, as_of):
        captured["insider"] = lookback_days
        return Form4Bundle(trades=[], derivatives=[])

    async def fake_ratios(ticker, *, as_of):
        return None

    async def fake_filings(ticker, *, as_of):
        return []

    monkeypatch.setattr(fundamental_fetch, "get_insider_trades",  fake_insider)
    monkeypatch.setattr(fundamental_fetch, "get_company_ratios",  fake_ratios)
    monkeypatch.setattr(fundamental_fetch, "get_company_filings", fake_filings)

    class FakeCtx:
        state = {"tickers": ["AAPL"], "as_of": datetime.now(timezone.utc)}

    await fundamental_fetch.fundamental_fetch_callback(FakeCtx())

    assert captured["insider"] == SENTINEL_INSIDER
```

> **Cross-reference.**  The sentinel config helper above uses the
> still-current field name `http_timeout_seconds`.  Task 12 renames the
> field on `DataConfig` to `quiver_http_timeout_seconds`; its Step 5
> includes a one-line edit to this file to match.  No re-ordering needed.

- [ ] **Step 2: Run the contract test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_lookbacks_sourced_from_config.py -v`
Expected: `2 xfailed`.  Both are expected failures because no
migration has run yet — the analyst modules still hardcode lookbacks.

If pytest reports `xpassed` instead of `xfailed`, the test isn't reaching
the assertion (most likely the monkey-patch didn't bind correctly) — debug
before committing.

- [ ] **Step 3: Sanity-check the test reaches the assertion**

Temporarily change one xfail-marked test's assertion (e.g. flip
`SENTINEL_POLITICIAN` to `30`) and rerun.  Expected: `xpassed` (strict
mode fails — `assert 30 == 30` succeeds, so xfail "fails"), proving the
test does reach the assertion.  Revert the change.

- [ ] **Step 4: Commit**

```bash
git add tests/contract/test_lookbacks_sourced_from_config.py
git commit -m "test(contract): land lookback contract tests (xfail-staged)

Two behavioural tests assert smart_money and fundamental analyst fetch
callbacks forward config sentinels to the provider layer.  Both are
marked xfail(strict=True) at landing — the migrations in Tasks 7 and 8
each remove one marker after demonstrating their site passes.

The aggregator (get_stock_signal_bundle) is deliberately excluded —
Phase 7.6 deletes it entirely; adding a contract row here would
land-then-delete churn.

TDD ordering: the test predates the code that satisfies it.  Strict
xfail means an unexpected pass surfaces immediately."
```

---

## Task 7: Route `smart_money/fetch.py` through `get_config()`

**Files:**
- Modify: `src/agents/analysts/smart_money/fetch.py:38-39,89,97`
- Modify: `tests/contract/test_lookbacks_sourced_from_config.py` (remove one xfail marker)

- [ ] **Step 1: Read the current module**

Reference: `src/agents/analysts/smart_money/fetch.py`.  The current
constants at lines 38–39 are:

```python
POLITICIAN_LOOKBACK_DAYS = 30
HOLDER_LOOKBACK_DAYS     = 90
```

These disagree with `config/data.json` (which says 90 / 180).  We adopt
the config values per spec decision D1.

- [ ] **Step 2: Migrate the fetch callback**

Edit `src/agents/analysts/smart_money/fetch.py`.  Remove the two
module-level constants at lines 38–39, and update the two `get_*` calls
at lines 88–98:

```python
async def smart_money_fetch_callback(
    callback_context: CallbackContext,
) -> None:
    """Fetch smart-money data and write it to state; always returns None.

    ...
    """
    from data.config import get_config

    state    = callback_context.state
    tickers  = state.get("tickers", [])

    # Pull the historical clock from session state; default to wall-clock for live.
    as_of: datetime = resolve_as_of(
        state.get("as_of"), allow_wallclock=True, site="smart_money/fetch",
    )

    # Source lookback windows from config — Phase 7.5 makes config/data.json
    # the single source of truth for these values.  Reading inside the
    # callback rather than at module load keeps the import cheap and lets
    # tests monkey-patch the config singleton.
    defaults = get_config().defaults
    politician_lookback_days = defaults.politician_lookback_days
    holder_lookback_days     = defaults.notable_holder_lookback_days

    smart_money_data: dict = {
        "politicians":     {},
        "notable_holders": {},
    }

    for ticker in tickers:
        try:
            politicians = await get_public_figure_trades(
                ticker, lookback_days=politician_lookback_days, as_of=as_of
            )
        except Exception as exc:
            logger.warning("politician_trades fetch failed for %s: %s", ticker, exc)
            politicians = []

        try:
            holders = await get_notable_holders(
                ticker, lookback_days=holder_lookback_days, as_of=as_of
            )
        except Exception as exc:
            logger.warning("notable_holders fetch failed for %s: %s", ticker, exc)
            holders = []

        smart_money_data["politicians"][ticker] = [
            t.model_dump() if hasattr(t, "model_dump") else t for t in politicians
        ]
        smart_money_data["notable_holders"][ticker] = [
            h.model_dump() if hasattr(h, "model_dump") else h for h in holders
        ]

    state["smart_money_data"] = smart_money_data
    _trace_maybe(state, "01_fetch_smart_money", smart_money_data)
    return None
```

- [ ] **Step 3: Run tests to verify**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "smart_money" -v`
Expected: All tests pass (no test currently asserts the literal 30/90).

- [ ] **Step 4: Remove the smart_money xfail marker from the contract test**

Edit `tests/contract/test_lookbacks_sourced_from_config.py`.  Find:

```python
@pytest.mark.xfail(strict=True, reason="awaiting Task 7 (smart_money) migration")
@pytest.mark.asyncio
async def test_smart_money_fetch_uses_config_lookbacks(monkeypatch) -> None:
```

Remove the `@pytest.mark.xfail(...)` line.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_lookbacks_sourced_from_config.py -v`
Expected: 1 passed (smart_money), 1 xfailed (fundamental — awaiting Task 8).

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/smart_money/fetch.py tests/contract/test_lookbacks_sourced_from_config.py
git commit -m "refactor(smart_money): read lookbacks from config/data.json

Removes POLITICIAN_LOOKBACK_DAYS (30) and HOLDER_LOOKBACK_DAYS (90) — they
disagreed with config/data.json (90 / 180).  Config wins per spec D1;
the higher values match the Ziobrowski et al. and 13F-cadence literature.

Drops the matching xfail marker on the smart_money contract test."
```

---

## Task 8: Route `fundamental/fetch.py` through `get_config()`

**Files:**
- Modify: `src/agents/analysts/fundamental/fetch.py:53,274`
- Modify: `tests/contract/test_lookbacks_sourced_from_config.py` (remove one xfail marker)

- [ ] **Step 1: Migrate the fetch callback**

Edit `src/agents/analysts/fundamental/fetch.py`.  Remove the module-level
constant at line 53:

```python
# (delete: _INSIDER_LOOKBACK_DAYS = 30)
```

And update the call at lines 272–275:

```python
        # --- insider trades (Form 4) ---
        try:
            # Source lookback from config — config/data.json owns the value
            # so all three call sites (analysts, cache providers, fetcher)
            # agree.
            from data.config import get_config
            insider_lookback_days = get_config().defaults.insider_lookback_days

            insider_bundle = await get_insider_trades(
                ticker, lookback_days=insider_lookback_days, as_of=as_of
            )
```

The `from data.config import get_config` line can be hoisted to the
module's import block if preferred; the local import is fine because
`get_config()` is `lru_cache`'d and the import itself is cheap.  Pick
whichever the team prefers — the test does not care.

- [ ] **Step 2: Run tests to verify**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "fundamental" -v`
Expected: All tests pass.

- [ ] **Step 3: Remove the fundamental xfail marker from the contract test**

Edit `tests/contract/test_lookbacks_sourced_from_config.py`.  Remove the
`@pytest.mark.xfail(...)` decorator above
`test_fundamental_fetch_uses_config_insider_lookback`.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_lookbacks_sourced_from_config.py -v`
Expected: 2 passed (full contract test green — both analyst sites now route through config).

- [ ] **Step 4: Commit**

```bash
git add src/agents/analysts/fundamental/fetch.py tests/contract/test_lookbacks_sourced_from_config.py
git commit -m "refactor(fundamental): read insider_lookback_days from config

Removes _INSIDER_LOOKBACK_DAYS module constant.  Live value was already
30 in config; this just kills the parallel definition.

Drops the matching xfail marker on the fundamental contract test."
```

---

> **Task 9 deliberately omitted.**  The previous draft of this plan
> included a Task 9 that migrated `aggregator.get_stock_signal_bundle`
> to source its kwarg defaults from `get_config()`.  Phase 7.6
> (data-shape contracts) deletes the aggregator outright — migrating it
> in 7.5 only to delete it in 7.6 is pure churn.  Task numbering
> resumes at 10 for downstream cross-reference stability.

---

## Task 10: Drop `lookback_days` defaults from cache providers

**Files:**
- Modify: `src/backtest/providers/notable_holders_cache.py:27`
- Modify: `src/backtest/providers/politician_trades_cache.py:29`
- Modify: `src/backtest/providers/insider_trades_cache.py:35`
- Modify: `src/backtest/providers/news_cache.py:21`
- Modify: `src/backtest/providers/filings_cache.py:28`

- [ ] **Step 1: Make `lookback_days` required in each cache provider**

For each of the five files above, change the `fetch` signature so
`lookback_days` is required (no default).  Example for
`src/backtest/providers/notable_holders_cache.py`:

```python
@register(
    "notable_holders", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int,   # was: int = 365 — defaults now flow from get_config()
    **_unused,
) -> list[NotableHolder]:
```

Repeat for the four siblings (`politician_trades_cache.py`,
`insider_trades_cache.py`, `news_cache.py`, `filings_cache.py`).

- [ ] **Step 2: Audit callers**

Run:

```bash
grep -rn "dispatch(\"notable_holders\"\|dispatch(\"politician_trades\"\|dispatch(\"insider_trades\"\|dispatch(\"news\"\|dispatch(\"filings\"" src/ scripts/ tests/
```

Every caller must pass `lookback_days=` explicitly.  Both analyst fetch
callbacks (Tasks 7 and 8) already do; the fetcher's mirror dict is
replaced in Task 11.  The aggregator (`get_stock_signal_bundle`) is
excluded — Phase 7.6 deletes it outright, so it does not need an
explicit `lookback_days` argument before that deletion.  Tests that
bypass the analysts and call the cache providers directly need to pass
`lookback_days` — update them.

- [ ] **Step 3: Run the cache-provider-touching suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/providers/ tests/integration/backtest/ -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/backtest/providers/
git commit -m "refactor(backtest providers): drop lookback_days defaults

The five cache providers had divergent defaults (365 / 90 / 90 / 30 /
365) that contradicted config/data.json's declared values.  Making the
kwarg required forces callers to pass the right number — which now flows
from get_config().defaults in the analyst paths.  (The aggregator is
excluded — Phase 7.6 deletes it entirely.)"
```

---

## Task 11: Delete `_ANALYST_LOOKBACK_DAYS` in `scripts/backtest_fetch.py`

**Files:**
- Modify: `scripts/backtest_fetch.py:83-91,213,244,260`

- [ ] **Step 1: Replace the dict with config reads**

Edit `scripts/backtest_fetch.py`.  Delete the block at lines 67–91 (the
docstring banner plus the `_ANALYST_LOOKBACK_DAYS` dict).  Anywhere the
dict was indexed, replace with a `get_config().defaults` read.

Specifically, the three sites listed by grep:

Line 213 (in `_news` fetch coverage maths):

```python
        # Pre-window buffer matches news_lookback_days so the first replay
        # tick still has a full window of articles.
        from data.config import get_config
        pre_window_buffer = timedelta(days=get_config().defaults.news_lookback_days)
```

Line 244 (in `_insider_trades` fetcher):

```python
        from data.config import get_config
        lookback = (end - start).days + get_config().defaults.insider_lookback_days
        # ...
```

Line 260 (in `_politician_trades` fetcher):

```python
        from data.config import get_config
        lookback = (end - start).days + get_config().defaults.politician_lookback_days
        # ...
```

If multiple sites in the same function use `get_config()`, hoist the call:

```python
def _fill_insider_trades(...):
    from data.config import get_config
    defaults = get_config().defaults
    lookback = (end - start).days + defaults.insider_lookback_days
    ...
```

- [ ] **Step 2: Run the fetch script's import-time smoke check**

Run: `PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --help`
Expected: Argparse help printed, exit code 0.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "backtest_fetch or fetcher" -v`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add scripts/backtest_fetch.py
git commit -m "chore(scripts): retire backtest_fetch's _ANALYST_LOOKBACK_DAYS mirror

The mirror dict was a tactical fix for the SVB-2023 start-of-window
coverage gap.  Now that both analyst sites source from
config/data.json, the fetcher reads the same place — one declared
source of truth for every consumer."
```

---

## Task 12: Rename `http_timeout_seconds` → `quiver_http_timeout_seconds` and route `quiver.py` through `get_config()`

**Rationale for rename:**  The current key sounds project-wide ("HTTP timeout
for any provider that uses HTTP") but is in fact only consumed by the Quiver
politician-trades provider.  Renaming it makes the scope honest and lets a
future maintainer add `<provider>_http_timeout_seconds` keys without
breaking the existing one.  This is the D5 amendment from the spec.

**Files:**
- Modify: `config/data.json` (rename key)
- Modify: `src/data/config.py` (rename `DataConfig` field)
- Modify: `src/data/providers/politician_trades/quiver.py:18,88` (delete
  `_HTTP_TIMEOUT`, route through `get_config()`)
- Modify: `tests/contract/test_lookbacks_sourced_from_config.py` (sentinel
  helper — one-line field rename in `_sentinel_config()` so the Task 6 test
  still constructs `DataConfig` correctly after the field rename)
- Create: `tests/contract/test_http_timeout_sourced_from_config.py`

- [ ] **Step 1: Write the failing contract test**

Create `tests/contract/test_http_timeout_sourced_from_config.py`.  Note
this test uses the **new** field name `quiver_http_timeout_seconds` — it
will fail twice before passing: once because the field does not exist yet
on `DataConfig`, then again (after Step 3) because the literal in
`quiver.py` still wins.

```python
"""Contract test: the Quiver provider uses get_config().quiver_http_timeout_seconds.

Patches the data-config singleton with a sentinel timeout and asserts
quiver._fetch_trades calls requests.get with that exact timeout.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from data.config import DataConfig, FetchDefaults


SENTINEL_TIMEOUT = 42.0


def _sentinel_config() -> DataConfig:
    """Build a DataConfig whose quiver timeout is a unique sentinel."""
    return DataConfig(
        providers={
            "price_history":      "yfinance",
            "company_ratios":     "pit_composite",
            "news":               "alpha_vantage",
            "social_sentiment":   "finnhub",
            "insider_trades":     "edgar",
            "politician_trades":  "fmp",
            "notable_holders":    "edgar",
            "filings":            "edgar",
            "earnings":           "finnhub",
            "analyst_consensus":  "yfinance",
            "short_interest":     "finra",
            "options":            "yfinance",
        },
        defaults                    = FetchDefaults(),
        quiver_http_timeout_seconds = SENTINEL_TIMEOUT,
    )


def test_quiver_uses_config_http_timeout(monkeypatch) -> None:
    """quiver._fetch_trades passes get_config().quiver_http_timeout_seconds to requests.get."""
    from data import config as data_config_mod
    from data.providers.politician_trades import quiver

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    fake_response = MagicMock()
    fake_response.content = b"[]"
    fake_response.json.return_value = []

    with patch("data.providers.politician_trades.quiver.requests.get",
               return_value=fake_response) as fake_get:
        quiver._fetch_trades("AAPL", api_key="fake")

    fake_get.assert_called_once()
    _, kwargs = fake_get.call_args
    assert kwargs["timeout"] == SENTINEL_TIMEOUT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_http_timeout_sourced_from_config.py -v`
Expected: FAIL — `pydantic.ValidationError: Extra inputs are not permitted
[type=extra_forbidden, input=quiver_http_timeout_seconds, ...]` because
the field has not been renamed yet on `DataConfig`.

- [ ] **Step 3: Rename the field on `DataConfig`**

Edit `src/data/config.py`.  Rename the field on the `DataConfig` class:

```python
class DataConfig(BaseModel):
    """Top-level data-layer configuration loaded from config/data.json."""
    model_config = ConfigDict(extra="forbid")

    providers:                   dict[str, str]
    defaults:                    FetchDefaults
    quiver_http_timeout_seconds: float = Field(gt=0.0)
```

(was: `http_timeout_seconds: float = Field(gt=0.0)` — rename the attribute,
keep the validator unchanged.)

- [ ] **Step 4: Rename the key in `config/data.json`**

Edit `config/data.json`.  Change the top-level key:

```json
{
  "providers": { ... },
  "defaults":  { ... },
  "quiver_http_timeout_seconds": 15.0
}
```

(was: `"http_timeout_seconds": 15.0`.)

- [ ] **Step 5: Update the Task 6 sentinel helper for the rename**

Edit `tests/contract/test_lookbacks_sourced_from_config.py`.  In
`_sentinel_config()`, rename the keyword argument:

```python
        ...
        quiver_http_timeout_seconds = 15.0,
    )
```

(was: `http_timeout_seconds = 15.0,` — single-line change.  The Task 6
test does not assert on the timeout; the value is set only to satisfy
`DataConfig`'s required field.)

Run the lookback contract test to confirm Task 6 still passes after the
rename:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_lookbacks_sourced_from_config.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Migrate `quiver.py`**

Edit `src/data/providers/politician_trades/quiver.py`.  Remove the
module-level constant at line 18 (`_HTTP_TIMEOUT = 15.0`) and update
`_fetch_trades` at line 88:

```python
@with_retry
def _fetch_trades(symbol: str | None, api_key: str) -> list[dict]:
    """Fetch raw congressional-trade rows from Quiver Quant's API.

    ...
    """
    # Source the HTTP timeout from config at call time so changes to
    # config/data.json take effect on the next call without reload tricks.
    from data.config import get_config
    timeout = get_config().quiver_http_timeout_seconds

    url     = f"{_BASE_URL}/live/congresstrading"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    params: dict[str, Any] = {}
    if symbol:
        params["ticker"] = symbol

    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []
```

- [ ] **Step 7: Run the contract test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_http_timeout_sourced_from_config.py -v`
Expected: PASS.

Also run the wider quiver suite:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "quiver or politician" -v
```

Expected: All tests pass.

- [ ] **Step 8: Search for stale references to the old key**

```bash
grep -rn "http_timeout_seconds" src/ tests/ config/ scripts/
```

Expected: every match is the renamed `quiver_http_timeout_seconds`.  If
any bare `http_timeout_seconds` remains, fix it before committing.

- [ ] **Step 9: Commit**

```bash
git add config/data.json src/data/config.py src/data/providers/politician_trades/quiver.py tests/contract/test_http_timeout_sourced_from_config.py tests/contract/test_lookbacks_sourced_from_config.py
git commit -m "refactor(config): rename http_timeout_seconds → quiver_http_timeout_seconds and route quiver through config

The key was named as if it were a project-wide HTTP timeout, but in
practice the only consumer is the Quiver politician-trades provider.
Rename to match the actual scope, and remove the duplicate
_HTTP_TIMEOUT module constant in quiver.py — get_config() is now the
single source of truth.  Contract test asserts the timeout flows from
get_config() through to requests.get; the Task 6 lookback contract
test's sentinel helper picks up the new field name in the same commit."
```

---

## Task 13: Update `config/README.md`

**Files:**
- Modify: `config/README.md`

This task reconciles the README with all the schema and key changes made
in Tasks 1–12.  Four edits in one commit.

- [ ] **Step 1: Drop "(planned)" from the loader row**

Edit `config/README.md` line 15.  Change:

```markdown
| `backtest_settings.json` | Cache path, run root, tick schedule, and lookback defaults for backtesting | `src/backtest/settings.py` (planned) |
```

to:

```markdown
| `backtest_settings.json` | Cache path, run root, tick schedule, and lookback defaults for backtesting | `src/backtest/settings.py` (`get_backtest_settings()`) |
```

- [ ] **Step 2: Rename the HTTP-timeout row in the `data.json` section**

Edit `config/README.md`.  In the `data.json` settings table (around
line 53), change:

```markdown
| `http_timeout_seconds` | float | Shared HTTP timeout applied to provider clients. |
```

to:

```markdown
| `quiver_http_timeout_seconds` | float | HTTP timeout (seconds) applied to the Quiver politician-trades provider.  Other providers manage their own timeouts; this key is scoped accordingly. |
```

- [ ] **Step 3: Delete the schedule rows in the `backtest_settings.json` section**

Edit `config/README.md`.  In the `backtest_settings.json` settings table
(around lines 350–352), delete these three rows in full:

```markdown
| `tz` | string | IANA timezone for all tick timestamps (must be `"America/New_York"`). |
| `open_time` | string | `HH:MM` wall-clock time for the `"open"` tick in `tz`. |
| `close_time` | string | `HH:MM` wall-clock time for the `"close"` tick in `tz`. |
```

After the table, append a short paragraph explaining why those keys are
gone:

```markdown
**Why no `tz`/`open_time`/`close_time`?**  NYSE session times — including
early-close days such as the day after Thanksgiving — are owned by
`pandas_market_calendars` (`_NYSE.schedule(...)`).  Honouring a duplicate
config setting here would silently desynchronise tick timestamps from the
PIT cache on every early-close session.  `ticks_per_day` is the only
schedule-shaped knob the harness still owns — it selects which phases of
each session to fire (`"open"`, `"close"`).
```

- [ ] **Step 4: Verify the doc is internally consistent**

```bash
grep -n "(planned)" config/README.md
grep -n "http_timeout_seconds" config/README.md
grep -nE "^\| \`(tz|open_time|close_time)\`" config/README.md
```

Expected output:
- `(planned)`: no matches.
- `http_timeout_seconds`: one match — the `quiver_http_timeout_seconds`
  row.
- `tz` / `open_time` / `close_time`: no matches.

- [ ] **Step 5: Commit**

```bash
git add config/README.md
git commit -m "docs(config): reconcile README with Phase 7.5 schema changes

- Drop '(planned)' from the backtest settings loader row — the loader
  now exists.
- Rename http_timeout_seconds → quiver_http_timeout_seconds row in the
  data.json section to match the renamed Pydantic field.
- Delete the tz / open_time / close_time rows from backtest_settings.json;
  add a one-paragraph note explaining that NYSE session times are owned
  by pandas_market_calendars, not config."
```

---

## Task 14: End-to-end verification

**Files:**
- No file changes — verification only.

- [ ] **Step 1: Run the full default test suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

Expected: All tests pass.

- [ ] **Step 2: Run the slow end-to-end smoke test against the SVB cache**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```

Expected: PASS.  Note any change in tick counts or skipped tickers
relative to the previous baseline.

- [ ] **Step 3: Run ruff to catch unused imports or stale references**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/ scripts/
```

Expected: No errors.

- [ ] **Step 4: Final greps to confirm acceptance criteria**

```bash
# Hardcoded analyst-lookback module constants are gone.
grep -rn "LOOKBACK_DAYS" src/agents src/data

# Quiver no longer carries its own _HTTP_TIMEOUT.
grep -rn "_HTTP_TIMEOUT" src/data

# The old open/close hardcoded module constants are gone from the schedule.
grep -rnE "_OPEN_TIME|_CLOSE_TIME" src/backtest

# No raw json.loads of backtest_settings.json anywhere — every consumer goes
# through get_backtest_settings().
grep -rn 'backtest_settings.json' src/ scripts/

# The deleted schedule keys must not reappear anywhere in the repo.
grep -rnE '"tz"|"open_time"|"close_time"' config/ src/ scripts/

# The old http_timeout_seconds key must not appear bare anywhere — only the
# renamed quiver_http_timeout_seconds is acceptable.
grep -rn 'http_timeout_seconds' src/ scripts/ tests/ config/ \
    | grep -v 'quiver_http_timeout_seconds'

# README marker "(planned)" is gone.
grep -n  '(planned)' config/README.md

# No xfail markers remain on the lookback contract test (each migration
# task removes its own marker).
grep -n  '@pytest.mark.xfail' tests/contract/test_lookbacks_sourced_from_config.py
```

Expected: Each grep prints zero lines.  The only nuanced one is the
`http_timeout_seconds` grep — it must print zero lines *after* the
`| grep -v` filter excludes the renamed variant.

- [ ] **Step 5: Sanity-check schedule.py against an early-close day**

```bash
PYTHONPATH=src .venv/bin/python -c "
from datetime import date
from backtest.schedule import generate_ticks
ticks = generate_ticks(start=date(2024, 11, 29), end=date(2024, 11, 29), phases=['close'])
print([t.timestamp.isoformat() for t in ticks])
"
```

Expected: `['2024-11-29T13:00:00-05:00']` — the day-after-Thanksgiving NYSE
early close.  If this prints `T16:00`, `schedule.py` is still reading from
config rather than the calendar and Task 4 was not completed correctly.

- [ ] **Step 6: Add a Phase 7.5 done.md**

Create `docs/Phase7.5-more-cleanup/done.md` with a short closeout
summary:

```markdown
# Phase 7.5 — done

**Status:** Closed.

## What landed

- `BacktestSettings` typed loader (`src/backtest/settings.py`,
  `extra="forbid"`) — replaces five raw `json.loads` consumers.
- `src/backtest/schedule.py` rewritten to read NYSE session times from
  `pandas_market_calendars` (`_NYSE.schedule(...)`); the redundant
  `tz` / `open_time` / `close_time` keys were deleted from
  `config/backtest_settings.json`.  Only `ticks_per_day` remains as a
  schedule-shaped knob, selecting which phases of each session to fire.
- Analyst lookbacks read from `get_config().defaults`.
  `POLITICIAN_LOOKBACK_DAYS`, `HOLDER_LOOKBACK_DAYS`, `_INSIDER_LOOKBACK_DAYS`
  removed.  (The aggregator (`get_stock_signal_bundle`) is intentionally
  not migrated here — Phase 7.6 deletes it outright.)
- Cache-provider `lookback_days` defaults dropped — caller-required.
- `scripts/backtest_fetch.py:_ANALYST_LOOKBACK_DAYS` retired.
- `http_timeout_seconds` renamed to `quiver_http_timeout_seconds` to
  reflect its actual single consumer; Quiver HTTP timeout now sourced
  from `get_config().quiver_http_timeout_seconds`.
- `FetchDefaults` extended with `earnings_lookback_quarters` and
  `short_interest_lookback_days`.
- Three runtime contract tests under `tests/contract/` lock the
  behaviour in: cross-cutting lookback flow, Quiver HTTP timeout, and
  schedule-comes-from-the-calendar (early-close-day assertion).

## Behavioural shifts

- `politician_lookback_days` 30 → 90 (analyst side).
- `notable_holder_lookback_days` 90 → 180 (analyst side).
- `notable_holders` cache provider default 365 → caller-required.
- `news_cache` default 30 → caller-required.
- Schedule on early-close NYSE sessions (e.g. day after Thanksgiving)
  now fires the close tick at 13:00 ET rather than 16:00 ET, matching
  reality.

Record these in the next backtest's metrics.md so the baseline is
re-anchored before Phase 8 audits begin.
```

- [ ] **Step 7: Commit**

```bash
git add docs/Phase7.5-more-cleanup/done.md
git commit -m "docs(phase7.5): closeout summary

Records the behavioural shifts that come with the config-as-truth fix
— politician 30→90, notable_holder 90→180 analyst-side, and early-close
NYSE sessions now fire the close tick at the actual 13:00 ET — so the
next backtest's baseline reflects the new values rather than the stale
ones."
```

---

## Self-review checklist

After the implementer finishes Task 14, run through this list once more:

- [ ] Every `grep` in Task 14, Step 4 returns zero lines (after the
      `quiver_http_timeout_seconds` filter on the HTTP-timeout grep).
- [ ] The Step 5 schedule probe prints `2024-11-29T13:00:00-05:00` —
      confirming `pandas_market_calendars` is the source of truth for
      session times, not config.
- [ ] `tests/contract/` has three new tests, all passing, none still
      marked `@pytest.mark.xfail` — each migration task removed its
      own marker as the matching consumer was updated.
- [ ] `src/backtest/settings.py` exists and is imported by Runner,
      `schedule.py`, and the four scripts.
- [ ] `BacktestSettings` is declared with `model_config = ConfigDict(extra="forbid")`,
      so stale `tz` / `open_time` / `close_time` keys in any
      hand-edited config file fail loudly at load time.
- [ ] `config/data.json` still loads cleanly via `get_config()` and
      contains `quiver_http_timeout_seconds` (not the old
      `http_timeout_seconds`).
- [ ] `config/backtest_settings.json` still loads cleanly via
      `get_backtest_settings()` and no longer contains `tz`,
      `open_time`, or `close_time`.
- [ ] The end-to-end smoke test passes.
- [ ] No new TODOs, FIXMEs, or `(planned)` strings introduced.

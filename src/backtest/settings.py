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

Per-window storage layout
-------------------------
Cache and run artefacts live under ``<backtests_root>/<window-name>/``:

    backtests/
    └── svb-stress-2023-03/
        ├── store.sqlite          # golden cache for this window only
        └── runs/
            └── svb-stress-2023-03-abc1234/
                ├── manifest.json
                ├── db.sqlite
                └── ...

The previous layout — one shared cache + one shared runs tree — mixed
window data into a single SQLite file which made it hard to inspect or
delete a single window's data without bespoke SQL.  ``cache_path_for_window``
and ``runs_root_for_window`` are the only sanctioned ways to derive a path
from the settings; helpers ensure callers cannot accidentally collide windows
by hand-rolling the join.
"""
from __future__ import annotations

import json
import re
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

    ``backtests_root`` is the single root directory under which every
    window's cache and runs are nested.  Per-window paths are computed by
    ``cache_path_for_window`` / ``runs_root_for_window``.
    """

    model_config = ConfigDict(extra="forbid")

    backtests_root:                str
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


# ── Per-window path helpers ────────────────────────────────────────────────

def cache_path_for_window(settings: BacktestSettings, window: str) -> Path:
    """Return the golden-cache SQLite path for a specific window.

    Layout: ``<backtests_root>/<window>/store.sqlite``.  Always the same
    filename so tooling does not need to know the window to open it once the
    directory is fixed.

    Parameters
    ----------
    settings:
        Loaded ``BacktestSettings`` instance.
    window:
        Window key from ``config/backtest_windows.json``
        (e.g. ``"svb-stress-2023-03"``).

    Returns
    -------
    Path
        The cache path.  Parent directories may not exist yet — callers
        that write are expected to ``mkdir(parents=True, exist_ok=True)``.
    """
    return Path(settings.backtests_root) / window / "store.sqlite"


def runs_root_for_window(settings: BacktestSettings, window: str) -> Path:
    """Return the runs directory for a specific window.

    Layout: ``<backtests_root>/<window>/runs/``.

    Parameters
    ----------
    settings:
        Loaded ``BacktestSettings`` instance.
    window:
        Window key (e.g. ``"svb-stress-2023-03"``).

    Returns
    -------
    Path
        The runs root.  Individual runs land under
        ``<this>/<run-id>/``.
    """
    return Path(settings.backtests_root) / window / "runs"


# A run-id is ``<window-key>-<7-char git sha>``.  ``_git_sha7()`` in the
# runner produces 7 lower-case hex chars; we anchor to that to avoid eating
# legitimate trailing chunks of a window slug.
_RUN_ID_RE = re.compile(r"^(?P<window>.+)-(?P<sha>[0-9a-f]{7})$")


def window_from_run_id(run_id: str) -> str:
    """Extract the window key from a run-id of the form ``<window>-<sha7>``.

    Run-IDs follow the format ``<window-key>-<7-char git sha>`` (see
    ``backtest.runner._git_sha7``).  We recover the window key by stripping
    the trailing 7-char hex sha (plus the dash that joins them).

    Parameters
    ----------
    run_id:
        Run identifier, e.g. ``"svb-stress-2023-03-abc1234"``.

    Returns
    -------
    str
        The window key, e.g. ``"svb-stress-2023-03"``.

    Raises
    ------
    ValueError
        If ``run_id`` does not match the expected shape.
    """
    m = _RUN_ID_RE.match(run_id)
    if not m:
        raise ValueError(
            f"run_id {run_id!r} does not match <window>-<sha7> format"
        )
    return m.group("window")

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

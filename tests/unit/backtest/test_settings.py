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
    """BacktestSettings uses extra='forbid' so stale schedule keys fail loudly."""
    from backtest.settings import load_backtest_settings_from

    payload = {
        "cache_path":                  "x",
        "runs_root":                   "y",
        "ticks_per_day":               ["open", "close"],
        "tz":                          "America/New_York",
        "open_time":                   "09:30",
        "close_time":                  "16:00",
        "failed_tick_abort_ratio":     0.1,
        "fake_broker_starting_cash":   100.0,
        "forward_return_horizons_days": [1],
        "ohlcv_warmup_days":           30,
    }
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception) as excinfo:
        load_backtest_settings_from(path)

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

    assert first == second
    assert first is not second

"""Unit tests for the BacktestSettings loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_loader_validates_minimal_payload(tmp_path: Path) -> None:
    """A complete payload loads into a BacktestSettings instance."""
    from backtest.settings import BacktestSettings, load_backtest_settings_from

    payload = {
        "backtests_root":              "backtests",
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
    assert settings.backtests_root            == "backtests"
    assert settings.ticks_per_day             == ["open", "close"]
    assert settings.fake_broker_starting_cash == 100_000.0


def test_loader_rejects_out_of_range_abort_ratio(tmp_path: Path) -> None:
    """failed_tick_abort_ratio outside [0, 1] is rejected by validation."""
    from pydantic import ValidationError

    from backtest.settings import load_backtest_settings_from

    payload = {
        "backtests_root":              "x",
        "ticks_per_day":               ["open", "close"],
        "failed_tick_abort_ratio":     1.5,
        "fake_broker_starting_cash":   100.0,
        "forward_return_horizons_days": [1],
        "ohlcv_warmup_days":           30,
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_backtest_settings_from(path)


def test_loader_rejects_unknown_keys(tmp_path: Path) -> None:
    """BacktestSettings uses extra='forbid' so stale schedule keys fail loudly."""
    from backtest.settings import load_backtest_settings_from

    payload = {
        "backtests_root":              "x",
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


def test_runner_accepts_backtest_settings_instance(tmp_path: Path, monkeypatch) -> None:
    """Runner.__init__ accepts an injected BacktestSettings instance."""
    from backtest.runner import Runner
    from backtest.settings import BacktestSettings

    settings = BacktestSettings(
        backtests_root               = str(tmp_path / "backtests"),
        ticks_per_day                = ["open", "close"],
        failed_tick_abort_ratio      = 0.1,
        fake_broker_starting_cash    = 100_000.0,
        forward_return_horizons_days = [1, 5, 20],
        ohlcv_warmup_days            = 30,
    )

    windows_path   = tmp_path / "windows.json"
    watchlist_path = tmp_path / "watchlist.json"
    windows_path.write_text(
        '{"smoke": {"start": "2024-01-02", "end": "2024-01-03", "notes": "",'
        ' "risk_free_rate_annual": 0.04}}',
        encoding="utf-8",
    )
    watchlist_path.write_text('{"tickers": ["AAPL"]}', encoding="utf-8")

    runner = Runner(
        settings       = settings,
        windows_path   = windows_path,
        watchlist_path = watchlist_path,
    )
    assert runner._settings is settings


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

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "loads"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                src_segment = ast.get_source_segment(source, node) or ""
                assert "backtest_settings.json" not in src_segment, (
                    f"{path_str}: direct json.loads(backtest_settings.json) "
                    "found — use get_backtest_settings() instead."
                )

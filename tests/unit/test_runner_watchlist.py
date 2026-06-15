"""Regression tests for the backtest runner's watchlist loading.

The watchlist.json schema gained an extended ``{"symbol", "name"}`` object
format (for the news re-ranker).  The runner reads watchlist.json directly via
its configurable ``watchlist_path``, so it must normalise both formats to bare
symbol strings — otherwise downstream cache reads (e.g. ``read_ohlcv``) receive
a dict and SQLite raises ``ProgrammingError: type 'dict' is not supported``.
"""
from __future__ import annotations

import json
from pathlib import Path

from backtest.runner import Runner
from backtest.settings import BacktestSettings


def _make_settings(tmp_path: Path) -> BacktestSettings:
    """Build a minimal, sandboxed ``BacktestSettings`` for runner construction."""
    return BacktestSettings(
        backtests_root               = str(tmp_path / "backtests"),
        ticks_per_day                = ["open"],
        failed_tick_abort_ratio      = 1.0,
        fake_broker_starting_cash    = 100_000.0,
        forward_return_horizons_days = [1],
        ohlcv_warmup_days            = 30,
    )


def _write_windows(tmp_path: Path) -> Path:
    """Write a minimal valid windows file and return its path."""
    windows_path = tmp_path / "backtest_windows.json"
    windows_path.write_text(json.dumps({
        "baseline-2025-09": {
            "start": "2025-09-02",
            "end":   "2025-09-02",
            "notes": "Single-tick slice for unit test.",
            "risk_free_rate_annual": 0.040,
        }
    }))
    return windows_path


def test_runner_normalises_extended_watchlist(tmp_path):
    """Extended ``{"symbol", "name"}`` entries load as bare symbol strings."""
    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({
        "tickers": [
            {"symbol": "AAPL", "name": "Apple"},
            {"symbol": "MSFT", "name": "Microsoft"},
        ]
    }))

    runner = Runner(
        settings       = _make_settings(tmp_path),
        windows_path   = _write_windows(tmp_path),
        watchlist_path = watchlist_path,
    )

    assert runner._watchlist == ["AAPL", "MSFT"]


def test_runner_still_accepts_legacy_watchlist(tmp_path):
    """The legacy flat-string format keeps working unchanged."""
    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"tickers": ["AAPL", "MSFT"]}))

    runner = Runner(
        settings       = _make_settings(tmp_path),
        windows_path   = _write_windows(tmp_path),
        watchlist_path = watchlist_path,
    )

    assert runner._watchlist == ["AAPL", "MSFT"]

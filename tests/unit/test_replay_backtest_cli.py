# tests/unit/test_replay_backtest_cli.py
from __future__ import annotations

from scripts.replay_backtest import build_runner_args


def test_default_window():
    args = build_runner_args([])
    assert args.window == "30d"


def test_explicit_args(tmp_path):
    args = build_runner_args([
        "--window", "7d",
        "--fixture-dir", str(tmp_path),
    ])
    assert args.window == "7d"
    assert str(args.fixture_dir) == str(tmp_path)

# tests/unit/test_smoke_run_cli.py
"""smoke_run script: --help works, dry mode validates wiring without LLM calls."""
from __future__ import annotations

from scripts.smoke_run import build_runner_args


def test_default_args():
    args = build_runner_args([])
    assert args.ticks == 3
    assert args.starting_cash == 10_000.0


def test_explicit_args():
    args = build_runner_args(["--ticks", "1", "--starting-cash", "5000"])
    assert args.ticks == 1
    assert args.starting_cash == 5000.0

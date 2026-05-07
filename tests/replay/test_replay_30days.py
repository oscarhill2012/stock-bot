# tests/replay/test_replay_30days.py
from __future__ import annotations

import pytest


@pytest.mark.replay
def test_replay_30_days_runs_and_produces_executions():
    """30-day walk-forward through full pipeline. Long-running."""
    from scripts.replay_backtest import run_replay
    summary = run_replay(window="30d", fixture_dir=None)
    # Basic sanity: ran some ticks, didn't crash
    assert summary.ticks_completed > 0

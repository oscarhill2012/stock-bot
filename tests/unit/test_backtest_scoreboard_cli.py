"""Unit tests for the ``scripts.backtest_scoreboard`` CLI helpers.

The end-to-end ``main()`` is disk-coupled (it resolves per-window golden
caches from config and opens SQLite), so it is exercised by a manual smoke
run rather than a unit test.  The pure, testable logic is the run/window
pairing + validation that backs the multi-window pooling invocation — that is
what these tests pin.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.backtest_scoreboard import _pair_run_specs


class TestPairRunSpecs:
    """``_pair_run_specs`` pairs repeated --run-dir / --window flags by position."""

    def test_equal_length_lists_pair_by_position(self) -> None:
        """Each run-dir is paired with the window at the same index, in order."""

        pairs = _pair_run_specs(
            run_dirs=["backtests/a/runs/r1", "backtests/b/runs/r2"],
            windows=["window-a", "window-b"],
        )

        assert pairs == [
            (Path("backtests/a/runs/r1"), "window-a"),
            (Path("backtests/b/runs/r2"), "window-b"),
        ]

    def test_single_pair_is_returned_as_one_tuple(self) -> None:
        """The single-window case (the legacy invocation) yields one pair."""

        pairs = _pair_run_specs(
            run_dirs=["backtests/a/runs/r1"],
            windows=["window-a"],
        )

        assert pairs == [(Path("backtests/a/runs/r1"), "window-a")]

    def test_mismatched_counts_raise_loudly(self) -> None:
        """A count mismatch is a user error and must raise, never silently
        truncate or pad (silent degradation is the house bug class)."""

        with pytest.raises(ValueError, match="--run-dir.*--window"):
            _pair_run_specs(
                run_dirs=["backtests/a/runs/r1", "backtests/b/runs/r2"],
                windows=["window-a"],
            )

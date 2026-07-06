"""Tests for the forward-return tail buffer in the backtest fetch step.

The forward-return scoreboard reads the realised price bar at
``tick + horizon`` calendar days.  For ticks near the window's end the +20d
bar lands *after* ``window.end``, so unless the cache holds a tail of bars
beyond the window the longest-horizon coverage silently drops (the eval shows
n=640 at +20d vs n=1160 at +1d).

``_forward_tail_days`` sizes that tail.  The tail feeds ONLY the un-PIT-gated
forward-return read — every in-window pipeline tick re-gates OHLCV by its own
``as_of`` — so it cannot leak future prices into a decision.
"""
from __future__ import annotations

from scripts.backtest_fetch import _FORWARD_BAR_SKIP_DAYS, _forward_tail_days


def test_tail_covers_longest_horizon_plus_forward_bar_skip() -> None:
    """The tail must reach at least ``max(horizon) + _forward_bar's skip``.

    ``_forward_bar`` searches ``[target, target + 4d]`` for the bar at
    ``base_date + h``; to score a tick sitting on the final window day at the
    longest horizon, bars must exist out to ``end + max(h) + skip``.
    """
    tail = _forward_tail_days([1, 5, 20])

    assert tail >= 20 + _FORWARD_BAR_SKIP_DAYS


def test_tail_uses_max_horizon_not_sum() -> None:
    """Coverage depends on the longest horizon alone, not their sum."""
    assert _forward_tail_days([1, 5, 20]) == _forward_tail_days([20])


def test_tail_is_zero_when_no_horizons_configured() -> None:
    """No forward horizons → no tail needed (don't over-fetch)."""
    assert _forward_tail_days([]) == 0

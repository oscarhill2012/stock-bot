"""Regression tests for ``_pit_adjust`` — the PIT back-adjustment helper.

These tests exercise the numeric path that the wider ``as_of`` suite skips
(it monkeypatches the function away).  The headline regression here is the
pandas 3.x ``LossySetitemError`` raised when multiplying an ``int64`` volume
column by a float split factor: a real ticker (GOOGL, AAPL, TSLA, …) with
any historical split crashes the cache-fill on the live provider.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def _make_history(n_days: int = 10, end: date = date(2023, 1, 10)) -> pd.DataFrame:
    """Build a fake yfinance ``history`` frame ending on ``end``.

    The shape mirrors what ``yf.Ticker.history(..., auto_adjust=False)``
    actually returns: OHLC as ``float64``, **Volume as ``int64``**, indexed
    by a tz-aware ``DatetimeIndex``.  The ``int64`` dtype on volume is the
    key detail — that is what triggers the pandas 3.x bug.
    """
    idx = pd.date_range(end=pd.Timestamp(end, tz="UTC"), periods=n_days, freq="D")
    return pd.DataFrame(
        {
            "Open":   np.linspace(100.0, 110.0, n_days, dtype="float64"),
            "High":   np.linspace(101.0, 111.0, n_days, dtype="float64"),
            "Low":    np.linspace( 99.0, 109.0, n_days, dtype="float64"),
            "Close":  np.linspace(100.5, 110.5, n_days, dtype="float64"),
            "Volume": np.arange(1_000_000, 1_000_000 + n_days, dtype="int64"),
        },
        index=idx,
    )


def _make_actions(ex_date: date, split: float = 0.0, div: float = 0.0) -> pd.DataFrame:
    """Build a fake yfinance ``actions`` frame with a single corporate event."""
    return pd.DataFrame(
        {"Dividends": [div], "Stock Splits": [split]},
        index=pd.DatetimeIndex([pd.Timestamp(ex_date, tz="UTC")]),
    )


def test_pit_adjust_handles_int64_volume_through_split() -> None:
    """A split with int64 volume must not raise ``LossySetitemError``.

    Regression for the cache-fill crash on tickers with a non-integer split
    factor in their history (e.g. JPM's 3-for-2 → ``split=1.5``, or GOOGL's
    2014 class-share restructure).  Under pandas >= 3.0, ``int64 *= 1.5``
    produces half-integer floats that the int64 block refuses to accept,
    raising ``LossySetitemError`` — so the previous in-place ``*=`` could
    not survive even one fractional split.

    We use ``split=1.5`` here because that is the value-shape that
    actually trips pandas 3.x.  Whole-number splits (2.0, 4.0, 20.0)
    happen to round-trip cleanly and would silently mask the bug.
    """
    import data.providers.stats.yfinance as mod

    # 10 days ending 2023-01-10, 3-for-2 split on 2023-01-06 →
    # bars 2023-01-01 .. 2023-01-05 are pre-split; rest are post-split.
    history = _make_history(n_days=10, end=date(2023, 1, 10))
    actions = _make_actions(ex_date=date(2023, 1, 6), split=1.5)

    pre_split_count = int(sum(history.index.date < date(2023, 1, 6)))
    pre_split_vol_before = history.loc[
        history.index.date < date(2023, 1, 6), "Volume",
    ].copy()

    out = mod._pit_adjust(history, actions, as_of=date(2023, 1, 10))

    assert out is not None, "expected a frame back, not None"
    assert len(out) == len(history)

    # Pre-split volume must have been multiplied by the split factor —
    # half-integer results (e.g. 1_000_000 * 1.5 = 1_500_000.0) are
    # expected and must be preserved exactly.
    pre_split_vol_after = out.loc[out.index.date < date(2023, 1, 6), "Volume"]
    assert len(pre_split_vol_after) == pre_split_count
    np.testing.assert_allclose(
        pre_split_vol_after.to_numpy(dtype="float64"),
        pre_split_vol_before.to_numpy(dtype="float64") * 1.5,
    )

    # Pre-split OHLC must have been divided by the split factor.
    pre_close_after = out.loc[out.index.date < date(2023, 1, 6), "Close"]
    pre_close_before = history.loc[history.index.date < date(2023, 1, 6), "Close"]
    np.testing.assert_allclose(
        pre_close_after.to_numpy(),
        pre_close_before.to_numpy() / 1.5,
    )


def test_pit_adjust_noop_when_actions_after_as_of() -> None:
    """Actions with ex-date after ``as_of`` must be ignored entirely.

    This keeps the existing PIT contract: a 2024-01-01 split is invisible
    to a 2023-06-01 ``as_of`` cutoff.
    """
    import data.providers.stats.yfinance as mod

    history = _make_history(n_days=5, end=date(2023, 6, 1))
    actions = _make_actions(ex_date=date(2024, 1, 1), split=2.0)

    out = mod._pit_adjust(history, actions, as_of=date(2023, 6, 1))

    # Unchanged — every value should match the input exactly.
    assert out is not None
    pd.testing.assert_frame_equal(out, history)

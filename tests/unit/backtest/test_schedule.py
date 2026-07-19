"""Tests for the tick-schedule generator."""
from __future__ import annotations

from datetime import date

from backtest.schedule import generate_ticks


def test_generate_ticks_skips_weekends() -> None:
    """Friday 2023-03-10 → Monday 2023-03-13: the intervening Sat/Sun are skipped.

    Only the two NYSE sessions (Fri + Mon) produce ticks; the weekend does
    not.  Asserted on the set of session *dates* rather than on exact
    ``Tick`` objects so the test isolates the weekend-skipping contract from
    two orthogonal concerns it should not depend on:

    - the ``ticks_per_day`` phase policy (open-only vs open+close), which is
      a config knob this test does not own; and
    - the calendar's timezone representation — ``pandas_market_calendars``
      owns session times and returns them tz-aware in UTC (see the
      ``schedule`` module docstring), so pinning wall-clock NY times here
      would merely re-test the library.

    Mirrors ``test_generate_ticks_skips_nyse_holidays`` below, which already
    asserts calendar behaviour via the session-date set.
    """
    ticks = generate_ticks(date(2023, 3, 10), date(2023, 3, 13))

    tick_dates = {t.as_of.date() for t in ticks}

    assert tick_dates == {date(2023, 3, 10), date(2023, 3, 13)}   # Fri + Mon only
    assert date(2023, 3, 11) not in tick_dates                    # Saturday skipped
    assert date(2023, 3, 12) not in tick_dates                    # Sunday skipped


def test_generate_ticks_skips_nyse_holidays() -> None:
    """2023-04-07 is Good Friday — NYSE closed. The schedule must skip it."""
    ticks = generate_ticks(date(2023, 4, 6), date(2023, 4, 10))

    tick_dates = {t.as_of.date() for t in ticks}
    assert date(2023, 4, 7)  not in tick_dates   # Good Friday
    assert date(2023, 4, 6)  in tick_dates       # Thursday
    assert date(2023, 4, 10) in tick_dates       # Monday


def test_generate_ticks_empty_range() -> None:
    """A range covering only a weekend yields zero ticks."""
    # 2023-03-11 (Sat) → 2023-03-12 (Sun)
    assert generate_ticks(date(2023, 3, 11), date(2023, 3, 12)) == []

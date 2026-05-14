"""Tests for the tick-schedule generator."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from backtest.schedule import Tick, generate_ticks

NY = ZoneInfo("America/New_York")


def test_generate_ticks_skips_weekends() -> None:
    """Friday 2023-03-10 → Monday 2023-03-13 yields 4 ticks (Fri open/close, Mon open/close)."""
    ticks = generate_ticks(date(2023, 3, 10), date(2023, 3, 13))

    expected = [
        Tick(as_of=datetime(2023, 3, 10,  9, 30, tzinfo=NY), phase="open"),
        Tick(as_of=datetime(2023, 3, 10, 16,  0, tzinfo=NY), phase="close"),
        Tick(as_of=datetime(2023, 3, 13,  9, 30, tzinfo=NY), phase="open"),
        Tick(as_of=datetime(2023, 3, 13, 16,  0, tzinfo=NY), phase="close"),
    ]
    assert ticks == expected


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

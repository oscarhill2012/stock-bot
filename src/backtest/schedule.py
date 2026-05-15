"""Tick-schedule generator.

Yields ``Tick(as_of, phase)`` pairs over NYSE business days in a date range,
emitting one tick at the configured open time and one at the close time per
session.  Holidays and weekends are skipped via ``pandas_market_calendars``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

Phase = Literal["open", "close"]

# NYSE calendar — singleton lookup is cheap, but instantiating per call is too.
_NYSE = mcal.get_calendar("NYSE")
_NY   = ZoneInfo("America/New_York")
_OPEN_TIME  = time(9, 30)
_CLOSE_TIME = time(16, 0)


@dataclass(frozen=True)
class Tick:
    """One scheduled tick — timezone-aware NY-local ``as_of`` plus phase tag."""

    as_of: datetime
    phase: Phase


def generate_ticks(start: date, end: date) -> list[Tick]:
    """Return open + close ticks for every NYSE business day in ``[start, end]``.

    Holidays and early-close days are handled via ``pandas_market_calendars``;
    weekends fall out naturally.  Returned list is sorted by ``as_of``.
    """
    sessions = _NYSE.valid_days(start_date=start, end_date=end)

    ticks: list[Tick] = []
    for ts in sessions:
        d = ts.date()
        ticks.append(Tick(datetime.combine(d, _OPEN_TIME,  tzinfo=_NY), "open"))
        ticks.append(Tick(datetime.combine(d, _CLOSE_TIME, tzinfo=_NY), "close"))
    return ticks

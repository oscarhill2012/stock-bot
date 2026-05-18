"""Tick-schedule generator — driven by pandas_market_calendars.

Yields ``Tick(as_of, phase)`` pairs over NYSE business days in a date
range.  For each session the configured ``ticks_per_day`` policy decides
whether to emit the open tick, the close tick, or both.

**Session times come from the calendar, not from config.**
``pandas_market_calendars`` already owns NYSE session times — including
early-close days (day-after-Thanksgiving 13:00 ET, Christmas Eve 13:00
ET, etc.).  Letting a user override ``open_time`` or ``close_time`` via
config would silently break PIT alignment on those days, so the keys are
absent from ``BacktestSettings`` by design.

Calendar choice (``NYSE``) is hardcoded for the same reason multi-calendar
support is out of scope: every consumer of ``pandas_market_calendars`` in
the harness would otherwise need a calendar identifier plumbed through,
and there is no plausible non-NYSE use case before live deploy.  The only
configurable schedule surface is ``ticks_per_day`` (which subset of
``{"open", "close"}`` to emit).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import pandas_market_calendars as mcal

from backtest.settings import get_backtest_settings

Phase = Literal["open", "close"]

# NYSE calendar — calendar choice is hardcoded by design (see module
# docstring).  Cached at import time because the calendar object is
# stateless and stable for the process lifetime.
_NYSE = mcal.get_calendar("NYSE")

# Phases this generator knows how to emit.  Any deviation in
# settings.ticks_per_day raises at run time rather than silently emitting
# the wrong cadence.
_SUPPORTED_PHASES: frozenset[str] = frozenset({"open", "close"})


@dataclass(frozen=True)
class Tick:
    """One scheduled tick — timezone-aware ``as_of`` plus phase tag."""

    as_of: datetime
    phase: Phase


def generate_ticks(start: date, end: date) -> list[Tick]:
    """Return ticks for every NYSE session in ``[start, end]``.

    Session times come from ``pandas_market_calendars.schedule()``, which
    handles holidays and early-close days correctly by construction.
    The configured ``ticks_per_day`` (a subset of ``{"open", "close"}``)
    decides which phases to emit per session.

    Parameters
    ----------
    start, end:
        Inclusive date range.

    Returns
    -------
    list[Tick]
        Ticks in chronological order.

    Raises
    ------
    ValueError
        If ``settings.ticks_per_day`` contains any value outside
        ``{"open", "close"}``.
    """
    settings = get_backtest_settings()

    # Validate supported phases up front — fail loudly rather than emit a
    # cadence the rest of the harness does not understand.  Set difference
    # (rather than equality) lets us accept ["open"] or ["close"] alone.
    requested_phases = set(settings.ticks_per_day)
    if not requested_phases.issubset(_SUPPORTED_PHASES):
        raise ValueError(
            f"unsupported ticks_per_day={settings.ticks_per_day!r}; "
            f"supported phases are {sorted(_SUPPORTED_PHASES)!r}."
        )

    # schedule() returns a DataFrame indexed by date with 'market_open'
    # and 'market_close' columns of tz-aware pandas Timestamps.  For
    # early-close days, 'market_close' is set to that day's actual close
    # time (e.g. 13:00 ET on day-after-Thanksgiving).
    sched = _NYSE.schedule(start_date=start, end_date=end)

    ticks: list[Tick] = []
    for _, row in sched.iterrows():
        if "open" in requested_phases:
            ticks.append(Tick(row["market_open"].to_pydatetime(), "open"))
        if "close" in requested_phases:
            ticks.append(Tick(row["market_close"].to_pydatetime(), "close"))

    ticks.sort(key=lambda t: t.as_of)
    return ticks

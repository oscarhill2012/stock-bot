# tests/unit/backtest/test_driver_wallclock_telemetry.py
"""Test that the driver propagates timeguard's per-tick fallback count
into the telemetry record's ``wall_clock_fallback_fired`` flag.

We do not boot the full Runner here — we exercise the telemetry-build
fragment in isolation by simulating one tick's drain.
"""

from __future__ import annotations

import pytest

from backtest.audit.telemetry import build_telemetry_record
from backtest.schedule import Tick
from data import timeguard


@pytest.fixture(autouse=True)
def _drain_counter():
    """Ensure each test starts with a clean fallback counter."""

    timeguard.drain_wallclock_fallback_count()
    yield
    timeguard.drain_wallclock_fallback_count()


def _make_tick():
    """Construct a Tick using whatever constructor the real dataclass exposes."""

    # Try the convenience factory first; fall back to constructor.
    if hasattr(Tick, "from_as_of_phase"):
        return Tick.from_as_of_phase("2024-01-02T13:30:00+00:00", "open")
    from datetime import UTC, datetime
    return Tick(as_of=datetime(2024, 1, 2, 13, 30, tzinfo=UTC), phase="open")


def test_telemetry_reports_fallback_when_timeguard_counter_nonzero():
    """If a wall-clock fallback fired during the tick, the flag is True."""

    # Simulate one fallback firing within the tick.
    timeguard.resolve_as_of(None, allow_wallclock=True, site="unit-test")

    count = timeguard.drain_wallclock_fallback_count()
    tick = _make_tick()

    record = build_telemetry_record(
        tick=tick,
        run_id="unit-test-run",
        strict_mode=False,
        per_domain={},
        report_cache_hits=[],
        db_writes_recorded_at={},
        wall_clock_fallback_fired=count > 0,
    )

    assert record["tripwires"]["wall_clock_fallback_fired"] is True


def test_telemetry_reports_no_fallback_when_counter_zero():
    """Cold drain → flag is False (regression guard for B1)."""

    count = timeguard.drain_wallclock_fallback_count()
    tick = _make_tick()

    record = build_telemetry_record(
        tick=tick,
        run_id="unit-test-run",
        strict_mode=False,
        per_domain={},
        report_cache_hits=[],
        db_writes_recorded_at={},
        wall_clock_fallback_fired=count > 0,
    )

    assert record["tripwires"]["wall_clock_fallback_fired"] is False

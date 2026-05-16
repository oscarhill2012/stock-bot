# tests/unit/data/test_timeguard_fallback_counter.py
"""Unit tests for the per-tick wall-clock fallback counter on timeguard.

The counter underpins Phase 6 tripwire ``wall_clock_fallback_fired``.
Strict mode is an absolute veto on wall-clock substitution, so all tests
below run with ``STOCKBOT_STRICT_AS_OF`` unset.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from data import timeguard


@pytest.fixture(autouse=True)
def _clear_strict_env(monkeypatch):
    """Ensure strict mode is OFF — we are exercising the fallback path."""

    monkeypatch.delenv(timeguard._STRICT_ENV_VAR, raising=False)
    # Drain any state left behind by other tests.
    timeguard.drain_wallclock_fallback_count()


def test_drain_returns_zero_when_no_fallback_fired():
    """A fresh process / freshly drained counter reports zero."""

    assert timeguard.drain_wallclock_fallback_count() == 0


def test_supplied_candidate_does_not_increment_counter():
    """If the caller supplied an as_of, no fallback fires."""

    from datetime import datetime

    candidate = datetime(2024, 1, 2, 13, 30, tzinfo=UTC)
    timeguard.resolve_as_of(candidate, allow_wallclock=True, site="test")
    assert timeguard.drain_wallclock_fallback_count() == 0


def test_wallclock_fallback_increments_counter():
    """Missing candidate + allow_wallclock=True bumps the counter."""

    timeguard.resolve_as_of(None, allow_wallclock=True, site="test")
    assert timeguard.drain_wallclock_fallback_count() == 1


def test_drain_resets_the_counter():
    """Reading the counter clears it, ready for the next tick."""

    timeguard.resolve_as_of(None, allow_wallclock=True, site="test")
    timeguard.resolve_as_of(None, allow_wallclock=True, site="test")

    assert timeguard.drain_wallclock_fallback_count() == 2
    assert timeguard.drain_wallclock_fallback_count() == 0

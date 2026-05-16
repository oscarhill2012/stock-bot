"""Leak-regression: the price_history_cache provider must strip any bar
whose timestamp falls on ``as_of``'s date during the OPEN phase.

This is the *positive* counterpart to the deliberate
``open_tick_sameday_bar`` tripwire exclusion in
``tests/integration/backtest/test_end_to_end_smoke.py``.  If a refactor
ever bypasses ``price_history_cache.fetch`` — routing through a different
code path — the existing smoke-test exclusion would silently hide the leak.
This test catches it explicitly at the provider boundary.

The assertion is intentionally narrow and sharp:
- At OPEN phase, zero bars dated ``as_of.date()`` may appear in the result.
- At CLOSE phase, the same-day bar IS permitted (close price is settled).
- With ``phase=None`` (conservative default), same-day bars are also stripped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers._store_handle import clear_store, set_store
from backtest.providers.price_history_cache import fetch
from data.models import OHLCBar

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store_with_prior_and_sameday_bars(tmp_path: Path) -> CachedDataStore:
    """Wire a real store containing one prior-day bar and one same-day bar.

    Both bars are written for ticker ``"TEST"`` so the provider has
    something to filter.  The fixture calls ``set_store`` so the provider
    picks it up via the normal ``get_store()`` pathway, then tears down
    with ``clear_store`` after the test.

    Parameters
    ----------
    tmp_path:
        Pytest-supplied temporary directory; a fresh ``cache.sqlite`` is
        created inside it so tests remain isolated.

    Returns
    -------
    CachedDataStore
        The configured store instance (returned for inspection if needed).
    """
    db_path = tmp_path / "cache.sqlite"
    store   = CachedDataStore(db_path)

    bars = [
        # Prior day — must survive both OPEN and CLOSE phase filtering.
        OHLCBar(
            timestamp=datetime(2024, 1, 8, 0, 0, tzinfo=UTC),
            open=100.0, high=101.0, low=99.0, close=100.5, volume=1_000_000,
        ),
        # Same day as ``as_of`` — must be STRIPPED at OPEN, kept at CLOSE.
        OHLCBar(
            timestamp=datetime(2024, 1, 9, 0, 0, tzinfo=UTC),
            open=101.0, high=102.0, low=100.0, close=101.5, volume=1_100_000,
        ),
    ]

    store.write_ohlcv("TEST", bars)
    set_store(store)

    yield store

    # Ensure no state bleeds into subsequent tests.
    clear_store()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_open_phase_strips_sameday_bar(
    store_with_prior_and_sameday_bars: CachedDataStore,
) -> None:
    """At OPEN, the provider must not return the bar dated as_of's date.

    This is the core B5 assertion.  The same-day bar's close price is not
    yet public at 09:30, so leaking it would constitute a look-ahead bias.
    """
    as_of = datetime(2024, 1, 9, 9, 30, tzinfo=UTC)  # open phase on the same day

    result = await fetch("TEST", as_of=as_of, phase="open")

    timestamps = [bar.timestamp.date() for bar in result.bars]

    # Prior-day bar must be present.
    assert date(2024, 1, 8) in timestamps, (
        "Prior-day bar unexpectedly absent from OPEN result"
    )

    # Same-day bar must NOT be present — this is the leak-prevention assertion.
    assert date(2024, 1, 9) not in timestamps, (
        f"Same-day bar leaked into OPEN phase output: {timestamps}"
    )


async def test_close_phase_retains_sameday_bar(
    store_with_prior_and_sameday_bars: CachedDataStore,
) -> None:
    """At CLOSE, today's bar IS visible because the close price is settled.

    This is the positive complement: confirm the strip is phase-conditional,
    not unconditional.  If this fails, the provider is over-stripping.
    """
    as_of = datetime(2024, 1, 9, 16, 0, tzinfo=UTC)  # close phase on the same day

    result = await fetch("TEST", as_of=as_of, phase="close")

    timestamps = [bar.timestamp.date() for bar in result.bars]

    assert date(2024, 1, 9) in timestamps, (
        "Same-day bar was incorrectly stripped at CLOSE phase"
    )


async def test_none_phase_defaults_to_open_behaviour(
    store_with_prior_and_sameday_bars: CachedDataStore,
) -> None:
    """When phase is omitted the provider must fail closed (strip same-day bar).

    The conservative rule is: unknown phase → treat as OPEN.  This prevents
    accidental look-ahead if a call site forgets to pass phase.
    """
    as_of = datetime(2024, 1, 9, 9, 30, tzinfo=UTC)

    result = await fetch("TEST", as_of=as_of)  # phase intentionally omitted

    timestamps = [bar.timestamp.date() for bar in result.bars]

    assert date(2024, 1, 9) not in timestamps, (
        f"Same-day bar leaked when phase=None (should default to open): {timestamps}"
    )

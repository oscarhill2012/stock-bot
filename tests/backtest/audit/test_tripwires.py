"""Tripwire flags fire on the documented scenarios."""
from __future__ import annotations

from datetime import UTC, datetime

from backtest.audit.tripwires import compute_tripwires


def test_filter_key_after_as_of_fires() -> None:
    """A row with filter_key > as_of must trip ``any_filter_key_after_as_of``."""
    as_of = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)

    flags = compute_tripwires(
        as_of=as_of,
        phase="open",
        per_domain={
            "news": {
                "provider": "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count": 1,
                        "min_published_at": as_of.isoformat(),
                        # Strictly after as_of — leak.
                        "max_published_at": datetime(2023, 3, 11, 12, 0, tzinfo=UTC).isoformat(),
                        "midnight_utc_count": 0,
                        "missing_timestamp_count": 0,
                    }
                }
            }
        },
        wall_clock_fallback_fired=False,
    )

    assert flags["any_filter_key_after_as_of"] is True


def test_open_tick_sameday_bar_fires() -> None:
    """``sameday_bar_seen=True`` on any ticker at open phase trips the flag."""
    as_of = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)

    flags = compute_tripwires(
        as_of=as_of,
        phase="open",
        per_domain={
            "price_history": {
                "provider": "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count": 1,
                        "min_ts": "2023-03-09T00:00:00+00:00",
                        "max_ts": "2023-03-10T00:00:00+00:00",
                        "sameday_bar_seen": True,
                    }
                }
            }
        },
        wall_clock_fallback_fired=False,
    )

    assert flags["open_tick_sameday_bar_advisory"] is True


def test_close_phase_sameday_bar_does_not_fire() -> None:
    """At ``"close"`` phase, today's bar is public — flag must stay False."""
    as_of = datetime(2023, 3, 10, 16, 0, tzinfo=UTC)

    flags = compute_tripwires(
        as_of=as_of,
        phase="close",
        per_domain={
            "price_history": {
                "provider": "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count": 1,
                        "min_ts": "2023-03-09T00:00:00+00:00",
                        "max_ts": "2023-03-10T00:00:00+00:00",
                        "sameday_bar_seen": True,
                    }
                }
            }
        },
        wall_clock_fallback_fired=False,
    )

    assert flags["open_tick_sameday_bar_advisory"] is False

"""S8 — benign tripwires renamed to ``*_advisory`` and excluded from actionable summary.

Two tripwires fired benignly on every (relevant) tick in baseline-2025-09:

- ``midnight_utc_timestamps_seen_advisory`` (46/46 ticks) — date-only sources
  promoted to midnight is steady state.
- ``open_tick_sameday_bar_advisory`` (23/23 open ticks) — provider strips the
  same-day bar before the consumer sees it; the audit fires before the strip.

Renaming both to ``*_advisory`` documents why they are benign and gets
them out of the "tripwires_fired" actionable count so real signal is
not drowned out.
"""
from __future__ import annotations

from datetime import UTC, datetime

from backtest.audit.tripwires import (
    ACTIONABLE_TRIPWIRES,
    compute_tripwires,
)


def test_renamed_tripwires_exist() -> None:
    """Both tripwires surface under the new ``*_advisory`` names."""

    result = compute_tripwires(
        as_of=datetime(2025, 9, 2, 13, 30, tzinfo=UTC),
        phase="open",
        per_domain={
            "price_history": {
                "provider":    "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count":            1,
                        "min_ts":           "2025-09-02T00:00:00+00:00",
                        "max_ts":           "2025-09-02T00:00:00+00:00",
                        "sameday_bar_seen": True,
                    },
                },
            },
            "news": {
                "provider":    "cache",
                "ticker_rows": {
                    "AAPL": {
                        "count":                    1,
                        "min_published_at":         "2025-09-02T00:00:00+00:00",
                        "max_published_at":         "2025-09-02T00:00:00+00:00",
                        "midnight_utc_count":       1,
                        "missing_timestamp_count":  0,
                    },
                },
            },
        },
        wall_clock_fallback_fired=False,
    )

    assert "midnight_utc_timestamps_seen_advisory" in result
    assert "open_tick_sameday_bar_advisory"        in result


def test_renamed_tripwires_not_in_actionable_set() -> None:
    """The advisory tripwires are excluded from ``ACTIONABLE_TRIPWIRES``."""

    assert "midnight_utc_timestamps_seen_advisory" not in ACTIONABLE_TRIPWIRES
    assert "open_tick_sameday_bar_advisory"        not in ACTIONABLE_TRIPWIRES


def test_legacy_keys_absent() -> None:
    """The old (un-suffixed) names must not coexist alongside the new ones."""

    result = compute_tripwires(
        as_of=datetime(2025, 9, 2, 13, 30, tzinfo=UTC),
        phase="open",
        per_domain={},
        wall_clock_fallback_fired=False,
    )

    assert "midnight_utc_timestamps_seen" not in result
    assert "open_tick_sameday_bar"        not in result

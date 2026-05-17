"""Unit tests for the ``ShortInterestSnapshot`` model."""
from __future__ import annotations

from datetime import date

from data.models.short_interest import ShortInterestSnapshot


def test_short_interest_snapshot_minimal() -> None:
    """ShortInterestSnapshot can be constructed with required fields only."""
    s = ShortInterestSnapshot(
        ticker="AAPL", settlement_date=date(2023, 2, 28),
        report_publish_date=date(2023, 3, 9), short_interest=100_000_000,
    )
    assert s.short_interest == 100_000_000
    assert s.days_to_cover is None
    assert s.average_daily_volume is None
    # Default source covers the v1-only synthesis path.
    assert s.source == "finra_regsho_synthesised"


def test_short_interest_snapshot_fully_populated() -> None:
    """ShortInterestSnapshot accepts all optional fields when supplied."""
    s = ShortInterestSnapshot(
        ticker="TSLA", settlement_date=date(2023, 2, 28),
        report_publish_date=date(2023, 3, 9), short_interest=50_000_000,
        average_daily_volume=10_000_000.0, days_to_cover=5.0,
        source="finra_regsho_synthesised",
    )
    assert s.days_to_cover == 5.0
    assert s.average_daily_volume == 10_000_000.0


def test_short_interest_snapshot_official_source() -> None:
    """The finra_official_snapshot source literal is accepted."""
    s = ShortInterestSnapshot(
        ticker="AAPL", settlement_date=date(2023, 2, 15),
        report_publish_date=date(2023, 2, 24), short_interest=80_000_000,
        source="finra_official_snapshot",
    )
    assert s.source == "finra_official_snapshot"


def test_short_interest_snapshot_round_trip() -> None:
    """ShortInterestSnapshot survives model_dump → model_validate round-trip."""
    s = ShortInterestSnapshot(
        ticker="GME", settlement_date=date(2023, 1, 31),
        report_publish_date=date(2023, 2, 1), short_interest=20_000_000,
        days_to_cover=2.5,
    )
    restored = ShortInterestSnapshot.model_validate(s.model_dump())
    assert restored == s

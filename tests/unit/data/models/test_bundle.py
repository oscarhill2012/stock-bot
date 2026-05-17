"""Unit tests for Phase 1 extensions to ``StockSignalBundle``."""
from __future__ import annotations

from datetime import UTC, date, datetime

from data.models.analyst_consensus import AnalystRating, AnalystRevision
from data.models.bundle import StockSignalBundle
from data.models.earnings import EarningsReport
from data.models.short_interest import ShortInterestSnapshot


def test_bundle_accepts_new_payload_fields() -> None:
    """New payload fields are accepted when fully populated."""
    b = StockSignalBundle(
        ticker="AAPL",
        generated_at=datetime(2023, 3, 10, tzinfo=UTC),
        earnings=[
            EarningsReport(
                ticker="AAPL", report_date=date(2023, 2, 2),
                fiscal_period="Q1 2023",
            ),
        ],
        analyst_consensus=AnalystRating(
            ticker="AAPL", as_of=date(2023, 3, 10),
        ),
        analyst_revisions=[
            AnalystRevision(
                ticker="AAPL", firm="GS", action="upgrade",
                event_date=date(2023, 3, 9),
            ),
        ],
        short_interest=ShortInterestSnapshot(
            ticker="AAPL", settlement_date=date(2023, 2, 28),
            report_publish_date=date(2023, 3, 9), short_interest=1_000_000,
        ),
    )
    assert len(b.earnings) == 1
    assert b.analyst_consensus is not None
    assert len(b.analyst_revisions) == 1
    assert b.short_interest.short_interest == 1_000_000


def test_bundle_new_fields_default_empty() -> None:
    """New payload fields default to None / [] — back-compat with existing traces."""
    b = StockSignalBundle(
        ticker="AAPL",
        generated_at=datetime(2023, 3, 10, tzinfo=UTC),
    )
    assert b.earnings == []
    assert b.analyst_consensus is None
    assert b.analyst_revisions == []
    assert b.short_interest is None


def test_bundle_new_fields_round_trip() -> None:
    """New payload fields survive model_dump → model_validate round-trip."""
    b = StockSignalBundle(
        ticker="MSFT",
        generated_at=datetime(2023, 3, 10, tzinfo=UTC),
        earnings=[
            EarningsReport(
                ticker="MSFT", report_date=date(2023, 1, 24),
                fiscal_period="Q2 FY2023", eps_actual=2.32,
            ),
        ],
        short_interest=ShortInterestSnapshot(
            ticker="MSFT", settlement_date=date(2023, 2, 28),
            report_publish_date=date(2023, 3, 1), short_interest=5_000_000,
        ),
    )
    restored = StockSignalBundle.model_validate(b.model_dump())
    assert restored == b

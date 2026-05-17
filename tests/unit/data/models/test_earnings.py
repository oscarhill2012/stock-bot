"""Unit tests for the ``EarningsReport`` and ``EarningsHistory`` models."""
from __future__ import annotations

from datetime import date

from data.models.earnings import EarningsHistory, EarningsReport


def test_earnings_report_minimal() -> None:
    """EarningsReport can be constructed with only required fields."""
    r = EarningsReport(
        ticker="AAPL", report_date=date(2023, 2, 2),
        fiscal_period="Q1 2023",
    )
    assert r.eps_actual is None
    assert r.eps_estimate is None
    assert r.surprise_pct is None


def test_earnings_report_fully_populated() -> None:
    """EarningsReport accepts all optional fields when supplied."""
    r = EarningsReport(
        ticker="AAPL", report_date=date(2023, 2, 2),
        fiscal_period="Q1 2023",
        eps_actual=1.88, eps_estimate=1.94,
        revenue_actual=117.15e9, revenue_estimate=121.10e9,
        surprise_pct=-3.1,
    )
    assert r.eps_actual == 1.88
    assert r.surprise_pct == -3.1


def test_earnings_history_wraps_reports() -> None:
    """EarningsHistory bundles a list of EarningsReport records."""
    h = EarningsHistory(ticker="AAPL", reports=[
        EarningsReport(
            ticker="AAPL", report_date=date(2023, 2, 2),
            fiscal_period="Q1 2023", eps_actual=1.88,
            eps_estimate=1.94, surprise_pct=-3.1,
        ),
    ])
    assert len(h.reports) == 1
    assert h.reports[0].eps_actual == 1.88


def test_earnings_history_empty_default() -> None:
    """EarningsHistory defaults to an empty reports list."""
    h = EarningsHistory(ticker="AAPL")
    assert h.reports == []


def test_earnings_report_round_trip() -> None:
    """EarningsReport survives model_dump → model_validate round-trip."""
    r = EarningsReport(
        ticker="MSFT", report_date=date(2023, 4, 25),
        fiscal_period="Q3 FY2023", eps_actual=2.45,
    )
    restored = EarningsReport.model_validate(r.model_dump())
    assert restored == r

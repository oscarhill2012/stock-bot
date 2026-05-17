"""Unit tests for Phase 1 extensions to ``CompanyRatios``."""
from __future__ import annotations

from datetime import date

from data.models.company_ratios import CompanyRatios


def test_company_ratios_accepts_new_fields() -> None:
    """All 10 new fundamental fields are accepted when populated."""
    r = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        peg=1.8, revenue_growth_yoy=0.07, profit_margin=0.25,
        debt_to_equity=1.5, roe=0.15, free_cash_flow=9.0e10,
        analyst_rating_avg=2.1, number_of_analyst_opinions=42,
        fifty_two_week_high=180.0, fifty_two_week_low=120.0,
    )
    assert r.peg == 1.8
    assert r.fifty_two_week_low == 120.0
    assert r.revenue_growth_yoy == 0.07
    assert r.number_of_analyst_opinions == 42


def test_company_ratios_new_fields_default_none() -> None:
    """Each new field defaults to None — back-compat with existing cached data."""
    r = CompanyRatios(ticker="AAPL", as_of=date(2023, 3, 10))
    for field_name in (
        "peg", "revenue_growth_yoy", "profit_margin", "debt_to_equity",
        "roe", "free_cash_flow", "analyst_rating_avg",
        "number_of_analyst_opinions", "fifty_two_week_high",
        "fifty_two_week_low",
    ):
        assert getattr(r, field_name) is None, f"{field_name} should default to None"


def test_company_ratios_new_fields_round_trip() -> None:
    """New fields survive model_dump → model_validate round-trip."""
    r = CompanyRatios(
        ticker="MSFT", as_of=date(2023, 3, 10),
        peg=2.1, fifty_two_week_high=350.0, fifty_two_week_low=220.0,
    )
    restored = CompanyRatios.model_validate(r.model_dump())
    assert restored == r

"""Unit tests for the ``CompanyRatios`` pydantic model."""
from __future__ import annotations

from data.models.company_ratios import CompanyRatios


def test_company_ratios_round_trip_with_all_fields() -> None:
    """A fully-populated CompanyRatios survives model_dump → model_validate."""
    cr = CompanyRatios(
        ticker="AAPL",
        long_name="Apple Inc.",
        sector="Technology",
        market_cap=3.0e12,
        trailing_pe=36.2,
        forward_pe=31.3,
        beta=1.25,
        dividend_yield=0.005,
        fifty_day_average=210.0,
        two_hundred_day_average=190.0,
        last_price=215.7,
    )

    payload = cr.model_dump()
    restored = CompanyRatios.model_validate(payload)
    assert restored == cr


def test_company_ratios_accepts_all_optionals_none() -> None:
    """Every fundamental field is optional — yfinance returns sparse data."""
    cr = CompanyRatios(ticker="ZZZZ")
    assert cr.market_cap is None
    assert cr.long_name is None

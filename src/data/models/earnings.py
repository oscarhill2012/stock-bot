"""Earnings report models — populated by the Finnhub earnings provider.

``EarningsReport`` captures one quarterly earnings event; ``EarningsHistory``
bundles a sequence of them for a single ticker, matching the
``<Bundle>`` / ``<History>`` pattern used elsewhere in ``src/data/models/``.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class EarningsReport(BaseModel):
    """One quarterly earnings report for a single ticker.

    Mirrors the surface Finnhub's ``earnings_calendar`` endpoint exposes.
    ``surprise_pct`` is computed by the provider where both ``eps_actual``
    and ``eps_estimate`` are available: ``(actual - estimate) / abs(estimate)``.

    All optional fields default to ``None`` — yfinance / Finnhub sometimes
    return partial data, and the provider normalises missing values rather
    than raising.

    Parameters
    ----------
    ticker:
        Upper-cased symbol this report belongs to.
    report_date:
        The calendar date the earnings were announced.
    fiscal_period:
        Human-readable fiscal label, e.g. ``"Q1 2023"`` or ``"FY2022"``.
    eps_actual:
        Reported earnings-per-share.
    eps_estimate:
        Consensus analyst EPS estimate prior to the report.
    revenue_actual:
        Reported total revenue in USD.
    revenue_estimate:
        Consensus analyst revenue estimate in USD.
    surprise_pct:
        EPS surprise as a signed percentage:
        ``(eps_actual - eps_estimate) / abs(eps_estimate) * 100``.
    """

    ticker: str
    report_date: date
    fiscal_period: str                       # e.g. "Q1 2023"

    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None      # USD
    revenue_estimate: float | None = None    # USD
    surprise_pct: float | None = None        # (actual - estimate) / abs(estimate) * 100


class EarningsHistory(BaseModel):
    """A bundle of recent earnings reports for one ticker.

    Matches the ``<Bundle>`` / ``<History>`` pattern used elsewhere in
    ``src/data/models/``.  ``reports`` is ordered newest-first by the
    Finnhub provider; consumers should not assume a particular ordering if
    data has been re-assembled from cache.

    Parameters
    ----------
    ticker:
        Upper-cased symbol all reports belong to.
    reports:
        Zero-or-more ``EarningsReport`` records for this ticker.
    """

    ticker: str
    reports: list[EarningsReport] = Field(default_factory=list)

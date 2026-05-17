"""Analyst consensus and revision models — populated by the yfinance
analyst_consensus provider.

``AnalystRating`` holds the consensus price-target + recommendation snapshot
for one ticker as of one date.  ``AnalystRevision`` captures a single
upgrade/downgrade/target event from one firm.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class AnalystRating(BaseModel):
    """Consensus snapshot for one ticker as of one date.

    Recommendation scale follows yfinance conventions:
    1.0 = Strong Buy, 2.0 = Buy, 3.0 = Hold, 4.0 = Underperform, 5.0 = Sell.

    All optional fields default to ``None``; yfinance returns sparse data for
    tickers with thin analyst coverage and the provider normalises missing
    values rather than raising.

    Parameters
    ----------
    ticker:
        Upper-cased symbol.
    as_of:
        Date this snapshot was fetched — the PIT gate for backtest cache
        lookups.
    target_high / target_low / target_mean / target_median:
        Price target distribution across all covering analysts.
    recommendation_mean:
        Mean numeric recommendation (1.0 = Strong Buy … 5.0 = Sell).
    number_of_analysts:
        Count of analysts contributing to the consensus.
    """

    ticker: str
    as_of: date

    target_high: float | None = None
    target_low: float | None = None
    target_mean: float | None = None
    target_median: float | None = None
    recommendation_mean: float | None = None
    number_of_analysts: int | None = None


class AnalystRevision(BaseModel):
    """One upgrade / downgrade / target change event from a single firm.

    ``action`` is normalised to a controlled set of literals by the provider.
    Raw firm-specific action strings (e.g. "Initiates Coverage", "Raised to")
    are mapped to the nearest literal; unrecognised strings map to
    ``"unknown"``.

    Parameters
    ----------
    ticker:
        Upper-cased symbol the revision applies to.
    firm:
        Name of the analyst firm (e.g. ``"Goldman Sachs"``).
    action:
        Normalised action — one of the seven controlled literals.
    from_grade / to_grade:
        Raw grade strings from the provider (e.g. ``"Neutral"``, ``"Buy"``).
        ``from_grade`` is ``None`` for initiations where no prior grade exists.
    event_date:
        Calendar date the revision was published.
    """

    ticker: str
    firm: str
    action: Literal[
        "upgrade", "downgrade", "initiate",
        "reiterate", "target_raise", "target_cut", "unknown",
    ]
    from_grade: str | None = None
    to_grade: str | None = None
    event_date: date

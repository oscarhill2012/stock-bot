"""``CompanyRatios`` — scalar fundamentals + summary stats for one ticker."""
from __future__ import annotations

from pydantic import BaseModel


class CompanyRatios(BaseModel):
    """Scalar company-level fundamentals + summary stats for one ticker.

    Replaces every non-history field of the retired ``StockStats`` model. The
    fifty-day and two-hundred-day moving averages live here (not in
    ``PriceHistory``) because yfinance serves them as scalars; they are summary
    statistics, not OHLCV bars.

    Every fundamental field is optional — yfinance returns sparse data for many
    tickers; the provider normalises non-finite floats to ``None``.

    Parameters
    ----------
    ticker:
        Upper-cased symbol the ratios belong to.
    long_name:
        Display name (e.g. ``"Apple Inc."``) when available.
    sector:
        GICS sector string when available.
    market_cap, trailing_pe, forward_pe, beta, dividend_yield,
    fifty_day_average, two_hundred_day_average, last_price:
        Self-explanatory fundamental scalars. ``last_price`` is the most recent
        trade price reported by yfinance.
    """

    ticker: str
    long_name: str | None = None
    sector: str | None = None

    market_cap: float | None              = None
    trailing_pe: float | None             = None
    forward_pe: float | None              = None
    beta: float | None                    = None
    dividend_yield: float | None          = None
    fifty_day_average: float | None       = None
    two_hundred_day_average: float | None = None
    last_price: float | None              = None

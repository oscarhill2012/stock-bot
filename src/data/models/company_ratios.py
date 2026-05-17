"""``CompanyRatios`` — scalar fundamentals + summary stats for one ticker."""
from __future__ import annotations

from datetime import date

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
    as_of:
        The date these ratios were retrieved — used as the point-in-time (PIT)
        gate in backtest cache lookups.
    long_name:
        Display name (e.g. ``"Apple Inc."``) when available.
    sector:
        GICS sector string when available.
    market_cap, trailing_pe, forward_pe, beta, dividend_yield,
    fifty_day_average, two_hundred_day_average, last_price:
        Self-explanatory fundamental scalars. ``last_price`` is the most recent
        trade price reported by yfinance.
    peg:
        PEG ratio — trailing PE divided by 5-year expected growth rate.
    revenue_growth_yoy:
        Year-over-year revenue growth as a decimal fraction (e.g. 0.07 = 7%).
    profit_margin:
        Net profit margin as a decimal fraction.
    debt_to_equity:
        Total debt divided by total shareholder equity.
    roe:
        Return on equity as a decimal fraction.
    free_cash_flow:
        Trailing twelve-month free cash flow in USD.
    analyst_rating_avg:
        Mean analyst recommendation — 1.0 = Strong Buy, 5.0 = Sell (yfinance
        scale). Populated by the stats/yfinance or pit_composite provider.
    number_of_analyst_opinions:
        Count of analyst opinions underlying ``analyst_rating_avg``.
    fifty_two_week_high:
        52-week intraday high price. Populated by the stats/yfinance provider.
    fifty_two_week_low:
        52-week intraday low price. Populated by the stats/yfinance provider.
    """

    ticker: str
    as_of: date | None = None

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

    # --- Phase 7 extensions (audit rows 1.5, 2.1) ---
    # Fundamental ratios populated by pit_composite or stats/yfinance provider.
    peg: float | None                        = None   # PEG ratio
    revenue_growth_yoy: float | None         = None   # year-on-year revenue growth
    profit_margin: float | None              = None   # net profit margin
    debt_to_equity: float | None             = None   # total debt / total equity
    roe: float | None                        = None   # return on equity
    free_cash_flow: float | None             = None   # TTM free cash flow (USD)
    analyst_rating_avg: float | None         = None   # 1.0 = Strong Buy … 5.0 = Sell
    number_of_analyst_opinions: int | None   = None   # analyst opinion count

    # 52-week extremes (audit 2.1) — populated by stats/yfinance provider.
    fifty_two_week_high: float | None        = None
    fifty_two_week_low: float | None         = None

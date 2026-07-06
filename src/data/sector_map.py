"""Static watchlist ticker → yfinance-style sector string mapping.

Sectors are effectively constant over our backtest windows (a company's GICS
sector does not change month-to-month), so a hard-coded map is both correct
and point-in-time safe — there is no date-dependent lookup to leak.

The sector *strings* are deliberately the exact keys of
``contract.extractors._sector_map.SECTOR_TO_ETF`` so that the downstream
technical extractor can resolve each ticker's SPDR sector ETF for the
``relative_strength_vs_sector_*`` features.  Any new watchlist ticker added
here MUST use one of those canonical sector strings.

Tickers absent from this map resolve to ``None`` (handled gracefully by the
provider — the ``sector`` field is simply left unpopulated).

Single source of truth for the watchlist sector classification; import from
here rather than re-declaring the mapping elsewhere.
"""
from __future__ import annotations

# Watchlist ticker → canonical yfinance sector string.
#
# Strings MUST match the keys of ``SECTOR_TO_ETF`` (verified against
# ``contract/extractors/_sector_map.py``) so the sector-ETF lookup resolves.
WATCHLIST_SECTORS: dict[str, str] = {
    # Technology
    "AAPL":  "Technology",
    "MSFT":  "Technology",
    "NVDA":  "Technology",
    "AMD":   "Technology",
    "AVGO":  "Technology",
    "CRM":   "Technology",

    # Communication Services
    "GOOGL": "Communication Services",
    "META":  "Communication Services",

    # Consumer Cyclical
    "AMZN":  "Consumer Cyclical",
    "TSLA":  "Consumer Cyclical",

    # Financial Services
    "JPM":   "Financial Services",
    "BAC":   "Financial Services",

    # Energy
    "XOM":   "Energy",
    "CVX":   "Energy",

    # Industrials
    "LMT":   "Industrials",
    "RTX":   "Industrials",

    # Healthcare
    "JNJ":   "Healthcare",
    "UNH":   "Healthcare",

    # Consumer Defensive
    "PG":    "Consumer Defensive",
    "WMT":   "Consumer Defensive",
}


def sector_for(symbol: str) -> str | None:
    """Return the canonical sector string for ``symbol``, or ``None`` if unknown.

    Parameters
    ----------
    symbol:
        Ticker symbol; case-insensitive (upper-cased before lookup).

    Returns
    -------
    str | None
        The yfinance-style sector string (a key of ``SECTOR_TO_ETF``) when the
        ticker is in the watchlist, otherwise ``None``.
    """
    return WATCHLIST_SECTORS.get(symbol.upper())

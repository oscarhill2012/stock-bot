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
#
# The map is deliberately a SUPERSET of the active watchlist: it carries both
# the current momentum universe and the retired baseline universe.  Entries for
# off-watchlist tickers are inert (nothing iterates this map — every read goes
# through ``sector_for``), so keeping both sets here means swapping the active
# universe in ``config/watchlist.json`` needs no corresponding edit in this
# file, and cannot silently null out ``company_ratios.sector``.
WATCHLIST_SECTORS: dict[str, str] = {
    # ══ Active universe — momentum/quality midcaps ══════════════════════════
    # Technology
    "LITE":  "Technology",             # Lumentum — optical components
    "CIEN":  "Technology",             # Ciena — optical networking

    # Healthcare
    "ARWR":  "Healthcare",             # Arrowhead Pharmaceuticals — RNAi therapeutics
    "ROIV":  "Healthcare",             # Roivant Sciences — biopharma holding company

    # Communication Services
    "WBD":   "Communication Services", # Warner Bros. Discovery — media
    "NYT":   "Communication Services", # New York Times — publishing

    # Basic Materials
    "ALB":   "Basic Materials",        # Albemarle — lithium / speciality chemicals
    "AA":    "Basic Materials",        # Alcoa — aluminium

    # Industrials
    "ATI":   "Industrials",            # ATI Inc. — speciality metals
    "FIX":   "Industrials",            # Comfort Systems USA — mechanical contracting

    # Energy
    "VAL":   "Energy",                 # Valaris — offshore drilling
    "FTI":   "Energy",                 # TechnipFMC — subsea energy services

    # Consumer Defensive
    "DAR":   "Consumer Defensive",     # Darling Ingredients — rendering / bioproducts
    "BG":    "Consumer Defensive",     # Bunge Global — agribusiness

    # Consumer Cyclical
    "TPR":   "Consumer Cyclical",      # Tapestry — accessories / luxury
    "FIVE":  "Consumer Cyclical",      # Five Below — discount retail

    # Financial Services
    "AMG":   "Financial Services",     # Affiliated Managers Group — asset management
    "CBOE":  "Financial Services",     # Cboe Global Markets — exchange operator

    # Utilities
    "EIX":   "Utilities",              # Edison International — electric utility

    # Real Estate
    "VTR":   "Real Estate",            # Ventas — healthcare REIT

    # ══ Retired baseline universe — inert unless swapped back in ════════════
    # Technology
    "MPWR":  "Technology",             # Monolithic Power Systems — semiconductors
    "FSLR":  "Technology",             # First Solar — solar / photovoltaics

    # Communication Services
    "OMC":   "Communication Services", # Omnicom Group — advertising

    # Consumer Cyclical
    "DECK":  "Consumer Cyclical",      # Deckers Outdoor — footwear
    "TSCO":  "Consumer Cyclical",      # Tractor Supply — specialty retail

    # Consumer Defensive
    "CLX":   "Consumer Defensive",     # Clorox — household products
    "HRL":   "Consumer Defensive",     # Hormel Foods — packaged foods

    # Financial Services
    "FDS":   "Financial Services",     # FactSet Research Systems — financial data
    "NDAQ":  "Financial Services",     # Nasdaq — exchange operator

    # Healthcare
    "RMD":   "Healthcare",             # ResMed — medical devices
    "DGX":   "Healthcare",             # Quest Diagnostics — diagnostics

    # Industrials
    "DOV":   "Industrials",            # Dover — diversified industrials
    "JBHT":  "Industrials",            # J.B. Hunt Transport — logistics

    # Basic Materials
    "CF":    "Basic Materials",        # CF Industries — nitrogen fertilisers
    "STLD":  "Basic Materials",        # Steel Dynamics — steel

    # Energy
    "DVN":   "Energy",                 # Devon Energy — oil & gas E&P
    "FANG":  "Energy",                 # Diamondback Energy — oil & gas E&P

    # Utilities
    "CMS":   "Utilities",              # CMS Energy — electric & gas utility
    "ATO":   "Utilities",              # Atmos Energy — natural gas utility

    # Real Estate
    "INVH":  "Real Estate",            # Invitation Homes — residential REIT
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

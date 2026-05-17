"""yfinance sector string → SPDR sector ETF symbol mapping.

Keys match the strings yfinance returns in ``CompanyRatios.sector``. Used by
the technical extractor to look up the per-ticker sector reference series out
of ``state["reference_prices"]`` (Phase 5 wiring — see plan Phase 5, Task 5.3).

The eleven sectors correspond to the standard SPDR sector ETF family that
covers the S&P 500 constituents.
"""
from __future__ import annotations

SECTOR_TO_ETF: dict[str, str] = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Energy":                 "XLE",
    "Healthcare":             "XLV",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Industrials":            "XLI",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Communication Services": "XLC",
}

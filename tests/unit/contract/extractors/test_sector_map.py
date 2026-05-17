"""Tests for the yfinance sector → SPDR ETF mapping helper."""
from __future__ import annotations

from contract.extractors._sector_map import SECTOR_TO_ETF


def test_sector_map_covers_eleven_spdr_sectors():
    """The mapping must contain exactly the eleven standard SPDR sector ETFs."""
    assert SECTOR_TO_ETF["Technology"] == "XLK"
    assert SECTOR_TO_ETF["Financial Services"] == "XLF"
    assert SECTOR_TO_ETF["Energy"] == "XLE"
    assert SECTOR_TO_ETF["Healthcare"] == "XLV"
    assert SECTOR_TO_ETF["Consumer Cyclical"] == "XLY"
    assert SECTOR_TO_ETF["Consumer Defensive"] == "XLP"
    assert SECTOR_TO_ETF["Industrials"] == "XLI"
    assert SECTOR_TO_ETF["Basic Materials"] == "XLB"
    assert SECTOR_TO_ETF["Real Estate"] == "XLRE"
    assert SECTOR_TO_ETF["Utilities"] == "XLU"
    assert SECTOR_TO_ETF["Communication Services"] == "XLC"
    assert len(SECTOR_TO_ETF) == 11

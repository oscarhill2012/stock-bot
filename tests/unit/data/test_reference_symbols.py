"""Canonical reference-symbol tuple lives in one module only."""
from data.reference_symbols import REFERENCE_SYMBOLS


def test_reference_symbols_contains_spy_and_eleven_sector_etfs():
    """SPY plus 11 SPDR sector ETFs — 12 symbols total, ordered deterministically."""
    assert REFERENCE_SYMBOLS[0] == "SPY"                              # broad-market benchmark first
    assert len(REFERENCE_SYMBOLS) == 12                               # SPY + 11 SPDR sector ETFs
    assert set(REFERENCE_SYMBOLS[1:]) == {
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
        "XLI", "XLB", "XLRE", "XLU", "XLC",
    }


def test_reference_symbols_is_an_immutable_tuple():
    """Tuple — not list — so call sites cannot accidentally mutate the canonical order."""
    assert isinstance(REFERENCE_SYMBOLS, tuple)

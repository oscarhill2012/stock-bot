"""Canonical reference-symbol tuple — SPY plus the 11 SPDR sector ETFs.

These symbols are fetched once per tick (live) and once per backtest window
(backtest) as market and sector benchmarks.  They are NOT in the watchlist;
they exist solely so the technical extractor can compute
``relative_strength_vs_spy_*`` and ``relative_strength_vs_sector_*`` features
without issuing per-ticker network calls.

Single source of truth — previously duplicated across ``orchestrator.tick``,
``scripts.backtest_fetch``, and ``backtest.runner``.  Any new consumer must
import from here.
"""
from __future__ import annotations

# SPY is the broad-market benchmark; the 11 SPDR sector ETFs cover every
# S&P 500 constituent sector.  Order is deterministic so tests can compare
# against a fixed expected list.
REFERENCE_SYMBOLS: tuple[str, ...] = (
    "SPY",                                                  # broad-market benchmark
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",              # SPDR sector ETFs (batch 1)
    "XLI", "XLB", "XLRE", "XLU", "XLC",                     # SPDR sector ETFs (batch 2)
)

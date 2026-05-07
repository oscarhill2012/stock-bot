"""Fundamental analyst output schema — thin subclass of AnalystSignal."""
from __future__ import annotations

from agents.analysts._common import AnalystSignal


class FundamentalSignal(AnalystSignal):
    """Filing / valuation-based signal for one ticker."""
    pass

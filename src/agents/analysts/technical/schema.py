"""Technical analyst output schema — thin subclass of AnalystSignal."""
from __future__ import annotations

from agents.analysts._common import AnalystSignal


class TechnicalSignal(AnalystSignal):
    """OHLCV + indicator-based signal for one ticker."""
    pass

"""Unit tests for the ``PriceHistory`` pydantic model."""
from __future__ import annotations

from datetime import datetime

from data.models import OHLCBar
from data.models.price_history import PriceHistory


def _bar(ts: str, close: float) -> OHLCBar:
    """Build a minimal OHLCBar for testing."""
    return OHLCBar(
        timestamp=datetime.fromisoformat(ts),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


def test_price_history_round_trips_through_model_dump() -> None:
    """A model_dump round-trip preserves ticker and bars."""
    ph = PriceHistory(
        ticker="AAPL",
        bars=[_bar("2026-05-01T00:00:00", 100.0), _bar("2026-05-02T00:00:00", 101.0)],
    )

    payload = ph.model_dump()
    assert payload["ticker"] == "AAPL"
    assert len(payload["bars"]) == 2

    restored = PriceHistory.model_validate(payload)
    assert restored == ph


def test_price_history_accepts_empty_bars() -> None:
    """An empty history is a valid state — e.g. an unknown ticker."""
    ph = PriceHistory(ticker="ZZZZ", bars=[])
    assert ph.bars == []

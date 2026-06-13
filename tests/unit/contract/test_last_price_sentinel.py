"""One sentinel for last_price: None means absent, every concrete value is positive.

Tests the constraint that TickerEvidence.last_price is
``float | None = Field(gt=0, allow_inf_nan=False)``, making None the sole
"no price" sentinel and rejecting 0.0 / negatives / infinities / NaN at the
schema boundary rather than silently propagating them downstream.

The explicit ``allow_inf_nan=False`` is load-bearing: Pydantic's bare
``PositiveFloat`` would accept ``inf`` (its ``allow_inf_nan`` defaults True),
so a degenerate price feed emitting ``inf`` would otherwise slip through.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _make_evidence(*, last_price):
    """Build a minimal TickerEvidence with the given last_price under test.

    All other fields are set to sensible neutral values so the only variable
    is the last_price argument — isolating the constraint under test.

    Args:
        last_price: The value to assign to TickerEvidence.last_price.

    Returns:
        A constructed TickerEvidence instance (or raises ValidationError).
    """
    return TickerEvidence(
        ticker      = "AAPL",
        tick_id     = "T-1",
        recorded_at = datetime(2026, 1, 1, tzinfo=UTC),
        per_analyst = {},
        aggregate   = AggregateVerdict(
            lean         = "neutral",
            magnitude    = 0.0,
            confidence   = 0.0,
            disagreement = 0.0,
            summary      = "0/0",
        ),
        weights     = {},
        last_price  = last_price,
    )


def test_last_price_none_is_accepted():
    """None remains the canonical 'no price available' sentinel."""
    ev = _make_evidence(last_price=None)
    assert ev.last_price is None


def test_last_price_positive_float_is_accepted():
    """Any positive float passes."""
    ev = _make_evidence(last_price=123.45)
    assert ev.last_price == 123.45


def test_last_price_zero_raises():
    """0.0 is no longer a silent 'no price' sentinel — must be coerced to None upstream."""
    with pytest.raises(ValidationError):
        _make_evidence(last_price=0.0)


def test_last_price_negative_raises():
    """Negative prices have never been valid — assert the constraint is live."""
    with pytest.raises(ValidationError):
        _make_evidence(last_price=-1.0)


def test_last_price_positive_infinity_raises():
    """+inf must raise — bare ``PositiveFloat`` would have accepted it.

    This is the case that motivated the explicit ``allow_inf_nan=False``: a
    degenerate price feed producing ``float('inf')`` (e.g. a divide-by-zero
    upstream) must fail loudly at the schema boundary, not propagate an
    infinite "price" into the strategist's renderer.
    """
    with pytest.raises(ValidationError):
        _make_evidence(last_price=math.inf)


def test_last_price_nan_raises():
    """NaN must raise — it is neither absent (None) nor a usable positive price."""
    with pytest.raises(ValidationError):
        _make_evidence(last_price=math.nan)

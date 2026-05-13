"""Round-trip and rejection tests for the extended insider trade models."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from data.models import Form4Bundle, InsiderDerivativeTrade, InsiderTrade


def _common_kwargs() -> dict:
    """Minimum required kwargs for an InsiderTrade."""
    return {
        "ticker": "AAPL",
        "insider_name": "Tim Cook",
        "insider_title": "CEO",
        "side": "buy",
        "shares": 1000.0,
        "price_per_share": 175.5,
        "transaction_date": date(2026, 5, 1),
        "filed_at": datetime(2026, 5, 2, tzinfo=UTC),
        "form_type": "4",
    }


def test_insider_trade_round_trip_with_new_fields() -> None:
    """InsiderTrade preserves transaction_code, is_10b5_1, footnote round-trip."""
    payload = _common_kwargs() | {
        "transaction_code": "P",
        "is_10b5_1": True,
        "footnote": "Sale pursuant to Rule 10b5-1 plan adopted 2025-12-01.",
    }
    t = InsiderTrade.model_validate(payload)
    assert t.transaction_code == "P"
    assert t.is_10b5_1 is True
    assert t.footnote is not None
    assert t.model_dump(mode="json")["transaction_code"] == "P"


def test_insider_trade_defaults_new_fields_to_none_or_false() -> None:
    """Omitting new fields keeps backwards-compatible defaults."""
    t = InsiderTrade.model_validate(_common_kwargs())
    assert t.transaction_code is None
    assert t.is_10b5_1 is False
    assert t.footnote is None


def test_insider_trade_rejects_unknown_field() -> None:
    """extra='forbid' rejects stray keys."""
    payload = _common_kwargs() | {"some_unknown_field": 1}
    with pytest.raises(ValidationError):
        InsiderTrade.model_validate(payload)


def test_insider_derivative_trade_round_trip() -> None:
    """InsiderDerivativeTrade round-trips strike, type, footnote."""
    payload = {
        "ticker": "MSFT", "insider_name": "Satya Nadella",
        "insider_title": "CEO", "side": "buy",
        "derivative_type": "option",
        "underlying_shares": 500.0, "strike_price": 200.0,
        "transaction_date": date(2026, 4, 12),
        "filed_at": datetime(2026, 4, 13, tzinfo=UTC),
        "transaction_code": "M", "is_10b5_1": False,
        "footnote": "Exercise of stock option granted 2020-01-01.",
    }
    t = InsiderDerivativeTrade.model_validate(payload)
    assert t.derivative_type == "option"
    assert t.strike_price == 200.0


def test_form4_bundle_wraps_both_lists() -> None:
    """Form4Bundle holds parallel trades + derivatives lists."""
    bundle = Form4Bundle(trades=[], derivatives=[])
    assert bundle.trades == []
    assert bundle.derivatives == []

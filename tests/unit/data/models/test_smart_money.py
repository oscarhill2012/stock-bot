"""Unit tests for SmartMoneyRaw — the per-ticker smart-money aggregate."""

import pytest
from pydantic import ValidationError

from data.models.smart_money import SmartMoneyRaw


def test_smart_money_raw_constructs_empty() -> None:
    """SmartMoneyRaw with no kwargs has empty lists, not None."""
    raw = SmartMoneyRaw()
    assert raw.politicians == []
    assert raw.notable_holders == []


def test_smart_money_raw_rejects_unknown_field() -> None:
    """extra='forbid' surfaces typos at construction time."""
    with pytest.raises(ValidationError):
        SmartMoneyRaw(politicans=[])  # typo (missing 'i')

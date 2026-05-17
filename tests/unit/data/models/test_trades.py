"""Unit tests for Phase 1 extensions to trade disclosure models.

Covers:
- Task 1.3: InsiderTrade reporter-flag booleans
- Task 1.4: InsiderDerivativeTrade Table II extras
- Task 1.6: NotableHolder body-parsed fields
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from data.models.trades import InsiderDerivativeTrade, InsiderTrade, NotableHolder

# ---------------------------------------------------------------------------
# Task 1.3 — InsiderTrade reporter flags
# ---------------------------------------------------------------------------

def test_insider_trade_reporter_flags() -> None:
    """Reporter-flag booleans are accepted and stored correctly."""
    t = InsiderTrade(
        ticker="AAPL", side="buy", shares=1000, price_per_share=100.0,
        insider_name="Doe, Jane", insider_title="CFO",
        transaction_code="P", transaction_date=date(2023, 3, 10),
        filed_at=datetime(2023, 3, 11, tzinfo=UTC),
        form_type="4",
        is_officer=True, is_director=False, is_ten_percent_owner=False,
    )
    assert t.is_officer is True
    assert t.is_director is False
    assert t.is_ten_percent_owner is False


def test_insider_trade_reporter_flags_default_false() -> None:
    """Reporter flags default to False — back-compat with existing cache data."""
    t = InsiderTrade(
        ticker="AAPL", side="buy", shares=1, price_per_share=1.0,
        insider_name="X", insider_title="Y", transaction_code="P",
        transaction_date=date(2023, 1, 1),
        filed_at=datetime(2023, 1, 2, tzinfo=UTC),
        form_type="4",
    )
    assert t.is_officer is False
    assert t.is_director is False
    assert t.is_ten_percent_owner is False


def test_insider_trade_reporter_flags_round_trip() -> None:
    """Reporter flags survive model_dump → model_validate round-trip."""
    t = InsiderTrade(
        ticker="MSFT", side="sell", shares=500, price_per_share=300.0,
        insider_name="Smith, Bob", insider_title="CEO",
        transaction_code="S", transaction_date=date(2023, 3, 10),
        filed_at=datetime(2023, 3, 11, tzinfo=UTC),
        form_type="4",
        is_officer=True, is_director=True, is_ten_percent_owner=False,
    )
    restored = InsiderTrade.model_validate(t.model_dump())
    assert restored == t


# ---------------------------------------------------------------------------
# Task 1.4 — InsiderDerivativeTrade Table II extras
# ---------------------------------------------------------------------------

def test_insider_derivative_table_ii_extras() -> None:
    """Table II extra fields are accepted when populated."""
    d = InsiderDerivativeTrade(
        ticker="AAPL", insider_name="Doe", insider_title="CFO",
        side="buy",
        transaction_code="A", derivative_type="option",
        underlying_shares=500.0, strike_price=120.0,
        transaction_date=date(2023, 3, 10),
        filed_at=datetime(2023, 3, 11, tzinfo=UTC),
        expiration_date=date(2033, 3, 10),
        is_indirect_ownership=True, is_late_filed=False,
    )
    assert d.expiration_date.year == 2033
    assert d.is_indirect_ownership is True
    assert d.is_late_filed is False


def test_insider_derivative_table_ii_defaults() -> None:
    """Table II extras default to None/False — back-compat."""
    d = InsiderDerivativeTrade(
        ticker="AAPL", insider_name="Doe", insider_title="CFO",
        side="buy",
        transaction_code="A", derivative_type="option",
        underlying_shares=500.0, strike_price=120.0,
        transaction_date=date(2023, 3, 10),
        filed_at=datetime(2023, 3, 11, tzinfo=UTC),
    )
    assert d.expiration_date is None
    assert d.is_indirect_ownership is False
    assert d.is_late_filed is False


def test_insider_derivative_table_ii_round_trip() -> None:
    """Table II extras survive model_dump → model_validate round-trip."""
    d = InsiderDerivativeTrade(
        ticker="TSLA", insider_name="Musk", insider_title="CEO",
        side="buy",
        transaction_code="A", derivative_type="option",
        underlying_shares=1000.0, strike_price=50.0,
        transaction_date=date(2023, 1, 15),
        filed_at=datetime(2023, 1, 16, tzinfo=UTC),
        expiration_date=date(2030, 1, 15),
        is_indirect_ownership=False, is_late_filed=True,
    )
    restored = InsiderDerivativeTrade.model_validate(d.model_dump())
    assert restored == d


# ---------------------------------------------------------------------------
# Task 1.6 — NotableHolder body-parsed fields
# ---------------------------------------------------------------------------

def test_notable_holder_body_fields() -> None:
    """Body-parsed cover-page fields are accepted when populated."""
    h = NotableHolder(
        ticker="AAPL", holder="Activist LP", form_type="SC 13D",
        filed_at=datetime(2023, 3, 10, tzinfo=UTC),
        accession_no="x", intent="active", is_amendment=False,
        percent_of_class=8.7, shares_held=1_000_000.0,
        purpose_excerpt="Acquired for investment purposes...",
    )
    assert h.percent_of_class == 8.7
    assert h.shares_held == 1_000_000.0
    assert "investment" in h.purpose_excerpt


def test_notable_holder_body_fields_default_none() -> None:
    """Body-parsed fields default to None — back-compat."""
    h = NotableHolder(
        ticker="GOOG", holder="Vanguard", form_type="SC 13G",
        filed_at=datetime(2023, 3, 10, tzinfo=UTC),
        accession_no="y",
    )
    assert h.percent_of_class is None
    assert h.shares_held is None
    assert h.purpose_excerpt is None


def test_notable_holder_body_fields_round_trip() -> None:
    """Body-parsed fields survive model_dump → model_validate round-trip."""
    h = NotableHolder(
        ticker="MSFT", holder="BlackRock", form_type="SC 13G",
        filed_at=datetime(2023, 3, 10, tzinfo=UTC),
        accession_no="z", intent="passive",
        percent_of_class=5.2, shares_held=500_000.0,
    )
    restored = NotableHolder.model_validate(h.model_dump())
    assert restored == h

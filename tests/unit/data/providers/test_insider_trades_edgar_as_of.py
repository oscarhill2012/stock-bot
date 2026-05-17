"""Tests for ``insider_trades/edgar.py`` — ``fetch`` PIT window and build helpers.

Covers:
- ``fetch`` deriving the lookback window from ``as_of``, not wall-clock time.
- ``fetch`` swallowing unrecognised kwargs from the registry dispatcher.
- ``_build_trade`` surfacing reporter flags (``isOfficer``, ``isDirector``,
  ``isTenPercentOwner``) from the Form 4 ``reportingOwnerRelationship`` block.
- ``_build_derivative`` surfacing Table II extras (``expiration_date``,
  ``is_indirect_ownership``, ``is_late_filed``) and reporter flags.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

# ---------------------------------------------------------------------------
# Shared XML fixtures — minimal Form 4 documents covering the fields under test.
# ---------------------------------------------------------------------------

@pytest.fixture()
def form4_xml_with_officer() -> ET.Element:
    """Minimal Form 4 XML with an officer reporter and one non-derivative row."""
    return ET.fromstring("""<?xml version="1.0"?>
    <ownershipDocument>
      <reportingOwner>
        <reportingOwnerRelationship>
          <isOfficer>1</isOfficer>
          <isDirector>0</isDirector>
          <isTenPercentOwner>0</isTenPercentOwner>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2023-03-05</value></transactionDate>
          <transactionAmounts>
            <transactionShares><value>1000</value></transactionShares>
            <transactionPricePerShare><value>180.00</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
          <transactionCoding>
            <transactionCode>P</transactionCode>
          </transactionCoding>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>""")


@pytest.fixture()
def form4_xml_with_derivative() -> ET.Element:
    """Minimal Form 4 XML with an officer reporter and one derivative row."""
    return ET.fromstring("""<?xml version="1.0"?>
    <ownershipDocument>
      <reportingOwner>
        <reportingOwnerRelationship>
          <isOfficer>1</isOfficer>
        </reportingOwnerRelationship>
      </reportingOwner>
      <derivativeTable>
        <derivativeTransaction>
          <securityTitle><value>Stock Option (Right to Buy)</value></securityTitle>
          <conversionOrExercisePrice><value>120.0</value></conversionOrExercisePrice>
          <transactionDate><value>2023-03-05</value></transactionDate>
          <expirationDate><value>2033-03-05</value></expirationDate>
          <underlyingSecurity>
            <underlyingSecurityShares><value>500</value></underlyingSecurityShares>
          </underlyingSecurity>
          <ownershipNature>
            <directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>
          </ownershipNature>
          <transactionCoding>
            <transactionCode>A</transactionCode>
          </transactionCoding>
        </derivativeTransaction>
      </derivativeTable>
    </ownershipDocument>""")


# ---------------------------------------------------------------------------
# Helpers — build minimal row dicts and form4 stubs for the builder functions.
# ---------------------------------------------------------------------------

def _trade_row() -> dict:
    """Minimal row dict for a common-stock purchase (matches form4_xml_with_officer)."""
    return {
        "Shares":          1000.0,
        "Price":           180.00,
        "Date":            date(2023, 3, 5),
        "transaction_code": "P",
    }


def _derivative_row() -> dict:
    """Minimal row dict for a derivative grant (matches form4_xml_with_derivative).

    ``expiration_date`` and ``direct_or_indirect_ownership`` are intentionally
    omitted here so the XML fallback path in ``_build_derivative`` is exercised.
    """
    return {
        "underlying_shares": 500.0,
        "strike_price":      120.0,
        "transaction_date":  date(2023, 3, 5),
        "transaction_code":  "A",
    }


# ---------------------------------------------------------------------------
# fetch() — PIT window and kwargs passthrough
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_lookback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` must derive the filing-date window from ``as_of``, not wall-clock today."""
    import data.providers.insider_trades.edgar as mod

    captured: dict = {}

    def fake_list(symbol: str, lookback_days: int, as_of: datetime) -> list:
        captured["symbol"]        = symbol
        captured["lookback_days"] = lookback_days
        captured["as_of"]         = as_of
        return []

    monkeypatch.setattr(mod, "_list_form4_filings", fake_list)

    await mod.fetch(
        "AAPL",
        lookback_days=30,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["symbol"]        == "AAPL"
    assert captured["lookback_days"] == 30
    assert captured["as_of"]         == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fetch_swallows_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider must accept extra kwargs other providers care about (``**_unused``)."""
    import data.providers.insider_trades.edgar as mod

    monkeypatch.setattr(mod, "_list_form4_filings", lambda s, days, a: [])

    # ``from_date`` is meaningless to insider_trades but news providers take it —
    # the registry dispatches the same kwargs to every domain.
    result = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )

    assert result.trades == []


# ---------------------------------------------------------------------------
# _build_trade() — reporter flags (audit 2.5)
# ---------------------------------------------------------------------------

def test_insider_trades_surfaces_reporter_flags(
    form4_xml_with_officer: ET.Element,
) -> None:
    """``_build_trade`` must populate ``is_officer``, ``is_director``, and
    ``is_ten_percent_owner`` from the Form 4 ``reportingOwnerRelationship`` block.

    Here ``form4`` is the XML element root — ``_reporter_flags`` handles that path.
    """
    import data.providers.insider_trades.edgar as mod

    filed_at = datetime(2023, 3, 6, 12, 0, tzinfo=UTC)
    out: list = []

    mod._build_trade(
        row=_trade_row(),
        form4=form4_xml_with_officer,
        form_insider="Jane Smith",
        form_title=None,
        side="buy",
        symbol="AAPL",
        form_type="4",
        filed_at=filed_at,
        filed_date=filed_at.date(),
        out=out,
    )

    assert len(out) == 1, "Expected exactly one InsiderTrade to be appended."
    trade = out[0]
    assert trade.is_officer is True,            "isOfficer=1 should parse to True"
    assert trade.is_director is False,          "isDirector=0 should parse to False"
    assert trade.is_ten_percent_owner is False, "isTenPercentOwner=0 should parse to False"


def test_insider_trades_reporter_flags_default_false_when_absent() -> None:
    """``_build_trade`` defaults all reporter flags to False when the XML block is missing."""
    import data.providers.insider_trades.edgar as mod

    # Form4 stub with no reportingOwner block.
    bare_form4 = SimpleNamespace(
        footnotes={},
        equity_swap_or_planned_sale=False,
        ticker="AAPL",
        form_type="4",
        filed_at=datetime(2023, 3, 6, tzinfo=UTC),
    )
    out: list = []

    mod._build_trade(
        row=_trade_row(),
        form4=bare_form4,
        form_insider="Jane Smith",
        form_title=None,
        side="buy",
        symbol="AAPL",
        form_type="4",
        filed_at=datetime(2023, 3, 6, tzinfo=UTC),
        filed_date=date(2023, 3, 6),
        out=out,
    )

    assert len(out) == 1
    trade = out[0]
    assert trade.is_officer is False
    assert trade.is_director is False
    assert trade.is_ten_percent_owner is False


# ---------------------------------------------------------------------------
# _build_derivative() — Table II extras (audit 2.6)
# ---------------------------------------------------------------------------

def test_insider_derivative_table_ii_extras(
    form4_xml_with_derivative: ET.Element,
) -> None:
    """``_build_derivative`` must populate ``expiration_date``, ``is_indirect_ownership``,
    ``is_late_filed``, ``is_officer``, and ``is_director`` from the derivative XML.

    ``expiration_date`` and ``direct_or_indirect_ownership`` are not in the row dict,
    so the XML fallback path is exercised.  Filed 2023-03-06 for a 2023-03-05 transaction
    is one business day — within the 2-day window — so ``is_late_filed`` must be False.
    """
    import data.providers.insider_trades.edgar as mod

    filed_at = datetime(2023, 3, 6, 12, 0, tzinfo=UTC)
    out: list = []

    mod._build_derivative(
        row=_derivative_row(),
        form4=form4_xml_with_derivative,
        form_insider="Jane Smith",
        form_title=None,
        symbol="AAPL",
        filed_at=filed_at,
        filed_date=filed_at.date(),
        out=out,
    )

    assert len(out) == 1, "Expected exactly one InsiderDerivativeTrade to be appended."
    deriv = out[0]
    assert deriv.expiration_date      == date(2033, 3, 5), "Expiration date must parse from XML"
    assert deriv.is_indirect_ownership is True,             "DirectOrIndirect=I → indirect"
    assert deriv.is_late_filed         is False,            "1 business day ≤ 2 → not late"
    assert deriv.is_officer            is True,             "isOfficer=1 → True"
    assert deriv.is_director           is False,            "isDirector absent → default False"


def test_insider_derivative_is_late_filed_when_beyond_two_days() -> None:
    """``_build_derivative`` marks ``is_late_filed=True`` when filed > 2 business days late."""
    import data.providers.insider_trades.edgar as mod

    # Transaction 2023-03-01 (Wed), filed 2023-03-06 (Mon) = 3 business days → late.
    bare_form4 = SimpleNamespace(
        footnotes={},
        equity_swap_or_planned_sale=False,
    )
    row = {
        "underlying_shares": 200.0,
        "transaction_date":  date(2023, 3, 1),
        "direct_or_indirect_ownership": "D",
    }
    filed_at = datetime(2023, 3, 6, 9, 0, tzinfo=UTC)
    out: list = []

    mod._build_derivative(
        row=row,
        form4=bare_form4,
        form_insider="Bob Jones",
        form_title=None,
        symbol="MSFT",
        filed_at=filed_at,
        filed_date=filed_at.date(),
        out=out,
    )

    assert len(out) == 1
    assert out[0].is_late_filed is True


# ---------------------------------------------------------------------------
# _business_days_between() — unit tests for the helper directly
# ---------------------------------------------------------------------------

def test_business_days_between_same_week() -> None:
    """1 weekday apart → 1 business day."""
    from data.providers.insider_trades.edgar import _business_days_between

    # Monday → Tuesday
    assert _business_days_between(date(2023, 3, 6), date(2023, 3, 7)) == 1


def test_business_days_between_spans_weekend() -> None:
    """Friday → following Monday skips Saturday and Sunday → 1 business day."""
    from data.providers.insider_trades.edgar import _business_days_between

    assert _business_days_between(date(2023, 3, 3), date(2023, 3, 6)) == 1


def test_business_days_between_three_days() -> None:
    """Wed → Mon (skipping Sat/Sun) = 3 business days (Thu, Fri, Mon)."""
    from data.providers.insider_trades.edgar import _business_days_between

    assert _business_days_between(date(2023, 3, 1), date(2023, 3, 6)) == 3


def test_business_days_between_b_before_a() -> None:
    """When end ≤ start, result is 0 (never negative)."""
    from data.providers.insider_trades.edgar import _business_days_between

    assert _business_days_between(date(2023, 3, 6), date(2023, 3, 5)) == 0
    assert _business_days_between(date(2023, 3, 6), date(2023, 3, 6)) == 0

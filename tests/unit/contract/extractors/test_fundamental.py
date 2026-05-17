"""Fundamental feature extractor tests — Tier 1, no LLM.

Phase 5 update: the extractor now accepts a triad payload shape
``{"ratios": dict, "filings": list, "insider": Form4Bundle}``.  Fixtures and
tests have been updated accordingly; the locked key catalogue now includes
insider and filings-derived columns.

Phase 5 data-model split: the ``"stats"`` key is renamed ``"ratios"`` at the
fetch-callback and extractor levels.  Fixture wrappers updated here.

Phase 7 (Task 2.4–2.8): new tests verify per-code insider aggregates,
reporter-flag senior-officer weighting, derivative features, and 8-K counters.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from contract.extractors.fundamental import _KEYS, extract_fundamental_features
from data.models import Form4Bundle
from data.models.filings import Filing
from data.models.trades import InsiderDerivativeTrade, InsiderTrade

FIXTURE = Path("tests/fixtures/contract/fundamental_aapl.json")


@pytest.fixture
def aapl_data():
    """Load the AAPL fixture and wrap it in the Phase 5 triad shape.

    Uses ``"ratios"`` key (renamed from ``"stats"`` in the Phase 5 data-model split).
    """
    ratios = json.loads(FIXTURE.read_text())
    return {
        "ratios": ratios,
        "filings": [],
        "insider": Form4Bundle(trades=[], derivatives=[]),
    }


def test_extracts_required_keys(aapl_data):
    """The returned dict must contain exactly the keys declared in _KEYS."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    assert set(features.keys()) == set(_KEYS)


def test_all_features_are_floats(aapl_data):
    """Every value in the feature dict must be a plain float."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r}"


def test_pe_values_carried_through(aapl_data):
    """P/E values from the ratios sub-dict must survive extraction unchanged."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    assert features["pe_trailing"] == pytest.approx(28.5)
    assert features["pe_forward"] == pytest.approx(26.0)


def test_fcf_yield_computed_from_fcf_and_market_cap(aapl_data):
    """fcf_yield_pct = (fcf / market_cap) × 100 using ratios sub-dict values."""
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    expected = (95_000_000_000 / 3_000_000_000_000) * 100
    assert features["fcf_yield_pct"] == pytest.approx(expected, rel=0.01)


def test_handles_empty_data_gracefully():
    """An entirely empty raw dict must return all-zero features without error."""
    features = extract_fundamental_features({}, ticker="AAPL")
    for v in features.values():
        assert v == 0.0


def test_handles_zero_market_cap_in_fcf_yield():
    """Zero market cap in ratios must not raise ZeroDivisionError."""
    features = extract_fundamental_features(
        {
            "ratios": {"free_cash_flow": 1_000_000, "market_cap": 0},
            "filings": [],
            "insider": Form4Bundle(trades=[], derivatives=[]),
        },
        ticker="AAPL",
    )
    assert features["fcf_yield_pct"] == 0.0


# ---------------------------------------------------------------------------
# Task 2.4 — Fix D: fundamental extractor wires 8 ratio fields
# ---------------------------------------------------------------------------

def test_fundamental_emits_eight_ratio_features():
    """All eight Phase 7 ratio fields must be extracted from raw['ratios']."""
    from data.models.company_ratios import CompanyRatios

    r = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        peg=1.8, revenue_growth_yoy=0.07, profit_margin=0.25,
        debt_to_equity=1.5, roe=0.15, free_cash_flow=9.0e10,
        analyst_rating_avg=2.1, number_of_analyst_opinions=42,
    )
    raw = {"ticker": "AAPL", "ratios": r.model_dump()}
    features = extract_fundamental_features(raw, state={})
    assert features["peg"] == pytest.approx(1.8)
    assert features["revenue_growth_yoy"] == pytest.approx(0.07)
    assert features["profit_margin"] == pytest.approx(0.25)
    assert features["debt_to_equity"] == pytest.approx(1.5)
    assert features["roe"] == pytest.approx(0.15)
    assert features["free_cash_flow"] == pytest.approx(9.0e10)
    assert features["analyst_rating_avg"] == pytest.approx(2.1)
    assert features["number_of_analyst_opinions"] == pytest.approx(42)


# ---------------------------------------------------------------------------
# Task 2.5 — Fix E: split insider net dollars into per-code aggregates
# ---------------------------------------------------------------------------

def test_fundamental_splits_insider_dollars_by_transaction_code():
    """Per-code aggregates must accumulate correctly for P/S/F/G codes."""
    trades = [
        InsiderTrade(
            ticker="AAPL", side="buy", shares=1000, price_per_share=100,
            insider_name="A", insider_title="CFO", transaction_code="P",
            transaction_date=date(2023, 3, 5),
            filed_at=datetime(2023, 3, 6, tzinfo=UTC),
            form_type="4",
        ).model_dump(),
        InsiderTrade(
            ticker="AAPL", side="sell", shares=500, price_per_share=100,
            insider_name="B", insider_title="CEO", transaction_code="S",
            transaction_date=date(2023, 3, 6),
            filed_at=datetime(2023, 3, 7, tzinfo=UTC),
            form_type="4",
        ).model_dump(),
        InsiderTrade(
            ticker="AAPL", side="sell", shares=200, price_per_share=100,
            insider_name="C", insider_title="GC", transaction_code="F",
            transaction_date=date(2023, 3, 7),
            filed_at=datetime(2023, 3, 8, tzinfo=UTC),
            form_type="4",
        ).model_dump(),
        InsiderTrade(
            ticker="AAPL", side="buy", shares=10, price_per_share=100,
            insider_name="D", insider_title="VP", transaction_code="G",
            transaction_date=date(2023, 3, 8),
            filed_at=datetime(2023, 3, 9, tzinfo=UTC),
            form_type="4",
        ).model_dump(),
    ]
    raw = {"ticker": "AAPL", "insider_trades": trades, "ratios": {}}
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["insider_open_market_buy_dollars_30d"] == pytest.approx(100_000)
    assert f["insider_open_market_sell_dollars_30d"] == pytest.approx(50_000)
    assert f["insider_tax_withholding_dollars_30d"] == pytest.approx(20_000)
    assert f["insider_gift_count_30d"] == pytest.approx(1)


# ---------------------------------------------------------------------------
# Task 2.6 — Fix F: replace _role_rank() with reporter flags
# ---------------------------------------------------------------------------

def test_fundamental_weights_senior_officer_trades_via_flags():
    """Senior officer (is_officer=True) buys should populate senior_officer_buy_dollars_30d."""
    senior = InsiderTrade(
        ticker="AAPL", side="buy", shares=1000, price_per_share=100,
        insider_name="CEO", insider_title="Chief Executive Officer",
        transaction_code="P",
        transaction_date=date(2023, 3, 5),
        filed_at=datetime(2023, 3, 6, tzinfo=UTC),
        form_type="4",
        is_officer=True, is_director=True,
    ).model_dump()
    junior = InsiderTrade(
        ticker="AAPL", side="buy", shares=1000, price_per_share=100,
        insider_name="VP", insider_title="VP of Engineering",
        transaction_code="P",
        transaction_date=date(2023, 3, 5),
        filed_at=datetime(2023, 3, 6, tzinfo=UTC),
        form_type="4",
        is_officer=False, is_director=False,
    ).model_dump()
    raw = {"ticker": "AAPL", "insider_trades": [senior, junior], "ratios": {}}
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    # Only the officer's buy should contribute to the senior aggregate.
    assert f["senior_officer_buy_dollars_30d"] == pytest.approx(100_000)

    # _role_rank must not be importable as a module-level symbol.
    import contract.extractors.fundamental as fund_mod
    assert not hasattr(fund_mod, "_role_rank"), (
        "_role_rank should have been deleted; it was replaced by reporter flags"
    )


# ---------------------------------------------------------------------------
# Task 2.7 — Fix G: derivative-table features
# ---------------------------------------------------------------------------

def test_fundamental_emits_derivative_features():
    """Option exercise value and senior-officer grant shares must be populated."""
    derivs = [
        InsiderDerivativeTrade(
            ticker="AAPL", insider_name="CEO", insider_title="CEO",
            side="buy", transaction_code="M",
            derivative_type="option",
            underlying_shares=1000.0, strike_price=120.0,
            transaction_date=date(2023, 3, 5),
            filed_at=datetime(2023, 3, 6, tzinfo=UTC),
            is_officer=True,
        ).model_dump(),
        InsiderDerivativeTrade(
            ticker="AAPL", insider_name="Dir", insider_title="Director",
            side="buy", transaction_code="A",
            derivative_type="rsu",
            underlying_shares=500.0, strike_price=0.0,
            transaction_date=date(2023, 3, 7),
            filed_at=datetime(2023, 3, 8, tzinfo=UTC),
            is_officer=True,
        ).model_dump(),
    ]
    ratios = {"last_price": 170.0}
    raw = {
        "ticker": "AAPL",
        "insider_trades": [],
        "insider_derivative_trades": derivs,
        "ratios": ratios,
    }
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    # Exercise value: 1000 shares × (170 - 120) intrinsic = 50,000.
    assert f["insider_option_exercise_value_30d"] == pytest.approx(1000 * (170.0 - 120.0))
    # RSU grant to officer.
    assert f["senior_officer_derivative_grant_shares_30d"] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# Task 2.8 — Fix H: 8-K item counters
# ---------------------------------------------------------------------------

def test_fundamental_counts_8k_items_in_30d_window():
    """8-K filings with known item numbers must increment the corresponding counters."""
    filings = [
        Filing(
            ticker="AAPL", form_type="8-K",
            filed_at=datetime(2023, 3, 5, tzinfo=UTC),
            accession_no="x1", items_8k=["5.02"],
        ).model_dump(),
        Filing(
            ticker="AAPL", form_type="8-K",
            filed_at=datetime(2023, 3, 6, tzinfo=UTC),
            accession_no="x2", items_8k=["2.02", "9.01"],
        ).model_dump(),
        Filing(
            ticker="AAPL", form_type="8-K",
            filed_at=datetime(2023, 3, 7, tzinfo=UTC),
            accession_no="x3", items_8k=["1.01"],
        ).model_dump(),
    ]
    raw = {
        "ticker": "AAPL",
        "filings": filings,
        "ratios": {},
        "insider_trades": [],
    }
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["n_item_502_30d"] == pytest.approx(1)
    assert f["n_item_202_30d"] == pytest.approx(1)
    assert f["n_item_101_30d"] == pytest.approx(1)

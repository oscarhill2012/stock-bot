"""Unit tests for extract_fundamental_features — Phase 5 insider columns.

These tests verify the new insider + filings-derived feature columns added in
Phase 5.  Existing stats-extraction coverage lives in
``tests/unit/contract/extractors/test_fundamental.py``.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from contract.extractors.fundamental import extract_fundamental_features
from data.models import Form4Bundle, InsiderDerivativeTrade, InsiderTrade

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_BUY = {
    "ticker": "AAPL",
    "side": "buy",
    "shares": 1000.0,
    "price_per_share": 150.0,
    "form_type": "4",
    "transaction_date": date(2026, 5, 1),
    "filed_at": datetime(2026, 5, 2, tzinfo=UTC),
}

_BASE_SELL = {
    "ticker": "AAPL",
    "side": "sell",
    "shares": 100.0,
    "price_per_share": 150.0,
    "form_type": "4",
    "transaction_date": date(2026, 5, 1),
    "filed_at": datetime(2026, 5, 2, tzinfo=UTC),
    "insider_name": "Tim Cook",
    "insider_title": "CEO",
}


def _bundle_with_cluster_buys() -> Form4Bundle:
    """Three officers each buying — should trigger cluster_buy_flag."""
    return Form4Bundle(
        trades=[
            InsiderTrade(**_BASE_BUY, insider_name="Tim Cook", insider_title="CEO"),
            InsiderTrade(**_BASE_BUY, insider_name="Luca Maestri", insider_title="CFO"),
            InsiderTrade(**_BASE_BUY, insider_name="Greg Joswiak", insider_title="SVP"),
        ],
        derivatives=[],
    )


def _raw_with_stats(**extra) -> dict:
    """Build a minimal Phase-5-shaped fundamental_data payload."""
    return {
        "stats": {"pe_trailing": 25.0, "revenue_growth_yoy": 0.08, **extra},
        "filings": [],
        "insider": Form4Bundle(trades=[], derivatives=[]),
    }


# ---------------------------------------------------------------------------
# Column presence
# ---------------------------------------------------------------------------

def test_extractor_emits_insider_columns():
    """The extractor now produces every Phase 5 insider feature column."""
    raw = {
        "stats": {"pe_trailing": 25.0, "revenue_growth_yoy": 0.08},
        "filings": [],
        "insider": _bundle_with_cluster_buys(),
    }
    features = extract_fundamental_features(raw, "AAPL")

    for key in (
        "insider_net_dollars_30d",
        "insider_n_buys_30d",
        "insider_n_sells_30d",
        "insider_cluster_buy_flag",
        "insider_cluster_sell_flag",
        "insider_planned_sale_ratio",
        "insider_max_filer_role_rank",
        "insider_derivative_exercise_count",
        "insider_derivative_grant_count",
        "days_since_last_filing",
        "n_filings_30d",
    ):
        assert key in features, f"missing feature column: {key}"


def test_all_features_are_floats():
    """Every value in the returned feature dict must be a plain float."""
    raw = {
        "stats": {},
        "filings": [],
        "insider": _bundle_with_cluster_buys(),
    }
    features = extract_fundamental_features(raw, "AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r} is not float"


# ---------------------------------------------------------------------------
# Cluster buy / sell flags
# ---------------------------------------------------------------------------

def test_extractor_cluster_buy_flag_fires_with_three_distinct_officers():
    """Three or more distinct officer-level buyers in the window flips cluster_buy_flag."""
    raw = {
        "stats": {},
        "filings": [],
        "insider": _bundle_with_cluster_buys(),
    }
    features = extract_fundamental_features(raw, "AAPL")

    assert features["insider_cluster_buy_flag"] == 1.0
    assert features["insider_n_buys_30d"] == 3.0
    assert features["insider_n_sells_30d"] == 0.0


def test_cluster_buy_flag_off_with_two_buyers():
    """Two distinct buyers is below the threshold — flag must remain 0.0."""
    bundle = Form4Bundle(
        trades=[
            InsiderTrade(**_BASE_BUY, insider_name="Tim Cook", insider_title="CEO"),
            InsiderTrade(**_BASE_BUY, insider_name="Luca Maestri", insider_title="CFO"),
        ],
        derivatives=[],
    )
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    assert features["insider_cluster_buy_flag"] == 0.0
    assert features["insider_n_buys_30d"] == 2.0


def test_cluster_sell_flag_fires_with_three_distinct_sellers():
    """Three or more distinct sellers trigger cluster_sell_flag."""
    sell = {**_BASE_BUY, "side": "sell"}
    bundle = Form4Bundle(
        trades=[
            InsiderTrade(**sell, insider_name="Alice", insider_title="CFO"),
            InsiderTrade(**sell, insider_name="Bob", insider_title="SVP"),
            InsiderTrade(**sell, insider_name="Carol", insider_title="VP"),
        ],
        derivatives=[],
    )
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    assert features["insider_cluster_sell_flag"] == 1.0


# ---------------------------------------------------------------------------
# Planned sale ratio (10b5-1)
# ---------------------------------------------------------------------------

def test_extractor_planned_sale_ratio_counts_10b5_1_correctly():
    """planned_sale_ratio = (10b5-1 sells) / total sells, clamped to [0, 1]."""
    bundle = Form4Bundle(
        trades=[
            InsiderTrade(**_BASE_SELL, is_10b5_1=True),
            InsiderTrade(**_BASE_SELL, is_10b5_1=True),
            InsiderTrade(**_BASE_SELL, is_10b5_1=False),
        ],
        derivatives=[],
    )
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    assert abs(features["insider_planned_sale_ratio"] - (2 / 3)) < 1e-6


def test_planned_sale_ratio_zero_when_no_sells():
    """When there are no sell transactions, ratio defaults to 0.0 (no division)."""
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": Form4Bundle(trades=[], derivatives=[])},
        "AAPL",
    )
    assert features["insider_planned_sale_ratio"] == 0.0


# ---------------------------------------------------------------------------
# Role ranking
# ---------------------------------------------------------------------------

def test_max_filer_role_rank_ceo_scores_highest():
    """A CEO buy must produce the maximum role rank (5)."""
    bundle = Form4Bundle(
        trades=[
            InsiderTrade(**_BASE_BUY, insider_name="Tim Cook", insider_title="CEO"),
        ],
        derivatives=[],
    )
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    assert features["insider_max_filer_role_rank"] == 5.0


def test_max_filer_role_rank_unknown_title_scores_zero():
    """An unrecognised title maps to rank 0."""
    bundle = Form4Bundle(
        trades=[
            InsiderTrade(**_BASE_BUY, insider_name="Foo Bar", insider_title="Chief Snack Officer"),
        ],
        derivatives=[],
    )
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    assert features["insider_max_filer_role_rank"] == 0.0


# ---------------------------------------------------------------------------
# Net dollars
# ---------------------------------------------------------------------------

def test_net_dollars_buy_minus_sell():
    """net_dollars = buy_value - sell_value across the 30-day window."""
    buy_trade = InsiderTrade(**_BASE_BUY, insider_name="Tim Cook", insider_title="CEO")
    # 1000 shares * £150 = £150,000 buy

    sell_trade = InsiderTrade(
        ticker="AAPL", side="sell", shares=200.0, price_per_share=150.0,
        form_type="4",
        transaction_date=date(2026, 5, 1),
        filed_at=datetime(2026, 5, 2, tzinfo=UTC),
        insider_name="Tim Cook", insider_title="CEO",
    )
    # 200 shares * £150 = £30,000 sell

    bundle = Form4Bundle(trades=[buy_trade, sell_trade], derivatives=[])
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    # Expected: 150_000 - 30_000 = 120_000
    assert abs(features["insider_net_dollars_30d"] - 120_000.0) < 1.0


# ---------------------------------------------------------------------------
# Derivative counts
# ---------------------------------------------------------------------------

def test_derivative_exercise_count_code_m():
    """Derivatives with transaction_code 'M' are counted as exercises."""
    deriv = InsiderDerivativeTrade(
        ticker="AAPL", insider_name="Tim Cook",
        side="buy", underlying_shares=500.0,
        transaction_date=date(2026, 5, 1),
        filed_at=datetime(2026, 5, 2, tzinfo=UTC),
        transaction_code="M",
    )
    bundle = Form4Bundle(trades=[], derivatives=[deriv])
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    assert features["insider_derivative_exercise_count"] == 1.0
    assert features["insider_derivative_grant_count"] == 0.0


def test_derivative_grant_count_code_a():
    """Derivatives with transaction_code 'A' are counted as grants."""
    deriv = InsiderDerivativeTrade(
        ticker="AAPL", insider_name="Tim Cook",
        side="buy", underlying_shares=1000.0,
        transaction_date=date(2026, 5, 1),
        filed_at=datetime(2026, 5, 2, tzinfo=UTC),
        transaction_code="A",
    )
    bundle = Form4Bundle(trades=[], derivatives=[deriv])
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": bundle}, "AAPL"
    )
    assert features["insider_derivative_grant_count"] == 1.0
    assert features["insider_derivative_exercise_count"] == 0.0


# ---------------------------------------------------------------------------
# Empty / no-data paths
# ---------------------------------------------------------------------------

def test_extractor_returns_zero_columns_when_no_insider_data():
    """Empty Form4Bundle yields zeros for every insider column."""
    features = extract_fundamental_features(
        {"stats": {}, "filings": [], "insider": Form4Bundle(trades=[], derivatives=[])},
        "AAPL",
    )
    assert features["insider_n_buys_30d"] == 0.0
    assert features["insider_n_sells_30d"] == 0.0
    assert features["insider_cluster_buy_flag"] == 0.0
    assert features["insider_cluster_sell_flag"] == 0.0
    assert features["insider_net_dollars_30d"] == 0.0
    assert features["insider_planned_sale_ratio"] == 0.0
    assert features["insider_max_filer_role_rank"] == 0.0
    assert features["insider_derivative_exercise_count"] == 0.0
    assert features["insider_derivative_grant_count"] == 0.0


def test_extractor_handles_entirely_missing_insider_key():
    """If the 'insider' key is absent the extractor returns zero insider columns."""
    features = extract_fundamental_features(
        {"stats": {"pe_trailing": 20.0}, "filings": []},
        "AAPL",
    )
    assert features["insider_n_buys_30d"] == 0.0
    assert features["insider_cluster_buy_flag"] == 0.0


# ---------------------------------------------------------------------------
# Stats columns still work with the new shape
# ---------------------------------------------------------------------------

def test_stats_columns_still_extracted_from_nested_stats_key():
    """pe_trailing and revenue_growth_yoy must still be extracted from raw['stats']."""
    raw = {
        "stats": {
            "trailing_pe": 28.5,
            "revenue_growth_yoy": 0.12,
        },
        "filings": [],
        "insider": Form4Bundle(trades=[], derivatives=[]),
    }
    features = extract_fundamental_features(raw, "AAPL")
    assert features["pe_trailing"] == pytest.approx(28.5)
    assert features["revenue_growth_yoy"] == pytest.approx(0.12)

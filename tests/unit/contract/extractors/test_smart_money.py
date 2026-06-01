"""Smart-money feature extractor tests — Tier 1, no LLM.

Phase 7 (Task 2.12): adds notable-holder aggregate tests.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from contract.extractors.smart_money import _KEYS, extract_smart_money_features
from data.models.trades import NotableHolder

AAPL_FIXTURE = Path("tests/fixtures/contract/smart_money_aapl.json")
NODATA_FIXTURE = Path("tests/fixtures/contract/smart_money_no_data.json")


@pytest.fixture
def aapl_data():
    return json.loads(AAPL_FIXTURE.read_text())


@pytest.fixture
def empty_data():
    return json.loads(NODATA_FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    """The returned dict must contain exactly the keys declared in _KEYS."""
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert set(features.keys()) == set(_KEYS)


def test_all_features_are_floats(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    for v in features.values():
        assert isinstance(v, float)


def test_unique_filer_count(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    # Three distinct filers in the fixture
    assert features["n_politicians"] == 3.0


def test_buy_sell_counts(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["n_buys_30d"] == 3.0
    assert features["n_sells_30d"] == 1.0


def test_dollar_totals(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["total_dollar_value_buys"] == 250_000 + 100_000 + 75_000
    assert features["total_dollar_value_sells"] == 50_000.0
    assert features["net_flow_dollar"] == (425_000 - 50_000)


def test_is_no_data_zero_when_filings_present(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["is_no_data"] == 0.0


def test_is_no_data_one_when_no_filings(empty_data):
    features = extract_smart_money_features(empty_data, ticker="TSLA")
    assert features["is_no_data"] == 1.0
    assert features["n_politicians"] == 0.0
    assert features["n_buys_30d"] == 0.0
    assert features["total_dollar_value_buys"] == 0.0


def test_is_no_data_one_when_empty_dict():
    features = extract_smart_money_features({}, ticker="UNKNOWN")
    assert features["is_no_data"] == 1.0


# ---------------------------------------------------------------------------
# Task 2.12 — Notable-holder aggregates
# ---------------------------------------------------------------------------

def test_smart_money_emits_holder_aggregates():
    """Holder aggregates must be computed correctly from the 90-day window."""
    holders = [
        NotableHolder(
            ticker="AAPL", holder="H1", form_type="SC 13D",
            filed_at=datetime(2023, 3, 5, tzinfo=UTC),
            accession_no="a", intent="active",
            is_amendment=False, percent_of_class=8.0,
            shares_held=500_000.0,
        ).model_dump(),
        NotableHolder(
            ticker="AAPL", holder="H2", form_type="SC 13G",
            filed_at=datetime(2023, 3, 6, tzinfo=UTC),
            accession_no="b", intent="passive",
            is_amendment=True, percent_of_class=5.2,
            shares_held=320_000.0,
        ).model_dump(),
    ]
    raw = {
        "ticker": "AAPL",
        "politician_trades": [],
        "notable_holders": holders,
    }
    f = extract_smart_money_features(
        raw, state={"as_of": date(2023, 3, 12).isoformat()})
    assert f["n_active_13d_30d"] == pytest.approx(1)
    assert f["n_passive_13g_30d"] == pytest.approx(1)
    assert f["n_amendments_30d"] == pytest.approx(1)
    assert f["notable_holder_present"] == pytest.approx(1.0)
    assert f["max_percent_of_class_30d"] == pytest.approx(8.0)
    assert f["total_shares_held_30d"] == pytest.approx(820_000.0)


def test_smart_money_holder_clears_no_data_flag():
    """If notable holders are present (even with no politician trades), is_no_data must be 0."""
    holders = [
        NotableHolder(
            ticker="AAPL", holder="BigFund", form_type="SC 13G",
            filed_at=datetime(2023, 3, 5, tzinfo=UTC),
            accession_no="x", intent="passive",
            is_amendment=False, percent_of_class=6.0,
            shares_held=100_000.0,
        ).model_dump(),
    ]
    raw = {"ticker": "AAPL", "notable_holders": holders}
    f = extract_smart_money_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["is_no_data"] == pytest.approx(0.0)
    assert f["notable_holder_present"] == pytest.approx(1.0)


def test_smart_money_holder_outside_window_excluded():
    """Holders filed before the 90-day cutoff must not be counted."""
    holders = [
        NotableHolder(
            ticker="AAPL", holder="OldFund", form_type="SC 13D",
            filed_at=datetime(2022, 1, 1, tzinfo=UTC),  # very old
            accession_no="old", intent="active",
            is_amendment=False, percent_of_class=5.0,
            shares_held=200_000.0,
        ).model_dump(),
    ]
    raw = {"ticker": "AAPL", "notable_holders": holders}
    f = extract_smart_money_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["notable_holder_present"] == pytest.approx(0.0)
    assert f["total_shares_held_30d"] == pytest.approx(0.0)
    # No politician trades either → still no-data.
    assert f["is_no_data"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# A-016 / A-049 regression — derive_smart_money_verdict must not fabricate prose
# ---------------------------------------------------------------------------

def test_deterministic_verdict_no_longer_fabricates_report() -> None:
    """A-016 / A-049 regression: smart_money extractor must leave
    report=None and let rationale carry the one-liner.

    The fixture has net positive flow (3 buys, no sells) to ensure a
    directional (bullish) verdict rather than the no-data short-circuit.
    """
    import json
    import pathlib

    from agents.analysts.heuristics import SmartMoneyHeuristics
    from contract.extractors.smart_money import _KEYS, derive_smart_money_verdict

    # Load thresholds from the real config so values are consistent with production.
    raw_cfg = json.loads(
        (pathlib.Path(__file__).parent.parent.parent.parent.parent
         / "config" / "analyst_heuristics.json").read_text()
    )
    h = SmartMoneyHeuristics(**raw_cfg["smart_money"])

    # Minimal directional features: net positive flow, is_no_data cleared.
    features = {
        "is_no_data": 0.0,
        "n_buys_30d": 3.0,
        "n_sells_30d": 0.0,
        "net_flow_dollar": 250_000.0,
        "total_dollar_value_buys": 250_000.0,
        "total_dollar_value_sells": 0.0,
        "n_politicians": 2.0,
    }
    # Backfill any remaining _KEYS to 0.0 so the helper doesn't KeyError.
    for k in _KEYS:
        features.setdefault(k, 0.0)

    v = derive_smart_money_verdict(features, h)

    assert v.is_no_data is False
    assert v.report is None
    assert v.rationale != ""


def test_no_data_branch_uses_canonical_builder() -> None:
    """is_no_data sentinel → canonical no-data shape via _no_data_analyst_verdict."""
    import json
    import pathlib

    from agents.analysts.heuristics import SmartMoneyHeuristics
    from contract.extractors.smart_money import _KEYS, derive_smart_money_verdict

    raw_cfg = json.loads(
        (pathlib.Path(__file__).parent.parent.parent.parent.parent
         / "config" / "analyst_heuristics.json").read_text()
    )
    h = SmartMoneyHeuristics(**raw_cfg["smart_money"])

    # All features zeroed with is_no_data set to trip the no-data branch.
    features = {k: 0.0 for k in _KEYS}
    features["is_no_data"] = 1.0

    v = derive_smart_money_verdict(features, h)

    assert v.is_no_data is True
    assert v.report is None
    assert v.rationale

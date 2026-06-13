# tests/unit/contract/test_insider_extractor_no_legacy.py
"""The legacy 'insider: Form4Bundle' payload path is retired — handing
the extractor that shape now raises rather than silently degrading."""
from __future__ import annotations

import pytest

from contract.extractors.fundamental import extract_fundamental_features
from data.models import Form4Bundle


def test_legacy_insider_key_raises():
    """Payload with only the typed 'insider' key (no 'insider_trades') raises KeyError."""

    raw = {
        "ratios":  {},
        "filings": [],
        "insider": Form4Bundle(trades=[], derivatives=[]),
    }

    with pytest.raises(KeyError):
        extract_fundamental_features(raw, "AAPL")


def test_flat_list_shape_still_works():
    """Phase 7 flat-list shape continues to extract cleanly."""

    raw = {
        "ratios":                    {},
        "filings":                   [],
        "insider_trades":            [],
        "insider_derivative_trades": [],
    }

    features = extract_fundamental_features(raw, "AAPL")
    assert features["insider_n_buys_30d"]  == 0.0
    assert features["insider_n_sells_30d"] == 0.0

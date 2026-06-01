from __future__ import annotations

import pytest

from contract.evidence import (
    AnalystVerdict,
    TickerVerdict,
    _no_data_analyst_verdict,
    build_no_data_verdict,
)


def test_builds_canonical_ticker_verdict() -> None:
    v = build_no_data_verdict("AAPL", reason="provider returned empty payload")
    assert isinstance(v, TickerVerdict)
    assert v.ticker == "AAPL"
    assert v.is_no_data is True
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0
    assert v.report is None
    assert v.rationale == "provider returned empty payload"
    assert v.key_factors == []


def test_empty_reason_raises() -> None:
    with pytest.raises(ValueError, match="reason"):
        build_no_data_verdict("AAPL", reason="")


def test_whitespace_reason_raises() -> None:
    with pytest.raises(ValueError, match="reason"):
        build_no_data_verdict("AAPL", reason="   \t\n  ")


def test_analyst_verdict_helper_drops_ticker() -> None:
    v = _no_data_analyst_verdict(reason="no verdict from LLM")
    assert isinstance(v, AnalystVerdict)
    assert not isinstance(v, TickerVerdict)
    assert v.is_no_data is True
    assert v.rationale == "no verdict from LLM"
    assert v.report is None


def test_analyst_verdict_helper_empty_reason_raises() -> None:
    with pytest.raises(ValueError, match="reason"):
        _no_data_analyst_verdict(reason="")

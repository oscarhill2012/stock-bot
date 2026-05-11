"""Dual-emit callback tests — Tier 1, no LLM."""
from __future__ import annotations

from typing import Any

from agents.analysts._common import AnalystSignal, make_dual_emit_callback
from contract.evidence import AnalystEvidence


class _State(dict):
    pass


class _Ctx:
    def __init__(self, state: dict):
        self.state = state


def _fake_extractor(raw: Any, ticker: str) -> dict[str, float]:
    """Toy extractor: returns one feature key per ticker for assertion."""
    return {"toy_feature": 1.0}


def _state_with(tickers, signals, data) -> _State:
    return _State(
        tick_id="2026-05-08T14:00:00Z",
        tickers=tickers,
        technical_signals=signals,
        technical_data=data,
    )


def test_dual_emit_writes_evidence_for_each_signal():
    state = _state_with(
        ["AAPL", "MSFT"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.7,
                          key_factors=["RSI 42"]).model_dump(),
            AnalystSignal(ticker="MSFT", direction="neutral", confidence=0.4).model_dump(),
        ],
        {"AAPL": {"x": 1}, "MSFT": {"x": 2}},
    )
    callback = make_dual_emit_callback(
        analyst="technical",
        signals_key="technical_signals",
        data_key="technical_data",
        evidence_key="technical_evidence",
        extractor=_fake_extractor,
    )
    out = callback(_Ctx(state))
    assert out is None  # no re-prompt — exhaustive

    evidence_list = state["technical_evidence"]
    assert len(evidence_list) == 2

    parsed = [AnalystEvidence.model_validate(e) for e in evidence_list]
    by_ticker = {e.ticker: e for e in parsed}
    assert by_ticker["AAPL"].verdict.lean == "bullish"
    assert by_ticker["AAPL"].verdict.confidence == 0.7
    # During dual-emit, magnitude proxies confidence (legacy can't separate them).
    assert by_ticker["AAPL"].verdict.magnitude == 0.7
    assert by_ticker["AAPL"].features == {"toy_feature": 1.0}
    assert by_ticker["AAPL"].analyst == "technical"
    assert by_ticker["AAPL"].tick_id == "2026-05-08T14:00:00Z"
    assert by_ticker["AAPL"].feature_warnings == []


def test_dual_emit_preserves_key_factors_as_structured_list():
    """Legacy AnalystSignal.key_factors must survive as a list on AnalystVerdict —
    NOT collapsed into rationale. This list is the future knowledge-base lookup
    primitive (backlog B2)."""
    state = _state_with(
        ["AAPL"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.6,
                          key_factors=["RSI cooled", "uptrend intact", "volume up"]).model_dump(),
        ],
        {"AAPL": {}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _fake_extractor)
    cb(_Ctx(state))

    ev = AnalystEvidence.model_validate(state["technical_evidence"][0])
    assert ev.verdict.key_factors == ["RSI cooled", "uptrend intact", "volume up"]
    # Rationale is the joined factors (for prompt readability)
    assert "RSI cooled" in ev.verdict.rationale
    assert "uptrend intact" in ev.verdict.rationale


def test_dual_emit_truncates_rationale_to_160_chars():
    long_factor = "x" * 200
    state = _state_with(
        ["AAPL"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.5,
                          key_factors=[long_factor]).model_dump(),
        ],
        {"AAPL": {}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _fake_extractor)
    cb(_Ctx(state))
    ev = AnalystEvidence.model_validate(state["technical_evidence"][0])
    assert len(ev.verdict.rationale) <= 160


def test_dual_emit_reprompts_on_missing_tickers():
    """If the LLM missed tickers, we still re-prompt rather than silently filling."""
    state = _state_with(
        ["AAPL", "MSFT"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.7).model_dump(),
        ],
        {"AAPL": {}, "MSFT": {}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _fake_extractor)
    out = cb(_Ctx(state))
    # Re-prompt content for missing MSFT
    assert out is not None
    assert "MSFT" in out.parts[0].text
    # No evidence written when re-prompting
    assert state.get("technical_evidence") in (None, [])


def test_dual_emit_handles_empty_features_gracefully():
    """If extractor returns {}, evidence still validates with empty features."""
    state = _state_with(
        ["AAPL"],
        [
            AnalystSignal(ticker="AAPL", direction="neutral", confidence=0.0).model_dump(),
        ],
        {"AAPL": {}},
    )
    cb = make_dual_emit_callback(
        "technical", "technical_signals", "technical_data", "technical_evidence",
        extractor=lambda raw, ticker: {},
    )
    cb(_Ctx(state))
    ev = AnalystEvidence.model_validate(state["technical_evidence"][0])
    assert ev.features == {}


def test_dual_emit_smart_money_no_data_flag_propagates():
    """smart_money extractor's `is_no_data` feature must set the verdict's
    is_no_data flag so the digest aggregator drops the verdict from voting."""
    state = _State(
        tick_id="t",
        tickers=["AAPL"],
        smart_money_signals=[
            AnalystSignal(ticker="AAPL", direction="neutral", confidence=0.0).model_dump(),
        ],
        smart_money_data={"AAPL": {}},
    )
    cb = make_dual_emit_callback(
        analyst="smart_money",
        signals_key="smart_money_signals",
        data_key="smart_money_data",
        evidence_key="smart_money_evidence",
        extractor=lambda raw, ticker: {"is_no_data": 1.0},
    )
    cb(_Ctx(state))
    ev = AnalystEvidence.model_validate(state["smart_money_evidence"][0])
    assert ev.verdict.is_no_data is True


def test_dual_emit_isolates_ticker_data_to_extractor():
    """Extractor is called with the per-ticker slice of `state[data_key]`, not the whole dict."""
    seen: list = []

    def _spy(raw: Any, ticker: str) -> dict[str, float]:
        seen.append((ticker, raw))
        return {}

    state = _state_with(
        ["AAPL", "MSFT"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.5).model_dump(),
            AnalystSignal(ticker="MSFT", direction="bearish", confidence=0.5).model_dump(),
        ],
        {"AAPL": {"price": 100}, "MSFT": {"price": 200}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _spy)
    cb(_Ctx(state))
    by_ticker = dict(seen)
    assert by_ticker["AAPL"] == {"price": 100}
    assert by_ticker["MSFT"] == {"price": 200}

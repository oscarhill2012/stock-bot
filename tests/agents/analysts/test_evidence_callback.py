"""make_evidence_callback writes only AnalystEvidence — no legacy *_signals.

These tests verify the new evidence-only callback introduced in D3. The callback
reads verdicts directly from ``state[verdicts_state_key]`` (LLM output), runs the
deterministic feature extractor, and writes ``state["{analyst}_evidence"]``. It
explicitly must NOT write any legacy ``*_signals`` key.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.analysts._common import make_evidence_callback
from contract.evidence import AnalystEvidence
from contract.extractors.technical import extract_technical_features

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_extractor(raw, ticker, *, as_of=None) -> dict[str, float]:
    """Toy extractor: always returns one feature key for simple assertions.

    The ``as_of`` kwarg is accepted (and ignored) to match the uniform
    extractor signature required by ``make_evidence_callback`` after C4.
    """
    return {"toy_feature": 1.0}


def _ctx(state: dict) -> SimpleNamespace:
    """Wrap a plain dict in a SimpleNamespace to match the ADK ctx.state protocol."""
    return SimpleNamespace(state=state)


# ---------------------------------------------------------------------------
# Core test: evidence written, no legacy signal key
# ---------------------------------------------------------------------------

def test_writes_only_evidence_state_key():
    """Callback writes ``technical_evidence`` and must NOT write ``technical_signals``."""
    state = {
        "tick_id": "2026-05-08T14:00:00Z",
        "tickers": ["AAPL"],
        "technical_data": {"AAPL": {"close": [100.0] * 30, "volume": [1.0e6] * 30}},
        "technical_verdicts": [
            {
                "ticker": "AAPL",
                "lean": "bullish",
                "magnitude": 0.5,
                "confidence": 0.6,
                "rationale": "trend intact",
                "key_factors": ["rsi"],
                "is_no_data": False,
            }
        ],
    }
    cb = make_evidence_callback(
        analyst="technical",
        extractor=extract_technical_features,
        verdicts_state_key="technical_verdicts",
    )
    cb(_ctx(state))

    # Evidence must be present and valid.
    assert "technical_evidence" in state
    assert isinstance(state["technical_evidence"], list)
    ev = state["technical_evidence"][0]
    AnalystEvidence.model_validate(ev)

    # Legacy signal key must NOT exist.
    assert "technical_signals" not in state


# ---------------------------------------------------------------------------
# Missing-verdict fallback: synthesise no-data evidence for absent tickers
# ---------------------------------------------------------------------------

def test_missing_verdict_synthesises_no_data_evidence():
    """If the LLM omitted a ticker's verdict, the callback synthesises a
    no-data AnalystEvidence so every ticker always has a record.
    """
    state = {
        "tick_id": "2026-05-08T14:00:00Z",
        "tickers": ["AAPL", "MSFT"],
        "technical_data": {"AAPL": {}, "MSFT": {}},
        "technical_verdicts": [
            {
                "ticker": "AAPL",
                "lean": "bullish",
                "magnitude": 0.5,
                "confidence": 0.6,
                "rationale": "trend",
                "key_factors": [],
                "is_no_data": False,
            },
            # MSFT verdict deliberately absent.
        ],
    }
    cb = make_evidence_callback(
        analyst="technical",
        extractor=extract_technical_features,
        verdicts_state_key="technical_verdicts",
    )
    cb(_ctx(state))

    evs = {e["ticker"]: e for e in state["technical_evidence"]}
    assert set(evs.keys()) == {"AAPL", "MSFT"}

    # The synthesised no-data record for MSFT.
    assert evs["MSFT"]["verdict"]["is_no_data"] is True
    assert evs["MSFT"]["verdict"]["lean"] == "neutral"


# ---------------------------------------------------------------------------
# Feature extraction is scoped to the per-ticker data slice
# ---------------------------------------------------------------------------

def test_extractor_called_with_per_ticker_slice():
    """The extractor receives the per-ticker dict, not the whole data dict."""
    seen: list = []

    def _spy(raw, ticker, *, as_of=None) -> dict[str, float]:
        # Accept as_of to match the uniform extractor signature (C4).
        seen.append((ticker, raw))
        return {"spy": 0.0}

    state = {
        "tick_id": "t",
        "tickers": ["AAPL", "MSFT"],
        "technical_data": {"AAPL": {"price": 100}, "MSFT": {"price": 200}},
        "technical_verdicts": [
            {"ticker": "AAPL", "lean": "neutral", "magnitude": 0.0,
             "confidence": 0.0, "rationale": "x", "key_factors": [], "is_no_data": False},
            {"ticker": "MSFT", "lean": "neutral", "magnitude": 0.0,
             "confidence": 0.0, "rationale": "x", "key_factors": [], "is_no_data": False},
        ],
    }
    cb = make_evidence_callback(
        analyst="technical",
        extractor=_spy,
        verdicts_state_key="technical_verdicts",
    )
    cb(_ctx(state))

    by_ticker = dict(seen)
    assert by_ticker["AAPL"] == {"price": 100}
    assert by_ticker["MSFT"] == {"price": 200}


# ---------------------------------------------------------------------------
# Verdict fields pass through correctly
# ---------------------------------------------------------------------------

def test_verdict_fields_round_trip():
    """lean / magnitude / confidence / rationale / key_factors survive intact."""
    state = {
        "tick_id": "tick-abc",
        "tickers": ["AAPL"],
        "technical_data": {"AAPL": {}},
        "technical_verdicts": [
            {
                "ticker": "AAPL",
                "lean": "bearish",
                "magnitude": 0.8,
                "confidence": 0.75,
                "rationale": "Death cross formed",
                "key_factors": ["death cross", "volume surge"],
                "is_no_data": False,
            }
        ],
    }
    cb = make_evidence_callback(
        analyst="technical",
        extractor=_fake_extractor,
        verdicts_state_key="technical_verdicts",
    )
    cb(_ctx(state))

    ev = AnalystEvidence.model_validate(state["technical_evidence"][0])
    assert ev.verdict.lean == "bearish"
    assert ev.verdict.magnitude == pytest.approx(0.8)
    assert ev.verdict.confidence == pytest.approx(0.75)
    assert ev.verdict.rationale == "Death cross formed"
    assert ev.verdict.key_factors == ["death cross", "volume surge"]
    assert ev.verdict.is_no_data is False
    assert ev.analyst == "technical"
    assert ev.tick_id == "tick-abc"
    assert ev.features == {"toy_feature": 1.0}
    assert ev.feature_warnings == []


# ---------------------------------------------------------------------------
# Empty tickers list — callback is a no-op that writes an empty evidence list
# ---------------------------------------------------------------------------

def test_empty_tickers_produces_empty_evidence():
    """With no tickers in watchlist, the callback writes an empty list and returns None."""
    state = {
        "tick_id": "t",
        "tickers": [],
        "technical_data": {},
        "technical_verdicts": [],
    }
    cb = make_evidence_callback(
        analyst="technical",
        extractor=_fake_extractor,
        verdicts_state_key="technical_verdicts",
    )
    result = cb(_ctx(state))

    assert result is None
    assert state["technical_evidence"] == []


# ---------------------------------------------------------------------------
# Malformed verdict — fail-fast contract
# ---------------------------------------------------------------------------

def test_malformed_verdict_raises_validation_error():
    """A verdict that violates AnalystVerdict's range constraints must raise,
    not be silently coerced or wrapped in a no-data evidence row."""
    from pydantic import ValidationError

    state = {
        "tick_id": "2026-05-08T14:00:00Z",
        "tickers": ["AAPL"],
        "technical_data": {"AAPL": {}},
        "technical_verdicts": [
            {
                "ticker": "AAPL",
                "lean": "bullish",
                "magnitude": 1.5,  # out of [0, 1] range
                "confidence": 0.6,
                "rationale": "x",
                "key_factors": [],
                "is_no_data": False,
            }
        ],
    }
    cb = make_evidence_callback(
        analyst="technical",
        extractor=extract_technical_features,
        verdicts_state_key="technical_verdicts",
    )
    with pytest.raises(ValidationError):
        cb(SimpleNamespace(state=state))

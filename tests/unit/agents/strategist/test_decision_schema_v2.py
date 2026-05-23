"""StrategistDecision v2 tests — Tier 1, no LLM."""
from __future__ import annotations

from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance


def test_decision_with_stances():
    """StrategistDecision accepts a list of TickerStance objects in the stances field."""
    d = StrategistDecision(
        stances=[
            TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                          rationale="open", horizon="swing",
                          target_price=210.0, stop_price=185.0),
        ],
        target_weights={},
        decision_tag="x", reasoning="x", thesis="y",
        confidence=0.6,
    )
    assert len(d.stances) == 1


def test_decision_trim_reasons_default_empty():
    """trim_reasons defaults to an empty dict when not supplied."""
    d = StrategistDecision(
        stances=[], target_weights={},
        decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
    )
    assert d.trim_reasons == {}


def test_decision_trim_reasons_round_trip():
    """trim_reasons survives a model_dump → model_validate round-trip."""
    d = StrategistDecision(
        stances=[], target_weights={"MSFT": 0.05},
        decision_tag="trim", reasoning="x", thesis="y",
        confidence=0.5,
        trim_reasons={"MSFT": "lock in profits"},
    )
    rebuilt = StrategistDecision.model_validate(d.model_dump(mode="json"))
    assert rebuilt.trim_reasons == {"MSFT": "lock in profits"}


def test_legacy_fields_preserved():
    """Existing legacy fields (target_weights, new_positions, close_reasons) still work."""
    d = StrategistDecision(
        stances=[], target_weights={"AAPL": 0.08},
        decision_tag="x", reasoning="x", thesis="y",
        confidence=0.7,
        new_positions={}, close_reasons={},
    )
    assert d.target_weights == {"AAPL": 0.08}
    assert d.new_positions == {}
    assert d.close_reasons == {}


def test_legacy_json_without_stances_parses():
    """JSON emitted before C7 (no 'stances' key) still parses cleanly.

    Any persisted ADK state blob from before this task — fixtures, replayed
    integration logs, snapshot tests — predates the new `stances` field and so
    will have no `"stances"` key in its serialised form. The
    `default_factory=list` makes this safe at the Pydantic level; this test
    pins down that safety so a future careless validator rule cannot quietly
    break replay.
    """
    payload = {
        "target_weights": {"AAPL": 0.08},
        "decision_tag": "legacy",
        "reasoning": "x",
        "thesis": "y",
        "confidence": 0.6,
    }
    d = StrategistDecision.model_validate(payload)
    assert d.stances == []

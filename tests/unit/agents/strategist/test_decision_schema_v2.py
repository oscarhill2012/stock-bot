"""StrategistDecision v2 tests — Tier 1, no LLM.

Updated for iter-3 three-verb schema: buy / sell / update.
``close_reasons`` and ``trim_reasons`` are replaced by ``sell_reasons``
and ``update_reasons`` respectively.
"""
from __future__ import annotations

from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance


def test_decision_with_stances():
    """StrategistDecision accepts a list of TickerStance objects in the stances field."""
    d = StrategistDecision(
        stances=[
            TickerStance(
                ticker="AAPL",
                intent="buy",
                weight=0.04,
                rationale="FCF-driven thesis",
            ),
        ],
        target_weights={},
        decision_tag="x", reasoning="x", thesis="y",
        confidence=0.6,
    )
    assert len(d.stances) == 1


def test_decision_sell_reasons_default_empty():
    """sell_reasons defaults to an empty dict when not supplied."""
    d = StrategistDecision(
        stances=[], target_weights={},
        decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
    )
    assert d.sell_reasons == {}


def test_decision_sell_reasons_round_trip():
    """sell_reasons survives a model_dump → model_validate round-trip."""
    d = StrategistDecision(
        stances=[], target_weights={"MSFT": 0.0},
        decision_tag="sell", reasoning="x", thesis="y",
        confidence=0.5,
        sell_reasons={"MSFT": "lock in profits"},
    )
    rebuilt = StrategistDecision.model_validate(d.model_dump(mode="json"))
    assert rebuilt.sell_reasons == {"MSFT": "lock in profits"}


def test_decision_update_reasons_round_trip():
    """update_reasons survives a model_dump → model_validate round-trip."""
    d = StrategistDecision(
        stances=[], target_weights={"MSFT": 0.05},
        decision_tag="update", reasoning="x", thesis="y",
        confidence=0.5,
        update_reasons={"MSFT": "raised AI capex view"},
    )
    rebuilt = StrategistDecision.model_validate(d.model_dump(mode="json"))
    assert rebuilt.update_reasons == {"MSFT": "raised AI capex view"}


def test_iter3_fields_present():
    """sell_reasons and update_reasons exist; old close_reasons/trim_reasons do not."""
    d = StrategistDecision(
        stances=[], target_weights={"AAPL": 0.04},
        decision_tag="x", reasoning="x", thesis="y",
        confidence=0.7,
    )
    assert d.target_weights == {"AAPL": 0.04}
    assert hasattr(d, "sell_reasons")
    assert hasattr(d, "update_reasons")
    assert not hasattr(d, "close_reasons"),  "close_reasons was removed in iter-3"
    assert not hasattr(d, "trim_reasons"),   "trim_reasons was removed in iter-3"


def test_legacy_json_without_stances_parses():
    """JSON emitted before C7 (no 'stances' key) still parses cleanly.

    Any persisted ADK state blob from before this task — fixtures, replayed
    integration logs, snapshot tests — predates the new `stances` field and so
    will have no ``"stances"`` key in its serialised form.  The
    ``default_factory=list`` makes this safe at the Pydantic level; this test
    pins down that safety so a future careless validator rule cannot quietly
    break replay.
    """
    payload = {
        "target_weights": {"AAPL": 0.04},
        "decision_tag": "legacy",
        "reasoning": "x",
        "thesis": "y",
        "confidence": 0.6,
    }
    d = StrategistDecision.model_validate(payload)
    assert d.stances == []

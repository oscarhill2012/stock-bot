"""StrategistDecision v2 tests — Tier 1, no LLM.

Updated for the A-013 tail collapse: ``sell_reasons`` and ``update_reasons``
have been removed from ``StrategistDecision``.  Sell / update rationale now
lives exclusively on ``TickerStance.rationale``.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def test_iter3_fields_present():
    """target_weights is present; deleted legacy dicts are absent."""
    d = StrategistDecision(
        stances=[], target_weights={"AAPL": 0.04},
        decision_tag="x", reasoning="x", thesis="y",
        confidence=0.7,
    )
    assert d.target_weights == {"AAPL": 0.04}

    # Legacy fields removed — these must not exist on the schema.
    assert not hasattr(d, "sell_reasons"),    "sell_reasons was removed (A-013 tail)"
    assert not hasattr(d, "update_reasons"),  "update_reasons was removed (A-013 tail)"
    assert not hasattr(d, "close_reasons"),   "close_reasons was removed in iter-3"
    assert not hasattr(d, "trim_reasons"),    "trim_reasons was removed in iter-3"


def test_legacy_json_without_stances_parses():
    """JSON emitted before C7 (no 'stances' key) still parses cleanly.

    Any persisted ADK state blob from before this task — fixtures, replayed
    integration logs, snapshot tests — predates the new ``stances`` field and so
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


def test_strategist_decision_rejects_legacy_reason_dicts():
    """A-013 tail: sell_reasons / update_reasons no longer exist on the schema.

    StrategistDecision is configured extra='forbid', so passing either
    legacy kwarg must raise ValidationError — a silent regression cannot
    reintroduce the byte-identical duplication.

    All required fields are supplied so that the ONLY reason for a
    ValidationError is the unexpected extra kwarg, not a missing-field error.
    """
    _base = dict(
        stances=[],
        target_weights={},
        decision_tag="x",
        reasoning="x",
        confidence=0.5,
    )

    with pytest.raises(ValidationError):
        StrategistDecision(**_base, sell_reasons={"AAPL": "x"})
    with pytest.raises(ValidationError):
        StrategistDecision(**_base, update_reasons={"AAPL": "x"})


def test_sell_rationale_accessible_via_stance():
    """Sell rationale is read from TickerStance.rationale after A-013 tail collapse.

    The former ``sell_reasons`` dict duplicated this value verbatim.
    Consumers should now iterate ``decision.stances`` and filter by
    ``intent == 'sell'`` to retrieve the prose.
    """
    sell_stance = TickerStance(
        ticker="MSFT",
        intent="sell",
        rationale="lock in profits — thesis reached target",
    )
    d = StrategistDecision(
        stances=[sell_stance],
        target_weights={"MSFT": 0.0},
        decision_tag="sell",
        reasoning="x",
        confidence=0.5,
    )

    # The rationale is accessible from the stance directly.
    sell_stances = [s for s in d.stances if s.intent == "sell"]
    assert len(sell_stances) == 1
    assert sell_stances[0].rationale == "lock in profits — thesis reached target"

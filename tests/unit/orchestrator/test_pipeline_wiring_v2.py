"""Pipeline v2 wiring tests — Tier 1, no LLM."""
from __future__ import annotations

from broker.fake import FakeBroker
from orchestrator.pipeline import build_pipeline


def test_pipeline_includes_strategist_decision_writer():
    """The pipeline must include the StrategistDecisionWriter stage between
    Strategist and RiskGate, so per-ticker stances are persisted before
    risk-gating runs."""
    pipe = build_pipeline(broker=FakeBroker(starting_cash=1000.0, prices={}), db_session=None)
    names = [a.name for a in pipe.sub_agents]
    assert "Strategist" in names
    assert "StrategistDecisionWriter" in names
    rg_name = "RiskGate" if "RiskGate" in names else "RiskGateAgent"
    assert rg_name in names
    si = names.index("Strategist")
    wi = names.index("StrategistDecisionWriter")
    rg = names.index(rg_name)
    assert si < wi < rg


def test_pipeline_stage_count_increased_by_one():
    """The decision writer adds one stage.

    Pre-Plan-C count was 7 (analyst_pool, attribution_writer, strategist, risk_gate,
    executor, memory_writer, snapshotter). Plan C adds StrategistDecisionWriter → 8.
    """
    pipe = build_pipeline(broker=FakeBroker(starting_cash=1000.0, prices={}), db_session=None)
    assert len(pipe.sub_agents) == 8

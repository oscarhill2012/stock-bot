"""Pipeline structural tests — no LLM calls."""
from google.adk.agents import SequentialAgent
from broker.fake import FakeBroker
from orchestrator.pipeline import build_pipeline


def test_build_pipeline_returns_sequential_agent():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    assert isinstance(pipeline, SequentialAgent)


def test_pipeline_name():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    assert pipeline.name == "HourlyTick"


def test_pipeline_has_six_stages():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    assert len(pipeline.sub_agents) == 6


def test_pipeline_stage_names():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    names = [a.name for a in pipeline.sub_agents]
    assert names[0] == "AnalystPool"
    assert names[1] == "Strategist"
    assert names[2] == "RiskGate"
    assert names[3] == "Executor"
    assert names[4] == "MemoryWriter"
    assert names[5] == "Snapshotter"

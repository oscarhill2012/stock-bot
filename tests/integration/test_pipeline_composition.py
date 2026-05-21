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


def test_pipeline_has_eight_stages():
    """Plan C adds StrategistDecisionWriter between Strategist and RiskGate → 8 stages."""
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    assert len(pipeline.sub_agents) == 8


def test_pipeline_stage_names():
    """Stage order: analyst pool → evidence writer → strategist branch → decision writer →
    risk gate → executor → memory writer → snapshotter.

    The strategist slot is a ``SequentialAgent`` named ``StrategistBranch``
    containing ``StrategistContextShim`` and a ``RetryingAgentWrapper``
    around the ``Strategist`` ``LlmAgent``.  The retry wrap lives *inside*
    the SequentialAgent so ContextShim's ``state_delta`` event reaches the
    ADK Runner before the LlmAgent reads it.  The outer pipeline still sees
    eight top-level stages.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker)
    names = [a.name for a in pipeline.sub_agents]
    assert names[0] == "AnalystPool"
    assert names[1] == "EvidenceWriter"
    assert names[2] == "StrategistBranch"
    assert names[3] == "StrategistDecisionWriter"
    assert names[4] == "RiskGate"
    assert names[5] == "Executor"
    assert names[6] == "MemoryWriter"
    assert names[7] == "Snapshotter"

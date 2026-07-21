"""Pipeline structural tests — no LLM calls."""
from google.adk.agents import SequentialAgent

from broker.fake import FakeBroker
from orchestrator.pipeline import build_pipeline


def test_build_pipeline_returns_sequential_agent():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker, tickers=["AAPL"])
    assert isinstance(pipeline, SequentialAgent)


def test_pipeline_name():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker, tickers=["AAPL"])
    assert pipeline.name == "HourlyTick"


def test_pipeline_has_seven_stages():
    """Plan C adds StrategistDecisionWriter between Strategist and RiskGate.

    MemoryWriter was removed (2026-07-21) — the memory-buffer/day-digest
    context items it fed the strategist prompt were dead weight — so the
    pipeline now has 7 stages, not 8.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker, tickers=["AAPL"])
    assert len(pipeline.sub_agents) == 7


def test_pipeline_stage_names():
    """Stage order: analyst pool → evidence writer → strategist branch → decision writer →
    risk gate → executor → snapshotter.

    The strategist slot is a ``SequentialAgent`` named ``StrategistBranch``
    containing ``StrategistContextShim`` and a ``RetryingAgentWrapper``
    around the ``Strategist`` ``LlmAgent``.  The retry wrap lives *inside*
    the SequentialAgent so ContextShim's ``state_delta`` event reaches the
    ADK Runner before the LlmAgent reads it.  The outer pipeline still sees
    seven top-level stages.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    pipeline = build_pipeline(broker, tickers=["AAPL"])
    names = [a.name for a in pipeline.sub_agents]
    assert names[0] == "AnalystPool"
    assert names[1] == "EvidenceWriter"
    assert names[2] == "StrategistBranch"
    assert names[3] == "StrategistDecisionWriter"
    assert names[4] == "RiskGate"
    assert names[5] == "Executor"
    assert names[6] == "Snapshotter"
    assert "MemoryWriter" not in names

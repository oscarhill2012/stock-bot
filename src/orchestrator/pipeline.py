"""Build the HourlyTick SequentialAgent pipeline."""
from __future__ import annotations

from google.adk.agents import SequentialAgent


def _build_analyst_pool():
    """Build a fresh AnalystPool each time to avoid single-parent constraint."""
    from google.adk.agents import ParallelAgent
    from agents.analysts.technical.agent import _build_technical_analyst
    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.sentiment.agent import _build_sentiment_analyst
    from agents.analysts.smart_money.agent import _build_smart_money_analyst
    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            _build_technical_analyst(),
            _build_fundamental_analyst(),
            _build_sentiment_analyst(),
            _build_smart_money_analyst(),
        ],
    )


def _build_strategist():
    """Build a fresh Strategist LlmAgent each time."""
    from google.adk.agents import LlmAgent
    from agents.strategist.agent import _strategist_validation_callback
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistDecision
    return LlmAgent(
        name="Strategist",
        model="gemini-2.0-pro-001",
        instruction=STRATEGIST_INSTRUCTION,
        output_schema=StrategistDecision,
        output_key="strategist_decision",
        after_agent_callback=_strategist_validation_callback,
    )


def _build_memory_writer():
    """Build a fresh MemoryWriter each time."""
    from agents.memory.writer import MemoryWriter
    return MemoryWriter()


def build_pipeline(broker, db_session=None) -> SequentialAgent:
    """Compose the full hourly tick pipeline."""
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.attribution.writer import build_attribution_writer
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(),
            build_attribution_writer(db_session),
            _build_strategist(),
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )

"""Build the HourlyTick SequentialAgent pipeline."""
from __future__ import annotations

from google.adk.agents import SequentialAgent


def _build_analyst_pool():
    """Build a fresh AnalystPool each time to avoid single-parent constraint.

    Five children: Fundamental, News, SmartMoney, Technical, and Social —
    all three deterministic analysts (SmartMoney, Technical, Social) are
    ``BaseAgent`` subclasses that derive verdicts via heuristics in
    ``_run_async_impl`` with no LLM involvement.
    """
    from google.adk.agents import ParallelAgent

    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.agent import _build_news_analyst
    from agents.analysts.smart_money.agent import _build_smart_money_analyst
    from agents.analysts.social.agent import _build_social_analyst
    from agents.analysts.technical.agent import _build_technical_analyst

    # Load heuristics once so all deterministic analysts share the same cached
    # config object — consumed by technical, social, and smart_money BaseAgent
    # analysts.
    h = load_heuristics()

    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            _build_technical_analyst(h.technical),         # deterministic BaseAgent
            _build_fundamental_analyst(h.fundamental_vocabulary),  # narrowed LlmAgent
            _build_news_analyst(h.news_vocabulary),
            _build_social_analyst(h.social),            # deterministic BaseAgent
            _build_smart_money_analyst(h.smart_money),  # deterministic BaseAgent
        ],
    )


def _build_strategist():
    """Build a fresh Strategist LlmAgent each time.

    Wires both the v2 before-callback (held-view + evidence-view) and the
    validation after-callback so the prompt template receives real holdings
    and per-ticker evidence before the LLM runs.
    """
    from google.adk.agents import LlmAgent

    from agents.strategist.agent import (
        _composite_before_callback,
        _strategist_validation_callback,
    )
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistDecision
    return LlmAgent(
        name="Strategist",
        model="gemini-2.5-pro",
        instruction=STRATEGIST_INSTRUCTION,
        output_schema=StrategistDecision,
        output_key="strategist_decision",
        before_agent_callback=_composite_before_callback,
        after_agent_callback=_strategist_validation_callback,
    )


def _build_memory_writer():
    """Build a fresh MemoryWriter each time."""
    from agents.memory.writer import MemoryWriter
    return MemoryWriter()


def build_pipeline(broker, db_session=None) -> SequentialAgent:
    """Compose the full hourly tick pipeline."""
    from agents.contract.evidence_writer import build_evidence_writer
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.strategist.decision_writer import build_strategist_decision_writer
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(),
            build_evidence_writer(db_session),
            _build_strategist(),
            build_strategist_decision_writer(db_session),
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )

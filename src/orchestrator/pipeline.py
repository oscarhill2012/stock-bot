"""Build the HourlyTick SequentialAgent pipeline."""
from __future__ import annotations

from google.adk.agents import SequentialAgent


def _build_analyst_pool():
    """Build a fresh AnalystPool each time to avoid single-parent constraint.

    Four children: Fundamental, News, Technical, and Social — the two
    deterministic analysts (Technical, Social) are ``BaseAgent`` subclasses
    that derive verdicts via heuristics in ``_run_async_impl`` with no LLM
    involvement.

    SmartMoney is currently shelved (2026-05-19).  Its two input streams are
    both unusable: ``politician_trades`` has no free PIT-correct historical
    source (FMP / Quiver are paid for back-data), and ``notable_holders``
    uses ``Company.get_filings()`` which returns filer-side filings (the
    issuer's own 10-K, 10-Q etc.) rather than subject-side 13D/13G
    holdings — see ``src/data/providers/notable_holders/edgar.py``.  The
    analyst module, extractor, heuristics, and consumer hooks
    (``smart_money_evidence`` key in ``evidence_writer``, ``memory.writer``,
    ``strategist.evidence_view``) all remain in the tree so the analyst
    can be revived in one line when a fix lands.  Downstream consumers
    already cope with the key being absent.
    """
    from google.adk.agents import ParallelAgent

    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.agent import _build_news_analyst
    from agents.analysts.social.agent import _build_social_analyst
    from agents.analysts.technical.agent import _build_technical_analyst

    # Load heuristics once so all deterministic analysts share the same cached
    # config object — consumed by the technical and social BaseAgent analysts.
    h = load_heuristics()

    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            _build_technical_analyst(h.technical),         # deterministic BaseAgent
            _build_fundamental_analyst(h.fundamental_vocabulary),  # narrowed LlmAgent
            _build_news_analyst(h.news_vocabulary),
            _build_social_analyst(h.social),            # deterministic BaseAgent
            # _build_smart_money_analyst(h.smart_money) — shelved (see docstring).
            # Re-enable by re-importing _build_smart_money_analyst above and
            # uncommenting the line below once notable_holders / politician
            # trades have working PIT-correct providers.
        ],
    )


def _build_strategist():
    """Build a fresh Strategist LlmAgent each time.

    Wires both the v2 before-callback (held-view + evidence-view) and the
    validation after-callback so the prompt template receives real holdings
    and per-ticker evidence before the LLM runs.
    """
    import os

    from google.adk.agents import LlmAgent

    from agents.strategist.agent import (
        _composite_before_callback,
        _strategist_validation_callback,
    )
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistDecision
    from observability.trace import make_llm_trace_callbacks

    # Wire the LLM-trace callbacks only when STOCKBOT_TRACE=1.  On production
    # runs both callbacks are ``None`` and ADK skips the hooks entirely.
    model_name = "gemini-2.5-pro"
    before_model = None
    after_model = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        before_model, after_model = make_llm_trace_callbacks(
            "05_strategist_llm", model=model_name
        )

    return LlmAgent(
        name="Strategist",
        model=model_name,
        instruction=STRATEGIST_INSTRUCTION,
        output_schema=StrategistDecision,
        output_key="strategist_decision",
        before_agent_callback=_composite_before_callback,
        after_agent_callback=_strategist_validation_callback,
        before_model_callback=before_model,
        after_model_callback=after_model,
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

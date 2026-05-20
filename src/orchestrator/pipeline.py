"""Build the HourlyTick SequentialAgent pipeline."""
from __future__ import annotations

from google.adk.agents import SequentialAgent


def _build_analyst_pool():
    """Build the AnalystPool — Sequential[Parallel[Tech,Social], Fund, News].

    Fundamental and News are sequential so each owns the state_delta rail
    unambiguously (see A2.7 — they wrap their LlmAgent in a
    ``YieldingAnalystWrapper`` to republish the evidence write as a yielded
    Event).  Technical and Social remain parallel — both are BaseAgent
    subclasses that already yield state_delta directly (A1.1 / A1.2), so
    Rule 4's unique-output-key invariant is satisfied (they write to
    distinct keys).

    SmartMoney is shelved (2026-05-19).  The analyst module remains so a
    one-line uncomment will revive it once notable_holders / politician
    trades have working PIT-correct providers.
    """
    from google.adk.agents import ParallelAgent, SequentialAgent

    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.agent import _build_news_analyst
    from agents.analysts.social.agent import _build_social_analyst
    from agents.analysts.technical.agent import _build_technical_analyst

    # Load heuristics once so all deterministic analysts share the same
    # cached config object — consumed by the technical and social BaseAgent
    # analysts.
    h = load_heuristics()

    # Technical and Social are BaseAgent subclasses with distinct output
    # keys — safe to run in parallel (Rule 4 satisfied).
    parallel_deterministic = ParallelAgent(
        name="DeterministicAnalysts",
        sub_agents=[
            _build_technical_analyst(h.technical),
            _build_social_analyst(h.social),
        ],
    )

    # Fundamental and News each own the state_delta rail: they run
    # sequentially so there is no ambiguity over which agent's write lands.
    return SequentialAgent(
        name="AnalystPool",
        sub_agents=[
            parallel_deterministic,
            _build_fundamental_analyst(h.fundamental_vocabulary),
            _build_news_analyst(h.news_vocabulary),
            # _build_smart_money_analyst(h.smart_money) — shelved (see docstring).
            # Re-enable by re-importing _build_smart_money_analyst above and
            # appending it here once notable_holders / politician trades have
            # working PIT-correct providers.
        ],
    )


def _build_strategist():
    """Build the Strategist branch — SequentialAgent[ContextShim, LlmAgent].

    The ContextShim hydrates ``temp:held_positions_view``,
    ``temp:ticker_evidence``, and ``temp:ticker_evidence_objects`` via a
    yielded ``Event(state_delta=…)`` (contract Rule 1).  The downstream
    LlmAgent then resolves those keys via ADK's instruction-variable
    substitution and emits its ``StrategistDecision``.  The validation +
    derivation work stays as an ``after_agent_callback`` on the LlmAgent —
    see the in-tick callback carve-out documented in
    ``docs/contract-invariants.md`` §C-Rule 1.
    """
    import os

    from google.adk.agents import LlmAgent, SequentialAgent

    from agents.strategist.agent import _strategist_validation_callback
    from agents.strategist.context_shim import StrategistContextShim
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION
    from agents.strategist.schema import StrategistDecision
    from observability.trace import make_llm_trace_callbacks

    model_name = "gemini-3.5-flash"
    before_model = None
    after_model = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        before_model, after_model = make_llm_trace_callbacks(
            "05_strategist_llm", model=model_name,
        )

    llm = LlmAgent(
        name="Strategist",
        model=model_name,
        instruction=STRATEGIST_INSTRUCTION,
        output_schema=StrategistDecision,
        output_key="strategist_decision",
        # before_agent_callback intentionally None — StrategistContextShim
        # now does the work that _composite_before_callback used to do.
        after_agent_callback=_strategist_validation_callback,
        before_model_callback=before_model,
        after_model_callback=after_model,
    )

    return SequentialAgent(
        name="StrategistBranch",
        sub_agents=[StrategistContextShim(), llm],
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

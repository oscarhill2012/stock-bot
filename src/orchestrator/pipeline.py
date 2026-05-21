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

    Both LLM-bearing branches (Fundamental and News) are wrapped in a
    :class:`agents.llm_retry.RetryingAgentWrapper` at this pipeline-
    composition layer so transient Vertex AI 429s are retried with
    exponential backoff before failing the tick.  The retry wrapper is
    deliberately applied here rather than inside the factories so the
    factory tests (which assert ``isinstance(..., YieldingAnalystWrapper)``)
    continue to work unchanged — retry is an orchestration concern, not
    part of the agent's identity.

    SmartMoney is shelved (2026-05-19).  The analyst module remains so a
    one-line uncomment will revive it once notable_holders / politician
    trades have working PIT-correct providers.
    """
    from google.adk.agents import ParallelAgent, SequentialAgent

    from agents.analysts.fundamental.agent import build_fundamental_analyst
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.agent import build_news_analyst
    from agents.analysts.social.agent import _build_social_analyst
    from agents.analysts.technical.agent import _build_technical_analyst
    from agents.llm_retry import RetryingAgentWrapper

    # Load heuristics once so all deterministic analysts share the same
    # cached config object — consumed by the technical and social BaseAgent
    # analysts.
    h = load_heuristics()

    # Technical and Social are BaseAgent subclasses with distinct output
    # keys — safe to run in parallel (Rule 4 satisfied).  Neither makes an
    # LLM call so they don't need the retry wrapper.
    parallel_deterministic = ParallelAgent(
        name="DeterministicAnalysts",
        sub_agents=[
            _build_technical_analyst(h.technical),
            _build_social_analyst(h.social),
        ],
    )

    # Fundamental and News each own the state_delta rail: they run
    # sequentially so there is no ambiguity over which agent's write
    # lands.  Each is wrapped in RetryingAgentWrapper so a Vertex 429 on
    # the underlying LlmAgent triggers a backoff-retry rather than
    # failing the whole tick.
    fundamental_branch = RetryingAgentWrapper(
        name  = "FundamentalAnalystRetrying",
        inner = build_fundamental_analyst(h.fundamental_vocabulary),
    )

    news_branch = RetryingAgentWrapper(
        name  = "NewsAnalystRetrying",
        inner = build_news_analyst(h.news_vocabulary),
    )

    return SequentialAgent(
        name="AnalystPool",
        sub_agents=[
            parallel_deterministic,
            fundamental_branch,
            news_branch,
            # _build_smart_money_analyst(h.smart_money) — shelved (see docstring).
            # Re-enable by re-importing _build_smart_money_analyst above and
            # appending it here once notable_holders / politician trades have
            # working PIT-correct providers.  When revived, wrap it in
            # RetryingAgentWrapper too.
        ],
    )


def _build_strategist():
    """Thin delegate to :func:`agents.strategist.agent.build_strategist`.

    Kept as a stable module-level symbol in ``orchestrator.pipeline`` so that
    existing backtest smoke tests which do
    ``mock.patch("orchestrator.pipeline._build_strategist", ...)`` continue to
    work without churn.  All actual construction logic — including the
    config-driven model selection and the ``RetryingAgentWrapper`` around the
    LlmAgent — lives in the strategist's own module, so the pipeline does
    not pick the strategist's model any more than it picks its prompt
    template.

    ``build_strategist()`` returns ``SequentialAgent[ContextShim,
    RetryingAgentWrapper[LlmAgent]]`` named ``"StrategistBranch"``.  The
    retry wrap lives *inside* the SequentialAgent (around the LlmAgent only)
    so ContextShim's ``state_delta`` event reaches the ADK Runner and is
    applied to session state before the LlmAgent's instruction template
    renders.  See ``build_strategist``'s docstring for the design rationale.
    """

    from agents.strategist.agent import build_strategist

    return build_strategist()


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

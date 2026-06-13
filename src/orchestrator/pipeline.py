"""Build the HourlyTick SequentialAgent pipeline."""
from __future__ import annotations

from google.adk.agents import SequentialAgent


def _build_analyst_pool(tickers: list[str]):
    """Build the AnalystPool — Parallel[Technical, Fund, News].

    Phase 9 changes
    ---------------
    Fundamental and News are now per-ticker fan-out branches constructed
    from the watchlist.  Each is a
    ``SequentialAgent[FetchAgent, ParallelAgent[PerTickerBranches], JoinerAgent]``
    built via ``build_fundamental_branch`` / ``build_news_branch``.

    The outer ``RetryingAgentWrapper`` that previously wrapped each branch
    at this composition layer is dropped — retries now live *inside* each
    per-ticker ``IsolatedFailureWrapper`` child so that one ticker's 429
    backoff does not block the other tickers running in the same branch.

    Across-analyst parallelism (post-Phase-9): the AnalystPool itself is a
    ``ParallelAgent`` so Fund and News run concurrently with the
    deterministic Parallel block.  The A2.7 sequential-rail guard
    ("Fundamental and News each own the state_delta rail unambiguously")
    is retired because per-ticker fan-out writes only to disjoint durable
    keys (``news_verdicts``/``news_evidence`` vs ``fundamental_verdicts``/
    ``fundamental_evidence``) and ``IsolatedFailureWrapper`` prevents
    sibling cancellation cascades inside ADK's ``asyncio.TaskGroup``.

    Technical is the sole ``BaseAgent`` currently wired in
    ``DeterministicAnalysts``.  Two analysts are shelved:

    - SmartMoney (shelved 2026-05-19): revive once notable_holders /
      politician-trades providers are PIT-correct.
    - Social (shelved 2026-06-13): revive once ``context_shim`` is updated
      to index ``social_evidence`` AND ``DEFAULT_ANALYST_WEIGHTS`` is
      updated in ``contract.digest``.

    Args:
        tickers: The current watchlist.  Drives the number of per-ticker
                 sub-agents built inside the Fundamental and News branches.

    Returns:
        ``ParallelAgent`` named ``"AnalystPool"`` containing
        ``[DeterministicAnalysts, FundamentalAnalystBranch, NewsAnalystBranch]``,
        all three running concurrently.
    """
    from google.adk.agents import ParallelAgent

    from agents.analysts.fundamental.agent import build_fundamental_branch
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.agent import build_news_branch
    from agents.analysts.technical.agent import _build_technical_analyst
    # _build_social_analyst — shelved (2026-06-13).  The analyst module remains
    # so a one-line uncomment will revive it.  Re-enable by re-importing
    # _build_social_analyst here and appending it to the DeterministicAnalysts
    # sub_agents list below, AND wiring ``social_evidence`` into
    # ``agents.strategist.context_shim`` (which currently never reads it),
    # AND re-adding ``"social": 1.0`` to ``DEFAULT_ANALYST_WEIGHTS`` in
    # ``contract.digest``.  All three changes are required together.

    # Load heuristics once so all deterministic analysts share the same
    # cached config object — consumed by the technical BaseAgent analyst.
    h = load_heuristics()

    # Technical is the sole deterministic BaseAgent currently wired.  Social
    # is shelved (see comment above); SmartMoney is also shelved (see below).
    # Neither shelved analyst makes an LLM call so re-adding them here does
    # not require a retry wrapper.
    parallel_deterministic = ParallelAgent(
        name="DeterministicAnalysts",
        sub_agents=[
            _build_technical_analyst(h.technical),
            # _build_social_analyst(h.social) — shelved (see comment above).
        ],
    )

    # Phase 9 fan-out: one per-ticker branch per ticker in the watchlist.
    # Each branch owns its own IsolatedFailureWrapper + RetryingAgentWrapper
    # so failures and retries are scoped to individual tickers.
    fundamental_branch = build_fundamental_branch(
        h.fundamental_vocabulary, tickers=tickers,
    )

    news_branch = build_news_branch(
        h.news_vocabulary, tickers=tickers,
    )

    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            parallel_deterministic,
            fundamental_branch,
            news_branch,
            # _build_smart_money_analyst(h.smart_money) — shelved (see docstring).
            # Re-enable by re-importing _build_smart_money_analyst above and
            # appending it here once notable_holders / politician trades have
            # working PIT-correct providers.
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


def build_pipeline(broker, db_session=None, *, tickers: list[str]) -> SequentialAgent:
    """Compose the full hourly tick pipeline.

    Phase 9: ``tickers`` is a required keyword-only argument.  Both
    lifecycles (live ``tick.py`` and backtest ``driver.py``) call
    ``build_pipeline`` per invocation with the current
    ``state["tickers"]``.

    Args:
        broker:     Broker instance (``FakeBroker`` for backtests,
                    ``Trading212Broker`` for live runs).
        db_session: Optional SQLAlchemy session for persistence writers.
        tickers:    The current watchlist.  Drives per-ticker fan-out of
                    the News and Fundamental analyst branches.  Passing an
                    empty list is valid (the branches will contain no
                    per-ticker sub-agents) but produces a degenerate pipeline
                    that will emit only no-data verdicts from both LLM
                    analysts.

    Returns:
        ``SequentialAgent`` named ``"HourlyTick"`` wiring the full
        analyst → evidence-writer → strategist → risk-gate → executor →
        memory-writer → snapshotter pipeline.
    """
    from agents.contract.evidence_writer import build_evidence_writer
    from agents.executor.agent import build_executor
    from agents.memory.writer import MemoryWriter
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.strategist.decision_writer import build_strategist_decision_writer

    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(tickers),
            build_evidence_writer(db_session),
            _build_strategist(),
            build_strategist_decision_writer(db_session),
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            MemoryWriter(),  # fresh instance each pipeline build — must not be cached across ticks
            build_snapshotter(broker, db_session),
        ],
    )

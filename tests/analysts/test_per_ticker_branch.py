"""Per-ticker branch factory tests — News + Fundamental.

The factory must produce:
  IsolatedFailureWrapper[RetryingAgentWrapper[LlmAgent]]
with:
  - output_schema=TickerVerdict
  - output_key="temp:news_verdict_<TICKER>"
  - instruction containing the ticker substituted in
  - no after_agent_callback (evidence-build moves to the joiner)
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.per_ticker import build_news_branch_for_ticker
from agents.llm_retry import RetryingAgentWrapper
from contract.evidence import TickerVerdict
from google.adk.agents import LlmAgent


def _news_vocab() -> NewsVocabulary:
    """Return a minimal NewsVocabulary suitable for test use."""
    return NewsVocabulary(
        catalysts=["earnings", "guidance"],
        novelty=["new", "ongoing"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_news_branch_is_isolated_wrapping_retrying_wrapping_llm():
    """The wrapper composition is exact: IsolatedFailureWrapper(Retrying(LlmAgent))."""

    from agents.isolated_failure import IsolatedFailureWrapper  # actual import path

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())

    assert isinstance(branch, IsolatedFailureWrapper)
    assert isinstance(branch.inner, RetryingAgentWrapper)
    assert isinstance(branch.inner.inner, LlmAgent)


def test_news_branch_output_schema_and_key():
    """output_schema is TickerVerdict; output_key is temp:news_verdict_<TICKER>."""

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())
    llm = branch.inner.inner

    assert llm.output_schema is TickerVerdict
    assert llm.output_key == "temp:news_verdict_AAPL"


def test_news_branch_has_no_after_agent_callback():
    """The per-ticker LlmAgent must not own evidence-build — that moved to the joiner."""

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())
    llm = branch.inner.inner

    assert llm.after_agent_callback is None
    # And no before_agent_callback either — fetch lives in NewsFetchAgent.
    assert llm.before_agent_callback is None


def test_news_branch_instruction_pins_ticker():
    """The rendered instruction must reference the specific ticker, not a placeholder."""

    branch = build_news_branch_for_ticker("AAPL", _news_vocab())
    llm = branch.inner.inner

    # {ticker} must be substituted at construction time; only ADK's
    # {news_context} runtime placeholder remains as a single-brace token.
    assert "{ticker}" not in llm.instruction
    assert "AAPL" in llm.instruction
    assert "{news_context}" in llm.instruction


# ---------------------------------------------------------------------------
# Fundamental branch tests
# ---------------------------------------------------------------------------

def _fundamental_vocab():
    """Return a minimal FundamentalVocabulary suitable for test use.

    Populated from the field names defined in
    ``agents.analysts.heuristics.FundamentalVocabulary``, mirroring the
    pattern in ``tests/analysts/fundamental/test_prompts.py``.  Every list
    must contain at least one entry — the model rejects empty fields.

    Returns
    -------
    FundamentalVocabulary
        A minimal but valid vocabulary instance for branch-factory tests.
    """
    from agents.analysts.heuristics import FundamentalVocabulary

    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=[
            "regulatory",
            "litigation",
            "macro",
            "going_concern",
        ],
        insider_signals=[
            "cluster_buying",
            "cluster_selling",
            "planned_sale_dominant",
            "discretionary_sale_dominant",
            "option_exercise_hold",
            "option_exercise_dump",
            "mixed",
        ],
    )


def test_fundamental_branch_composition():
    """Same wrapper composition as News, with fundamental-specific output key."""

    from agents.analysts.fundamental.per_ticker import build_fundamental_branch_for_ticker
    from agents.isolated_failure import IsolatedFailureWrapper
    from agents.llm_retry import RetryingAgentWrapper
    from contract.evidence import TickerVerdict
    from google.adk.agents import LlmAgent

    branch = build_fundamental_branch_for_ticker("AAPL", _fundamental_vocab())

    assert isinstance(branch, IsolatedFailureWrapper)
    assert isinstance(branch.inner, RetryingAgentWrapper)

    llm = branch.inner.inner
    assert isinstance(llm, LlmAgent)
    assert llm.output_schema is TickerVerdict
    assert llm.output_key   == "temp:fundamental_verdict_AAPL"
    assert llm.before_agent_callback is None
    assert llm.after_agent_callback  is None
    assert "{ticker}" not in llm.instruction
    assert "AAPL" in llm.instruction
    assert "{fundamental_context}" in llm.instruction

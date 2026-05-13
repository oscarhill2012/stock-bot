"""News analyst LlmAgent — evidence-only output (D3 / Phase 5 Task 6).

The LLM is instructed to emit ``AnalystVerdict``-shaped dicts keyed as
``news_verdicts`` in session state.  The ``make_evidence_callback`` after-
callback then converts those verdicts into ``AnalystEvidence`` records and
writes them to ``state["news_evidence"]``.

Renamed from SentimentAnalyst in Task 6. Provider input narrowed to
``news/`` only; social_sentiment migrates to the new Social analyst (Task 7).
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_evidence_callback
from contract.extractors.news import extract_news_features

from .fetch import news_fetch_callback
from .prompts import NEWS_INSTRUCTION

# Evidence-only after-callback: reads verdicts, runs feature extractor,
# writes state["news_evidence"].  No legacy signals path.
_after = make_evidence_callback(
    analyst="news",
    extractor=extract_news_features,
    verdicts_state_key="news_verdicts",
)


# Module-level singleton used by unit tests that construct the agent directly.
news_analyst = LlmAgent(
    name="NewsAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=NEWS_INSTRUCTION,
    output_key="news_verdicts",
    before_agent_callback=news_fetch_callback,
    after_agent_callback=_after,
)


def _build_news_analyst() -> LlmAgent:
    """Construct a fresh ``NewsAnalyst`` instance.

    Returns a brand-new ``LlmAgent`` wired with the same evidence-only
    callback, fetch step, and prompt as the module-level singleton.
    Used by the orchestrator factory so each run gets an independent agent.

    Returns:
        LlmAgent: A fully-configured news analyst agent instance.
    """
    return LlmAgent(
        name="NewsAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=NEWS_INSTRUCTION,
        output_key="news_verdicts",
        before_agent_callback=news_fetch_callback,
        after_agent_callback=_after,
    )

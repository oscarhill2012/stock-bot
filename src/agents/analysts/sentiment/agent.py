"""Sentiment analyst LlmAgent — evidence-only output (D3).

The LLM is instructed to emit ``AnalystVerdict``-shaped dicts keyed as
``sentiment_verdicts`` in session state.  The ``make_evidence_callback`` after-
callback then converts those verdicts into ``AnalystEvidence`` records and writes
them to ``state["sentiment_evidence"]``.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_evidence_callback
from contract.extractors.sentiment import extract_sentiment_features

from .fetch import sentiment_fetch_callback
from .prompts import SENTIMENT_INSTRUCTION

# Evidence-only after-callback: reads verdicts, runs feature extractor,
# writes state["sentiment_evidence"].  No legacy signals path.
_after = make_evidence_callback(
    analyst="sentiment",
    extractor=extract_sentiment_features,
    verdicts_state_key="sentiment_verdicts",
)


# Module-level singleton used by unit tests that construct the agent directly.
sentiment_analyst = LlmAgent(
    name="SentimentAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=SENTIMENT_INSTRUCTION,
    output_key="sentiment_verdicts",
    before_agent_callback=sentiment_fetch_callback,
    after_agent_callback=_after,
)


def _build_sentiment_analyst() -> LlmAgent:
    """Construct a fresh ``SentimentAnalyst`` instance.

    Returns a brand-new ``LlmAgent`` wired with the same evidence-only
    callback, fetch step, and prompt as the module-level singleton.
    Used by the orchestrator factory so each run gets an independent agent.
    """
    return LlmAgent(
        name="SentimentAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=SENTIMENT_INSTRUCTION,
        output_key="sentiment_verdicts",
        before_agent_callback=sentiment_fetch_callback,
        after_agent_callback=_after,
    )

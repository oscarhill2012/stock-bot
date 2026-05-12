"""Fundamental analyst LlmAgent — evidence-only output (D3).

The LLM is instructed to emit ``AnalystVerdict``-shaped dicts keyed as
``fundamental_verdicts`` in session state.  The ``make_evidence_callback`` after-
callback then converts those verdicts into ``AnalystEvidence`` records and writes
them to ``state["fundamental_evidence"]``.  No legacy ``fundamental_signals`` key
is written after D3.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_evidence_callback
from contract.extractors.fundamental import extract_fundamental_features

from .fetch import fundamental_fetch_callback
from .prompts import FUNDAMENTAL_INSTRUCTION

# Evidence-only after-callback: reads verdicts, runs feature extractor,
# writes state["fundamental_evidence"].  No legacy signals path.
_after = make_evidence_callback(
    analyst="fundamental",
    extractor=extract_fundamental_features,
    verdicts_state_key="fundamental_verdicts",
)


# Module-level singleton used by unit tests that construct the agent directly.
fundamental_analyst = LlmAgent(
    name="FundamentalAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=FUNDAMENTAL_INSTRUCTION,
    output_key="fundamental_verdicts",
    before_agent_callback=fundamental_fetch_callback,
    after_agent_callback=_after,
)


def _build_fundamental_analyst() -> LlmAgent:
    """Construct a fresh ``FundamentalAnalyst`` instance.

    Returns a brand-new ``LlmAgent`` wired with the same evidence-only
    callback, fetch step, and prompt as the module-level singleton.
    Used by the orchestrator factory so each run gets an independent agent.
    """
    return LlmAgent(
        name="FundamentalAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=FUNDAMENTAL_INSTRUCTION,
        output_key="fundamental_verdicts",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=_after,
    )

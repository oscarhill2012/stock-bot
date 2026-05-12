"""Technical analyst LlmAgent — evidence-only output (D3).

The LLM is instructed to emit ``AnalystVerdict``-shaped dicts keyed as
``technical_verdicts`` in session state.  The ``make_evidence_callback`` after-
callback then converts those verdicts into ``AnalystEvidence`` records and writes
them to ``state["technical_evidence"]``.  No legacy ``technical_signals`` key is
written after D3.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_evidence_callback
from contract.extractors.technical import extract_technical_features

from .fetch import technical_fetch_callback
from .prompts import TECHNICAL_INSTRUCTION

# Evidence-only after-callback: reads verdicts, runs feature extractor,
# writes state["technical_evidence"].  No legacy signals path.
_after = make_evidence_callback(
    analyst="technical",
    extractor=extract_technical_features,
    verdicts_state_key="technical_verdicts",
)


# Module-level singleton — used directly by unit tests and the orchestrator.
technical_analyst = LlmAgent(
    name="TechnicalAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=TECHNICAL_INSTRUCTION,
    output_key="technical_verdicts",
    before_agent_callback=technical_fetch_callback,
    after_agent_callback=_after,
)


def _build_technical_analyst() -> LlmAgent:
    """Construct a fresh ``TechnicalAnalyst`` instance.

    Returns a brand-new ``LlmAgent`` wired with the same evidence-only
    callback, fetch step, and prompt as the module-level singleton.
    Used by the orchestrator factory so each run gets an independent agent.
    """
    return LlmAgent(
        name="TechnicalAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=TECHNICAL_INSTRUCTION,
        output_key="technical_verdicts",
        before_agent_callback=technical_fetch_callback,
        after_agent_callback=_after,
    )

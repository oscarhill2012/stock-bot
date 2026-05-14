"""Fundamental analyst LlmAgent — closed-vocab narrowed (Phase 5 Task 10).

The LLM is instructed to emit ``AnalystVerdict``-shaped dicts keyed as
``fundamental_verdicts`` in session state.  The ``make_evidence_callback``
after-callback then converts those verdicts into ``AnalystEvidence`` records
and writes them to ``state["fundamental_evidence"]``.

The agent factory ``_build_fundamental_analyst(vocab)`` now accepts a
``FundamentalVocabulary`` at construction time and renders the closed-vocab
prompt via ``build_fundamental_instruction`` before wiring the
``LlmAgent``.  The module-level singleton uses the default heuristics config
so unit tests that import the module directly still work.

When the environment variable ``STOCKBOT_TRACE=1`` is set, the factory also
attaches ``before_model_callback`` and ``after_model_callback`` hooks that
capture the raw LLM prompt and response into a ``TraceWriter`` (if one is
present in session state under the ``"_trace"`` key).
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import FundamentalVocabulary, load_heuristics
from contract.evidence import VerdictBatch
from contract.extractors.fundamental import extract_fundamental_features
from observability.trace import make_llm_trace_callbacks

from .fetch import fundamental_fetch_callback
from .prompts import build_fundamental_instruction

# Evidence-only after-callback: reads verdicts, runs feature extractor,
# writes state["fundamental_evidence"].  No legacy signals path.
_after = make_evidence_callback(
    analyst="fundamental",
    extractor=extract_fundamental_features,
    verdicts_state_key="fundamental_verdicts",
)


def _build_fundamental_analyst(vocab: FundamentalVocabulary) -> LlmAgent:
    """Construct a fresh ``FundamentalAnalyst`` LlmAgent with closed-vocab prompt.

    Renders the instruction by substituting the four closed-vocabulary lists
    (guidance, tone, risks, insider_signals) into the prompt template.  The
    resulting instruction still contains ADK runtime placeholders
    ``{fundamental_context}`` and ``{tickers}`` which ADK's
    ``inject_session_state`` fills each tick from session state written by
    ``fundamental_fetch_callback``.

    When the environment variable ``STOCKBOT_TRACE=1`` is set, the factory
    attaches before/after model callbacks that capture the raw LLM prompt and
    response into the active ``TraceWriter`` (if any).

    Parameters
    ----------
    vocab:
        Validated ``FundamentalVocabulary`` holding the closed-vocab tag lists.

    Returns
    -------
    LlmAgent
        A fully-wired ``FundamentalAnalyst`` ready to be added to the
        ``AnalystPool`` ``ParallelAgent``.
    """
    instruction = build_fundamental_instruction(vocab)
    model = "gemini-2.5-flash-lite"

    # Attach LLM trace callbacks only in trace mode — zero-cost gate.
    before_cb = None
    after_cb = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        before_cb, after_cb = make_llm_trace_callbacks("03_fundamental_llm", model=model)

    return LlmAgent(
        name="FundamentalAnalyst",
        model=model,
        instruction=instruction,
        output_schema=VerdictBatch,
        output_key="fundamental_verdicts",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=_after,
        before_model_callback=before_cb,
        after_model_callback=after_cb,
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
# Built from the default heuristics config so tests that ``import
# fundamental_analyst`` directly still get a valid agent without needing to
# construct one explicitly.  Production code uses ``_build_fundamental_analyst``
# called from the pipeline factory.
# ---------------------------------------------------------------------------

fundamental_analyst = _build_fundamental_analyst(load_heuristics().fundamental_vocabulary)

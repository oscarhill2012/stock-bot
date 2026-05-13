"""News analyst LlmAgent — closed-vocab narrowed (Phase 5 Task 11).

The LLM is instructed to emit ``AnalystVerdict``-shaped dicts keyed as
``news_verdicts`` in session state.  The ``make_evidence_callback`` after-
callback then converts those verdicts into ``AnalystEvidence`` records and
writes them to ``state["news_evidence"]``.

Renamed from SentimentAnalyst in Task 6.  Provider input narrowed to
``news/`` only; social_sentiment migrates to the new Social analyst (Task 7).

The agent factory ``_build_news_analyst(vocab)`` now accepts a
``NewsVocabulary`` at construction time and renders the closed-vocab prompt
via ``build_news_instruction`` before wiring the ``LlmAgent``.  The
module-level singleton uses the default heuristics config so unit tests that
import the module directly still work.

When the environment variable ``STOCKBOT_TRACE=1`` is set, the factory also
attaches ``before_model_callback`` and ``after_model_callback`` hooks that
capture the raw LLM prompt and response into a ``TraceWriter`` (if one is
present in session state under the ``"_trace"`` key).
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import NewsVocabulary, load_heuristics
from contract.evidence import VerdictBatch
from contract.extractors.news import extract_news_features
from observability.trace import TraceWriter

from .fetch import news_fetch_callback
from .prompts import build_news_instruction

# Evidence-only after-callback: reads verdicts, runs feature extractor,
# writes state["news_evidence"].  No legacy signals path.
_after = make_evidence_callback(
    analyst="news",
    extractor=extract_news_features,
    verdicts_state_key="news_verdicts",
)


def _make_llm_trace_before(model: str) -> object:
    """Build a before_model_callback that captures the prompt into the TraceWriter.

    The callback is a no-op if ``state["_trace"]`` is not set, preserving the
    zero-cost production path.

    Parameters
    ----------
    model:
        The model identifier string to record alongside the prompt text.

    Returns
    -------
    Callable
        A before_model_callback compatible with ADK's ``LlmAgent``.
    """
    def _before(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> genai_types.Content | None:
        """Capture the outgoing prompt into the TraceWriter, if active."""
        state = callback_context.state

        # Single dict lookup — no-op on production paths.
        tw = state.get("_trace") if isinstance(state, dict) else None
        if not isinstance(tw, TraceWriter):
            return None

        # Extract the text content from the request; fall back gracefully.
        prompt_parts: list[str] = []
        for content in (llm_request.contents or []):
            for part in (content.parts or []):
                if hasattr(part, "text") and part.text:
                    prompt_parts.append(part.text)

        tw.llm_pair(
            "03_news_llm",
            prompt="\n---\n".join(prompt_parts) or "(no text parts)",
            response="(pending)",
            model=model,
        )
        return None

    return _before


def _make_llm_trace_after(model: str) -> object:
    """Build an after_model_callback that updates the TraceWriter with the LLM response.

    Overwrites the ``"(pending)"`` placeholder written by the before-callback
    with the actual model response text.

    Parameters
    ----------
    model:
        The model identifier string (used to overwrite the ``_in`` section's
        model value for consistency).

    Returns
    -------
    Callable
        An after_model_callback compatible with ADK's ``LlmAgent``.
    """
    def _after_llm(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> genai_types.Content | None:
        """Update the TraceWriter with the model's response text."""
        state = callback_context.state

        tw = state.get("_trace") if isinstance(state, dict) else None
        if not isinstance(tw, TraceWriter):
            return None

        # Extract response text from the LlmResponse content field.
        response_parts: list[str] = []
        if llm_response.content:
            for part in (llm_response.content.parts or []):
                if hasattr(part, "text") and part.text:
                    response_parts.append(part.text)

        response_text = "\n---\n".join(response_parts) or "(no text parts)"

        # Overwrite the _out entry written by llm_pair (the _in was already set).
        tw._sections["03_news_llm_out"] = {
            "model": model,
            "response": response_text,
        }
        return None

    return _after_llm


def _build_news_analyst(vocab: NewsVocabulary) -> LlmAgent:
    """Construct a fresh ``NewsAnalyst`` LlmAgent with closed-vocab prompt.

    Renders the instruction by substituting the three closed-vocabulary lists
    (catalysts, novelty, direction) into the prompt template.  The resulting
    instruction still contains ADK runtime placeholders ``{news_context}`` and
    ``{tickers}`` which ADK's ``inject_session_state`` fills each tick from
    session state written by ``news_fetch_callback``.

    When the environment variable ``STOCKBOT_TRACE=1`` is set, the factory
    attaches before/after model callbacks that capture the raw LLM prompt and
    response into the active ``TraceWriter`` (if any).

    Parameters
    ----------
    vocab:
        Validated ``NewsVocabulary`` holding the closed-vocab tag lists.

    Returns
    -------
    LlmAgent
        A fully-wired ``NewsAnalyst`` ready to be added to the
        ``AnalystPool`` ``ParallelAgent``.
    """
    instruction = build_news_instruction(vocab)
    model = "gemini-2.5-flash-lite"

    # Attach LLM trace callbacks only in trace mode — zero-cost gate.
    before_cb = None
    after_cb = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        before_cb = _make_llm_trace_before(model)
        after_cb = _make_llm_trace_after(model)

    return LlmAgent(
        name="NewsAnalyst",
        model=model,
        instruction=instruction,
        output_schema=VerdictBatch,
        output_key="news_verdicts",
        before_agent_callback=news_fetch_callback,
        after_agent_callback=make_evidence_callback(
            analyst="news",
            extractor=extract_news_features,
            verdicts_state_key="news_verdicts",
        ),
        before_model_callback=before_cb,
        after_model_callback=after_cb,
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
# Built from the default heuristics config so tests that ``import
# news_analyst`` directly still get a valid agent without needing to construct
# one explicitly.  Production code uses ``_build_news_analyst`` called from
# the pipeline factory.
# ---------------------------------------------------------------------------

news_analyst = _build_news_analyst(load_heuristics().news_vocabulary)

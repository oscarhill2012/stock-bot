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

Phase 5 Task 6 adds a disk-backed memoisation cache.  The cache layer is now
wired via the shared ``make_report_cache_callbacks`` factory in
``agents.analysts.cache_callbacks`` — see that module's docstring for the
lifecycle details and the B22 bug-fix that motivates centralising the logic
(specifically, ``_after`` must parse ``llm_response`` directly rather than
reading state, because ADK's ``__maybe_save_output_to_state`` runs after the
after-model-callback chain).

When the environment variable ``STOCKBOT_TRACE=1`` is set, the factory also
attaches trace hooks (after the cache layer) that capture the raw LLM prompt
and response into a ``TraceWriter`` (if one is present in session state under
the ``"_trace"`` key).
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from agents.analysts._base_yield import YieldingAnalystWrapper
from agents.analysts._common import (
    _chain_after,
    _chain_before,
    make_evidence_callback,
)
from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.heuristics import NewsVocabulary, load_heuristics
from agents.analysts.report_cache import (
    NEWS_PROMPT_VERSION,
    news_hash_inputs,
)
from contract.evidence import VerdictBatch
from contract.extractors.news import extract_news_features
from observability.trace import make_llm_trace_callbacks

from .fetch import news_fetch_callback
from .prompts import build_news_instruction

# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _build_news_analyst(vocab: NewsVocabulary) -> YieldingAnalystWrapper:
    """Construct a fresh ``NewsAnalyst`` LlmAgent with closed-vocab prompt + cache.

    Renders the instruction by substituting the three closed-vocabulary lists
    (catalysts, novelty, direction) into the prompt template.  The resulting
    instruction still contains ADK runtime placeholders ``{news_context}`` and
    ``{tickers}`` which ADK's ``inject_session_state`` fills each tick from
    session state written by ``news_fetch_callback``.

    Cache layer:
        ``make_report_cache_callbacks(...)`` (from ``agents.analysts.cache_callbacks``)
        returns before/after hooks that consult the disk cache.  A full cache hit
        short-circuits the LLM call; a miss falls through to the real model.

    Trace layer:
        When ``STOCKBOT_TRACE=1`` is set, trace hooks are chained *after* the
        cache hook so that cache hits are recorded as ``model="cache"`` in the
        trace log.

    Parameters
    ----------
    vocab:
        Validated ``NewsVocabulary`` holding the closed-vocab tag lists.

    Returns
    -------
    YieldingAnalystWrapper
        A fully-wired ``NewsAnalystBranch`` ready to be added to the
        ``AnalystPool`` ``ParallelAgent``.  The inner ``LlmAgent`` is
        accessible via ``.inner`` for tests that need to inspect it directly.
    """
    instruction = build_news_instruction(vocab)
    model = "gemini-2.5-flash-lite"

    # Attach LLM trace callbacks only in trace mode — zero-cost gate.
    trace_before = None
    trace_after  = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks("03_news_llm", model=model)

    # Build cache hooks via the shared factory — these run before the trace hooks
    # so a cache hit is still visible in the trace log (the _before hook emits
    # its own marker).  The lambda unpacks the per-ticker news-data dict and
    # passes the article list to the domain-specific hash function.
    cache_before, cache_after = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = NEWS_PROMPT_VERSION,
        data_state_key     = "temp:news_data",
        verdicts_state_key = "news_verdicts",
        hash_inputs        = lambda d: news_hash_inputs((d or {}).get("news") or []),
        trace_label        = "03_news_llm",
    )

    # Chain: cache first (may short-circuit), then trace.
    before_cb = _chain_before(cache_before, trace_before)
    after_cb  = _chain_after(cache_after, trace_after)

    # Build the inner LlmAgent — all callbacks and config are unchanged from
    # the pre-A2.5 version.  The outer YieldingAnalystWrapper republishes the
    # after_agent_callback's evidence write as a ``state_delta`` yield so the
    # write is durable on persistent ADK session backends (Rule 1 compliance).
    llm = LlmAgent(
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
    return YieldingAnalystWrapper(
        name="NewsAnalystBranch",
        inner=llm,
        evidence_state_key="news_evidence",
        trace_key="02_news_verdict",
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

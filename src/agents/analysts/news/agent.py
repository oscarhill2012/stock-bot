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

Phase 5 Task 6 adds a disk-backed memoisation cache.  Before the model call,
a ``before_model_callback`` consults the hash cache for every watchlist ticker.
If all tickers hit the cache, the LLM round-trip is skipped and verdicts are
loaded directly from disk.  After a real LLM call, an ``after_model_callback``
persists the fresh verdicts so subsequent ticks on the same data are free.

When the environment variable ``STOCKBOT_TRACE=1`` is set, the factory also
attaches trace hooks (after the cache layer) that capture the raw LLM prompt
and response into a ``TraceWriter`` (if one is present in session state under
the ``"_trace"`` key).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.genai import types as genai_types

from agents.analysts._common import (
    _chain_after,
    _chain_before,
    make_evidence_callback,
)
from agents.analysts.heuristics import NewsVocabulary, load_heuristics
from agents.analysts.report_cache import (
    NEWS_PROMPT_VERSION,
    news_hash_inputs,
    read_cache,
    write_cache,
)
from config.analysts import get_analysts_config
from contract.evidence import VerdictBatch
from contract.extractors.news import extract_news_features
from observability.trace import TraceWriter, make_llm_trace_callbacks

from .fetch import news_fetch_callback
from .prompts import build_news_instruction

# Module-level logger — used in the _after cache callback so that disk errors
# after a paid LLM call produce a warning rather than crashing the agent tick.
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache helper
# ---------------------------------------------------------------------------

def _build_news_cache_callbacks():
    """Return ``(before, after)`` hooks that consult/update the news report cache.

    The ``before`` hook checks the cache for every watchlist ticker. If every
    ticker is a hit (same input hash and prompt version), the LLM call is
    skipped: verdicts are loaded from disk, written into ``state["news_verdicts"]``,
    and a synthetic ``Content`` is returned to short-circuit ADK's model call.

    The ``after`` hook fires on a real LLM call (not a cache hit) and persists
    each ticker's verdict to disk so the next identical tick is free.

    Returns
    -------
    tuple[Callable, Callable]
        ``(before_model_callback, after_model_callback)`` suitable for passing
        directly to ``LlmAgent``.
    """
    cfg  = get_analysts_config().cache
    root = Path(cfg.directory)

    def _before(callback_context, llm_request):
        """Short-circuit the LLM if every watchlist ticker hits the cache.

        Parameters
        ----------
        callback_context:
            ADK callback context with mutable session state.
        llm_request:
            The pending LLM request (not inspected; may be ``None`` in tests).

        Returns
        -------
        google.genai.types.Content | None
            A synthetic ``Content`` on a full cache hit (skips the LLM call);
            ``None`` on any miss (LLM call proceeds normally).
        """
        if not cfg.enabled:
            return None

        state = callback_context.state
        tickers: list[str] = state.get("tickers", []) or []
        news_data: dict = state.get("news_data", {}) or {}

        cached_verdicts = []

        for ticker in tickers:
            articles = (news_data.get(ticker) or {}).get("news") or []
            input_hash = news_hash_inputs(articles)

            hit = read_cache(
                root, "news", ticker,
                input_hash=input_hash,
                prompt_version=NEWS_PROMPT_VERSION,
            )

            if hit is None:
                # Any single miss forces a full LLM call — do not partial-load.
                return None

            # Merge the report back into the verdict dict if one was stored.
            v = hit["verdict"]
            if hit["report"] is not None:
                v = {**v, "report": hit["report"]}

            cached_verdicts.append({**v, "ticker": ticker})

        # All tickers hit — write cached batch into the output key so that
        # make_evidence_callback (after_agent_callback) sees populated verdicts.
        state["news_verdicts"] = VerdictBatch.model_validate(
            {"verdicts": cached_verdicts}
        ).model_dump()

        # Emit a trace marker if a TraceWriter is active so the trace log
        # reflects that the LLM was bypassed.
        try:
            tw = state.get("_trace")
        except (AttributeError, TypeError):
            tw = None

        if isinstance(tw, TraceWriter):
            tw.llm_pair(
                "03_news_llm",
                prompt=f"(cache hit — all tickers, prompt_version={NEWS_PROMPT_VERSION})",
                response="(loaded from cache/reports/news/<ticker>.json)",
                model="cache",
            )

        # Returning a Content object short-circuits the model call in ADK.
        return genai_types.Content(
            parts=[genai_types.Part.from_text(text="(cached)")]
        )

    def _after(callback_context, llm_response):
        """Persist fresh verdicts from a real LLM call to the cache.

        Invoked unconditionally after the model call. On a cache hit (where
        ``_before`` returned a non-None Content), ADK does not invoke this
        hook, so no double-write can occur.

        Parameters
        ----------
        callback_context:
            ADK callback context with mutable session state.
        llm_response:
            Raw LLM response (not inspected; we read state instead).

        Returns
        -------
        None
            Always ``None`` — this hook never short-circuits the flow.
        """
        if not cfg.enabled:
            return None

        state = callback_context.state
        batch = state.get("news_verdicts") or {}
        news_data: dict = state.get("news_data", {}) or {}

        # Support both dict and Pydantic model forms of the batch.
        if isinstance(batch, dict):
            verdicts = batch.get("verdicts", [])
        else:
            verdicts = getattr(batch, "verdicts", [])

        for v in verdicts:
            v_dict = v if isinstance(v, dict) else v.model_dump()
            ticker = v_dict.get("ticker")
            if not ticker:
                continue

            articles = (news_data.get(ticker) or {}).get("news") or []
            input_hash = news_hash_inputs(articles)

            # Store verdict and report separately so each is independently
            # addressable by the cache reader.
            verdict_payload = {k: val for k, val in v_dict.items() if k != "report"}
            report_payload  = v_dict.get("report")

            try:
                write_cache(
                    root, "news", ticker,
                    input_hash=input_hash,
                    prompt_version=NEWS_PROMPT_VERSION,
                    verdict=verdict_payload,
                    report=report_payload,
                )
            except OSError:
                # Disk errors after a paid LLM call must not crash the agent
                # tick.  Log a warning and continue — the verdict is still
                # usable; the cache will simply miss on the next run.
                _log.warning(
                    "news cache write failed for ticker %s — disk error, "
                    "verdict will not be cached for this tick.",
                    ticker,
                    exc_info=True,
                )

        return None

    return _before, _after


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _build_news_analyst(vocab: NewsVocabulary) -> LlmAgent:
    """Construct a fresh ``NewsAnalyst`` LlmAgent with closed-vocab prompt + cache.

    Renders the instruction by substituting the three closed-vocabulary lists
    (catalysts, novelty, direction) into the prompt template.  The resulting
    instruction still contains ADK runtime placeholders ``{news_context}`` and
    ``{tickers}`` which ADK's ``inject_session_state`` fills each tick from
    session state written by ``news_fetch_callback``.

    Cache layer:
        ``_build_news_cache_callbacks()`` returns before/after hooks that
        consult the disk cache.  A full cache hit short-circuits the LLM call;
        a miss falls through to the real model.

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
    LlmAgent
        A fully-wired ``NewsAnalyst`` ready to be added to the
        ``AnalystPool`` ``ParallelAgent``.
    """
    instruction = build_news_instruction(vocab)
    model = "gemini-2.5-flash-lite"

    # Attach LLM trace callbacks only in trace mode — zero-cost gate.
    trace_before = None
    trace_after  = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks("03_news_llm", model=model)

    # Build cache hooks — these run before the trace hooks so a cache hit is
    # still visible in the trace log (the _before hook emits its own entry).
    cache_before, cache_after = _build_news_cache_callbacks()

    # Chain: cache first (may short-circuit), then trace.
    before_cb = _chain_before(cache_before, trace_before)
    after_cb  = _chain_after(cache_after, trace_after)

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

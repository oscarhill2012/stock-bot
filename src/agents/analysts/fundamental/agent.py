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
from agents.analysts.heuristics import FundamentalVocabulary, load_heuristics
from agents.analysts.report_cache import (
    FUNDAMENTAL_PROMPT_VERSION,
    fundamental_hash_inputs,
    read_cache,
    write_cache,
)
from config.analysts import get_analysts_config
from contract.evidence import VerdictBatch
from contract.extractors.fundamental import extract_fundamental_features
from data.models import CompanyRatios, Filing, Form4Bundle
from observability.trace import TraceWriter, make_llm_trace_callbacks

from .fetch import fundamental_fetch_callback
from .prompts import build_fundamental_instruction

# Module-level logger — used in the _after cache callback so that disk errors
# after a paid LLM call produce a warning rather than crashing the agent tick.
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache helper
# ---------------------------------------------------------------------------

def _build_fundamental_cache_callbacks():
    """Return ``(before, after)`` hooks for the Fundamental report cache.

    Mirrors ``_build_news_cache_callbacks`` in the News agent.  The key
    differences are:

    - Cache subdirectory is ``"fundamental"``.
    - Hash function is ``fundamental_hash_inputs(ratios, filings, insider)``
      which re-constructs typed objects from the dicts stored in
      ``state["fundamental_data"]``.
    - Prompt-version constant is ``FUNDAMENTAL_PROMPT_VERSION``.
    - Output state key is ``"fundamental_verdicts"``.

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

        Reconstructs typed ``CompanyRatios``, ``list[Filing]``, and
        ``Form4Bundle`` objects from the dicts stored by the fetch callback
        before computing the hash.  The insider bundle is already a typed
        object in state; ratios and filings are re-validated from dicts.

        Parameters
        ----------
        callback_context:
            ADK callback context with mutable session state.
        llm_request:
            The pending LLM request (not inspected; may be ``None`` in tests).

        Returns
        -------
        google.genai.types.Content | None
            Synthetic ``Content`` on a full cache hit; ``None`` on any miss.
        """
        if not cfg.enabled:
            return None

        state = callback_context.state
        tickers: list[str] = state.get("tickers", []) or []
        fundamental_data: dict = state.get("fundamental_data", {}) or {}

        cached_verdicts = []

        for ticker in tickers:
            triad = fundamental_data.get(ticker) or {}

            # Re-construct typed objects from the state dicts.  The fetch
            # callback stores ratios as a model_dump() dict (or None on
            # failure) and filings as a list of model_dump() dicts; the
            # insider bundle is stored as a typed Form4Bundle instance.
            ratios_dict = triad.get("ratios") or {"ticker": ticker}
            filings_raw = triad.get("filings") or []
            insider_obj = triad.get("insider") or Form4Bundle(trades=[], derivatives=[])

            ratios  = CompanyRatios.model_validate(ratios_dict)
            filings = [
                Filing.model_validate(f) if isinstance(f, dict) else f
                for f in filings_raw
            ]

            input_hash = fundamental_hash_inputs(ratios, filings, insider_obj)

            hit = read_cache(
                root, "fundamental", ticker,
                input_hash=input_hash,
                prompt_version=FUNDAMENTAL_PROMPT_VERSION,
            )

            if hit is None:
                return None  # Any miss -> run the full LLM call.

            # Merge the report back into the verdict dict if one was stored.
            v = hit["verdict"]
            if hit["report"] is not None:
                v = {**v, "report": hit["report"]}

            cached_verdicts.append({**v, "ticker": ticker})

        # All tickers hit — write cached batch into the output key.
        state["fundamental_verdicts"] = VerdictBatch.model_validate(
            {"verdicts": cached_verdicts}
        ).model_dump()

        # Emit a trace marker if a TraceWriter is active.
        try:
            tw = state.get("_trace")
        except (AttributeError, TypeError):
            tw = None

        if isinstance(tw, TraceWriter):
            tw.llm_pair(
                "03_fundamental_llm",
                prompt=f"(cache hit — all tickers, prompt_version={FUNDAMENTAL_PROMPT_VERSION})",
                response="(loaded from cache/reports/fundamental/<ticker>.json)",
                model="cache",
            )

        return genai_types.Content(
            parts=[genai_types.Part.from_text(text="(cached)")]
        )

    def _after(callback_context, llm_response):
        """Persist fresh verdicts from a real LLM call to the cache.

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
        batch = state.get("fundamental_verdicts") or {}
        fundamental_data: dict = state.get("fundamental_data", {}) or {}

        if isinstance(batch, dict):
            verdicts = batch.get("verdicts", [])
        else:
            verdicts = getattr(batch, "verdicts", [])

        for v in verdicts:
            v_dict = v if isinstance(v, dict) else v.model_dump()
            ticker = v_dict.get("ticker")
            if not ticker:
                continue

            triad = fundamental_data.get(ticker) or {}
            ratios_dict = triad.get("ratios") or {"ticker": ticker}
            filings_raw = triad.get("filings") or []
            insider_obj = triad.get("insider") or Form4Bundle(trades=[], derivatives=[])

            ratios  = CompanyRatios.model_validate(ratios_dict)
            filings = [
                Filing.model_validate(f) if isinstance(f, dict) else f
                for f in filings_raw
            ]

            input_hash = fundamental_hash_inputs(ratios, filings, insider_obj)

            verdict_payload = {k: val for k, val in v_dict.items() if k != "report"}
            report_payload  = v_dict.get("report")

            try:
                write_cache(
                    root, "fundamental", ticker,
                    input_hash=input_hash,
                    prompt_version=FUNDAMENTAL_PROMPT_VERSION,
                    verdict=verdict_payload,
                    report=report_payload,
                )
            except OSError:
                # Disk errors after a paid LLM call must not crash the agent
                # tick.  Log a warning and continue — the verdict is still
                # usable; the cache will simply miss on the next run.
                _log.warning(
                    "fundamental cache write failed for ticker %s — disk error, "
                    "verdict will not be cached for this tick.",
                    ticker,
                    exc_info=True,
                )

        return None

    return _before, _after


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _build_fundamental_analyst(vocab: FundamentalVocabulary) -> LlmAgent:
    """Construct a fresh ``FundamentalAnalyst`` LlmAgent with closed-vocab prompt + cache.

    Renders the instruction by substituting the four closed-vocabulary lists
    (guidance, tone, risks, insider_signals) into the prompt template.  The
    resulting instruction still contains ADK runtime placeholders
    ``{fundamental_context}`` and ``{tickers}`` which ADK's
    ``inject_session_state`` fills each tick from session state written by
    ``fundamental_fetch_callback``.

    Cache layer:
        ``_build_fundamental_cache_callbacks()`` returns before/after hooks
        that consult the disk cache.  A full cache hit short-circuits the LLM
        call; a miss falls through to the real model.

    Trace layer:
        When ``STOCKBOT_TRACE=1`` is set, trace hooks are chained *after* the
        cache hook so that cache hits are recorded as ``model="cache"`` in the
        trace log.

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
    trace_before = None
    trace_after  = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks("03_fundamental_llm", model=model)

    # Build cache hooks — run before trace so that cache hits appear in the
    # trace log as model="cache".
    cache_before, cache_after = _build_fundamental_cache_callbacks()

    # Chain: cache first (may short-circuit), then trace.
    before_cb = _chain_before(cache_before, trace_before)
    after_cb  = _chain_after(cache_after, trace_after)

    return LlmAgent(
        name="FundamentalAnalyst",
        model=model,
        instruction=instruction,
        output_schema=VerdictBatch,
        output_key="fundamental_verdicts",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=make_evidence_callback(
            analyst="fundamental",
            extractor=extract_fundamental_features,
            verdicts_state_key="fundamental_verdicts",
        ),
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

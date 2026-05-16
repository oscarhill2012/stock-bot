"""Shared report-cache callback factory for LLM analyst agents.

Centralises the before/after model callback pair that consults and updates the
hash-based LLM report cache.  Previously each LLM analyst (News, Fundamental)
held a ~150-LOC near-identical private helper.  This module collapses them into
a single ``make_report_cache_callbacks`` factory; analysts become 10-line call
sites that pass their per-domain differences as arguments.

Lifecycle bug fixed here (B22)
-------------------------------
The original per-analyst ``_after`` hooks read ``state[verdicts_state_key]``
to discover what the LLM returned.  However, ADK's ``__maybe_save_output_to_state``
(in ``google.adk.agents.llm_agent``) — which parses the LLM JSON against
``output_schema=VerdictBatch`` and writes to state — fires **after** the
after-model-callback chain.  So ``_after`` was seeing an empty state key,
iterating over zero verdicts, and writing nothing to the cache.

The factory's ``_after`` hook fixes this by parsing ``llm_response.content``
directly — the raw model output is available immediately, independent of ADK's
state-save timing.

How to wire a new analyst's cache
----------------------------------
Three steps:

1. **Hash function** — add a ``<name>_hash_inputs`` function in
   ``src/agents/analysts/report_cache.py`` that accepts the per-ticker raw data
   slice (whatever the fetch callback stores in state) and returns a
   blake2b hex digest.  See ``news_hash_inputs`` / ``fundamental_hash_inputs``
   for examples.

2. **Prompt-version constant** — add ``<NAME>_PROMPT_VERSION = "YYYY-MM-DD-a"``
   in ``report_cache.py``.  Bump this string whenever the prompt template or
   closed vocabulary changes to invalidate every cached entry automatically.

3. **Wire the factory** — in ``<name>/agent.py``, replace the inline
   ``_build_<name>_cache_callbacks`` helper with::

       from agents.analysts.cache_callbacks import make_report_cache_callbacks
       from agents.analysts.report_cache import (
           <NAME>_PROMPT_VERSION,
           <name>_hash_inputs,
       )

       cache_before, cache_after = make_report_cache_callbacks(
           analyst_name       = "<name>",
           prompt_version     = <NAME>_PROMPT_VERSION,
           data_state_key     = "<name>_data",
           verdicts_state_key = "<name>_verdicts",
           hash_inputs        = lambda d: <name>_hash_inputs(...),
           trace_label        = "NN_<name>_llm",
       )

   The ``hash_inputs`` lambda receives the per-ticker raw-data dict (i.e.
   ``state["<name>_data"].get(ticker, {}) or {}``) and must return a hash
   string.  Any typed-object reconstruction (e.g. ``CompanyRatios.model_validate``)
   belongs inside the lambda, keeping the factory free of analyst-specific types.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from google.adk.models import LlmResponse
from google.genai import types as genai_types

from agents.analysts.report_cache import log_cache_hit_to_state, read_cache, write_cache
from config.analysts import get_analysts_config
from contract.evidence import VerdictBatch
from observability.trace import TraceWriter

# Module-level logger — disk errors after a paid LLM call must warn, not crash.
_log = logging.getLogger(__name__)


def make_report_cache_callbacks(
    *,
    analyst_name: str,
    prompt_version: str,
    data_state_key: str,
    verdicts_state_key: str,
    hash_inputs: Callable[[dict], str],
    trace_label: str | None = None,
) -> tuple[Callable, Callable]:
    """Build ``(before_model_callback, after_model_callback)`` for a cache-aware analyst.

    Both hooks share the same config snapshot and cache root so they behave
    consistently within a single agent construction.

    Parameters
    ----------
    analyst_name:
        Short identifier for the analyst — determines the cache subdirectory,
        e.g. ``"news"`` maps to ``<root>/news/<TICKER>.json``.
    prompt_version:
        Prompt-version fingerprint baked into every cache entry.  Bump this
        constant (alongside its definition in ``report_cache.py``) whenever the
        prompt template or closed vocabulary changes to invalidate stale entries.
    data_state_key:
        Key in session state that holds the per-ticker raw data dict, e.g.
        ``"news_data"`` or ``"fundamental_data"``.
    verdicts_state_key:
        Key in session state where the LLM-emitted ``VerdictBatch`` eventually
        lands, e.g. ``"news_verdicts"``.  Used by ``_before`` to write cache
        hits into state; **never read by** ``_after`` (see lifecycle bug note
        in the module docstring).
    hash_inputs:
        Callable that accepts the per-ticker raw-data dict (the value at
        ``state[data_state_key].get(ticker, {}) or {}``) and returns a blake2b
        hex digest string.  Any typed-object reconstruction belongs here.
    trace_label:
        Optional section label for ``TraceWriter.llm_pair`` markers on cache
        hits, e.g. ``"03_news_llm"``.  When ``None``, no trace marker is emitted.

    Returns
    -------
    tuple[Callable, Callable]
        ``(before_model_callback, after_model_callback)`` suitable for passing
        directly to ``LlmAgent``.
    """
    # Read config once at factory invocation — matches the existing per-analyst
    # pattern and avoids repeated lru_cache lookups during the hot callback path.
    cfg  = get_analysts_config().cache
    root = Path(cfg.directory)

    # -----------------------------------------------------------------------
    # before_model_callback
    # -----------------------------------------------------------------------

    def _before(callback_context, llm_request):
        """Short-circuit the LLM if every watchlist ticker hits the cache.

        Iterates all tickers in state.  For each, computes the input hash from
        the per-ticker raw data slice and checks the cache.  A single miss
        returns ``None`` immediately (forces a full LLM call — no partial
        loads).  All-hit returns a synthetic ``Content`` that ADK treats as
        the model's response, bypassing the actual model call.

        Parameters
        ----------
        callback_context:
            ADK callback context exposing mutable session state.
        llm_request:
            The pending LLM request (not inspected by this hook).

        Returns
        -------
        google.adk.models.LlmResponse | None
            A synthetic ``LlmResponse`` on a full cache hit; ``None`` on any
            miss.  ADK's downstream post-processors (notably ``_nl_planning``)
            read ``llm_response.content`` on the return value, so the synthetic
            ``Content`` MUST be wrapped in an ``LlmResponse`` — returning a
            bare ``Content`` here crashes the agent flow with ``AttributeError:
            'Content' object has no attribute 'content'`` the moment a cache
            hit actually occurs.  See the regression test
            ``test_before_full_hit_returns_llm_response`` for the pinned
            contract.
        """
        if not cfg.enabled:
            return None

        state   = callback_context.state
        tickers: list[str] = state.get("tickers", []) or []
        data:    dict      = state.get(data_state_key, {}) or {}

        cached_verdicts: list[dict] = []

        for ticker in tickers:
            per_ticker = data.get(ticker, {}) or {}
            input_hash = hash_inputs(per_ticker)

            hit = read_cache(
                root, analyst_name, ticker,
                input_hash=input_hash,
                prompt_version=prompt_version,
            )

            if hit is None:
                # Any single miss forces a full LLM call — do not partial-load
                # a mixed cache/LLM batch (the LLM would then re-score everyone).
                return None

            # Log the cache hit for audit telemetry — records which tick the
            # verdict was originally computed under so reviewers can see when
            # a cached result spans multiple ticks.
            log_cache_hit_to_state(
                state,
                analyst=analyst_name,
                ticker=ticker,
                input_hash=input_hash,
                originating_as_of=hit.get("originating_as_of"),
            )

            # Merge the report blob back into the verdict dict if one was stored
            # — caches can omit the report on analysts that don't emit reports.
            v = hit["verdict"]
            if hit["report"] is not None:
                v = {**v, "report": hit["report"]}

            cached_verdicts.append({**v, "ticker": ticker})

        # All tickers hit — validate the assembled batch.  We need the parsed
        # object (to write to state) AND the JSON string (to feed back as the
        # synthetic LLM response text — see the comment block above the
        # ``return LlmResponse(...)`` at the bottom of this function).
        batch      = VerdictBatch.model_validate({"verdicts": cached_verdicts})
        batch_json = batch.model_dump_json()

        # Write to state so that make_evidence_callback (after_agent_callback)
        # sees populated verdicts.  Note: ADK's ``__maybe_save_output_to_state``
        # will ALSO parse ``batch_json`` below and write it to
        # ``state[output_key]`` — which for our analysts is the same key as
        # ``verdicts_state_key`` — so this manual write is technically
        # redundant.  It is kept as defence-in-depth in case an analyst is ever
        # configured without ``output_key`` set.
        state[verdicts_state_key] = batch.model_dump()

        # Emit a trace marker so the trace log reflects that the LLM was bypassed.
        if trace_label is not None:
            try:
                tw = state.get("_trace")
            except (AttributeError, TypeError):
                tw = None

            if isinstance(tw, TraceWriter):
                tw.llm_pair(
                    trace_label,
                    prompt=f"(cache hit — all tickers, prompt_version={prompt_version})",
                    response=f"(loaded from cache/reports/{analyst_name}/<ticker>.json)",
                    model="cache",
                )

        # Short-circuit the model call.  Two constraints on the return value:
        #
        # 1. ADK's downstream post-processors (``_nl_planning`` et al.) access
        #    ``llm_response.content``, which only exists on ``LlmResponse``.
        #    Returning a raw ``Content`` crashes the flow with
        #    ``AttributeError: 'Content' object has no attribute 'content'``.
        #
        # 2. ADK's ``__maybe_save_output_to_state`` (in
        #    ``google.adk.agents.llm_agent``) then validates the response's
        #    text against the agent's ``output_schema``.  Our analysts declare
        #    ``output_schema=VerdictBatch``, so the text MUST be valid JSON
        #    that parses cleanly as a ``VerdictBatch`` — a placeholder string
        #    like ``"(cached)"`` raises ``pydantic.ValidationError: Invalid
        #    JSON`` and tanks the tick.
        #
        # Both bugs were latent while B22 was unfixed (cache writes never
        # landed, so this code path never fired); they surfaced the first time
        # a real cache hit was attempted after B22 + B23 landed.  The
        # regression tests ``test_before_full_hit_returns_llm_response`` and
        # ``test_before_full_hit_content_is_valid_verdict_batch_json`` pin
        # both invariants.
        return LlmResponse(
            content=genai_types.Content(
                parts=[genai_types.Part.from_text(text=batch_json)]
            )
        )

    # -----------------------------------------------------------------------
    # after_model_callback
    # -----------------------------------------------------------------------

    def _after(callback_context, llm_response):
        """Persist fresh verdicts from a real LLM call to the cache.

        Invoked only after an actual model call — when ``_before`` returns a
        non-None ``Content``, ADK short-circuits and does **not** invoke this hook.
        So there is no double-write risk for cache hits.

        NEVER read state[verdicts_state_key] here.  ADK's __maybe_save_output_to_state
        (see google.adk.agents.llm_agent) runs AFTER after_model_callback, so the
        output_schema-parsed verdicts are not yet in state at this point.  Parse
        llm_response directly instead — its shape comes straight from the model and
        is independent of ADK's state-save timing.

        Parameters
        ----------
        callback_context:
            ADK callback context exposing mutable session state.
        llm_response:
            The raw LLM response object.  This hook reads
            ``llm_response.content.parts[0].text`` to find the JSON payload.

        Returns
        -------
        None
            Always ``None`` — this hook never short-circuits the response flow.
        """
        if not cfg.enabled:
            return None

        state = callback_context.state
        data: dict = state.get(data_state_key, {}) or {}

        # --- Parse verdicts from the LLM response, NOT from state -----------
        #
        # NEVER read state[verdicts_state_key] here.  ADK's __maybe_save_output_to_state
        # (see google.adk.agents.llm_agent) runs AFTER after_model_callback, so the
        # output_schema-parsed verdicts are not yet in state at this point.  Parse
        # llm_response directly instead — its shape comes straight from the model and
        # is independent of ADK's state-save timing.
        try:
            text     = llm_response.content.parts[0].text
            payload  = json.loads(text)
            verdicts = payload.get("verdicts", []) or []
        except (AttributeError, IndexError, TypeError, json.JSONDecodeError):
            # LLM response shape is unexpected — skip cache write.  Verdicts will
            # still land in state via __maybe_save_output_to_state and this tick
            # is just uncached, which is acceptable degradation.
            _log.warning(
                "%s cache: could not parse LLM response — cache write skipped for "
                "this tick.  The verdict will be populated by ADK's state-save "
                "machinery but will not be cached.",
                analyst_name,
            )
            return None

        for v in verdicts:
            # Tolerate both dict and non-dict entries defensively.
            v_dict = v if isinstance(v, dict) else {}
            ticker = v_dict.get("ticker")
            if not ticker:
                continue

            # Recompute the input hash from the per-ticker raw slice — this
            # must match what _before computed so the next tick's cache check
            # produces a hit for the same input data.
            per_ticker = data.get(ticker, {}) or {}
            input_hash = hash_inputs(per_ticker)

            # Store verdict and report separately so each is independently
            # addressable by the cache reader (report can be large; some callers
            # only want the verdict summary).
            verdict_payload = {k: val for k, val in v_dict.items() if k != "report"}
            report_payload  = v_dict.get("report")

            try:
                write_cache(
                    root, analyst_name, ticker,
                    input_hash=input_hash,
                    prompt_version=prompt_version,
                    verdict=verdict_payload,
                    report=report_payload,
                    # Pass the tick's as_of so future cache hits can surface the
                    # originating tick in the audit telemetry.
                    originating_as_of=state.get("as_of"),
                )
            except OSError:
                # Disk errors after a paid LLM call must not crash the agent
                # tick.  The verdict is still usable in-session; only the cache
                # misses on the next run.
                _log.warning(
                    "%s cache write failed for ticker %s — disk error, "
                    "verdict will not be cached for this tick.",
                    analyst_name,
                    ticker,
                    exc_info=True,
                )

        return None

    return _before, _after

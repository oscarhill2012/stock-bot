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
``output_schema=TickerVerdict`` and writes to state — fires **after** the
after-model-callback chain.  So ``_after`` was seeing an empty state key
and writing nothing to the cache.

The factory's ``_after`` hook fixes this by parsing ``llm_response.content``
directly — the raw model output is available immediately, independent of ADK's
state-save timing.

Phase 9 change — per-ticker shape
----------------------------------
Each LlmAgent is now bound to a SINGLE ticker.  Both hooks therefore look up a
single per-ticker cache entry rather than iterating every watchlist ticker.
``output_schema`` (a Pydantic model class) is passed by the caller so the
factory stays free of analyst-specific imports — typically ``TickerVerdict``.

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

3. **Wire the factory** — in the per-ticker factory (Tasks 7/8), build the
   callback pair as::

       from agents.analysts.cache_callbacks import make_report_cache_callbacks
       from agents.analysts.report_cache import (
           <NAME>_PROMPT_VERSION,
           <name>_hash_inputs,
       )
       from contract.evidence import TickerVerdict

       cache_before, cache_after = make_report_cache_callbacks(
           analyst_name       = "<name>",
           prompt_version     = <NAME>_PROMPT_VERSION,
           data_state_key     = "temp:<name>_data",  # A2.6: temp: prefix required
           verdicts_state_key = "temp:<name>_verdict_<TICKER>",
           ticker             = ticker,
           output_schema      = TickerVerdict,
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
from pydantic import ValidationError

from agents.analysts.report_cache import read_cache, write_cache
from config.analysts import get_analysts_config
from data.timeguard import resolve_as_of
from observability.trace import TraceWriter

# Module-level logger — disk errors after a paid LLM call must warn, not crash.
_log = logging.getLogger(__name__)


def make_report_cache_callbacks(
    *,
    analyst_name: str,
    prompt_version: str,
    data_state_key: str,
    verdicts_state_key: str,
    ticker: str,
    output_schema: type,
    hash_inputs: Callable[[dict], str],
    trace_label: str | None = None,
) -> tuple[Callable, Callable]:
    """Build ``(before_model_callback, after_model_callback)`` for a single-ticker cache-aware LlmAgent.

    Changed in Phase 9: each LlmAgent is bound to ONE ticker.  Both hooks
    therefore look up a single per-ticker cache entry rather than iterating
    every watchlist ticker.  ``output_schema`` is the Pydantic model used
    to validate the cached payload and to shape the synthetic LlmResponse
    text — typically ``TickerVerdict`` for the per-ticker News and
    Fundamental analysts.

    See module docstring for the broader cache lifecycle.

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
        ``"temp:news_data"`` or ``"temp:fundamental_data"`` (A2.6: ``temp:``
        prefix required so ADK strips the key at the invocation boundary).
    verdicts_state_key:
        Key in session state where this agent's single ``TickerVerdict``
        eventually lands, e.g. ``"temp:news_verdict_AAPL"``.  Used by
        ``_before`` to write cache hits into state; **never read by** ``_after``
        (see lifecycle bug note in the module docstring).
    ticker:
        The single ticker this callback pair is bound to.  Set at LlmAgent
        construction time by the per-ticker factory.
    output_schema:
        Pydantic model class that the synthetic ``LlmResponse`` text must
        validate against — must match the agent's ``output_schema`` so
        ADK's ``__maybe_save_output_to_state`` parses cleanly.  Typically
        ``TickerVerdict``.
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
        """Short-circuit if this single ticker's cache hits.

        Computes the input hash from the per-ticker raw data slice and checks
        the cache.  A miss returns ``None`` immediately (forces an LLM call).
        A hit returns a synthetic ``LlmResponse`` wrapping the cached verdict
        JSON — ADK treats this as the model's response, bypassing the actual
        model call.

        Parameters
        ----------
        callback_context:
            ADK callback context exposing mutable session state.
        llm_request:
            The pending LLM request (not inspected by this hook).

        Returns
        -------
        google.adk.models.LlmResponse | None
            A synthetic ``LlmResponse`` on a cache hit; ``None`` on a miss.
            ADK's downstream post-processors (notably ``_nl_planning``)
            read ``llm_response.content`` on the return value, so the synthetic
            ``Content`` MUST be wrapped in an ``LlmResponse`` — returning a
            bare ``Content`` here crashes the agent flow with ``AttributeError:
            'Content' object has no attribute 'content'`` the moment a cache
            hit actually occurs.
        """
        if not cfg.enabled:
            return None

        state      = callback_context.state
        data: dict = state.get(data_state_key, {}) or {}
        per_ticker = data.get(ticker, {}) or {}
        input_hash = hash_inputs(per_ticker)

        hit = read_cache(
            root, analyst_name, ticker,
            input_hash=input_hash,
            prompt_version=prompt_version,
        )

        if hit is None:
            # Cache miss — let the LLM run.
            _log.info(
                "report_cache_miss",
                extra={
                    "analyst":        analyst_name,
                    "ticker":         ticker,
                    "input_hash":     input_hash,
                    "prompt_version": prompt_version,
                    "kind":           "report_cache_miss",
                },
            )
            return None

        _log.info(
            "report_cache_hit",
            extra={
                "analyst":           analyst_name,
                "ticker":            ticker,
                "input_hash":        input_hash,
                "originating_as_of": hit.get("originating_as_of"),
                "prompt_version":    prompt_version,
                "kind":              "report_cache_hit",
            },
        )

        # Merge report blob into verdict if one was stored.
        v = hit["verdict"]
        if hit["report"] is not None:
            v = {**v, "report": hit["report"]}
        v = {**v, "ticker": ticker}

        # Validate against the per-ticker schema — same shape ADK's
        # __maybe_save_output_to_state will expect from the response text.
        validated    = output_schema.model_validate(v)
        verdict_json = validated.model_dump_json()

        # Write to state so any downstream consumer that reads
        # state[verdicts_state_key] sees the populated value.  Note: ADK's
        # __maybe_save_output_to_state will also write the same JSON to the
        # agent's output_key — kept as defence-in-depth.
        state[verdicts_state_key] = validated.model_dump()

        if trace_label is not None:
            try:
                tw = state.get("temp:_trace")
            except (AttributeError, TypeError):
                tw = None

            if isinstance(tw, TraceWriter):
                tw.llm_pair(
                    trace_label,
                    prompt=f"(cache hit — {ticker}, prompt_version={prompt_version})",
                    response=f"(loaded from cache/reports/{analyst_name}/{ticker}.json)",
                    model="cache",
                )

        # Record this branch in the terminal-summary accumulator.  ADK skips
        # ``after_model_callback`` whenever ``before_model_callback`` returns an
        # LlmResponse (documented in ``_after`` below), so the observability
        # after-callback never runs on cache hits — if we don't write here,
        # the joiner sees a missing per-ticker record and emits the
        # misleading ``"N failed"`` row.  Tag the record with
        # ``cache_hit=True`` so ``emit_analyst_summary`` can render it as a
        # cached row rather than a (zero-token, zero-latency) success.
        #
        # Key shape mirrors
        # ``observability.terminal_log.make_observability_callbacks``
        # (``temp:_obs_<analyst>_call_<TICKER>``) so the same joiner read
        # path picks it up.  Per-ticker scalar (not a shared list) so
        # parallel fan-out has no race to lose records to — see the
        # ``make_observability_callbacks`` docstring for the full
        # rationale.
        state[f"temp:_obs_{analyst_name}_call_{ticker}"] = {
            "ticker":           ticker,
            "elapsed":          None,   # no LLM latency — served from disk
            "prompt_tokens":    0,
            "candidate_tokens": 0,
            "ok":               True,
            "cache_hit":        True,
        }

        # Short-circuit the model call.  Two constraints on the return value:
        #
        # 1. ADK's downstream post-processors (``_nl_planning`` et al.) access
        #    ``llm_response.content``, which only exists on ``LlmResponse``.
        #    Returning a raw ``Content`` crashes the flow with
        #    ``AttributeError: 'Content' object has no attribute 'content'``.
        #
        # 2. ADK's ``__maybe_save_output_to_state`` then validates the
        #    response's text against the agent's declared ``output_schema``.
        #    The text MUST be valid JSON that parses cleanly as the passed
        #    ``output_schema`` — a placeholder like ``"(cached)"`` raises
        #    ``pydantic.ValidationError`` and tanks the tick.
        return LlmResponse(
            content=genai_types.Content(
                parts=[genai_types.Part.from_text(text=verdict_json)]
            )
        )

    # -----------------------------------------------------------------------
    # after_model_callback
    # -----------------------------------------------------------------------

    def _after(callback_context, llm_response):
        """Persist the fresh single-ticker verdict to the cache.

        Invoked only after an actual model call — when ``_before`` returns a
        non-None ``LlmResponse``, ADK short-circuits and does **not** invoke
        this hook.  So there is no double-write risk for cache hits.

        NEVER read state[verdicts_state_key] here.  ADK's __maybe_save_output_to_state
        (see google.adk.agents.llm_agent) runs AFTER after_model_callback, so the
        output_schema-parsed verdict is not yet in state at this point.  Parse
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

        state      = callback_context.state
        data: dict = state.get(data_state_key, {}) or {}

        # Parse the single-verdict JSON directly from the response — NOT from
        # state.  See the lifecycle bug note in the module docstring (B22).
        try:
            text    = llm_response.content.parts[0].text
            payload = json.loads(text)
        except (AttributeError, IndexError, TypeError, json.JSONDecodeError):
            # LLM response shape is unexpected — skip cache write.  The verdict
            # will still land in state via ADK's __maybe_save_output_to_state;
            # this tick is just uncached, which is acceptable degradation.
            _log.warning(
                "%s cache: could not parse LLM response — cache write skipped for %s.",
                analyst_name, ticker,
            )
            return None

        # Payload is a single TickerVerdict dict (NOT {verdicts: [...]}).
        v_dict = payload if isinstance(payload, dict) else {}

        # Schema-gate the cache write.  Partial / malformed LLM responses
        # (missing ``report`` when ``is_no_data=false``, wrong field shapes,
        # truncated payloads, etc.) are valid JSON but invalid against
        # ``output_schema`` — writing them poisons the cache so the next tick
        # with the same input hash replays the broken verdict instead of
        # re-running the LLM.  ``is_no_data=true`` is a legitimate no-signal
        # response (still schema-valid) and is intentionally still cached.
        try:
            output_schema.model_validate({**v_dict, "ticker": ticker})
        except ValidationError as exc:
            _log.warning(
                "%s cache: LLM response failed %s validation for %s — cache "
                "write skipped so next tick re-runs the LLM. Error: %s",
                analyst_name, output_schema.__name__, ticker, exc,
            )
            return None

        per_ticker = data.get(ticker, {}) or {}
        input_hash = hash_inputs(per_ticker)

        # Separate the report blob from the core verdict fields so each is
        # independently addressable by the cache reader.
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
                # originating tick in the audit telemetry.  ``write_cache``
                # calls ``.isoformat()`` on this value, so we must coerce
                # the backtest's ISO-string ``state["as_of"]`` back to a
                # datetime here at the boundary — without this the news
                # branch raises ``AttributeError: 'str' object has no
                # attribute 'isoformat'`` on every cache write.
                originating_as_of=resolve_as_of(
                    state.get("as_of"),
                    allow_wallclock=True,
                    site="cache_callbacks.write_cache",
                ),
            )
        except OSError:
            # Disk errors after a paid LLM call must not crash the agent tick.
            # The verdict is still usable in-session; only the cache misses on
            # the next run.
            _log.warning(
                "%s cache write failed for ticker %s — disk error.",
                analyst_name, ticker, exc_info=True,
            )

        return None

    return _before, _after

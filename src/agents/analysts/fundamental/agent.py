"""Fundamental analyst LlmAgent — closed-vocab narrowed (Phase 5 Task 10).

The LLM is instructed to emit ``AnalystVerdict``-shaped dicts keyed as
``fundamental_verdicts`` in session state.  The ``make_evidence_callback``
after-callback then converts those verdicts into ``AnalystEvidence`` records
and writes them to ``state["fundamental_evidence"]``.

The agent factory :func:`build_fundamental_analyst` accepts a
``FundamentalVocabulary`` at construction time and renders the closed-vocab
prompt via ``build_fundamental_instruction`` before wiring the ``LlmAgent``.
This factory is the **single construction path** — production wiring goes
through it from ``orchestrator.pipeline``, and the structural tests in
``tests/analysts/test_fundamental.py`` call it directly via the
``fundamental_analyst_fixture`` conftest helper.  Pre-2026-05-21 this
module exposed a module-level ``fundamental_analyst`` singleton built at
import time; both the singleton and the hardcoded ``"gemini-…"`` literal
are gone — the model ID is now read from
``config/models.json::fundamental_analyst`` via
``src.config.models.get_models_config``.

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
from agents.analysts.heuristics import FundamentalVocabulary
from agents.analysts.report_cache import (
    FUNDAMENTAL_PROMPT_VERSION,
    fundamental_hash_inputs,
)
from config.models import get_models_config
from contract.evidence import VerdictBatch
from contract.extractors.fundamental import extract_fundamental_features
from data.models import CompanyRatios, Filing, Form4Bundle
from observability.trace import make_llm_trace_callbacks

from .fetch import fundamental_fetch_callback
from .prompts import build_fundamental_instruction

# ---------------------------------------------------------------------------
# Internal helper — typed-object reconstruction for the hash lambda
# ---------------------------------------------------------------------------

def _fundamental_hash_inputs_from_dict(ticker: str, triad: dict) -> str:
    """Reconstruct typed objects from the per-ticker state dict and hash them.

    The fetch callback stores ``ratios`` as a ``CompanyRatios.model_dump()``
    dict (or ``None`` on failure), ``filings`` as a list of
    ``Filing.model_dump()`` dicts, and ``insider`` as a typed ``Form4Bundle``
    instance.  This function re-validates the stored dicts so
    ``fundamental_hash_inputs`` receives the proper typed objects.

    Parameters
    ----------
    ticker:
        Ticker symbol — used as the ``CompanyRatios`` fallback dict key.
    triad:
        Per-ticker slice from ``state["temp:fundamental_data"]``.

    Returns
    -------
    str
        Blake2b hex digest over the combined fundamental input payload.
    """
    ratios_dict = triad.get("ratios") or {"ticker": ticker}
    filings_raw = triad.get("filings") or []
    insider_obj = triad.get("insider") or Form4Bundle(trades=[], derivatives=[])

    ratios = CompanyRatios.model_validate(ratios_dict)
    filings = [
        Filing.model_validate(f) if isinstance(f, dict) else f
        for f in filings_raw
    ]

    return fundamental_hash_inputs(ratios, filings, insider_obj)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_fundamental_analyst(vocab: FundamentalVocabulary) -> YieldingAnalystWrapper:
    """Construct a fresh ``FundamentalAnalyst`` LlmAgent with closed-vocab prompt + cache.

    Renders the instruction by substituting the four closed-vocabulary lists
    (guidance, tone, risks, insider_signals) into the prompt template.  The
    resulting instruction still contains ADK runtime placeholders
    ``{fundamental_context}`` and ``{tickers}`` which ADK's
    ``inject_session_state`` fills each tick from session state written by
    ``fundamental_fetch_callback``.

    Cache layer:
        ``make_report_cache_callbacks(...)`` (from ``agents.analysts.cache_callbacks``)
        returns before/after hooks that consult the disk cache.  A full cache hit
        short-circuits the LLM call; a miss falls through to the real model.
        The ``hash_inputs`` lambda calls ``_fundamental_hash_inputs_from_dict``
        which reconstructs typed ``CompanyRatios`` / ``Filing`` / ``Form4Bundle``
        objects before invoking ``fundamental_hash_inputs``.

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
    YieldingAnalystWrapper
        A fully-wired ``FundamentalAnalystBranch`` ready to be added to the
        ``AnalystPool`` ``ParallelAgent``.  The inner ``LlmAgent`` is
        accessible via ``.inner`` for tests that need to inspect it directly.
    """
    instruction = build_fundamental_instruction(vocab)

    # Read the Fundamental analyst's model ID from the central config — the
    # only source of truth.  See ``config/models.json`` and the
    # ``src/config/models.py`` loader for the rationale.
    model = get_models_config().fundamental_analyst

    # Attach LLM trace callbacks only in trace mode — zero-cost gate.
    trace_before = None
    trace_after  = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks("03_fundamental_llm", model=model)

    # Build cache hooks via the shared factory — run before trace so that cache
    # hits appear in the trace log as model="cache".  The hash_inputs lambda
    # reconstructs typed objects from the per-ticker state dict (the fetch
    # callback stores them as model_dump() dicts) before computing the hash.
    # The ticker is extracted from the ratios dict's own "ticker" field — the
    # fetch callback always sets it, so the fallback to "" is defensive-only.
    cache_before, cache_after = make_report_cache_callbacks(
        analyst_name       = "fundamental",
        prompt_version     = FUNDAMENTAL_PROMPT_VERSION,
        data_state_key     = "temp:fundamental_data",
        verdicts_state_key = "fundamental_verdicts",
        hash_inputs        = lambda d: _fundamental_hash_inputs_from_dict(
            ticker=((d or {}).get("ratios") or {}).get("ticker", ""),
            triad=(d or {}),
        ),
        trace_label        = "03_fundamental_llm",
    )

    # Chain: cache first (may short-circuit), then trace.
    before_cb = _chain_before(cache_before, trace_before)
    after_cb  = _chain_after(cache_after, trace_after)

    # Build the inner LlmAgent — all callbacks and config are unchanged from
    # the pre-A2.5 version.  The outer YieldingAnalystWrapper republishes the
    # after_agent_callback's evidence write as a ``state_delta`` yield so the
    # write is durable on persistent ADK session backends (Rule 1 compliance).
    llm = LlmAgent(
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
    return YieldingAnalystWrapper(
        name="FundamentalAnalystBranch",
        inner=llm,
        evidence_state_key="fundamental_evidence",
        trace_key="02_fundamental_verdict",
    )


# Module-level singleton removed 2026-05-21.  Previously a
# ``fundamental_analyst = _build_fundamental_analyst(load_heuristics().fundamental_vocabulary)``
# was constructed at import time; it ran ``load_heuristics()`` (a disk read)
# unconditionally on module import.  All callers — production pipeline and
# structural tests alike — now invoke :func:`build_fundamental_analyst`
# explicitly.

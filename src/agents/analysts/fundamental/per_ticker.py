# src/agents/analysts/fundamental/per_ticker.py
"""Per-ticker Fundamental branch factory (Phase 9).

Constructs one IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))
bound to a single ticker.  The LlmAgent's instruction has ``{ticker}``
substituted at build time so each branch's prompt mentions only its
own ticker.  The ``{fundamental_context}`` placeholder remains for ADK's
``inject_session_state`` to fill from ``state["temp:fundamental_context_<TICKER>"]``
at run time — see ``FundamentalFetchAgent`` for the writer side.

This module is the Fundamental mirror of ``agents.analysts.news.per_ticker``;
the two are kept structurally symmetric so they evolve in lock-step.
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from agents.analysts._common import _chain_after, _chain_before
from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.heuristics import FundamentalVocabulary
from agents.analysts.report_cache import fundamental_hash_inputs_from_dict
from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.report_cache import (
    FUNDAMENTAL_PROMPT_VERSION,
)
from agents.isolated_failure import IsolatedFailureWrapper
from agents.llm_retry import RetryingAgentWrapper
from config.models import get_models_config
from contract.evidence import TickerVerdict
from observability.terminal_log import make_observability_callbacks
from observability.trace import make_llm_trace_callbacks


def build_fundamental_branch_for_ticker(
    ticker: str,
    vocab: FundamentalVocabulary,
    *,
    ticker_index: int = 0,
    ticker_count: int = 0,
) -> IsolatedFailureWrapper:
    """Build a single-ticker Fundamental branch.

    Produces one IsolatedFailureWrapper wrapping a RetryingAgentWrapper
    wrapping an LlmAgent.  The wrappers' names embed ``ticker`` so traces
    and logs identify each branch unambiguously.

    The returned agent emits exactly one TickerVerdict, written to
    ``state["temp:fundamental_verdict_<TICKER>"]`` via ADK's output_key
    mechanism.  No after_agent_callback is set — evidence-build is the
    joiner's responsibility (see ``FundamentalJoinerAgent``).  No
    before_agent_callback either — fundamental context is pre-populated by
    ``FundamentalFetchAgent`` which runs once per tick before any per-ticker
    branch.

    Args:
        ticker:        The ticker symbol this branch is bound to (e.g. "AAPL").
        vocab:         Validated FundamentalVocabulary holding the closed-vocab
                       tag lists (guidance, tone, risks, insider_signals).
        ticker_index:  1-based position in the watchlist; forwarded to the
                       terminal-log observability callback so rows show
                       ``N/M`` progress.  Defaults to 0 (no progress display).
        ticker_count:  Total watchlist size; forwarded to the observability
                       callback.  Defaults to 0.

    Returns:
        IsolatedFailureWrapper[RetryingAgentWrapper[LlmAgent]] bound to
        the given ticker.
    """
    # -----------------------------------------------------------------------
    # Build the instruction — substitute {ticker} at factory time so each
    # branch's prompt is already specialised.
    #
    # Also remap the generic {fundamental_context} placeholder to the
    # ticker-specific ADK state key
    # {temp:fundamental_context_<TICKER>} so ADK's inject_session_state
    # resolves the right per-ticker block written by
    # FundamentalFetchAgent.  ADK supports <prefix>:<identifier> state
    # names (validated by _is_valid_state_name in instructions_utils.py),
    # so "temp:fundamental_context_AAPL" is a legal placeholder target.
    # -----------------------------------------------------------------------
    base_instruction = build_fundamental_instruction(vocab)
    instruction      = (
        base_instruction
        .replace("{ticker}", ticker)
        .replace("{fundamental_context}", f"{{temp:fundamental_context_{ticker}}}")
    )

    model = get_models_config().fundamental_analyst

    # -----------------------------------------------------------------------
    # Cache callbacks — per-ticker shape (Phase 9 Task 6 API).
    #
    # The hash_inputs lambda receives the per-ticker slice from
    # state["temp:fundamental_data"] and delegates to
    # fundamental_hash_inputs_from_dict (imported directly from agent.py).
    # That helper re-validates the stored model_dump() dicts back into typed
    # CompanyRatios / Filing / Form4Bundle objects before computing the digest.
    # -----------------------------------------------------------------------
    cache_before, cache_after = make_report_cache_callbacks(
        analyst_name       = "fundamental",
        prompt_version     = FUNDAMENTAL_PROMPT_VERSION,
        data_state_key     = "temp:fundamental_data",
        verdicts_state_key = f"temp:fundamental_verdict_{ticker}",
        ticker             = ticker,
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: fundamental_hash_inputs_from_dict(
            ticker=ticker,
            triad=(d or {}),
        ),
        trace_label        = f"04_fundamental_llm_{ticker}",
    )

    # -----------------------------------------------------------------------
    # Observability callbacks — emit one terminal log row per LLM call.
    # Only wired when STOCKBOT_TERMINAL_LOG=1 so backtest replays and unit
    # tests add zero overhead.
    # -----------------------------------------------------------------------
    obs_before = None
    obs_after  = None

    if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
        obs_before, obs_after = make_observability_callbacks(
            analyst      = "fundamental",
            ticker       = ticker,
            ticker_index = ticker_index,
            ticker_count = ticker_count,
            model_name   = model,
        )

    # -----------------------------------------------------------------------
    # Optional trace callbacks — only wired when STOCKBOT_TRACE=1 so normal
    # test runs and backtest replays add zero overhead.
    # -----------------------------------------------------------------------
    trace_before = None
    trace_after  = None

    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks(
            f"04_fundamental_llm_{ticker}", model=model,
        )

    # Chain cache, observability, and trace callbacks.  _chain_before
    # short-circuits on the first non-None return (cache hit returns synthetic
    # Content to bypass the LLM call); _chain_after runs all callbacks
    # unconditionally.  Observability is placed AFTER cache so that the
    # before-stamp only fires when an actual model call is about to happen
    # (cache hits never reach the model and therefore skip this hook).
    before_cb = _chain_before(cache_before, obs_before, trace_before)
    after_cb  = _chain_after(cache_after, obs_after, trace_after)

    # -----------------------------------------------------------------------
    # Assemble the LlmAgent.
    # - before_agent_callback and after_agent_callback are intentionally
    #   omitted (left as None) — see docstring above.
    # - before_model_callback / after_model_callback carry cache + trace hooks.
    # -----------------------------------------------------------------------
    llm = LlmAgent(
        name             = f"FundamentalAnalyst_{ticker}",
        model            = model,
        instruction      = instruction,
        output_schema    = TickerVerdict,
        output_key       = f"temp:fundamental_verdict_{ticker}",
        before_model_callback = before_cb,
        after_model_callback  = after_cb,
    )

    # Wrap in the retry layer so transient Vertex AI 429s are handled before
    # any failure bubbles up to the isolation boundary.
    retrying = RetryingAgentWrapper(
        name  = f"FundamentalAnalyst_{ticker}_retrying",
        inner = llm,
    )

    # Outermost isolation wrapper — catches and logs any exception (including
    # exhausted retries) so a single broken ticker cannot abort the tick.
    return IsolatedFailureWrapper(
        name    = f"FundamentalAnalyst_{ticker}_isolated",
        inner   = retrying,
        analyst = "fundamental",
        ticker  = ticker,
    )

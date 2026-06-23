# src/agents/analysts/news/per_ticker.py
"""Per-ticker News branch factory (Phase 9).

Constructs one IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))
bound to a single ticker.  The LlmAgent's instruction has {ticker}
substituted at build time so each branch's prompt mentions only its
own ticker.  The {news_context} placeholder remains for ADK's
inject_session_state to fill from temp:news_context_<TICKER> at run
time — see ``NewsFetchAgent`` for the writer side.
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.genai import types as genai_types

from agents.analysts._common import _chain_after, _chain_before
from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction
from agents.analysts.report_cache import (
    NEWS_PROMPT_VERSION,
    news_hash_inputs,
)
from agents.isolated_failure import IsolatedFailureWrapper
from agents.llm_retry import RetryingAgentWrapper, build_retry_policies
from agents.model_resolver import resolve_model
from agents.thinking_config import build_thinking_config
from config.analysts import get_analysts_config
from config.models import get_models_config
from contract.evidence import LlmTickerVerdict
from observability.terminal_log import make_observability_callbacks
from observability.trace import make_llm_trace_callbacks


def build_news_branch_for_ticker(
    ticker: str,
    vocab: NewsVocabulary,
    *,
    ticker_index: int = 0,
    ticker_count: int = 0,
) -> IsolatedFailureWrapper:
    """Build a single-ticker News branch.

    Produces one IsolatedFailureWrapper wrapping a RetryingAgentWrapper
    wrapping an LlmAgent.  The wrappers' names embed ``ticker`` so traces
    and logs identify each branch unambiguously.

    The returned agent emits exactly one ``LlmTickerVerdict`` (the narrow
    LLM emit-schema), written to ``state["temp:news_verdict_<TICKER>"]`` via
    ADK's output_key mechanism.  The joiner inflates each emit into the
    canonical downstream ``TickerVerdict`` (``rationale`` defaults to "" —
    LLM analysts no longer emit it; the field is populated only by the
    deterministic extractors).  No after_agent_callback is set — evidence-build is the
    joiner's responsibility (see ``NewsJoinerAgent``).  No
    before_agent_callback either — news context is pre-populated by
    ``NewsFetchAgent`` which runs once per tick before any per-ticker branch.

    Args:
        ticker:        The ticker symbol this branch is bound to (e.g. "AAPL").
        vocab:         Validated NewsVocabulary holding closed-vocab tag lists.
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
    # Also remap the generic {news_context} placeholder to the ticker-
    # specific ADK state key {temp:news_context_<TICKER>} so ADK's
    # inject_session_state resolves the right per-ticker block written by
    # NewsFetchAgent.  ADK supports <prefix>:<identifier> state names
    # (validated by _is_valid_state_name in instructions_utils.py), so
    # "temp:news_context_AAPL" is a legal placeholder target.
    # -----------------------------------------------------------------------
    base_instruction = build_news_instruction(vocab)
    instruction      = (
        base_instruction
        .replace("{ticker}", ticker)
        .replace("{news_context}", f"{{temp:news_context_{ticker}}}")
    )

    model = get_models_config().news_analyst

    # -----------------------------------------------------------------------
    # Cache callbacks — per-ticker shape (Phase 9 Task 6 API).
    # The hash_inputs lambda extracts the article list from the per-ticker
    # raw-data slice and passes it to the canonical news hash function.
    # -----------------------------------------------------------------------
    cache_before, cache_after = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = NEWS_PROMPT_VERSION,
        data_state_key     = "temp:news_data",
        verdicts_state_key = f"temp:news_verdict_{ticker}",
        ticker             = ticker,
        output_schema      = LlmTickerVerdict,
        hash_inputs        = lambda d: news_hash_inputs((d or {}).get("news") or []),
        trace_label        = f"03_news_llm_{ticker}",
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
            analyst      = "news",
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
            f"03_news_llm_{ticker}", model=model,
        )

    # Chain cache, observability, and trace callbacks.  _chain_before
    # short-circuits on the first non-None return (cache hit returns synthetic
    # Content to bypass the LLM call); _chain_after runs all callbacks
    # unconditionally.  Observability is placed AFTER cache so that the
    # before-stamp only fires when an actual model call is about to happen
    # (cache hits never reach the model and therefore skip this hook).
    before_cb = _chain_before(cache_before, obs_before, trace_before)
    after_cb  = _chain_after(cache_after, obs_after, trace_after)

    # Read the per-agent LLM caps from analysts config.  These drive three
    # things: the GenerateContentConfig token cap on the LlmAgent call,
    # and the timeout + retry budgets on the RetryingAgentWrapper.
    llm_caps = get_analysts_config().news.llm

    # -----------------------------------------------------------------------
    # Build the GenerateContentConfig.
    #
    # ``max_output_tokens`` caps output so long-running generation cannot
    # wedge the tick before the wall-clock timeout fires.
    #
    # Thinking control is config-driven and model-family-agnostic: the config
    # block sets exactly one of ``thinking_budget`` (Gemini 2.5 integer
    # ceiling) or ``thinking_level`` (Gemini 3 effort enum).  ``build_thinking_
    # config`` selects the right ``ThinkingConfig`` shape, returns ``None`` when
    # neither is set (native thinking), and refuses both-at-once — which is the
    # exact request Gemini 3 rejects with an HTTP 400.  The knob is
    # Gemini-specific; a Claude-on-Vertex model ignores it.
    # -----------------------------------------------------------------------
    thinking_config = build_thinking_config(
        thinking_budget = llm_caps.thinking_budget,
        thinking_level  = llm_caps.thinking_level,
    )

    # -----------------------------------------------------------------------
    # Assemble the LlmAgent.
    # - before_agent_callback and after_agent_callback are intentionally omitted
    #   (left as None) — see docstring above.
    # - before_model_callback / after_model_callback carry cache + trace hooks.
    # -----------------------------------------------------------------------
    llm = LlmAgent(
        name                    = f"NewsAnalyst_{ticker}",
        # ``model`` (the raw config string) is reused above for the trace /
        # observability labels; only the ADK call site needs the resolved
        # form, which is a bare string for Gemini or a native ADK ``Claude``
        # instance for an ``anthropic_vertex/<region>/<model>`` ID.
        model                   = resolve_model(model),
        instruction             = instruction,
        output_schema           = LlmTickerVerdict,
        output_key              = f"temp:news_verdict_{ticker}",
        before_model_callback   = before_cb,
        after_model_callback    = after_cb,
        generate_content_config = genai_types.GenerateContentConfig(
            max_output_tokens = llm_caps.max_output_tokens,
            thinking_config   = thinking_config,
            # Low sampling temperature → consistent structured verdicts.  The
            # verdict is classification-like JSON, so a low temperature reduces
            # run-to-run sampling variance (the dominant source of iter-to-iter
            # backtest swings at the default temperature = 1).
            temperature       = llm_caps.temperature,
        ),
    )

    # Wrap in the retry layer so transient Vertex AI 429s, wall-clock
    # timeouts, and schema-validation failures are all handled before any
    # exception bubbles up to the isolation boundary.
    retrying = RetryingAgentWrapper(
        name            = f"NewsAnalyst_{ticker}_retrying",
        inner           = llm,
        timeout_seconds = llm_caps.timeout_seconds,
        policies        = build_retry_policies(
            timeout_retries = llm_caps.timeout_retries,
            schema_retries  = llm_caps.schema_retries,
        ),
        retry_state_key = "temp:_obs_news_retries",
    )

    # Outermost isolation wrapper — catches and logs any exception (including
    # exhausted retries) so a single broken ticker cannot abort the tick.
    return IsolatedFailureWrapper(
        name    = f"NewsAnalyst_{ticker}_isolated",
        inner   = retrying,
        analyst = "news",
        ticker  = ticker,
    )

"""Strategist v2 LlmAgent — emits per-ticker TickerStance, derives decision fields server-side."""
from __future__ import annotations

import logging

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.strategist.derivation import (
    StrategistContractViolation,
    TickContext,
    derive_decision_fields,
)
from agents.strategist.prompts import STRATEGIST_INSTRUCTION
from agents.strategist.schema import StrategistDecision
from broker.portfolio import Portfolio
from data.timeguard import resolve_as_of
from observability.terminal_log import emit_analyst_summary
from observability.trace import _trace_maybe

# Module-level logger for the validation callback and any future callbacks.
logger = logging.getLogger(__name__)


def _coerce_portfolio(value: Portfolio | dict | None) -> Portfolio:
    """Return a Portfolio regardless of whether state stores it as an object or a serialised dump.

    Args:
        value: Either a ``Portfolio`` instance, a dict produced by
            ``Portfolio.model_dump(mode="json")``, or ``None``.

    Returns:
        A ``Portfolio`` instance. Returns an empty portfolio (cash=0.0, no positions)
        when ``value`` is ``None``.
    """
    if isinstance(value, Portfolio):
        return value
    if value is None:
        return Portfolio(cash=0.0)
    return Portfolio.model_validate(value)


# NOTE — A2.1 removed the two strategist before_agent_callbacks
# (``_held_view_before_callback`` and ``_evidence_view_before_callback``).  Their
# work now lives in ``agents.strategist.context_shim.StrategistContextShim``,
# which yields a single ``Event(state_delta=...)`` rather than mutating state in
# place from a callback — see Rule 1 of ``docs/contract-invariants.md``.


def _log_offending_decision(
    tick_id: str,
    decision: StrategistDecision,
    violation: str,
) -> None:
    """Emit a structured error log capturing the strategist's offending output.

    Called immediately before every ``StrategistContractViolation`` raise so
    that the LLM's own reasoning / decision_tag survives in the run log even
    when ``STOCKBOT_TRACE=1`` is not set.  Without this, the raised exception
    carries only the bad ticker(s) and the rest of the decision context
    (decision_tag, reasoning, thesis) is lost when the tick aborts.

    Args:
        tick_id: The tick identifier from state (or ``"unknown"`` fallback).
        decision: The parsed ``StrategistDecision`` that failed validation.
        violation: A short human-readable description of which contract was
            broken — included verbatim in the log message so the line is
            self-contained when grepping run logs.
    """

    logger.error(
        "Strategist contract violation on tick=%s: %s | decision_tag=%r "
        "reasoning=%r thesis=%r confidence=%s n_stances=%d",
        tick_id,
        violation,
        decision.decision_tag,
        decision.reasoning,
        decision.thesis,
        decision.confidence,
        len(decision.stances),
    )


def _strategist_validation_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Validate per-ticker stances; on success, derive decision fields and write back.

    Runs the cross-stance checks that the schema can't express on its own
    (off-watchlist tickers; intent=None on any stance).  Verb-conditional
    reason checks (close/trim must supply a reason) are delegated to
    ``derive_decision_fields`` which raises loudly on violation.

    Active-stances contract (from the 2026-05-21 simplification):

        The strategist no longer needs to emit a stance for *every*
        watchlist ticker.  It emits stances only for tickers it wants to
        *change* (open / add / trim / close); any watchlist ticker the
        strategist does NOT emit a stance for is treated as a carry-forward
        (held → keep holding, flat → stay flat).  Derivation pads
        ``target_weights`` accordingly so downstream agents still see an
        exhaustive dict.  This callback therefore does NOT enforce
        exhaustiveness — only that whatever IS emitted is on-watchlist.

    Why every failure raises rather than returning Content:

        Returning a ``genai_types.Content`` from an ``after_agent_callback``
        does NOT re-prompt the LLM in ADK — it replaces the agent's final
        response and ends the agent.  The original implementation returned
        ``_reprompt(...)`` Content intending to round-trip the validation
        error back to the model, but ADK never did so.  The result was a
        silent partial decision: stances persisted to the database, but
        the after-callback's derivation step never ran, so
        ``target_weights`` stayed at the schema default ``{}`` and the
        RiskGate produced zero orders for every tick.  Raising
        ``StrategistContractViolation`` instead makes the failure abort
        the tick loudly so we can see the LLM misbehaving.

    Args:
        callback_context: ADK callback context carrying the mutable pipeline state.

    Returns:
        ``None`` on success; never returns a value otherwise — failures raise.

    Raises:
        StrategistContractViolation: when the decision violates a
            watchlist-level contract (off-watchlist tickers, intent=None,
            or missing reason on close/trim).
    """
    state = callback_context.state
    raw = state.get("strategist_decision")
    if not raw:
        return None

    # Deserialise if the decision arrived as a JSON dict (post-serialisation path).
    decision = (
        StrategistDecision.model_validate(raw) if isinstance(raw, dict) else raw
    )

    tickers: list[str] = state.get("tickers", []) or []
    portfolio = _coerce_portfolio(state.get("portfolio"))
    current_weights = portfolio.current_weights()
    tick_id: str = state.get("tick_id") or state.get("recorded_at", "unknown")

    # ── Pass 1: No off-watchlist tickers ─────────────────────────────────────
    # Prevents the model from inventing tickers not in the current watchlist.
    # No exhaustiveness check — omission is read as an implicit hold by
    # ``derive_legacy_fields`` (active-stances contract); the strategist only
    # emits stances for the tickers it wants to *change*.
    emitted = {s.ticker for s in decision.stances}
    extras = [t for t in emitted if t not in tickers]
    if extras:

        msg = (
            f"Strategist included off-watchlist tickers: {extras}.  "
            f"Only emit stances for the watchlist."
        )
        _log_offending_decision(str(tick_id), decision, msg)
        raise StrategistContractViolation(msg)

    # ── Pass 2: Intent-based action count tally ──────────────────────────────
    # Read ``stance.intent`` directly — no more weight-comparison derivation.
    # Reason-presence checks (close/trim require a reason) are now enforced
    # inside ``derive_decision_fields`` with a loud raise, so we only need to
    # tally the per-action counts here for the success summary log below.
    action_counts: dict[str, int] = {
        "open": 0, "close": 0, "trim": 0, "add": 0, "hold": 0, "update": 0,
    }

    for stance in decision.stances:
        # Intent-based: read the verb directly rather than inferring it from
        # current vs preferred weight (the old lifecycle-derived approach).
        action = stance.intent or "hold"
        action_counts[action] = action_counts.get(action, 0) + 1

    # ── Pass 3: Derive decision fields ───────────────────────────────────────
    # All validation passed — derive the canonical decision fields from the
    # stances so downstream consumers (executor, persistence) see the shape
    # they expect.  Reads intent + weight rather than preferred_weight; raises
    # loudly if any stance has intent=None (no silent legacy-path fallback).
    #
    # Use state["as_of"] as the derivation timestamp when available (backtest
    # replay path) so PositionThesis.opened_at is deterministic.  Fall back to
    # wall-clock on live runs where as_of is absent.
    #
    # ``watchlist`` is passed through so the derivation's carry-forward pass
    # can pad ``target_weights`` for tickers the strategist did not emit a
    # stance for (active-stances contract).
    raw_as_of = state.get("as_of")
    derivation_now = resolve_as_of(
        raw_as_of,
        allow_wallclock=True,
        site="strategist/agent._after_validation",
    )
    ctx = TickContext(
        tick_id=str(tick_id),
        decision_tag=decision.decision_tag,
        now=derivation_now,
        current_weights=current_weights,
        watchlist=tickers,
    )

    # derive_decision_fields reads stance.intent + stance.weight directly.
    derived = derive_decision_fields(decision.stances, ctx)
    decision.target_weights = derived.target_weights
    decision.close_reasons = derived.close_reasons
    decision.trim_reasons = derived.trim_reasons

    # ── Per-tick success summary ─────────────────────────────────────────────
    # One concise INFO line so you can scan a run log and immediately see
    # whether the strategist is actually committing capital or just
    # hold-flat-ing the entire watchlist.  Useful sanity check after the
    # silent-zero-orders bug fixed in this same change — if the new
    # target_weights are still empty post-derivation, this line will say so.
    nonzero_weight_sum = sum(w for w in derived.target_weights.values() if w > 0.0)
    logger.info(
        "Strategist tick=%s: opens=%d closes=%d trims=%d adds=%d holds=%d updates=%d"
        " | nonzero_weight_sum=%.4f decision_tag=%r confidence=%s",
        tick_id,
        action_counts["open"],
        action_counts["close"],
        action_counts["trim"],
        action_counts["add"],
        action_counts["hold"],
        action_counts["update"],
        nonzero_weight_sum,
        decision.decision_tag,
        decision.confidence,
    )

    # Write the enriched decision (with legacy fields populated) back to state.
    decision_dump = decision.model_dump(mode="json")
    state["strategist_decision"] = decision_dump

    # Surface the strategist decision on the per-tick trace so downstream
    # inspection (decisions/, report/) and ad-hoc trace forensics can see
    # the full stance set, decision_tag, reasoning, and derived weights.
    # No-op unless state["temp:_trace"] is set by the backtest driver.
    _trace_maybe(state, "03_strategist", decision_dump)

    # ── Terminal summary row ──────────────────────────────────────────────────
    # Emit one singleton summary row ("strategist: 1/1 ✓ · 2.1s · 8.4k tok")
    # using the accumulator written by the after_model_callback.  This mirrors
    # the same pattern used in news/joiner.py and fundamental/joiner.py, keeping
    # all three analysts' summary rows visually consistent.
    import os
    if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
        _strat_calls:   list[dict]     = state.get("temp:_obs_strategist_calls")   or []
        _strat_retries: dict[str, int] = state.get("temp:_obs_strategist_retries") or {}
        emit_analyst_summary(
            "strategist",
            calls        = _strat_calls,
            ticker_count = 1,
            retries      = _strat_retries,
        )

    return None


# ── Agent factory ─────────────────────────────────────────────────────────────


def build_strategist():
    """Construct the production Strategist branch — ``SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent]]``.

    This factory is the **single construction path** for the strategist.  Both
    the live pipeline (``orchestrator.pipeline._build_strategist``) and any
    test that needs a real strategist agent should call this function — there
    is no module-level singleton.  Pre-2026-05-21 the strategist had two
    construction sites: an inline one in ``pipeline.py`` (which production
    used) and a module-level singleton here (which a few tests used) — each
    carried its own ``"gemini-…"`` literal, and a model swap on one silently
    no-op'd on the other.  Centralising via ``config/models.json`` plus this
    factory closes that footgun.

    The branch shape:

    - ``StrategistContextShim`` runs first and hydrates ``temp:held_positions_view``,
      ``temp:ticker_evidence``, and ``temp:ticker_evidence_objects`` via a
      yielded ``Event(state_delta=…)`` (contract Rule 1).
    - The downstream ``LlmAgent`` (wrapped in ``RetryingAgentWrapper``)
      resolves those keys via ADK's instruction-variable substitution and
      emits its ``StrategistDecision``.

    Why the retry wrap is **inside** the SequentialAgent
    ----------------------------------------------------
    The original implementation wrapped the whole SequentialAgent in a
    ``RetryingAgentWrapper`` at the pipeline-composition layer.  That broke
    the strategist with ``KeyError: 'Context variable not found:
    temp:held_positions_view'`` because the retry wrapper buffers every
    event the inner yields, then forwards them only on success.  When the
    inner is a SequentialAgent, ContextShim's ``state_delta`` event is
    buffered — the ADK Runner never sees it, never applies it to
    ``ctx.session.state``, and the LlmAgent's
    ``inject_session_state`` step fails before any 429 risk even
    materialises.

    The fix: wrap only the ``LlmAgent`` (the unit that can actually 429).
    ContextShim runs unwrapped — its ``state_delta`` event flows to the
    outer Runner via the SequentialAgent, the Runner applies it, and the
    wrapped LlmAgent then reads it from a hydrated session state.  See
    :mod:`agents.llm_retry` for the wrap's invariants.

    The model identifier is read from ``config/models.json::strategist`` via
    :func:`src.config.models.get_models_config`.  Trace callbacks are wired
    only when the ``STOCKBOT_TRACE=1`` environment variable is set — a
    zero-cost gate that keeps prod hot-path free of trace overhead.

    Returns
    -------
    google.adk.agents.SequentialAgent
        The ``"StrategistBranch"`` SequentialAgent ready to be added to the
        pipeline's top-level SequentialAgent.  ``branch.sub_agents[1]`` is a
        ``RetryingAgentWrapper``; the inner ``LlmAgent`` is at
        ``branch.sub_agents[1].inner`` for tests that need to inspect
        LlmAgent attributes (model, callbacks, output_key, etc.).
    """

    import os

    from google.adk.agents import SequentialAgent
    from google.genai import types as genai_types

    from agents.analysts._common import _chain_after, _chain_before
    from agents.llm_retry import RetryingAgentWrapper, build_retry_policies
    from agents.strategist.context_shim import StrategistContextShim
    from config.models import get_models_config
    from config.strategist import get_strategist_config
    from observability.terminal_log import make_observability_callbacks
    from observability.trace import make_llm_trace_callbacks

    # Read the model ID from the central config.  One JSON edit moves both
    # live and backtest runs — no shadow constant to forget.
    model_name = get_models_config().strategist

    # Read the per-call runtime caps from config/strategist.json.  These drive
    # the LlmAgent's token budget and the RetryingAgentWrapper's timeout + retry
    # budgets — the single source of truth so tuning one JSON key takes effect
    # everywhere.
    llm_caps = get_strategist_config().llm

    # Observability callbacks — emit one terminal log row for the strategist
    # LLM call.  Only wired when STOCKBOT_TERMINAL_LOG=1 so backtest replays
    # and unit tests add zero overhead.  The strategist has no per-ticker
    # progress counter (it makes one call per tick), so ticker_index=1 /
    # ticker_count=1 is used to suppress the N/M column meaningfully.
    obs_before = None
    obs_after  = None

    if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
        obs_before, obs_after = make_observability_callbacks(
            analyst      = "strategist",
            ticker       = "decision",
            ticker_index = 1,
            ticker_count = 1,
            model_name   = model_name,
        )

    # Trace callbacks are opt-in via STOCKBOT_TRACE=1.  Zero-cost when off:
    # both callbacks remain ``None`` and ADK skips the dispatch entirely.
    trace_before = None
    trace_after  = None

    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks(
            "05_strategist_llm",
            model=model_name,
        )

    # Chain observability + trace callbacks.
    before_model = _chain_before(obs_before, trace_before)
    after_model  = _chain_after(obs_after, trace_after)

    # The inner LlmAgent — its ``after_agent_callback`` runs the legacy-field
    # derivation + contract validation defined above in this module.  Note:
    # no ``after_model_callback`` beyond the optional trace hook (the legacy
    # ``_strategist_after_model_composite`` clamp was deleted in A2.3 because
    # the prompt itself forbids negative weights — see
    # ``tests/unit/agents/strategist/test_after_model_unwired.py``).
    llm = LlmAgent(
        name                    = "Strategist",
        model                   = model_name,
        instruction             = STRATEGIST_INSTRUCTION,
        output_schema           = StrategistDecision,
        output_key              = "strategist_decision",
        after_agent_callback    = _strategist_validation_callback,
        before_model_callback   = before_model,
        after_model_callback    = after_model,
        generate_content_config = genai_types.GenerateContentConfig(
            max_output_tokens = llm_caps.max_output_tokens,
        ),
    )

    # Wrap the LlmAgent in the retry layer so transient Vertex 429s trigger
    # exponential backoff, wall-clock timeouts abort hung calls, and schema
    # failures trigger re-prompts.  The wrap goes here (inside the
    # SequentialAgent), not around the SequentialAgent itself — see the
    # docstring for why.  The retry budgets (timeout_retries, schema_retries)
    # come from the same strategist.llm config section as the token budget.
    wrapped_llm = RetryingAgentWrapper(
        name            = "StrategistLlmRetrying",
        inner           = llm,
        timeout_seconds = llm_caps.timeout_seconds,
        policies        = build_retry_policies(
            timeout_retries = llm_caps.timeout_retries,
            schema_retries  = llm_caps.schema_retries,
        ),
        retry_state_key = "temp:_obs_strategist_retries",
    )

    return SequentialAgent(
        name       = "StrategistBranch",
        sub_agents = [StrategistContextShim(), wrapped_llm],
    )

"""Strategist v2 LlmAgent — emits per-ticker TickerStance, derives legacy fields server-side."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.risk_gate.lifecycle import StrategistContractViolation
from agents.strategist.derivation import TickContext, derive_legacy_fields
from agents.strategist.held_view import render_held_positions_view
from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.prompts import STRATEGIST_INSTRUCTION
from agents.strategist.schema import StrategistDecision
from broker.portfolio import Portfolio
from contract.digest import build_ticker_evidence
from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS
from contract.evidence import AnalystEvidence
from contract.strategist_prompt import render_all_ticker_blocks
from contract.ticker_evidence import TickerEvidence
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe, make_llm_trace_callbacks

# Module-level logger.  Used by the after-model clamp callback to emit a
# WARNING whenever it has to fix an out-of-range stance value — see
# ``_clamp_stance_bounds_after_model`` below.
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


def _held_view_before_callback(callback_context: CallbackContext) -> genai_types.Content | None:
    """Render the held-positions block into ``state["held_positions_view"]``.

    Reads ``state["positions"]`` (dict of ticker → PositionThesis dump) and
    ``state["portfolio"]`` (Portfolio dump or object), then writes a formatted
    string to ``state["held_positions_view"]`` for the prompt template to
    interpolate.

    Args:
        callback_context: ADK callback context carrying the mutable pipeline state.

    Returns:
        ``None`` — this callback never short-circuits the agent run.
    """
    state = callback_context.state
    positions = state.get("positions", {}) or {}
    portfolio = _coerce_portfolio(state.get("portfolio"))
    state["held_positions_view"] = render_held_positions_view(positions, portfolio)
    return None


def _evidence_view_before_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Build TickerEvidence per ticker from the per-analyst evidence lists, then render.

    The pipeline stores per-analyst evidence as flat lists in state under keys like
    ``technical_evidence``, ``fundamental_evidence``, etc. This callback:
    1. Indexes each list by ticker.
    2. Calls ``build_ticker_evidence`` to aggregate them into a ``TickerEvidence`` per ticker.
    3. Writes the rendered string to ``state["ticker_evidence"]`` for the prompt template.
    4. Also writes the raw JSON-serialised objects to ``state["ticker_evidence_objects"]``
       for any downstream code that needs the structured data.

    Args:
        callback_context: ADK callback context carrying the mutable pipeline state.

    Returns:
        ``None`` — this callback never short-circuits the agent run.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", []) or []
    tick_id: str = state.get("tick_id", "unknown")

    # Resolve the tick timestamp used as ``recorded_at`` for evidence objects.
    # Priority order:
    #   1. state["as_of"]    — set by the backtest driver to the historical tick
    #      timestamp; guarantees deterministic replay.
    #   2. state["recorded_at"] — set by some live-path callers as an ISO string
    #      or datetime.
    #   3. resolve_as_of wall-clock fallback — live fallback when neither key is
    #      present.  Strict mode vetoes this if STOCKBOT_STRICT_AS_OF=1.
    as_of_raw = state.get("as_of")
    if isinstance(as_of_raw, datetime):
        # Backtest path — deterministic replay timestamp is available.
        recorded_at = as_of_raw
    else:
        recorded_at_raw = state.get("recorded_at")
        if isinstance(recorded_at_raw, str):
            # Live path where recorded_at was serialised as an ISO string.
            recorded_at = datetime.fromisoformat(recorded_at_raw)
        else:
            # Fall through to timeguard — walls clock or strict-mode abort.
            recorded_at = resolve_as_of(
                recorded_at_raw if isinstance(recorded_at_raw, datetime) else None,
                allow_wallclock=True,
                site="strategist/agent._evidence_view",
            )

    def _index(key: str) -> dict[str, AnalystEvidence]:
        """Index a per-analyst evidence list by ticker.

        Items in the list may be raw dicts (post-JSON-serialisation) or
        already-validated ``AnalystEvidence`` objects.

        Args:
            key: The state key, e.g. ``"technical_evidence"``.

        Returns:
            A dict mapping ticker → ``AnalystEvidence``.
        """
        items = state.get(key, []) or []
        out: dict[str, AnalystEvidence] = {}
        for item in items:
            ev = AnalystEvidence.model_validate(item) if isinstance(item, dict) else item
            out[ev.ticker] = ev
        return out

    # Collect evidence for each analyst dimension, indexed by ticker.
    tech = _index("technical_evidence")
    fund = _index("fundamental_evidence")
    news = _index("news_evidence")  # renamed from sentiment_evidence in Task 6
    sm = _index("smart_money_evidence")

    # Build one TickerEvidence per watchlist ticker by assembling the available
    # per-analyst evidence. Tickers with no evidence for a given analyst simply
    # omit that analyst from per_analyst — build_ticker_evidence handles sparse dicts.
    ticker_evidence: list[TickerEvidence] = []
    for t in tickers:
        per_analyst: dict[str, AnalystEvidence] = {}
        if t in tech:
            per_analyst["technical"] = tech[t]
        if t in fund:
            per_analyst["fundamental"] = fund[t]
        if t in news:
            per_analyst["news"] = news[t]
        if t in sm:
            per_analyst["smart_money"] = sm[t]

        te = build_ticker_evidence(
            per_analyst=per_analyst,
            ticker=t,
            tick_id=tick_id,
            recorded_at=recorded_at,
            weights=DEFAULT_ANALYST_WEIGHTS,
        )
        ticker_evidence.append(te)

    # Keep both shapes in state — the rendered string for the prompt template, and
    # the JSON-serialised objects for any downstream code that wants structured data.
    # The renderer (render_all_ticker_blocks) uses the feature-bullet registries in
    # contract.strategist_prompt to produce labelled, human-readable per-ticker blocks
    # that include feature values, rationale tags, and any prose AnalystReport.
    state["ticker_evidence_objects"] = [te.model_dump(mode="json") for te in ticker_evidence]
    state["ticker_evidence"] = render_all_ticker_blocks(ticker_evidence)

    # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
    _trace_maybe(state, "04_digest", state["ticker_evidence_objects"])

    return None


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
    (decision_tag, reasoning, updated_thesis) is lost when the tick aborts.

    Args:
        tick_id: The tick identifier from state (or ``"unknown"`` fallback).
        decision: The parsed ``StrategistDecision`` that failed validation.
        violation: A short human-readable description of which contract was
            broken — included verbatim in the log message so the line is
            self-contained when grepping run logs.
    """

    logger.error(
        "Strategist contract violation on tick=%s: %s | decision_tag=%r "
        "reasoning=%r updated_thesis=%r confidence=%s n_stances=%d",
        tick_id,
        violation,
        decision.decision_tag,
        decision.reasoning,
        decision.updated_thesis,
        decision.confidence,
        len(decision.stances),
    )


def _strategist_validation_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Validate per-ticker stances; on success, derive legacy fields and write back.

    Runs the cross-stance checks that the schema can't express on its own
    (exhaustiveness across the watchlist, no off-watchlist tickers, and
    lifecycle-specific reason fields that depend on the current portfolio).
    Per-stance discipline (non-zero stances must carry horizon/target_price/
    stop_price) is enforced at the schema level by
    ``TickerStance._require_lifecycle_hints_on_nonzero`` — failures there
    raise during ADK's ``output_schema`` parse and never reach this
    callback.

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
            watchlist-level contract (missing tickers, off-watchlist
            tickers, or missing close_reason/trim_reason for the
            lifecycle action implied by current vs preferred weight).
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
    current_prices = {t: pos.last_price for t, pos in portfolio.positions.items()}
    tick_id: str = state.get("tick_id") or state.get("recorded_at", "unknown")

    # ── Pass 1: Exhaustive ────────────────────────────────────────────────────
    # Every watchlist ticker must have exactly one stance.
    emitted = {s.ticker for s in decision.stances}
    missing = [t for t in tickers if t not in emitted]
    if missing:

        msg = (
            f"Strategist missed stances for these tickers: {missing}.  "
            f"The strategist must emit a TickerStance for EVERY watchlist ticker."
        )
        _log_offending_decision(str(tick_id), decision, msg)
        raise StrategistContractViolation(msg)

    # ── Pass 2: No off-watchlist tickers ─────────────────────────────────────
    # Prevents the model from inventing tickers not in the current watchlist.
    extras = [t for t in emitted if t not in tickers]
    if extras:

        msg = (
            f"Strategist included off-watchlist tickers: {extras}.  "
            f"Only emit stances for the watchlist."
        )
        _log_offending_decision(str(tick_id), decision, msg)
        raise StrategistContractViolation(msg)

    # ── Pass 3: Lifecycle reason enforcement ─────────────────────────────────
    # The derived action for each stance is computed from current vs preferred
    # weight.  Closes and trims need an explicit reason in the audit trail —
    # these checks live here (not in the schema) because they depend on the
    # current portfolio state, which the schema validator can't see.
    #
    # Non-zero stances missing horizon/target_price/stop_price are caught
    # earlier by ``TickerStance._require_lifecycle_hints_on_nonzero`` at
    # schema-validation time.
    #
    # We also accumulate the per-action counts here so the success log at the
    # end of the callback can summarise the tick in one line without a second
    # pass over the stance list.
    action_counts: dict[str, int] = {
        "open": 0, "close": 0, "trim": 0, "add": 0, "hold": 0,
    }

    for stance in decision.stances:
        curr = current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(curr, stance.preferred_weight)
        action_counts[action] = action_counts.get(action, 0) + 1

        if action == "close" and not stance.close_reason:

            # Full exit requires an explicit close_reason for audit trail.
            msg = (
                f"Stance for {stance.ticker} closes a position but is missing "
                f"close_reason."
            )
            _log_offending_decision(str(tick_id), decision, msg)
            raise StrategistContractViolation(msg)

        if action == "trim" and not stance.trim_reason:

            # Partial reduction requires an explicit trim_reason for audit trail.
            msg = (
                f"Stance for {stance.ticker} trims a position but is missing "
                f"trim_reason."
            )
            _log_offending_decision(str(tick_id), decision, msg)
            raise StrategistContractViolation(msg)

    # ── Pass 4: Derive legacy fields ─────────────────────────────────────────
    # All validation passed — derive the flat legacy fields from the stances
    # so downstream consumers (executor, persistence) see the shape they expect.
    #
    # Use state["as_of"] as the derivation timestamp when available (backtest
    # replay path) so PositionThesis.opened_at is deterministic.  Fall back to
    # wall-clock on live runs where as_of is absent.
    raw_as_of = state.get("as_of")
    derivation_now = resolve_as_of(
        raw_as_of if isinstance(raw_as_of, datetime) else None,
        allow_wallclock=True,
        site="strategist/agent._after_validation",
    )
    ctx = TickContext(
        tick_id=str(tick_id),
        decision_tag=decision.decision_tag,
        now=derivation_now,
        current_prices=current_prices,
        current_weights=current_weights,
    )
    derived = derive_legacy_fields(decision.stances, ctx)
    decision.target_weights = derived.target_weights
    decision.new_positions = derived.new_positions
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
        "Strategist tick=%s: opens=%d closes=%d trims=%d adds=%d holds=%d "
        "| nonzero_weight_sum=%.4f decision_tag=%r confidence=%s",
        tick_id,
        action_counts["open"],
        action_counts["close"],
        action_counts["trim"],
        action_counts["add"],
        action_counts["hold"],
        nonzero_weight_sum,
        decision.decision_tag,
        decision.confidence,
    )

    # Write the enriched decision (with legacy fields populated) back to state.
    state["strategist_decision"] = decision.model_dump(mode="json")
    return None


def _composite_before_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Run held-view and evidence-view before-callbacks in sequence.

    Short-circuits if either callback returns a ``Content`` object (which would
    indicate an unexpected error; in practice, neither callback currently does).

    Args:
        callback_context: ADK callback context carrying the mutable pipeline state.

    Returns:
        ``None`` if both callbacks complete normally, or the first non-``None``
        ``Content`` returned by either callback.
    """
    out = _held_view_before_callback(callback_context)
    if out is not None:
        return out
    return _evidence_view_before_callback(callback_context)


# ── Sanitising after-model callback ───────────────────────────────────────────

# Fields on ``TickerStance`` that carry the ``ge=0.0, le=1.0`` constraint and
# therefore need clamping if the LLM drifts.  Kept as a module-level constant
# so the unit test can import it and stay in sync.
_CLAMPED_STANCE_FIELDS: tuple[str, ...] = ("preferred_weight", "conviction")


def _clamp_stance_bounds_after_model(
    callback_context: CallbackContext,                                          # noqa: ARG001 — required by ADK signature
    llm_response: Any,
) -> Any:
    """Clamp ``preferred_weight`` and ``conviction`` to ``[0.0, 1.0]`` before validation.

    The ``TickerStance`` schema enforces ``ge=0.0, le=1.0`` on both fields, but
    Gemini occasionally produces out-of-range values — notably *negative*
    ``preferred_weight`` to express short positions, which this bot does not
    support (see ``stance_schema.py`` and ``lifecycle.py`` — long-only by design).
    Without intervention, ADK's ``_maybe_save_output_to_state`` calls Pydantic's
    ``model_validate_json``, which raises ``ValidationError`` and aborts the
    whole tick.

    This callback fires *after* the LLM call but *before* ADK's schema
    validation pass.  It deserialises the response JSON, clamps any
    out-of-range numeric fields on each stance to ``[0.0, 1.0]``, and writes
    the corrected JSON back into the response part.  A WARNING is emitted
    listing every clamp so we can monitor how often the model drifts (a high
    rate would indicate the prompt needs further tightening).

    Defensive design notes:
    - Silently tolerates any response shape the function cannot understand
      (missing content/parts, non-JSON text, non-list stances).  The
      downstream validator will surface the original error if there is one;
      this callback's job is *only* to fix out-of-range numerics on stances.
    - Mutates ``part.text`` in place rather than constructing a new
      ``LlmResponse``, mirroring the in-place pattern used by ADK's own
      trace callbacks.
    - Returns ``None`` so ADK continues with the (possibly modified) response.

    Parameters
    ----------
    callback_context:
        ADK callback context (unused — kept for signature compatibility).
    llm_response:
        The raw ``LlmResponse`` from Gemini.  Mutated in place.

    Returns
    -------
    None
        ADK proceeds with the (mutated) response.
    """

    # ── 1. Reach the JSON text on the response, if any ─────────────────────────
    content = getattr(llm_response, "content", None)
    parts   = getattr(content, "parts", None)
    if not parts:
        return None

    part = parts[0]
    text = getattr(part, "text", None)
    if not text:
        return None

    # ── 2. Parse — bail out on garbage so the downstream parser can complain ──
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None

    stances = payload.get("stances") if isinstance(payload, dict) else None
    if not isinstance(stances, list):
        return None

    # ── 3. Walk every stance, clamp every constrained field, collect a log ────
    clamped: list[str] = []

    for stance in stances:
        if not isinstance(stance, dict):
            continue

        ticker = stance.get("ticker", "?")

        for field in _CLAMPED_STANCE_FIELDS:
            value = stance.get(field)

            # Booleans are technically a subclass of int — exclude explicitly.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue

            if value < 0.0 or value > 1.0:
                new_value = max(0.0, min(1.0, float(value)))
                clamped.append(f"{ticker}:{field}={value!r}->{new_value!r}")
                stance[field] = new_value

    # ── 4. If we touched anything, re-serialise + warn ─────────────────────────
    if clamped:
        logger.warning(
            "Strategist response: clamped %d out-of-range stance value(s) to [0,1]: %s",
            len(clamped), ", ".join(clamped),
        )
        # ``separators`` keeps the re-serialisation compact so we don't bloat
        # the trace; ADK only needs valid JSON, not formatted.
        part.text = json.dumps(payload, separators=(",", ":"))

    return None


# ── Agent definition ──────────────────────────────────────────────────────────

# Attach LLM trace callbacks only when STOCKBOT_TRACE=1 is set at import time.
# The module-level singleton is built once; trace callbacks gate on state["_trace"]
# at call time so they are fully inert on production runs where _trace is absent.
_STRATEGIST_MODEL = "gemini-2.5-pro"
_strategist_before_model: object = None
_strategist_after_trace: object  = None
if os.environ.get("STOCKBOT_TRACE") == "1":
    _strategist_before_model, _strategist_after_trace = make_llm_trace_callbacks(
        "05_strategist_llm", model=_STRATEGIST_MODEL
    )


def _strategist_after_model_composite(
    callback_context: CallbackContext,
    llm_response: Any,
) -> Any:
    """Run the clamp callback first, then the trace callback if trace mode is on.

    Always-on clamping is mandatory: the ``ge=0`` constraint on
    ``preferred_weight`` must hold regardless of whether tracing is enabled.
    Trace is best-effort and runs *after* the clamp so the recorded trace
    reflects what ADK actually validated (the corrected JSON), not the raw
    pre-clamp response.

    Parameters
    ----------
    callback_context:
        ADK callback context, forwarded to both callbacks.
    llm_response:
        The raw ``LlmResponse``.  Mutated in place by the clamp callback.

    Returns
    -------
    None
        ADK proceeds with the (possibly mutated) response.
    """

    _clamp_stance_bounds_after_model(callback_context, llm_response)

    if _strategist_after_trace is not None:
        _strategist_after_trace(callback_context, llm_response)

    return None


strategist_agent = LlmAgent(
    name="Strategist",
    model=_STRATEGIST_MODEL,  # preserved from prior agent.py — do not downgrade
    instruction=STRATEGIST_INSTRUCTION,
    output_schema=StrategistDecision,
    output_key="strategist_decision",
    before_agent_callback=_composite_before_callback,
    after_agent_callback=_strategist_validation_callback,
    before_model_callback=_strategist_before_model,
    after_model_callback=_strategist_after_model_composite,
)

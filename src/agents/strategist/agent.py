"""Strategist v2 LlmAgent — emits per-ticker TickerStance, derives legacy fields server-side."""
from __future__ import annotations

import os
from datetime import UTC, datetime

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.strategist.derivation import TickContext, derive_legacy_fields
from agents.strategist.evidence_view import render_ticker_evidence
from agents.strategist.held_view import render_held_positions_view
from agents.strategist.lifecycle import derive_lifecycle_action
from agents.strategist.prompts import STRATEGIST_INSTRUCTION
from agents.strategist.schema import StrategistDecision
from broker.portfolio import Portfolio
from contract.digest import build_ticker_evidence
from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS
from contract.evidence import AnalystEvidence
from contract.ticker_evidence import TickerEvidence
from observability.trace import _trace_maybe


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

    # recorded_at may arrive as an ISO string (most common when state is round-tripped
    # through JSON), as a datetime already (in-process path), or be absent entirely.
    # On Python 3.11+ ``datetime.fromisoformat`` accepts the trailing ``Z`` natively.
    recorded_at_raw = state.get("recorded_at")
    recorded_at = (
        datetime.fromisoformat(recorded_at_raw)
        if isinstance(recorded_at_raw, str)
        else (recorded_at_raw or datetime.now(tz=UTC))
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
    state["ticker_evidence_objects"] = [te.model_dump(mode="json") for te in ticker_evidence]
    state["ticker_evidence"] = render_ticker_evidence(ticker_evidence)

    # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
    _trace_maybe(state, "04_digest", state["ticker_evidence_objects"])

    return None


def _reprompt(text: str) -> genai_types.Content:  # always returns Content, not None
    """Wrap a re-prompt message in the ADK Content envelope.

    Args:
        text: The corrective instruction to send back to the LLM.

    Returns:
        A ``genai_types.Content`` with role ``"user"`` containing a single text part.
    """
    return genai_types.Content(
        parts=[genai_types.Part(text=text)],
        role="user",
    )


def _strategist_validation_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Validate per-ticker stances; on success, derive legacy fields and write back.

    Runs four validation passes in order:
    1. Exhaustive — every watchlist ticker must have a stance.
    2. No extras — no off-watchlist tickers may appear.
    3. Lifecycle hint enforcement — open stances need horizon/target_price/stop_price;
       close stances need close_reason; trim stances need trim_reason.
    4. On a clean pass, derive legacy fields (target_weights, new_positions,
       close_reasons, trim_reasons) and write the enriched decision back to state.

    Args:
        callback_context: ADK callback context carrying the mutable pipeline state.

    Returns:
        A ``genai_types.Content`` re-prompt message if validation fails, or ``None``
        if all checks pass (allowing the pipeline to continue).
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
        return _reprompt(
            f"You missed stances for these tickers: {missing}. "
            f"Emit a TickerStance for EVERY watchlist ticker."
        )

    # ── Pass 2: No off-watchlist tickers ─────────────────────────────────────
    # Prevents the model from inventing tickers not in the current watchlist.
    extras = [t for t in emitted if t not in tickers]
    if extras:
        return _reprompt(
            f"You included off-watchlist tickers: {extras}. "
            f"Only emit stances for the watchlist."
        )

    # ── Pass 3: Lifecycle hint enforcement ────────────────────────────────────
    # The derived action for each stance is computed from current vs preferred weight.
    # Certain actions require the stance to carry additional fields.
    for stance in decision.stances:
        curr = current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(curr, stance.preferred_weight)

        if action == "open":
            # Opening a new position requires horizon, target_price, and stop_price
            # so the executor and memory writer can populate PositionThesis correctly.
            missing_fields = [
                name for name, val in (
                    ("horizon", stance.horizon),
                    ("target_price", stance.target_price),
                    ("stop_price", stance.stop_price),
                ) if val is None
            ]
            if missing_fields:
                return _reprompt(
                    f"Stance for {stance.ticker} opens a position but is missing: "
                    f"{missing_fields}. Include horizon, target_price, and stop_price on opens."
                )

        elif action == "close":
            # Full exit requires an explicit close_reason for audit trail.
            if not stance.close_reason:
                return _reprompt(
                    f"Stance for {stance.ticker} closes a position but is missing close_reason."
                )

        elif action == "trim":
            # Partial reduction requires an explicit trim_reason for audit trail.
            if not stance.trim_reason:
                return _reprompt(
                    f"Stance for {stance.ticker} trims a position but is missing trim_reason."
                )

    # ── Pass 4: Derive legacy fields ─────────────────────────────────────────
    # All validation passed — derive the flat legacy fields from the stances
    # so downstream consumers (executor, persistence) see the shape they expect.
    ctx = TickContext(
        tick_id=str(tick_id),
        decision_tag=decision.decision_tag,
        now=datetime.now(tz=UTC),
        current_prices=current_prices,
        current_weights=current_weights,
    )
    derived = derive_legacy_fields(decision.stances, ctx)
    decision.target_weights = derived.target_weights
    decision.new_positions = derived.new_positions
    decision.close_reasons = derived.close_reasons
    decision.trim_reasons = derived.trim_reasons

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


# ── LLM trace callbacks (attached only when STOCKBOT_TRACE=1) ─────────────────

def _make_strategist_trace_before(model: str) -> object:
    """Build a before_model_callback that captures the Strategist prompt.

    The callback is a no-op if ``state["_trace"]`` is not set.

    Parameters
    ----------
    model:
        Model identifier recorded alongside the prompt text.

    Returns
    -------
    Callable
        A before_model_callback compatible with ADK's ``LlmAgent``.
    """
    from google.adk.agents.callback_context import CallbackContext as _CC
    from google.adk.models.llm_request import LlmRequest as _Req
    from google.genai import types as _types

    from observability.trace import TraceWriter as _TW

    def _before(
        callback_context: _CC,
        llm_request: _Req,
    ) -> _types.Content | None:
        """Capture the outgoing Strategist prompt into the TraceWriter, if active."""
        state = callback_context.state
        tw = state.get("_trace") if isinstance(state, dict) else None
        if not isinstance(tw, _TW):
            return None

        prompt_parts: list[str] = []
        for content in (llm_request.contents or []):
            for part in (content.parts or []):
                if hasattr(part, "text") and part.text:
                    prompt_parts.append(part.text)

        tw.llm_pair(
            "05_strategist_llm",
            prompt="\n---\n".join(prompt_parts) or "(no text parts)",
            response="(pending)",
            model=model,
        )
        return None

    return _before


def _make_strategist_trace_after(model: str) -> object:
    """Build an after_model_callback that updates the Strategist response in the trace.

    Overwrites the ``"(pending)"`` placeholder written by the before-callback.

    Parameters
    ----------
    model:
        Model identifier (for consistency in the ``_out`` record).

    Returns
    -------
    Callable
        An after_model_callback compatible with ADK's ``LlmAgent``.
    """
    from google.adk.agents.callback_context import CallbackContext as _CC
    from google.adk.models.llm_response import LlmResponse as _Resp
    from google.genai import types as _types

    from observability.trace import TraceWriter as _TW

    def _after(
        callback_context: _CC,
        llm_response: _Resp,
    ) -> _types.Content | None:
        """Update the TraceWriter with the Strategist's response text."""
        state = callback_context.state
        tw = state.get("_trace") if isinstance(state, dict) else None
        if not isinstance(tw, _TW):
            return None

        response_parts: list[str] = []
        if llm_response.content:
            for part in (llm_response.content.parts or []):
                if hasattr(part, "text") and part.text:
                    response_parts.append(part.text)

        response_text = "\n---\n".join(response_parts) or "(no text parts)"

        # Overwrite the _out placeholder set by llm_pair during the before-callback.
        tw._sections["05_strategist_llm_out"] = {
            "model": model,
            "response": response_text,
        }
        return None

    return _after


# ── Agent definition ──────────────────────────────────────────────────────────

# Attach LLM trace callbacks only when STOCKBOT_TRACE=1 is set at import time.
# The module-level singleton is built once; trace callbacks gate on state["_trace"]
# at call time so they are fully inert on production runs where _trace is absent.
_STRATEGIST_MODEL = "gemini-2.5-pro"
_strategist_before_model: object = None
_strategist_after_model: object = None
if os.environ.get("STOCKBOT_TRACE") == "1":
    _strategist_before_model = _make_strategist_trace_before(_STRATEGIST_MODEL)
    _strategist_after_model  = _make_strategist_trace_after(_STRATEGIST_MODEL)

strategist_agent = LlmAgent(
    name="Strategist",
    model=_STRATEGIST_MODEL,  # preserved from prior agent.py — do not downgrade
    instruction=STRATEGIST_INSTRUCTION,
    output_schema=StrategistDecision,
    output_key="strategist_decision",
    before_agent_callback=_composite_before_callback,
    after_agent_callback=_strategist_validation_callback,
    before_model_callback=_strategist_before_model,
    after_model_callback=_strategist_after_model,
)

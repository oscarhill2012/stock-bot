"""Shared analyst base and callback utilities.

D3 removes the dual-emit pattern (``make_dual_emit_callback``) and the
exhaustive-validator helper in favour of the simpler ``make_evidence_callback``
that reads LLM-emitted verdicts directly from state and writes fully-formed
``AnalystEvidence`` records.  The legacy ``AnalystSignal`` Pydantic class is
also removed — the four per-analyst ``schema.py`` subclasses are deleted
alongside it (see D3 option-a).

``_chain_before`` and ``_chain_after`` are defined here to avoid identical
copies living in every LLM agent module.  Import them wherever callback
chains are needed.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from contract.evidence import AnalystEvidence, AnalystName, AnalystVerdict
from data.timeguard import resolve_as_of


def make_evidence_callback(
    *,
    analyst: AnalystName,
    extractor: Callable[[Any, str], dict[str, float]],
    verdicts_state_key: str,
) -> Callable[[CallbackContext], genai_types.Content | None]:
    """Build an ``after_agent_callback`` that converts LLM verdicts to ``AnalystEvidence``.

    The new evidence-only callback introduced in D3 does three things:

    1. Reads the per-ticker verdict list from ``state[verdicts_state_key]``.
       Each element is a dict matching the ``AnalystVerdict`` schema (``lean``,
       ``magnitude``, ``confidence``, ``rationale``, ``key_factors``,
       ``is_no_data``).
    2. For every ticker in the watchlist, calls
       ``extractor(state["temp:{analyst}_data"][ticker], ticker)`` to obtain
       the deterministic feature vector.  If the LLM omitted a verdict for a
       ticker, a no-data ``AnalystVerdict`` is synthesised so downstream
       consumers always receive one record per ticker.
    3. Writes the resulting ``AnalystEvidence`` list (as JSON-serialisable
       dicts) to ``state["{analyst}_evidence"]``.

    Note: ``feature_warnings`` is set to ``[]`` for now — extractors do not
    yet expose a warnings channel.  That plumbing is out of scope for D3.

    Parameters
    ----------
    analyst:
        The ``AnalystName`` literal identifying this analyst
        (``"technical"``, ``"fundamental"``, ``"news"``, ``"social"``, or
        ``"smart_money"``).
    extractor:
        Callable ``(raw_ticker_data, ticker) -> {feature: value}`` that
        computes the deterministic feature vector for one ticker.  Signature
        matches all four real extractors: args are ``(raw, ticker)``.
    verdicts_state_key:
        The state key that holds the list of verdict dicts emitted by the LLM
        (e.g. ``"technical_verdicts"``).

    Returns
    -------
    Callable
        A callback function compatible with ADK's ``after_agent_callback``
        protocol.  The callback returns ``None`` (no re-prompt) in all paths.
    """

    def _callback(callback_context: CallbackContext) -> genai_types.Content | None:
        """Execute the evidence-build loop for one analyst tick.

        Reads verdicts, runs extractors, and writes a complete evidence list
        to state.  Always returns ``None`` — the LLM is never re-prompted by
        this callback (the exhaustive-validator behaviour from dual-emit is
        retired in D3).
        """
        state = callback_context.state
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str = state.get("tick_id", "unknown")

        # Single timestamp for the whole batch — avoids microsecond skew
        # between records that belong to the same tick.
        # In a backtest, state["as_of"] holds the historical tick time so
        # evidence records are stamped with the replayed clock rather than
        # wall-clock now.  Live sessions have no "as_of" → wall-clock fallback.
        recorded_at: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="_common/fetch",
        )

        # Per-ticker raw data dict keyed by ticker symbol.
        # The ``temp:`` prefix mirrors the fetch callbacks which write
        # ``state["temp:{analyst}_data"]`` — ADK strips these keys at the
        # invocation boundary so stale data never bleeds across ticks.
        data: dict = state.get(f"temp:{analyst}_data", {}) or {}

        # Build a lookup from ticker → verdict dict for fast access below.
        # Two shapes are supported:
        # - Deterministic analysts (Technical, Social, SmartMoney) write a
        #   flat ``list[dict]`` directly to ``state[verdicts_state_key]``.
        # - LLM analysts (Fundamental, News) use ADK's ``output_schema=VerdictBatch``,
        #   which lands as ``{"verdicts": [...]}`` in state. Unwrap that here.
        raw = state.get(verdicts_state_key, []) or []
        if isinstance(raw, dict) and "verdicts" in raw:
            raw_verdicts: list[dict] = raw["verdicts"] or []
        else:
            raw_verdicts = raw
        verdicts_by_ticker: dict[str, dict] = {
            v["ticker"]: v for v in raw_verdicts
        }

        evidence_list: list[dict] = []

        for ticker in tickers:
            # Run the deterministic feature extractor for this ticker.
            # The extractor receives the per-ticker slice, not the full dict.
            # Pass as_of so time-delta features (e.g. days_since_last_filing in
            # the fundamental extractor) are computed from the replayed historical
            # clock rather than wall-clock time.
            # Pass state so extractors that need pipeline-wide context
            # (e.g. the technical extractor reading state["reference_prices"]
            # for relative_strength_vs_spy_* — Fix C) can access it.
            # callback_context.state is an ADK State proxy (no __iter__), so
            # call .to_dict() to get a plain dict.  Fall back to dict() for
            # plain-dict state objects in unit tests.
            _to_dict = getattr(state, "to_dict", None)
            state_snapshot: dict = _to_dict() if callable(_to_dict) else dict(state)

            # Retrieve the per-ticker slice.  Phase 7.6 Task 17 changes
            # smart_money_data to store SmartMoneyRaw Pydantic model instances
            # rather than plain dicts.  All other analysts continue to use plain
            # dicts.  Normalise to a dict here so every extractor always receives
            # a plain dict regardless of the upstream storage format.
            raw_slice = data.get(ticker, {})
            if hasattr(raw_slice, "model_dump"):
                raw_slice = raw_slice.model_dump()

            features: dict[str, float] = extractor(
                raw_slice, ticker, as_of=recorded_at,
                state=state_snapshot,
            )

            raw_v = verdicts_by_ticker.get(ticker)

            if raw_v is None:
                # LLM omitted this ticker — synthesise a safe no-data record
                # so downstream consumers always receive one record per ticker.
                verdict = AnalystVerdict(
                    lean="neutral",
                    magnitude=0.0,
                    confidence=0.0,
                    rationale="no verdict from LLM",
                    key_factors=[],
                    is_no_data=True,
                )
            else:
                # Validate the LLM's output dict against the strict schema.
                verdict = AnalystVerdict.model_validate(raw_v)

            ev = AnalystEvidence(
                analyst=analyst,
                ticker=ticker,
                tick_id=tick_id,
                recorded_at=recorded_at,
                verdict=verdict,
                features=features,
                feature_warnings=[],  # extractors do not yet expose warnings
            )
            evidence_list.append(ev.model_dump(mode="json"))

        # Write the evidence list; no legacy *_signals key is touched.
        state[f"{analyst}_evidence"] = evidence_list
        return None

    return _callback


# ---------------------------------------------------------------------------
# Callback chain helpers — shared across all LLM analyst modules.
# ---------------------------------------------------------------------------

def _chain_before(*callbacks: Callable | None) -> Callable | None:
    """Run before-model callbacks in order; first non-None return short-circuits.

    If no callbacks are provided, or all are ``None``, returns ``None``
    (no-op for that slot).

    Parameters
    ----------
    *callbacks:
        Zero or more before-model callback functions (or ``None`` entries).

    Returns
    -------
    Callable | None
        A chained callback, or ``None`` if the chain is empty.
    """
    active = [c for c in callbacks if c is not None]
    if not active:
        return None

    # ADK invokes before-model callbacks with keyword arguments
    # (``callback_context=...``, ``llm_request=...``), so the outer wrapper
    # must declare the same parameter names — positional names like ``ctx``
    # cause ``TypeError: unexpected keyword argument 'callback_context'``.
    def _chained(callback_context, llm_request):
        """Invoke each before-model callback; stop and return on first non-None result."""
        for cb in active:
            result = cb(callback_context, llm_request)
            if result is not None:
                return result
        return None

    return _chained


def _chain_after(*callbacks: Callable | None) -> Callable | None:
    """Run after-model callbacks in order; all are invoked unconditionally.

    Parameters
    ----------
    *callbacks:
        Zero or more after-model callback functions (or ``None`` entries).

    Returns
    -------
    Callable | None
        A chained callback, or ``None`` if the chain is empty.
    """
    active = [c for c in callbacks if c is not None]
    if not active:
        return None

    # Match ADK's keyword-argument call convention (see _chain_before).
    def _chained(callback_context, llm_response):
        """Invoke every after-model callback regardless of their return values."""
        for cb in active:
            cb(callback_context, llm_response)
        return None

    return _chained

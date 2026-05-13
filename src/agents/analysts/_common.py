"""Shared analyst base and callback utilities.

D3 removes the dual-emit pattern (``make_dual_emit_callback``) and the
exhaustive-validator helper in favour of the simpler ``make_evidence_callback``
that reads LLM-emitted verdicts directly from state and writes fully-formed
``AnalystEvidence`` records.  The legacy ``AnalystSignal`` Pydantic class is
also removed — the four per-analyst ``schema.py`` subclasses are deleted
alongside it (see D3 option-a).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from contract.evidence import AnalystEvidence, AnalystName, AnalystVerdict


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
       ``extractor(state["{analyst}_data"][ticker], ticker)`` to obtain the
       deterministic feature vector.  If the LLM omitted a verdict for a
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
        recorded_at = datetime.now(tz=UTC)

        # Per-ticker raw data dict keyed by ticker symbol.
        data: dict = state.get(f"{analyst}_data", {}) or {}

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
            features: dict[str, float] = extractor(data.get(ticker, {}), ticker)

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

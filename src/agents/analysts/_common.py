"""Shared analyst base and callback utilities."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from contract.evidence import AnalystEvidence, AnalystName, AnalystVerdict


class AnalystSignal(BaseModel):
    ticker: str
    direction: str  # "bullish" | "bearish" | "neutral"
    confidence: float = Field(ge=0.0, le=1.0)
    key_factors: list[str] = Field(default_factory=list, max_length=3)


def make_exhaustive_validator(
    signals_key: str,
    tickers_key: str = "tickers",
):
    """Return an after_agent_callback that re-prompts if any watchlist tickers are missing."""

    def _validator(callback_context: CallbackContext) -> genai_types.Content | None:
        state = callback_context.state
        signals = state.get(signals_key, [])
        tickers = state.get(tickers_key, [])
        if not tickers:
            return None
        emitted = {
            (s["ticker"] if isinstance(s, dict) else s.ticker)
            for s in signals
        }
        missing = [t for t in tickers if t not in emitted]
        if missing:
            return genai_types.Content(
                parts=[genai_types.Part(
                    text=f"You missed these tickers: {missing}. "
                         f"Please emit a signal for every watchlist ticker."
                )],
                role="user",
            )
        return None

    return _validator


# ── Dual-emit (legacy AnalystSignal + new AnalystEvidence) ────────────────────

def make_dual_emit_callback(
    analyst: AnalystName,
    signals_key: str,
    data_key: str,
    evidence_key: str,
    extractor: Callable[[Any, str], dict[str, float]],
):
    """Return an ``after_agent_callback`` that writes both legacy signals and new evidence.

    The callback does three things in order:

    1. Validates exhaustiveness — re-prompts the LLM if any watchlist ticker is
       missing from ``state[signals_key]``. No evidence is written on re-prompt.
    2. For each legacy ``AnalystSignal`` in ``state[signals_key]``, calls
       ``extractor(state[data_key][ticker], ticker)`` to obtain the deterministic
       feature vector, then constructs a full ``AnalystEvidence`` record.
    3. Writes the evidence list to ``state[evidence_key]``.

    The legacy ``state[signals_key]`` is left untouched so that existing downstream
    consumers (``attribution_writer``, ``memory_writer``) continue to work without
    modification. Plan C will start reading ``state[evidence_key]`` in the strategist;
    Plan D will drop the legacy path entirely.

    Translation rules (``AnalystSignal`` → ``AnalystVerdict``):
      - ``direction``    → ``lean``         (1:1)
      - ``confidence``   → ``confidence``   (1:1)
      - ``confidence``   → ``magnitude``    (placeholder — Plan D will re-prompt for a
                                             real magnitude value)
      - ``key_factors``  → ``key_factors``  (kept as a structured list for the future
                                             knowledge-base lookup primitive)
      - ``key_factors``  → ``rationale``    (joined into a string for prompt readability,
                                             truncated to 160 chars)
      - extractor's ``is_no_data`` feature  → ``verdict.is_no_data`` (signals the
                                              digest aggregator to drop this verdict)

    Args:
        analyst:      The ``AnalystName`` literal identifying which analyst this is.
        signals_key:  State key holding the list of ``AnalystSignal`` dicts.
        data_key:     State key holding the per-ticker raw data dict.
        evidence_key: State key to write the resulting ``AnalystEvidence`` list to.
        extractor:    Callable ``(raw_ticker_data, ticker) -> {feature: value}`` that
                      computes the deterministic feature vector for one ticker.

    Returns:
        A callback function compatible with ADK's ``after_agent_callback`` protocol.
    """

    # Reuse the existing exhaustiveness check — no duplication needed.
    exhaustive = make_exhaustive_validator(signals_key)

    def _callback(callback_context: CallbackContext) -> genai_types.Content | None:
        # 1) Exhaustiveness check first — bail early if the LLM missed tickers.
        out = exhaustive(callback_context)
        if out is not None:
            return out

        # 2) Build the evidence list from the validated signal set.
        state = callback_context.state
        signals_raw = state.get(signals_key, []) or []
        per_ticker_data = state.get(data_key, {}) or {}
        tick_id = state.get("tick_id", "unknown")

        # Capture a single timestamp for the whole batch so all evidence records
        # for this tick are aligned (avoids microsecond skew from per-record calls).
        recorded_at = datetime.now(tz=UTC)

        evidence_list: list[dict] = []

        for sig in signals_raw:
            # Accept either a plain dict (typical after JSON round-trip through ADK
            # session state) or a live Pydantic model instance.
            sig_dict = sig if isinstance(sig, dict) else sig.model_dump()
            ticker = sig_dict["ticker"]

            # Pass only the per-ticker slice to the extractor — not the full dict.
            features = extractor(per_ticker_data.get(ticker, {}), ticker)

            key_factors = list(sig_dict.get("key_factors", []) or [])

            # Build a human-readable rationale string from the structured factor list.
            # Truncate to the 160-char field limit enforced by AnalystVerdict.
            rationale = " | ".join(key_factors)
            if not rationale:
                # Fall back to a minimal description when the LLM emitted no factors.
                rationale = f"{analyst} {sig_dict['direction']}"
            rationale = rationale[:160]

            confidence = float(sig_dict["confidence"])

            # Map the extractor's boolean `is_no_data` feature (encoded as 1.0) to the
            # verdict flag so the digest aggregator can exclude this verdict from voting.
            is_no_data = bool(features.get("is_no_data", 0.0) >= 1.0)

            evidence = AnalystEvidence(
                ticker=ticker,
                analyst=analyst,
                tick_id=tick_id,
                recorded_at=recorded_at,
                features=features,
                feature_warnings=[],
                verdict=AnalystVerdict(
                    lean=sig_dict["direction"],
                    magnitude=confidence,
                    confidence=confidence,
                    rationale=rationale,
                    # Slice to the 8-item max defined on AnalystVerdict.
                    key_factors=key_factors[:8],
                    is_no_data=is_no_data,
                ),
            )
            evidence_list.append(evidence.model_dump(mode="json"))

        # 3) Write the evidence alongside the existing legacy signals.
        state[evidence_key] = evidence_list
        return None

    return _callback

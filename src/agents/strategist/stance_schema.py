"""TickerStance — the strategist's per-ticker decision substrate.

The strategist emits one ``TickerStance`` per watchlist ticker on every tick.
Downstream derivation helpers (``derive_legacy_fields``) read a list of stances
and produce the flat ``target_weights``, ``new_positions``, ``close_reasons``,
and ``trim_reasons`` fields that the executor already expects — preserving
backwards compatibility while giving the strategist a richer internal model.

The stance is *not* a trade instruction. It expresses the strategist's desired
portfolio position and the reasoning behind it. The executor translates that
desire into actual orders.

Consumers:
- ``derive_legacy_fields`` (C4) — flattens stances into the legacy output shape
- ``StrategistDecision`` (C7) — embeds the stance list in the decision payload
- The after-callback (C9) — validates stances before persisting
- ``TickerStanceRow`` (C10) — persists each stance to the database
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TickerStance(BaseModel):
    """One stance per watchlist ticker per strategist tick.

    Args:
        ticker: The stock ticker symbol (e.g. ``"AAPL"``).
        preferred_weight: Target portfolio weight in ``[0.0, 1.0]``.
            0.0 signals a full close; values above the risk gate's
            single-position cap will be clamped downstream.
        conviction: The strategist's own confidence in this stance,
            in ``[0.0, 1.0]``. Distinct from analyst ``confidence`` —
            conviction is the *synthesised* view after weighing all
            analyst signals, whereas analyst confidence is each analyst's
            self-reported certainty about their individual signal.
        rationale: A short human-readable justification for the stance
            (max 140 chars — intentionally brief; full reasoning lives in
            the LLM's chain-of-thought, not the schema).
        horizon: Optional investment horizon. ``None`` means the
            strategist did not specify one.
        target_price: Optional price target (fundamental upside anchor).
        stop_price: Optional stop-loss level. The risk gate may enforce
            this independently, but the strategist can express a view here.
        catalyst: Optional short description of the expected near-term
            catalyst that underpins the stance (max 80 chars).
        close_reason: Why the position is being closed (``preferred_weight
            == 0.0``). Distinct from ``trim_reason`` — a close exits the
            position entirely, whereas a trim just reduces it.
        trim_reason: Why the position size is being reduced but not zeroed.
    """

    ticker: str

    # Target portfolio weight — bounded fraction of total portfolio value.
    preferred_weight: float = Field(ge=0.0, le=1.0)

    # Synthesised conviction after weighing all analyst signals.
    conviction: float = Field(ge=0.0, le=1.0)

    # Brief justification; kept short to encourage clear thinking.
    rationale: str = Field(max_length=140)

    # Optional lifecycle / context fields
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=80)

    # Reason fields — only one should be set per stance, depending on action.
    # close_reason: full exit (preferred_weight == 0.0).
    # trim_reason:  partial reduction (preferred_weight > 0.0 but lower than current).
    close_reason: str | None = Field(default=None, max_length=120)
    trim_reason: str | None = Field(default=None, max_length=120)

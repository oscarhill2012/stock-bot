"""Smart-money analyst output schema."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents.analysts._common import AnalystSignal


class SmartMoneySignal(AnalystSignal):
    """Signal derived from insider filings, congressional trades, and SC 13D/G holders.

    Extends ``AnalystSignal`` so the dual-emit callback can translate it into
    ``AnalystEvidence`` uniformly across analysts. Smart-money keeps its own
    extras — ``conviction``, ``insiders``, ``politicians``, ``total_dollar_value``
    — alongside the inherited ``direction`` / ``confidence`` / ``key_factors``
    fields so existing downstream consumers (strategist prompt, attribution
    writer, memory writer) continue to work without modification.

    ``direction`` widens from the inherited string field but in practice the
    smart-money prompt only emits ``bullish``, ``bearish``, or ``neutral`` —
    ``neutral`` is now valid since the analyst emits per watchlist ticker
    (with ``neutral`` + ``confidence=0.0`` when no activity is observed).
    """

    # Conviction is optional because neutral / no-activity emissions don't
    # carry a meaningful conviction level. The strategist filters on
    # ``conviction == 'high'`` so a missing/null value safely de-prioritises
    # the ticker without crashing template rendering.
    conviction: Literal["low", "high"] | None = None
    insiders: list[str] = Field(default_factory=list)       # insider names involved
    politicians: list[str] = Field(default_factory=list)    # politician names involved
    total_dollar_value: float = 0.0                         # USD sum of reported transactions

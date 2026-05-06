"""Shared state schemas — TickState built incrementally across phases."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── constants ────────────────────────────────────────────────────────
MIN_HELD_WEIGHT: float = 0.001
MAX_POSITION_WEIGHT: float = 0.20
CASH_FLOOR_WEIGHT: float = 0.10
MAX_DELTA_PER_TICKER: float = 0.01
MAX_TOTAL_TURNOVER: float = 0.30
ORDER_EPSILON: float = 1e-6


# ── orders + clamp telemetry ─────────────────────────────────────────
class Order(BaseModel):
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: float
    est_price: float


class ClampRecord(BaseModel):
    rule: Literal[
        "max_position", "max_delta", "cash_floor", "max_turnover", "no_short"
    ]
    ticker: str | None
    before: float
    after: float


class Execution(BaseModel):
    order: Order
    status: Literal["filled", "rejected", "partial"]
    actual_price: float | None = None
    actual_quantity: float | None = None
    slippage_bps: float | None = None
    broker_order_id: str | None = None
    error: str | None = None


# ── TickState ─────────────────────────────────────────────────────────
from typing import Any


class TickState(BaseModel):
    """Complete shared state schema. Agents read/write session.state[key]."""

    # Seeded at tick start
    tick_id: str = ""
    tickers: list[str] = Field(default_factory=list)

    # Written by analyst before_callbacks
    technical_data: dict[str, Any] = Field(default_factory=dict)
    fundamental_data: dict[str, Any] = Field(default_factory=dict)
    sentiment_data: dict[str, Any] = Field(default_factory=dict)
    smart_money_data: dict[str, Any] | None = None

    # Written by analyst LLMs
    technical_signals: list[Any] = Field(default_factory=list)
    fundamental_signals: list[Any] = Field(default_factory=list)
    sentiment_signals: list[Any] = Field(default_factory=list)
    smart_money_signals: list[Any] = Field(default_factory=list)

    # Persistent across ticks
    memory_buffer: list[Any] = Field(default_factory=list)
    day_digest: str = ""
    thesis: str = ""
    positions: dict[str, Any] = Field(default_factory=dict)
    last_executed_tick_id: str | None = None

    # Written by strategist
    strategist_decision: Any = None

    # Written by risk gate
    final_orders: list[Any] = Field(default_factory=list)
    risk_clamps_applied: list[Any] = Field(default_factory=list)

    # Written by executor
    executions: list[Any] = Field(default_factory=list)

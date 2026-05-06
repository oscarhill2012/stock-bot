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

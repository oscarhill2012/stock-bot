"""Shared state schemas — TickState built incrementally across pipeline phases."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Risk-gate constants ───────────────────────────────────────────────────────
MIN_HELD_WEIGHT: float    = 0.001   # position is considered "open" above this threshold
MAX_POSITION_WEIGHT: float = 0.20   # single-ticker concentration cap
CASH_FLOOR_WEIGHT: float   = 0.10   # minimum cash reserve fraction
MAX_DELTA_PER_TICKER: float = 0.01  # maximum weight change per tick per ticker
MAX_TOTAL_TURNOVER: float  = 0.30   # maximum total portfolio turnover per tick
ORDER_EPSILON: float       = 1e-6   # weight change below this is ignored (no order generated)


# ── Orders + clamp telemetry ──────────────────────────────────────────────────

class Order(BaseModel):
    """A trade instruction produced by the risk gate for the executor."""

    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: float     # shares to trade
    est_price: float    # price used to size the order (may differ from fill price)


class ClampRecord(BaseModel):
    """Telemetry for one constraint application — logged for analysis."""

    rule: Literal[
        "max_position", "max_delta", "cash_floor", "max_turnover", "no_short"
    ]
    ticker: str | None
    before: float   # weight before the clamp
    after: float    # weight after the clamp


class Execution(BaseModel):
    """Result of submitting one Order to the broker."""

    order: Order
    status: Literal["filled", "rejected", "partial"]
    actual_price: float | None    = None
    actual_quantity: float | None = None
    slippage_bps: float | None    = None  # basis-points slippage vs est_price
    broker_order_id: str | None   = None
    error: str | None             = None


# ── TickState ─────────────────────────────────────────────────────────────────

class TickState(BaseModel):
    """Complete shared state schema. Agents read/write session.state[key].

    Fields are grouped by which pipeline stage writes them.
    """

    # Seeded at tick start by the entrypoint.
    tick_id: str = ""
    tickers: list[str] = Field(default_factory=list)

    # Written by analyst before_callbacks (raw data fetched from providers).
    # NB: at runtime the state keys carry the ``temp:`` prefix
    # (``temp:technical_data`` etc. — A2.6 rename) so ADK strips them at the
    # invocation boundary.  These Pydantic field names are the Python
    # identifiers used before serialisation; they are NOT the live session-
    # state keys and must NOT be renamed here.
    technical_data: dict[str, Any]      = Field(default_factory=dict)
    fundamental_data: dict[str, Any]    = Field(default_factory=dict)
    news_data: dict[str, Any]           = Field(default_factory=dict)  # renamed from sentiment_data (Task 6)
    social_data: dict[str, Any]         = Field(default_factory=dict)  # added Task 7
    smart_money_data: dict[str, Any] | None = None

    # Persistent across ticks (loaded from and saved to the ADK session store).
    memory_buffer: list[Any]  = Field(default_factory=list)
    day_digest: str           = ""
    thesis: str               = ""
    positions: dict[str, Any] = Field(default_factory=dict)
    last_executed_tick_id: str | None = None

    # Written by the Strategist LlmAgent.
    strategist_decision: Any = None

    # Written by the RiskGate.
    final_orders: list[Any]        = Field(default_factory=list)
    risk_clamps_applied: list[Any] = Field(default_factory=list)

    # Written by the Executor.
    executions: list[Any] = Field(default_factory=list)

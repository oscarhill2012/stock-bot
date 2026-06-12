"""Shared state schemas (orders, executions, clamp telemetry) and risk-gate constants."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ── Risk-gate constants ───────────────────────────────────────────────────────
# Resolved from ``config/risk_gate.json`` at import time so every existing
# ``from orchestrator.state import …`` site keeps working unchanged.  See
# ``src/config/risk_gate.py`` for the field semantics.
from config.risk_gate import get_risk_gate_config as _get_risk_cfg

_risk = _get_risk_cfg()

MIN_HELD_WEIGHT:      float = _risk.min_held_weight        # open-position threshold
MAX_POSITION_WEIGHT:  float = _risk.max_position_weight    # single-ticker concentration cap
CASH_FLOOR_WEIGHT:    float = _risk.cash_floor_weight      # minimum cash reserve fraction
MAX_TOTAL_TURNOVER:   float = _risk.max_total_turnover     # maximum total portfolio turnover per tick
MAX_DELTA_PER_BUY:    float = _risk.max_delta_per_buy      # per-buy stance delta cap (single source of truth)
ORDER_EPSILON:        float = 1e-6                          # weight change below this is ignored (no order generated)


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
        "max_position", "cash_floor", "max_turnover", "no_short",
        "buy_delta_exceeded",
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


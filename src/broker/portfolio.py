"""Portfolio + Position dataclasses. No I/O — see broker.protocol."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Position(BaseModel):
    """A single open holding in the portfolio."""

    quantity: float       # number of shares held
    avg_cost: float       # volume-weighted average purchase price
    last_price: float     # most recent market price (updated by broker on each tick)

    @property
    def market_value(self) -> float:
        """Current market value of this position."""
        return self.quantity * self.last_price


class Portfolio(BaseModel):
    """Snapshot of the bot's full portfolio at a point in time."""

    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)

    @property
    def total_value(self) -> float:
        """Cash plus the market value of all open positions."""
        return self.cash + sum(p.market_value for p in self.positions.values())

    def current_weights(self) -> dict[str, float]:
        """Return each ticker's fraction of total portfolio value."""
        total = self.total_value
        if total == 0:
            return {}
        return {t: p.market_value / total for t, p in self.positions.items()}

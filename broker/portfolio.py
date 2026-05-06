"""Portfolio + Position dataclasses. No I/O — see broker.protocol."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Position(BaseModel):
    quantity: float
    avg_cost: float
    last_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price


class Portfolio(BaseModel):
    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)

    @property
    def total_value(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    def current_weights(self) -> dict[str, float]:
        total = self.total_value
        if total == 0:
            return {}
        return {t: p.market_value / total for t, p in self.positions.items()}

"""Broker Protocol — single abstraction for paper / live / fake."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from .portfolio import Portfolio


class Fill(BaseModel):
    id: str
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: float
    price: float


class BrokerRejection(Exception):
    """Broker refused the order. Logged but doesn't crash the tick."""


class Broker(Protocol):
    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill: ...

    async def position_size(self, ticker: str) -> float: ...

    async def get_portfolio(self) -> Portfolio: ...

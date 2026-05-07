"""Broker Protocol — single abstraction for paper / live / fake."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from .portfolio import Portfolio


class Fill(BaseModel):
    """Confirmed execution details returned by the broker after an order fills."""

    id: str                      # broker-assigned order ID
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: float              # shares actually filled (may differ from requested)
    price: float                 # execution price per share


class BrokerRejection(Exception):
    """Broker refused the order. Logged but doesn't crash the tick."""


class Broker(Protocol):
    """Structural interface satisfied by Trading212Broker, FakeBroker, and any future adapters."""

    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill: ...

    async def position_size(self, ticker: str) -> float: ...

    async def get_portfolio(self) -> Portfolio: ...

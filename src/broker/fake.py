"""Deterministic in-memory broker for tests."""
from __future__ import annotations

import itertools
from typing import Literal

from .portfolio import Portfolio, Position
from .protocol import BrokerRejection, Fill


class FakeBroker:
    """In-memory broker that simulates order execution without real market calls.

    Prices are injected via the constructor and can be updated mid-test via
    `set_price`. All state is mutable but deterministic — no randomness.
    """

    def __init__(self, starting_cash: float, prices: dict[str, float]):
        self._cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._prices = dict(prices)
        self._order_seq = itertools.count(1)

    def set_price(self, ticker: str, price: float) -> None:
        """Update the market price and mark existing position to market."""
        self._prices[ticker] = price
        if ticker in self._positions:
            self._positions[ticker].last_price = price

    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill:
        """Execute a market order against the current injected price.

        Raises BrokerRejection for unknown tickers, insufficient cash,
        or attempts to sell more than is held.
        """
        if ticker not in self._prices:
            raise BrokerRejection(f"no price for {ticker}")

        price = self._prices[ticker]
        notional = quantity * price

        if action == "BUY":
            if notional > self._cash:
                raise BrokerRejection(
                    f"insufficient cash: need {notional}, have {self._cash}"
                )
            self._cash -= notional

            existing = self._positions.get(ticker)
            if existing:
                # Blend the new purchase into the existing position (VWAP cost basis).
                new_qty = existing.quantity + quantity
                new_cost = (existing.avg_cost * existing.quantity + notional) / new_qty
                self._positions[ticker] = Position(
                    quantity=new_qty, avg_cost=new_cost, last_price=price
                )
            else:
                self._positions[ticker] = Position(
                    quantity=quantity, avg_cost=price, last_price=price
                )

        else:  # SELL
            existing = self._positions.get(ticker)
            if existing is None or existing.quantity < quantity:
                held = existing.quantity if existing else 0
                raise BrokerRejection(f"sell {quantity} > held {held} of {ticker}")

            self._cash += notional
            new_qty = existing.quantity - quantity
            if new_qty == 0:
                del self._positions[ticker]
            else:
                # Partial sell — keep cost basis unchanged.
                self._positions[ticker] = Position(
                    quantity=new_qty, avg_cost=existing.avg_cost, last_price=price
                )

        return Fill(
            id=f"fake-{next(self._order_seq)}",
            ticker=ticker,
            action=action,
            quantity=quantity,
            price=price,
        )

    async def position_size(self, ticker: str) -> float:
        """Return shares held for `ticker`, or 0 if not in portfolio."""
        return self._positions[ticker].quantity if ticker in self._positions else 0.0

    async def get_portfolio(self) -> Portfolio:
        """Return a snapshot of current cash and positions."""
        return Portfolio(cash=self._cash, positions=dict(self._positions))

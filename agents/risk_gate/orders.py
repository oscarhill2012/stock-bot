"""Translate post-clamp target weights into broker Orders."""
from __future__ import annotations

from broker import Portfolio
from orchestrator.state import ORDER_EPSILON, Order


def weights_to_orders(
    target: dict[str, float],
    portfolio: Portfolio,
    prices: dict[str, float],
) -> list[Order]:
    total = portfolio.total_value
    current = portfolio.current_weights()
    orders: list[Order] = []
    for ticker, new_w in target.items():
        old_w = current.get(ticker, 0.0)
        delta_w = new_w - old_w
        if abs(delta_w) < ORDER_EPSILON:
            continue
        if ticker not in prices:
            raise ValueError(f"no price for {ticker}")
        notional = abs(delta_w) * total
        qty = notional / prices[ticker]
        action = "BUY" if delta_w > 0 else "SELL"
        orders.append(
            Order(ticker=ticker, action=action, quantity=qty, est_price=prices[ticker])
        )
    return orders

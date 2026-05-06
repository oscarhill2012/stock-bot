import pytest

from agents.risk_gate.orders import weights_to_orders
from broker import Portfolio, Position


def test_buy_order_when_target_above_current():
    portfolio = Portfolio(cash=10_000.0, positions={})
    target = {"AAPL": 0.10}    # 10% of $10k = $1000
    prices = {"AAPL": 200.0}
    orders = weights_to_orders(target, portfolio, prices)
    assert len(orders) == 1
    assert orders[0].ticker == "AAPL"
    assert orders[0].action == "BUY"
    assert orders[0].quantity == pytest.approx(5.0)   # $1000 / $200
    assert orders[0].est_price == 200.0


def test_sell_order_when_target_below_current():
    portfolio = Portfolio(
        cash=8_000.0,
        positions={"AAPL": Position(quantity=10, avg_cost=200.0, last_price=200.0)},
    )                                                  # total = 10k, AAPL @ 20%
    target = {"AAPL": 0.10}                            # halve it
    prices = {"AAPL": 200.0}
    orders = weights_to_orders(target, portfolio, prices)
    assert len(orders) == 1
    assert orders[0].action == "SELL"
    assert orders[0].quantity == pytest.approx(5.0)


def test_no_order_when_delta_below_epsilon():
    portfolio = Portfolio(
        cash=8_000.0,
        positions={"AAPL": Position(quantity=10, avg_cost=200.0, last_price=200.0)},
    )
    target = {"AAPL": 0.20}                            # already at target
    prices = {"AAPL": 200.0}
    orders = weights_to_orders(target, portfolio, prices)
    assert orders == []


def test_orders_for_multiple_tickers():
    portfolio = Portfolio(cash=10_000.0, positions={})
    target = {"AAPL": 0.10, "MSFT": 0.05}
    prices = {"AAPL": 200.0, "MSFT": 100.0}
    orders = weights_to_orders(target, portfolio, prices)
    tickers = {o.ticker for o in orders}
    assert tickers == {"AAPL", "MSFT"}

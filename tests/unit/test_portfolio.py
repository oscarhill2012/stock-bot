import pytest

from broker.portfolio import Portfolio, Position


def test_total_value_includes_cash_and_positions():
    p = Portfolio(
        cash=1000.0,
        positions={"AAPL": Position(quantity=10, avg_cost=150.0, last_price=200.0)},
    )
    assert p.total_value == 1000.0 + 10 * 200.0


def test_current_weights_sum_to_one_minus_cash_ratio():
    p = Portfolio(
        cash=200.0,
        positions={
            "AAPL": Position(quantity=10, avg_cost=150.0, last_price=200.0),  # $2000
            "MSFT": Position(quantity=5, avg_cost=300.0, last_price=400.0),    # $2000
        },
    )
    weights = p.current_weights()
    # total = 200 + 2000 + 2000 = 4200; AAPL = 2000/4200, MSFT = 2000/4200
    assert weights["AAPL"] == pytest.approx(2000 / 4200)
    assert weights["MSFT"] == pytest.approx(2000 / 4200)
    assert sum(weights.values()) == pytest.approx((4200 - 200) / 4200)


def test_empty_portfolio_returns_empty_weights():
    p = Portfolio(cash=1000.0, positions={})
    assert p.current_weights() == {}
    assert p.total_value == 1000.0

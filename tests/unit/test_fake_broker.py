import pytest

from broker.fake import FakeBroker
from broker.protocol import BrokerRejection


@pytest.mark.asyncio
async def test_buy_creates_position():
    b = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    fill = await b.submit_market("AAPL", "BUY", 10)
    assert fill.price == 200.0
    assert fill.quantity == 10
    p = await b.get_portfolio()
    assert p.cash == 10_000 - 2000
    assert p.positions["AAPL"].quantity == 10


@pytest.mark.asyncio
async def test_sell_reduces_position():
    b = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    await b.submit_market("AAPL", "BUY", 10)
    await b.submit_market("AAPL", "SELL", 4)
    p = await b.get_portfolio()
    assert p.positions["AAPL"].quantity == 6
    assert p.cash == pytest.approx(10_000 - 2000 + 800)


@pytest.mark.asyncio
async def test_buy_with_insufficient_cash_raises():
    b = FakeBroker(starting_cash=100.0, prices={"AAPL": 200.0})
    with pytest.raises(BrokerRejection):
        await b.submit_market("AAPL", "BUY", 10)


@pytest.mark.asyncio
async def test_sell_more_than_held_raises():
    b = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    with pytest.raises(BrokerRejection):
        await b.submit_market("AAPL", "SELL", 5)

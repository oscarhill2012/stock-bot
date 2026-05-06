"""Verify Trading212Broker builds requests correctly. No network calls."""
from unittest.mock import AsyncMock

import pytest

from broker.trading212 import Trading212Broker


@pytest.mark.asyncio
async def test_buy_constructs_correct_request():
    client = AsyncMock()
    client.post.return_value.json = AsyncMock(return_value={
        "id": "abc-123",
        "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": 1.5,
        "filledPrice": 200.0,
    })
    client.post.return_value.raise_for_status = lambda: None

    b = Trading212Broker(mode="paper", api_key="K", http_client=client,
                         instrument_map={"AAPL": "AAPL_US_EQ"})
    fill = await b.submit_market("AAPL", "BUY", 1.5)

    client.post.assert_called_once()
    call = client.post.call_args
    assert call.kwargs["json"] == {
        "instrumentCode": "AAPL_US_EQ", "quantity": 1.5
    }
    assert "/api/v0/equity/orders/market" in call.args[0]
    assert call.kwargs["headers"]["Authorization"] == "K"
    assert fill.price == 200.0
    assert fill.quantity == 1.5


@pytest.mark.asyncio
async def test_sell_uses_negative_quantity():
    client = AsyncMock()
    client.post.return_value.json = AsyncMock(return_value={
        "id": "abc-2", "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": -1.0, "filledPrice": 199.0,
    })
    client.post.return_value.raise_for_status = lambda: None

    b = Trading212Broker(mode="paper", api_key="K", http_client=client,
                         instrument_map={"AAPL": "AAPL_US_EQ"})
    await b.submit_market("AAPL", "SELL", 1.0)

    body = client.post.call_args.kwargs["json"]
    assert body["quantity"] == -1.0  # Trading 212 uses sign for direction


@pytest.mark.asyncio
async def test_paper_uses_demo_base_url():
    b = Trading212Broker(mode="paper", api_key="K",
                         http_client=AsyncMock(), instrument_map={})
    assert "demo" in b.base_url


@pytest.mark.asyncio
async def test_live_uses_live_base_url():
    b = Trading212Broker(mode="live", api_key="K",
                         http_client=AsyncMock(), instrument_map={})
    assert "demo" not in b.base_url
    assert "trading212" in b.base_url

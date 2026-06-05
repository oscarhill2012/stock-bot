"""Verify Trading212Broker builds requests correctly. No network calls."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from broker.trading212 import Trading212Broker


@pytest.mark.asyncio
async def test_buy_constructs_correct_request():
    """Verify a BUY order sends the correct URL, payload, and auth header.

    Uses MagicMock (not AsyncMock) for .json() to reflect real httpx behaviour
    where Response.json() is synchronous. Any await would raise TypeError.
    """
    response = MagicMock()
    response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value={
        "id": "abc-123",
        "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": 1.5,
        "filledPrice": 200.0,
    })

    client = MagicMock()
    client.post = AsyncMock(return_value=response)  # only the HTTP verb is async

    b = Trading212Broker(
        mode="paper", api_key="K",
        http_client=client, instrument_map={"AAPL": "AAPL_US_EQ"},
    )
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
    """Verify a SELL order sends a negative quantity (Trading 212 sign convention).

    Uses MagicMock for .json() to match real httpx synchronous contract.
    """
    response = MagicMock()
    response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value={
        "id": "abc-2",
        "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": -1.0,
        "filledPrice": 199.0,
    })

    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    b = Trading212Broker(
        mode="paper", api_key="K",
        http_client=client, instrument_map={"AAPL": "AAPL_US_EQ"},
    )
    await b.submit_market("AAPL", "SELL", 1.0)

    body = client.post.call_args.kwargs["json"]
    assert body["quantity"] == -1.0  # Trading 212 uses sign for direction


@pytest.mark.asyncio
async def test_paper_uses_demo_base_url():
    """Verify paper mode sets the demo base URL."""
    b = Trading212Broker(mode="paper", api_key="K",
                         http_client=AsyncMock(), instrument_map={})
    assert "demo" in b.base_url


@pytest.mark.asyncio
async def test_live_uses_live_base_url():
    """Verify live mode sets the live (non-demo) base URL."""
    b = Trading212Broker(mode="live", api_key="K",
                         http_client=AsyncMock(), instrument_map={})
    assert "demo" not in b.base_url
    assert "trading212" in b.base_url


@pytest.mark.asyncio
async def test_submit_market_does_not_await_sync_json():
    """Real httpx returns a dict (sync) from .json(); awaiting it raises TypeError.

    Cementing-test fix: previous tests set ``client.post.return_value.json =
    AsyncMock(...)`` which papered over the bug.  Use ``MagicMock`` here so
    ``.json()`` returns a plain dict, exactly like real httpx.
    """
    response = MagicMock()
    response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value={
        "id": "abc-123",
        "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": 1.5,
        "filledPrice": 200.0,
    })

    client = MagicMock()
    client.post = AsyncMock(return_value=response)  # only the HTTP verb is async

    b = Trading212Broker(
        mode="paper", api_key="K",
        http_client=client, instrument_map={"AAPL": "AAPL_US_EQ"},
    )
    fill = await b.submit_market("AAPL", "BUY", 1.5)

    assert fill.price == 200.0
    assert fill.quantity == 1.5

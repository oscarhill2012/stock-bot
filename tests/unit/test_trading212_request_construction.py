"""Verify Trading212Broker builds requests correctly. No network calls."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from broker.protocol import BrokerRejection
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
    """Cement that ``submit_market`` calls ``.json()`` as a plain sync callable.

    ``response.json`` is a ``MagicMock`` (not ``AsyncMock``), which mirrors real
    httpx behaviour where ``Response.json()`` is synchronous.  If the
    implementation re-introduced ``await resp.json()``, awaiting a
    ``MagicMock``'s return value would raise ``TypeError`` before the fill
    assertions are reached — that failure is the signal.

    The explicit ``assert_called_once()`` at the end additionally confirms that
    ``.json`` was invoked exactly once as a plain callable, not zero times (dead
    code) and not more than once (spurious extra reads).
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

    # Cement the no-await contract explicitly: .json must be invoked exactly
    # once as a plain synchronous callable.  If the implementation re-introduced
    # `await resp.json()`, awaiting a MagicMock's return value would raise
    # TypeError before we ever reach these assertions — that failure is the signal.
    response.json.assert_called_once()


@pytest.mark.asyncio
async def test_get_portfolio_raises_on_unknown_instrument_code():
    """T212 may return positions in instruments the local map does not know
    about (instrument map stale).  Silently dropping them shrinks the
    portfolio that concentration clamps + BUY->SELL bridge see, causing
    over-allocation.  The fix raises BrokerRejection listing the offenders.
    """
    cash_resp = MagicMock()
    cash_resp.raise_for_status = MagicMock(return_value=None)
    cash_resp.json = MagicMock(return_value={"free": 5_000.0})

    port_resp = MagicMock()
    port_resp.raise_for_status = MagicMock(return_value=None)
    port_resp.json = MagicMock(return_value=[
        {"ticker": "AAPL_US_EQ", "quantity": 1.0,
         "averagePrice": 100.0, "currentPrice": 110.0},
        # Unknown instrument code — not in the local instrument_map.
        {"ticker": "XYZ_US_EQ",  "quantity": 5.0,
         "averagePrice": 50.0,  "currentPrice": 55.0},
    ])

    client = MagicMock()
    client.get = AsyncMock(side_effect=[cash_resp, port_resp])

    b = Trading212Broker(
        mode="paper", api_key="K",
        http_client=client, instrument_map={"AAPL": "AAPL_US_EQ"},
    )

    with pytest.raises(BrokerRejection, match="XYZ_US_EQ"):
        await b.get_portfolio()

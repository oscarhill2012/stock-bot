"""Trading 212 REST client. Paper (demo) and live mode behind a flag."""
from __future__ import annotations

from typing import Literal

import httpx

from .portfolio import Portfolio, Position
from .protocol import BrokerRejection, Fill

PAPER_BASE = "https://demo.trading212.com"
LIVE_BASE  = "https://live.trading212.com"


class Trading212Broker:
    """Async broker adapter wrapping the Trading 212 REST API.

    `instrument_map` maps ticker symbols (e.g. "AAPL") to Trading 212's
    internal instrument codes (e.g. "AAPL_US_EQ"). Build this map once at
    startup using the /instruments endpoint.
    """

    def __init__(
        self,
        *,
        mode: Literal["paper", "live"],
        api_key: str,
        http_client: httpx.AsyncClient,
        instrument_map: dict[str, str],
    ):
        self.mode = mode
        self.base_url = PAPER_BASE if mode == "paper" else LIVE_BASE
        self._api_key = api_key
        self._client = http_client
        self._instruments = dict(instrument_map)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._api_key, "Content-Type": "application/json"}

    def _instrument(self, ticker: str) -> str:
        """Look up Trading 212 instrument code; raise BrokerRejection if missing."""
        if ticker not in self._instruments:
            raise BrokerRejection(f"unknown instrument for {ticker}")
        return self._instruments[ticker]

    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill:
        """Submit a market order. Positive quantity = buy; negative = sell in T212's API."""
        signed_qty = quantity if action == "BUY" else -quantity
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v0/equity/orders/market",
                json={"instrumentCode": self._instrument(ticker), "quantity": signed_qty},
                headers=self._headers(),
            )
            resp.raise_for_status()
            # httpx.Response.json() is synchronous even on AsyncClient.  The previous
            # "await ... if callable(...)" hedge papered over an AsyncMock-shaped test
            # and would TypeError against real httpx.
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise BrokerRejection(f"HTTP {e.response.status_code}: {e.response.text}") from e

        return Fill(
            id=str(data["id"]),
            ticker=ticker,
            action=action,
            quantity=abs(float(data["filledQuantity"])),
            price=float(data["filledPrice"]),
        )

    async def position_size(self, ticker: str) -> float:
        """Return shares currently held for `ticker`, or 0 if not in portfolio."""
        resp = await self._client.get(
            f"{self.base_url}/api/v0/equity/portfolio",
            headers=self._headers(),
        )
        resp.raise_for_status()
        # Sync call — see submit_market for why .json() is not awaited.
        data = resp.json()

        code = self._instrument(ticker)
        for pos in data:
            if pos["ticker"] == code:
                return float(pos["quantity"])
        return 0.0

    async def get_portfolio(self) -> Portfolio:
        """Fetch cash balance and all open positions from T212."""
        acct = await self._client.get(
            f"{self.base_url}/api/v0/equity/account/cash",
            headers=self._headers(),
        )
        acct.raise_for_status()
        # Sync call — see submit_market for why .json() is not awaited.
        acct_data = acct.json()
        cash = float(acct_data["free"])

        port = await self._client.get(
            f"{self.base_url}/api/v0/equity/portfolio",
            headers=self._headers(),
        )
        port.raise_for_status()
        # Sync call — see submit_market for why .json() is not awaited.
        items = port.json()

        # Reverse the instrument map so we can convert T212 codes back to tickers.
        rev = {v: k for k, v in self._instruments.items()}

        # Detect unknown instrument codes up-front and raise so concentration
        # clamps + BUY->SELL bridge cannot operate on a silently-shrunken
        # portfolio.  A stale instrument_map is a deployment bug, not a
        # per-position degradation to be swallowed.
        unknown_codes = [it["ticker"] for it in items if it["ticker"] not in rev]
        if unknown_codes:
            raise BrokerRejection(
                f"Trading 212 returned positions for unknown instrument codes: "
                f"{sorted(unknown_codes)}. Refresh instrument_map at startup."
            )

        positions: dict[str, Position] = {}
        for it in items:
            code = it["ticker"]
            positions[rev[code]] = Position(
                quantity=float(it["quantity"]),
                avg_cost=float(it["averagePrice"]),
                last_price=float(it["currentPrice"]),
            )

        return Portfolio(cash=cash, positions=positions)

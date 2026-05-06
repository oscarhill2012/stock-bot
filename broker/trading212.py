"""Trading 212 REST client. Paper (demo) and live mode behind a flag."""
from __future__ import annotations

from typing import Literal

import httpx

from .portfolio import Portfolio, Position
from .protocol import BrokerRejection, Fill

PAPER_BASE = "https://demo.trading212.com"
LIVE_BASE = "https://live.trading212.com"


class Trading212Broker:
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
        if ticker not in self._instruments:
            raise BrokerRejection(f"unknown instrument for {ticker}")
        return self._instruments[ticker]

    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill:
        signed_qty = quantity if action == "BUY" else -quantity
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v0/equity/orders/market",
                json={"instrumentCode": self._instrument(ticker), "quantity": signed_qty},
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()
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
        resp = await self._client.get(
            f"{self.base_url}/api/v0/equity/portfolio",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()
        code = self._instrument(ticker)
        for pos in data:
            if pos["ticker"] == code:
                return float(pos["quantity"])
        return 0.0

    async def get_portfolio(self) -> Portfolio:
        acct = await self._client.get(
            f"{self.base_url}/api/v0/equity/account/cash",
            headers=self._headers(),
        )
        acct.raise_for_status()
        acct_data = await acct.json() if callable(getattr(acct, "json", None)) else acct.json()
        cash = float(acct_data["free"])

        port = await self._client.get(
            f"{self.base_url}/api/v0/equity/portfolio",
            headers=self._headers(),
        )
        port.raise_for_status()
        items = await port.json() if callable(getattr(port, "json", None)) else port.json()

        rev = {v: k for k, v in self._instruments.items()}
        positions: dict[str, Position] = {}
        for it in items:
            code = it["ticker"]
            if code not in rev:
                continue
            positions[rev[code]] = Position(
                quantity=float(it["quantity"]),
                avg_cost=float(it["averagePrice"]),
                last_price=float(it["currentPrice"]),
            )
        return Portfolio(cash=cash, positions=positions)

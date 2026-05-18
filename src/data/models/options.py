"""Option contract model — canonical payload for the ``options`` domain.

v1 shell caveat: the live provider (``src/data/providers/options/yfinance.py``)
is a stub that returns an empty list for all ``as_of`` values.  The real
yfinance wiring is deferred to a follow-up spec.  This model is defined now so
the ``options`` domain has a well-typed canonical shape in ``DOMAIN_SHAPES``
and the contract test can pass without special-casing.

Fields are drawn from the standard options-chain columns exposed by yfinance
(``yf.Ticker.option_chain(expiry)``).  All monetary values are in USD.
All nullable fields default to ``None`` to accommodate sparse data from
different option-chain providers.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OptionContract(BaseModel):
    """One row from an options chain — a single call or put contract.

    Parameters
    ----------
    ticker:
        Upper-cased underlying symbol (e.g. ``"AAPL"``).
    expiry:
        Expiration date of the contract.
    strike:
        Strike price in USD.
    option_type:
        ``"call"`` or ``"put"``.
    last_price:
        Most recent trade price of the contract.  ``None`` when unavailable.
    bid:
        Current best bid.  ``None`` when unavailable.
    ask:
        Current best ask.  ``None`` when unavailable.
    implied_volatility:
        Black–Scholes implied volatility (0–1 scale, e.g. 0.35 = 35%).
        ``None`` when the contract has not traded or IV cannot be computed.
    open_interest:
        Number of open contracts (outstanding positions).  ``None`` when
        unavailable.
    volume:
        Number of contracts traded in the current session.  ``None`` when zero
        or unavailable.
    in_the_money:
        ``True`` if the contract's intrinsic value is positive at fetch time.
        ``None`` when the underlying price is unavailable for comparison.
    contract_symbol:
        OCC-standard contract symbol string (e.g. ``"AAPL230317C00150000"``).
        ``None`` for providers that do not supply this identifier.
    """

    model_config = ConfigDict(extra="forbid")

    ticker: str
    expiry: date
    strike: float
    option_type: Literal["call", "put"]

    # Price fields — all optional since the shell returns nothing today.
    last_price: float | None = Field(default=None, description="Most recent trade price.")
    bid:        float | None = None
    ask:        float | None = None

    # Greeks / derived metrics.
    implied_volatility: float | None = None
    open_interest:      int   | None = None
    volume:             int   | None = None

    in_the_money:    bool | None = None
    contract_symbol: str  | None = None

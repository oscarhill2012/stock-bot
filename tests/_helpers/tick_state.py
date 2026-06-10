"""Shared factory for contract-compliant tick-state dicts.

See ``docs/contract-invariants.md`` §A for the authoritative key list.
"""
from __future__ import annotations

from datetime import datetime, timezone


def make_tick_state(
    *,
    watchlist: list[str],
    held: dict[str, float] | None = None,
    as_of: str | datetime | None = None,
    reference_prices: dict[str, float] | None = None,
    portfolio_cash: float = 10_000.0,
) -> dict[str, object]:
    """Build a contract-compliant tick-state dict for pipeline tests.

    See plan-11 §2.2 for the contract.
    """
    held = held or {}

    # Coerce as_of to ISO string (state-write boundary rule).
    if as_of is None:
        as_of_str = datetime.now(timezone.utc).isoformat()
    elif isinstance(as_of, datetime):
        as_of_str = as_of.isoformat()
    else:
        # already string
        as_of_str = as_of

    # Default reference_prices: stub 1.0 for every ticker we know about.
    if reference_prices is None:
        reference_prices = {t: 1.0 for t in set(watchlist) | set(held.keys())}

    # Build position rows in the canonical shape.
    positions = {
        ticker: {
            "ticker": ticker,
            "qty": qty,
            "avg_price": reference_prices.get(ticker, 1.0),
        }
        for ticker, qty in held.items()
    }

    portfolio = {
        "cash": portfolio_cash,
        "positions": positions,
    }

    return {
        "as_of": as_of_str,
        "watchlist": list(watchlist),
        "user:positions": positions,
        "temp:_positions": positions,  # in-tick bridge
        "reference_prices": reference_prices,
        "portfolio": portfolio,
        "temp:_trace": [],
        "temp:_decision_logger": [],
    }

"""Tests that ``as_of`` is forwarded to every wrapper + aggregator dispatch."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from data import (
    get_company_filings, get_insider_trades, get_public_figure_trades,
    get_social_sentiment, get_stock_news, get_stock_stats,
    get_notable_holders,
)


FIXED = datetime(2023, 3, 15, 9, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("fn,domain", [
    (get_stock_stats,           "stats"),
    (get_stock_news,            "news"),
    (get_social_sentiment,      "social_sentiment"),
    (get_insider_trades,        "insider_trades"),
    (get_public_figure_trades,  "politician_trades"),
    (get_notable_holders,       "notable_holders"),
    (get_company_filings,       "filings"),
])
async def test_wrapper_forwards_as_of(fn, domain) -> None:
    """Every wrapper threads ``as_of`` into the dispatch kwargs."""
    with patch("data.registry.dispatch", new=AsyncMock(return_value=None)) as m:
        await fn("AAPL", as_of=FIXED)

    assert m.await_args.kwargs.get("as_of") == FIXED

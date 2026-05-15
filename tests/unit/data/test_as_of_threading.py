"""Tests that ``as_of`` is forwarded to every wrapper + aggregator dispatch.

DEVIATION from plan: The plan's test referenced ``get_stock_stats`` and domain
``"stats"``, both of which were retired in Phase 5.  This test uses the actual
Phase-5 wrappers: ``get_price_history``, ``get_company_ratios``, and the five
remaining wrappers that map to the real DOMAINS frozenset.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from data import (
    get_company_filings,
    get_company_ratios,
    get_insider_trades,
    get_notable_holders,
    get_price_history,
    get_public_figure_trades,
    get_social_sentiment,
    get_stock_news,
)


FIXED = datetime(2023, 3, 15, 9, 30, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("fn,domain", [
    (get_price_history,         "price_history"),
    (get_company_ratios,        "company_ratios"),
    (get_stock_news,            "news"),
    (get_social_sentiment,      "social_sentiment"),
    (get_insider_trades,        "insider_trades"),
    (get_public_figure_trades,  "politician_trades"),
    (get_notable_holders,       "notable_holders"),
    (get_company_filings,       "filings"),
])
async def test_wrapper_forwards_as_of(fn, domain) -> None:
    """Every wrapper threads ``as_of`` into the dispatch kwargs.

    Note: the wrappers call ``_dispatch``, which is the ``dispatch`` name
    imported into ``data.__init__`` at module load time.  Patching
    ``data.registry.dispatch`` would not intercept those calls — we must
    patch the name as it exists in the ``data`` package namespace.
    """
    with patch("data._dispatch", new=AsyncMock(return_value=None)) as m:
        await fn("AAPL", as_of=FIXED)

    assert m.await_args.kwargs.get("as_of") == FIXED, (
        f"{fn.__name__} did not forward as_of into dispatch kwargs"
    )


@pytest.mark.asyncio
async def test_aggregator_forwards_as_of() -> None:
    """``get_stock_signal_bundle`` threads ``as_of`` into every dispatch call."""
    from data.aggregator import get_stock_signal_bundle

    with patch("data.aggregator.dispatch", new=AsyncMock(return_value=None)) as m:
        # Suppress StockSignalBundle construction — not the focus of this test.
        with patch("data.aggregator.StockSignalBundle") as sb:
            sb.return_value = object()
            await get_stock_signal_bundle("AAPL", as_of=FIXED)

    # Every dispatch call must carry as_of=FIXED.
    for call in m.await_args_list:
        assert call.kwargs.get("as_of") == FIXED, (
            f"dispatch call missing as_of: {call}"
        )

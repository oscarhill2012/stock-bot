"""Tests that each analyst fetch callback reads ``state['as_of']`` and forwards it."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


FIXED = datetime(2023, 3, 15, 9, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("module,patch_target", [
    ("agents.analysts.technical.fetch",   "agents.analysts.technical.fetch.get_stock_stats"),
    ("agents.analysts.news.fetch",        "agents.analysts.news.fetch.get_stock_news"),
    ("agents.analysts.social.fetch",      "agents.analysts.social.fetch.get_social_sentiment"),
])
async def test_callback_forwards_state_as_of(module, patch_target) -> None:
    """Each analyst's fetch callback passes ``as_of`` from state into its wrapper."""
    import importlib
    m = importlib.import_module(module)
    callback = next(
        v for k, v in vars(m).items() if k.endswith("_fetch_callback")
    )

    state = {"tickers": ["AAPL"], "as_of": FIXED}
    ctx   = SimpleNamespace(state=state)

    with patch(patch_target, new=AsyncMock(return_value=None)) as p:
        await callback(ctx)

    assert p.await_args.kwargs.get("as_of") == FIXED

"""Unit tests for fundamental_fetch_callback — Phase 5 triad (stats + filings + insider).

The callback must fetch all three data domains for every watchlist ticker and
write them into ``state["fundamental_data"][ticker]`` under the keys
``"stats"``, ``"filings"``, and ``"insider"``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from data.models import Form4Bundle


def _make_ctx(tickers: list[str]) -> MagicMock:
    """Construct a minimal ADK CallbackContext stub carrying the given tickers."""
    ctx = MagicMock()
    ctx.state = {"tickers": tickers}
    return ctx


@pytest.mark.asyncio
async def test_fundamental_fetch_pulls_three_domains(monkeypatch):
    """After Phase 5, fundamental fetch writes stats + filings + insider per ticker."""
    import agents.analysts.fundamental.fetch as fetch_mod

    bundle = Form4Bundle(trades=[], derivatives=[])

    monkeypatch.setattr(
        fetch_mod,
        "get_stock_stats",
        AsyncMock(return_value=MagicMock(**{"model_dump.return_value": {"pe_trailing": 25.0}})),
    )
    monkeypatch.setattr(
        fetch_mod,
        "get_company_filings",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        fetch_mod,
        "get_insider_trades",
        AsyncMock(return_value=bundle),
    )

    ctx = _make_ctx(["AAPL"])
    result = await fetch_mod.fundamental_fetch_callback(ctx)

    assert result is None

    fundata = ctx.state["fundamental_data"]["AAPL"]
    assert "stats" in fundata, "stats key missing from fundamental_data payload"
    assert "filings" in fundata, "filings key missing from fundamental_data payload"
    assert "insider" in fundata, "insider key missing from fundamental_data payload"


@pytest.mark.asyncio
async def test_fundamental_fetch_insider_is_form4bundle(monkeypatch):
    """The 'insider' value must be the raw Form4Bundle object, not a model_dump dict."""
    import agents.analysts.fundamental.fetch as fetch_mod

    bundle = Form4Bundle(trades=[], derivatives=[])

    monkeypatch.setattr(fetch_mod, "get_stock_stats", AsyncMock(return_value=None))
    monkeypatch.setattr(fetch_mod, "get_company_filings", AsyncMock(return_value=[]))
    monkeypatch.setattr(fetch_mod, "get_insider_trades", AsyncMock(return_value=bundle))

    ctx = _make_ctx(["MSFT"])
    await fetch_mod.fundamental_fetch_callback(ctx)

    insider_val = ctx.state["fundamental_data"]["MSFT"]["insider"]
    assert isinstance(insider_val, Form4Bundle), (
        f"Expected Form4Bundle, got {type(insider_val)}"
    )


@pytest.mark.asyncio
async def test_fundamental_fetch_partial_failure_does_not_break_other_domains(monkeypatch):
    """If one domain raises, the other two still populate their keys (partial failure tolerance)."""
    import agents.analysts.fundamental.fetch as fetch_mod

    monkeypatch.setattr(
        fetch_mod,
        "get_stock_stats",
        AsyncMock(side_effect=RuntimeError("stats unavailable")),
    )
    monkeypatch.setattr(fetch_mod, "get_company_filings", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        fetch_mod,
        "get_insider_trades",
        AsyncMock(return_value=Form4Bundle(trades=[], derivatives=[])),
    )

    ctx = _make_ctx(["TSLA"])
    await fetch_mod.fundamental_fetch_callback(ctx)

    fundata = ctx.state["fundamental_data"]["TSLA"]
    # stats failed — key should exist with a safe fallback (None or empty dict)
    assert "stats" in fundata
    # Other domains must still be populated despite the stats failure.
    assert "filings" in fundata
    assert "insider" in fundata


@pytest.mark.asyncio
async def test_fundamental_fetch_multiple_tickers(monkeypatch):
    """The callback iterates all tickers independently."""
    import agents.analysts.fundamental.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "get_stock_stats", AsyncMock(return_value=None))
    monkeypatch.setattr(fetch_mod, "get_company_filings", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        fetch_mod,
        "get_insider_trades",
        AsyncMock(return_value=Form4Bundle(trades=[], derivatives=[])),
    )

    ctx = _make_ctx(["AAPL", "GOOG"])
    await fetch_mod.fundamental_fetch_callback(ctx)

    assert "AAPL" in ctx.state["fundamental_data"]
    assert "GOOG" in ctx.state["fundamental_data"]

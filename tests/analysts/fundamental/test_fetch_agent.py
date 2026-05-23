"""Unit tests for FundamentalFetchAgent.

The fetch agent runs ONCE per tick, calls the three fundamental providers
(company_ratios, company_filings, insider_trades) for every watchlist ticker,
and yields exactly one state_delta event containing:

  - temp:fundamental_data — dict keyed by ticker (machine-readable triad)
  - temp:fundamental_context_<TICKER> — per-ticker formatted text block (one
    key per ticker; consumed by that ticker's LlmAgent via
    {fundamental_context})
  - temp:fundamental_context — aggregate joined block (all tickers), retained
    for trace/debug surfaces per Phase 9 spec §1
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.fundamental.fetch_agent import FundamentalFetchAgent
from data.models import Form4Bundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ratios_dict() -> dict:
    """Return a minimal ratios dict shaped like CompanyRatios.model_dump()."""
    return {
        "ticker": "AAPL",
        "pe_ratio": 28.5,
        "market_cap": 3_000_000_000_000,
        "beta": 1.2,
    }


def _make_filing_dict(ticker: str, form_type: str = "10-K") -> dict:
    """Return a minimal Filing.model_dump() dict."""
    return {
        "ticker": ticker,
        "form_type": form_type,
        "filed_at": "2026-01-15",
        "mda_excerpt": f"{ticker} delivered strong results this quarter.",
        "risk_factors_excerpt": f"Market risk for {ticker} remains elevated.",
    }


def _make_empty_bundle() -> Form4Bundle:
    """Return a Form4Bundle with no trades or derivatives."""
    return Form4Bundle(trades=[], derivatives=[])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_writes_per_ticker_context_keys():
    """One temp:fundamental_context_<TICKER> key is written per watchlist ticker."""

    tickers = ["AAPL", "MSFT"]

    aapl_ratios = {**_make_ratios_dict(), "ticker": "AAPL"}
    msft_ratios = {**_make_ratios_dict(), "ticker": "MSFT", "pe_ratio": 32.0}

    aapl_filings = [_make_filing_dict("AAPL")]
    msft_filings = [_make_filing_dict("MSFT", form_type="10-Q")]

    # Each ratios call returns a plain dict (already model_dump()-ed) so the
    # agent's ``hasattr(ratios_obj, "model_dump")`` branch is exercised.
    async def _mock_ratios(ticker, as_of=None):
        return aapl_ratios if ticker == "AAPL" else msft_ratios

    # Filings come back as plain dicts — the agent handles both dicts and models.
    async def _mock_filings(ticker, as_of=None, limit=3, include_excerpts=True):
        return aapl_filings if ticker == "AAPL" else msft_filings

    async def _mock_insider(ticker, lookback_days=30, as_of=None):
        return _make_empty_bundle()

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test",
        user_id="test",
        state={
            "tickers": tickers,
            "as_of": datetime(2026, 5, 21, 14, 0),
        },
        session_id="t1",
    )

    agent = FundamentalFetchAgent(name="FundamentalFetch")
    ctx = InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-1",
        agent=agent,
    )

    with (
        patch("agents.analysts.fundamental.fetch_agent.get_company_ratios", _mock_ratios),
        patch("agents.analysts.fundamental.fetch_agent.get_company_filings", _mock_filings),
        patch("agents.analysts.fundamental.fetch_agent.get_insider_trades", _mock_insider),
    ):
        events = [ev async for ev in agent.run_async(ctx)]

    assert len(events) == 1
    state_delta = events[0].actions.state_delta

    # temp:fundamental_data carries the machine-readable per-ticker triad.
    assert "temp:fundamental_data" in state_delta
    fd = state_delta["temp:fundamental_data"]
    assert "AAPL" in fd and "MSFT" in fd
    assert fd["AAPL"]["ratios"]["pe_ratio"] == 28.5
    assert fd["MSFT"]["ratios"]["pe_ratio"] == 32.0
    assert fd["AAPL"]["filings"] == aapl_filings
    assert fd["MSFT"]["filings"] == msft_filings

    # Pin the contract: ``insider`` is stored as a typed ``Form4Bundle``, NOT
    # a ``.model_dump()`` dict.  Several downstream consumers — most notably
    # ``fundamental_hash_inputs_from_dict`` in the per-ticker cache callback
    # and the legacy branch of ``extract_fundamental_features`` — gate on
    # ``isinstance(_, Form4Bundle)`` / call ``.trades`` directly.  Dumping
    # here silently breaks both (the cache callback raises ``AttributeError``
    # and aborts the branch before the LLM is invoked).  See the S5 ↔ Spec A
    # regression for context.  The strict decision-logger's recursive
    # ``_coerce`` handles serialisation at the log-write boundary, so the
    # in-state shape stays typed.
    from data.models import Form4Bundle as _Form4Bundle

    assert isinstance(fd["AAPL"]["insider"], _Form4Bundle)
    assert isinstance(fd["MSFT"]["insider"], _Form4Bundle)

    # One temp:fundamental_context_<TICKER> key per ticker, each containing
    # only that ticker's block.
    assert "temp:fundamental_context_AAPL" in state_delta
    assert "temp:fundamental_context_MSFT" in state_delta

    aapl_block = state_delta["temp:fundamental_context_AAPL"]
    msft_block = state_delta["temp:fundamental_context_MSFT"]

    # AAPL block should mention AAPL content, not MSFT content.
    assert "AAPL" in aapl_block
    assert "MSFT" not in aapl_block

    # MSFT block should mention MSFT content, not AAPL content.
    assert "MSFT" in msft_block
    assert "AAPL" not in msft_block


@pytest.mark.asyncio
async def test_fetch_degrades_on_provider_error():
    """A provider exception for one ticker yields empty placeholders for it."""

    async def _good_ratios(ticker, as_of=None):
        return _make_ratios_dict()

    async def _flaky_ratios(ticker, as_of=None):
        if ticker == "MSFT":
            raise RuntimeError("ratios provider down")
        return _make_ratios_dict()

    async def _good_filings(ticker, as_of=None, limit=3, include_excerpts=True):
        return [_make_filing_dict(ticker)]

    async def _flaky_filings(ticker, as_of=None, limit=3, include_excerpts=True):
        if ticker == "MSFT":
            raise RuntimeError("filings provider down")
        return [_make_filing_dict(ticker)]

    async def _good_insider(ticker, lookback_days=30, as_of=None):
        return _make_empty_bundle()

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test",
        user_id="test",
        state={"tickers": ["AAPL", "MSFT"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )

    agent = FundamentalFetchAgent(name="FundamentalFetch")
    ctx = InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-1",
        agent=agent,
    )

    with (
        patch("agents.analysts.fundamental.fetch_agent.get_company_ratios", _flaky_ratios),
        patch("agents.analysts.fundamental.fetch_agent.get_company_filings", _flaky_filings),
        patch("agents.analysts.fundamental.fetch_agent.get_insider_trades", _good_insider),
    ):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta

    # MSFT ratios and filings both failed — they should be None / empty list.
    assert sd["temp:fundamental_data"]["MSFT"]["ratios"] is None
    assert sd["temp:fundamental_data"]["MSFT"]["filings"] == []

    # Per-ticker context for MSFT still exists (no KeyError), just reflects the
    # empty state — the "(no filings available)" placeholder should appear.
    assert "temp:fundamental_context_MSFT" in sd
    msft_block = sd["temp:fundamental_context_MSFT"]
    assert "(no filings available)" in msft_block

    # AAPL should be unaffected by MSFT's failures.
    assert sd["temp:fundamental_data"]["AAPL"]["ratios"] is not None
    assert len(sd["temp:fundamental_data"]["AAPL"]["filings"]) == 1


@pytest.mark.asyncio
async def test_fetch_writes_aggregate_fundamental_context_for_trace():
    """The aggregate ``temp:fundamental_context`` key (multi-ticker joined
    block) is retained for trace/debug surfaces — see Phase 9 spec §1.
    """

    async def _mock_ratios(ticker, as_of=None):
        return _make_ratios_dict()

    async def _mock_filings(ticker, as_of=None, limit=3, include_excerpts=True):
        return [_make_filing_dict(ticker)]

    async def _mock_insider(ticker, lookback_days=30, as_of=None):
        return _make_empty_bundle()

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test",
        user_id="test",
        state={"tickers": ["AAPL", "MSFT"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )
    agent = FundamentalFetchAgent(name="FundamentalFetch")
    ctx = InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-1",
        agent=agent,
    )

    with (
        patch("agents.analysts.fundamental.fetch_agent.get_company_ratios", _mock_ratios),
        patch("agents.analysts.fundamental.fetch_agent.get_company_filings", _mock_filings),
        patch("agents.analysts.fundamental.fetch_agent.get_insider_trades", _mock_insider),
    ):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta
    assert "temp:fundamental_context" in sd

    # Both ticker headers appear in the joined aggregate block.
    assert "=== AAPL ===" in sd["temp:fundamental_context"]
    assert "=== MSFT ===" in sd["temp:fundamental_context"]

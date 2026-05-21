# tests/analysts/news/test_joiner.py
"""NewsJoinerAgent — reads N temp:news_verdict_<TICKER> keys; builds
news_verdicts + news_evidence; synthesises no-data for missing keys."""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.news.joiner import NewsJoinerAgent


@pytest.mark.asyncio
async def test_joiner_builds_canonical_keys_from_per_ticker_state():
    """news_verdicts + news_evidence land via one state_delta event."""

    state = {
        "tickers":  ["AAPL", "MSFT"],
        "tick_id":  "t-1",
        "as_of":    "2026-05-21T14:00",
        "temp:news_data": {
            "AAPL": {"news": [{"title": "AAPL beats", "summary": "Q3 strong"}]},
            "MSFT": {"news": [{"title": "MSFT guides up", "summary": "Cloud growth"}]},
        },
        "temp:news_verdict_AAPL": {
            "ticker": "AAPL", "lean": "bullish", "magnitude": 0.7,
            "confidence": 0.8, "rationale": "Earnings beat",
            "key_factors": ["catalyst:earnings"], "is_no_data": False,
        },
        "temp:news_verdict_MSFT": {
            "ticker": "MSFT", "lean": "bullish", "magnitude": 0.5,
            "confidence": 0.6, "rationale": "Guidance up",
            "key_factors": ["catalyst:guidance"], "is_no_data": False,
        },
    }

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )

    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]

    assert len(events) == 1
    delta = events[0].actions.state_delta

    # news_verdicts is a VerdictBatch dict.
    assert "news_verdicts" in delta
    assert "verdicts" in delta["news_verdicts"]
    verdict_tickers = {v["ticker"] for v in delta["news_verdicts"]["verdicts"]}
    assert verdict_tickers == {"AAPL", "MSFT"}

    # news_evidence is a list of AnalystEvidence dumps, one row per ticker.
    assert "news_evidence" in delta
    ev_tickers = {row["ticker"] for row in delta["news_evidence"]}
    assert ev_tickers == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_joiner_synthesises_no_data_for_missing_key():
    """A missing temp:news_verdict_<TICKER> → synthetic no-data verdict in both outputs."""

    state = {
        "tickers":  ["AAPL", "MSFT"],
        "tick_id":  "t-1",
        "as_of":    "2026-05-21T14:00",
        "temp:news_data": {
            "AAPL": {"news": [{"title": "AAPL beats"}]},
            "MSFT": {"news": []},
        },
        # MSFT key absent — simulates a failed branch.
        "temp:news_verdict_AAPL": {
            "ticker": "AAPL", "lean": "bullish", "magnitude": 0.7,
            "confidence": 0.8, "rationale": "ok", "key_factors": [],
            "is_no_data": False,
        },
    }

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )

    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]
    delta = events[0].actions.state_delta

    # MSFT appears as no-data in both outputs.
    msft_verdict = next(
        v for v in delta["news_verdicts"]["verdicts"] if v["ticker"] == "MSFT"
    )
    assert msft_verdict["is_no_data"] is True
    assert msft_verdict["lean"]       == "neutral"

    msft_ev = next(row for row in delta["news_evidence"] if row["ticker"] == "MSFT")
    assert msft_ev["verdict"]["is_no_data"] is True


@pytest.mark.asyncio
async def test_joiner_output_consumable_by_strategist_index_evidence():
    """The joiner's `news_evidence` list must round-trip through
    Strategist's ``_index_evidence`` without shape errors.  This locks in the
    contract verified at plan-time (``context_shim._index_evidence`` accepts
    either a raw ``dict`` or a validated ``AnalystEvidence``).  If a future
    edit changes ``ev.model_dump(mode="json")`` to a non-dict payload, this
    test catches it before the strategist crashes mid-tick.
    """

    from agents.strategist.context_shim import _index_evidence

    state = {
        "tickers": ["AAPL"],
        "tick_id": "t-1",
        "as_of":   "2026-05-21T14:00",
        "temp:news_data": {"AAPL": {"news": [{"title": "AAPL beats"}]}},
        "temp:news_verdict_AAPL": {
            "ticker": "AAPL", "lean": "bullish", "magnitude": 0.7,
            "confidence": 0.8, "rationale": "ok", "key_factors": [],
            "is_no_data": False,
        },
    }

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )
    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    delta = (await agent.run_async(ctx).__anext__()).actions.state_delta

    # Simulate Strategist downstream consumption.
    indexed = _index_evidence({"news_evidence": delta["news_evidence"]}, "news_evidence")
    assert "AAPL" in indexed
    assert indexed["AAPL"].ticker == "AAPL"

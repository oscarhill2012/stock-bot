# tests/analysts/fundamental/test_joiner.py
"""FundamentalJoinerAgent — reads N temp:fundamental_verdict_<TICKER> keys;
builds fundamental_verdicts + fundamental_evidence; synthesises no-data for
missing keys.

Mirror of tests/analysts/news/test_joiner.py with ``news`` → ``fundamental``
throughout.  The per-ticker data shape follows the Phase 9 fetch-agent output:

    {
        "<TICKER>": {
            "ratios":  dict | None,
            "filings": list[dict],
            "insider": Form4Bundle-compatible dict | None,
        }
    }
"""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from agents.analysts.fundamental.joiner import FundamentalJoinerAgent


# ---------------------------------------------------------------------------
# Shared sample data helpers
# ---------------------------------------------------------------------------

def _make_ratios(pe: float = 20.0) -> dict:
    """Return a minimal company-ratios dict with the fields the extractor reads."""
    return {
        "pe_ratio":              pe,
        "forward_pe":            18.0,
        "peg_ratio":             1.5,
        "revenue_growth":        0.12,
        "profit_margins":        0.25,
        "debt_to_equity":        0.4,
        "free_cashflow":         5_000_000_000.0,
        "return_on_equity":      0.3,
        "analyst_rating":        2.1,
        "number_of_analyst_opinions": 30,
    }


def _make_ticker_slice(pe: float = 20.0) -> dict:
    """Return a per-ticker fundamental payload suitable for the extractor."""
    return {
        "ratios":  _make_ratios(pe),
        "filings": [
            {
                "form":        "10-Q",
                "filed":       "2026-04-15",
                "description": "Quarterly report",
                "item":        None,
                "excerpt":     None,
            },
        ],
        "insider": None,   # tolerated — extractor degrades gracefully
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_joiner_builds_canonical_keys_from_per_ticker_state():
    """fundamental_verdicts + fundamental_evidence land via one state_delta event."""

    state = {
        "tickers":  ["AAPL", "MSFT"],
        "tick_id":  "t-1",
        "as_of":    "2026-05-21T14:00",
        "temp:fundamental_data": {
            "AAPL": _make_ticker_slice(pe=20.0),
            "MSFT": _make_ticker_slice(pe=30.0),
        },
        "temp:fundamental_verdict_AAPL": {
            "ticker":      "AAPL",
            "lean":        "bullish",
            "magnitude":   0.7,
            "confidence":  0.8,
            "rationale":   "Strong earnings and low debt",
            "key_factors": ["catalyst:earnings", "factor:low_debt"],
            "is_no_data":  False,
        },
        "temp:fundamental_verdict_MSFT": {
            "ticker":      "MSFT",
            "lean":        "bullish",
            "magnitude":   0.5,
            "confidence":  0.6,
            "rationale":   "Cloud segment growing fast",
            "key_factors": ["factor:cloud_growth"],
            "is_no_data":  False,
        },
    }

    svc     = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )

    agent = FundamentalJoinerAgent(name="FundamentalJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]

    # Exactly one event carrying the canonical keys.
    assert len(events) == 1
    delta = events[0].actions.state_delta

    # fundamental_verdicts is a VerdictBatch dict.
    assert "fundamental_verdicts" in delta
    assert "verdicts" in delta["fundamental_verdicts"]
    verdict_tickers = {v["ticker"] for v in delta["fundamental_verdicts"]["verdicts"]}
    assert verdict_tickers == {"AAPL", "MSFT"}

    # fundamental_evidence is a list of AnalystEvidence dumps, one row per ticker.
    assert "fundamental_evidence" in delta
    ev_tickers = {row["ticker"] for row in delta["fundamental_evidence"]}
    assert ev_tickers == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_joiner_synthesises_no_data_for_missing_key():
    """A missing temp:fundamental_verdict_<TICKER> → synthetic no-data verdict in both outputs."""

    state = {
        "tickers":  ["AAPL", "MSFT"],
        "tick_id":  "t-1",
        "as_of":    "2026-05-21T14:00",
        "temp:fundamental_data": {
            "AAPL": _make_ticker_slice(pe=20.0),
            "MSFT": _make_ticker_slice(pe=30.0),
        },
        # MSFT verdict key is absent — simulates a failed branch.
        "temp:fundamental_verdict_AAPL": {
            "ticker":      "AAPL",
            "lean":        "bullish",
            "magnitude":   0.7,
            "confidence":  0.8,
            "rationale":   "Strong earnings",
            "key_factors": [],
            "is_no_data":  False,
        },
    }

    svc     = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )

    agent = FundamentalJoinerAgent(name="FundamentalJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]
    delta  = events[0].actions.state_delta

    # MSFT appears as no-data in fundamental_verdicts.
    msft_verdict = next(
        v for v in delta["fundamental_verdicts"]["verdicts"] if v["ticker"] == "MSFT"
    )
    assert msft_verdict["is_no_data"] is True
    assert msft_verdict["lean"]       == "neutral"

    # MSFT also appears as no-data in fundamental_evidence.
    msft_ev = next(row for row in delta["fundamental_evidence"] if row["ticker"] == "MSFT")
    assert msft_ev["verdict"]["is_no_data"] is True


@pytest.mark.asyncio
async def test_joiner_output_consumable_by_strategist_index_evidence():
    """The joiner's ``fundamental_evidence`` list must round-trip through
    Strategist's ``_index_evidence`` without shape errors.

    This locks in the contract verified at plan-time (``context_shim._index_evidence``
    accepts either a raw ``dict`` or a validated ``AnalystEvidence``).  If a
    future edit changes ``ev.model_dump(mode="json")`` to a non-dict payload,
    this test catches it before the strategist crashes mid-tick.
    """
    from agents.strategist.context_shim import _index_evidence

    state = {
        "tickers": ["AAPL"],
        "tick_id": "t-1",
        "as_of":   "2026-05-21T14:00",
        "temp:fundamental_data": {
            "AAPL": _make_ticker_slice(pe=22.0),
        },
        "temp:fundamental_verdict_AAPL": {
            "ticker":      "AAPL",
            "lean":        "bullish",
            "magnitude":   0.7,
            "confidence":  0.8,
            "rationale":   "Strong balance sheet",
            "key_factors": [],
            "is_no_data":  False,
        },
    }

    svc     = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t1",
    )
    agent = FundamentalJoinerAgent(name="FundamentalJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    delta = (await agent.run_async(ctx).__anext__()).actions.state_delta

    # Simulate Strategist downstream consumption.
    indexed = _index_evidence(
        {"fundamental_evidence": delta["fundamental_evidence"]},
        "fundamental_evidence",
    )
    assert "AAPL" in indexed
    assert indexed["AAPL"].ticker == "AAPL"


@pytest.mark.asyncio
async def test_fundamental_joiner_passes_retries_to_summary(monkeypatch) -> None:
    """The joiner reads temp:_obs_fundamental_retries and passes it as the
    ``retries=`` kwarg to ``emit_analyst_summary``.

    Symmetric mirror of ``test_news_joiner_passes_retries_to_summary`` in
    ``tests/analysts/news/test_joiner.py``.  ``InMemorySessionService`` strips
    ``temp:`` keys on session creation, so they are injected directly onto
    ``session.state`` after creation, then ``emit_analyst_summary`` is
    monkeypatched to capture its kwargs without producing log output.
    """
    captured: list[dict] = []

    def _fake_emit(analyst_label: str, *, calls, ticker_count, retries=None) -> None:
        """Capture the call kwargs for assertion; do not emit any log output."""
        captured.append({
            "analyst_label": analyst_label,
            "calls":         calls,
            "ticker_count":  ticker_count,
            "retries":       retries,
        })

    monkeypatch.setenv("STOCKBOT_TERMINAL_LOG", "1")
    monkeypatch.setattr(
        "agents.analysts.fundamental.joiner.emit_analyst_summary",
        _fake_emit,
    )

    state = {
        "tickers":  ["AAPL"],
        "tick_id":  "t-retry",
        "as_of":    "2026-05-21T14:00",
        "temp:fundamental_data": {
            "AAPL": _make_ticker_slice(pe=22.0),
        },
        "temp:fundamental_verdict_AAPL": {
            "ticker":      "AAPL",
            "lean":        "bullish",
            "magnitude":   0.6,
            "confidence":  0.7,
            "rationale":   "ok",
            "key_factors": [],
            "is_no_data":  False,
        },
    }

    svc     = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t-retry",
    )

    # InMemorySessionService strips temp: keys; inject them directly onto the
    # state dict after creation so the joiner can read them.
    # Per-ticker observability key — one scalar dict per branch, written by
    # ``make_observability_callbacks`` in the live pipeline.  Disjoint keys
    # avoid the shared-list race that ParallelAgent fan-out otherwise causes.
    session.state["temp:_obs_fundamental_call_AAPL"] = {
        "ticker":           "AAPL",
        "elapsed":          1.2,
        "prompt_tokens":    1200,
        "candidate_tokens": 600,
        "ok":               True,
    }
    session.state["temp:_obs_fundamental_retries"] = {"timeout": 1}

    agent = FundamentalJoinerAgent(name="FundamentalJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-retry", agent=agent,
    )

    # Drive the joiner — the monkeypatched emit_analyst_summary captures args.
    _events = [ev async for ev in agent.run_async(ctx)]

    assert captured, "emit_analyst_summary was never called"
    call = captured[0]
    assert call["analyst_label"] == "fundamental"
    assert call["retries"] == {"timeout": 1}, (
        f"Expected retries={{'timeout': 1}}; got retries={call['retries']}"
    )

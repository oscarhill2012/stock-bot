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


@pytest.mark.asyncio
async def test_news_joiner_passes_retries_to_summary(monkeypatch) -> None:
    """The joiner reads temp:_obs_news_retries and passes it as the
    ``retries=`` kwarg to ``emit_analyst_summary``.

    ``InMemorySessionService`` strips ``temp:`` prefixed keys on session
    creation, so this test cannot populate them via the normal session init.
    Instead it:

    1. Monkeypatches ``emit_analyst_summary`` so we can capture the kwargs it
       receives without any terminal output.
    2. Populates ``temp:_obs_news_retries`` directly onto the session state
       dict *after* session creation (ADK's InMemorySessionService returns a
       plain ``dict`` via ``session.state``, so direct assignment works).
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

    # Patch at the module the joiner imports from so the replacement is seen
    # regardless of how Python caches the function reference.
    monkeypatch.setenv("STOCKBOT_TERMINAL_LOG", "1")
    monkeypatch.setattr(
        "agents.analysts.news.joiner.emit_analyst_summary",
        _fake_emit,
    )

    state = {
        "tickers":  ["AAPL"],
        "tick_id":  "t-retry",
        "as_of":    "2026-05-21T14:00",
        "temp:news_data": {
            "AAPL": {"news": [{"title": "Rate-limited news", "summary": "ok"}]},
        },
        "temp:news_verdict_AAPL": {
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
    session.state["temp:_obs_news_call_AAPL"] = {
        "ticker":           "AAPL",
        "elapsed":          1.0,
        "prompt_tokens":    1000,
        "candidate_tokens": 500,
        "ok":               True,
    }
    session.state["temp:_obs_news_retries"] = {"rate_limit": 2}

    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-retry", agent=agent,
    )

    # Drive the joiner — the monkeypatched emit_analyst_summary captures args.
    _events = [ev async for ev in agent.run_async(ctx)]

    assert captured, "emit_analyst_summary was never called"
    call = captured[0]
    assert call["analyst_label"] == "news"
    assert call["retries"] == {"rate_limit": 2}, (
        f"Expected retries={{'rate_limit': 2}}; got retries={call['retries']}"
    )


@pytest.mark.asyncio
async def test_news_joiner_verdict_evidence_consistency():
    """Assert verdict/evidence structural consistency (F-analysts-016).

    The joiner guarantees the following invariants for every ticker:

    1. The lean recorded in ``news_verdicts`` matches the lean recorded in the
       corresponding ``news_evidence`` row — both derive from the same
       ``AnalystVerdict`` object, so they cannot diverge.

    2. When ``is_no_data=True`` (synthesised no-data path), the lean is always
       ``"neutral"`` — the joiner forces this explicitly in the synthesis branch.

    3. Every ticker in the input ``tickers`` list produces exactly one row in
       ``news_verdicts["verdicts"]`` and one row in ``news_evidence`` — the
       lengths always equal ``len(tickers)`` regardless of which branches
       succeeded or failed.

    Note: the joiner does NOT guarantee that a non-neutral, non-no-data verdict
    carries at least one ``key_factors`` entry — the LLM may emit an empty list,
    and the schema permits that.  The invariant asserted here is the structural
    mirror-image: the two output lists are always aligned and the synthesised
    no-data verdict always has lean ``"neutral"``.
    """
    state = {
        "tickers":  ["AAPL", "MSFT", "TSLA"],
        "tick_id":  "t-consist",
        "as_of":    "2026-05-21T14:00",
        "temp:news_data": {
            "AAPL": {"news": [{"title": "AAPL beats", "summary": "Q3 strong"}]},
            "MSFT": {"news": []},
            "TSLA": {"news": [{"title": "Bearish note"}]},
        },
        # TSLA verdict is absent — simulates a failed branch; should become
        # a no-data neutral synthesis.
        "temp:news_verdict_AAPL": {
            "ticker": "AAPL", "lean": "bullish", "magnitude": 0.7,
            "confidence": 0.8, "rationale": "Earnings beat",
            "key_factors": ["catalyst:earnings"], "is_no_data": False,
        },
        "temp:news_verdict_MSFT": {
            "ticker": "MSFT", "lean": "bearish", "magnitude": 0.4,
            "confidence": 0.5, "rationale": "Macro headwind",
            "key_factors": [], "is_no_data": False,
        },
    }

    svc     = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t-consist",
    )
    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-consist", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]
    delta  = events[0].actions.state_delta

    verdicts  = {v["ticker"]: v for v in delta["news_verdicts"]["verdicts"]}
    evidences = {row["ticker"]: row for row in delta["news_evidence"]}

    # Invariant 3: one row per input ticker in both output lists.
    assert set(verdicts.keys())  == {"AAPL", "MSFT", "TSLA"}
    assert set(evidences.keys()) == {"AAPL", "MSFT", "TSLA"}

    for ticker in ("AAPL", "MSFT", "TSLA"):
        v   = verdicts[ticker]
        ev  = evidences[ticker]

        # Invariant 1: verdict lean matches evidence lean for the same ticker.
        assert v["lean"] == ev["verdict"]["lean"], (
            f"{ticker}: verdict lean {v['lean']!r} != evidence lean "
            f"{ev['verdict']['lean']!r}"
        )

        # Invariant 2: no-data synthesis always produces lean == "neutral".
        if v["is_no_data"]:
            assert v["lean"] == "neutral", (
                f"{ticker}: is_no_data=True but lean={v['lean']!r} (expected 'neutral')"
            )

    # Specific check: TSLA (missing verdict key) must be synthesised as no-data neutral.
    assert verdicts["TSLA"]["is_no_data"] is True
    assert verdicts["TSLA"]["lean"]       == "neutral"

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
            "horizon_days": 60,
            "rationale":   "Strong earnings and low debt",
            "key_factors": ["catalyst:earnings", "factor:low_debt"],
            "is_no_data":  False,
        },
        "temp:fundamental_verdict_MSFT": {
            "ticker":      "MSFT",
            "lean":        "bullish",
            "magnitude":   0.5,
            "confidence":  0.6,
            "horizon_days": 60,
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

    # Phase 14: a synthesised no-data verdict carries the canonical default
    # horizon (1) — the long fundamental horizon applies only to real emits.
    assert msft_verdict["horizon_days"] == 1

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


@pytest.mark.asyncio
async def test_fundamental_joiner_verdict_evidence_consistency():
    """Assert verdict/evidence structural consistency (F-analysts-016).

    The joiner guarantees the following invariants for every ticker:

    1. The lean recorded in ``fundamental_verdicts`` matches the lean recorded in
       the corresponding ``fundamental_evidence`` row — both derive from the same
       ``AnalystVerdict`` object, so they cannot diverge.

    2. When ``is_no_data=True`` (synthesised no-data path), the lean is always
       ``"neutral"`` — the joiner forces this explicitly in the synthesis branch.

    3. Every ticker in the input ``tickers`` list produces exactly one row in
       ``fundamental_verdicts["verdicts"]`` and one row in
       ``fundamental_evidence`` — the lengths always equal ``len(tickers)``
       regardless of which branches succeeded or failed.

    Note: the joiner does NOT guarantee that a non-neutral verdict carries at
    least one ``key_factors`` entry — the LLM may emit an empty list, and the
    schema permits that.  The invariant asserted here is the structural
    mirror-image: the two output lists are always aligned and the synthesised
    no-data verdict always has lean ``"neutral"``.

    Symmetric mirror of ``test_news_joiner_verdict_evidence_consistency`` in
    ``tests/unit/agents/analysts/news/test_joiner.py``.
    """
    state = {
        "tickers":  ["AAPL", "MSFT", "NVDA"],
        "tick_id":  "t-consist",
        "as_of":    "2026-05-21T14:00",
        "temp:fundamental_data": {
            "AAPL": _make_ticker_slice(pe=20.0),
            "MSFT": _make_ticker_slice(pe=30.0),
            "NVDA": _make_ticker_slice(pe=50.0),
        },
        # NVDA verdict is absent — simulates a failed branch; should become
        # a no-data neutral synthesis.
        "temp:fundamental_verdict_AAPL": {
            "ticker":      "AAPL",
            "lean":        "bullish",
            "magnitude":   0.7,
            "confidence":  0.8,
            "rationale":   "Strong earnings and low debt",
            "key_factors": ["catalyst:earnings"],
            "is_no_data":  False,
        },
        "temp:fundamental_verdict_MSFT": {
            "ticker":      "MSFT",
            "lean":        "bearish",
            "magnitude":   0.3,
            "confidence":  0.5,
            "rationale":   "Guidance disappoints",
            "key_factors": [],
            "is_no_data":  False,
        },
    }

    svc     = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t-consist",
    )
    agent = FundamentalJoinerAgent(name="FundamentalJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-consist", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]
    delta  = events[0].actions.state_delta

    verdicts  = {v["ticker"]: v for v in delta["fundamental_verdicts"]["verdicts"]}
    evidences = {row["ticker"]: row for row in delta["fundamental_evidence"]}

    # Invariant 3: one row per input ticker in both output lists.
    assert set(verdicts.keys())  == {"AAPL", "MSFT", "NVDA"}
    assert set(evidences.keys()) == {"AAPL", "MSFT", "NVDA"}

    for ticker in ("AAPL", "MSFT", "NVDA"):
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

    # Specific check: NVDA (missing verdict key) must be synthesised as no-data neutral.
    assert verdicts["NVDA"]["is_no_data"] is True
    assert verdicts["NVDA"]["lean"]       == "neutral"


@pytest.mark.asyncio
async def test_joiner_injects_config_horizon_days_into_the_verdict_batch():
    """horizon_days is injected by the joiner from config — the LLM no longer
    emits it (Phase 14 Task 6).  Symmetric mirror of the equivalent news-joiner
    test in ``tests/unit/agents/analysts/news/test_joiner.py``.

    ``InMemorySessionService`` strips every ``temp:``-prefixed key from the
    ``state=`` kwarg passed to ``create_session``, so
    ``temp:fundamental_verdict_AAPL`` has to be injected directly onto
    ``session.state`` *after* the session exists, not via the constructor.
    """
    from config.analysts import get_analysts_config

    expected_horizon = get_analysts_config().fundamental.filing_delta_horizon_days

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test",
        user_id="test",
        state={
            "tickers": ["AAPL"],
            "tick_id": "t-1",
            "as_of": "2026-07-06T14:00:00",
        },
        session_id="t-1",
    )

    # ``temp:fundamental_data`` left unset — the extractor's raw-slice lookup
    # degrades to ``{}`` and returns zero-features early; this test only
    # exercises the horizon-injection path through the LLM verdict branch.
    session.state["temp:fundamental_verdict_AAPL"] = {
        "ticker": "AAPL",
        "lean": "bullish",
        "magnitude": 0.4,
        "confidence": 0.6,
        "is_no_data": False,
        "key_factors": ["catalyst:earnings"],
        "report": {
            "summary": "Filing-delta drift signal building.",
            "drivers": [
                {"name": "backlog", "direction": "bull", "weight": 0.6,
                 "body": "Backlog growth understated in the headline."},
                {"name": "margins", "direction": "bull", "weight": 0.4,
                 "body": "Margin expansion continuing."},
            ],
        },
    }

    agent = FundamentalJoinerAgent(name="FundamentalJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]

    batch = events[-1].actions.state_delta["fundamental_verdicts"]
    assert batch["verdicts"][0]["horizon_days"] == expected_horizon


# ---------------------------------------------------------------------------
# Task 8 — deterministic post-LLM magnitude clamp + decay
# ---------------------------------------------------------------------------
#
# NB: ``InMemorySessionService.create_session`` strips every ``temp:``-prefixed
# key from the ``state=`` kwarg (see the retries test above) — so
# ``temp:fundamental_data`` and ``temp:fundamental_verdict_<TICKER>`` must be
# injected directly onto ``session.state`` *after* the session exists, exactly
# like the horizon-injection test above does, or the extractor never sees the
# raw slice and silently degrades to zero-features.

async def _run_joiner_with_llm_verdict(
    *, lean: str, magnitude: float, key_factors: list[str], ticker_slice: dict,
) -> dict:
    """Drive FundamentalJoinerAgent for a single ticker with the given raw LLM
    verdict fields and raw fundamental-data slice; return the resulting
    fundamental_evidence verdict for that ticker.
    """
    state = {
        "tickers": ["AAPL"],
        "tick_id": "t-clamp",
        "as_of": "2026-05-21T14:00",
    }

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test", state=state, session_id="t-clamp",
    )

    # Inject temp: keys directly onto session.state — create_session strips them.
    # NB: the LLM emit-schema is LlmTickerVerdict, not a bare
    # lean/magnitude/confidence/rationale dict — it requires a structured
    # ``report`` (summary + 2-4 drivers), not a flat ``rationale`` string.
    session.state["temp:fundamental_data"] = {"AAPL": ticker_slice}
    session.state["temp:fundamental_verdict_AAPL"] = {
        "ticker":      "AAPL",
        "lean":        lean,
        "magnitude":   magnitude,
        "confidence":  0.8,
        "key_factors": key_factors,
        "is_no_data":  False,
        "report": {
            "summary": "Test rationale for the clamp/decay scenario.",
            "drivers": [
                {"name": "driver_one", "direction": "bear", "weight": 0.6,
                 "body": "First driver body text."},
                {"name": "driver_two", "direction": "bear", "weight": 0.4,
                 "body": "Second driver body text."},
            ],
        },
    }

    agent = FundamentalJoinerAgent(name="FundamentalJoiner")
    ctx   = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-clamp", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]
    delta  = events[0].actions.state_delta
    ev_row = next(row for row in delta["fundamental_evidence"] if row["ticker"] == "AAPL")
    return ev_row["verdict"]


def _make_no_periodic_filing_slice(pe: float = 20.0) -> dict:
    """A ticker slice with an 8-K only (no periodic filing) — filing_anchor_days
    falls back to the sentinel, so the decay guard never fires regardless of
    horizon config."""
    return {
        "ratios": _make_ratios(pe),
        "filings": [
            {"form_type": "8-K", "filed_at": "2026-05-20T00:00:00+00:00"},
        ],
        "insider_trades": [],
        "insider_derivative_trades": [],
    }


def _make_fresh_periodic_filing_slice(pe: float = 20.0) -> dict:
    """A ticker slice with a 10-Q filed the day before ``as_of`` — well inside
    any sane ``filing_delta_horizon_days`` config, so the decay guard never
    fires."""
    return {
        "ratios": _make_ratios(pe),
        "filings": [
            {"form_type": "10-Q", "filed_at": "2026-05-20T00:00:00+00:00"},
        ],
        "insider_trades": [],
        "insider_derivative_trades": [],
    }


@pytest.mark.asyncio
async def test_fundamental_magnitude_clamped_absent_going_concern():
    """An LLM magnitude above the cap is clamped when no going-concern tag is
    present (Task 8 — deterministic downstream clamp; Lazy Prices is a weak
    per-name tilt, not a fresh material catalyst)."""
    from config.analysts import get_analysts_config

    cap = get_analysts_config().fundamental.filing_delta_magnitude_cap

    verdict = await _run_joiner_with_llm_verdict(
        lean="bearish", magnitude=0.9, key_factors=["guidance_change:true"],
        ticker_slice=_make_no_periodic_filing_slice(),
    )

    # Exact-equality (not merely ``<= cap``) so this assertion cannot pass
    # vacuously via the no-data branch (which would yield magnitude 0.0): the
    # 0.9 LLM magnitude must genuinely traverse the clamp and land ON the cap.
    # The slice has no periodic filing, so decay never fires — the clamp alone
    # determines the result.
    assert verdict["magnitude"] == pytest.approx(cap)


@pytest.mark.asyncio
async def test_going_concern_bypasses_the_clamp():
    """Going-concern is a thesis-break catalyst — magnitude is NOT clamped."""
    from config.analysts import get_analysts_config

    cap = get_analysts_config().fundamental.filing_delta_magnitude_cap

    verdict = await _run_joiner_with_llm_verdict(
        lean="bearish", magnitude=0.9, key_factors=["going_concern:true"],
        ticker_slice=_make_fresh_periodic_filing_slice(),
    )

    assert verdict["magnitude"] > cap

"""Tier 2 LLM-touching smoke test for Strategist v2.

Sends a real inference request to Gemini and asserts that the strategist:
  - writes ``state["strategist_decision"]`` by the end of the tick, and
  - returns a ``StrategistDecision`` with per-ticker ``stances`` and
    ``target_weights`` covering every watchlist ticker.

GATING
------
This file is skipped by default (CI must never call Gemini). To run it
locally set the environment variable::

    RUN_LLM_TESTS=1 pytest tests/integration/test_strategist_v2_smoke.py -v --timeout=120

Known failure modes (if the test reaches the LLM but fails):
  - ``GOOGLE_APPLICATION_CREDENTIALS`` not set / expired → the genai API
    client raises ``AttributeError: 'BaseApiClient' object has no attribute
    '_async_httpx_client'`` (observed on Python 3.14 / google-genai without
    auth). The runner swallows it but state is never written, so the
    ``assert decision_raw is not None`` fires. Fix: ``gcloud auth
    application-default login`` or set ``GOOGLE_API_KEY``.
  - ADK 1.32 runner-cleanup bug → an ``AttributeError`` or
    ``BaseExceptionGroup`` is raised *after* state has been written. The
    test swallows this (mirroring ``tick.py``) and continues to read state.
  - Gemini quota exhausted → HTTP 429 from inside ``runner.run_async``.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

# ── Skip gate ────────────────────────────────────────────────────────────────
# Module-level mark: the *entire file* is skipped unless RUN_LLM_TESTS=1 is
# set explicitly in the environment. This ensures CI never reaches Gemini.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_TESTS") != "1",
    reason="Set RUN_LLM_TESTS=1 to run LLM-touching integration tests",
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _ev(analyst: str, lean: str, conf: float, ticker: str) -> dict:
    """Build a minimal ``AnalystEvidence`` dict for one analyst / ticker pair.

    For the ``"news"`` analyst this helper automatically attaches a minimal
    ``AnalystReport`` so the smoke test exercises the report-rendering path
    through the strategist prompt (feature added in Task 5).

    Args:
        analyst: One of ``"technical"``, ``"fundamental"``, ``"news"``,
            or ``"smart_money"``.
        lean: Directional call — ``"bullish"``, ``"bearish"``, or
            ``"neutral"``.
        conf: Analyst self-reported confidence in ``[0.0, 1.0]``.
        ticker: The stock ticker symbol (e.g. ``"AAPL"``).

    Returns:
        A JSON-serialisable dict matching the ``AnalystEvidence`` schema,
        suitable for insertion into ``state["{analyst}_evidence"]``.
    """
    from contract.evidence import (
        AnalystEvidence,
        AnalystReport,
        AnalystVerdict,
        ReportDriver,
    )

    # Attach a minimal AnalystReport to the news verdict so the smoke test
    # exercises the full render_all_ticker_blocks path including prose reports.
    report: AnalystReport | None = None
    if analyst == "news":
        report = AnalystReport(
            summary=(
                "Smoke-test news summary. Two articles this tick — one broadly "
                "positive on earnings expectations, one cautious on macro outlook."
            ),
            drivers=[
                ReportDriver(
                    name="Earnings optimism",
                    direction="bull",
                    weight=0.6,
                    body="Analyst consensus raised guidance expectations ahead of print.",
                ),
                ReportDriver(
                    name="Macro headwind",
                    direction="bear",
                    weight=0.3,
                    body="Rising yield environment tempers near-term multiple expansion.",
                ),
            ],
        )

    evidence = AnalystEvidence(
        ticker=ticker,
        analyst=analyst,          # type: ignore[arg-type]
        tick_id="tick_TEST",
        recorded_at=datetime(2026, 4, 22, 14, tzinfo=UTC),
        features={},
        verdict=AnalystVerdict(
            lean=lean,            # type: ignore[arg-type]
            magnitude=conf,
            confidence=conf,
            rationale=f"Smoke test stub ({lean})",
            report=report,
        ),
    )

    return evidence.model_dump(mode="json")


# ── Test ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_strategist_v2_emits_per_ticker_stances_with_held_position():
    """Verify Strategist v2 writes a valid decision to state when run via ADK Runner.

    Seeds a two-ticker watchlist (AAPL / MSFT) where AAPL has a held position
    with a PositionThesis, then runs the strategist in isolation using
    ``InMemorySessionService``. After the runner completes (or raises the known
    ADK 1.32 cleanup error), reads back the session state and validates the
    ``strategist_decision`` key against ``StrategistDecision``.

    Assertions:
        - ``state["strategist_decision"]`` is not ``None``.
        - The raw value validates as a ``StrategistDecision``.
        - Both AAPL and MSFT appear in ``decision.stances``.
        - Both AAPL and MSFT appear in ``decision.target_weights``.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

    from agents.strategist.agent import build_strategist
    from agents.strategist.schema import PositionThesis, StrategistDecision
    from broker.portfolio import Portfolio, Position

    # The module-level ``strategist_agent`` singleton was deleted (2026-05-21)
    # in favour of the :func:`build_strategist` factory.  Construct a fresh
    # strategist instance here so this test matches production wiring exactly,
    # including the ``StrategistContextShim`` that hydrates the
    # ``temp:held_positions_view`` / ``temp:ticker_evidence`` rails.
    strategist_agent = build_strategist()

    APP_NAME = "strategist_v2_smoke"
    USER_ID = "t"

    # ── Build the AAPL held-position thesis ──────────────────────────────────
    # The strategist's held-view callback reads ``state["positions"]`` and
    # renders it into a prompt block; this seeds a realistic held position so
    # the callback path is exercised.
    aapl_thesis = PositionThesis(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 22, 14, tzinfo=UTC),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="FCF + insider buying",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
        last_reviewed_at=datetime(2026, 4, 22, 14, tzinfo=UTC),
        opened_tick_id="tick_OPEN",
    )

    # ── Build portfolio with AAPL position ───────────────────────────────────
    portfolio = Portfolio(
        cash=8000.0,
        positions={
            "AAPL": Position(quantity=10.0, avg_cost=192.40, last_price=198.50),
        },
    )

    # ── Build per-analyst evidence for both tickers ───────────────────────────
    # Leans / confidences per plan §C15:
    #   technical → bullish 0.6
    #   fundamental → bullish 0.5
    #   news → neutral 0.3
    #   smart_money → neutral 0.0
    tickers = ["AAPL", "MSFT"]

    def _build_evidence_list(analyst: str, lean: str, conf: float) -> list[dict]:
        """Return one AnalystEvidence dump per ticker for a single analyst."""
        return [_ev(analyst, lean, conf, ticker) for ticker in tickers]

    # ── Seed the ADK session with the full pipeline state ─────────────────────
    initial_state: dict = {
        "tick_id": "tick_TEST",
        "tickers": tickers,
        "portfolio": portfolio.model_dump(mode="json"),
        "positions": {"AAPL": aapl_thesis.model_dump(mode="json")},
        "memory_buffer": [],
        "day_digest": "",
        "thesis": "",
        # Per-analyst evidence lists — each is a list[AnalystEvidence] dump.
        "technical_evidence":   _build_evidence_list("technical",   "bullish", 0.6),
        "fundamental_evidence": _build_evidence_list("fundamental",  "bullish", 0.5),
        "news_evidence":        _build_evidence_list("news",         "neutral", 0.3),
        "smart_money_evidence": _build_evidence_list("smart_money",  "neutral", 0.0),
    }

    session_service = InMemorySessionService()
    adk_session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        state=initial_state,
    )

    runner = Runner(
        agent=strategist_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    # ── Run the strategist and consume all events ─────────────────────────────
    events = runner.run_async(
        user_id=USER_ID,
        session_id=adk_session.id,
        new_message=genai_types.Content(
            parts=[genai_types.Part(text="Run strategist smoke")],
            role="user",
        ),
    )
    try:
        async for _ in events:
            pass
    except (AttributeError, BaseException):
        # ADK 1.32 has a known cleanup bug: after the agent finishes, the
        # runner may raise AttributeError('NoneType'.partial) or a
        # BaseExceptionGroup from parallel-agent teardown. Both occur *after*
        # session state has been written. We swallow and continue, mirroring
        # the pattern in orchestrator/tick.py:88-94.
        pass

    # ── Read back the completed session state ─────────────────────────────────
    updated = await session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=adk_session.id,
    )

    decision_raw = updated.state.get("strategist_decision")

    # ── Assertions ────────────────────────────────────────────────────────────
    assert decision_raw is not None, (
        "Strategist did not write strategist_decision to state. "
        "Check Gemini credentials and ADK runner logs."
    )

    # Validate the raw dict against the Pydantic schema.
    decision = StrategistDecision.model_validate(decision_raw)

    # Every watchlist ticker must have a stance.
    stance_tickers = {s.ticker for s in decision.stances}
    assert stance_tickers == {"AAPL", "MSFT"}, (
        f"Expected stances for AAPL and MSFT; got {stance_tickers}"
    )

    # Every watchlist ticker must have a target weight (may be 0 = no position).
    assert set(decision.target_weights.keys()) == {"AAPL", "MSFT"}, (
        f"Expected target_weights for AAPL and MSFT; got "
        f"{set(decision.target_weights.keys())}"
    )

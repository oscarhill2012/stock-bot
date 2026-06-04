"""Integration test: user:positions does NOT bleed across different app_name values.

Two sessions, same ``user_id="stockbot"``, different ``app_name`` values
(``StockBot-paper`` vs ``StockBot-live``).  Writing ``user:positions`` to the
paper app must not make it visible in the live app.

This guards against a hypothetical ADK regression where the user_state table
uses only ``user_id`` as the key, leaking positions between deployments.
"""
from __future__ import annotations

import pytest
from google.adk.events import Event, EventActions
from google.adk.sessions import DatabaseSessionService


@pytest.mark.asyncio
async def test_user_positions_scoped_to_app_name():
    """user:positions written to StockBot-paper must not appear in StockBot-live.

    The ADK user_state table is keyed on ``(app_name, user_id)``, so the two
    namespaces must be fully disjoint.  This test is the regression guard.
    """

    svc = DatabaseSessionService(db_url="sqlite+aiosqlite:///:memory:")

    # ── Paper session: write a position ─────────────────────────────────────
    paper_session = await svc.create_session(
        app_name = "StockBot-paper",
        user_id  = "stockbot",
        state    = {"tick_id": "paper-t1"},
    )

    paper_positions = {
        "AAPL": {
            "ticker":                  "AAPL",
            "opened_at":               "2026-05-23T00:00:00+00:00",
            "opened_tick_id":          "paper-tick-1",
            "opened_price":            192.50,
            "weight":                  0.05,
            "target_price":            210.0,
            "stop_price":              180.0,
            "catalyst":                "paper-test",
            "horizon":                 "swing",
            "rationale":               "Paper trading test position",
            "last_reviewed_at":        "2026-05-23T00:00:00+00:00",
            "last_reviewed_decision":  "open",
        },
    }

    await svc.append_event(
        paper_session,
        Event(
            invocation_id = "paper-iv-1",
            author        = "test",
            actions       = EventActions(state_delta={
                "user:positions": paper_positions,
            }),
        ),
    )

    # ── Live session: must NOT see the paper positions ───────────────────────
    live_session = await svc.create_session(
        app_name = "StockBot-live",
        user_id  = "stockbot",
        state    = {"tick_id": "live-t1"},
    )

    live_positions = live_session.state.get("user:positions", {})
    assert "AAPL" not in live_positions, (
        "StockBot-live session must not inherit positions from StockBot-paper; "
        "ADK user_state namespacing is broken if this fails"
    )

"""Integration test: user:positions round-trips across ADK sessions.

Verifies that writing ``user:positions`` in session A and then creating a
new session B for the same ``(app_name, user_id)`` pair causes the value
to be visible in session B via the ADK user-state merge.

This is the Phase 2 'implicit hydration' guarantee: ``DatabaseSessionService``
persists ``user:``-prefixed keys to the ``user_state`` table and merges them
into every new session for the same ``(app_name, user_id)``.  The whole
memory backbone relies on this behaviour surviving across tick boundaries.
"""
from __future__ import annotations

import pytest
from google.adk.events import Event, EventActions
from google.adk.sessions import DatabaseSessionService


@pytest.mark.asyncio
async def test_thesis_persistence_round_trips_across_sessions():
    """Writing user:positions in session A and reading in session B
    (same app_name + user_id) reproduces the value via DatabaseSessionService.

    Verifies ADK DatabaseSessionService merges user_state into every
    new session for the same (app_name, user_id) pair — the Phase 2
    'implicit hydration' step the spec relies on.
    """

    svc = DatabaseSessionService(db_url="sqlite+aiosqlite:///:memory:")

    # ── Session A: write a position thesis via a state_delta event ──────────
    session_a = await svc.create_session(
        app_name = "StockBot-test",
        user_id  = "stockbot",
        state    = {"tick_id": "t-1"},
    )

    # Build a complete PositionThesis-shaped dict matching the iter-3 schema.
    # Note: target_price, stop_price, and horizon were removed in iter-3;
    # last_reviewed_decision uses the three-verb vocabulary (buy / sell / update).
    avgo_thesis = {
        "ticker":                   "AVGO",
        "opened_at":                "2026-05-23T00:00:00+00:00",
        "opened_tick_id":           "tick-t1",
        "opened_price":             1023.50,
        "weight":                   0.10,
        "catalyst":                 "AI capex cycle",
        "rationale":                "AI capex thesis intact — test fixture",
        "last_reviewed_at":         "2026-05-23T00:00:00+00:00",
        "last_reviewed_decision":   "buy",
        "last_reviewed_reason":     "Initial entry on AI capex thesis.",
        "thesis_last_updated_tick": 0,
    }

    positions_a = {"AVGO": avgo_thesis}

    # Write via an Event whose state_delta carries the user:-prefixed key.
    # DatabaseSessionService persists user:-prefixed keys to the user_state
    # table, which is then merged into subsequent sessions.
    await svc.append_event(
        session_a,
        Event(
            invocation_id = "iv-1",
            author        = "test",
            actions       = EventActions(state_delta={
                "user:positions": positions_a,
            }),
        ),
    )

    # ── Session B: fresh session for the same (app_name, user_id) ───────────
    session_b = await svc.create_session(
        app_name = "StockBot-test",
        user_id  = "stockbot",
        state    = {"tick_id": "t-2"},
    )

    # ADK must have merged the user_state from session A into session B.
    assert "user:positions" in session_b.state, (
        "user:positions must be merged from user_state into the new session; "
        "if this fails, DatabaseSessionService is not merging user:-prefixed keys"
    )
    assert "AVGO" in session_b.state["user:positions"], (
        "AVGO must be present in user:positions in session B — "
        "the round-trip did not preserve the value"
    )
    avgo_back = session_b.state["user:positions"]["AVGO"]
    assert avgo_back["opened_price"] == pytest.approx(1023.50)
    assert avgo_back["weight"] == pytest.approx(0.10)

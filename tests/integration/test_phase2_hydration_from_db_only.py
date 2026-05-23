"""Integration test: Phase 2 hydration comes from the DB row, not in-process state.

Uses a real sqlite file (not :memory:) so that destroying and re-instantiating
``DatabaseSessionService`` guarantees that the value retrieved by process B
cannot possibly originate from process A's in-process state.

Process A writes ``user:positions`` via an event, then the service instance is
deleted (simulating a process restart).  Process B creates a fresh
``DatabaseSessionService`` against the same file, opens a new session for the
same ``(app_name, user_id)``, and asserts the value equals what process A wrote.
"""
from __future__ import annotations

import pytest
from google.adk.events import Event, EventActions
from google.adk.sessions import DatabaseSessionService


@pytest.mark.asyncio
async def test_phase2_hydration_comes_from_db_not_in_process_state(tmp_path):
    """After a service restart, user:positions is loaded from the DB, not memory.

    Creates a sqlite file, writes a position through service A, destroys the
    service instance, then asserts service B reads the same value from disk.
    This proves Phase 2 hydration is durable — not a cache of in-process state.
    """

    db_file = tmp_path / "state.db"
    db_url  = f"sqlite+aiosqlite:///{db_file}"

    # ── Process A: write user:positions, then discard the service instance ──
    svc_a = DatabaseSessionService(db_url=db_url)

    session_a = await svc_a.create_session(
        app_name = "StockBot-test",
        user_id  = "stockbot",
        state    = {"tick_id": "t-1"},
    )

    written_positions = {
        "MSFT": {
            "ticker":                  "MSFT",
            "opened_at":               "2026-05-23T00:00:00+00:00",
            "opened_tick_id":          "tick-t1",
            "opened_price":            415.0,
            "weight":                  0.08,
            "target_price":            480.0,
            "stop_price":              390.0,
            "catalyst":                "Cloud margin expansion",
            "horizon":                 "swing",
            "rationale":               "Azure growth rate inflecting upward",
            "last_reviewed_at":        "2026-05-23T00:00:00+00:00",
            "last_reviewed_decision":  "open",
            "last_reviewed_reason":    "Initial open",
        },
    }

    await svc_a.append_event(
        session_a,
        Event(
            invocation_id = "iv-1",
            author        = "test",
            actions       = EventActions(state_delta={
                "user:positions": written_positions,
            }),
        ),
    )

    # Explicitly destroy service A.  Any in-process caches are gone.
    del svc_a
    del session_a

    # ── Process B: fresh service instance, must read from DB ────────────────
    svc_b = DatabaseSessionService(db_url=db_url)

    session_b = await svc_b.create_session(
        app_name = "StockBot-test",
        user_id  = "stockbot",
        state    = {"tick_id": "t-2"},
    )

    # user_state merge must populate user:positions from the DB row.
    assert "user:positions" in session_b.state, (
        "Fresh DatabaseSessionService must hydrate user:positions from the DB; "
        "if this fails, Phase 2 hydration does not survive a process restart"
    )
    positions_b = session_b.state["user:positions"]
    assert "MSFT" in positions_b, (
        "MSFT must be present in session B's user:positions — "
        "the DB row was not loaded"
    )
    assert positions_b["MSFT"]["opened_price"] == pytest.approx(415.0), (
        "opened_price mismatch — value came from in-process state, not DB"
    )
    assert positions_b["MSFT"]["rationale"] == "Azure growth rate inflecting upward"

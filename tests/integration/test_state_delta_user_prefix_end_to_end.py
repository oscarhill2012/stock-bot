"""Integration test: minimal pipeline → Executor.after_agent_callback → DB persistence.

Wires the real Executor (with its ``_executor_thesis_writer_callback``) against
a real ``DatabaseSessionService`` backed by an in-memory sqlite.  After one
tick the test fetches the session from the service and asserts that:
- ``session.state["user:positions"]`` contains the expected position thesis.
- ``session.state["user:thesis"]`` contains the expected standing thesis string.

This test proves the auto-yield path:
  _run_async_impl yields broker-effect Event
    → ADK runs _executor_thesis_writer_callback
      → callback writes user:positions + user:thesis via ctx.state[key] = val
        → ADK auto-yields state-delta Event from accumulated delta
          → DatabaseSessionService.append_event persists user:-prefixed keys
            to user_state table
              → get_session merges user_state on reload.

See contract-invariants.md §C-Rule 1 amendment (2026-05-23).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.adk import Runner
from google.adk.apps import App
from google.adk.sessions import DatabaseSessionService
from google.genai import types as genai_types

from agents.executor.agent import build_executor
from broker.fake import FakeBroker


@pytest.mark.asyncio
async def test_user_positions_and_thesis_written_after_executor_tick():
    """Running Executor via ADK Runner persists user:positions and user:thesis.

    Uses a real ``DatabaseSessionService`` (in-memory sqlite) so that the
    assertion is against the persisted session, not an in-process variable.
    The ADK Runner is responsible for invoking the after_agent_callback; this
    test verifies the full callback → auto-yield → append_event → reload chain.
    """

    # ── Setup ────────────────────────────────────────────────────────────────
    broker    = FakeBroker(starting_cash=50_000.0, prices={"AAPL": 200.0})
    executor  = build_executor(broker)
    svc       = DatabaseSessionService(db_url="sqlite+aiosqlite:///:memory:")
    # ADK 1.34: wrap the agent in an ``App`` rather than passing ``agent=``.
    # ``App.name`` must be identifier-safe, but the partition key here is the
    # hyphenated ``"test-e2e"`` (matched at ``create_session`` below), so the
    # real name rides on the ``app_name`` override.
    app       = App(name="test_e2e_app", root_agent=executor)
    runner    = Runner(app=app, app_name="test-e2e", session_service=svc)

    open_ts   = datetime(2026, 5, 23, 9, 30, tzinfo=UTC).isoformat()

    # ── Seed session: BUY order + strategist decision with a buy stance ────
    # The strategist_decision must carry a TickerStance with intent="buy"
    # so both the executor BUY-path (assembling the bare-key bridge thesis)
    # and the after-callback's apply_stance_to_thesis (writing user:positions)
    # fire correctly.  iter-3: no horizon / target_price / stop_price.
    session = await svc.create_session(
        app_name = "test-e2e",
        user_id  = "stockbot",
        state    = {
            "tick_id":         "tick-1",
            "as_of":           open_ts,
            "user:positions":  {},
            "user:thesis":     "",
            "final_orders": [
                {"ticker": "AAPL", "action": "BUY", "quantity": 5.0, "est_price": 200.0},
            ],
            "strategist_decision": {
                "decision_tag": "buy_aapl",
                "reasoning":    "Strong FCF + insider buying",
                "confidence":   0.8,
                "thesis":       "FCF-driven AI infrastructure thesis",
                "stances": [
                    {
                        "ticker":    "AAPL",
                        "intent":    "buy",
                        "weight":    0.04,
                        "rationale": "Strong FCF + insider buying",
                    },
                ],
                "target_weights": {"AAPL": 0.04},
                "sell_reasons":   {},
            },
        },
    )

    # ── Run executor via ADK Runner ──────────────────────────────────────────
    message = genai_types.Content(
        parts=[genai_types.Part(text="run executor")],
        role="user",
    )

    # No try/except here — investigation confirmed no exception is raised by
    # ADK 1.32 for this test path.  Any exception that surfaces is a real
    # failure and must propagate; swallowing it would silence the very bugs
    # this test is designed to catch.
    async for _ in runner.run_async(
        user_id    = "stockbot",
        session_id = session.id,
        new_message = message,
    ):
        pass

    # ── Reload and assert ────────────────────────────────────────────────────
    reloaded = await svc.get_session(
        app_name   = "test-e2e",
        user_id    = "stockbot",
        session_id = session.id,
    )
    assert reloaded is not None, "session must exist after the run"

    # user:positions must contain the AAPL thesis written by the callback.
    user_positions = reloaded.state.get("user:positions")
    assert user_positions is not None, (
        "user:positions must be present in the reloaded session; "
        "_executor_thesis_writer_callback did not write it"
    )
    assert "AAPL" in user_positions, (
        "AAPL must be in user:positions after a BUY tick; "
        "the open stance was not processed by apply_stance_to_thesis"
    )
    aapl_thesis = user_positions["AAPL"]
    assert aapl_thesis["opened_price"] == pytest.approx(200.0), (
        "opened_price must match the FakeBroker fill price"
    )
    assert aapl_thesis["weight"] == pytest.approx(0.04)
    # iter-3: horizon / target_price / stop_price no longer exist in PositionThesis.
    assert "horizon"      not in aapl_thesis
    assert "target_price" not in aapl_thesis
    assert "stop_price"   not in aapl_thesis

    # user:thesis must contain the standing thesis string from the decision.
    user_thesis = reloaded.state.get("user:thesis")
    assert user_thesis == "FCF-driven AI infrastructure thesis", (
        f"user:thesis must be the decision.thesis string; got {user_thesis!r}"
    )

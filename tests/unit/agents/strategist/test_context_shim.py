"""Contract Rule 1 test for ``StrategistContextShim``.

The shim replaces ``_composite_before_callback`` (held-view +
evidence-view) on the Strategist LlmAgent.  The contract requires every
state write to ride on a yielded ``Event(actions=EventActions(state_delta=...))``
— callbacks cannot yield events (Rule 3), so the work has to live on a
``BaseAgent``.

This test wires the shim by itself (without the downstream LlmAgent) and
asserts that one event is emitted carrying the three expected keys with the
``temp:`` prefix mandated by Task 7's later edit.  It does NOT assert on
the rendered string content of the held-positions view — separate tests in
``test_held_view.py`` / ``test_evidence_view.py`` already cover formatting.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agents.strategist.context_shim import StrategistContextShim


@pytest.fixture
def populated_state() -> dict:
    """Build a session-state dict with the keys the shim needs to read.

    The shim reads ``user:positions``, ``portfolio``, ``tickers``, ``tick_id``,
    ``as_of``, and the four per-analyst ``*_evidence`` lists.  An empty
    ``user:positions`` dict is fine — the held-view renderer handles the flat-
    portfolio case.  The evidence lists are empty too — the evidence-view
    branch handles that path.
    """
    return {
        "tickers":              ["AAPL"],
        "tick_id":              "test-tick-1",
        "as_of":                datetime(2026, 5, 20, 13, 30, tzinfo=UTC),
        "user:positions":       {},
        "portfolio":            {"cash": 100_000.0, "positions": {}},
        "technical_evidence":   [],
        "fundamental_evidence": [],
        "news_evidence":        [],
        "smart_money_evidence": [],
    }


def test_shim_yields_one_event_with_temp_prefixed_keys(populated_state: dict) -> None:
    """Run the shim and assert exactly one event carrying the three context keys."""
    shim = StrategistContextShim()

    # Fake InvocationContext — just needs invocation_id + a session whose
    # .state attribute is our populated dict.  ADK's BaseAgent contract only
    # touches ctx.invocation_id and ctx.session.state during _run_async_impl.
    fake_session = MagicMock()
    fake_session.state = populated_state
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-1"
    fake_ctx.session = fake_ctx.session_service = fake_session

    async def _drain() -> list:
        events: list = []
        async for ev in shim._run_async_impl(fake_ctx):
            events.append(ev)
        return events

    events = asyncio.run(_drain())

    assert len(events) == 1, (
        f"StrategistContextShim must yield exactly one event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    expected_keys = {
        "temp:strategist_mode",
        "temp:held_positions_view",
        "temp:ticker_evidence",
        "temp:ticker_evidence_objects",
        # Past-trades memory addition — rendered from user:closed_trades_log
        # (empty-state copy when no closes have happened yet this run).
        "temp:recent_trades_view",
        # Spec B Band 2: shim bridges user:thesis → thesis for the prompt placeholder.
        "thesis",
        # Seeded empty so the RetryingAgentWrapper's schema-error feedback
        # slot resolves on the first attempt (overwritten on schema retry).
        "temp:_last_schema_error",
        # Task 9: selective-output flag — "True" on first tick, "False" thereafter.
        "temp:first_tick_flag",
    }
    assert set(delta.keys()) == expected_keys, (
        f"state_delta keys mismatch: {set(delta.keys())} vs {expected_keys}"
    )
    # held-view always produces *some* string (empty portfolio -> sentinel msg).
    assert isinstance(delta["temp:held_positions_view"], str)
    # evidence-view list is empty (no per-ticker evidence in the fixture) but
    # still serialised as a list/string pair.
    assert isinstance(delta["temp:ticker_evidence"], str)
    assert isinstance(delta["temp:ticker_evidence_objects"], list)
    # recent-trades view is always a string — explicit empty-state copy when
    # no closes have happened yet.
    assert isinstance(delta["temp:recent_trades_view"], str)


def test_shim_accepts_iso_string_as_of(populated_state: dict) -> None:
    """state["as_of"] arriving as an ISO-8601 string (from DatabaseSessionService
    JSON round-trip) must not raise AsOfRequiredError.

    Locks in the fix to context_shim that delegates ISO parsing to resolve_as_of
    rather than the defunct ``isinstance(as_of_raw, datetime)`` pre-filter.
    """
    shim = StrategistContextShim()

    iso_as_of = "2026-05-20T13:30:00+00:00"
    populated_state["as_of"] = iso_as_of    # replace datetime with ISO string

    fake_session = MagicMock()
    fake_session.state = populated_state
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-iso"
    fake_ctx.session = fake_ctx.session_service = fake_session

    async def _drain() -> list:
        events: list = []
        async for ev in shim._run_async_impl(fake_ctx):
            events.append(ev)
        return events

    # Must not raise — previously the isinstance guard fell through to the
    # wall-clock branch which raised under STOCKBOT_STRICT_AS_OF=1.
    events = asyncio.run(_drain())
    assert len(events) == 1, "Shim must still yield one event with an ISO-string as_of"


def test_shim_bridges_user_thesis_to_bare_thesis_key(populated_state: dict) -> None:
    """Spec B Band 2: shim must read ``user:thesis`` and write it as ``thesis``.

    The strategist prompt template uses the ``{thesis}`` placeholder; ADK's
    ``inject_session_state`` resolves that from ``state["user:thesis"]``.  After
    Spec B, the persisted value lives at ``state["user:thesis"]``.  The shim
    bridges the two so the prompt fills correctly without a bare-key seed in
    the runner.

    This test covers the warm-start case: ``user:thesis`` is populated.
    """
    shim = StrategistContextShim()

    populated_state["user:thesis"] = "AAPL momentum trade — target $225"

    fake_session = MagicMock()
    fake_session.state = populated_state
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-thesis"
    fake_ctx.session = fake_ctx.session_service = fake_session

    async def _drain() -> list:
        events: list = []
        async for ev in shim._run_async_impl(fake_ctx):
            events.append(ev)
        return events

    events = asyncio.run(_drain())
    delta = events[0].actions.state_delta

    # The bare-key ``thesis`` must be present so ADK can resolve ``{thesis}``
    # in the strategist instruction template.
    assert "thesis" in delta, "state_delta must carry 'thesis' for the prompt placeholder"
    assert delta["thesis"] == "AAPL momentum trade — target $225", (
        f"thesis in state_delta should mirror user:thesis; got {delta['thesis']!r}"
    )


def test_shim_thesis_cold_start_defaults_to_empty_string(populated_state: dict) -> None:
    """Spec B Band 2: when ``user:thesis`` is absent (first tick / cold start),
    the shim must write an empty string to ``thesis`` so the prompt placeholder
    does not raise ``KeyError``.
    """
    shim = StrategistContextShim()

    # Ensure user:thesis is not present in the state.
    populated_state.pop("user:thesis", None)

    fake_session = MagicMock()
    fake_session.state = populated_state
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-cold"
    fake_ctx.session = fake_ctx.session_service = fake_session

    async def _drain() -> list:
        events: list = []
        async for ev in shim._run_async_impl(fake_ctx):
            events.append(ev)
        return events

    events = asyncio.run(_drain())
    delta = events[0].actions.state_delta

    assert "thesis" in delta, "state_delta must carry 'thesis' even on cold start"
    assert delta["thesis"] == "", (
        f"cold-start thesis must be empty string; got {delta['thesis']!r}"
    )


# ---------------------------------------------------------------------------
# Task 9 — selective-output flag + thesis staleness
# ---------------------------------------------------------------------------

def test_first_tick_sets_flag_true() -> None:
    """On the first tick (active_stances_initialised=False), first_tick_flag is 'True'.

    ``temp:first_tick_flag`` is rendered by ``StrategistContextShim.render()``
    from the durable boolean ``user:active_stances_initialised``.  When the
    flag is absent or False, this IS the first tick of the window, so the
    rendered value should be "True".
    """
    from agents.strategist.context_shim import StrategistContextShim

    state = {"user:positions": {}, "user:active_stances_initialised": False}
    shim = StrategistContextShim()
    rendered = shim.render(state)
    assert rendered["temp:first_tick_flag"] == "True"


def test_subsequent_tick_sets_flag_false() -> None:
    """Once initialised, first_tick_flag is 'False'.

    After the first successful tick the enricher sets
    ``user:active_stances_initialised = True``.  On every tick thereafter
    ``render()`` should produce ``"False"`` for ``temp:first_tick_flag``.
    """
    from agents.strategist.context_shim import StrategistContextShim

    state = {"user:positions": {}, "user:active_stances_initialised": True}
    shim = StrategistContextShim()
    rendered = shim.render(state)
    assert rendered["temp:first_tick_flag"] == "False"


def test_held_view_shows_thesis_staleness() -> None:
    """Held positions view shows ticks since the thesis last updated.

    The rendered ``temp:held_positions_view`` must contain the ticker symbol
    and either an explicit "N ticks" staleness string or the word "stale".
    Staleness is computed as ``current_tick_index - thesis_last_updated_tick``.
    """
    from agents.strategist.context_shim import StrategistContextShim

    state = {
        "user:positions": {
            "AAPL": {
                "rationale":                "iPhone launch",
                "opened_price":             210.0,
                "opened_at":                "2026-01-15T13:30:00+00:00",
                "thesis_last_updated_tick": 1,
            }
        },
        "user:current_tick_index": 5,
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)
    held = rendered["temp:held_positions_view"]
    assert "AAPL" in held
    assert "4 ticks" in held or "stale" in held.lower()


def test_held_view_omits_horizon_target_stop() -> None:
    """Held view must not mention horizon/target_price/stop_price.

    iter-3 removed those fields from ``PositionThesis``.  The held view
    rendered by ``context_shim`` must not leak them back into the prompt.
    """
    from agents.strategist.context_shim import StrategistContextShim

    state = {
        "user:positions": {
            "AAPL": {
                "rationale":    "iPhone launch",
                "opened_price": 210.0,
                "opened_at":    "2026-01-15T13:30:00+00:00",
            }
        },
        "user:current_tick_index": 1,
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)
    held = rendered["temp:held_positions_view"]
    assert "horizon" not in held.lower()
    assert "target" not in held.lower()
    assert "stop" not in held.lower()

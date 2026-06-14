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
        # Rendered (collapsed) memory buffer — injected as temp key so
        # collapse_repeat_buffer_entries runs at injection time rather than
        # having ADK stringify the raw list from state["memory_buffer"].
        "temp:memory_buffer",
        # A-086: the bare "thesis" key is NOT emitted here — the strategist
        # prompt uses {user:thesis?} which ADK resolves from state["user:thesis"]
        # directly.  No bridge into a bare key is needed or permitted.
        # Seeded empty so the RetryingAgentWrapper's schema-error feedback
        # slot resolves on the first attempt (overwritten on schema retry).
        "temp:_last_schema_error",
        # Task 9: selective-output flag — "True" on first tick, "False" thereafter.
        "temp:first_tick_flag",
        # Change 3: first-tick-only preamble.  Full guidance on tick 0 (empty
        # thesis book must be populated); empty string on iterative ticks so
        # the placeholder adds zero tokens.
        "temp:first_tick_preamble",
        # iter-3 deployment-readout: one-line live summary of invested fraction
        # vs the 70–80% target band, injected into ## Deployment posture so the
        # model sees its actual exposure alongside the target guidance.
        "temp:deployment_readout",
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


def test_shim_does_not_bridge_thesis_into_state_delta(populated_state: dict) -> None:
    """A-086: shim must NOT write a bare ``thesis`` key into state_delta.

    After A-086, the strategist prompt template uses the ``{user:thesis?}``
    placeholder.  ADK's ``inject_session_state`` resolves that directly from
    ``state["user:thesis"]``; no bridge from the shim is needed or permitted.
    Emitting a bare ``thesis`` key would be a regression to the old pattern.

    This test covers the warm-start case: ``user:thesis`` is populated.  The
    value must NOT appear under the bare ``thesis`` key in state_delta.
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

    assert len(events) == 1, f"Shim must yield exactly one event; got {len(events)}"

    delta = events[0].actions.state_delta

    # The bare ``thesis`` key must NOT be present in state_delta — ADK resolves
    # {user:thesis?} from state["user:thesis"] directly.
    assert "thesis" not in delta, (
        "state_delta must NOT carry bare 'thesis' key; the prompt uses {user:thesis?} "
        "which ADK resolves from state['user:thesis'] without a shim bridge"
    )

    # Positive companion: the canonical user-scoped key remains readable from
    # session state — ADK resolves the {user:thesis?} placeholder from there
    # directly, so the warm-start value must still be present.
    assert fake_session.state.get("user:thesis") == "AAPL momentum trade — target $225", (
        "user:thesis must remain readable from session state after the shim runs"
    )


def test_shim_cold_start_does_not_bridge_thesis_key(populated_state: dict) -> None:
    """A-086: on cold start (no ``user:thesis``), shim must NOT write a bare ``thesis`` key.

    The optional ``{user:thesis?}`` placeholder in the strategist prompt resolves
    to an empty string when ``state["user:thesis"]`` is absent — ADK handles the
    cold-start case natively.  The shim must not emit a bare ``thesis`` key for
    any reason; doing so would re-introduce the legacy bare-key pattern.
    """
    shim = StrategistContextShim()

    # Ensure user:thesis is not present in the state (cold start / first tick).
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

    assert len(events) == 1, f"Shim must yield exactly one event; got {len(events)}"

    delta = events[0].actions.state_delta

    # The bare ``thesis`` key must NOT appear — the optional {user:thesis?}
    # placeholder handles the empty case at the ADK layer, not the shim layer.
    assert "thesis" not in delta, (
        "state_delta must NOT carry bare 'thesis' key on cold start; "
        "the optional {user:thesis?} placeholder resolves to empty string natively"
    )

    # Positive companion: confirm the cold-start condition is real — user:thesis
    # is absent, which is exactly the case the optional {user:thesis?} placeholder
    # resolves to an empty string (no KeyError) at the ADK layer.
    assert fake_session.state.get("user:thesis") is None, (
        "cold-start test must actually exercise the missing-user:thesis path"
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
    from broker.portfolio import Portfolio

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": False,
        # Portfolio must always be present in state — from_state_value raises
        # on None (audit fix: silent-empty portfolio is a contract violation).
        "portfolio":                       Portfolio(cash=0.0).model_dump(mode="json"),
    }
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
    from broker.portfolio import Portfolio

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": True,
        "portfolio":                       Portfolio(cash=0.0).model_dump(mode="json"),
    }
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
    from broker.portfolio import Portfolio

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
        "portfolio":               Portfolio(cash=0.0).model_dump(mode="json"),
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
    from broker.portfolio import Portfolio

    state = {
        "user:positions": {
            "AAPL": {
                "rationale":    "iPhone launch",
                "opened_price": 210.0,
                "opened_at":    "2026-01-15T13:30:00+00:00",
            }
        },
        "user:current_tick_index": 1,
        "portfolio":               Portfolio(cash=0.0).model_dump(mode="json"),
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)
    held = rendered["temp:held_positions_view"]
    assert "horizon" not in held.lower()
    assert "target" not in held.lower()
    assert "stop" not in held.lower()


def test_context_shim_ignores_bare_positions_key() -> None:
    """The shim must read user:positions exclusively — no bare-key fallback.

    Audit finding A-014: external readers used to silently fall back to
    the bare ``positions`` state key (a legacy in-tick bridge that has since
    been removed), which would persist stale BUY->SELL intermediate state
    across ticks.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Portfolio

    shim = StrategistContextShim()
    state = {
        "user:positions":                  {},
        "positions":                       {"AAPL": {"rationale": "bridge-leak"}},
        "portfolio":                       Portfolio(cash=1.0).model_dump(mode="json"),
        "user:active_stances_initialised": True,
    }

    out = shim.render(state)

    # The bridge value must NOT appear in the rendered held-view.
    assert "bridge-leak" not in out["temp:held_positions_view"]


# ---------------------------------------------------------------------------
# Change 3 — first-tick-only preamble (temp:first_tick_preamble)
# ---------------------------------------------------------------------------

def test_first_tick_preamble_non_empty_on_first_tick() -> None:
    """On the first tick the preamble must contain meaningful guidance.

    The full ``FIRST_TICK_PREAMBLE`` block is injected under
    ``temp:first_tick_preamble`` when ``user:active_stances_initialised``
    is absent or False.  The strategist must receive explicit instruction to
    populate the thesis book — without it the model has no guidance on how
    to handle an empty-portfolio cold start.

    Positive assertions: the preamble must mention the baseline-tick context
    and guide the model to buy / update / avoid no_action as a default.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Portfolio

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": False,
        "portfolio":                       Portfolio(cash=0.0).model_dump(mode="json"),
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)

    preamble = rendered["temp:first_tick_preamble"]

    # Must be non-empty — the model needs guidance on the cold-start path.
    assert preamble, (
        "temp:first_tick_preamble must be non-empty on the first tick "
        "(user:active_stances_initialised=False)"
    )

    # Must reference the baseline / cold-start context.
    assert "baseline" in preamble.lower() or "first" in preamble.lower(), (
        "First-tick preamble must mention the baseline/first-tick context"
    )


def test_first_tick_preamble_empty_on_iterative_tick() -> None:
    """On iterative ticks the preamble must be an empty string.

    The ``## Mode`` section and ``## Deployment posture`` already cover the
    incremental framing.  Injecting the preamble again would repeat the same
    guidance and waste tokens.  An empty string causes the
    ``{temp:first_tick_preamble}`` placeholder to render to nothing.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Portfolio

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": True,   # iterative tick
        "portfolio":                       Portfolio(cash=0.0).model_dump(mode="json"),
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)

    preamble = rendered["temp:first_tick_preamble"]

    # Must be empty — no duplicate guidance on iterative ticks.
    assert preamble == "", (
        f"temp:first_tick_preamble must be an empty string on iterative ticks "
        f"(user:active_stances_initialised=True); got {preamble!r}"
    )


# ---------------------------------------------------------------------------
# iter-3 — deployment readout (temp:deployment_readout)
# ---------------------------------------------------------------------------
#
# The readout is injected into ``## Deployment posture`` so the model sees
# its live invested fraction alongside the 70–80% target band prose.
# Tests cover: first-tick / empty portfolio, below-band, in-band, above-band,
# and that the directional cue is always present and correct.
# ---------------------------------------------------------------------------

def test_deployment_readout_empty_portfolio_first_tick() -> None:
    """An empty portfolio (first tick, no positions) must produce a 0%-invested readout.

    The readout must explicitly state 0% invested, 100% idle cash, and that
    the portfolio is BELOW the target band — so the model knows to open
    positions rather than defaulting to cash.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Portfolio

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": False,
        "portfolio":                       Portfolio(cash=100_000.0).model_dump(mode="json"),
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)

    readout = rendered["temp:deployment_readout"]

    # Must be a non-empty string — silent blank is a failure mode.
    assert readout, "temp:deployment_readout must be non-empty even on first tick"

    # Must show 0% invested (no positions open).
    assert "0%" in readout, (
        f"Empty-portfolio readout must show 0% invested; got: {readout!r}"
    )

    # Must show 100% idle cash.
    assert "100%" in readout, (
        f"Empty-portfolio readout must show 100% idle cash; got: {readout!r}"
    )

    # Must flag BELOW band so the model knows to open positions.
    assert "BELOW" in readout, (
        f"Empty-portfolio readout must contain 'BELOW' directional cue; got: {readout!r}"
    )


def test_deployment_readout_below_band() -> None:
    """A portfolio at ~51% invested must report BELOW the 70–80% target band.

    Uses a concrete scenario matching the iter-2 observed average: 6 positions
    each holding ~8.5% weight, totalling ~51% invested.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Position, Portfolio

    # Build a portfolio with 6 positions at 8.5% weight each ≈ 51% invested.
    # Total NAV = £100k; each position is ~£8,500.
    nav = 100_000.0
    pos_value = 8_500.0
    n_pos = 6
    cash = nav - (pos_value * n_pos)   # £49,000

    positions = {
        f"TICK{i}": Position(quantity=100.0, avg_cost=85.0, last_price=85.0)
        for i in range(n_pos)
    }
    portfolio = Portfolio(cash=cash, positions=positions)

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": True,
        "portfolio":                       portfolio.model_dump(mode="json"),
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)

    readout = rendered["temp:deployment_readout"]

    # Invested fraction = 51%; directional cue must be BELOW.
    assert "BELOW" in readout, (
        f"51%-invested portfolio must show BELOW-band cue; got: {readout!r}"
    )

    # The pp gap must be present (70 − 51 = 19pp).
    assert "19pp" in readout, (
        f"Readout must state the 19pp gap below band; got: {readout!r}"
    )

    # Idle cash must be mentioned — it is the actionable insight (deploy it).
    assert "idle cash" in readout, (
        f"Below-band readout must mention idle cash; got: {readout!r}"
    )


def test_deployment_readout_within_band() -> None:
    """A portfolio at ~75% invested must report WITHIN the target band.

    Within-band readout must contain WITHIN and must NOT contain BELOW or ABOVE.
    """
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Position, Portfolio

    # 6 positions at ~12.5% each ≈ 75% invested.
    nav = 100_000.0
    pos_value = 12_500.0
    n_pos = 6
    cash = nav - (pos_value * n_pos)   # £25,000

    positions = {
        f"TICK{i}": Position(quantity=100.0, avg_cost=125.0, last_price=125.0)
        for i in range(n_pos)
    }
    portfolio = Portfolio(cash=cash, positions=positions)

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": True,
        "portfolio":                       portfolio.model_dump(mode="json"),
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)

    readout = rendered["temp:deployment_readout"]

    assert "WITHIN" in readout, (
        f"75%-invested portfolio must show WITHIN-band cue; got: {readout!r}"
    )
    assert "BELOW" not in readout, (
        f"Within-band readout must not contain BELOW; got: {readout!r}"
    )
    assert "ABOVE" not in readout, (
        f"Within-band readout must not contain ABOVE; got: {readout!r}"
    )


def test_deployment_readout_above_band() -> None:
    """A portfolio at ~90% invested must report ABOVE the target band."""
    from agents.strategist.context_shim import StrategistContextShim
    from broker.portfolio import Position, Portfolio

    # 6 positions at ~15% each = 90% invested.
    nav = 100_000.0
    pos_value = 15_000.0
    n_pos = 6
    cash = nav - (pos_value * n_pos)   # £10,000

    positions = {
        f"TICK{i}": Position(quantity=100.0, avg_cost=150.0, last_price=150.0)
        for i in range(n_pos)
    }
    portfolio = Portfolio(cash=cash, positions=positions)

    state = {
        "user:positions":                  {},
        "user:active_stances_initialised": True,
        "portfolio":                       portfolio.model_dump(mode="json"),
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)

    readout = rendered["temp:deployment_readout"]

    assert "ABOVE" in readout, (
        f"90%-invested portfolio must show ABOVE-band cue; got: {readout!r}"
    )
    # The pp gap above band: 90 − 80 = 10pp.
    assert "10pp" in readout, (
        f"Readout must state the 10pp overshoot; got: {readout!r}"
    )

    assert "trim" in readout.lower(), (
        f"Above-band readout must advise trimming; got: {readout!r}"
    )

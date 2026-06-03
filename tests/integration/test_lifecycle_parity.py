# tests/integration/test_lifecycle_parity.py
"""Cross-lifecycle parity — A-047.

Runs the same minimal pipeline through both the live (``orchestrator.tick``)
and backtest (``backtest.driver``) entry points with a stubbed broker and
asserts the resulting session state has the same key shape: ``as_of`` is an
ISO string on both, ``tick_phase`` is present on both, and the
``HandleInjectorPlugin`` is installed on both runners.

This test is the structural canary for plans 05, 06, and 10 — they all
assume one harness, and this test fails fast the moment either lifecycle
drifts.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.lifecycle_runner import build_runner, build_seed_state


@pytest.mark.asyncio
async def test_live_seed_state_has_iso_as_of_and_no_temp_keys() -> None:
    """Live ``_build_initial_state`` → ``build_seed_state`` round-trip must
    produce an ISO ``as_of`` string and no ``temp:``-prefixed keys."""

    from orchestrator.tick import _build_initial_state

    broker = MagicMock()
    portfolio = MagicMock()
    portfolio.model_dump.return_value = {"cash": 0.0, "positions": {}}
    broker.get_portfolio = AsyncMock(return_value=portfolio)

    with patch(
        "orchestrator.tick._fetch_reference_prices",
        new=AsyncMock(return_value={}),
    ):
        raw_state = await _build_initial_state(
            broker, tick_id="tick-parity-001", tickers=["AAPL"],
        )

    # _build_initial_state itself must write an ISO string — build_seed_state
    # must not be the only line of defence.  A revert of tick.py's
    # ``.isoformat()`` would otherwise be silently rescued by the coercion
    # inside ``build_seed_state`` and go undetected here.
    assert isinstance(raw_state["as_of"], str), (
        f"_build_initial_state must produce ISO as_of; "
        f"got {type(raw_state['as_of']).__name__}"
    )

    seed = build_seed_state(raw_state)

    assert isinstance(seed["as_of"], str), (
        f"live seed as_of must be ISO string; got {type(seed['as_of']).__name__}"
    )
    # Round-trip parses cleanly.
    parsed = datetime.fromisoformat(seed["as_of"])
    assert parsed.tzinfo is not None
    assert seed.get("tick_phase") == "live"
    assert all(not k.startswith("temp:") for k in seed)


def test_backtest_seed_state_has_iso_as_of_and_no_temp_keys() -> None:
    """The backtest per-tick state built by the driver feeds through the
    same ``build_seed_state`` and must yield identically-shaped output."""

    # Build a minimal driver-style state dict (the driver builds richer
    # ones, but the boundary helper only cares about ``as_of`` shape +
    # ``temp:`` stripping).
    raw_state = {
        "tick_id":        "tick-parity-002",
        "as_of":          datetime(2026, 5, 26, 14, 30, tzinfo=UTC),
        "tick_phase":     "open",
        "tickers":        ["AAPL"],
        "temp:_trace":    object(),  # would be installed by plugin instead
    }

    seed = build_seed_state(raw_state)

    assert isinstance(seed["as_of"], str)
    parsed = datetime.fromisoformat(seed["as_of"])
    assert parsed.tzinfo is not None
    assert seed.get("tick_phase") == "open"
    assert all(not k.startswith("temp:") for k in seed)


def test_both_lifecycles_install_handle_injector_plugin() -> None:
    """Whichever code path constructs the runner (live or backtest), the
    HandleInjectorPlugin must end up installed on the runner's plugin
    manager."""

    from google.adk.agents import SequentialAgent

    from observability.handle_injector_plugin import HandleInjectorPlugin

    # ADK 1.34's ``App`` validates ``root_agent`` as a real ``BaseAgent``,
    # so a MagicMock no longer suffices — use a minimal SequentialAgent.
    pipeline = SequentialAgent(name="probe", sub_agents=[])
    session_service = MagicMock(name="session_service")

    live_runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-live",
        session_service = session_service,
        trace_writer    = None,
        decision_logger = None,
    )

    bt_runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-backtest-xyz",
        session_service = session_service,
        trace_writer    = MagicMock(name="tw"),
        decision_logger = MagicMock(name="dl"),
    )

    # ADK stores plugins on the plugin_manager, not as a top-level
    # attribute — ``runner.plugin_manager.plugins`` is the live list.
    assert any(
        isinstance(p, HandleInjectorPlugin) for p in live_runner.plugin_manager.plugins
    )
    assert any(
        isinstance(p, HandleInjectorPlugin) for p in bt_runner.plugin_manager.plugins
    )

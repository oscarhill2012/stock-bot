# tests/unit/orchestrator/test_handle_injector_install.py
"""Regression test for A-010 / A-047 — proves ``HandleInjectorPlugin``
is installed via ``App(plugins=…)`` and survives ``DatabaseSessionService``
rehydration.

The bug this test pins: prior to this plan, the live lifecycle never
installed the plugin, so every ``state.get("temp:_trace")`` lookup in
agents returned ``None`` (silent no-op).  Post-fix, both lifecycles use
``build_runner``, which always constructs the plugin.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from google.adk.agents import SequentialAgent

from orchestrator.lifecycle_runner import build_runner


def test_build_runner_always_installs_handle_injector_plugin() -> None:
    """Even when both handles are ``None``, the plugin must still be
    registered so the install path is structurally identical to the
    backtest path."""

    # ADK 1.34's ``App`` validates ``root_agent`` as a real ``BaseAgent``
    # (a MagicMock no longer passes), so use a minimal empty SequentialAgent.
    pipeline = SequentialAgent(name="probe", sub_agents=[])
    session_service = MagicMock(name="session_service")

    runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-test",
        session_service = session_service,
        trace_writer    = None,
        decision_logger = None,
    )

    # The runner's plugin list must contain exactly one
    # HandleInjectorPlugin instance (other plugins may be added in
    # future, but the handle injector is mandatory).
    from observability.handle_injector_plugin import HandleInjectorPlugin

    # ADK stores plugins on the plugin_manager, not as a top-level
    # attribute — ``runner.plugin_manager.plugins`` is the live list.
    plugin_list = runner.plugin_manager.plugins
    injectors = [p for p in plugin_list if isinstance(p, HandleInjectorPlugin)]
    assert len(injectors) == 1, (
        f"build_runner must install exactly one HandleInjectorPlugin; "
        f"got {len(injectors)} (plugins: {[type(p).__name__ for p in plugin_list]})"
    )


def test_build_runner_passes_handles_through_to_plugin() -> None:
    """When handles are supplied, the plugin must hold them by closure
    for ``before_run_callback`` to install onto the live invocation state."""

    # ADK 1.34's ``App`` validates ``root_agent`` as a real ``BaseAgent``
    # (a MagicMock no longer passes), so use a minimal empty SequentialAgent.
    pipeline = SequentialAgent(name="probe", sub_agents=[])
    session_service = MagicMock(name="session_service")
    tw = MagicMock(name="trace_writer")
    dl = MagicMock(name="decision_logger")

    runner = build_runner(
        agent           = pipeline,
        app_name        = "StockBot-test",
        session_service = session_service,
        trace_writer    = tw,
        decision_logger = dl,
    )

    from observability.handle_injector_plugin import HandleInjectorPlugin

    injector = next(
        p for p in runner.plugin_manager.plugins
        if isinstance(p, HandleInjectorPlugin)
    )
    assert injector._trace_writer is tw
    assert injector._decision_logger is dl

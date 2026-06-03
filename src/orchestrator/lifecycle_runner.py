# src/orchestrator/lifecycle_runner.py
"""Shared lifecycle-runner helpers used by both live and backtest ticks.

Live and backtest both build a per-tick ``Runner`` against the same
pipeline, against an ADK session whose ``state`` is JSON-serialised by
``DatabaseSessionService``.  This module owns the two invariants both
lifecycles must satisfy *identically* at session-creation time:

1. ``temp:``-prefixed keys must be stripped from the seed dict (ADK
   strips them anyway during persistence; passing them through silently
   wastes the round-trip and gives a false sense that the handle has
   been installed).
2. ``datetime`` values must be ISO-coerced (``DatabaseSessionService``
   serialises via ``json.dumps``, which raises on raw ``datetime``).

It also owns the canonical place to construct the ``Runner`` with
``HandleInjectorPlugin`` so both lifecycles share one install path.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def iso_coerce_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``state`` with ``datetime`` values ISO-stringified.

    Parameters
    ----------
    state:
        The raw seed dict produced by the lifecycle's ``_build_initial_state``
        (live) or per-tick state builder (backtest).  May contain
        ``datetime`` values under any key — most commonly ``"as_of"``.

    Returns
    -------
    dict[str, Any]
        A shallow copy of ``state`` where every ``datetime`` value has been
        replaced with its ``.isoformat()`` string.  All other values pass
        through unchanged.

    Notes
    -----
    Consumers downstream (``data.timeguard.resolve_as_of``) accept either
    ``datetime`` or ISO ``str``, so passing through pre-stringified values
    is safe.  We do **not** recurse into nested dicts — the only datetime
    fields we own at this layer are top-level (``as_of``); nested data
    structures are model_dump'd to JSON-safe shapes by their respective
    writers.
    """

    # Shallow copy with per-value coercion — keeps the helper pure (no
    # mutation of the caller's dict) and trivially testable.
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in state.items()
    }


def build_seed_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitised seed dict suitable for ``create_session(state=…)``.

    Strips ``temp:``-prefixed keys (ADK discards them at persistence time
    anyway; per-invocation handles like ``temp:_trace`` are injected by
    :class:`observability.handle_injector_plugin.HandleInjectorPlugin`'s
    ``before_run_callback`` instead) and ISO-coerces datetime values via
    :func:`iso_coerce_state`.

    Parameters
    ----------
    state:
        The raw per-tick state dict produced by either lifecycle's
        initial-state builder.

    Returns
    -------
    dict[str, Any]
        A JSON-serialisable dict safe to pass to
        ``DatabaseSessionService.create_session(state=…)``.
    """

    # Strip first, then coerce — strip is by key (cheap), coerce walks
    # values (slightly costlier).  Order doesn't matter for correctness
    # but this minimises the work iso_coerce_state does.
    stripped = {k: v for k, v in state.items() if not k.startswith("temp:")}
    return iso_coerce_state(stripped)


def build_runner(
    *,
    agent:           Any,
    app_name:        str,
    session_service: Any,
    trace_writer:    Any | None = None,
    decision_logger: Any | None = None,
    extra_plugins:   list[Any] | None = None,
) -> Any:
    """Construct an ADK ``Runner`` with ``HandleInjectorPlugin`` always installed.

    Both live (``orchestrator.tick.run_once``) and backtest
    (``backtest.driver.Driver.run_tick``) must build their per-tick
    runner through this helper so the observability-handle install
    pathway is structurally identical.  The plugin is registered even
    when both ``trace_writer`` and ``decision_logger`` are ``None`` —
    in that case its ``before_run_callback`` is a no-op, but the install
    path stays symmetric and future handles only need to be wired here.

    Parameters
    ----------
    agent:
        The root pipeline agent (typically a ``SequentialAgent``).
    app_name:
        ADK app_name partition.  Live uses ``"StockBot-{live,paper}"``;
        backtest uses ``"StockBot-backtest-{window_key}"``.
    session_service:
        Either an ``InMemorySessionService`` (tests) or a
        ``DatabaseSessionService`` (live / backtest).
    trace_writer:
        Optional :class:`observability.trace.TraceWriter`.  When ``None``
        the plugin does not install ``state["temp:_trace"]``.
    decision_logger:
        Optional :class:`backtest.decision_logger.DecisionLogger`.  When
        ``None`` the plugin does not install ``state["temp:_decision_logger"]``.
    extra_plugins:
        Optional list of additional ``BasePlugin`` instances appended
        after the handle injector.  Defaults to no extra plugins.

    Returns
    -------
    google.adk.Runner
        A ``Runner`` ready to ``run_async`` against a session created on
        ``session_service``.

    Notes
    -----
    Direct ``adk_session.state["temp:_…"] = …`` mutation *after*
    ``create_session`` is silently discarded by ADK (the runner calls
    ``get_session`` again, which rebuilds state from persisted storage
    and strips ``temp:`` keys).  This helper is the *only* sanctioned
    way to wire observability handles into a tick.
    """

    # Deferred import — keeps the module import-light for tests that
    # mock the ADK Runner entirely (and avoids forcing google-adk at
    # tooling-import time).
    from google.adk import Runner

    from observability.handle_injector_plugin import HandleInjectorPlugin

    # Always construct the plugin, even when both handles are None —
    # the install path must be structurally identical across lifecycles.
    handle_injector = HandleInjectorPlugin(
        trace_writer    = trace_writer,
        decision_logger = decision_logger,
    )

    plugins = [handle_injector]
    if extra_plugins:
        plugins.extend(extra_plugins)

    return Runner(
        agent           = agent,
        app_name        = app_name,
        session_service = session_service,
        plugins         = plugins,
    )

"""Per-invocation handle injector plugin for the backtest driver.

Background — the bug this module fixes
--------------------------------------

The backtest driver historically installed per-invocation observability
handles by mutating ``adk_session.state`` immediately after
``session_service.create_session(...)``::

    adk_session = await session_service.create_session(state=seed_state, ...)
    adk_session.state["temp:_trace"]           = tw    # ← broken
    adk_session.state["temp:_decision_logger"] = self._dl  # ← broken

That pattern is silently discarded by ADK.  ``Runner.run_async`` internally
calls ``session_service.get_session(...)`` for every invocation, which does
a fresh DB round-trip and returns a brand-new :class:`Session` object whose
``.state`` is rebuilt from persisted storage.  Because ADK strips
``temp:``-prefixed keys before persisting (by design — those keys are meant
to be invocation-scoped), the in-memory mutation made by the driver lives
only on the discarded original :class:`Session` object and is invisible to
every sub-agent.

The symptom: across the entire history of the backtest harness, every
run's ``traces/<tick>.json`` was an empty ``{}`` and every run's
``decisions/<tick>.json`` directory was empty — both reading
``state.get("temp:_*")`` and finding ``None``.  Both ``TraceWriter`` and
``DecisionLogger`` were dead code for the lifetime of the project.

The fix — ``BasePlugin.before_run_callback``
--------------------------------------------

ADK provides exactly the right hook for this: ``before_run_callback`` is
invoked by the plugin manager AFTER ``_get_or_create_session`` has resolved
the live :class:`InvocationContext.session` but BEFORE the root agent
executes.  Anything we mutate on ``invocation_context.session.state`` from
inside the callback is the SAME dict the sub-agents will read from for the
duration of this invocation, so the handles propagate cleanly.

The plugin is constructed per-tick by the driver and registered on the
runner via ``App(plugins=[…])`` (see ``orchestrator.lifecycle_runner``);
the per-tick :class:`TraceWriter` and per-run :class:`DecisionLogger` are
captured by closure on the plugin instance.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents.invocation_context import InvocationContext
from google.adk.plugins.base_plugin import BasePlugin

# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class HandleInjectorPlugin(BasePlugin):
    """Inject per-invocation observability handles into ``session.state``.

    Constructed by :class:`backtest.driver.Driver` once per tick with that
    tick's :class:`TraceWriter` and the run's :class:`DecisionLogger`.  The
    handles are stashed under ``temp:_trace`` and ``temp:_decision_logger``
    on the live invocation state dict so every sub-agent's
    ``state.get("temp:_…")`` lookup sees them.

    The plugin is intentionally a no-op for any handle that is ``None`` so
    the same install pathway can be reused outside the backtest harness
    (e.g.  live runs that only want one of the two handles, or none).
    """

    # Default plugin name — only relevant for ADK's plugin registry log
    # output.  A constant string keeps the registry deterministic across
    # ticks; the plugin instance itself is rebuilt per tick.
    _DEFAULT_NAME = "stockbot_handle_injector"

    def __init__(
        self,
        *,
        trace_writer:    Any | None = None,
        decision_logger: Any | None = None,
        name:            str        = _DEFAULT_NAME,
    ) -> None:
        """Capture the handles by closure.

        Parameters
        ----------
        trace_writer:
            Optional :class:`observability.trace.TraceWriter` instance.
            When non-``None``, installed as ``state["temp:_trace"]``.
        decision_logger:
            Optional :class:`backtest.decision_logger.DecisionLogger`
            instance.  When non-``None``, installed as
            ``state["temp:_decision_logger"]``.
        name:
            Plugin name forwarded to :class:`BasePlugin`.  Defaults to a
            stable identifier; rarely overridden.
        """

        super().__init__(name=name)

        # Stored as private attributes — never mutated after construction
        # so the plugin is safe to share across asyncio tasks within one
        # invocation (it isn't, in practice — Driver builds one per tick).
        self._trace_writer    = trace_writer
        self._decision_logger = decision_logger


    # ------------------------------------------------------------------
    # ADK hook
    # ------------------------------------------------------------------

    async def before_run_callback(
        self,
        *,
        invocation_context: InvocationContext,
    ) -> None:
        """Inject the handles onto the live invocation session state.

        Called by ADK's :class:`PluginManager` exactly once per invocation,
        after the session has been fetched/created and before the root
        agent's ``_run_async_impl`` runs.  Returning ``None`` lets the run
        proceed normally; returning a ``types.Content`` would short-circuit
        the invocation (we never want to).

        Parameters
        ----------
        invocation_context:
            The freshly-built per-invocation context.  Its ``session.state``
            attribute is the dict every sub-agent reads from via
            ``ctx.session.state`` or ``callback_context.state``.
        """

        # Single dict reference — ADK guarantees this is the live state
        # dict the sub-agents will see for the duration of the invocation.
        state = invocation_context.session.state

        # Conditional install — keep the plugin reusable when a caller
        # wires only one of the two handles (e.g.  paper-trading mode
        # without a DecisionLogger seed corpus, or a CI smoke run without
        # a TraceWriter).
        if self._trace_writer is not None:

            state["temp:_trace"] = self._trace_writer

        if self._decision_logger is not None:

            state["temp:_decision_logger"] = self._decision_logger

        # Explicit ``None`` return — required by the BasePlugin contract
        # ("None to proceed normally").  Returning anything else would
        # abort the invocation before the root agent ran.
        return None

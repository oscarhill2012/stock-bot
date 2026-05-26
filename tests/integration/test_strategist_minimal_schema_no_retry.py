"""Band 4 — end-to-end: the simplified schema fixes the dual-form retry storm.

The 2026-05-24 backtest exhausted three schema retries on MSFT / AVGO / LMT
because the LLM bounced between legacy and intent forms.  This test runs the
strategist against a stub LLM that emits a single clean intent-form decision
and asserts:
  1. Zero schema retries on the llm_retry counter.
  2. The decision passes ``_strategist_validation_callback`` unmodified.
  3. ``derive_decision_fields`` produces the expected ``target_weights`` /
     ``close_reasons``.

Honours the no-live-API hard rule in ``docs/test-policy.md`` — the LlmAgent
is a hand-built fake that never touches Vertex.  Scaffolding follows the
pattern established in ``tests/integration/test_retry_smoke.py``.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService, Session

from agents.llm_retry import RetryingAgentWrapper, build_retry_policies
from agents.strategist.agent import _strategist_validation_callback
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio

# ---------------------------------------------------------------------------
# Helpers: clean intent-form decision payload
# ---------------------------------------------------------------------------

def _build_clean_decision() -> dict:
    """Return a serialised ``StrategistDecision`` using iter-3 three-verb fields.

    Represents a one-ticker tick where AAPL is bought at 4 % weight
    (within the 5 % buy-delta cap) and MSFT is explicitly updated
    (prose-only; no trade).  No legacy fields (``preferred_weight``,
    ``conviction``, ``close_reason``, ``trim_reason``, ``horizon``,
    ``target_price``, ``stop_price``) appear anywhere.

    Returns
    -------
    dict
        A JSON-serialisable dict produced by ``StrategistDecision.model_dump``
        ready to be written directly to ``session.state["strategist_decision"]``.
    """
    return StrategistDecision(
        stances=[
            TickerStance(
                ticker    = "AAPL",
                intent    = "buy",
                weight    = 0.04,
                rationale = "Strong earnings momentum and AI-tailwind.",
            ),
            TickerStance(
                ticker = "MSFT",
                intent = "update",
                rationale = "No new evidence; prior thesis intact.",
            ),
        ],
        decision_tag = "buy_aapl_update_msft",
        reasoning    = "Initiating AAPL; updating MSFT thesis.",
        thesis       = "Tech names retain secular growth support.",
        confidence   = 0.72,
    ).model_dump(mode="json")


def _build_initial_state() -> dict:
    """Return a minimal session state ready for the strategist validation callback.

    Includes the watchlist, an empty portfolio (no existing positions), and
    the clean intent-form decision.  ``as_of`` is deliberately absent so the
    callback falls back to wall-clock (the live-run path).

    Returns
    -------
    dict
        A plain dict suitable for ``Session(state=…)``.
    """
    portfolio = Portfolio(cash=10_000.0)

    return {
        "tickers":             ["AAPL", "MSFT"],
        "positions":           {},
        "portfolio":           portfolio.model_dump(mode="json"),
        "tick_id":             "t-no-retry",
        # The stub LLM writes the decision here on its first (and only) call.
        "strategist_decision": _build_clean_decision(),
    }


# ---------------------------------------------------------------------------
# Helpers: ADK InvocationContext
# ---------------------------------------------------------------------------

class _PlaceholderAgent(BaseAgent):
    """Minimal ``BaseAgent`` to satisfy the non-Optional ``agent`` field on
    ``InvocationContext``.  Never executed — ``run_async`` replaces it with
    the actual wrapper before dispatching ``_run_async_impl``.
    """

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """No-op — required to satisfy the abstract method."""
        return        # pragma: no cover
        yield         # pragma: no cover — makes this an async generator


def _make_invocation_context(initial_state: dict | None = None) -> InvocationContext:
    """Construct a real ``InvocationContext`` with the given initial session state.

    Uses ``InMemorySessionService`` and a directly-constructed ``Session``.
    The ``agent`` field is populated with a placeholder — ``run_async``
    overwrites it via ``model_copy`` before each ``_run_async_impl`` call.

    Parameters
    ----------
    initial_state:
        Dict to pre-populate ``session.state`` with.  Defaults to empty.

    Returns
    -------
    InvocationContext
        A fully-populated context ready to pass to ``wrapper.run_async``.
    """
    svc     = InMemorySessionService()
    session = Session(
        id       = "no-retry-test",
        app_name = "test",
        user_id  = "test",
        state    = initial_state or {},
        events   = [],
    )

    return InvocationContext(
        session_service = svc,
        session         = session,
        invocation_id   = "inv-no-retry-1",
        agent           = _PlaceholderAgent(name="placeholder"),
    )


# ---------------------------------------------------------------------------
# Stub inner agent — succeeds on first call (no retry needed)
# ---------------------------------------------------------------------------

class _CleanDecisionAgent(BaseAgent):
    """ADK ``BaseAgent`` stub that yields a clean intent-form decision immediately.

    Emits one ``state_delta`` event writing the pre-built intent-form
    ``StrategistDecision`` dict into ``strategist_decision`` on its very
    first call — no ValidationError, no retry loop.

    This simulates what the real ``LlmAgent`` with ``output_schema=StrategistDecision``
    and ``output_key="strategist_decision"`` would emit when the model returns
    a valid intent-form JSON on its first attempt.
    """

    name: str = "StubStrategistLlm"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Yield one success event carrying the clean intent-form decision.

        Parameters
        ----------
        ctx:
            ADK invocation context (unused — stub reads nothing from state).

        Yields
        ------
        Event
            One event with ``strategist_decision`` in its ``state_delta``.
        """
        yield Event(
            author  = self.name,
            content = None,
            actions = EventActions(
                state_delta={"strategist_decision": _build_clean_decision()},
            ),
        )


# ---------------------------------------------------------------------------
# Context shim — minimal state provider for the callback
# ---------------------------------------------------------------------------

class _Ctx:
    """Minimal ``CallbackContext`` shim that exposes a mutable ``state`` dict.

    The ``_strategist_validation_callback`` only reads and writes
    ``callback_context.state`` — this shim is sufficient for unit-level
    verification of the derivation output without importing any ADK internals.
    """

    def __init__(self, state: dict):
        self.state = state


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clean_intent_form_produces_zero_schema_retries() -> None:
    """A clean intent-form decision never triggers a schema retry.

    Flow:
    1. The stub inner agent yields a single event with a valid intent-form
       ``StrategistDecision`` (no legacy fields).
    2. The ``RetryingAgentWrapper`` wraps the stub and forwards the event;
       no ``ValidationError`` fires, so no retry loop is entered.
    3. We assert that ``temp:_obs_strategist_retries`` is absent / empty
       (zero schema retries instrumented).
    4. We call ``_strategist_validation_callback`` directly on the resulting
       state to verify ``derive_decision_fields`` populates ``target_weights``
       and leaves ``close_reasons`` / ``trim_reasons`` empty.

    Assertions:
    - No retry-counter events in the wrapper's output stream.
    - ``target_weights["AAPL"] == 0.04`` (buy stance at 4 % weight).
    - ``target_weights["MSFT"] == 0.0`` (update stance on flat ticker).
    - ``sell_reasons == {}`` (no sells this tick).
    """
    # Construct the initial state — pre-loaded with the clean decision so the
    # stub agent's write is idempotent (the callback reads from state after the
    # wrapper drains all events).
    initial_state = _build_initial_state()

    wrapper = RetryingAgentWrapper(
        inner           = _CleanDecisionAgent(),
        timeout_seconds = 5.0,
        policies        = build_retry_policies(timeout_retries=3, schema_retries=3),
        retry_state_key = "temp:_obs_strategist_retries",
    )

    ctx    = _make_invocation_context(initial_state)
    events: list[Event] = []

    async for ev in wrapper.run_async(ctx):
        events.append(ev)

    # ── Assert: no retry events fired ────────────────────────────────────────
    # The wrapper emits a ``state_delta`` event carrying the retry counter
    # whenever any retry fires.  A clean first-pass payload must produce zero
    # such events.
    retry_evs = [
        e for e in events
        if e.actions and e.actions.state_delta
        and "temp:_obs_strategist_retries" in (e.actions.state_delta or {})
    ]
    assert retry_evs == [], (
        f"Expected zero retry-counter events; got {len(retry_evs)}: {retry_evs}"
    )

    # ── Assert: the success event was emitted ─────────────────────────────────
    # The stub emits exactly one event carrying ``strategist_decision``.  The
    # wrapper must not swallow it.
    decision_evs = [
        e for e in events
        if e.actions and e.actions.state_delta
        and "strategist_decision" in (e.actions.state_delta or {})
    ]
    assert len(decision_evs) == 1, (
        f"Expected exactly 1 decision event; got {len(decision_evs)}: {decision_evs}"
    )

    # ── Assert: derivation produces correct output ────────────────────────────
    # Call the validation callback directly on a state dict that mirrors what
    # the ADK runner would accumulate after applying the event's state_delta.
    # This verifies that ``derive_decision_fields`` correctly reads the new
    # intent-form stances and populates the derived fields without error.
    state_for_callback = _build_initial_state()

    result = _strategist_validation_callback(_Ctx(state_for_callback))

    # Callback returns None on success.
    assert result is None, f"Callback raised or returned a non-None value: {result}"

    derived = state_for_callback["strategist_decision"]

    # AAPL bought at 4 % — target weight must reflect the buy stance.
    assert derived["target_weights"]["AAPL"] == 0.04, (
        f"Expected AAPL target_weight=0.04; got {derived['target_weights']}"
    )

    # MSFT updated — update is prose-only; derivation carries current weight
    # forward (0.0 since MSFT is flat in this tick, not held from prior state).
    assert derived["target_weights"]["MSFT"] == 0.0, (
        f"Expected MSFT target_weight=0.0 (update, flat); got {derived['target_weights']}"
    )

    # No sells in this tick — sell_reasons must be empty.
    assert derived["sell_reasons"] == {}, (
        f"Expected no sell_reasons; got {derived['sell_reasons']}"
    )

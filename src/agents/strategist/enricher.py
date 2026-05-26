"""StrategistEnricher — turn the LLM's narrow output into the full StrategistDecision.

The wrapped Strategist LlmAgent emits the *narrow* :class:`StrategistLLMDecision`
shape (stances + decision_tag + reasoning + thesis + confidence) via ADK's
``output_key`` mechanism.  Downstream agents (RiskGate, Executor,
StrategistDecisionWriter) need the *full* :class:`StrategistDecision` shape,
which adds the derived ``target_weights`` / ``sell_reasons`` / ``update_reasons``
dicts.  This enricher runs that derivation and overwrites
``state["strategist_decision"]`` with the enriched dump.

Why this is a separate BaseAgent rather than an ``after_agent_callback`` on
the LlmAgent
------------------------------------------------------------------------
Originally the enrichment lived inside ``_strategist_validation_callback``
wired as the LlmAgent's ``after_agent_callback``.  That coupled the
enrichment to the LLM call's lifecycle and broke under schema-retry:

1. The :class:`RetryingAgentWrapper` buffers events from the inner LLM
   agent across attempts.
2. On a schema-validation failure (attempt 1), ADK still ran the
   ``after_agent_callback`` — but ``state["strategist_decision"]`` was
   ``None`` because no ``output_key`` write had landed.  The callback
   early-returned.
3. The wrapper retried.  On attempt 2 the LLM succeeded and its
   ``output_key`` write put the *narrow* shape in state.
4. The ``after_agent_callback`` did **not** re-fire for the successful
   attempt — so the narrow shape reached RiskGate ungenuine.
5. RiskGate read ``decision.target_weights``, got ``{}`` (Pydantic schema
   default), produced zero orders, no fills.
6. The executor's after_agent_callback then iterated ``decision.stances``,
   hit ``intent="open"`` with no fill, and tripped the
   ``open without fill price — caller bug`` assertion for every open stance.

Sequencing this enricher as a discrete BaseAgent after the wrapped LLM
agent in the StrategistBranch SequentialAgent makes the enrichment
unconditional: it runs once the wrapper has produced a successful LLM
response, regardless of how many retries were needed inside.

Contract Rule 1 (``docs/contract-invariants.md``)
-------------------------------------------------
Per Rule 1, agents do not mutate ``ctx.session.state`` directly — they
yield an ``Event`` whose ``actions.state_delta`` carries the change, and
the SessionService merges it.  This enricher follows that pattern.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.strategist.derivation import (
    StrategistContractViolation,
    TickContext,
    derive_decision_fields,
)
from agents.strategist.schema import StrategistDecision, StrategistLLMDecision
from broker.portfolio import Portfolio
from data.timeguard import resolve_as_of
from observability.terminal_log import emit_analyst_summary
from observability.trace import _trace_maybe

logger = logging.getLogger(__name__)


# ── pure helper ───────────────────────────────────────────────────────────────


def _coerce_portfolio(value: Portfolio | dict | None) -> Portfolio:
    """Return a Portfolio regardless of whether state stores it as an object or a dump.

    Args:
        value: ``Portfolio`` instance, dict produced by
            ``Portfolio.model_dump(mode="json")``, or ``None``.

    Returns:
        A ``Portfolio`` instance.  ``None`` yields an empty portfolio
        (cash=0.0, no positions) so cold-start ticks don't NPE here.
    """

    if isinstance(value, Portfolio):
        return value
    if value is None:
        return Portfolio(cash=0.0)
    return Portfolio.model_validate(value)


def _log_offending_decision(
    tick_id: str,
    decision: StrategistDecision | StrategistLLMDecision,
    violation: str,
) -> None:
    """Emit a structured error log capturing the strategist's offending output.

    Called immediately before every ``StrategistContractViolation`` raise so
    that the LLM's own reasoning + decision_tag survive in the run log even
    when ``STOCKBOT_TRACE=1`` is not set — without this, the raised exception
    carries only the bad ticker(s) and the rest of the decision context
    (decision_tag, reasoning, thesis) is lost when the tick aborts.
    """

    logger.error(
        "Strategist contract violation on tick=%s: %s | decision_tag=%r "
        "reasoning=%r thesis=%r confidence=%s n_stances=%d",
        tick_id,
        violation,
        decision.decision_tag,
        decision.reasoning,
        decision.thesis,
        decision.confidence,
        len(decision.stances),
    )


def validate_and_enrich(state: dict) -> dict | None:
    """Validate the narrow LLM output and return the enriched full-decision dump.

    Pure function — reads from ``state`` but does not mutate it.  The caller
    (the :class:`StrategistEnricher` BaseAgent or a unit test) is responsible
    for writing the returned dump back to state, either by yielding an Event
    with ``state_delta`` or by direct assignment in test fixtures.

    Parameters
    ----------
    state:
        Session state dict.  Reads ``strategist_decision`` (narrow LLM
        output, dict or pydantic instance), ``tickers`` (watchlist),
        ``portfolio`` (for current_weights), ``tick_id`` (for the
        derivation TickContext), and ``as_of`` (for the
        deterministic-replay timestamp).

    Returns
    -------
    dict | None
        The enriched :class:`StrategistDecision` as ``model_dump(mode="json")``,
        or ``None`` if no decision was emitted this tick (no-op short-circuit).

    Raises
    ------
    StrategistContractViolation
        On off-watchlist tickers, intent=None on any stance, or other
        watchlist-level contract breaches surfaced by
        :func:`derive_decision_fields`.
    """

    raw = state.get("strategist_decision")
    if not raw:
        # Cold-start path / ticks where the strategist did not run.
        return None

    # The LLM emits the narrow ``StrategistLLMDecision`` shape via output_key.
    # Accept any of: raw dict (production path), already-validated
    # ``StrategistLLMDecision``, or full ``StrategistDecision`` (replay/test
    # path) — the latter is a strict superset that still validates as the
    # narrow shape (Pydantic ignores extras by default).
    if isinstance(raw, dict):
        llm_decision = StrategistLLMDecision.model_validate(raw)
    elif isinstance(raw, StrategistDecision):
        llm_decision = StrategistLLMDecision(
            stances      = raw.stances,
            decision_tag = raw.decision_tag,
            reasoning    = raw.reasoning,
            thesis       = raw.thesis,
            confidence   = raw.confidence,
        )
    else:
        llm_decision = raw                                                          # already StrategistLLMDecision

    tickers: list[str] = state.get("tickers", []) or []
    portfolio = _coerce_portfolio(state.get("portfolio"))
    current_weights = portfolio.current_weights()
    tick_id: str = state.get("tick_id") or state.get("recorded_at", "unknown")

    # ── Pass 1: no off-watchlist tickers ─────────────────────────────────────
    # Active-stances contract: omission is read as an implicit hold by
    # derive_decision_fields — exhaustiveness is NOT enforced here.
    emitted = {s.ticker for s in llm_decision.stances}
    extras = [t for t in emitted if t not in tickers]
    if extras:
        msg = (
            f"Strategist included off-watchlist tickers: {extras}.  "
            f"Only emit stances for the watchlist."
        )
        _log_offending_decision(str(tick_id), llm_decision, msg)
        raise StrategistContractViolation(msg)

    # ── Pass 2: intent-based action tally for the success-summary log ────────
    # Four-verb model: buy / sell / update / no_action.
    action_counts: dict[str, int] = {"buy": 0, "sell": 0, "update": 0, "no_action": 0}
    for stance in llm_decision.stances:
        action = stance.intent or "no_action"
        action_counts[action] = action_counts.get(action, 0) + 1

    # ── Pass 3: derive decision fields ───────────────────────────────────────
    # All validation passed — derive ``target_weights`` / ``sell_reasons`` /
    # ``update_reasons`` from the stances.  Reads intent + weight directly;
    # raises ``StrategistContractViolation`` on intent=None or missing
    # reason on sell/update (no silent legacy fallback).
    raw_as_of = state.get("as_of")
    derivation_now = resolve_as_of(
        raw_as_of,
        allow_wallclock=True,
        site="strategist/enricher.validate_and_enrich",
    )
    ctx = TickContext(
        tick_id         = str(tick_id),
        decision_tag    = llm_decision.decision_tag,
        now             = derivation_now,
        current_weights = current_weights,
        watchlist       = tickers,
    )

    derived = derive_decision_fields(llm_decision.stances, ctx)

    # Construct the full StrategistDecision downstream agents consume.
    decision = StrategistDecision(
        stances        = llm_decision.stances,
        decision_tag   = llm_decision.decision_tag,
        reasoning      = llm_decision.reasoning,
        thesis         = llm_decision.thesis,
        confidence     = llm_decision.confidence,
        target_weights = derived.target_weights,
        sell_reasons   = derived.sell_reasons,
        update_reasons = derived.update_reasons,
    )

    # ── Per-tick success summary ─────────────────────────────────────────────
    # One concise INFO line so a run log shows whether the strategist is
    # actually committing capital or just hold-flat-ing the watchlist.
    nonzero_weight_sum = sum(w for w in derived.target_weights.values() if w > 0.0)
    logger.info(
        "Strategist tick=%s: buys=%d sells=%d updates=%d no_actions=%d"
        " | nonzero_weight_sum=%.4f decision_tag=%r confidence=%s",
        tick_id,
        action_counts["buy"],
        action_counts["sell"],
        action_counts["update"],
        action_counts["no_action"],
        nonzero_weight_sum,
        decision.decision_tag,
        decision.confidence,
    )

    decision_dump = decision.model_dump(mode="json")

    # Surface the strategist decision on the per-tick trace so downstream
    # inspection (decisions/, report/) and ad-hoc trace forensics can see
    # the full stance set, decision_tag, reasoning, and derived weights.
    # No-op unless state["temp:_trace"] is set by the backtest driver.
    _trace_maybe(state, "03_strategist", decision_dump)

    # ── Terminal summary row ──────────────────────────────────────────────────
    # Mirrors the pattern used by news/joiner.py and fundamental/joiner.py —
    # emit one singleton summary row ("strategist: 1/1 ✓ · 2.1s · 8.4k tok")
    # using the per-ticker call record written by the LlmAgent's
    # after_model_callback.  Strategist is a singleton agent — it passes
    # ``ticker="decision"`` (synthetic) to ``make_observability_callbacks``
    # — so we read exactly one key here.
    if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
        _strat_calls: list[dict] = []
        _rec = state.get("temp:_obs_strategist_call_decision")
        if _rec is not None:
            _strat_calls.append(_rec)

        _strat_retries: dict[str, int] = state.get("temp:_obs_strategist_retries") or {}
        emit_analyst_summary(
            "strategist",
            calls        = _strat_calls,
            ticker_count = 1,
            retries      = _strat_retries,
        )

    return decision_dump


# ── BaseAgent wrapper ─────────────────────────────────────────────────────────


class StrategistEnricher(BaseAgent):
    """ADK BaseAgent that runs the validate-and-enrich step after the LLM call.

    Wired as the third sub_agent of the ``StrategistBranch`` SequentialAgent
    (after ``StrategistContextShim`` and the wrapped LlmAgent), this agent
    reads the narrow ``StrategistLLMDecision`` that the LlmAgent wrote via
    ``output_key="strategist_decision"`` and yields a single ``Event`` whose
    ``state_delta`` overwrites ``state["strategist_decision"]`` with the full
    enriched dump.

    No-op short-circuit
    -------------------
    Yields nothing when ``state["strategist_decision"]`` is absent or falsy
    (e.g. a tick where the strategist did not run, or a forced cold-start
    path).  This matches :class:`StrategistDecisionWriter`'s behaviour.

    Contract violations
    -------------------
    Off-watchlist tickers / intent=None / missing reason on sell|update →
    raises :class:`StrategistContractViolation` from
    :func:`derive_decision_fields`.  The exception propagates up through
    the SequentialAgent → Runner → backtest driver, aborting the tick.
    Per the "silent failures are the recurring bug class" policy, this is
    intentional — a loud raise beats a corrupted decision sneaking
    downstream.
    """

    name: str = "StrategistEnricher"
    model_config: dict[str, Any] = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Read the narrow LLM output, derive enriched fields, yield one Event.

        Parameters
        ----------
        ctx:
            ADK invocation context.  Reads via ``ctx.session.state``.

        Yields
        ------
        Event
            Exactly one Event whose ``actions.state_delta`` carries the
            enriched ``strategist_decision`` dump.  Yields nothing when the
            strategist did not run this tick.
        """

        state = ctx.session.state

        enriched = validate_and_enrich(state)
        if enriched is None:
            return
            yield  # pragma: no cover — keeps the function an async generator

        # Contract Rule 1: yield a state_delta Event rather than mutating
        # ``ctx.session.state`` in place.  ``SessionService.append_event``
        # is the writer-of-record.
        #
        # Task 9: also flip ``user:active_stances_initialised`` to True so
        # subsequent ticks know a baseline stance set has been established.
        # This is a one-shot flip — once True it stays True for the rest of
        # the window.  ``StrategistContextShim.render()`` reads this to derive
        # ``temp:first_tick_flag`` ("True" only on the first tick).
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "strategist_decision":            enriched,
                "user:active_stances_initialised": True,
            }),
        )


def build_strategist_enricher() -> StrategistEnricher:
    """Factory for the StrategistEnricher — kept for symmetry with the
    other strategist factories (``build_strategist_decision_writer``).
    """

    return StrategistEnricher()

"""Persist TickerEvidence + per-ticker stances after each strategist tick.

The ``StrategistDecisionWriter`` is a lightweight ADK ``BaseAgent`` that runs
post-strategist (pipeline position 4) and owns two writes:

1. One ``TickerEvidenceRow`` per watchlist ticker, read from
   ``state["temp:ticker_evidence_objects"]`` — the cross-analyst aggregate
   produced by ``StrategistContextShim`` inside ``_build_strategist``
   (pipeline position 3).  Ownership moved here from the pre-strategist
   ``EvidenceWriter`` (position 2), which ran *before* that key existed and
   so silently wrote zero rows.  An empty list while the watchlist is
   non-empty now raises rather than degrading silently.
2. One ``TickerStanceRow`` per ticker, read from
   ``state["strategist_decision"]`` and written via ``save_ticker_stance``.
   The lifecycle action is read directly from ``stance.intent`` — no
   weight-comparison derivation.

It yields no events — it is a pure side-effectful write step wired into the
orchestrator pipeline.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from data.timeguard import resolve_as_of


class StrategistDecisionWriter(BaseAgent):
    """ADK agent that persists ticker evidence and per-ticker stances.

    Writes one ``TickerEvidenceRow`` per watchlist ticker from
    ``state["temp:ticker_evidence_objects"]`` every invocation (this write does
    not depend on a decision existing), then — only when
    ``state["strategist_decision"]`` is present — writes one
    ``TickerStanceRow`` per ticker via ``save_ticker_stance``.  The lifecycle
    action column is populated from ``stance.intent`` directly.

    The agent is a no-op (and yields nothing) when ``db_session`` is ``None``.
    It raises ``ValueError`` when ``temp:ticker_evidence_objects`` is empty
    while the watchlist is non-empty — the silent-degradation bug this write
    was moved here to fix.
    """

    name: str = "StrategistDecisionWriter"
    db_session: Any = None

    # Allow SQLAlchemy session and other non-Pydantic types as field values.
    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Drain ticker evidence and strategist stances from state and write them.

        Yields nothing; returns early on the ``db_session is None`` no-op
        short-circuit.  Raises ``ValueError`` when ticker-evidence is missing
        for a non-empty watchlist.
        """
        # No-op short-circuit: no database session available.
        if self.db_session is None:
            return
            yield  # pragma: no cover — generator gate

        state = ctx.session.state

        # Lazy imports keep the module importable without ADK/ORM in tests.
        from agents.strategist.schema import StrategistDecision
        from orchestrator.persistence import save_ticker_evidence, save_ticker_stance

        # Timestamp shared across every row written this invocation.  Prefer
        # state["as_of"] (the backtest replay clock) so replay is deterministic;
        # fall back to wall-clock only on live runs.
        recorded_at = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="decision_writer",
        )
        tick_id: str = state.get("tick_id", "unknown")

        # ── TickerEvidence persistence (moved here from the pre-strategist
        # EvidenceWriter, which ran BEFORE the context shim produced this key and
        # so silently wrote zero rows — the first-month-5 silent-degradation bug).
        # The shim always emits one dump per watchlist ticker, so an empty list
        # while the watchlist is non-empty means the aggregate never reached us:
        # raise loudly rather than no-op (loud-failure convention).
        ticker_evidence_objects = state.get("temp:ticker_evidence_objects", []) or []
        tickers: list[str] = state.get("tickers", []) or []

        if tickers and not ticker_evidence_objects:
            raise ValueError(
                "decision_writer: temp:ticker_evidence_objects is empty but the "
                f"watchlist has {len(tickers)} ticker(s) — the strategist context "
                "shim did not produce the aggregate. Refusing to silently drop "
                "ticker_evidence rows."
            )

        for te in ticker_evidence_objects:
            # Accept dicts (post-JSON state round-trip) or model instances.
            te_dict = te if isinstance(te, dict) else te.model_dump()
            save_ticker_evidence(
                self.db_session,
                tick_id=tick_id,
                ticker=te_dict["ticker"],
                aggregate=te_dict["aggregate"],
                weights=te_dict.get("weights", {}),
                # len(per_analyst) = number of analysts aggregated into this row.
                analyst_count=len(te_dict.get("per_analyst", {})),
                recorded_at=recorded_at,
            )

        # ── Stance persistence — only when a decision was emitted this tick.
        raw_decision = state.get("strategist_decision")
        if raw_decision:
            if isinstance(raw_decision, StrategistDecision):
                decision = raw_decision
            else:
                decision = StrategistDecision.model_validate(raw_decision)

            for stance in decision.stances:
                # intent=None is rejected upstream by derive_decision_fields; the
                # "update" fallback is an unreachable safety net.
                action = stance.intent or "update"
                save_ticker_stance(
                    self.db_session,
                    tick_id=tick_id,
                    decision_tag=decision.decision_tag,
                    recorded_at=recorded_at,
                    stance=stance.model_dump(mode="json"),
                    lifecycle_action=action,
                )

        self.db_session.commit()
        return
        yield  # required to make this a generator function


def build_strategist_decision_writer(db_session=None) -> StrategistDecisionWriter:
    """Factory that constructs a ``StrategistDecisionWriter`` bound to ``db_session``."""
    return StrategistDecisionWriter(db_session=db_session)

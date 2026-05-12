"""Persist AnalystEvidence + TickerEvidence rows after every tick.

``EvidenceWriter`` is a lightweight ADK ``BaseAgent`` that reads four
``{analyst}_evidence`` keys and ``ticker_evidence_objects`` from session state,
then calls the savers in ``orchestrator.persistence`` to write one
``AnalystEvidenceRow`` per evidence item and one ``TickerEvidenceRow`` per
ticker.  It yields no events — it is a pure side-effectful write step wired
into the orchestrator pipeline.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

# Maps session-state key → analyst label used in the database.
_EVIDENCE_KEYS = (
    ("technical_evidence", "technical"),
    ("fundamental_evidence", "fundamental"),
    ("sentiment_evidence", "sentiment"),
    ("smart_money_evidence", "smart_money"),
)


class EvidenceWriter(BaseAgent):
    """ADK agent that persists per-analyst and per-ticker evidence to the database.

    Reads ``state["{analyst}_evidence"]`` lists and
    ``state["ticker_evidence_objects"]`` from the invocation context, then
    writes one ``AnalystEvidenceRow`` per evidence item and one
    ``TickerEvidenceRow`` per ticker via ``save_analyst_evidence`` and
    ``save_ticker_evidence``.

    The agent is a no-op (and yields nothing) when ``db_session`` is ``None``.
    """

    name: str = "EvidenceWriter"
    db_session: Any = None

    # Allow SQLAlchemy session (and other non-Pydantic types) as field values.
    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Drain evidence dicts from state and write them to the database.

        Yields nothing; returns early when no database session is available.

        Args:
            ctx: The ADK invocation context providing access to session state.
        """
        # No-op short-circuit: no database session available.
        if self.db_session is None:
            return
            yield  # pragma: no cover — generator gate

        # Lazy import mirrors the style used in attribution/writer.py and
        # keeps this module importable in environments that stub out
        # orchestrator.persistence.
        from orchestrator.persistence import save_analyst_evidence, save_ticker_evidence

        state = ctx.session.state
        tick_id = state.get("tick_id", "unknown")

        # Persist one AnalystEvidenceRow per evidence item across all analysts.
        for state_key, analyst in _EVIDENCE_KEYS:
            for ev in state.get(state_key, []) or []:
                # Accept both Pydantic model instances and plain dicts — state
                # survives serialisation round-trips so either form may arrive.
                ev_dict = ev if isinstance(ev, dict) else ev.model_dump()

                save_analyst_evidence(
                    self.db_session,
                    tick_id=tick_id,
                    analyst=analyst,
                    ticker=ev_dict["ticker"],
                    verdict=ev_dict["verdict"],
                    features=ev_dict.get("features", {}),
                    feature_warnings=ev_dict.get("feature_warnings", []),
                )

        # Persist one TickerEvidenceRow per ticker's aggregated cross-analyst stance.
        for te in state.get("ticker_evidence_objects", []) or []:
            # Same dict-vs-Pydantic duality as above.
            te_dict = te if isinstance(te, dict) else te.model_dump()

            save_ticker_evidence(
                self.db_session,
                tick_id=tick_id,
                ticker=te_dict["ticker"],
                aggregate=te_dict["aggregate"],
                weights=te_dict.get("weights", {}),
                # Derive analyst_count from the per_analyst mapping present in
                # the TickerEvidence dict — len() gives the number of analysts
                # whose evidence was aggregated into this row.
                analyst_count=len(te_dict.get("per_analyst", {})),
            )

        # NOTE: no try/except wrapping the saver loop — a mid-loop failure leaves the
        # session dirty with flushed but uncommitted rows. The caller must catch the
        # exception and rollback. Acceptable pre-deployment; revisit when the
        # orchestrator gains error recovery.
        self.db_session.commit()
        return
        yield  # required to make this a generator function


def build_evidence_writer(db_session=None) -> EvidenceWriter:
    """Factory that constructs an ``EvidenceWriter`` bound to ``db_session``.

    Args:
        db_session: SQLAlchemy ``Session`` to use for persistence, or ``None``
            to create a no-op writer (useful for dry-run and test scenarios).

    Returns:
        A configured ``EvidenceWriter`` instance.
    """
    return EvidenceWriter(db_session=db_session)

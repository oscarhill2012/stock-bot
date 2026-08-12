"""Persist AnalystEvidence rows after every tick.

``EvidenceWriter`` is a lightweight ADK ``BaseAgent`` that reads five
``{analyst}_evidence`` keys (technical, fundamental, news, smart_money,
social) from session state, then calls the saver in
``orchestrator.persistence`` to write one ``AnalystEvidenceRow`` per
evidence item.  It yields no events — it is a pure side-effectful write
step wired into the orchestrator pipeline.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from data.timeguard import resolve_as_of

# Maps session-state key → analyst label used in the database.
# "sentiment_evidence" / "sentiment" renamed to "news_evidence" / "news" in Task 6.
# "social_evidence" / "social" added in Task 7 when SocialAnalyst became the 5th analyst.
_EVIDENCE_KEYS = (
    ("technical_evidence", "technical"),
    ("fundamental_evidence", "fundamental"),
    ("news_evidence", "news"),
    ("smart_money_evidence", "smart_money"),
    ("social_evidence", "social"),
)


class EvidenceWriter(BaseAgent):
    """ADK agent that persists per-analyst evidence to the database.

    Reads ``state["{analyst}_evidence"]`` lists from the invocation context,
    then writes one ``AnalystEvidenceRow`` per evidence item via
    ``save_analyst_evidence``.

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

        # Lazy import keeps this module importable in environments that
        # stub out orchestrator.persistence.
        from orchestrator.persistence import save_analyst_evidence

        state = ctx.session.state
        tick_id = state.get("tick_id", "unknown")

        # Resolve the evidence timestamp.  Prefer state["as_of"] (set by the
        # backtest driver) so all evidence rows for a tick share the historical
        # timestamp and are deterministic in replay.  Fall back to wall-clock
        # on live runs where as_of is absent.
        raw_as_of = state.get("as_of")
        evidence_recorded_at: datetime = resolve_as_of(
            raw_as_of,
            allow_wallclock=True,
            site="evidence_writer",
        )

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
                    recorded_at=evidence_recorded_at,
                    # Report cache's input hash — present for the LLM analysts
                    # (fundamental, news) only; ``.get`` defaults to None for
                    # deterministic analysts (technical, social, smart_money),
                    # which never populate this key on their evidence dicts.
                    input_hash=ev_dict.get("input_hash"),
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

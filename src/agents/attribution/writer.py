"""Persist every analyst signal per tick to the attribution_signals table."""
from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event


_SIGNAL_KEYS = (
    ("technical_signals", "technical"),
    ("fundamental_signals", "fundamental"),
    ("sentiment_signals", "sentiment"),
    ("smart_money_signals", "smart_money"),
)


class AttributionWriter(BaseAgent):
    name: str = "AttributionWriter"
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if self.db_session is None:
            return
            yield  # pragma: no cover — generator gate
        from orchestrator.persistence import save_attribution_signal

        state = ctx.session.state
        tick_id = state.get("tick_id", "unknown")
        for state_key, analyst in _SIGNAL_KEYS:
            for sig in state.get(state_key, []) or []:
                signal_dict = sig if isinstance(sig, dict) else sig.model_dump()
                save_attribution_signal(
                    self.db_session,
                    tick_id=tick_id,
                    analyst=analyst,
                    signal=signal_dict,
                )
        self.db_session.commit()
        return
        yield  # required to make this a generator


def build_attribution_writer(db_session=None) -> AttributionWriter:
    return AttributionWriter(db_session=db_session)

"""Rule 1 conformance test for ``TechnicalAnalyst``.

Asserts that the analyst yields a single ``Event`` whose
``actions.state_delta`` contains ``technical_verdicts``.  The previous
implementation wrote directly to ``ctx.session.state`` and used the
``return / yield`` no-op generator trick — that pattern is forbidden by
contract Rule 1 because ADK's ``SessionService.append_event`` only
persists state when the event carries a non-empty ``state_delta``.

See ``docs/contract-invariants.md`` §C-Rule 1.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.analysts.heuristics import TechnicalHeuristics, load_heuristics
from agents.analysts.technical.agent import TechnicalAnalyst


def _make_heuristics() -> TechnicalHeuristics:
    """Return the cached ``TechnicalHeuristics`` config section."""

    # Use the project-default heuristics — exercising the production config
    # avoids drift between the test and what the live analyst sees.
    return load_heuristics().technical


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK ``InvocationContext`` double.

    Mirrors the established pattern in ``tests/unit/test_social_analyst_run.py``
    — the analyst only touches ``ctx.session.state`` and
    ``ctx.invocation_id``, so a ``MagicMock`` carrying those two attributes
    is sufficient.
    """

    ctx = MagicMock()
    ctx.session.state = state
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_technical_yields_state_delta_with_verdicts() -> None:
    """``_run_async_impl`` must yield exactly one ``Event`` carrying
    ``technical_verdicts`` in ``actions.state_delta``.

    The verdict list shape is exercised elsewhere; this test only locks in
    the Rule 1 wiring.
    """

    analyst = TechnicalAnalyst(heuristics=_make_heuristics())

    # Empty ``technical_data`` is enough — the analyst still iterates the
    # ticker list and emits an empty list of verdicts.  Rule 1 fires
    # regardless of payload size.
    # A2.6: the fetch callback writes under the temp:-prefixed key; seed it here
    # so _run_async_impl finds an empty-but-present dict to iterate over.
    state: dict = {"tickers": ["AAPL"], "temp:technical_data": {}}
    ctx = _make_ctx(state)

    events: list = []
    async for event in analyst._run_async_impl(ctx):
        events.append(event)

    # Exactly one Event must be yielded — the state_delta carrier.
    assert len(events) == 1, (
        f"expected exactly one yielded Event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    assert "technical_verdicts" in delta, (
        "state_delta must carry the 'technical_verdicts' key per Rule 1"
    )
    assert isinstance(delta["technical_verdicts"], list)

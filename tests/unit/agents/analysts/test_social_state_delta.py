"""Rule 1 conformance test for ``SocialAnalyst``.

Asserts that the analyst yields a single ``Event`` whose
``actions.state_delta`` carries ``social_verdicts``.  See the technical
analyst counterpart and ``docs/contract-invariants.md``
§C-Rule 1 for the contract rationale.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.analysts.heuristics import SocialHeuristics
from agents.analysts.social.agent import SocialAnalyst


def _make_heuristics() -> SocialHeuristics:
    """Return a canonical ``SocialHeuristics`` fixture.

    The values mirror ``tests/unit/test_social_analyst_run.py`` so the
    two test files agree on what "default-ish" looks like for the social
    analyst.
    """

    return SocialHeuristics(
        score_neutral_band=0.05,
        score_to_magnitude_scale=2.0,
        high_volume_mentions=200,
        high_volume_magnitude_boost=0.15,
        confidence_volume_floor=30,
        platform_disagreement_threshold=0.3,
        confidence_base=0.4,
        confidence_boost_step=0.2,
        confidence_penalty_step=0.2,
        magnitude_cap=1.0,
    )


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal ADK ``InvocationContext`` double with mutable state."""

    ctx = MagicMock()
    ctx.session.state = state
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_social_yields_state_delta_with_verdicts() -> None:
    """``_run_async_impl`` must yield one ``Event`` whose ``state_delta``
    carries ``social_verdicts``."""

    analyst = SocialAnalyst(heuristics=_make_heuristics())

    # Empty payload — the agent emits an empty verdict list, but the
    # yielded Event must still appear (Rule 1 is shape-not-size).
    # A2.6: the fetch callback writes under the temp:-prefixed key; seed it here.
    state: dict = {"temp:social_data": {}}
    ctx = _make_ctx(state)

    events: list = []
    async for event in analyst._run_async_impl(ctx):
        events.append(event)

    assert len(events) == 1, (
        f"expected exactly one yielded Event; got {len(events)}"
    )

    delta = events[0].actions.state_delta
    assert "social_verdicts" in delta
    assert isinstance(delta["social_verdicts"], list)

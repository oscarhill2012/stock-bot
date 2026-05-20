"""Tests for SocialAnalyst._run_async_impl and the full fetch→run→evidence pipeline.

Exercises the BaseAgent body in isolation (without ADK's in-process runner)
and confirms that social_verdicts is correctly populated with per-ticker
verdict dicts that are compatible with make_evidence_callback.

Phase 7 (Task 2.11 / Fix K): social_data now carries the typed snapshot list
shape ``{"snapshots": [...], "aggregate_score": ...}``.  Tests updated to
use the new canonical shape.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.analysts.heuristics import SocialHeuristics
from agents.analysts.social.agent import SocialAnalyst


def _make_heuristics() -> SocialHeuristics:
    """Build a canonical SocialHeuristics fixture matching the config defaults."""
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
    """Build a minimal ADK InvocationContext mock with mutable session.state."""
    ctx = MagicMock()
    ctx.session.state = state
    ctx.invocation_id = "test-invocation"
    return ctx


def _aapl_social_payload() -> dict:
    """Phase 7 canonical social payload for AAPL — typed snapshot list."""
    return {
        "snapshots": [
            {
                "platform": "reddit",
                "mention_count": 50,
                "positive_score": 0.4,
                "negative_score": 0.1,
                "score": 0.3,
            },
            {
                "platform": "twitter",
                "mention_count": 30,
                "positive_score": 0.3,
                "negative_score": 0.2,
                "score": 0.1,
            },
        ],
        "aggregate_score": 0.2,
    }


@pytest.mark.asyncio
async def test_run_async_impl_writes_social_verdicts():
    """_run_async_impl reads social_data and writes social_verdicts as a list."""
    analyst = SocialAnalyst(heuristics=_make_heuristics())
    state = {
        "tickers": ["AAPL"],
        "social_data": {"AAPL": _aapl_social_payload()},
    }
    ctx = _make_ctx(state)

    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in analyst._run_async_impl(ctx):
        state.update(_event.actions.state_delta)

    assert "social_verdicts" in state
    assert isinstance(state["social_verdicts"], list)


@pytest.mark.asyncio
async def test_run_async_impl_verdict_has_ticker_key():
    """Each verdict dict in social_verdicts must have a 'ticker' key for make_evidence_callback."""
    analyst = SocialAnalyst(heuristics=_make_heuristics())
    state = {
        "social_data": {
            "MSFT": {
                "snapshots": [
                    {
                        "platform": "reddit",
                        "mention_count": 100,
                        "positive_score": 0.5,
                        "negative_score": 0.0,
                        "score": 0.5,
                    }
                ],
                "aggregate_score": 0.5,
            },
        },
    }
    ctx = _make_ctx(state)

    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in analyst._run_async_impl(ctx):
        state.update(_event.actions.state_delta)

    verdicts = state["social_verdicts"]
    assert len(verdicts) == 1
    assert verdicts[0]["ticker"] == "MSFT"
    # Confirm AnalystVerdict-compatible fields are present.
    assert "lean" in verdicts[0]
    assert "confidence" in verdicts[0]
    assert "magnitude" in verdicts[0]
    assert "is_no_data" in verdicts[0]


@pytest.mark.asyncio
async def test_run_async_impl_empty_social_data():
    """Empty social_data produces an empty social_verdicts list — no crash."""
    analyst = SocialAnalyst(heuristics=_make_heuristics())
    state = {"social_data": {}}
    ctx = _make_ctx(state)

    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in analyst._run_async_impl(ctx):
        state.update(_event.actions.state_delta)

    assert state["social_verdicts"] == []


@pytest.mark.asyncio
async def test_run_async_impl_no_data_ticker():
    """A ticker with an empty payload yields a no-data verdict (is_no_data=True)."""
    analyst = SocialAnalyst(heuristics=_make_heuristics())
    state = {
        "social_data": {
            # Phase 7 no-data shape from the updated fetch callback.
            "GOOG": {"snapshots": [], "aggregate_score": None},
        },
    }
    ctx = _make_ctx(state)

    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in analyst._run_async_impl(ctx):
        state.update(_event.actions.state_delta)

    verdicts = {v["ticker"]: v for v in state["social_verdicts"]}
    assert verdicts["GOOG"]["is_no_data"] is True
    assert verdicts["GOOG"]["lean"] == "neutral"


@pytest.mark.asyncio
async def test_after_callback_fires_and_writes_evidence():
    """Full pipeline smoke: _run_async_impl + after_agent_callback produces social_evidence.

    This test directly invokes the after-callback to confirm that the verdict
    list written by _run_async_impl is compatible with make_evidence_callback
    — verifying that C1 (after-callback never fires) and C2 (evidence never
    written) are both resolved.
    """
    from contract.evidence import AnalystEvidence

    analyst = SocialAnalyst(heuristics=_make_heuristics())

    state = {
        "tick_id": "test-tick-001",
        "tickers": ["AAPL"],
        "social_data": {"AAPL": _aapl_social_payload()},
    }
    ctx = _make_ctx(state)

    # Step 1 — run the agent body to populate social_verdicts.
    # Drain the generator and merge the yielded state_delta into ``state``
    # so the assertions below see the post-propagation view (Rule 1).
    async for _event in analyst._run_async_impl(ctx):
        state.update(_event.actions.state_delta)

    assert "social_verdicts" in state, "social_verdicts must be written by _run_async_impl"

    # Step 2 — invoke the after-callback directly (simulates ADK calling it
    # after _run_async_impl returns).
    after_cb = analyst.after_agent_callback
    cb_ctx = SimpleNamespace(state=state)
    result = after_cb(cb_ctx)

    # After-callback must return None (no re-prompt).
    assert result is None

    # social_evidence must now be present and contain valid AnalystEvidence records.
    assert "social_evidence" in state, "social_evidence must be written by after-callback"
    assert len(state["social_evidence"]) == 1
    ev = AnalystEvidence.model_validate(state["social_evidence"][0])
    assert ev.analyst == "social"
    assert ev.ticker == "AAPL"
    assert ev.tick_id == "test-tick-001"
    # Evidence record for a ticker with data must not be flagged as no-data.
    assert ev.verdict.is_no_data is False

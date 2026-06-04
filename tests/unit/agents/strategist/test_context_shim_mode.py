"""Chunk 5 — context-shim tests for the temp:strategist_mode emit.

The shim previously emitted exactly three temp keys —
``temp:held_positions_view``, ``temp:ticker_evidence``,
``temp:ticker_evidence_objects``.  Spec B adds a fourth key,
``temp:strategist_mode``, whose value is one of two literal templates:

  * COLD_START_MODE_TEMPLATE  — when ``len(state["user:positions"]) == 0``
  * INCREMENTAL_MODE_TEMPLATE — when there are held positions; the
    ``{N}`` placeholder is substituted with the count.

This module exercises the three contract points called out in the
spec at lines ~694-723: cold-start selection, incremental selection,
and N substitution.  We drive the shim through its public
``_run_async_impl`` so the test exercises the same code path the
runtime pipeline does.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agents.strategist.context_shim import StrategistContextShim
from agents.strategist.prompts import (
    COLD_START_MODE_TEMPLATE,
    INCREMENTAL_MODE_TEMPLATE,
)
from broker.portfolio import Portfolio

pytestmark = pytest.mark.asyncio


def _fake_ctx(state: dict[str, Any]) -> SimpleNamespace:
    """Build a minimal InvocationContext stand-in carrying ``state``.

    The shim only touches ``ctx.session.state`` and ``ctx.invocation_id``;
    a SimpleNamespace satisfies both attribute reads without dragging in
    the full ADK runtime.
    """

    return SimpleNamespace(
        session       = SimpleNamespace(state=state),
        invocation_id = "test-invocation",
    )


async def _run_shim_and_collect(state: dict[str, Any]) -> dict[str, Any]:
    """Run the shim and return the merged state_delta from its single event."""

    shim = StrategistContextShim()
    merged: dict[str, Any] = {}
    async for event in shim._run_async_impl(_fake_ctx(state)):
        merged.update(event.actions.state_delta or {})
    return merged


async def test_shim_emits_cold_start_mode_when_positions_empty() -> None:
    """``len(state['user:positions']) == 0`` selects the cold-start template."""

    state = {
        "user:positions":          {},
        "portfolio":               Portfolio(cash=1000.0).model_dump(mode="json"),
        "tickers":                 ["AVGO", "MSFT"],
        "tick_id":                 "tick_001",
        "as_of":                   datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    assert delta["temp:strategist_mode"] == COLD_START_MODE_TEMPLATE


async def test_shim_emits_incremental_mode_when_positions_present() -> None:
    """Non-empty ``user:positions`` selects the incremental template."""

    state = {
        "user:positions":          {
            "AVGO": {
                "ticker":                 "AVGO",
                "opened_at":              "2026-05-01T14:00:00+00:00",
                "opened_tick_id":         "tick_001",
                "opened_price":           100.0,
                "weight":                 0.05,
                "target_price":           120.0,
                "stop_price":              90.0,
                "catalyst":               "Q3 guidance",
                "horizon":                "swing",
                "rationale":              "Cloud-AI margin expansion",
                "last_reviewed_at":       "2026-05-01T14:00:00+00:00",
                "last_reviewed_decision": "open",
            },
        },
        "portfolio":               Portfolio(cash=950.0).model_dump(mode="json"),
        "tickers":                 ["AVGO"],
        "tick_id":                 "tick_005",
        "as_of":                   datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    # The incremental template carries ``{N}`` — substituted with the count.
    assert delta["temp:strategist_mode"] == INCREMENTAL_MODE_TEMPLATE.format(N=1)


async def test_shim_n_substitution_in_incremental_text() -> None:
    """``{N}`` must reflect the actual count, not a hardcoded value."""

    state = {
        "user:positions":          {
            "AVGO": {
                "ticker":                 "AVGO",
                "opened_at":              "2026-05-01T14:00:00+00:00",
                "opened_tick_id":         "tick_001",
                "opened_price":           100.0,
                "weight":                 0.05,
                "horizon":                "swing",
                "rationale":              "r1",
                "last_reviewed_at":       "2026-05-01T14:00:00+00:00",
                "last_reviewed_decision": "open",
            },
            "MSFT": {
                "ticker":                 "MSFT",
                "opened_at":              "2026-05-02T14:00:00+00:00",
                "opened_tick_id":         "tick_002",
                "opened_price":           400.0,
                "weight":                 0.04,
                "horizon":                "swing",
                "rationale":              "r2",
                "last_reviewed_at":       "2026-05-02T14:00:00+00:00",
                "last_reviewed_decision": "open",
            },
            "XOM": {
                "ticker":                 "XOM",
                "opened_at":              "2026-05-03T14:00:00+00:00",
                "opened_tick_id":         "tick_003",
                "opened_price":            110.0,
                "weight":                 0.03,
                "horizon":                "swing",
                "rationale":              "r3",
                "last_reviewed_at":       "2026-05-03T14:00:00+00:00",
                "last_reviewed_decision": "open",
            },
        },
        "portfolio":               Portfolio(cash=900.0).model_dump(mode="json"),
        "tickers":                 ["AVGO", "MSFT", "XOM"],
        "tick_id":                 "tick_010",
        "as_of":                   datetime(2026, 5, 10, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    # N is the held-position count, not the watchlist length — although
    # here both happen to be 3.  The incremental template substitutes N
    # into "you hold {N} live position(s) opened on prior ticks."
    assert "3 live position" in delta["temp:strategist_mode"]

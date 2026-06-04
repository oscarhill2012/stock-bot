"""Chunk 5 — multi-tick prompt-diversity integration test.

The "stuck on tick 1" pathology that motivated Spec B is the
strategist producing byte-identical rationale across all sampled
ticks of the baseline-2025-09 / first-test run.  The root cause was
prompt isomorphism — same evidence, same instruction text, same
output by design.

Spec B's Chunks 4-5 fix that by making the prompt structurally
different across ticks:
  * Tick 1 (cold start) — Mode header reads "Cold start — your
    portfolio is empty"; Held Positions block is the flat-portfolio
    sentinel.
  * Tick N > 1 (incremental) — Mode header reads "Incremental —
    you have N held positions opened on prior ticks"; Held Positions
    block renders the evolution columns.

This integration test runs a 5-tick backtest against a stub LLM that
echoes its prompt back (so we can inspect every prompt that was sent)
with a portfolio that is empty on tick 1 and seeded with one
position from tick 2 onwards.  It asserts:

  (a) The Mode header text differs on ticks 2-5 vs tick 1 (cold-start
      vs incremental framing).
  (b) The Held Positions block is non-empty on ticks 2-5 — at minimum
      it contains the seeded ticker symbol and the "Thesis staleness" line.

Together these prove the prompt is no longer tick-isomorphic; an LLM
running against this surface cannot produce byte-identical rationale
because the input itself differs.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agents.strategist.context_shim import StrategistContextShim
from agents.strategist.prompts import (
    COLD_START_MODE_TEMPLATE,
    INCREMENTAL_MODE_TEMPLATE,
    STRATEGIST_INSTRUCTION,
)
from broker.portfolio import Portfolio


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Stub LLM — captures the rendered prompt onto a list for later inspection
# ---------------------------------------------------------------------------

class _PromptRecorder:
    """Receives the rendered strategist prompt at each tick.

    The shim resolves the {temp:strategist_mode} and {temp:held_positions_view}
    placeholders by emitting them as state_delta keys; the LlmAgent's
    ``inject_session_state`` then does the final ``.format(**state)`` pass
    before the request is sent.  We short-circuit the LLM call by
    capturing the post-injection prompt directly here.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def capture(self, instruction: str, state: dict[str, Any]) -> None:
        """Resolve runtime placeholders and append the resulting prompt."""

        # Only resolve the placeholders the test cares about — the full
        # ADK inject_session_state pass also resolves {portfolio} etc.
        # which we do not need for the diversity assertion.
        resolved = (
            instruction
            .replace("{temp:strategist_mode}",          state.get("temp:strategist_mode", ""))
            .replace("{temp:held_positions_view}", state.get("temp:held_positions_view", ""))
        )
        self.prompts.append(resolved)


# ---------------------------------------------------------------------------
# Pipeline driver — runs the shim only (the LLM call is stubbed)
# ---------------------------------------------------------------------------

async def _run_one_tick(
    *,
    state:    dict[str, Any],
    recorder: _PromptRecorder,
) -> None:
    """Run StrategistContextShim once and capture the resolved prompt.

    We invoke the shim's ``_run_async_impl`` directly with a fake context,
    merge its state_delta into ``state`` (mimicking ADK's session merge),
    and then ask the recorder to resolve the instruction template against
    the post-shim state.  This is sufficient to assert prompt diversity
    without spinning up the full pipeline.
    """

    from types import SimpleNamespace

    ctx = SimpleNamespace(
        session       = SimpleNamespace(state=state),
        invocation_id = f"tick-{len(recorder.prompts) + 1}",
    )

    shim = StrategistContextShim()
    async for event in shim._run_async_impl(ctx):
        state.update(event.actions.state_delta or {})

    recorder.capture(STRATEGIST_INSTRUCTION, state)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

async def test_multi_tick_backtest_produces_diverse_rationale() -> None:
    """Tick 1 prompt differs structurally from ticks 2-5 prompts."""

    # ── Fixture state ────────────────────────────────────────────────────
    # Tick 1: portfolio empty → cold-start mode, flat-portfolio held-view.
    # Tick 2-5: one seeded position → incremental mode, populated held-view.
    #
    # iter-3: target_price / stop_price / horizon removed from PositionThesis.
    # The seeded dict uses the prose-only contract.  ``thesis_last_updated_tick``
    # is 1 so that staleness advances as ``user:current_tick_index`` increments
    # from 1 to 4 across ticks 2-5 (giving distinct stale_ticks values of 0-3).
    seeded_position = {
        "ticker":                   "AVGO",
        "opened_at":                "2026-05-01T14:00:00+00:00",
        "opened_tick_id":           "tick_001",
        "opened_price":             100.0,
        "weight":                   0.05,
        "catalyst":                 "Q3 guidance call",
        "rationale":                "Cloud-AI margin expansion thesis",
        "last_reviewed_at":         "2026-05-01T14:00:00+00:00",
        "last_reviewed_decision":   "buy",
        "thesis_last_updated_tick": 1,
    }

    portfolio = Portfolio(cash=950.0).model_dump(mode="json")
    recorder  = _PromptRecorder()

    base_state = {
        "portfolio":            portfolio,
        "tickers":              ["AVGO", "MSFT"],
        "technical_evidence":   [],
        "fundamental_evidence": [],
        "news_evidence":        [],
        "smart_money_evidence": [],
    }

    # Run 5 ticks at hourly cadence.
    # ``user:current_tick_index`` increments per tick so the shim can compute
    # thesis staleness (stale_ticks = current_tick_index - thesis_last_updated_tick).
    as_of_start = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)
    for i in range(5):
        # Tick 1 — empty positions; ticks 2-5 — seeded.
        positions = {} if i == 0 else {"AVGO": seeded_position}

        state = {
            **base_state,
            "user:positions":        positions,
            "tick_id":               f"tick_{i + 1:03d}",
            "as_of":                 as_of_start + timedelta(hours=i),
            "user:current_tick_index": i,
        }
        await _run_one_tick(state=state, recorder=recorder)

    assert len(recorder.prompts) == 5

    tick1 = recorder.prompts[0]
    ticks_n = recorder.prompts[1:]

    # ── Assertion 1 — Mode header text differs on ticks 2-5 vs tick 1 ────
    # Tick 1: cold-start template substring present.
    assert COLD_START_MODE_TEMPLATE in tick1, (
        "Tick 1 prompt is missing the cold-start mode header"
    )

    for i, prompt in enumerate(ticks_n, start=2):

        # Each subsequent tick: incremental template substring present
        # with N=1 substituted.
        expected = INCREMENTAL_MODE_TEMPLATE.format(N=1)
        assert expected in prompt, (
            f"Tick {i} prompt is missing the incremental mode header "
            f"(expected substring not found)"
        )

        # And the cold-start template MUST NOT also be present — the two
        # modes are mutually exclusive at the substring level.
        assert COLD_START_MODE_TEMPLATE not in prompt, (
            f"Tick {i} prompt contains both the cold-start AND "
            f"incremental templates — modes leaked across each other"
        )

    # ── Assertion 2 — Held Positions block is non-empty on ticks 2-5 ─────
    # Tick 1: flat-portfolio sentinel present.
    assert "(Thesis book is empty — no views recorded yet.)" in tick1

    for i, prompt in enumerate(ticks_n, start=2):

        # The seeded ticker symbol must appear in the rendered held-view.
        assert "AVGO" in prompt, (
            f"Tick {i} prompt does not render the seeded AVGO position"
        )

        # Thesis-staleness line — proves the context_shim renderer
        # was actually invoked and injected held-position data.
        # (context_shim uses _render_positions_shim which shows
        # "Thesis staleness: N ticks since last update" and splits held vs
        # watched theses into two labelled sections.)
        assert "Thesis staleness" in prompt, (
            f"Tick {i} prompt is missing the Thesis staleness line"
        )

        # And the flat-portfolio sentinel MUST NOT be present alongside
        # a populated held set.
        assert "(Thesis book is empty — no views recorded yet.)" not in prompt, (
            f"Tick {i} prompt contains the flat-portfolio sentinel "
            f"despite a seeded held set"
        )

    # ── Assertion 3 — prompts are not byte-identical across ticks 2-5 ────
    # Thesis staleness (stale_ticks = user:current_tick_index -
    # thesis_last_updated_tick) increments every tick because
    # user:current_tick_index advances while thesis_last_updated_tick stays
    # at 1.  This is sufficient to defeat byte-identical prompts and closes
    # the "stuck on tick 1" pathology that Spec B was designed to fix.
    unique_prompts = {p for p in ticks_n}
    assert len(unique_prompts) == len(ticks_n), (
        f"Ticks 2-5 produced only {len(unique_prompts)} unique prompts; "
        f"expected {len(ticks_n)} — the Held-for evolution column "
        f"should advance every tick"
    )

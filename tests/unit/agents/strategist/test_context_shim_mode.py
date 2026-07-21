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
                # iter-3 schema: target_price / stop_price / catalyst / horizon removed.
                "ticker":                 "AVGO",
                "opened_at":              "2026-05-01T14:00:00+00:00",
                "opened_tick_id":         "tick_001",
                "opened_price":           100.0,
                "weight":                 0.05,
                "rationale":              "Cloud-AI margin expansion",
                "last_reviewed_at":       "2026-05-01T14:00:00+00:00",
                "last_reviewed_decision": "buy",
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
                # iter-3 schema: horizon removed; last_reviewed_decision uses four-verb vocab.
                "ticker":                 "AVGO",
                "opened_at":              "2026-05-01T14:00:00+00:00",
                "opened_tick_id":         "tick_001",
                "opened_price":           100.0,
                "weight":                 0.05,
                "rationale":              "r1",
                "last_reviewed_at":       "2026-05-01T14:00:00+00:00",
                "last_reviewed_decision": "buy",
            },
            "MSFT": {
                "ticker":                 "MSFT",
                "opened_at":              "2026-05-02T14:00:00+00:00",
                "opened_tick_id":         "tick_002",
                "opened_price":           400.0,
                "weight":                 0.04,
                "rationale":              "r2",
                "last_reviewed_at":       "2026-05-02T14:00:00+00:00",
                "last_reviewed_decision": "buy",
            },
            "XOM": {
                "ticker":                 "XOM",
                "opened_at":              "2026-05-03T14:00:00+00:00",
                "opened_tick_id":         "tick_003",
                "opened_price":            110.0,
                "weight":                 0.03,
                "rationale":              "r3",
                "last_reviewed_at":       "2026-05-03T14:00:00+00:00",
                "last_reviewed_decision": "buy",
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


# ---------------------------------------------------------------------------
# C1 — mode header must count only LIVE positions, not the whole thesis book.
#
# ``state["user:positions"]`` holds one row per ticker the agent has a view
# on — owned OR not (see position_thesis.py).  A row with ``opened_at is
# None`` is a watched-only view, not a live position.  The mode header must
# count only rows with a live position — the same discriminator
# ``_render_positions_shim`` uses for its ``[POSITION]`` tag — so the header
# agrees with the number of ``[POSITION]`` tags the model sees in the
# Thesis Book below it.
# ---------------------------------------------------------------------------

def _make_thesis_dict(*, opened_at: str | None) -> dict:
    """Build a minimal thesis-book row dict for count tests.

    Parameters
    ----------
    opened_at:
        ISO-8601 open timestamp, or ``None`` for a watched-only (no
        live position) row.

    Returns
    -------
    dict
        A thesis-row dict with the entry fields populated only when
        ``opened_at`` is given — mirrors the real schema's convention
        that a live position is signalled by populated entry fields.
    """
    return {
        "rationale":  "test rationale",
        "opened_at":  opened_at,
    }


async def test_count_live_positions_mixed_dict_book() -> None:
    """A 5-row book with 2 live + 3 watched-only rows counts to 2 (dicts)."""
    from agents.strategist.context_shim import _count_live_positions

    positions = {
        "AAA": _make_thesis_dict(opened_at="2026-05-01T14:00:00+00:00"),
        "BBB": _make_thesis_dict(opened_at="2026-05-02T14:00:00+00:00"),
        "CCC": _make_thesis_dict(opened_at=None),
        "DDD": _make_thesis_dict(opened_at=None),
        "EEE": _make_thesis_dict(opened_at=None),
    }

    assert _count_live_positions(positions) == 2


async def test_count_live_positions_mixed_position_thesis_book() -> None:
    """The same 2-live / 3-watched-only split, using PositionThesis instances."""
    from agents.strategist.context_shim import _count_live_positions
    from agents.strategist.position_thesis import PositionThesis

    def _thesis(ticker: str, *, opened_at) -> PositionThesis:
        kwargs: dict = {
            "ticker":                 ticker,
            "rationale":              "test rationale",
            "opened_at":              opened_at,
            "last_reviewed_at":       datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
            "last_reviewed_decision": "buy" if opened_at is not None else "update",
        }
        if opened_at is not None:
            kwargs["opened_tick_id"] = "tick_001"
            kwargs["opened_price"]   = 100.0
            kwargs["weight"]         = 0.05
        return PositionThesis(**kwargs)

    positions = {
        "AAA": _thesis("AAA", opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=UTC)),
        "BBB": _thesis("BBB", opened_at=datetime(2026, 5, 2, 14, 0, tzinfo=UTC)),
        "CCC": _thesis("CCC", opened_at=None),
        "DDD": _thesis("DDD", opened_at=None),
        "EEE": _thesis("EEE", opened_at=None),
    }

    assert _count_live_positions(positions) == 2


async def test_count_live_positions_empty_book() -> None:
    """An empty book counts to zero."""
    from agents.strategist.context_shim import _count_live_positions

    assert _count_live_positions({}) == 0


async def test_shim_mode_header_counts_live_positions_not_book_size() -> None:
    """The rendered mode header text must reflect live-position count, not book size.

    A 5-row thesis book with 2 live positions and 3 watched-only rows must
    yield "2 live position(s)" in ``temp:strategist_mode`` — not 5.  The
    cold-start branch is untouched: a non-empty book (even with zero live
    rows) still selects the incremental template, never cold-start.
    """
    state = {
        "user:positions": {
            "AAA": _make_thesis_dict(opened_at="2026-05-01T14:00:00+00:00"),
            "BBB": _make_thesis_dict(opened_at="2026-05-02T14:00:00+00:00"),
            "CCC": _make_thesis_dict(opened_at=None),
            "DDD": _make_thesis_dict(opened_at=None),
            "EEE": _make_thesis_dict(opened_at=None),
        },
        "portfolio":               Portfolio(cash=950.0).model_dump(mode="json"),
        "tickers":                 ["AAA", "BBB", "CCC", "DDD", "EEE"],
        "tick_id":                 "tick_005",
        "as_of":                   datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    assert delta["temp:strategist_mode"] == INCREMENTAL_MODE_TEMPLATE.format(N=2)
    assert "2 live position" in delta["temp:strategist_mode"]
    assert "5 live position" not in delta["temp:strategist_mode"]


async def test_shim_mode_header_zero_live_positions_still_incremental() -> None:
    """A non-empty book with zero live rows still selects incremental mode.

    Cold-start selection is driven purely by ``not positions`` (empty book),
    not by the live-position count.  A book of watched-only rows (no live
    positions yet) must still render the incremental template, with "0 live
    position(s)".
    """
    state = {
        "user:positions": {
            "CCC": _make_thesis_dict(opened_at=None),
            "DDD": _make_thesis_dict(opened_at=None),
        },
        "portfolio":               Portfolio(cash=1000.0).model_dump(mode="json"),
        "tickers":                 ["CCC", "DDD"],
        "tick_id":                 "tick_002",
        "as_of":                   datetime(2026, 5, 2, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    assert delta["temp:strategist_mode"] == INCREMENTAL_MODE_TEMPLATE.format(N=0)
    assert "0 live position" in delta["temp:strategist_mode"]

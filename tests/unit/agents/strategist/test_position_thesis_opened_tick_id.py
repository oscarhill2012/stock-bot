"""PositionThesis.opened_tick_id field tests — Tier 1, no LLM.

Migrated to import from ``agents.strategist.position_thesis`` (the canonical
iter-3 model) rather than the legacy ``agents.strategist.schema`` class.
"""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.position_thesis import PositionThesis


def _thesis(**kwargs) -> PositionThesis:
    """Build a minimal valid PositionThesis with caller overrides applied."""
    defaults = dict(
        ticker="AAPL",
        opened_at=datetime.now(tz=UTC),
        opened_price=192.40,
        weight=0.05,
        rationale="x",
        last_reviewed_at=datetime.now(tz=UTC),
        last_reviewed_decision="buy",
    )
    return PositionThesis(**(defaults | kwargs))


def test_opened_tick_id_required():
    """``opened_tick_id`` is a required field on the iter-3 PositionThesis."""
    pt = _thesis(opened_tick_id="tick_2026-05-08T14:00")
    assert pt.opened_tick_id == "tick_2026-05-08T14:00"


def test_opened_tick_id_round_trip():
    """``opened_tick_id`` survives a model_dump → model_validate round-trip."""
    pt = _thesis(opened_tick_id="tick_2026-05-08T14:00")
    rebuilt = PositionThesis.model_validate(pt.model_dump(mode="json"))
    assert rebuilt.opened_tick_id == "tick_2026-05-08T14:00"

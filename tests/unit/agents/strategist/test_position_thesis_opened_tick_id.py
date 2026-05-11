"""PositionThesis.opened_tick_id field tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.schema import PositionThesis


def test_opened_tick_id_defaults_to_empty_string():
    pt = PositionThesis(
        ticker="AAPL",
        opened_at=datetime.now(tz=UTC),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="x",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=UTC),
    )
    assert pt.opened_tick_id == ""


def test_opened_tick_id_round_trip():
    pt = PositionThesis(
        ticker="AAPL",
        opened_at=datetime.now(tz=UTC),
        opened_price=192.40,
        opened_tag="open_aapl",
        rationale="x",
        horizon="swing",
        last_reviewed_at=datetime.now(tz=UTC),
        opened_tick_id="tick_2026-05-08T14:00",
    )
    rebuilt = PositionThesis.model_validate(pt.model_dump(mode="json"))
    assert rebuilt.opened_tick_id == "tick_2026-05-08T14:00"

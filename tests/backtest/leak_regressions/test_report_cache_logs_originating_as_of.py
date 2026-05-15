"""``report_cache`` payload now records the originating tick's as_of."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agents.analysts.report_cache import read_cache, write_cache


def test_write_records_originating_as_of(tmp_path: Path) -> None:
    """``write_cache(..., originating_as_of=T)`` stamps the payload."""
    t1 = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)
    write_cache(
        tmp_path,
        analyst="news",
        ticker="AAPL",
        input_hash="h1",
        prompt_version="v1",
        verdict={"stance": "BULLISH"},
        report={"text": "yes"},
        originating_as_of=t1,
    )

    written = json.loads(
        (tmp_path / "news" / "AAPL.json").read_text(),
    )
    assert written["originating_as_of"] == t1.isoformat()


def test_read_returns_originating_as_of(tmp_path: Path) -> None:
    """``read_cache`` exposes ``originating_as_of`` so the caller can log it."""
    t1 = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)
    write_cache(
        tmp_path, analyst="news", ticker="AAPL",
        input_hash="h1", prompt_version="v1",
        verdict={"stance": "BULLISH"}, report=None,
        originating_as_of=t1,
    )

    record = read_cache(tmp_path, "news", "AAPL", input_hash="h1", prompt_version="v1")
    assert record is not None
    assert record["originating_as_of"] == t1.isoformat()

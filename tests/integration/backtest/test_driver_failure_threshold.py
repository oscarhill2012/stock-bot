"""Integration test: driver aborts past the configured failed-tick ratio.

Injects a broken ``_run_one_tick`` implementation so every tick raises.
Over a 10-tick schedule, the driver should abort after the first tick
(1/1 = 100% > 10%) and write ``manifest.status = "aborted"``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backtest.driver import Driver
from backtest.schedule import Tick
from broker.fake import FakeBroker


@pytest.mark.asyncio
async def test_aborts_above_threshold(tmp_path: Path) -> None:
    """If more than 10% of ticks fail, the driver raises and writes status='aborted'."""
    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 1.0})

    # Pre-populate manifest so the writer has an existing file to patch.
    (tmp_path / "manifest.json").write_text("{}")

    driver = Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="t",
        failure_abort_ratio=0.10,
    )

    schedule = [
        Tick(as_of=datetime(2023, 3, d, 9, 30, tzinfo=UTC), phase="open")
        for d in range(6, 16)   # 10 ticks
    ]

    with patch.object(
        driver,
        "_run_one_tick",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(RuntimeError, match="exceeded threshold"):
            await driver.run({"watchlist": [], "tickers": []}, schedule)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["status"] == "aborted", (
        f"expected status='aborted', got {manifest['status']!r}"
    )

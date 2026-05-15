"""Tests that the driver aborts past the configured failed-tick ratio.

Patches ``Driver._run_one_tick`` with an ``AsyncMock`` that always raises so
no real pipeline or ADK runner is invoked.  The test asserts that after more
than 10% of ticks fail the driver raises ``RuntimeError`` and writes
``status="aborted"`` to ``manifest.json``.
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
    """If more than 10% of ticks fail, the driver raises and writes ``status='aborted'``."""
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 1.0})

    # Pre-populate manifest so _write_manifest_status has something to patch.
    (tmp_path / "manifest.json").write_text("{}")

    driver = Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="t",
        failure_abort_ratio=0.10,
    )

    # 10 ticks — the first failure pushes the ratio to 1/1 = 100% > 10%.
    # With a ratio threshold of 0.10, the driver should abort on the very
    # first failure (1/1 > 0.10), not after reaching 10 failures.
    schedule = [
        Tick(as_of=datetime(2023, 3, d, tzinfo=UTC), phase="open")
        for d in range(6, 16)   # 10 ticks
    ]

    with patch.object(
        driver, "_run_one_tick",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(RuntimeError, match="exceeded threshold"):
            await driver.run({"watchlist": [], "tickers": []}, schedule)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert manifest["ticks_failed"] >= 1

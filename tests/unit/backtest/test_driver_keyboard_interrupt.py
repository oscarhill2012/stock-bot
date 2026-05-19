"""Tests that the backtest driver does not swallow KeyboardInterrupt.

``Driver._run_one_tick`` previously caught ``(AttributeError, BaseException)``
to absorb a known ADK 1.32 teardown bug.  Catching ``BaseException`` also
swallows ``KeyboardInterrupt`` and ``SystemExit``, which breaks Ctrl-C and
process-signal handling.

The fix narrows the catch to ``(AttributeError, Exception)`` so that
``BaseException`` subclasses that are *not* ``Exception`` subclasses
(i.e. ``KeyboardInterrupt``, ``SystemExit``, ``GeneratorExit``) propagate as
expected.

These tests verify the contract by patching the internal ADK ``runner.run_async``
call (inside ``_run_one_tick``) to raise the relevant exception and asserting
that the driver re-raises it rather than recording a failed tick.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backtest.driver import Driver
from backtest.schedule import Tick
from broker.fake import FakeBroker


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_driver(tmp_path: Path) -> Driver:
    """Build a minimal Driver wired to a FakeBroker with no tickers."""
    broker = FakeBroker(starting_cash=10_000, prices={})
    # Pre-create manifest so _write_manifest_status does not crash.
    (tmp_path / "manifest.json").write_text("{}")
    return Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="test-window",
        failure_abort_ratio=0.10,
        # This suite patches the ADK runner to raise specific exception
        # types — the real pipeline (and therefore the Snapshotter) never
        # executes, so disable the post-tick snapshot-completion check or
        # it would mask the very behaviour we want to assert here.
        enforce_pipeline_completion=False,
    )


def _one_tick() -> list[Tick]:
    """Return a single-element tick schedule."""
    return [Tick(as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC), phase="open")]


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_keyboard_interrupt_propagates(tmp_path: Path) -> None:
    """KeyboardInterrupt raised inside the ADK runner must NOT be swallowed.

    The narrow ``(AttributeError, Exception)`` catch in ``_run_one_tick``
    intentionally excludes ``KeyboardInterrupt`` (a direct ``BaseException``
    subclass).  This test confirms it re-raises and that no failed-tick entry
    is recorded (because the interrupt halts execution before the failure
    accounting loop can fire).
    """
    driver = _make_driver(tmp_path)

    # Patch the internal ADK runner call that lives inside ``_run_one_tick``.
    # We raise KeyboardInterrupt *from within* the async-for loop to simulate
    # the runner raising it during teardown.
    async def _raise_kbi(*args, **kwargs):
        raise KeyboardInterrupt("simulated Ctrl-C")
        yield  # pragma: no cover — makes this an async generator

    with patch(
        "backtest.driver.Runner",
        return_value=MagicMock(run_async=_raise_kbi),
    ):
        with pytest.raises(KeyboardInterrupt):
            await driver.run({"watchlist": [], "tickers": []}, _one_tick())

    # No failed tick should have been recorded — the interrupt aborted before
    # the exception-handling accounting loop in ``Driver.run`` could fire.
    assert driver._failed == [], (
        "KeyboardInterrupt must propagate immediately; no failed-tick entry expected"
    )


@pytest.mark.asyncio
async def test_attribute_error_is_absorbed(tmp_path: Path) -> None:
    """AttributeError from ADK teardown must be absorbed (known ADK bug).

    The catch still covers ``AttributeError`` so the known ADK 1.32 cleanup
    bug does not cause spurious tick failures when the pipeline itself
    completed successfully.
    """
    driver = _make_driver(tmp_path)

    async def _raise_attr_err(*args, **kwargs):
        raise AttributeError("ADK internal teardown attr error")
        yield  # pragma: no cover

    with patch(
        "backtest.driver.Runner",
        return_value=MagicMock(run_async=_raise_attr_err),
    ):
        # Should NOT raise — AttributeError is silently absorbed.
        await driver.run({"watchlist": [], "tickers": []}, _one_tick())

    # The tick completed (AttributeError absorbed), so no failure recorded.
    assert driver._failed == []

"""Driver guards must raise loudly when the store handle is missing in
production mode (``require_store=True`` — the default).  Plan 10 replaces
the previous ``except RuntimeError: pass`` quartet with explicit failure.

Two contracts are tested here:
1. Default construction (``require_store=True``) raises ``RuntimeError`` at
   construction time when no store has been wired.
2. The opt-out flag (``require_store=False``) allows construction to succeed
   with a warning log and no exception.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backtest.providers import _store_handle

# ── Helpers ───────────────────────────────────────────────────────────────────

class _NullBroker:
    """Minimal broker stub — only ``get_portfolio`` is needed by Driver.__init__."""

    async def get_portfolio(self):
        """Return an empty portfolio — the stub satisfies the broker interface
        the Driver stores at construction; no tick runs in these tests, so this
        method is never actually called."""
        from broker.portfolio import Portfolio
        return Portfolio(cash=0.0)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_driver_raises_when_store_missing_in_production_mode(tmp_path: Path) -> None:
    """Default Driver construction (``require_store=True``) must raise the moment
    it cannot reach a wired store handle — no silent skip.

    The capture-enable guard lives in ``__init__``, so the raise happens at
    *construction* time, before any tick runs.  The ``Driver(...)`` call
    itself must be inside the ``pytest.raises`` block.
    """
    from backtest.driver import Driver

    # Guarantee the store handle is empty for this test — defensive against
    # any upstream fixture or import-time side effect that might wire it.
    _store_handle.clear_store()

    # No set_store() call — the store handle is empty.
    with pytest.raises(RuntimeError, match="store handle not wired"):
        Driver(
            broker=_NullBroker(),
            run_dir=tmp_path,
            window_key="unit-test",
            run_id="unit-test-run",
            # require_store defaults to True
        )


def test_driver_does_not_raise_when_require_store_disabled(tmp_path: Path) -> None:
    """Opt-in escape hatch for isolated unit tests — must not raise at
    construction even with no store wired (it logs a WARNING instead)."""
    from backtest.driver import Driver

    # Guarantee the store handle is empty — same defensive clear as above.
    _store_handle.clear_store()

    driver = Driver(
        broker=_NullBroker(),
        run_dir=tmp_path,
        window_key="unit-test",
        run_id="unit-test-run",
        require_store=False,
    )
    assert driver._require_store is False

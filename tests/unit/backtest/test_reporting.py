"""Unit tests for end-of-window reporting: metrics computation and forward-return backfill.

Both tests use hand-built fixtures so they run deterministically with no
network calls, no database, and no LLM.

Coverage
--------
- ``compute_metrics`` — verify total return, Sharpe sign, max drawdown, and
  vs-SPY delta on a small hand-crafted snapshot series.
- ``backfill_forward_returns`` — write a synthetic decision JSON, seed a tiny
  in-memory-ish cache (via a real ``CachedDataStore`` in a tmp dir), call the
  backfill, and assert the patched values are correct.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from backtest.reporting import backfill_forward_returns, compute_metrics


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_snapshots(
    start: datetime,
    values: list[float],
    spy_prices: list[float] | None = None,
) -> list[tuple[datetime, float, float, float]]:
    """Build a synthetic snapshot series for testing.

    Parameters
    ----------
    start:
        Timestamp for the first tick.
    values:
        Bot total values, one per tick.
    spy_prices:
        Optional SPY prices aligned to ``values``.  Defaults to a flat series
        at 100 if omitted.

    Returns
    -------
    list of (recorded_at, bot_total_value, spy_price, spy_value_if_held)
    """
    n = len(values)
    if spy_prices is None:
        spy_prices = [100.0] * n

    return [
        (start + timedelta(hours=i), values[i], spy_prices[i], 0.0)
        for i in range(n)
    ]


# ── compute_metrics ────────────────────────────────────────────────────────────

class TestComputeMetrics:
    """Tests for the ``compute_metrics`` reporting function."""

    def test_total_return_positive(self, tmp_path: Path) -> None:
        """Total return is (end - start) / start; 100 → 110 = +10.00%."""
        snapshots = _make_snapshots(
            start=datetime(2023, 3, 6, tzinfo=UTC),
            values=[100_000.0, 105_000.0, 110_000.0],
        )
        compute_metrics(snapshots, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "+10.00%" in text, f"Expected +10.00% in:\n{text}"

    def test_total_return_negative(self, tmp_path: Path) -> None:
        """A declining series produces a negative total return."""
        snapshots = _make_snapshots(
            start=datetime(2023, 3, 6, tzinfo=UTC),
            values=[100_000.0, 90_000.0],
        )
        compute_metrics(snapshots, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "-10.00%" in text, f"Expected -10.00% in:\n{text}"

    def test_max_drawdown_detected(self, tmp_path: Path) -> None:
        """Max drawdown is computed from the peak-to-trough percentage drop.

        Series: 100 → 120 (peak) → 90.  Drawdown = (90 - 120) / 120 = -25%.
        """
        snapshots = _make_snapshots(
            start=datetime(2023, 3, 6, tzinfo=UTC),
            values=[100_000.0, 120_000.0, 90_000.0],
        )
        compute_metrics(snapshots, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "-25.00%" in text, f"Expected -25.00% max drawdown in:\n{text}"

    def test_vs_spy_delta_positive(self, tmp_path: Path) -> None:
        """vs-SPY delta is positive when bot outperforms SPY.

        Bot: 100 → 110 (+10%).  SPY: 100 → 105 (+5%).  Delta = +5.00%.
        """
        snapshots = _make_snapshots(
            start=datetime(2023, 3, 6, tzinfo=UTC),
            values=[100_000.0, 110_000.0],
            spy_prices=[100.0, 105.0],
        )
        compute_metrics(snapshots, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "+5.00%" in text, f"Expected +5.00% vs-SPY delta in:\n{text}"

    def test_sharpe_nan_when_flat_returns(self, tmp_path: Path) -> None:
        """If every tick return is identical (zero variance), Sharpe is NaN → 'N/A'."""
        snapshots = _make_snapshots(
            start=datetime(2023, 3, 6, tzinfo=UTC),
            # Flat values → zero returns → zero std dev → NaN Sharpe.
            values=[100_000.0, 100_000.0, 100_000.0],
        )
        compute_metrics(snapshots, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "N/A" in text, f"Expected N/A for Sharpe when returns are flat:\n{text}"

    def test_sharpe_positive_for_rising_series(self, tmp_path: Path) -> None:
        """A consistently rising series should produce a positive Sharpe ratio."""
        snapshots = _make_snapshots(
            start=datetime(2023, 3, 6, tzinfo=UTC),
            # Monotonically increasing, with some variance in the steps.
            values=[100_000.0, 101_000.0, 102_500.0, 104_200.0, 106_000.0],
        )
        compute_metrics(snapshots, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        # Extract the Sharpe line and check it contains a positive number.
        sharpe_line = next(
            (l for l in text.splitlines() if "Sharpe" in l), None
        )
        assert sharpe_line is not None, "No Sharpe line found in metrics"
        # Positive Sharpe should not contain '-' or 'N/A' in the value position.
        assert "N/A" not in sharpe_line
        # The value appears after the last '**' marker.
        value_part = sharpe_line.split("**")[-2]
        assert not value_part.strip().startswith("-"), (
            f"Expected positive Sharpe in: {sharpe_line}"
        )

    def test_metrics_file_contains_all_sections(self, tmp_path: Path) -> None:
        """The output file must contain every expected metric heading."""
        snapshots = _make_snapshots(
            start=datetime(2023, 3, 6, tzinfo=UTC),
            values=[100_000.0, 103_000.0],
            spy_prices=[100.0, 101.0],
        )
        compute_metrics(snapshots, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()

        expected_headings = [
            "Total return (bot)",
            "SPY return (window)",
            "vs-SPY delta",
            "Sharpe",
            "Max drawdown",
            "Win rate",
            "Ticks recorded",
        ]
        for heading in expected_headings:
            assert heading in text, f"Missing heading '{heading}' in metrics:\n{text}"


# ── backfill_forward_returns ───────────────────────────────────────────────────

class TestBackfillForwardReturns:
    """Tests for the ``backfill_forward_returns`` forward-return patching function."""

    @pytest.fixture()
    def cache(self, tmp_path: Path):
        """A real (but tiny) ``CachedDataStore`` seeded with a few OHLCV bars."""
        from backtest.cache.store import CachedDataStore
        from data.models import OHLCBar

        store = CachedDataStore(tmp_path / "store.sqlite")

        # Seed bars for ticker AAPL on three calendar dates.
        # Entry date: 2023-03-13.  Bars at +1d (2023-03-14) and +5d (2023-03-18).
        bars = [
            OHLCBar(
                timestamp=datetime(2023, 3, 14, 16, 0, tzinfo=UTC),
                open=150.0, high=155.0, low=149.0, close=152.0, volume=1_000_000,
            ),
            OHLCBar(
                timestamp=datetime(2023, 3, 18, 16, 0, tzinfo=UTC),
                open=152.0, high=158.0, low=151.0, close=156.0, volume=900_000,
            ),
        ]
        store.write_ohlcv("AAPL", bars)
        return store

    def _write_decision(
        self,
        decisions_dir: Path,
        filename: str,
        ticker: str = "AAPL",
        fill_price: float = 148.0,
        as_of: str = "2023-03-13T13:30:00Z",
    ) -> Path:
        """Write a minimal decision snapshot JSON file to ``decisions_dir``."""
        decisions_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "decision_id": filename.replace(".json", ""),
            "tick": {"as_of": as_of, "phase": "open"},
            "ticker": ticker,
            "side": "buy",
            "execution": {"fill_price": fill_price, "fill_qty": 10, "status": "filled"},
            "forward_returns": None,
        }
        path = decisions_dir / filename
        path.write_text(json.dumps(snapshot, indent=2))
        return path

    def test_forward_returns_patched_correctly(
        self, tmp_path: Path, cache
    ) -> None:
        """Verify +1d and +5d returns are computed from the fill price.

        Fill price = 148.00.
        +1d close  = 152.00 → (152 - 148) / 148 ≈ 0.027027.
        +5d close  = 156.00 → (156 - 148) / 148 ≈ 0.054054.
        """
        decisions_dir = tmp_path / "decisions"
        self._write_decision(decisions_dir, "tick__AAPL__buy.json")

        backfill_forward_returns(
            decisions_dir=decisions_dir,
            cache=cache,
            horizons_days=[1, 5],
        )

        snapshot = json.loads((decisions_dir / "tick__AAPL__buy.json").read_text())
        fwd = snapshot["forward_returns"]

        assert isinstance(fwd, dict), "forward_returns should be a dict after backfill"

        assert "+1d" in fwd, "+1d key missing"
        assert "+5d" in fwd, "+5d key missing"

        assert fwd["+1d"] is not None, "+1d return should not be None (bar exists at +1d)"
        assert fwd["+5d"] is not None, "+5d return should not be None (bar exists at +5d)"

        assert math.isclose(fwd["+1d"], (152.0 - 148.0) / 148.0, rel_tol=1e-5), (
            f"Unexpected +1d return: {fwd['+1d']}"
        )
        assert math.isclose(fwd["+5d"], (156.0 - 148.0) / 148.0, rel_tol=1e-5), (
            f"Unexpected +5d return: {fwd['+5d']}"
        )

    def test_missing_horizon_is_none(self, tmp_path: Path, cache) -> None:
        """A horizon for which no bar exists in the cache is recorded as ``null``."""
        decisions_dir = tmp_path / "decisions"
        self._write_decision(decisions_dir, "tick__AAPL__buy.json")

        # Ask for +20d — no bar is in the cache that far out.
        backfill_forward_returns(
            decisions_dir=decisions_dir,
            cache=cache,
            horizons_days=[20],
        )

        snapshot = json.loads((decisions_dir / "tick__AAPL__buy.json").read_text())
        fwd = snapshot["forward_returns"]

        assert "+20d" in fwd, "+20d key missing"
        assert fwd["+20d"] is None, "+20d should be null when no bar exists"

    def test_empty_decisions_dir_is_no_op(
        self, tmp_path: Path, cache
    ) -> None:
        """Calling backfill on an empty directory should silently succeed."""
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()

        # Should not raise.
        backfill_forward_returns(
            decisions_dir=decisions_dir,
            cache=cache,
            horizons_days=[1, 5, 20],
        )

    def test_nonexistent_decisions_dir_is_no_op(
        self, tmp_path: Path, cache
    ) -> None:
        """Calling backfill when the directory does not exist should silently succeed."""
        missing_dir = tmp_path / "nonexistent_decisions"

        # Should not raise.
        backfill_forward_returns(
            decisions_dir=missing_dir,
            cache=cache,
            horizons_days=[1, 5, 20],
        )

    def test_multiple_decisions_all_patched(
        self, tmp_path: Path, cache
    ) -> None:
        """All JSON files in the directory receive the ``forward_returns`` key."""
        decisions_dir = tmp_path / "decisions"
        file_a = self._write_decision(
            decisions_dir, "tick_a__AAPL__buy.json"
        )
        file_b = self._write_decision(
            decisions_dir, "tick_b__AAPL__sell.json"
        )

        backfill_forward_returns(
            decisions_dir=decisions_dir,
            cache=cache,
            horizons_days=[1],
        )

        for path in [file_a, file_b]:
            snap = json.loads(path.read_text())
            assert "forward_returns" in snap, f"forward_returns missing in {path.name}"
            assert isinstance(snap["forward_returns"], dict)

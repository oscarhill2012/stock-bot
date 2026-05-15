"""Tests for end-of-window reporting: metrics, equity curve, forward-return backfill.

These tests are fully offline — no live DB connections, no external API calls.
Fixtures use tmp_path (pytest built-in) and synthetic in-memory data.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from backtest.reporting import (
    _write_metrics,
    _backfill_forward_returns,
    _compute_vs_spy_delta,
    _parse_date,
)


# ── _write_metrics ────────────────────────────────────────────────────────────

class TestWriteMetrics:
    """Unit tests for the _write_metrics helper."""

    def test_total_return_positive(self, tmp_path: Path) -> None:
        """Total return is (end - start) / start, written as a percentage."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 105_000.0),
        ]
        _write_metrics(series, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "+5.00%" in text

    def test_total_return_negative(self, tmp_path: Path) -> None:
        """Negative total return is written with a minus sign."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC),  80_000.0),
        ]
        _write_metrics(series, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "-20.00%" in text

    def test_max_drawdown_zero_for_monotonic_rise(self, tmp_path: Path) -> None:
        """Max drawdown is 0.0 when the portfolio only ever rises."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 110_000.0),
            (datetime(2023, 3, 8, tzinfo=UTC), 120_000.0),
        ]
        _write_metrics(series, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        # Max drawdown of 0 should be written as +0.00%
        assert "+0.00%" in text

    def test_max_drawdown_detected(self, tmp_path: Path) -> None:
        """Max drawdown correctly reflects the largest peak-to-trough decline."""
        # 100k → 120k (peak) → 90k: drawdown = (90k - 120k) / 120k = -25%
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 120_000.0),
            (datetime(2023, 3, 8, tzinfo=UTC),  90_000.0),
        ]
        _write_metrics(series, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "-25.00%" in text

    def test_sharpe_nan_for_single_tick(self, tmp_path: Path) -> None:
        """Sharpe is NaN when there is only one tick (zero returns to compute)."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
        ]
        _write_metrics(series, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        # NaN is written as 'nan' by Python's float formatting.
        assert "nan" in text.lower()

    def test_tick_count_recorded(self, tmp_path: Path) -> None:
        """Ticks recorded reflects the length of the input series."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 101_000.0),
            (datetime(2023, 3, 8, tzinfo=UTC), 102_000.0),
        ]
        _write_metrics(series, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert "**3**" in text

    def test_metrics_file_is_markdown(self, tmp_path: Path) -> None:
        """Output file starts with a Markdown heading."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 105_000.0),
        ]
        _write_metrics(series, tmp_path / "metrics.md")

        text = (tmp_path / "metrics.md").read_text()
        assert text.startswith("# Backtest metrics")


# ── _parse_date ───────────────────────────────────────────────────────────────

class TestParseDate:
    """Unit tests for _parse_date, the ISO-string → date parser."""

    def test_zulu_suffix(self) -> None:
        """'Z' suffix is handled as UTC."""
        assert _parse_date("2023-03-10T09:30:00Z") == date(2023, 3, 10)

    def test_offset_aware(self) -> None:
        """Offset-aware ISO strings are parsed correctly."""
        assert _parse_date("2023-03-10T09:30:00-04:00") == date(2023, 3, 10)

    def test_naive_iso(self) -> None:
        """Naive ISO strings (no tz suffix) are accepted."""
        assert _parse_date("2023-03-10T09:30:00") == date(2023, 3, 10)


# ── _backfill_forward_returns ─────────────────────────────────────────────────

class TestBackfillForwardReturns:
    """Unit tests for the forward-return backfill helper."""

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _write_decision(
        decisions_dir: Path,
        ticker: str = "AAPL",
        fill_price: float = 150.0,
        as_of: str = "2023-03-06T09:30:00Z",
        side: str = "buy",
    ) -> Path:
        """Write a minimal decision JSON fixture and return its path."""
        snapshot = {
            "ticker": ticker,
            "side": side,
            "execution": {"fill_price": fill_price},
            "tick": {"as_of": as_of},
            "forward_returns": None,
        }
        path = decisions_dir / f"2023-03-06__{ticker}__{side}.json"
        path.write_text(json.dumps(snapshot))
        return path

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_backfill_writes_forward_returns(self, tmp_path: Path) -> None:
        """When the cache has bars, forward_returns is patched into the JSON."""
        from unittest.mock import MagicMock
        from data.models import OHLCBar

        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        path = self._write_decision(decisions_dir, fill_price=150.0)

        # Fake cache: always returns a single bar with close = 165.0 (+10%)
        fake_bar = MagicMock(spec=OHLCBar)
        fake_bar.close = 165.0

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = [fake_bar]

        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[1])

        result = json.loads(path.read_text())
        assert result["forward_returns"] is not None
        assert pytest.approx(result["forward_returns"]["+1d"], rel=1e-3) == 0.10

    def test_backfill_none_when_no_bars(self, tmp_path: Path) -> None:
        """When the cache returns no bars for a horizon, the value is None."""
        from unittest.mock import MagicMock

        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        path = self._write_decision(decisions_dir, fill_price=150.0)

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = []  # no bars

        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[5])

        result = json.loads(path.read_text())
        assert result["forward_returns"]["+5d"] is None

    def test_backfill_multiple_horizons(self, tmp_path: Path) -> None:
        """Multiple horizons are all patched in a single pass."""
        from unittest.mock import MagicMock
        from data.models import OHLCBar

        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        path = self._write_decision(decisions_dir, fill_price=100.0)

        bar_plus1  = MagicMock(spec=OHLCBar); bar_plus1.close  = 102.0
        bar_plus5  = MagicMock(spec=OHLCBar); bar_plus5.close  = 105.0
        bar_plus20 = MagicMock(spec=OHLCBar); bar_plus20.close = 110.0

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = [
            [bar_plus1],
            [bar_plus5],
            [bar_plus20],
        ]

        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[1, 5, 20])

        result = json.loads(path.read_text())
        fwd = result["forward_returns"]
        assert pytest.approx(fwd["+1d"],  rel=1e-3) == 0.02
        assert pytest.approx(fwd["+5d"],  rel=1e-3) == 0.05
        assert pytest.approx(fwd["+20d"], rel=1e-3) == 0.10

    def test_backfill_skips_missing_fill_price(self, tmp_path: Path) -> None:
        """Decisions without a fill_price are skipped without error."""
        from unittest.mock import MagicMock

        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()

        snapshot = {
            "ticker": "AAPL",
            "side": "buy",
            "execution": {},          # no fill_price
            "tick": {"as_of": "2023-03-06T09:30:00Z"},
            "forward_returns": None,
        }
        path = decisions_dir / "no_fill.json"
        path.write_text(json.dumps(snapshot))

        mock_cache = MagicMock()
        # Should not raise, and read_ohlcv should never be called.
        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[1])
        mock_cache.read_ohlcv.assert_not_called()

    def test_backfill_noop_when_no_decisions_dir(self, tmp_path: Path) -> None:
        """When the decisions directory does not exist, the function returns silently."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        _backfill_forward_returns(tmp_path / "decisions", mock_cache, horizons_days=[1])
        mock_cache.read_ohlcv.assert_not_called()

    def test_backfill_skips_missing_as_of(self, tmp_path: Path) -> None:
        """Decisions without tick.as_of are skipped without error."""
        from unittest.mock import MagicMock

        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()

        snapshot = {
            "ticker": "AAPL",
            "side": "buy",
            "execution": {"fill_price": 150.0},
            "tick": {},               # no as_of
            "forward_returns": None,
        }
        path = decisions_dir / "no_as_of.json"
        path.write_text(json.dumps(snapshot))

        mock_cache = MagicMock()
        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[1])
        mock_cache.read_ohlcv.assert_not_called()


# ── _write_metrics: win rate + fill count ────────────────────────────────────

class TestWriteMetricsNewFields:
    """Unit tests for the win rate, fill count, and vs-SPY fields in metrics.md."""

    _SIMPLE_SERIES = [
        (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
        (datetime(2023, 3, 7, tzinfo=UTC), 105_000.0),
    ]

    def test_win_rate_written(self, tmp_path: Path) -> None:
        """Win rate is written as a percentage when provided."""
        _write_metrics(
            self._SIMPLE_SERIES,
            tmp_path / "metrics.md",
            fill_count=10,
            win_rate=0.6,
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "60.0%" in text, f"Expected win rate in output; got:\n{text}"

    def test_fill_count_written(self, tmp_path: Path) -> None:
        """Total fill count is written as an integer."""
        _write_metrics(
            self._SIMPLE_SERIES,
            tmp_path / "metrics.md",
            fill_count=42,
            win_rate=0.5,
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "**42**" in text, f"Expected fill count 42 in output; got:\n{text}"

    def test_win_rate_nan_written_as_na(self, tmp_path: Path) -> None:
        """When win_rate is NaN (no trades), output contains 'N/A'."""
        _write_metrics(
            self._SIMPLE_SERIES,
            tmp_path / "metrics.md",
            fill_count=0,
            win_rate=float("nan"),
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "N/A" in text, f"Expected N/A for win rate; got:\n{text}"

    def test_vs_spy_float_written_as_percent(self, tmp_path: Path) -> None:
        """vs-SPY delta as float is written as a signed percentage."""
        _write_metrics(
            self._SIMPLE_SERIES,
            tmp_path / "metrics.md",
            vs_spy_delta=0.03,  # +3 pp outperformance
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "+3.00%" in text, f"Expected +3.00% vs-SPY delta; got:\n{text}"

    def test_vs_spy_na_string_written_as_italic(self, tmp_path: Path) -> None:
        """vs-SPY delta as a string is written in italics (Markdown underscore)."""
        _write_metrics(
            self._SIMPLE_SERIES,
            tmp_path / "metrics.md",
            vs_spy_delta="N/A — SPY not in cache (run backtest_fetch with SPY)",
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "SPY not in cache" in text, f"Expected SPY N/A message; got:\n{text}"


# ── _compute_vs_spy_delta ────────────────────────────────────────────────────

class TestComputeVsSpyDelta:
    """Unit tests for the _compute_vs_spy_delta helper."""

    _EQUITY = [
        (datetime(2023, 3, 6, tzinfo=UTC), 10_000.0),
        (datetime(2023, 3, 8, tzinfo=UTC), 10_500.0),   # +5% bot return
    ]

    def test_delta_computed_when_spy_present(self) -> None:
        """When SPY bars are in the cache, the delta is bot_return − spy_return."""
        from unittest.mock import MagicMock
        from data.models import OHLCBar

        spy_open  = MagicMock(spec=OHLCBar); spy_open.open  = 400.0
        spy_close = MagicMock(spec=OHLCBar); spy_close.close = 404.0  # +1% SPY

        mock_cache = MagicMock()
        # read_ohlcv returns a list: first bar for open, last bar for close.
        mock_cache.read_ohlcv.return_value = [spy_open, spy_close]

        delta = _compute_vs_spy_delta(self._EQUITY, mock_cache)

        # bot +5%, SPY +1% → delta = +4 pp = 0.04
        assert isinstance(delta, float), f"Expected float, got {type(delta)}"
        assert pytest.approx(delta, rel=1e-3) == 0.04

    def test_na_string_when_spy_absent(self) -> None:
        """When SPY has no bars, a descriptive string is returned (no crash)."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = []  # SPY not in cache

        delta = _compute_vs_spy_delta(self._EQUITY, mock_cache)

        assert isinstance(delta, str), f"Expected str N/A, got {type(delta)}"
        assert "SPY" in delta, f"Expected SPY mention in N/A message: {delta}"

    def test_na_string_on_cache_error(self) -> None:
        """When cache.read_ohlcv raises, a descriptive string is returned."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = RuntimeError("db locked")

        delta = _compute_vs_spy_delta(self._EQUITY, mock_cache)

        assert isinstance(delta, str), f"Expected str N/A on error, got {type(delta)}"

    def test_na_string_when_equity_empty(self) -> None:
        """When the equity series is empty, a descriptive string is returned."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        delta = _compute_vs_spy_delta([], mock_cache)

        assert isinstance(delta, str)
        mock_cache.read_ohlcv.assert_not_called()

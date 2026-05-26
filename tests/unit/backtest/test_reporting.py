"""Tests for end-of-window reporting: metrics, equity curve, forward-return backfill.

These tests are fully offline — no live DB connections, no external API calls.
Fixtures use tmp_path (pytest built-in) and synthetic in-memory data.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.reporting import (
    _annualised_sharpe,
    _avg_exposure_pct,
    _backfill_forward_returns,
    _build_equity_figure,
    _compute_vs_spy_delta,
    _matched_exposure_series,
    _parse_date,
    _write_metrics,
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
        # timestamp is now read by the backfill to record actual-bar dates (B8).
        fake_bar.timestamp = datetime(2023, 3, 7, 14, 30, tzinfo=UTC)

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

        # timestamp is now read by the backfill to record actual-bar dates (B8).
        bar_plus1  = MagicMock(spec=OHLCBar)
        bar_plus1.close     = 102.0
        bar_plus1.timestamp = datetime(2023, 3,  7, tzinfo=UTC)

        bar_plus5  = MagicMock(spec=OHLCBar)
        bar_plus5.close     = 105.0
        bar_plus5.timestamp = datetime(2023, 3, 11, tzinfo=UTC)

        bar_plus20 = MagicMock(spec=OHLCBar)
        bar_plus20.close     = 110.0
        bar_plus20.timestamp = datetime(2023, 3, 26, tzinfo=UTC)

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
    """Unit tests for the _compute_vs_spy_delta helper.

    Post-refactor: ``_compute_vs_spy_delta`` delegates SPY valuation to
    ``_spy_benchmark_series`` and now requires a ``starting_cash`` arg so
    the SPY "position" is sized against the same $-zero as the portfolio.
    Both equity ticks here use hour 0 (< 17 UTC) so the open-phase
    classifier reads ``bar.open`` — that's why the fixtures only populate
    ``bar.open`` and skip ``bar.close``.
    """

    # Two equity ticks at hour 0 — open phase by the 17 UTC classifier.
    _EQUITY = [
        (datetime(2023, 3, 6, tzinfo=UTC), 10_000.0),
        (datetime(2023, 3, 8, tzinfo=UTC), 10_500.0),   # +5% bot return
    ]

    def test_delta_computed_when_spy_present(self) -> None:
        """When SPY bars are in the cache, the delta is bot_return − spy_return.

        Mock setup: one SPY bar per equity-tick date.  Both equity ticks
        are in the open phase (hour 0), so the classifier reads
        ``bar.open``.  ``bar.timestamp.date()`` must match the equity
        tick's date for the per-date lookup to succeed.
        """
        from unittest.mock import MagicMock

        from data.models import OHLCBar

        # Bar for 2023-03-06: anchors the SPY "position" at $10,000.
        bar1 = MagicMock(spec=OHLCBar)
        bar1.open      = 400.0
        bar1.timestamp = datetime(2023, 3, 6, tzinfo=UTC)

        # Bar for 2023-03-08: +1% SPY (400 → 404).
        bar2 = MagicMock(spec=OHLCBar)
        bar2.open      = 404.0
        bar2.timestamp = datetime(2023, 3, 8, tzinfo=UTC)

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = [bar1, bar2]

        delta = _compute_vs_spy_delta(
            self._EQUITY, mock_cache, starting_cash=self._EQUITY[0][1],
        )

        # bot +5%, SPY +1% → delta = +4 pp = 0.04
        assert isinstance(delta, float), f"Expected float, got {type(delta)}"
        assert pytest.approx(delta, rel=1e-3) == 0.04

    def test_na_string_when_spy_absent(self) -> None:
        """When SPY has no bars, a descriptive string is returned (no crash)."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = []  # SPY not in cache

        delta = _compute_vs_spy_delta(
            self._EQUITY, mock_cache, starting_cash=self._EQUITY[0][1],
        )

        assert isinstance(delta, str), f"Expected str N/A, got {type(delta)}"
        assert "SPY" in delta, f"Expected SPY mention in N/A message: {delta}"

    def test_na_string_on_cache_error(self) -> None:
        """When cache.read_ohlcv raises, a descriptive string is returned."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = RuntimeError("db locked")

        delta = _compute_vs_spy_delta(
            self._EQUITY, mock_cache, starting_cash=self._EQUITY[0][1],
        )

        assert isinstance(delta, str), f"Expected str N/A on error, got {type(delta)}"

    def test_na_string_when_equity_empty(self) -> None:
        """When the equity series is empty, a descriptive string is returned."""
        from unittest.mock import MagicMock

        mock_cache = MagicMock()
        delta = _compute_vs_spy_delta([], mock_cache, starting_cash=10_000.0)

        assert isinstance(delta, str)
        mock_cache.read_ohlcv.assert_not_called()


# ── _build_equity_figure ─────────────────────────────────────────────────────

class TestBuildEquityFigure:
    """Unit tests for the equity-curve Figure builder (portfolio + SPY + initial-funds).

    Equity ticks use the standard NYSE UTC schedule (13:30 open, 20:00
    close) so the ``_spy_benchmark_series`` open/close-phase classifier
    routes each tick to the correct OHLCV field.
    """

    # Two trading days × two ticks per day — matches the live tick schedule.
    _SERIES = [
        (datetime(2026, 2, 2, 13, 30, tzinfo=UTC), 100_000.0),  # day-1 open
        (datetime(2026, 2, 2, 20,  0, tzinfo=UTC), 101_000.0),  # day-1 close
        (datetime(2026, 2, 3, 13, 30, tzinfo=UTC),  99_500.0),  # day-2 open
        (datetime(2026, 2, 3, 20,  0, tzinfo=UTC), 100_200.0),  # day-2 close
    ]

    @staticmethod
    def _legend_texts(fig) -> list[str]:
        """Return legend label text strings in order — small readability helper."""
        return [t.get_text() for t in fig.axes[0].get_legend().get_texts()]

    def test_three_lines_when_spy_present(self) -> None:
        """With SPY bars in the cache, the chart consumes the same tick-aligned
        benchmark series as ``_compute_vs_spy_delta``.

        With two SPY bars (one per date) and four equity ticks, the new
        helper emits four (timestamp, $-value) pairs — one per equity
        tick — switching between ``bar.open`` and ``bar.close`` based on
        each tick's intraday phase.  The anchor is the first tick's price
        (bar1.open), and ``starting_cash`` sizes the SPY "position".
        """
        from unittest.mock import MagicMock

        import matplotlib.pyplot as plt

        from data.models import OHLCBar

        # Two SPY bars, one per date.  Both ``open`` and ``close`` are set
        # so the open- and close-phase ticks both have a price to read.
        bar1 = MagicMock(spec=OHLCBar)
        bar1.timestamp = datetime(2026, 2, 2, tzinfo=UTC)
        bar1.open      = 400.0     # anchor — first equity tick is open phase
        bar1.close     = 405.0     # day-1 close-phase reading

        bar2 = MagicMock(spec=OHLCBar)
        bar2.timestamp = datetime(2026, 2, 3, tzinfo=UTC)
        bar2.open      = 410.0
        bar2.close     = 420.0

        cache = MagicMock()
        cache.read_ohlcv.return_value = [bar1, bar2]

        fig = _build_equity_figure(self._SERIES, cache=cache, starting_cash=100_000.0)
        try:
            assert self._legend_texts(fig) == [
                "Portfolio", "SPY (rebased)", "Initial funds",
            ]

            # SPY line is the second plotted Line2D — one point per equity tick.
            spy_line = fig.axes[0].lines[1]
            spy_ys   = list(spy_line.get_ydata())
            assert len(spy_ys) == 4, (
                f"SPY series must align tick-for-tick with the portfolio "
                f"(4 ticks expected); got {len(spy_ys)} points"
            )

            # spy_shares = 100_000 / 400 = 250.  Each tick: spy_shares × price.
            assert spy_ys[0] == pytest.approx(250 * 400.0, rel=1e-9)  # day-1 open
            assert spy_ys[1] == pytest.approx(250 * 405.0, rel=1e-9)  # day-1 close
            assert spy_ys[2] == pytest.approx(250 * 410.0, rel=1e-9)  # day-2 open
            assert spy_ys[3] == pytest.approx(250 * 420.0, rel=1e-9)  # day-2 close
        finally:
            plt.close(fig)

    def test_two_lines_when_spy_empty(self) -> None:
        """When SPY is absent from the cache, the SPY line is skipped silently."""
        from unittest.mock import MagicMock

        import matplotlib.pyplot as plt

        cache = MagicMock()
        cache.read_ohlcv.return_value = []  # SPY not in cache

        fig = _build_equity_figure(self._SERIES, cache=cache, starting_cash=100_000.0)
        try:
            assert self._legend_texts(fig) == ["Portfolio", "Initial funds"]
        finally:
            plt.close(fig)

    def test_two_lines_and_logs_when_spy_raises(self, caplog) -> None:
        """When cache.read_ohlcv raises, SPY is skipped and the error is logged."""
        import logging
        from unittest.mock import MagicMock

        import matplotlib.pyplot as plt

        cache = MagicMock()
        cache.read_ohlcv.side_effect = RuntimeError("db locked")

        with caplog.at_level(logging.ERROR, logger="backtest.reporting"):
            fig = _build_equity_figure(
                self._SERIES, cache=cache, starting_cash=100_000.0,
            )
        try:
            assert self._legend_texts(fig) == ["Portfolio", "Initial funds"]
            # logger.exception(...) emits at ERROR level with traceback info.
            assert any(
                "SPY" in r.getMessage() for r in caplog.records
            ), f"Expected an SPY error log; got: {[r.getMessage() for r in caplog.records]}"
        finally:
            plt.close(fig)

    def test_initial_funds_is_dashed(self) -> None:
        """The initial-funds line is rendered with a dashed linestyle for visual distinction."""
        from unittest.mock import MagicMock

        import matplotlib.pyplot as plt

        cache = MagicMock()
        cache.read_ohlcv.return_value = []

        fig = _build_equity_figure(self._SERIES, cache=cache, starting_cash=100_000.0)
        try:
            # axhline produces a Line2D; locate it by its label.
            initial_line = next(
                ln for ln in fig.axes[0].lines if ln.get_label() == "Initial funds"
            )
            # matplotlib normalises '--' to 'dashed' or keeps it as '--' depending
            # on call site; accept either to keep the test robust.
            assert initial_line.get_linestyle() in {"--", "dashed"}
        finally:
            plt.close(fig)

    def test_four_lines_when_matched_series_passed(self) -> None:
        """When ``matched_series`` is supplied, a fourth line is drawn between SPY and initial.

        The matched-exposure series is built from the bot's per-tick equity
        exposure; the chart consumer only needs the resulting (timestamp,
        $-value) pairs.  We pass a hand-crafted series here and assert the
        legend order + ydata round-trips correctly — no SPY mock needed.
        """
        from unittest.mock import MagicMock

        import matplotlib.pyplot as plt

        from data.models import OHLCBar

        # Same SPY fixture as ``test_three_lines_when_spy_present`` — two
        # bars, one per date, both open and close populated.
        bar1 = MagicMock(spec=OHLCBar)
        bar1.timestamp = datetime(2026, 2, 2, tzinfo=UTC)
        bar1.open      = 400.0
        bar1.close     = 405.0

        bar2 = MagicMock(spec=OHLCBar)
        bar2.timestamp = datetime(2026, 2, 3, tzinfo=UTC)
        bar2.open      = 410.0
        bar2.close     = 420.0

        cache = MagicMock()
        cache.read_ohlcv.return_value = [bar1, bar2]

        # A synthetic matched-exposure series — values are arbitrary; we
        # only need the chart to plot them where we tell it to.
        matched = [
            (datetime(2026, 2, 2, 13, 30, tzinfo=UTC), 100_000.0),
            (datetime(2026, 2, 2, 20,  0, tzinfo=UTC), 100_250.0),
            (datetime(2026, 2, 3, 13, 30, tzinfo=UTC), 100_500.0),
            (datetime(2026, 2, 3, 20,  0, tzinfo=UTC), 100_750.0),
        ]

        fig = _build_equity_figure(
            self._SERIES,
            cache=cache,
            starting_cash=100_000.0,
            matched_series=matched,
        )
        try:
            assert self._legend_texts(fig) == [
                "Portfolio",
                "SPY (rebased)",
                "Matched-exposure (rebased)",
                "Initial funds",
            ]

            # Matched line is plotted 3rd (index 2) — assert the y-values
            # match what we passed in, so the chart faithfully renders the
            # caller's series rather than re-deriving anything internally.
            matched_line = fig.axes[0].lines[2]
            matched_ys   = list(matched_line.get_ydata())
            assert matched_ys == [pt[1] for pt in matched]
        finally:
            plt.close(fig)

    def test_no_matched_line_when_matched_series_none(self) -> None:
        """``matched_series=None`` (default) preserves the three-line layout.

        Existing callers that haven't been migrated yet must keep working.
        """
        from unittest.mock import MagicMock

        import matplotlib.pyplot as plt

        cache = MagicMock()
        cache.read_ohlcv.return_value = []

        fig = _build_equity_figure(
            self._SERIES, cache=cache, starting_cash=100_000.0,
            matched_series=None,
        )
        try:
            assert "Matched-exposure (rebased)" not in self._legend_texts(fig)
        finally:
            plt.close(fig)


# ── _annualised_sharpe ───────────────────────────────────────────────────────

class TestAnnualisedSharpe:
    """Unit tests for the shared Sharpe helper.

    The helper is used to compute three Sharpe ratios that must be
    directly comparable (bot, SPY, matched-exposure).  We pin the
    annualisation factor here so future refactors can't silently
    introduce a different convention for one of the three.
    """

    def test_nan_for_empty_series(self) -> None:
        """No data → NaN.  Caller decides how to format."""
        result = _annualised_sharpe([], ticks_per_day=1)
        assert result != result  # NaN check

    def test_nan_for_single_tick(self) -> None:
        """One tick → zero returns → NaN."""
        result = _annualised_sharpe(
            [(datetime(2023, 3, 6, tzinfo=UTC), 100.0)], ticks_per_day=1,
        )
        assert result != result

    def test_nan_for_zero_variance(self) -> None:
        """A flat series has zero std → NaN (no risk-adjusted signal)."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 100.0),
            (datetime(2023, 3, 8, tzinfo=UTC), 100.0),
        ]
        result = _annualised_sharpe(series, ticks_per_day=1)
        assert result != result

    def test_positive_sharpe_for_consistent_gains(self) -> None:
        """A monotonic +1%/tick series → positive, finite Sharpe."""
        series = [
            (datetime(2023, 3, 6, tzinfo=UTC), 100.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 101.0),
            (datetime(2023, 3, 8, tzinfo=UTC), 102.01),
        ]
        result = _annualised_sharpe(series, ticks_per_day=1)
        # Mean / std is large (variance near zero with rounding), but
        # std isn't exactly zero on float arithmetic — we just check sign.
        assert result > 0
        assert result == result  # not NaN

    def test_annualisation_scales_with_ticks_per_day(self) -> None:
        """sqrt(252 × ticks_per_day) scaling: two-ticks-per-day doubles input freq.

        The helper's job is to multiply the per-tick Sharpe by the
        annualisation factor; doubling ``ticks_per_day`` should
        multiply the result by ``sqrt(2)``.
        """
        # A series whose per-tick mean and std are both stable enough
        # that the ratio matters — use alternating +1% / -0.5%.
        series = [
            (datetime(2023, 3, 1, tzinfo=UTC), 100.0),
            (datetime(2023, 3, 2, tzinfo=UTC), 101.0),
            (datetime(2023, 3, 3, tzinfo=UTC), 100.495),  # -0.5%
            (datetime(2023, 3, 4, tzinfo=UTC), 101.5),    # ~+1%
            (datetime(2023, 3, 5, tzinfo=UTC), 100.99),   # -0.5%
        ]
        s_tpd1 = _annualised_sharpe(series, ticks_per_day=1)
        s_tpd2 = _annualised_sharpe(series, ticks_per_day=2)
        assert s_tpd2 == pytest.approx(s_tpd1 * (2 ** 0.5), rel=1e-9)


# ── _avg_exposure_pct ────────────────────────────────────────────────────────

class TestAvgExposurePct:
    """Unit tests for the average-exposure helper."""

    def test_full_cash_is_zero(self) -> None:
        """When cash == total at every tick, exposure is 0%."""
        equity = [
            (datetime(2023, 3, 6, tzinfo=UTC), 10_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 10_000.0),
        ]
        cash = [10_000.0, 10_000.0]
        assert _avg_exposure_pct(equity, cash) == pytest.approx(0.0)

    def test_no_cash_is_one(self) -> None:
        """When cash == 0 at every tick, exposure is 100%."""
        equity = [
            (datetime(2023, 3, 6, tzinfo=UTC), 10_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 10_500.0),
        ]
        cash = [0.0, 0.0]
        assert _avg_exposure_pct(equity, cash) == pytest.approx(1.0)

    def test_half_cash_is_half(self) -> None:
        """50% cash at every tick → 50% mean exposure."""
        equity = [
            (datetime(2023, 3, 6, tzinfo=UTC), 10_000.0),
            (datetime(2023, 3, 7, tzinfo=UTC), 10_000.0),
        ]
        cash = [5_000.0, 5_000.0]
        assert _avg_exposure_pct(equity, cash) == pytest.approx(0.5)

    def test_clamped_when_cash_exceeds_total(self) -> None:
        """Cash > total (rounding edge case) → clamped to 0% rather than negative."""
        equity = [
            (datetime(2023, 3, 6, tzinfo=UTC), 10_000.0),
        ]
        cash = [11_000.0]  # impossible but defensive
        result = _avg_exposure_pct(equity, cash)
        assert result == pytest.approx(0.0)

    def test_skips_zero_total_snapshots(self) -> None:
        """Zero-total snapshots (degenerate) are excluded from the mean."""
        equity = [
            (datetime(2023, 3, 6, tzinfo=UTC),     0.0),  # skipped
            (datetime(2023, 3, 7, tzinfo=UTC), 10_000.0), # 60% invested
        ]
        cash = [0.0, 4_000.0]
        assert _avg_exposure_pct(equity, cash) == pytest.approx(0.6)

    def test_nan_when_no_valid_snapshots(self) -> None:
        """All zero-total → NaN (no usable data)."""
        equity = [(datetime(2023, 3, 6, tzinfo=UTC), 0.0)]
        cash   = [0.0]
        result = _avg_exposure_pct(equity, cash)
        assert result != result


# ── _matched_exposure_series ─────────────────────────────────────────────────

class TestMatchedExposureSeries:
    """Unit tests for the matched-exposure benchmark builder.

    Concept: at every tick the synthetic portfolio holds ``equity_pct ×
    SPY + cash_pct × cash`` (cash earns 0%) and compounds from
    ``starting_cash``.  We pin three boundary cases (100% cash → flat;
    100% equity → tracks SPY; 50/50 → halfway) plus the N/A
    propagation paths.
    """

    # Shared synthetic timeline — two ticks, SPY +10%.
    _T0 = datetime(2026, 2, 2, 13, 30, tzinfo=UTC)
    _T1 = datetime(2026, 2, 2, 20,  0, tzinfo=UTC)
    _SPY_UP_10PCT = [(_T0, 10_000.0), (_T1, 11_000.0)]

    def test_full_cash_is_flat(self) -> None:
        """100% cash exposure → matched value never moves regardless of SPY."""
        equity = [(self._T0, 10_000.0), (self._T1, 10_000.0)]
        cash   = [10_000.0, 10_000.0]
        result = _matched_exposure_series(
            equity, cash, self._SPY_UP_10PCT, starting_cash=10_000.0,
        )
        assert isinstance(result, list)
        assert result[0][1] == pytest.approx(10_000.0)
        assert result[1][1] == pytest.approx(10_000.0)

    def test_full_equity_tracks_spy(self) -> None:
        """100% equity exposure → matched series matches SPY 1:1."""
        equity = [(self._T0, 10_000.0), (self._T1, 11_000.0)]
        cash   = [0.0, 0.0]
        result = _matched_exposure_series(
            equity, cash, self._SPY_UP_10PCT, starting_cash=10_000.0,
        )
        assert isinstance(result, list)
        assert result[0][1] == pytest.approx(10_000.0)
        assert result[1][1] == pytest.approx(11_000.0)

    def test_half_exposure_is_halfway(self) -> None:
        """50% equity / 50% cash → matched series captures half the SPY move."""
        equity = [(self._T0, 10_000.0), (self._T1, 10_500.0)]
        cash   = [5_000.0, 5_250.0]    # always 50% cash
        result = _matched_exposure_series(
            equity, cash, self._SPY_UP_10PCT, starting_cash=10_000.0,
        )
        assert isinstance(result, list)
        # Lagged exposure: tick 0 → 1 uses 50% exposure × +10% SPY = +5%.
        assert result[1][1] == pytest.approx(10_500.0)

    def test_uses_lagged_exposure(self) -> None:
        """Compounding uses *start-of-period* exposure, not end-of-period.

        At tick 0 the bot was 100% equity; at tick 1 it had rebalanced to
        100% cash.  The return from tick 0 → 1 should reflect the 100%
        equity that was held entering the period, not the cash position
        held at exit (which represents future information).
        """
        equity = [(self._T0, 10_000.0), (self._T1, 11_000.0)]
        cash   = [0.0, 11_000.0]   # 100% → 0% over the period
        result = _matched_exposure_series(
            equity, cash, self._SPY_UP_10PCT, starting_cash=10_000.0,
        )
        assert isinstance(result, list)
        # 100% exposure during the period → full SPY +10%.
        assert result[1][1] == pytest.approx(11_000.0)

    def test_propagates_na_string_from_spy(self) -> None:
        """When SPY is unavailable, the same N/A string is returned verbatim."""
        result = _matched_exposure_series(
            equity=[(self._T0, 10_000.0)],
            cash=[0.0],
            spy_series="N/A — SPY not in cache (run backtest_fetch with SPY)",
            starting_cash=10_000.0,
        )
        assert isinstance(result, str)
        assert "SPY not in cache" in result

    def test_na_when_equity_empty(self) -> None:
        """Empty equity series → N/A string (matches surrounding helpers)."""
        result = _matched_exposure_series(
            equity=[], cash=[], spy_series=self._SPY_UP_10PCT, starting_cash=10_000.0,
        )
        assert isinstance(result, str)

    def test_na_when_lengths_mismatch(self) -> None:
        """Defensive: cash and equity must be the same length."""
        result = _matched_exposure_series(
            equity=[(self._T0, 10_000.0), (self._T1, 10_000.0)],
            cash=[5_000.0],  # too short
            spy_series=self._SPY_UP_10PCT,
            starting_cash=10_000.0,
        )
        assert isinstance(result, str)
        assert "length mismatch" in result.lower()


# ── _write_metrics: new benchmark rows ───────────────────────────────────────

class TestWriteMetricsBenchmarks:
    """Unit tests for the SPY-Sharpe, matched-Sharpe, matched-delta, and
    avg-exposure rows added on top of the original metrics file."""

    _SIMPLE_SERIES = [
        (datetime(2023, 3, 6, tzinfo=UTC), 100_000.0),
        (datetime(2023, 3, 7, tzinfo=UTC), 105_000.0),
    ]

    def test_spy_sharpe_float_written(self, tmp_path: Path) -> None:
        """A float SPY Sharpe is rendered as a 2dp bold number."""
        _write_metrics(
            self._SIMPLE_SERIES, tmp_path / "metrics.md",
            spy_sharpe=1.42,
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "SPY Sharpe" in text
        assert "**1.42**" in text

    def test_spy_sharpe_na_string_written(self, tmp_path: Path) -> None:
        """An N/A string is written in italics rather than crashing."""
        _write_metrics(
            self._SIMPLE_SERIES, tmp_path / "metrics.md",
            spy_sharpe="N/A — SPY not in cache",
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "_N/A — SPY not in cache_" in text

    def test_matched_sharpe_float_written(self, tmp_path: Path) -> None:
        """Matched-exposure Sharpe renders alongside SPY Sharpe."""
        _write_metrics(
            self._SIMPLE_SERIES, tmp_path / "metrics.md",
            matched_sharpe=0.85,
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "Matched-exposure SPY Sharpe" in text
        assert "**0.85**" in text

    def test_vs_matched_delta_float_written(self, tmp_path: Path) -> None:
        """vs-matched-exposure delta renders as a signed percentage."""
        _write_metrics(
            self._SIMPLE_SERIES, tmp_path / "metrics.md",
            vs_matched_delta=0.012,
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "vs matched-exposure SPY" in text
        assert "+1.20%" in text

    def test_avg_exposure_written_as_percentage(self, tmp_path: Path) -> None:
        """Avg equity exposure renders as a 1dp percentage."""
        _write_metrics(
            self._SIMPLE_SERIES, tmp_path / "metrics.md",
            avg_exposure_pct=0.624,
        )
        text = (tmp_path / "metrics.md").read_text()
        assert "Avg bot equity exposure" in text
        assert "**62.4%**" in text

    def test_avg_exposure_nan_written_as_na(self, tmp_path: Path) -> None:
        """NaN exposure → italic N/A (no NaN leaking into the markdown)."""
        _write_metrics(
            self._SIMPLE_SERIES, tmp_path / "metrics.md",
            avg_exposure_pct=float("nan"),
        )
        text = (tmp_path / "metrics.md").read_text()
        # Avoid asserting on the raw float repr — just confirm the row
        # contains the italic N/A and not a stray "nan".
        assert "Avg bot equity exposure: _N/A_" in text
        assert "nan%" not in text.lower()

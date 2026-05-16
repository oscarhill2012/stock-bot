"""Forward-return backfill must record the actual bar date used.

Bug context: when a target horizon falls on a market closure the backfill
silently uses the next available bar.  Snapshots should record which bar
was actually consulted so downstream RAG / supervision can see the horizon
error (i.e. the gap between the target calendar date and the bar date that
was actually used).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

from backtest.reporting import _backfill_forward_returns
from data.models import OHLCBar

# ── helpers ───────────────────────────────────────────────────────────────────

def _write_snapshot(
    decisions_dir: Path,
    ticker: str,
    fill_price: float,
    as_of: str,
    side: str = "buy",
) -> Path:
    """Write a minimal decision-snapshot JSON file and return its path.

    Parameters
    ----------
    decisions_dir:
        Directory in which the snapshot file should be written.
    ticker:
        Stock ticker symbol (e.g. ``"AAPL"``).
    fill_price:
        Entry price at which the order was filled.
    as_of:
        ISO-8601 string for the tick timestamp.
    side:
        Order side — ``"buy"`` or ``"sell"``.

    Returns
    -------
    Path
        Path to the newly written JSON file.
    """
    snapshot = {
        "ticker": ticker,
        "side": side,
        "execution": {"fill_price": fill_price},
        "tick": {"as_of": as_of},
        "forward_returns": None,
    }
    path = decisions_dir / f"2024-01-05__{ticker}__{side}.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


def _make_bar(ts: datetime, close: float) -> MagicMock:
    """Return a mock OHLCBar with the given timestamp and close price.

    Parameters
    ----------
    ts:
        The bar's timestamp (timezone-aware).
    close:
        The bar's closing price.

    Returns
    -------
    MagicMock
        A mock that satisfies ``bar.timestamp`` and ``bar.close`` access.
    """
    bar = MagicMock(spec=OHLCBar)
    bar.timestamp = ts
    bar.close = close
    return bar


# ── tests ─────────────────────────────────────────────────────────────────────

class TestForwardReturnActualDate:
    """Backfill must write ``forward_returns_actual_date`` per horizon."""

    def test_actual_date_recorded_when_bar_found(self, tmp_path: Path) -> None:
        """When a bar is found, its date is written to forward_returns_actual_date.

        The entry is 2024-01-05 (+1d target = 2024-01-06).  The cache skips
        that date (e.g. weekend) and returns the first bar on 2024-01-08.
        The snapshot must record ``"+1d": "2024-01-08"``.
        """
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        path = _write_snapshot(
            decisions_dir,
            ticker="AAPL",
            fill_price=100.0,
            as_of="2024-01-05T14:30:00+00:00",
        )

        # Bar lands 3 calendar days after the +1d target — simulates a holiday gap.
        bar_ts = datetime(2024, 1, 8, 14, 30, tzinfo=UTC)
        bar = _make_bar(bar_ts, close=110.0)

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = [bar]

        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[1])

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "forward_returns_actual_date" in data, (
            "Snapshot should carry forward_returns_actual_date after backfill"
        )
        assert data["forward_returns_actual_date"]["+1d"] == "2024-01-08"

    def test_actual_date_none_when_no_bar(self, tmp_path: Path) -> None:
        """When no bar is available for a horizon, its actual-date entry is None.

        This preserves a 1-to-1 key correspondence between ``forward_returns``
        and ``forward_returns_actual_date`` so consumers can zip the two dicts.
        """
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        path = _write_snapshot(
            decisions_dir,
            ticker="AAPL",
            fill_price=100.0,
            as_of="2024-01-05T14:30:00+00:00",
        )

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = []  # no bar available

        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[5])

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "forward_returns_actual_date" in data
        assert data["forward_returns_actual_date"]["+5d"] is None

    def test_actual_date_keys_match_return_keys(self, tmp_path: Path) -> None:
        """Keys of forward_returns_actual_date must mirror forward_returns exactly.

        For multiple horizons, every ``+Nd`` key present in ``forward_returns``
        must also be present in ``forward_returns_actual_date``.
        """
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        path = _write_snapshot(
            decisions_dir,
            ticker="MSFT",
            fill_price=200.0,
            as_of="2024-01-05T14:30:00+00:00",
        )

        bar1  = _make_bar(datetime(2024, 1,  8, tzinfo=UTC), close=202.0)
        bar5  = _make_bar(datetime(2024, 1, 12, tzinfo=UTC), close=208.0)
        bar20 = _make_bar(datetime(2024, 1, 29, tzinfo=UTC), close=220.0)

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = [[bar1], [bar5], [bar20]]

        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[1, 5, 20])

        data = json.loads(path.read_text(encoding="utf-8"))
        fwd      = data["forward_returns"]
        fwd_date = data["forward_returns_actual_date"]

        assert set(fwd.keys()) == set(fwd_date.keys()), (
            "forward_returns and forward_returns_actual_date must have identical keys"
        )

    def test_actual_date_iso_format(self, tmp_path: Path) -> None:
        """Recorded actual dates must be plain ISO date strings (YYYY-MM-DD)."""
        decisions_dir = tmp_path / "decisions"
        decisions_dir.mkdir()
        path = _write_snapshot(
            decisions_dir,
            ticker="GOOG",
            fill_price=150.0,
            as_of="2024-01-05T14:30:00+00:00",
        )

        bar_ts = datetime(2024, 1, 8, 9, 30, tzinfo=UTC)
        bar = _make_bar(bar_ts, close=155.0)

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = [bar]

        _backfill_forward_returns(decisions_dir, mock_cache, horizons_days=[1])

        data = json.loads(path.read_text(encoding="utf-8"))
        actual_date_str = data["forward_returns_actual_date"]["+1d"]

        # Must be parseable as a plain date and match YYYY-MM-DD format.
        assert actual_date_str == "2024-01-08"
        parsed = date.fromisoformat(actual_date_str)
        assert parsed == date(2024, 1, 8)

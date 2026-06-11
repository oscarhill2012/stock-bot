# tests/unit/test_debug_cache_audit.py
"""Tests for the new deep-check functions in scripts/debug_cache_audit.py.

Covers:
- deep_check_temporal_density: zero-month inclusion, correct month bucketing
- deep_check_first_tick: has_10k/has_10q true and false cases,
  8-K staleness boundary (exactly staleness_days before start counts;
  one day older does not), insider and news lookback counting
- DOMAINS registry: notable_holders has enabled=False
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from scripts.debug_cache_audit import (
    DOMAINS,
    deep_check_first_tick,
    deep_check_temporal_density,
)

# ---------------------------------------------------------------------------
# Helpers — build minimal in-memory SQLite fixtures for each test.
# ---------------------------------------------------------------------------

def _make_con() -> sqlite3.Connection:
    """Return an in-memory SQLite connection."""
    return sqlite3.connect(":memory:")


def _create_filings(con: sqlite3.Connection) -> None:
    """Create a minimal filings table matching the cache schema."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS filings (
            accession_no TEXT PRIMARY KEY,
            ticker       TEXT,
            form_type    TEXT,
            filed_at     DATETIME,
            title        TEXT,
            url          TEXT,
            risk_factors_excerpt TEXT,
            mda_excerpt  TEXT
        )
        """
    )


def _create_news(con: sqlite3.Connection) -> None:
    """Create a minimal news_articles table matching the cache schema."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS news_articles (
            ticker       TEXT,
            url          TEXT,
            headline     TEXT,
            summary      TEXT,
            source       TEXT,
            published_at DATETIME,
            sentiment    TEXT
        )
        """
    )


def _create_insider_trades(con: sqlite3.Connection) -> None:
    """Create a minimal insider_trades table matching the cache schema."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS insider_trades (
            ticker    TEXT,
            filed_at  DATETIME,
            insider_name TEXT
        )
        """
    )


def _create_ohlcv(con: sqlite3.Connection) -> None:
    """Create a minimal ohlcv_bars table matching the cache schema."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_bars (
            ticker TEXT,
            ts     DATETIME
        )
        """
    )


# ---------------------------------------------------------------------------
# Tests for deep_check_temporal_density
# ---------------------------------------------------------------------------

class TestTemporalDensity:
    """Temporal density check returns per-month counts including zero months."""

    def test_zero_months_present_in_output(self):
        """Months with no rows MUST appear with count 0 — the truncation
        signature is a sparse tail-only distribution, not a missing key."""
        con = _make_con()
        _create_filings(con)

        # Insert one row in January 2023; February and March should be 0.
        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-001", "AAPL", "10-K", "2023-01-15", "Annual", "http://x", None, None),
        )
        con.commit()

        result = deep_check_temporal_density(
            con,
            domains=[d for d in DOMAINS if d.name == "filings"],
            tickers=["AAPL"],
            start_iso="2023-01-01",
            end_iso="2023-03-31",
        )

        assert "filings" in result
        aapl = result["filings"]["AAPL"]

        # All three months present in output.
        assert "2023-01" in aapl
        assert "2023-02" in aapl
        assert "2023-03" in aapl

        # Only January has a row.
        assert aapl["2023-01"] == 1
        assert aapl["2023-02"] == 0
        assert aapl["2023-03"] == 0

    def test_row_lands_in_correct_month_bucket(self):
        """A filing filed on 2023-02-28 should appear in bucket '2023-02'."""
        con = _make_con()
        _create_filings(con)

        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-001", "MSFT", "10-Q", "2023-02-28", "Quarterly", "http://y", None, None),
        )
        con.commit()

        result = deep_check_temporal_density(
            con,
            domains=[d for d in DOMAINS if d.name == "filings"],
            tickers=["MSFT"],
            start_iso="2023-01-01",
            end_iso="2023-03-31",
        )

        aapl = result["filings"]["MSFT"]
        assert aapl["2023-02"] == 1
        assert aapl["2023-01"] == 0
        assert aapl["2023-03"] == 0

    def test_multiple_rows_same_month_counted_together(self):
        """Two rows in the same month produce count 2 in that bucket."""
        con = _make_con()
        _create_filings(con)

        for day, acc in [("2023-03-05", "acc-001"), ("2023-03-20", "acc-002")]:
            con.execute(
                "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
                (acc, "GOOG", "8-K", day, "Event", "http://z", None, None),
            )
        con.commit()

        result = deep_check_temporal_density(
            con,
            domains=[d for d in DOMAINS if d.name == "filings"],
            tickers=["GOOG"],
            start_iso="2023-01-01",
            end_iso="2023-03-31",
        )

        assert result["filings"]["GOOG"]["2023-03"] == 2

    def test_ohlcv_domain_uses_ts_column(self):
        """Density check works for ohlcv domain which uses the ``ts`` PIT column."""
        con = _make_con()
        _create_ohlcv(con)

        # Two bars in January, none in February.
        for day in ("2023-01-10", "2023-01-11"):
            con.execute("INSERT INTO ohlcv_bars VALUES (?,?)", ("SPY", day))
        con.commit()

        result = deep_check_temporal_density(
            con,
            domains=[d for d in DOMAINS if d.name == "ohlcv"],
            tickers=["SPY"],
            start_iso="2023-01-01",
            end_iso="2023-02-28",
        )

        assert result["ohlcv"]["SPY"]["2023-01"] == 2
        assert result["ohlcv"]["SPY"]["2023-02"] == 0

    def test_disabled_domain_excluded_from_output(self):
        """Disabled domains should not appear in the density result."""
        con = _make_con()

        result = deep_check_temporal_density(
            con,
            domains=DOMAINS,       # full list — includes politician_trades (disabled)
            tickers=["AAPL"],
            start_iso="2023-01-01",
            end_iso="2023-01-31",
        )

        assert "politician_trades" not in result


# ---------------------------------------------------------------------------
# Tests for deep_check_first_tick
# ---------------------------------------------------------------------------

class TestFirstTick:
    """First-tick serviceability: at t=start, can the analyst be served?"""

    # Config knobs from config/data.json defaults.
    _STALENESS = 90     # filings_8k_staleness_days
    _INSIDER   = 30     # insider_lookback_days
    _NEWS      = 7      # news_lookback_days

    def _run(self, con: sqlite3.Connection, start: date) -> dict:
        """Helper: call deep_check_first_tick with fixed test parameters."""
        return deep_check_first_tick(
            con,
            tickers=["AAPL"],
            start_iso=start.isoformat(),
            staleness_days=self._STALENESS,
            insider_lookback_days=self._INSIDER,
            news_lookback_days=self._NEWS,
        )

    # ── 10-K / 10-Q presence ────────────────────────────────────────────────

    def test_has_10k_true_when_row_exists_before_start(self):
        """has_10k should be True when a 10-K is filed before start."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-10k", "AAPL", "10-K", "2022-10-01", "Annual", "u", None, None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["has_10k"] is True

    def test_has_10k_false_when_only_future_row_exists(self):
        """has_10k should be False when the only 10-K is filed AFTER start."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-10k", "AAPL", "10-K", "2023-04-01", "Annual", "u", None, None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["has_10k"] is False

    def test_has_10q_true_when_row_exists_on_start_date(self):
        """A 10-Q filed exactly on start counts (inclusive boundary)."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-10q", "AAPL", "10-Q", "2023-03-01", "Quarterly", "u", None, None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["has_10q"] is True

    def test_has_10q_false_when_no_filings(self):
        """has_10q should be False when the table has no 10-Q rows at all."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        result = self._run(con, date(2023, 3, 1))
        assert result["AAPL"]["has_10q"] is False

    # ── 8-K staleness boundary ───────────────────────────────────────────────

    def test_8k_exactly_staleness_days_before_start_counts(self):
        """An 8-K filed exactly ``staleness_days`` before start is still visible
        (inclusive lower bound).  This is the critical boundary case."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        boundary_date = start - timedelta(days=self._STALENESS)  # exactly 90 days before

        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-8k", "AAPL", "8-K", boundary_date.isoformat(), "Event", "u", None, None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["eightk_count"] == 1

    def test_8k_one_day_outside_staleness_does_not_count(self):
        """An 8-K filed ``staleness_days + 1`` before start is too old and
        should NOT appear in eightk_count."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        too_old = start - timedelta(days=self._STALENESS + 1)

        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-8k", "AAPL", "8-K", too_old.isoformat(), "Event", "u", None, None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["eightk_count"] == 0

    def test_8k_on_start_date_counts(self):
        """An 8-K filed exactly on start is within the window and should count."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        con.execute(
            "INSERT INTO filings VALUES (?,?,?,?,?,?,?,?)",
            ("acc-8k", "AAPL", "8-K", start.isoformat(), "Event", "u", None, None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["eightk_count"] == 1

    # ── Insider lookback ─────────────────────────────────────────────────────

    def test_insider_within_lookback_counted(self):
        """Insider trade filed within lookback window should be counted."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        within = start - timedelta(days=self._INSIDER - 1)  # one day inside lookback

        con.execute(
            "INSERT INTO insider_trades VALUES (?,?,?)",
            ("AAPL", within.isoformat(), "John Smith"),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["insider_count"] == 1

    def test_insider_exactly_at_lookback_boundary_counted(self):
        """Insider trade filed exactly ``insider_lookback_days`` before start
        sits on the inclusive boundary and should be counted."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        boundary = start - timedelta(days=self._INSIDER)

        con.execute(
            "INSERT INTO insider_trades VALUES (?,?,?)",
            ("AAPL", boundary.isoformat(), "Jane Doe"),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["insider_count"] == 1

    def test_insider_too_old_not_counted(self):
        """Insider trade filed one day outside the lookback window should not
        be counted."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        too_old = start - timedelta(days=self._INSIDER + 1)

        con.execute(
            "INSERT INTO insider_trades VALUES (?,?,?)",
            ("AAPL", too_old.isoformat(), "Old Timer"),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["insider_count"] == 0

    # ── News lookback ────────────────────────────────────────────────────────

    def test_news_within_lookback_counted(self):
        """News article published within the news lookback window is counted."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        within = start - timedelta(days=self._NEWS - 1)  # within 7-day window

        con.execute(
            "INSERT INTO news_articles VALUES (?,?,?,?,?,?,?)",
            ("AAPL", "http://n", "Headline", "Summary", "Reuters", within.isoformat(), None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["news_count"] == 1

    def test_news_too_old_not_counted(self):
        """News article published beyond the lookback window is not counted."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        start = date(2023, 3, 1)
        too_old = start - timedelta(days=self._NEWS + 1)

        con.execute(
            "INSERT INTO news_articles VALUES (?,?,?,?,?,?,?)",
            ("AAPL", "http://n", "Old Headline", "Summary", "BBC", too_old.isoformat(), None),
        )
        con.commit()

        result = self._run(con, start)
        assert result["AAPL"]["news_count"] == 0

    def test_multiple_tickers_returned(self):
        """When two tickers are passed, both appear in the result dict."""
        con = _make_con()
        _create_filings(con)
        _create_news(con)
        _create_insider_trades(con)

        result = deep_check_first_tick(
            con,
            tickers=["AAPL", "MSFT"],
            start_iso="2023-03-01",
            staleness_days=self._STALENESS,
            insider_lookback_days=self._INSIDER,
            news_lookback_days=self._NEWS,
        )

        assert "AAPL" in result
        assert "MSFT" in result


# ---------------------------------------------------------------------------
# Tests for the DOMAINS registry — notable_holders must be disabled.
# ---------------------------------------------------------------------------

class TestDomainsRegistry:
    """Sanity checks on the DOMAINS registry entries."""

    def test_notable_holders_is_disabled(self):
        """notable_holders must have enabled=False — it is commented out in
        the fetcher (_build_provider_fns) just like politician_trades."""
        domain_map = {d.name: d for d in DOMAINS}

        assert "notable_holders" in domain_map, \
            "notable_holders entry missing from DOMAINS — expected but disabled"

        assert domain_map["notable_holders"].enabled is False, (
            "notable_holders.enabled should be False because the fetcher "
            "never writes to this table — audit would spuriously WARN otherwise"
        )

    def test_politician_trades_is_disabled(self):
        """Regression guard: politician_trades must still have enabled=False."""
        domain_map = {d.name: d for d in DOMAINS}
        assert domain_map["politician_trades"].enabled is False

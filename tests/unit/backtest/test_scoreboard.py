"""Unit tests for the analyst predictive-power scoreboard (Phase 12).

TDD: these tests were written BEFORE the implementation exists.  They define
the required behaviour; the implementation must pass all of them without
modification to the tests.

All tests are fully offline — no live DB, no network, no LLM calls.  A small
fixture SQLite database is built in ``tmp_path`` using the existing
``AnalystEvidenceRow`` SQLAlchemy model, and a mock ``CachedDataStore`` is
used for the price cache.

Naming follows British English per project conventions.
"""
from __future__ import annotations

import math
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.models import OHLCBar

# The models that must exist in the production code.
from orchestrator.persistence import AnalystEvidenceRow
from orchestrator.persistence import Base as PersistenceBase

# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_evidence_row(
    *,
    analyst: str,
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    lean: str,
    magnitude: float = 0.5,
    confidence: float = 0.7,
) -> AnalystEvidenceRow:
    """Build an ``AnalystEvidenceRow`` without persisting it.

    Parameters mirror the columns used by the scoreboard.  Rationale and
    feature columns are left at their defaults since the scoreboard does not
    read them.
    """
    return AnalystEvidenceRow(
        analyst=analyst,
        ticker=ticker,
        tick_id=tick_id,
        recorded_at=recorded_at,
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
    )


def _make_ohlcbar(
    *,
    ticker: str,
    ts: datetime,
    open: float,
    close: float,
) -> MagicMock:
    """Return a mock ``OHLCBar`` with the specified fields.

    The mock's ``spec`` is ``OHLCBar`` so attribute access is constrained,
    but we override the values we need.
    """
    bar = MagicMock(spec=OHLCBar)
    bar.timestamp = ts
    bar.open  = open
    bar.close = close
    return bar


def _build_fixture_db(tmp_path, rows: list[AnalystEvidenceRow]):
    """Persist ``rows`` to a fresh SQLite fixture and return the path.

    Creates all tables (from the ORM Base used by persistence.py), inserts
    the supplied rows, and returns the Path to the SQLite file.
    """
    db_path = tmp_path / "db.sqlite"
    engine  = create_engine(f"sqlite:///{db_path}", future=True)
    PersistenceBase.metadata.create_all(engine)

    with Session(engine) as s:
        for row in rows:
            s.add(row)
        s.commit()

    return db_path


# ── Open-phase tick timestamps ────────────────────────────────────────────────
# Below 17:00 UTC → open phase; base_price = bar.open.
OPEN_TICK  = datetime(2025, 9, 5, 13, 30, tzinfo=UTC)   # NYSE open phase
CLOSE_TICK = datetime(2025, 9, 5, 20,  0, tzinfo=UTC)   # NYSE close phase

# Three separate tick_ids used across tests.
TICK_A = "tick-2025-09-05-open"
TICK_B = "tick-2025-09-05-close"


# ── Tests: build_analyst_scoreboard ──────────────────────────────────────────

class TestBuildAnalystScoreboard:
    """Unit tests for ``build_analyst_scoreboard`` — the pure scoring function."""

    def _import(self):
        """Lazy import so tests fail clearly if the module doesn't exist yet."""
        from backtest.scoreboard import build_analyst_scoreboard
        return build_analyst_scoreboard

    # ── Case 1: bullish beat → positive score ─────────────────────────────────

    def test_bullish_lean_that_beats_peers_yields_positive_score(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A bullish lean on a ticker that outperforms the cross-sectional mean
        must produce a positive score.

        Tick has two tickers (AAPL, MSFT).  AAPL rises +5 %; MSFT rises +1 %.
        Cross-sectional mean fwd return = (0.05 + 0.01) / 2 = 0.03.
        AAPL excess = 0.05 − 0.03 = +0.02.
        Analyst A is bullish on AAPL → position = +1 → score = +0.02 (positive).
        """
        build = self._import()

        # Build a tick: two analysts each covering one ticker.
        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # AAPL base 100 → fwd 105 (+5 %).  MSFT base 200 → fwd 202 (+1 %).
        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start == date(2025, 9, 5):
                    # base-price query
                    return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=101.0)]
                # forward-price queries
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=104.0, close=105.0)]
            if ticker == "MSFT":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="MSFT", ts=OPEN_TICK, open=200.0, close=201.0)]
                return [_make_ohlcbar(ticker="MSFT", ts=OPEN_TICK, open=201.0, close=202.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        # Locate the technical analyst's "all" row for horizon 1d.
        cell = result.cell(analyst="technical", horizon=1, subset="all")
        assert cell.n > 0, "Expected at least one scored verdict"
        # Bullish AAPL beats peers → score is positive (in bps).
        assert cell.mean_excess_bps > 0, (
            f"Expected positive mean excess; got {cell.mean_excess_bps:.4f} bps"
        )

    # ── Case 2: bullish lagging peers → negative excess ───────────────────────

    def test_bullish_lean_rising_but_lagging_peers_yields_negative_excess(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Core baseline-correction assertion: a bullish lean on a ticker that
        rises absolutely but LAGS peers must produce a NEGATIVE excess score.

        Tick has two tickers.  AAPL rises +1 %; MSFT rises +5 %.
        Mean fwd return = (0.01 + 0.05) / 2 = 0.03.
        AAPL excess = 0.01 − 0.03 = −0.02 (negative despite positive raw return).
        Analyst is bullish on AAPL → position = +1 → score = −0.02 (negative).

        This proves the scoreboard measures SELECTION skill, not market direction.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # AAPL: base 100, fwd 101 (+1 %).  MSFT: base 200, fwd 210 (+5 %).
        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=101.0, close=101.0)]
            if ticker == "MSFT":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="MSFT", ts=OPEN_TICK, open=200.0, close=200.0)]
                return [_make_ohlcbar(ticker="MSFT", ts=OPEN_TICK, open=210.0, close=210.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell = result.cell(analyst="technical", horizon=1, subset="all")
        assert cell.n > 0
        # AAPL lagged peers — score must be NEGATIVE despite +1 % absolute return.
        assert cell.mean_excess_bps < 0, (
            f"Expected negative mean excess for lagging-but-positive return; "
            f"got {cell.mean_excess_bps:.4f} bps"
        )

    # ── Case 3: neutral lean → score exactly 0 ────────────────────────────────

    def test_neutral_lean_score_is_exactly_zero(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A neutral lean produces score = position × excess = 0 × anything = 0.

        The analyst has no directional call so regardless of whether the ticker
        rose or fell, its contribution to the mean-excess must be exactly 0.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="fundamental",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # AAPL: base 100, fwd 110 (+10 %) — large move but analyst is neutral.
        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=110.0, close=110.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell = result.cell(analyst="fundamental", horizon=1, subset="all")
        # score = 0 × excess = 0 for every neutral verdict → mean must be 0.
        assert cell.mean_excess_bps == pytest.approx(0.0, abs=1e-9), (
            f"Neutral lean should score exactly 0; got {cell.mean_excess_bps}"
        )

    # ── Case 4: window-edge verdict — no +20d bar ─────────────────────────────

    def test_window_edge_verdict_excluded_from_long_horizon(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A verdict near the window edge where no +20d bar exists must be
        excluded from the 20d aggregation (n decremented) but still counted
        in 1d and 5d horizons.

        The test fixture has one bullish verdict at date D.  The mock cache
        returns bars for +1d and +5d but returns [] for +20d (window edge).
        We assert:
          - n(horizon=1) == 1
          - n(horizon=5) == 1
          - n(horizon=20) == 0  (excluded — no bar available)
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # One verdict.  +1d and +5d bars exist; +20d does not.
        def _mock_read(ticker, start, end):
            from datetime import timedelta
            if ticker == "AAPL":
                base_date = date(2025, 9, 5)
                if start == base_date:
                    # base-price query
                    return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
                # Check if this is a +20d query (start ≈ base + 20d).
                if start >= base_date + timedelta(days=18):
                    return []  # no bar at +20d — window edge
                # +1d or +5d — return a bar.
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=105.0, close=105.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1, 5, 20])

        cell_1d  = result.cell(analyst="technical", horizon=1,  subset="all")
        cell_5d  = result.cell(analyst="technical", horizon=5,  subset="all")
        cell_20d = result.cell(analyst="technical", horizon=20, subset="all")

        assert cell_1d.n  == 1, f"Expected n=1 at 1d; got {cell_1d.n}"
        assert cell_5d.n  == 1, f"Expected n=1 at 5d; got {cell_5d.n}"
        assert cell_20d.n == 0, f"Expected n=0 at 20d (window edge); got {cell_20d.n}"

    # ── Case 5: per-tick cross-sectional mean — 3-ticker hand-computed ────────

    def test_per_tick_cross_sectional_mean_three_tickers(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """The per-tick cross-sectional mean is computed correctly over three tickers.

        Setup:
          Tick T: three tickers — AAPL (+3 %), MSFT (+1 %), GOOG (−2 %).
          Cross-sectional mean = (0.03 + 0.01 + (−0.02)) / 3 = 0.02 / 3 ≈ 0.006667.

          Analyst is bullish on AAPL, bearish on MSFT, neutral on GOOG.

          AAPL excess = 0.03 − 0.006667 ≈ +0.023333   score = +1 × +0.023333 = +0.023333
          MSFT excess = 0.01 − 0.006667 ≈ +0.003333   score = −1 × +0.003333 = −0.003333
          GOOG excess = (−0.02) − 0.006667 = −0.026667 score = 0 × ... = 0

        Mean score = mean(+0.023333, −0.003333, 0) = +0.02 / 3 ≈ +0.006667
        In bps: 0.006667 × 10_000 ≈ 66.67 bps.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bearish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="GOOG",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # Base prices (open-phase tick, so bar.open is used).
        _BASES = {"AAPL": 100.0, "MSFT": 100.0, "GOOG": 100.0}
        # Forward prices: AAPL +3 %, MSFT +1 %, GOOG −2 %.
        _FWDS  = {"AAPL": 103.0, "MSFT": 101.0, "GOOG": 98.0}

        def _mock_read(ticker, start, end):
            if ticker not in _BASES:
                return []
            if start == date(2025, 9, 5):
                # base-price query (same date as recorded_at)
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK,
                                      open=_BASES[ticker], close=_BASES[ticker])]
            # forward-price query
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK,
                                  open=_FWDS[ticker], close=_FWDS[ticker])]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell_all = result.cell(analyst="technical", horizon=1, subset="all")
        assert cell_all.n == 3, f"Expected 3 verdicts scored; got {cell_all.n}"

        # Hand-computed expected mean excess:
        # scores = [+0.023333, −0.003333, 0]
        # mean   = +0.02 / 3 ≈ +0.006667
        # bps    = 0.006667 × 10_000 ≈ +66.67
        expected_bps = (0.02 / 3) * 10_000
        assert cell_all.mean_excess_bps == pytest.approx(expected_bps, rel=1e-3), (
            f"Expected mean excess ≈ {expected_bps:.2f} bps; "
            f"got {cell_all.mean_excess_bps:.4f} bps"
        )

    # ── Case 6: bullish-only subset filters correctly ─────────────────────────

    def test_bullish_subset_contains_only_bullish_leans(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """The 'bullish' subset includes only bullish-lean verdicts.

        One bullish and one bearish verdict for the same analyst.  The
        bullish-only subset should have n=1; bearish-only n=1; all n=2.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bearish",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # Both tickers rise moderately.
        def _mock_read(ticker, start, end):
            prices = {"AAPL": (100.0, 103.0), "MSFT": (100.0, 101.0)}
            if ticker not in prices:
                return []
            base, fwd = prices[ticker]
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=base, close=base)]
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=fwd, close=fwd)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell_all      = result.cell(analyst="technical", horizon=1, subset="all")
        cell_bullish  = result.cell(analyst="technical", horizon=1, subset="bullish")
        cell_bearish  = result.cell(analyst="technical", horizon=1, subset="bearish")

        assert cell_all.n     == 2, f"Expected n=2 for 'all'; got {cell_all.n}"
        assert cell_bullish.n == 1, f"Expected n=1 for 'bullish'; got {cell_bullish.n}"
        assert cell_bearish.n == 1, f"Expected n=1 for 'bearish'; got {cell_bearish.n}"

    # ── Case 7: base_price uses open for open-phase, close for close-phase ────

    def test_phase_matched_base_price_open_phase(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Open-phase ticks (recorded_at.hour < 17 UTC) use bar.open as base_price.

        If bar.open ≠ bar.close and the scoreboard reads the wrong one, the
        forward return will be wrong.  We verify by running a TWO-ticker tick
        where AAPL and MSFT have very different open/close values — if the
        wrong field (close) is used, the excess calculation produces a
        meaningfully different result.

        Setup (open-phase: base = bar.open):
          AAPL: open=100, close=50.  fwd_close=110.
            Correct base=100: fwd_return = 0.10
            Wrong   base=50 : fwd_return = 1.20

          MSFT: open=200, close=200. fwd_close=202 (+1%).

          Cross-sectional mean with correct bases:
            mean(0.10, 0.01) = 0.055
          AAPL excess (correct) = 0.10 - 0.055 = +0.045 → +450 bps (bullish → positive score)
          AAPL excess (wrong)   = 1.20 - 1.105 = +0.095 → +950 bps — clearly different
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,   # hour=13 < 17 → open phase
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start == date(2025, 9, 5):
                    # Base-price bar: open=100 (should be used), close=50 (wrong).
                    return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=50.0)]
                # Forward bar close = 110.
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=115.0, close=110.0)]
            if ticker == "MSFT":
                if start == date(2025, 9, 5):
                    # MSFT open == close so phase doesn't matter for MSFT itself.
                    return [_make_ohlcbar(ticker="MSFT", ts=OPEN_TICK, open=200.0, close=200.0)]
                return [_make_ohlcbar(ticker="MSFT", ts=OPEN_TICK, open=202.0, close=202.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell = result.cell(analyst="technical", horizon=1, subset="all")
        assert cell.n > 0

        # With correct open-phase bases:
        #   AAPL fwd = 0.10, MSFT fwd = 0.01, cs_mean = 0.055
        #   AAPL excess = +0.045 → score for bullish = +0.045 → 450 bps
        #   MSFT excess = −0.045 → score for neutral = 0
        #   mean = 450 / 2 … wait, neutral contributes 0 to score
        #   all scores: [+0.045, 0.0] → mean = 225 bps
        # Distinguish: if close was used for AAPL (base=50):
        #   AAPL fwd = 1.20, MSFT fwd = 0.01, cs_mean = 0.605
        #   AAPL excess = +0.595 → score = +0.595 → 5950 bps
        #   mean of [+5950, 0] = 2975 bps — clearly different from ~225.
        assert 100 < cell.mean_excess_bps < 1000, (
            f"Expected ~225 bps (open-phase base=100); "
            f"got {cell.mean_excess_bps:.2f} bps.  "
            f"If ~2975, the close (50) was used instead of the open (100)."
        )

    def test_phase_matched_base_price_close_phase(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Close-phase ticks (recorded_at.hour >= 17 UTC) use bar.close as base_price.

        Two-ticker tick so the cross-sectional demean is meaningful.
        AAPL: open=50 (wrong), close=100 (correct for close phase). fwd=110.
        MSFT: open=200, close=200. fwd=202 (+1 %).

        With correct close bases:
          AAPL fwd = 0.10, MSFT fwd = 0.01, cs_mean = 0.055
          AAPL excess (bullish) = +0.045 → 450 bps
          mean all = [+450, 0] / 2 = 225 bps

        With wrong open-as-base for AAPL (base=50):
          AAPL fwd = 1.20, cs_mean = 0.605
          AAPL excess = +0.595 → 5950 bps
          mean all = [+5950, 0] / 2 = 2975 bps
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_B,
                recorded_at=CLOSE_TICK,  # hour=20 >= 17 → close phase
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id=TICK_B,
                recorded_at=CLOSE_TICK,
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start == date(2025, 9, 5):
                    # Base-price bar: open=50 (wrong), close=100 (should be used).
                    return [_make_ohlcbar(ticker="AAPL", ts=CLOSE_TICK, open=50.0, close=100.0)]
                # Forward bar close = 110.
                return [_make_ohlcbar(ticker="AAPL", ts=CLOSE_TICK, open=115.0, close=110.0)]
            if ticker == "MSFT":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="MSFT", ts=CLOSE_TICK, open=200.0, close=200.0)]
                return [_make_ohlcbar(ticker="MSFT", ts=CLOSE_TICK, open=202.0, close=202.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell = result.cell(analyst="technical", horizon=1, subset="all")
        assert cell.n > 0

        # Correct close base → mean_excess_bps ≈ 225 (see docstring for derivation).
        # Wrong open base → ≈ 2975 bps.
        assert 100 < cell.mean_excess_bps < 1000, (
            f"Expected ~225 bps (close-phase base=100); "
            f"got {cell.mean_excess_bps:.2f} bps.  "
            f"If ~2975, the open (50) was used instead of the close (100)."
        )

    # ── Case 8: t-stat is finite and non-zero for meaningful data ─────────────

    def test_t_stat_is_finite_for_multiple_verdicts(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """With multiple scored verdicts, t-stat and p-value must be finite numbers.

        Each tick has TWO tickers (AAPL + MSFT) so the cross-sectional demean
        yields non-zero excess.  AAPL consistently outperforms MSFT across all
        three ticks, so the analyst's bullish AAPL call yields a consistent
        positive excess → positive t-stat.
        """
        build = self._import()

        # Three ticks, each with AAPL (bullish, outperformer) and MSFT (neutral, flat).
        tick_data = [
            ("tick-1", datetime(2025, 9, 5,  13, 30, tzinfo=UTC)),
            ("tick-2", datetime(2025, 9, 8,  13, 30, tzinfo=UTC)),
            ("tick-3", datetime(2025, 9, 9,  13, 30, tzinfo=UTC)),
        ]

        rows = []
        for tid, ts in tick_data:
            rows.append(_make_evidence_row(
                analyst="technical", ticker="AAPL",
                tick_id=tid, recorded_at=ts, lean="bullish",
            ))
            rows.append(_make_evidence_row(
                analyst="technical", ticker="MSFT",
                tick_id=tid, recorded_at=ts, lean="neutral",
            ))

        db_path = _build_fixture_db(tmp_path, rows)

        # AAPL: base=100, fwd=105 (+5 %).  MSFT: base=100, fwd=101 (+1 %).
        # cs_mean = (0.05 + 0.01) / 2 = 0.03.  AAPL excess = +0.02 every tick.
        _BASES = {"AAPL": 100.0, "MSFT": 100.0}
        _FWDS  = {"AAPL": 105.0, "MSFT": 101.0}
        _BASE_DATES = {date(2025, 9, 5), date(2025, 9, 8), date(2025, 9, 9)}

        def _mock_read(ticker, start, end):
            if ticker not in _BASES:
                return []
            if start in _BASE_DATES:
                ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                return [_make_ohlcbar(ticker=ticker, ts=ts_bar,
                                      open=_BASES[ticker], close=_BASES[ticker])]
            # Forward bar.
            ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
            return [_make_ohlcbar(ticker=ticker, ts=ts_bar,
                                  open=_FWDS[ticker], close=_FWDS[ticker])]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell = result.cell(analyst="technical", horizon=1, subset="all")
        # AAPL (bullish) + MSFT (neutral) × 3 ticks = 6 total verdicts.
        assert cell.n == 6, f"Expected n=6; got {cell.n}"

        # t-stat and p must be finite, non-NaN numbers.
        assert math.isfinite(cell.t_stat), f"t-stat must be finite; got {cell.t_stat}"
        assert math.isfinite(cell.p_value), f"p-value must be finite; got {cell.p_value}"
        # AAPL consistently outperforms → positive mean excess → positive t-stat.
        assert cell.t_stat > 0, f"Expected positive t-stat; got {cell.t_stat}"

    # ── Case 9: no-bar base → verdict excluded entirely ───────────────────────

    def test_verdict_excluded_when_no_base_bar(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A verdict whose base-price query returns no bars must be excluded
        from ALL horizons (n=0 everywhere), not just the forward windows.

        This guards against a sentinel value (e.g. 0.0) substituted for the
        missing base causing a spurious score.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # Cache returns nothing for every query.
        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = []

        result = build(db_path=db_path, cache=mock_cache, horizons=[1, 5, 20])

        for h in [1, 5, 20]:
            cell = result.cell(analyst="technical", horizon=h, subset="all")
            assert cell.n == 0, (
                f"Expected n=0 at {h}d when base bar is missing; got {cell.n}"
            )

    # ── Case 10: multi-analyst result has entries for each analyst ────────────

    def test_multiple_analysts_each_have_own_cells(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Results for 'technical' and 'fundamental' analysts are independent cells.

        Two analysts each cover TWO tickers in the same tick so the cross-
        sectional demean is meaningful.  AAPL outperforms MSFT; technical is
        bullish on AAPL (positive score), fundamental is bearish on AAPL
        (negative score).

        AAPL: base=100, fwd=105 (+5 %).  MSFT: base=100, fwd=101 (+1 %).
        cs_mean = 0.03.  AAPL excess = +0.02.
        Technical (bullish AAPL): score = +1 × +0.02 = +0.02 → positive
        Fundamental (bearish AAPL): score = −1 × +0.02 = −0.02 → negative
        """
        build = self._import()

        rows = [
            # Technical covers AAPL (bullish) and MSFT (neutral).
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
            # Fundamental covers AAPL (bearish) and MSFT (neutral).
            _make_evidence_row(
                analyst="fundamental",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bearish",
            ),
            _make_evidence_row(
                analyst="fundamental",
                ticker="MSFT",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        _BASES = {"AAPL": 100.0, "MSFT": 100.0}
        _FWDS  = {"AAPL": 105.0, "MSFT": 101.0}

        def _mock_read(ticker, start, end):
            if ticker not in _BASES:
                return []
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK,
                                      open=_BASES[ticker], close=_BASES[ticker])]
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK,
                                  open=_FWDS[ticker], close=_FWDS[ticker])]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        tech_cell = result.cell(analyst="technical",   horizon=1, subset="all")
        fund_cell = result.cell(analyst="fundamental", horizon=1, subset="all")

        # Both analysts have coverage (AAPL + MSFT = 2 verdicts each).
        assert tech_cell.n == 2, f"Expected n=2 for technical; got {tech_cell.n}"
        assert fund_cell.n == 2, f"Expected n=2 for fundamental; got {fund_cell.n}"

        # Technical is bullish on outperforming AAPL → positive mean excess.
        assert tech_cell.mean_excess_bps > 0, (
            f"Expected positive excess for bullish-AAPL technical; "
            f"got {tech_cell.mean_excess_bps:.2f} bps"
        )
        # Fundamental is bearish on outperforming AAPL → negative mean excess.
        assert fund_cell.mean_excess_bps < 0, (
            f"Expected negative excess for bearish-AAPL fundamental; "
            f"got {fund_cell.mean_excess_bps:.2f} bps"
        )

    # ── Case 11: hit rate computed correctly ─────────────────────────────────

    def test_hit_rate_is_fraction_of_positive_scores(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Hit rate = fraction of non-neutral verdicts with score > 0.

        Two ticks, each with AAPL (bullish) and MSFT (neutral, anchor).
        Tick 1: AAPL outperforms MSFT → AAPL excess > 0 → score > 0 (hit).
        Tick 2: AAPL underperforms MSFT → AAPL excess < 0 → score < 0 (miss).

        Hit rate for analyst 'technical', subset 'all':
          Scored verdicts: [AAPL tick-1 (hit), MSFT tick-1 (0, neutral),
                            AAPL tick-2 (miss), MSFT tick-2 (0, neutral)]
          n = 4 (coverage counts every scored verdict, neutrals included).
          The hit-rate denominator, however, is the NON-NEUTRAL verdicts only:
          the two AAPL calls, of which one is a hit → hit_rate = 1/2 = 0.5.
          The two neutral MSFT verdicts are excluded so abstention is not
          punished as failure.

        Subset 'bullish' only (AAPL verdicts):
          n = 2.  Positive = 1.  hit_rate = 0.5.
        """
        build = self._import()

        rows = [
            # Tick 1: AAPL outperforms — bullish call is a hit.
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id="tick-1",
                recorded_at=datetime(2025, 9, 5, 13, 30, tzinfo=UTC),
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id="tick-1",
                recorded_at=datetime(2025, 9, 5, 13, 30, tzinfo=UTC),
                lean="neutral",
            ),
            # Tick 2: AAPL underperforms — bullish call is a miss.
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id="tick-2",
                recorded_at=datetime(2025, 9, 8, 13, 30, tzinfo=UTC),
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id="tick-2",
                recorded_at=datetime(2025, 9, 8, 13, 30, tzinfo=UTC),
                lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # Base prices: AAPL=100, MSFT=100 in both ticks.
        # Tick-1 fwd: AAPL=105 (+5 %), MSFT=101 (+1 %) → AAPL outperforms.
        # Tick-2 fwd: AAPL=100 (flat 0 %), MSFT=103 (+3 %) → AAPL underperforms.
        _BASE_DATES = {date(2025, 9, 5), date(2025, 9, 8)}

        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start in _BASE_DATES:
                    ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                    return [_make_ohlcbar(ticker="AAPL", ts=ts_bar, open=100.0, close=100.0)]
                # Forward +1d from 2025-09-05 → start = 2025-09-06 (tick-1 fwd: +5 %)
                # Forward +1d from 2025-09-08 → start = 2025-09-09 (tick-2 fwd: flat)
                if start >= date(2025, 9, 9):
                    ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                    return [_make_ohlcbar(ticker="AAPL", ts=ts_bar, open=100.0, close=100.0)]
                ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                return [_make_ohlcbar(ticker="AAPL", ts=ts_bar, open=105.0, close=105.0)]
            if ticker == "MSFT":
                if start in _BASE_DATES:
                    ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                    return [_make_ohlcbar(ticker="MSFT", ts=ts_bar, open=100.0, close=100.0)]
                # MSFT tick-1 fwd = +1 %, tick-2 fwd = +3 %.
                if start >= date(2025, 9, 9):
                    ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                    return [_make_ohlcbar(ticker="MSFT", ts=ts_bar, open=103.0, close=103.0)]
                ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                return [_make_ohlcbar(ticker="MSFT", ts=ts_bar, open=101.0, close=101.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell_bullish = result.cell(analyst="technical", horizon=1, subset="bullish")
        # Bullish subset: AAPL in tick-1 (hit) and AAPL in tick-2 (miss).
        assert cell_bullish.n == 2, f"Expected n=2 for bullish subset; got {cell_bullish.n}"
        assert cell_bullish.hit_rate == pytest.approx(0.5, abs=1e-9), (
            f"Expected hit_rate=0.5 for bullish subset; got {cell_bullish.hit_rate}"
        )

        # 'all' subset: coverage n counts the two neutral MSFT verdicts too, but
        # the hit-rate denominator excludes them — so it equals the bullish
        # hit-rate (1 AAPL hit / 2 AAPL directional calls), NOT 1/4.
        cell_all = result.cell(analyst="technical", horizon=1, subset="all")
        assert cell_all.n == 4, f"Expected n=4 for 'all' subset; got {cell_all.n}"
        assert cell_all.hit_rate == pytest.approx(0.5, abs=1e-9), (
            "Hit-rate denominator must exclude neutral verdicts; "
            f"expected 0.5, got {cell_all.hit_rate}"
        )


# ── Tests: render_scoreboard_md ───────────────────────────────────────────────

class TestRenderScoreboardMd:
    """Unit tests for the ``render_scoreboard_md`` formatting function."""

    def _build_minimal_result(self, tmp_path, rows, horizons, mock_read):
        """Helper: build a scoreboard result from fixture rows and a read function."""
        from backtest.scoreboard import build_analyst_scoreboard

        db_path    = _build_fixture_db(tmp_path, rows)
        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = mock_read

        return build_analyst_scoreboard(db_path=db_path, cache=mock_cache, horizons=horizons)

    def test_rendered_md_contains_analyst_names(self, tmp_path) -> None:
        """The rendered markdown contains a row for each analyst that has verdicts."""
        from backtest.scoreboard import render_scoreboard_md

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
            _make_evidence_row(
                analyst="fundamental",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        def _read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=105.0, close=105.0)]

        result = self._build_minimal_result(tmp_path, rows, [1], _read)
        md = render_scoreboard_md(result)

        assert "technical"   in md, "Expected 'technical' in rendered markdown"
        assert "fundamental" in md, "Expected 'fundamental' in rendered markdown"

    def test_rendered_md_contains_n_per_cell(self, tmp_path) -> None:
        """The rendered markdown includes the coverage count (n) for each cell."""
        from backtest.scoreboard import render_scoreboard_md

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
        ]

        def _read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=105.0, close=105.0)]

        result = self._build_minimal_result(tmp_path, rows, [1], _read)
        md = render_scoreboard_md(result)

        # n=1 must appear somewhere in the rendered output.
        assert "1" in md, "Expected the coverage count n=1 in rendered markdown"

    def test_rendered_md_has_section_heading(self, tmp_path) -> None:
        """The rendered markdown starts with or contains a section heading."""
        from backtest.scoreboard import render_scoreboard_md

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
        ]

        def _read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=105.0, close=105.0)]

        result = self._build_minimal_result(tmp_path, rows, [1], _read)
        md = render_scoreboard_md(result)

        # The rendered section must begin with a Markdown heading.
        assert md.strip().startswith("#"), (
            "Rendered markdown must start with a '#' heading"
        )

    def test_rendered_md_contains_horizon_labels(self, tmp_path) -> None:
        """The rendered markdown contains labels for each horizon requested."""
        from backtest.scoreboard import render_scoreboard_md

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
        ]

        def _read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=105.0, close=105.0)]

        result = self._build_minimal_result(tmp_path, rows, [1, 5, 20], _read)
        md = render_scoreboard_md(result)

        # Each horizon label must appear somewhere in the section.
        for label in ("1d", "5d", "20d"):
            assert label in md, f"Expected horizon label '{label}' in rendered markdown"

    def test_rendered_md_contains_subset_labels(self, tmp_path) -> None:
        """The rendered markdown contains labels for all/bullish/bearish subsets."""
        from backtest.scoreboard import render_scoreboard_md

        rows = [
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
        ]

        def _read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=105.0, close=105.0)]

        result = self._build_minimal_result(tmp_path, rows, [1], _read)
        md = render_scoreboard_md(result)

        # All three subsets must be labelled.
        for subset_label in ("all", "bullish", "bearish"):
            assert subset_label in md.lower(), (
                f"Expected subset label '{subset_label}' in rendered markdown"
            )


# ── Tests: _forward_close helper is extracted and shared ─────────────────────

class TestForwardCloseHelper:
    """Verify that ``_forward_close`` is importable from ``backtest.reporting``
    and that it returns the correct first-available-bar close.

    This is the helper extracted from ``_backfill_forward_returns`` to share
    the forward-price lookup logic with the scoreboard.
    """

    def test_forward_close_returns_first_bar_close(self) -> None:
        """``_forward_close`` returns the close of the first available bar."""
        from backtest.reporting import _forward_close

        bar = _make_ohlcbar(
            ticker="AAPL",
            ts=datetime(2025, 9, 6, tzinfo=UTC),
            open=105.0,
            close=106.0,
        )

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = [bar]

        result = _forward_close(mock_cache, "AAPL", date(2025, 9, 5), h=1)
        assert result == pytest.approx(106.0), f"Expected 106.0; got {result}"

    def test_forward_close_returns_none_when_no_bar(self) -> None:
        """``_forward_close`` returns ``None`` when no bar is available."""
        from backtest.reporting import _forward_close

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = []

        result = _forward_close(mock_cache, "AAPL", date(2025, 9, 5), h=1)
        assert result is None, f"Expected None when no bar; got {result}"

    def test_forward_close_uses_first_bar_in_range(self) -> None:
        """``_forward_close`` uses the first bar in the forward window, not the last."""
        from backtest.reporting import _forward_close

        bar1 = _make_ohlcbar(ticker="AAPL", ts=datetime(2025, 9, 6, tzinfo=UTC), open=100.0, close=101.0)
        bar2 = _make_ohlcbar(ticker="AAPL", ts=datetime(2025, 9, 7, tzinfo=UTC), open=102.0, close=103.0)

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.return_value = [bar1, bar2]

        result = _forward_close(mock_cache, "AAPL", date(2025, 9, 5), h=1)
        # Should take bar1's close, not bar2's.
        assert result == pytest.approx(101.0), f"Expected 101.0 (first bar); got {result}"

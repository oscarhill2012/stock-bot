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

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.models import OHLCBar

# The models that must exist in the production code.
from orchestrator.persistence import AnalystEvidenceRow, TickerEvidenceRow, TickerStanceRow
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


def _make_ticker_evidence_row(
    *,
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    lean: str,
    magnitude: float = 0.5,
    confidence: float = 0.7,
) -> TickerEvidenceRow:
    """Build a ``TickerEvidenceRow`` (cross-analyst aggregate) without persisting it.

    Mirrors the columns the scoreboard's aggregate-injection path reads
    directly (lean/magnitude/confidence); the remaining columns are given
    plausible defaults since the scoreboard/agreement code does not read them.
    """
    return TickerEvidenceRow(
        tick_id=tick_id,
        recorded_at=recorded_at,
        ticker=ticker,
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        disagreement=0.0,
        summary="",
        weights_json="{}",
        analyst_count=3,
    )


def _make_stance_row(
    *,
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    lifecycle_action: str,
) -> TickerStanceRow:
    """Build a ``TickerStanceRow`` without persisting it.

    ``lifecycle_action`` is the three-verb canonical form (``"buy" | "sell" |
    "update"``) consumed by ``stance_vs_aggregate_agreement`` to derive a
    directional label.
    """
    return TickerStanceRow(
        tick_id=tick_id,
        recorded_at=recorded_at,
        ticker=ticker,
        rationale="",
        lifecycle_action=lifecycle_action,
        decision_tag="test",
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


def _build_fixture_db(tmp_path, rows: list[PersistenceBase]):
    """Persist ``rows`` to a fresh SQLite fixture and return the path.

    Creates all tables (from the ORM Base used by persistence.py), inserts
    the supplied rows, and returns the Path to the SQLite file.

    ``rows`` may be a MIXED list of any ORM row type sharing ``PersistenceBase``
    (e.g. ``AnalystEvidenceRow``, ``TickerEvidenceRow``, ``TickerStanceRow``) —
    each is simply ``session.add``-ed in turn.
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

        # Locate the technical analyst's "directional" row for horizon 1d.
        cell = result.cell(analyst="technical", horizon=1, subset="directional")
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

        cell = result.cell(analyst="technical", horizon=1, subset="directional")
        assert cell.n > 0
        # AAPL lagged peers — score must be NEGATIVE despite +1 % absolute return.
        assert cell.mean_excess_bps < 0, (
            f"Expected negative mean excess for lagging-but-positive return; "
            f"got {cell.mean_excess_bps:.4f} bps"
        )

    # ── Case 3: neutral lean → excluded entirely from 'directional' ───────────

    def test_neutral_lean_excluded_entirely_from_directional_subset(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A neutral lean is an ABSTENTION, not a bet that the stock is flat.

        Phase 14 (iter-11) change: neutral verdicts no longer contribute a
        score of exactly 0 to the 'directional' subset — they are EXCLUDED
        from it entirely (no contribution to mean, n, or t-stat).  A lone
        neutral verdict with no directional counterpart must leave the
        'directional' cell with n=0 and a NaN mean (no data to score), while
        still being visible in the analyst's coverage/abstention diagnostic
        (n_neutral=1).
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

        cell = result.cell(analyst="fundamental", horizon=1, subset="directional")
        # No directional (bullish/bearish) verdicts exist at all → n=0, NaN mean.
        assert cell.n == 0, f"Expected n=0 (neutral excluded entirely); got {cell.n}"
        assert math.isnan(cell.mean_excess_bps), (
            f"Expected NaN mean excess with zero directional observations; "
            f"got {cell.mean_excess_bps}"
        )

        # The neutral verdict must still show up in the coverage diagnostic.
        coverage = result.coverage(analyst="fundamental", horizon=1)
        assert coverage.n_neutral == 1, (
            f"Expected n_neutral=1 for the lone neutral verdict; got {coverage.n_neutral}"
        )
        assert coverage.neutral_rate == pytest.approx(1.0, abs=1e-9), (
            f"Expected neutral_rate=1.0 (1/1 scoreable verdicts neutral); "
            f"got {coverage.neutral_rate}"
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

        cell_1d  = result.cell(analyst="technical", horizon=1,  subset="directional")
        cell_5d  = result.cell(analyst="technical", horizon=5,  subset="directional")
        cell_20d = result.cell(analyst="technical", horizon=20, subset="directional")

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
          GOOG excess = (−0.02) − 0.006667 = −0.026667 — neutral, EXCLUDED from
            the 'directional' subset entirely (not scored as 0).

        'directional' subset = bullish ∪ bearish only (GOOG's neutral excluded):
        Mean score = mean(+0.023333, −0.003333) = +0.02 / 2 = +0.01
        In bps: 0.01 × 10_000 = 100.0 bps.
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

        cell_all = result.cell(analyst="technical", horizon=1, subset="directional")
        assert cell_all.n == 2, (
            f"Expected 2 directional verdicts scored (GOOG neutral excluded); "
            f"got {cell_all.n}"
        )

        # Hand-computed expected mean excess (directional only):
        # scores = [+0.023333, −0.003333]
        # mean   = +0.02 / 2 = +0.01
        # bps    = 0.01 × 10_000 = +100.0
        expected_bps = (0.02 / 2) * 10_000
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

        cell_all      = result.cell(analyst="technical", horizon=1, subset="directional")
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

        cell = result.cell(analyst="technical", horizon=1, subset="directional")
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

        cell = result.cell(analyst="technical", horizon=1, subset="directional")
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

        MSFT is given a BEARISH lean (not neutral): MSFT lags AAPL every
        tick, so a bearish MSFT call also yields a positive score, and —
        crucially — MSFT's directional cluster keeps the cluster-robust
        estimator from degenerating to a single ticker cluster (which would
        be the case if MSFT's calls were excluded as neutral, since the
        'directional' subset would then contain ONLY AAPL).

        Phase 14 note: each (analyst, ticker) pair has a DIFFERENT confidence
        value per tick (0.7, 0.8, 0.9) so the dedup proxy treats each tick as
        a distinct fresh call (not a cache replay).  This gives n=6 as intended.
        If confidence were identical across ticks, dedup would collapse to n=2
        (one observation per ticker) — which is the correct behaviour for
        replayed verdicts (see TestCacheReplayDedup).
        """
        build = self._import()

        # Three ticks — DELIBERATELY vary confidence so dedup does NOT collapse them
        # (each tick represents a genuinely fresh verdict, not a cache replay).
        tick_data = [
            ("tick-1", datetime(2025, 9, 5,  13, 30, tzinfo=UTC), 0.70),
            ("tick-2", datetime(2025, 9, 8,  13, 30, tzinfo=UTC), 0.80),
            ("tick-3", datetime(2025, 9, 9,  13, 30, tzinfo=UTC), 0.90),
        ]

        rows = []
        for tid, ts, conf in tick_data:
            rows.append(_make_evidence_row(
                analyst="technical", ticker="AAPL",
                tick_id=tid, recorded_at=ts, lean="bullish",
                confidence=conf,  # varies → dedup treats each as a fresh call
            ))
            rows.append(_make_evidence_row(
                analyst="technical", ticker="MSFT",
                tick_id=tid, recorded_at=ts, lean="bearish",
                confidence=conf,
            ))

        db_path = _build_fixture_db(tmp_path, rows)

        # AAPL: base=100, fwd=105 (+5 %).  MSFT: base=100, fwd=101 (+1 %).
        # cs_mean = (0.05 + 0.01) / 2 = 0.03.  AAPL excess = +0.02 every tick.
        # MSFT excess = 0.01 − 0.03 = −0.02; bearish MSFT → score = +0.02.
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

        cell = result.cell(analyst="technical", horizon=1, subset="directional")
        # Each tick has a distinct confidence → dedup keeps all 3 ticks per ticker.
        # AAPL (bullish, 3 ticks) + MSFT (bearish, 3 ticks) = 6 directional verdicts.
        assert cell.n == 6, f"Expected n=6 (3 AAPL + 3 MSFT, both directional); got {cell.n}"

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
            cell = result.cell(analyst="technical", horizon=h, subset="directional")
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

        tech_cell = result.cell(analyst="technical",   horizon=1, subset="directional")
        fund_cell = result.cell(analyst="fundamental", horizon=1, subset="directional")

        # Each analyst has 1 directional verdict (AAPL); MSFT's neutral is
        # excluded entirely from the 'directional' subset.
        assert tech_cell.n == 1, f"Expected n=1 for technical; got {tech_cell.n}"
        assert fund_cell.n == 1, f"Expected n=1 for fundamental; got {fund_cell.n}"

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

        Hit rate for analyst 'technical', subset 'directional':
          Scored verdicts: [AAPL tick-1 (hit), AAPL tick-2 (miss)] — the two
          neutral MSFT verdicts are excluded ENTIRELY from 'directional' (not
          just from the hit-rate denominator), so n = 2, not 4.
          hit_rate = 1/2 = 0.5 (one AAPL hit out of two AAPL directional calls).

        Subset 'bullish' only (AAPL verdicts):
          n = 2.  Positive = 1.  hit_rate = 0.5.

        Phase 14 note: AAPL confidence differs per tick (0.7 vs 0.6) so the
        dedup proxy treats each tick as a distinct fresh call, preserving n=2
        for the bullish AAPL verdicts.  If they were identical, dedup would
        collapse to n=1 (correct cache-replay handling — see TestCacheReplayDedup).
        """
        build = self._import()

        rows = [
            # Tick 1: AAPL outperforms — bullish call is a hit.
            # confidence=0.7 here and 0.6 in tick-2 → different → fresh calls.
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id="tick-1",
                recorded_at=datetime(2025, 9, 5, 13, 30, tzinfo=UTC),
                lean="bullish",
                confidence=0.7,   # tick-1 confidence
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id="tick-1",
                recorded_at=datetime(2025, 9, 5, 13, 30, tzinfo=UTC),
                lean="neutral",
                confidence=0.7,
            ),
            # Tick 2: AAPL underperforms — bullish call is a miss.
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id="tick-2",
                recorded_at=datetime(2025, 9, 8, 13, 30, tzinfo=UTC),
                lean="bullish",
                confidence=0.6,   # different confidence → dedup treats as new fresh call
            ),
            _make_evidence_row(
                analyst="technical",
                ticker="MSFT",
                tick_id="tick-2",
                recorded_at=datetime(2025, 9, 8, 13, 30, tzinfo=UTC),
                lean="neutral",
                confidence=0.6,
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
        # Both are kept by dedup (different confidence values → distinct fresh calls).
        assert cell_bullish.n == 2, f"Expected n=2 for bullish subset; got {cell_bullish.n}"
        assert cell_bullish.hit_rate == pytest.approx(0.5, abs=1e-9), (
            f"Expected hit_rate=0.5 for bullish subset; got {cell_bullish.hit_rate}"
        )

        # 'directional' subset: neutral MSFT verdicts are excluded entirely
        # (not merely from the hit-rate denominator), so n equals the bullish
        # subset's n exactly (here there are no bearish verdicts at all).
        cell_all = result.cell(analyst="technical", horizon=1, subset="directional")
        assert cell_all.n == 2, f"Expected n=2 for 'directional' subset; got {cell_all.n}"
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
        for subset_label in ("directional", "bullish", "bearish"):
            assert subset_label in md.lower(), (
                f"Expected subset label '{subset_label}' in rendered markdown"
            )


# ── Tests: _forward_close helper is extracted and shared ─────────────────────

# ── Tests: defect fixes (TDD — written BEFORE implementation) ─────────────────


class TestCacheReplayDedup:
    """Defect 1: cache-replay amplification.

    A single fresh verdict replayed across consecutive ticks (same analyst,
    ticker, lean, magnitude, confidence) must count as EXACTLY ONE observation
    in the scoreboard, anchored at the first tick.  The six replayed copies
    must NOT each contribute an independent score.

    Design note: the ``analyst_evidence`` table carries NO hash or identity
    column that directly flags a cache replay.  We use a deterministic proxy:
    a run of identical consecutive (lean, magnitude, confidence) values for the
    same (analyst, ticker) sequence collapses to the first occurrence.  This is
    documented in the scoreboard source and acknowledged here.
    """

    def _import(self):
        from backtest.scoreboard import build_analyst_scoreboard
        return build_analyst_scoreboard

    def test_replayed_verdict_counts_once_not_six(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """One bullish verdict replayed across 6 consecutive ticks for the same
        (analyst, ticker) must produce exactly ONE scored observation, not six.

        The proxy: collapse consecutive identical (lean, magnitude, confidence)
        tuples for the same (analyst, ticker) pair into a single observation
        anchored at the first tick.  The anchor tick's forward return is used.

        Setup:
          - Analyst "fundamental" on AAPL, 6 ticks (2025-09-01 through 2025-09-08
            on market days), all with lean="bullish", magnitude=0.5, confidence=0.7.
          - One separate ticker MSFT also has 6 verdicts (lean="neutral"), so the
            cross-sectional mean can be computed.
          - AAPL rises 5 % from base on tick-1; MSFT flat.

        Expected: n=1 in the "fundamental" analyst cell at any horizon
        (the first occurrence only, not 6).
        """
        build = self._import()

        # Six consecutive ticks on market days for fundamental analyst on AAPL.
        # All are identical in lean/magnitude/confidence — simulating a cache replay.
        tick_dates = [
            ("tick-2025-09-01", datetime(2025, 9, 1, 13, 30, tzinfo=UTC)),
            ("tick-2025-09-02", datetime(2025, 9, 2, 13, 30, tzinfo=UTC)),
            ("tick-2025-09-03", datetime(2025, 9, 3, 13, 30, tzinfo=UTC)),
            ("tick-2025-09-04", datetime(2025, 9, 4, 13, 30, tzinfo=UTC)),
            ("tick-2025-09-05", datetime(2025, 9, 5, 13, 30, tzinfo=UTC)),
            ("tick-2025-09-08", datetime(2025, 9, 8, 13, 30, tzinfo=UTC)),
        ]

        rows = []
        for tid, ts in tick_dates:
            # Identical verdict across all 6 ticks — this is the cache-replay pattern.
            rows.append(_make_evidence_row(
                analyst="fundamental",
                ticker="AAPL",
                tick_id=tid,
                recorded_at=ts,
                lean="bullish",
                magnitude=0.5,
                confidence=0.7,
            ))
            # MSFT is neutral on each tick — exists to make cs_mean non-trivial
            # and to give the cross-sectional demean a second ticker.
            rows.append(_make_evidence_row(
                analyst="fundamental",
                ticker="MSFT",
                tick_id=tid,
                recorded_at=ts,
                lean="neutral",
                magnitude=0.0,
                confidence=0.5,
            ))

        db_path = _build_fixture_db(tmp_path, rows)

        # Prices: each date's base bar returns AAPL=100, MSFT=100.
        # Forward bars return AAPL=105, MSFT=100 (AAPL outperforms by 5 %).
        _ANCHOR_DATE = date(2025, 9, 1)  # first tick date
        _BASE_DATES = {
            date(2025, 9, 1), date(2025, 9, 2), date(2025, 9, 3),
            date(2025, 9, 4), date(2025, 9, 5), date(2025, 9, 8),
        }

        def _mock_read(ticker, start, end):
            if start in _BASE_DATES:
                # Base-price query — return base bar for either ticker.
                ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]
            # Forward-price query — AAPL outperforms, MSFT flat.
            ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
            if ticker == "AAPL":
                return [_make_ohlcbar(ticker="AAPL", ts=ts_bar, open=105.0, close=105.0)]
            return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell = result.cell(analyst="fundamental", horizon=1, subset="directional")

        # KEY ASSERTION: without dedup the old code gives n=12 (6 ticks × 2 tickers).
        # With dedup, the replayed AAPL verdict collapses to 1 observation.
        # MSFT is neutral throughout, so it is excluded entirely from the
        # 'directional' subset (dedup also collapses its 6 replays to 1, but
        # that 1 observation never enters 'directional').  n must be 1, not 12.
        assert cell.n == 1, (
            f"Cache-replay dedup failed: expected n=1 (1 AAPL after dedup; "
            f"MSFT neutral excluded from 'directional'); got n={cell.n}.  "
            f"Without dedup this would be 6 (6 ticks of AAPL)."
        )

    def test_changed_verdict_breaks_dedup_run(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A changed verdict (different lean) is treated as a NEW fresh call and
        starts a new dedup run.  The transition verdict must also count.

        Setup:
          - Ticks 1–3: fundamental AAPL bullish (magnitude=0.5, confidence=0.7) — 1 obs.
          - Tick 4:    fundamental AAPL bearish (magnitude=0.4, confidence=0.6) — new fresh call.
          - Ticks 5–6: fundamental AAPL bearish (same as tick 4) — replays, collapse into tick 4.

        Expected: n=2 (the first bullish observation + the first bearish observation).
        We also need a second ticker to make the cross-sectional mean non-trivial.
        """
        build = self._import()

        tick_dates = [
            ("tick-1", datetime(2025, 9, 1, 13, 30, tzinfo=UTC)),
            ("tick-2", datetime(2025, 9, 2, 13, 30, tzinfo=UTC)),
            ("tick-3", datetime(2025, 9, 3, 13, 30, tzinfo=UTC)),
            ("tick-4", datetime(2025, 9, 4, 13, 30, tzinfo=UTC)),
            ("tick-5", datetime(2025, 9, 5, 13, 30, tzinfo=UTC)),
            ("tick-6", datetime(2025, 9, 8, 13, 30, tzinfo=UTC)),
        ]

        rows = []
        for i, (tid, ts) in enumerate(tick_dates):
            lean       = "bullish" if i < 3 else "bearish"
            magnitude  = 0.5       if i < 3 else 0.4
            confidence = 0.7       if i < 3 else 0.6
            rows.append(_make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=tid, recorded_at=ts,
                lean=lean, magnitude=magnitude, confidence=confidence,
            ))
            # MSFT anchor — neutral throughout.
            rows.append(_make_evidence_row(
                analyst="fundamental", ticker="MSFT",
                tick_id=tid, recorded_at=ts,
                lean="neutral", magnitude=0.0, confidence=0.5,
            ))

        db_path = _build_fixture_db(tmp_path, rows)

        _BASE_DATES = {
            date(2025, 9, 1), date(2025, 9, 2), date(2025, 9, 3),
            date(2025, 9, 4), date(2025, 9, 5), date(2025, 9, 8),
        }

        def _mock_read(ticker, start, end):
            if start in _BASE_DATES:
                ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
                return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]
            ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
            if ticker == "AAPL":
                return [_make_ohlcbar(ticker="AAPL", ts=ts_bar, open=105.0, close=105.0)]
            return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        cell = result.cell(analyst="fundamental", horizon=1, subset="directional")
        # 1 bullish AAPL observation + 1 bearish AAPL observation = 2 directional
        # observations.  MSFT is neutral throughout, so its (deduplicated) single
        # observation is excluded entirely from 'directional'.
        assert cell.n == 2, (
            f"Expected n=2 after verdict-change dedup; got n={cell.n}.  "
            f"Should count: tick-1 bullish AAPL (anchor), tick-4 bearish AAPL "
            f"(new fresh).  MSFT's neutral anchor is excluded from 'directional'."
        )


class TestPerAnalystHorizon:
    """Defect 2: per-analyst primary scoring horizon.

    The scoreboard must use a per-analyst primary horizon for its headline metric
    and ranking, driven by config.  News signals decay in ~1 day; fundamental
    signals last ~20 days.  Both should still REPORT all horizons; the config
    controls which horizon drives the 'primary' rank.

    We test that:
      - ``build_analyst_scoreboard`` accepts and uses a
        ``primary_horizon_by_analyst`` mapping.
      - The returned ``ScoreboardResult`` exposes ``primary_horizon(analyst)``
        that returns the configured value per analyst.
      - Cells exist at ALL horizons for EVERY analyst (reporting unchanged).
      - The default fallback (analyst not in the map) uses the largest horizon.
    """

    def _import(self):
        from backtest.scoreboard import build_analyst_scoreboard
        return build_analyst_scoreboard

    def _build_two_analyst_fixture(self, tmp_path):
        """Return (db_path, mock_cache) for a two-analyst fixture.

        Analysts: "news" (AAPL bullish) and "fundamental" (AAPL bullish).
        Single tick, two tickers (AAPL + MSFT) so cross-sectional mean is defined.
        AAPL: base=100, fwd_1d=103, fwd_20d=110.
        MSFT: base=100, fwd_1d=100, fwd_20d=100.
        """
        rows = [
            _make_evidence_row(
                analyst="news", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
            _make_evidence_row(
                analyst="news", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
            ),
            _make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
            _make_evidence_row(
                analyst="fundamental", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        _BASE_DATE = date(2025, 9, 5)

        def _mock_read(ticker, start, end):
            ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
            if start == _BASE_DATE:
                return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]
            # Forward: AAPL outperforms at +1d and +20d; MSFT flat.
            if ticker == "AAPL":
                # +1d → +3 %; +20d → +10 %
                close = 103.0 if (start - _BASE_DATE).days <= 5 else 110.0
                return [_make_ohlcbar(ticker="AAPL", ts=ts_bar, open=close, close=close)]
            return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read
        return db_path, mock_cache

    def test_scoreboard_result_exposes_primary_horizon_per_analyst(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """``ScoreboardResult.primary_horizon(analyst)`` returns the configured
        horizon for that analyst, or the default for unconfigured analysts.
        """
        build = self._import()
        db_path, mock_cache = self._build_two_analyst_fixture(tmp_path)

        result = build(
            db_path=db_path,
            cache=mock_cache,
            horizons=[1, 5, 20],
            primary_horizon_by_analyst={"news": 1, "fundamental": 20},
        )

        assert result.primary_horizon("news")        == 1,  \
            "news analyst primary horizon should be 1d"
        assert result.primary_horizon("fundamental") == 20, \
            "fundamental analyst primary horizon should be 20d"

    def test_news_analyst_primary_horizon_is_1d(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """The primary_horizon for the 'news' analyst must be 1 when configured.

        Cells at ALL horizons must still exist (reporting is unaffected).
        """
        build = self._import()
        db_path, mock_cache = self._build_two_analyst_fixture(tmp_path)

        result = build(
            db_path=db_path,
            cache=mock_cache,
            horizons=[1, 5, 20],
            primary_horizon_by_analyst={"news": 1, "fundamental": 20},
        )

        # Primary horizon for news = 1.
        assert result.primary_horizon("news") == 1

        # All horizons must still have cells (reporting unchanged).
        for h in [1, 5, 20]:
            cell = result.cell(analyst="news", horizon=h, subset="directional")
            assert cell.n >= 0, f"Cell for news at {h}d must exist"

    def test_fundamental_analyst_primary_horizon_is_20d(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """The primary_horizon for the 'fundamental' analyst must be 20 when configured."""
        build = self._import()
        db_path, mock_cache = self._build_two_analyst_fixture(tmp_path)

        result = build(
            db_path=db_path,
            cache=mock_cache,
            horizons=[1, 5, 20],
            primary_horizon_by_analyst={"news": 1, "fundamental": 20},
        )

        assert result.primary_horizon("fundamental") == 20

        # All horizons still have cells.
        for h in [1, 5, 20]:
            cell = result.cell(analyst="fundamental", horizon=h, subset="directional")
            assert cell.n >= 0

    def test_primary_horizon_defaults_for_unconfigured_analyst(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """An analyst not in ``primary_horizon_by_analyst`` falls back to the largest
        horizon in the ``horizons`` list (the most stable / long-term measure).
        """
        build = self._import()
        db_path, mock_cache = self._build_two_analyst_fixture(tmp_path)

        # "news" is in the map; "fundamental" is NOT — should default to 20.
        result = build(
            db_path=db_path,
            cache=mock_cache,
            horizons=[1, 5, 20],
            primary_horizon_by_analyst={"news": 1},
        )

        # "fundamental" not in map → default = max(horizons) = 20.
        assert result.primary_horizon("fundamental") == 20, (
            "Unconfigured analyst should default to max(horizons)=20; "
            f"got {result.primary_horizon('fundamental')}"
        )

    def test_config_driven_primary_horizons_loaded_from_settings(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """``primary_horizon_by_analyst`` must be loadable from backtest settings
        so callers read it from config rather than hardcoding values.

        We write a temporary settings JSON with the new key, load it, and assert
        the mapping is present and correct.
        """
        import json
        from backtest.settings import load_backtest_settings_from

        # Write a minimal settings file that includes the new config key.
        settings_path = tmp_path / "backtest_settings.json"
        settings_data = {
            "backtests_root": "backtests",
            "ticks_per_day": ["open", "close"],
            "failed_tick_abort_ratio": 0.10,
            "fake_broker_starting_cash": 100000.0,
            "forward_return_horizons_days": [1, 5, 20],
            "ohlcv_warmup_days": 90,
            "primary_horizon_by_analyst": {"news": 1, "fundamental": 20},
        }
        settings_path.write_text(json.dumps(settings_data), encoding="utf-8")

        settings = load_backtest_settings_from(settings_path)

        assert hasattr(settings, "primary_horizon_by_analyst"), \
            "BacktestSettings must have 'primary_horizon_by_analyst' field"
        assert settings.primary_horizon_by_analyst["news"] == 1
        assert settings.primary_horizon_by_analyst["fundamental"] == 20


class TestSectorNeutralisation:
    """Defect 3: sector/beta neutralisation.

    Excess must be computed against the ticker's SECTOR PEER GROUP when sector
    data is available in the cache, rather than the whole-universe cross-sectional
    mean.

    Implementation: ``build_analyst_scoreboard`` accepts a ``neutralise_by``
    parameter (``"sector"`` | ``"universe"``).  When ``"sector"``, it reads
    ``cache.read_company_ratios(ticker, as_of)`` for each ticker to find its
    sector, then computes the cross-sectional mean over that sector's tickers
    in that tick.

    Sector data comes from ``CachedDataStore.read_company_ratios``, which
    returns a ``CompanyRatios`` model whose ``.sector`` field holds the GICS
    sector string (e.g. ``"Technology"``, ``"Energy"``).

    If sector data is unavailable for a ticker, it falls back to the whole
    universe for that ticker's calculation and logs a WARNING.
    """

    def _import(self):
        from backtest.scoreboard import build_analyst_scoreboard
        return build_analyst_scoreboard

    def test_sector_neutral_uses_sector_peers_not_whole_universe(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Excess is computed against the sector peer mean, not the whole universe.

        Synthetic two-sector setup where sector mean ≠ universe mean:

          Tick T, four tickers:
            Tech:    AAPL (+10 %), MSFT (+2 %)    → tech mean = +6 %
            Energy:  XOM  (+0 %),  CVX  (−2 %)   → energy mean = −1 %

          Analyst covers AAPL (bullish) and XOM (bullish) and neutral on MSFT and CVX.

          Universe mean = (0.10 + 0.02 + 0.00 + (−0.02)) / 4 = 0.025 (2.5 %)

          With sector-neutral excess:
            AAPL excess = 0.10 − 0.06 = +0.04   (vs tech mean)
            MSFT excess = 0.02 − 0.06 = −0.04   (vs tech mean, neutral → score = 0)
            XOM  excess = 0.00 − (−0.01) = +0.01 (vs energy mean)
            CVX  excess = (−0.02) − (−0.01) = −0.01 (vs energy mean, neutral → score = 0)

            Bullish scores: AAPL = +1 × 0.04 = +0.04;  XOM = +1 × 0.01 = +0.01
            Mean score (all, incl neutrals) = (+0.04 + 0 + 0.01 + 0) / 4 = 0.0125
            In bps: +125

          With universe-neutral excess:
            AAPL excess = 0.10 − 0.025 = +0.075
            XOM  excess = 0.00 − 0.025 = −0.025   ← negative! universe mean swamps it
            Bullish scores: AAPL = +0.075, XOM = −0.025
            Mean (all) = (0.075 + 0 − 0.025 + 0) / 4 = 0.0125 bps … wait

        Revised: easier contrast — make the two sectors DIVERGE sharply so that
        sector-neutral and universe-neutral give different signs for XOM:

          Tick T:
            Tech:    AAPL (+20 %), MSFT (+18 %)  → tech mean = +19 %
            Energy:  XOM  (+1 %),  CVX  (−1 %)  → energy mean = 0 %

          Universe mean = (0.20 + 0.18 + 0.01 + (−0.01)) / 4 = 0.095 (9.5 %)

          Analyst: bullish AAPL, bullish XOM, neutral MSFT, neutral CVX.

          Sector-neutral excess:
            AAPL  = 0.20 − 0.19 = +0.01  (bullish → +0.01)
            XOM   = 0.01 − 0.00 = +0.01  (bullish → +0.01)
            Mean of all scores = (+0.01 + 0 + 0.01 + 0) / 4 = +0.005 → +50 bps

          Universe-neutral excess:
            AAPL  = 0.20 − 0.095 = +0.105 (bullish → +0.105)
            XOM   = 0.01 − 0.095 = −0.085 (bullish → −0.085) ← NEGATIVE
            Mean of all scores = (+0.105 + 0 − 0.085 + 0) / 4 = +0.005

        Hmm, same mean in this case.  Need different number of tickers per sector
        or skewed absolute values for the means to differ meaningfully.

        FINAL SETUP — unequal sector sizes:
          Tech (3 tickers): AAPL +15 %, MSFT +13 %, GOOG +14 %   → tech mean = +14 %
          Energy (1 ticker): XOM −2 %                              → energy mean = −2 %

          Universe mean = (0.15 + 0.13 + 0.14 + (−0.02)) / 4 = 0.40 / 4 = 0.10

          Analyst: bullish AAPL, bullish XOM, neutral MSFT, neutral GOOG.

          Sector-neutral:
            AAPL excess = 0.15 − 0.14 = +0.01  (bullish → +0.01)
            MSFT excess = 0.13 − 0.14 = −0.01  (neutral → 0)
            GOOG excess = 0.14 − 0.14 =  0.00  (neutral → 0)
            XOM  excess = −0.02 − (−0.02) = 0.00 (bullish → 0)
            Mean all = (+0.01 + 0 + 0 + 0) / 4 = +0.0025 → +25 bps

          Universe-neutral:
            AAPL excess = 0.15 − 0.10 = +0.05  (bullish → +0.05)
            MSFT excess = 0.13 − 0.10 = +0.03  (neutral → 0)
            GOOG excess = 0.14 − 0.10 = +0.04  (neutral → 0)
            XOM  excess = −0.02 − 0.10 = −0.12 (bullish → −0.12)
            Mean all = (+0.05 + 0 + 0 − 0.12) / 4 = −0.07 / 4 = −0.0175 → −175 bps

        Sector-neutral mean ≈ +25 bps (positive).
        Universe-neutral mean ≈ −175 bps (negative).
        The two neutralisation modes produce OPPOSITE SIGNS — ideal discriminator.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
            ),
            _make_evidence_row(
                analyst="technical", ticker="GOOG",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
            ),
            _make_evidence_row(
                analyst="technical", ticker="XOM",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        _BASE_DATE = date(2025, 9, 5)
        _FWD_RETURNS = {
            "AAPL": 1.15, "MSFT": 1.13, "GOOG": 1.14, "XOM": 0.98,
        }

        def _mock_read_ohlcv(ticker, start, end):
            ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
            if start == _BASE_DATE:
                return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]
            # Forward bar: multiply base 100 by the return multiplier.
            fwd_close = _FWD_RETURNS.get(ticker, 1.0) * 100.0
            return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=fwd_close, close=fwd_close)]

        # Mock company ratios: AAPL/MSFT/GOOG → Technology, XOM → Energy.
        def _make_mock_ratios(sector: str):
            """Return a minimal mock CompanyRatios-like object with .sector set."""
            mock_r = MagicMock()
            mock_r.sector = sector
            return mock_r

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read_ohlcv
        mock_cache.read_company_ratios.side_effect = lambda ticker, as_of: {
            "AAPL": _make_mock_ratios("Technology"),
            "MSFT": _make_mock_ratios("Technology"),
            "GOOG": _make_mock_ratios("Technology"),
            "XOM":  _make_mock_ratios("Energy"),
        }.get(ticker)

        # Sector-neutral mode: analyst's mean excess must be POSITIVE (≈ +25 bps).
        result_sector = build(
            db_path=db_path,
            cache=mock_cache,
            horizons=[1],
            neutralise_by="sector",
        )
        cell_sector = result_sector.cell(analyst="technical", horizon=1, subset="directional")
        assert cell_sector.mean_excess_bps > 0, (
            f"Sector-neutral excess should be positive (≈ +25 bps); "
            f"got {cell_sector.mean_excess_bps:.2f} bps"
        )

        # Universe-neutral mode (the baseline fallback): mean excess must be NEGATIVE (≈ −175 bps).
        result_universe = build(
            db_path=db_path,
            cache=mock_cache,
            horizons=[1],
            neutralise_by="universe",
        )
        cell_universe = result_universe.cell(analyst="technical", horizon=1, subset="directional")
        assert cell_universe.mean_excess_bps < 0, (
            f"Universe-neutral excess should be negative (≈ −175 bps); "
            f"got {cell_universe.mean_excess_bps:.2f} bps"
        )

    def test_sector_fallback_to_universe_when_ratios_unavailable(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """When sector data is unavailable for a ticker, the scoreboard falls
        back to universe-mean neutralisation for that ticker and logs a WARNING.

        This prevents a silent null result when the cache has no company_ratios
        for a ticker — the scoreboard should remain functional, just less precise.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read_ohlcv(ticker, start, end):
            ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]
            close = 105.0 if ticker == "AAPL" else 100.0
            return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=close, close=close)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read_ohlcv
        # Sector data unavailable for both tickers.
        mock_cache.read_company_ratios.return_value = None

        # Should not raise; should fall back gracefully to universe-mean.
        result = build(
            db_path=db_path,
            cache=mock_cache,
            horizons=[1],
            neutralise_by="sector",
        )

        cell = result.cell(analyst="technical", horizon=1, subset="directional")
        # AAPL outperforms MSFT; bullish call is correct; positive excess.
        assert cell.n > 0, "Should have scored at least one verdict"
        assert cell.mean_excess_bps > 0, (
            f"Fallback (universe) should still yield positive excess; "
            f"got {cell.mean_excess_bps:.2f} bps"
        )

    def test_neutralise_by_config_key_loadable_from_settings(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """``neutralise_by`` must be loadable from ``config/backtest_settings.json``
        via ``BacktestSettings``.
        """
        import json
        from backtest.settings import load_backtest_settings_from

        settings_path = tmp_path / "backtest_settings.json"
        settings_data = {
            "backtests_root": "backtests",
            "ticks_per_day": ["open", "close"],
            "failed_tick_abort_ratio": 0.10,
            "fake_broker_starting_cash": 100000.0,
            "forward_return_horizons_days": [1, 5, 20],
            "ohlcv_warmup_days": 90,
            "primary_horizon_by_analyst": {"news": 1, "fundamental": 20},
            "scoreboard_neutralise_by": "sector",
        }
        settings_path.write_text(json.dumps(settings_data), encoding="utf-8")

        settings = load_backtest_settings_from(settings_path)

        assert hasattr(settings, "scoreboard_neutralise_by"), \
            "BacktestSettings must have 'scoreboard_neutralise_by' field"
        assert settings.scoreboard_neutralise_by == "sector"


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


# ── Tests: autocorrelation-robust inference (TDD — written BEFORE implementation) ─

class TestClusterRobustInference:
    """Measurement-layer fix: the scoreboard's ``t-stat`` / ``p-value`` must not
    treat autocorrelated observations as independent.

    Background
    ----------
    For a given ticker the verdict persists across ticks and the +5d / +20d
    forward-return windows heavily overlap, so the scored observations are
    strongly serially autocorrelated within a ticker.  ``ttest_1samp`` assumes
    independence and therefore UNDERSTATES the standard error — it rejects a
    true null far too often.

    The fix is a cluster-robust ("sandwich") standard error clustered by ticker
    with degrees of freedom = (#clusters − 1).  These tests demonstrate, by
    Monte-Carlo simulation under a TRUE null (population mean = 0), that:

      (a) the naive ``ttest_1samp`` over-rejects (empirical false-positive rate
          far above the nominal 5 %), and
      (b) the cluster-robust estimator restores roughly nominal coverage; and
      (c) on genuinely i.i.d. data (every cluster a singleton) the cluster-robust
          estimator AGREES with the naive one (no spurious change).
    """

    def _import(self):
        """Lazy import of the cluster-robust inference helper under test."""
        from backtest.scoreboard import _cluster_robust_ttest
        return _cluster_robust_ttest

    @staticmethod
    def _make_autocorrelated_panel(rng, *, n_tickers, n_per_ticker):
        """Generate a true-null panel with strong within-ticker autocorrelation.

        Each ticker contributes a block of ``n_per_ticker`` observations sharing
        a single random per-ticker level (mean 0) plus small idiosyncratic noise.
        The shared level makes observations within a ticker highly correlated
        while the population mean remains exactly 0 — the textbook setup that
        breaks the independence assumption of ``ttest_1samp``.

        Parameters
        ----------
        rng:
            A ``numpy.random.Generator`` for reproducibility.
        n_tickers:
            Number of distinct clusters (tickers).
        n_per_ticker:
            Observations per ticker (the overlapping/replayed window).

        Returns
        -------
        tuple[list[float], list[str]]
            ``(scores, cluster_labels)`` aligned element-for-element.
        """
        scores: list[float]   = []
        labels: list[str]     = []

        for t in range(n_tickers):
            # Per-ticker shared level — this is the autocorrelation source.
            level = rng.normal(0.0, 1.0)
            for _ in range(n_per_ticker):
                scores.append(level + rng.normal(0.0, 0.1))
                labels.append(f"T{t}")

        return scores, labels

    def test_naive_over_rejects_but_cluster_robust_restores_coverage(self) -> None:
        """Headline demonstration: under a true null with autocorrelated panels,
        the naive t-test's false-positive rate is badly inflated, and the
        cluster-robust t-test restores roughly nominal (~5 %) coverage.

        We run many independent trials.  In each trial the data has population
        mean 0, so a correctly-sized 5 %-level test should reject ~5 % of the
        time.  We assert the naive test rejects far more often (broken) and the
        cluster-robust test rejects roughly the nominal rate (fixed).
        """
        import scipy.stats

        cluster_robust = self._import()

        rng       = np.random.default_rng(20260623)
        n_trials  = 600
        alpha     = 0.05

        naive_rejections   = 0
        cluster_rejections = 0

        for _ in range(n_trials):
            scores, labels = self._make_autocorrelated_panel(
                rng, n_tickers=12, n_per_ticker=10,
            )
            arr = np.array(scores, dtype=float)

            # Naive one-sample t-test (the broken estimator).
            naive = scipy.stats.ttest_1samp(arr, 0.0)
            if naive.pvalue < alpha:
                naive_rejections += 1

            # Cluster-robust t-test (the fix).
            _t, p, _df = cluster_robust(scores, labels)
            if p < alpha:
                cluster_rejections += 1

        naive_rate   = naive_rejections   / n_trials
        cluster_rate = cluster_rejections / n_trials

        # (a) The naive test must be badly over-sized — empirically it rejects a
        #     true null far more than the nominal 5 %.
        assert naive_rate > 0.25, (
            f"Expected the naive t-test to over-reject the true null "
            f"(false-positive rate well above 0.05); got {naive_rate:.3f}."
        )

        # (b) The cluster-robust test must restore roughly nominal coverage.
        assert cluster_rate < 0.12, (
            f"Cluster-robust test should restore ~nominal 5 % coverage; "
            f"got false-positive rate {cluster_rate:.3f}."
        )

        # (c) And it must be a strict, large improvement over naive.
        assert cluster_rate < naive_rate / 2, (
            f"Cluster-robust ({cluster_rate:.3f}) should be far below "
            f"naive ({naive_rate:.3f})."
        )

    def test_cluster_robust_agrees_with_naive_on_iid_data(self) -> None:
        """On i.i.d. data (every observation its own singleton cluster) the
        cluster-robust estimator must reduce to the naive ``ttest_1samp``.

        This guards against the fix spuriously moving a result that had no
        autocorrelation to correct.  With singleton clusters there are no
        within-cluster cross terms, so the sandwich variance equals the naive
        variance (the small-sample factor G/(G−1) → N/(N−1) matches the t-test's
        own (n−1) divisor).
        """
        import scipy.stats

        cluster_robust = self._import()

        rng    = np.random.default_rng(42)
        scores = list(rng.normal(0.3, 1.0, size=200))
        # Every observation is its own cluster → i.i.d. case.
        labels = [f"obs{i}" for i in range(len(scores))]

        naive = scipy.stats.ttest_1samp(np.array(scores), 0.0)
        t_cluster, p_cluster, df_cluster = cluster_robust(scores, labels)

        assert t_cluster == pytest.approx(float(naive.statistic), rel=1e-6), (
            f"Cluster-robust t ({t_cluster:.6f}) must match naive "
            f"({float(naive.statistic):.6f}) on i.i.d. data."
        )
        assert p_cluster == pytest.approx(float(naive.pvalue), rel=1e-6), (
            f"Cluster-robust p ({p_cluster:.6f}) must match naive "
            f"({float(naive.pvalue):.6f}) on i.i.d. data."
        )
        assert df_cluster == len(scores) - 1, (
            f"i.i.d. degrees of freedom should be N−1={len(scores) - 1}; "
            f"got {df_cluster}."
        )

    def test_cluster_robust_inflates_se_relative_to_naive_when_autocorrelated(
        self,
    ) -> None:
        """For a single autocorrelated sample, the cluster-robust |t| must be
        SMALLER than the naive |t| (the honest SE is larger).

        Same mean-excess, same n — only the SE / t-stat changes, which is the
        whole point of the fix.
        """
        import scipy.stats

        cluster_robust = self._import()

        rng = np.random.default_rng(7)
        # Strong within-ticker autocorrelation, small non-zero mean.
        scores, labels = self._make_autocorrelated_panel(
            rng, n_tickers=8, n_per_ticker=12,
        )
        # Nudge the mean slightly positive so |t| is well-defined and non-trivial.
        scores = [s + 0.2 for s in scores]

        naive = scipy.stats.ttest_1samp(np.array(scores), 0.0)
        t_cluster, _p, _df = cluster_robust(scores, labels)

        assert abs(t_cluster) < abs(float(naive.statistic)), (
            f"Cluster-robust |t| ({abs(t_cluster):.3f}) should be smaller than "
            f"naive |t| ({abs(float(naive.statistic)):.3f}) under autocorrelation."
        )

    def test_pipeline_threads_ticker_clusters_through_to_cell(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """End-to-end: ``build_analyst_scoreboard`` with ``inference='cluster_ticker'``
        must produce a cluster-robust t-stat that differs from the naive one when
        the same ticker contributes many serially-correlated observations, while
        leaving ``n`` and ``mean_excess_bps`` unchanged.

        Setup: one analyst (technical), bullish on AAPL across many ticks (each a
        DISTINCT confidence so dedup keeps them — simulating overlapping windows),
        plus a BEARISH MSFT call (also a winning call, since MSFT lags AAPL every
        tick).  MSFT is deliberately directional (not neutral) here — under
        directional-only scoring, a neutral MSFT would be excluded entirely from
        'directional', leaving ONLY the AAPL ticker cluster and degenerating the
        cluster-robust estimator (a single cluster has no defined variance).  AAPL
        outperforms every tick by the same margin, so naive inference sees many
        "independent" wins and reports a large |t|; the cluster-robust estimator
        (two ticker clusters) deflates it.
        """
        from backtest.scoreboard import build_analyst_scoreboard

        # Twelve ticks, each a distinct confidence so dedup treats them as fresh.
        # A THIRD ticker (GOOG, neutral) is included purely to break the
        # AAPL/MSFT mirror symmetry: with only two tickers in the peer group,
        # AAPL's bullish score and MSFT's bearish score are mathematically
        # IDENTICAL at every tick (both equal (aapl_fwd − msft_fwd) / 2),
        # which makes their two "clusters" perfectly correlated — a
        # degenerate input for the cluster-robust sandwich estimator
        # (G=2 with perfectly-correlated cluster sums) regardless of any
        # per-tick wobble.  GOOG gives each ticker its own independent peer
        # mean and a third (here unscored) cluster slot is irrelevant since
        # GOOG is neutral — the key fix is simply that AAPL's and MSFT's
        # excess no longer have to be exact mirror images of one another.
        rows = []
        for i in range(12):
            ts = datetime(2025, 9, 1 + i, 13, 30, tzinfo=UTC)
            rows.append(_make_evidence_row(
                analyst="technical", ticker="AAPL",
                tick_id=f"tick-{i}", recorded_at=ts, lean="bullish",
                confidence=0.50 + 0.01 * i,
            ))
            rows.append(_make_evidence_row(
                analyst="technical", ticker="MSFT",
                tick_id=f"tick-{i}", recorded_at=ts, lean="bearish",
                confidence=0.50 + 0.01 * i,
            ))
            rows.append(_make_evidence_row(
                analyst="technical", ticker="GOOG",
                tick_id=f"tick-{i}", recorded_at=ts, lean="neutral",
                confidence=0.50 + 0.01 * i,
            ))

        db_path = _build_fixture_db(tmp_path, rows)

        # Forward returns vary slightly tick-to-tick (rather than being a
        # perfectly constant +5 % / 0 %) so that within-cluster residuals are
        # not EXACTLY zero-variance.  A perfectly-constant-per-cluster score
        # is a genuine edge case for the sandwich estimator (the cluster sums
        # become deterministic multiples of each other, and finite-precision
        # cancellation can blow the SE up or down to a degenerate near-zero
        # value) — this is a property of the estimator's degenerate input,
        # not a bug in it, but it makes a poor fixture for asserting "the
        # honest SE is larger".  A small per-tick wobble keeps the comparison
        # well-conditioned while preserving "AAPL consistently outperforms".
        # Index by the VERDICT tick (0..11), not the forward-bar calendar
        # date — the forward bar for tick i lands one day later (Sep 2+i),
        # so the lookup below converts back to the tick index explicitly.
        # A genuinely irregular (non-arithmetic-progression) per-tick wobble.
        # A perfectly linear progression leaves residuals perfectly linear
        # too, which is itself a degenerate input to the sandwich estimator
        # (the cluster sums become exact rational multiples of each other,
        # and the resulting variance can collapse to numerical noise).  An
        # irregular sequence avoids this without weakening the "AAPL
        # consistently outperforms" narrative (every wobble stays small
        # relative to the +5 pp / 0 pp gap between the two tickers).
        _rng = np.random.default_rng(seed=42)
        aapl_fwd_pcts = [0.05 + 0.002 * w for w in _rng.standard_normal(12)]
        msft_fwd_pcts = [0.00 + 0.001 * w for w in _rng.standard_normal(12)]
        goog_fwd_pcts = [0.02 + 0.001 * w for w in _rng.standard_normal(12)]

        # NOTE: base-price queries use exactly the tick's own date (Sep 1..12);
        # forward queries (target = tick_date + 1 day) ask for Sep 2..13 — an
        # OVERLAPPING range with the base dates.  Distinguish them by exact
        # (ticker, start) -> tick-index mapping built up-front instead of a
        # set-membership test, so a forward query for tick i is never
        # mistaken for a base-price query belonging to tick i+1.
        from datetime import timedelta as _timedelta
        base_date_to_tick_idx = {date(2025, 9, 1 + i): i for i in range(12)}

        def _mock_read(ticker, start, end):
            ts_bar = datetime(start.year, start.month, start.day, 13, 30, tzinfo=UTC)
            # `end` distinguishes the two call shapes: the base-price lookup
            # passes start == end (single-day query); the forward-close
            # lookup passes a multi-day window (start, start + 4 days).
            is_base_price_query = (end == start)
            if is_base_price_query:
                return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=100.0, close=100.0)]
            # Forward bar query: start == tick_date + 1 day → recover tick index.
            tick_idx = base_date_to_tick_idx[start - _timedelta(days=1)]
            if ticker == "AAPL":
                pct = aapl_fwd_pcts[tick_idx]
            elif ticker == "MSFT":
                pct = msft_fwd_pcts[tick_idx]
            else:
                pct = goog_fwd_pcts[tick_idx]
            close = 100.0 * (1 + pct)
            return [_make_ohlcbar(ticker=ticker, ts=ts_bar, open=close, close=close)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result_naive = build_analyst_scoreboard(
            db_path=db_path, cache=mock_cache, horizons=[1], inference="naive",
        )
        result_cluster = build_analyst_scoreboard(
            db_path=db_path, cache=mock_cache, horizons=[1], inference="cluster_ticker",
        )

        cell_naive   = result_naive.cell(analyst="technical", horizon=1, subset="directional")
        cell_cluster = result_cluster.cell(analyst="technical", horizon=1, subset="directional")

        # n and mean-excess must be IDENTICAL across inference modes.
        assert cell_cluster.n == cell_naive.n, "n must not change with inference mode"
        assert cell_cluster.mean_excess_bps == pytest.approx(
            cell_naive.mean_excess_bps, rel=1e-9,
        ), "mean excess must not change with inference mode"

        # The cluster-robust |t| must be strictly smaller (honest SE is larger).
        assert math.isfinite(cell_cluster.t_stat)
        assert abs(cell_cluster.t_stat) < abs(cell_naive.t_stat), (
            f"Cluster-robust |t| ({abs(cell_cluster.t_stat):.3f}) should be below "
            f"naive |t| ({abs(cell_naive.t_stat):.3f}) for a repeated single-ticker call."
        )


# ── Tests: multi-window pooling (Phase 14 — TDD, written BEFORE implementation) ──

class TestMultiWindowPooling:
    """Pooling observations from N ``(db_path, cache, window_id)`` triples into
    one aggregated scoreboard, while keeping each window's own scoreboard intact.

    Locked design decisions under test:
      1. Peer groups stay strictly within-window (cs-mean keyed on tick_id,
         which is already unique per window — pooling must not contaminate it).
      2. Dedup runs per-window, BEFORE pooling (never across the pooled set).
      3. Cluster-robust SE clusters by (ticker, window) when pooling, not bare
         ticker — single-window behaviour (cluster by ticker) is unchanged.
      4. Pooled n = sum of per-window n's; backwards-compat with the existing
         single-window ``build_analyst_scoreboard`` is preserved exactly.
    """

    def _import(self):
        """Lazy import of the pooling entrypoint under test."""
        from backtest.scoreboard import build_pooled_analyst_scoreboard
        return build_pooled_analyst_scoreboard

    # ── Case 1: pooled n = sum of per-window n's ──────────────────────────────

    def test_pooled_n_equals_sum_of_per_window_n(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Pooling two windows, each with 1 directional verdict (AAPL bullish;
        MSFT is neutral and excluded entirely), must yield n=2 in the pooled
        'directional' cell — the simple sum of each window's directional n.
        """
        from backtest.scoreboard import build_analyst_scoreboard

        build_pooled = self._import()

        def _make_window(tmp_dir, ticker_fwd):
            """Build a one-tick, two-ticker fixture window and return (db_path, cache)."""
            rows = [
                _make_evidence_row(
                    analyst="technical", ticker="AAPL",
                    tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
                ),
                _make_evidence_row(
                    analyst="technical", ticker="MSFT",
                    tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
                ),
            ]
            db_path = _build_fixture_db(tmp_dir, rows)

            def _mock_read(ticker, start, end):
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
                close = ticker_fwd.get(ticker, 100.0)
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=close, close=close)]

            mock_cache = MagicMock()
            mock_cache.read_ohlcv.side_effect = _mock_read
            return db_path, mock_cache

        win_a_dir = tmp_path / "win_a"
        win_b_dir = tmp_path / "win_b"
        win_a_dir.mkdir()
        win_b_dir.mkdir()

        db_a, cache_a = _make_window(win_a_dir, {"AAPL": 105.0, "MSFT": 101.0})
        db_b, cache_b = _make_window(win_b_dir, {"AAPL": 103.0, "MSFT": 100.0})

        # Sanity: each window alone scores n=1 directional (MSFT neutral excluded).
        result_a_alone = build_analyst_scoreboard(db_path=db_a, cache=cache_a, horizons=[1])
        result_b_alone = build_analyst_scoreboard(db_path=db_b, cache=cache_b, horizons=[1])
        assert result_a_alone.cell(analyst="technical", horizon=1, subset="directional").n == 1
        assert result_b_alone.cell(analyst="technical", horizon=1, subset="directional").n == 1

        pooled = build_pooled(
            runs=[
                (db_a, cache_a, "window-a"),
                (db_b, cache_b, "window-b"),
            ],
            horizons=[1],
        )

        cell = pooled.cell(analyst="technical", horizon=1, subset="directional")
        assert cell.n == 2, f"Expected pooled n=2 (1+1); got {cell.n}"

    # ── Case 2: peer-group isolation — pooling must not change a window's
    #             excess returns relative to scoring it alone ─────────────────

    def test_peer_groups_stay_within_window_not_contaminated_by_pooling(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """If peer means were (incorrectly) computed over the pooled set, the
        mean used to compute AAPL's excess in window A would shift because
        window B's tickers have a wildly different cross-sectional mean.

        Window A: AAPL +5 %, MSFT +1 % → peer mean (universe) = 3 %.
        Window B: AAPL +5 %, MSFT +50 % → peer mean (universe) = 27.5 %.

        If pooled incorrectly (means computed over the union), AAPL's excess in
        EITHER window would be computed against a blended mean across BOTH
        windows' tickers — which is wrong, because tick_ids are window-local and
        a peer group must never cross a tick/window boundary.

        We assert that the per-window excess scores recovered from the pooled
        per-observation records are IDENTICAL to scoring each window alone —
        proving the peer mean was never blended across windows.
        """
        build_pooled       = self._import()
        from backtest.scoreboard import build_analyst_scoreboard

        def _make_window(tmp_dir, ticker_fwd):
            rows = [
                _make_evidence_row(
                    analyst="technical", ticker="AAPL",
                    tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
                ),
                _make_evidence_row(
                    analyst="technical", ticker="MSFT",
                    tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
                ),
            ]
            db_path = _build_fixture_db(tmp_dir, rows)

            def _mock_read(ticker, start, end):
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
                close = ticker_fwd.get(ticker, 100.0)
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=close, close=close)]

            mock_cache = MagicMock()
            mock_cache.read_ohlcv.side_effect = _mock_read
            return db_path, mock_cache

        win_a_dir = tmp_path / "win_a"
        win_b_dir = tmp_path / "win_b"
        win_a_dir.mkdir()
        win_b_dir.mkdir()

        # Window A: AAPL +5 %, MSFT +1 % → universe mean = 3 %. AAPL excess = +2 %.
        db_a, cache_a = _make_window(win_a_dir, {"AAPL": 105.0, "MSFT": 101.0})
        # Window B: AAPL +5 %, MSFT +50 % → universe mean = 27.5 %. AAPL excess = -22.5 %.
        db_b, cache_b = _make_window(win_b_dir, {"AAPL": 105.0, "MSFT": 150.0})

        result_a_alone = build_analyst_scoreboard(
            db_path=db_a, cache=cache_a, horizons=[1], neutralise_by="universe",
        )
        result_b_alone = build_analyst_scoreboard(
            db_path=db_b, cache=cache_b, horizons=[1], neutralise_by="universe",
        )
        alone_a_bps = result_a_alone.cell(analyst="technical", horizon=1, subset="directional").mean_excess_bps
        alone_b_bps = result_b_alone.cell(analyst="technical", horizon=1, subset="directional").mean_excess_bps

        # Sanity: the two windows' peer means genuinely differ — otherwise this
        # test would not be able to detect cross-window contamination.
        assert alone_a_bps != pytest.approx(alone_b_bps, rel=1e-3), (
            "Fixture windows must have meaningfully different peer means for "
            "this isolation test to be discriminating."
        )

        pooled = build_pooled(
            runs=[
                (db_a, cache_a, "window-a"),
                (db_b, cache_b, "window-b"),
            ],
            horizons=[1],
            neutralise_by="universe",
        )
        pooled_bps = pooled.cell(analyst="technical", horizon=1, subset="directional").mean_excess_bps

        # Pooled mean must be the SIMPLE AVERAGE of the two windows' per-
        # observation scores (each window has 2 verdicts: AAPL hit, MSFT
        # neutral-zero) — i.e. exactly the mean of the two alone-runs' means,
        # since both windows contribute the same n and the AAPL score is the
        # only non-zero contributor in each.
        expected_pooled_bps = (alone_a_bps + alone_b_bps) / 2.0
        assert pooled_bps == pytest.approx(expected_pooled_bps, rel=1e-6), (
            f"Pooled mean excess ({pooled_bps:.4f} bps) must equal the average "
            f"of the two windows scored alone ({expected_pooled_bps:.4f} bps) — "
            f"any deviation implies the peer mean leaked across windows."
        )

    # ── Case 3: dedup runs per-window, never across the pooled set ────────────

    def test_dedup_is_per_window_not_across_pooled_set(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A ticker whose verdict tuple is identical at the boundary across two
        windows must still yield ONE observation PER WINDOW (n=2 total), not
        collapsed into a single observation across the pooled set.

        If dedup ran AFTER pooling (on the concatenated, ordered-by-window
        record list), the last call in window A and the first call in window B
        — both bullish/0.5/0.7 — would look like a "replay" and collapse to
        n=1.  Dedup must run inside each window's own helper, before pooling.
        """
        build_pooled = self._import()

        def _make_window(tmp_dir):
            """One bullish AAPL verdict + one neutral MSFT anchor, identical
            (lean, magnitude, confidence) tuple in both windows."""
            rows = [
                _make_evidence_row(
                    analyst="technical", ticker="AAPL",
                    tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
                    magnitude=0.5, confidence=0.7,
                ),
                _make_evidence_row(
                    analyst="technical", ticker="MSFT",
                    tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
                    magnitude=0.0, confidence=0.5,
                ),
            ]
            db_path = _build_fixture_db(tmp_dir, rows)

            def _mock_read(ticker, start, end):
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
                close = 105.0 if ticker == "AAPL" else 100.0
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=close, close=close)]

            mock_cache = MagicMock()
            mock_cache.read_ohlcv.side_effect = _mock_read
            return db_path, mock_cache

        win_a_dir = tmp_path / "win_a"
        win_b_dir = tmp_path / "win_b"
        win_a_dir.mkdir()
        win_b_dir.mkdir()

        db_a, cache_a = _make_window(win_a_dir)
        db_b, cache_b = _make_window(win_b_dir)

        pooled = build_pooled(
            runs=[
                (db_a, cache_a, "window-a"),
                (db_b, cache_b, "window-b"),
            ],
            horizons=[1],
        )

        cell = pooled.cell(analyst="technical", horizon=1, subset="directional")
        # Identical tuples ACROSS windows must NOT collapse: 1 directional obs
        # per window (AAPL anchor; MSFT's neutral anchor is excluded from
        # 'directional') × 2 windows = 2.
        assert cell.n == 2, (
            f"Expected n=2 (1 directional obs per window × 2 windows, dedup not "
            f"crossing the window boundary); got {cell.n}.  If dedup incorrectly "
            f"ran across the pooled set, identical boundary tuples would collapse."
        )

    # ── Case 4: cluster key is (ticker, window) — produces a different SE/t
    #             than clustering by bare ticker ─────────────────────────────

    def test_cluster_key_is_ticker_and_window_not_bare_ticker(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Pooling the SAME ticker's observations from two unrelated windows
        must cluster them as TWO separate clusters (ticker, window-a) and
        (ticker, window-b) — not collapsed into one bare-ticker cluster.

        We construct a pooled case where clustering by bare ticker vs by
        (ticker, window) produces a DIFFERENT t-stat, and assert the actual
        result matches the (ticker, window) clustering, not bare-ticker.

        Setup: AAPL appears many times in window A (strong shared level, autocorrelated)
        and many times in window B (a different shared level). If clustered by
        bare ticker, all of AAPL's observations across both windows collapse into
        ONE cluster — drastically inflating the SE (only 1 AAPL cluster total once
        MSFT's clusters are factored in, the d.o.f. shrinks). Clustering by
        (ticker, window) correctly treats them as 2 distinct clusters.

        MSFT is given a BEARISH (not neutral) lean here — under the
        directional-only scoring change, a neutral MSFT anchor would be
        excluded entirely from the 'directional' subset, collapsing the
        bare-ticker scheme to a single degenerate AAPL-only cluster (G=1,
        undefined SE) and making the two clustering schemes impossible to
        compare meaningfully.  A bearish MSFT keeps it IN the directional
        subset (contributing a genuine non-zero score) so the bare-ticker
        vs (ticker, window) contrast remains well-defined.
        """
        from backtest.scoreboard import _cluster_robust_ttest

        build_pooled = self._import()

        def _make_window(tmp_dir, window_tag, fwd_aapl_pct):
            """Many ticks of a bullish AAPL call (distinct confidence so dedup
            keeps every tick) plus a bearish MSFT call, in one window."""
            rows = []
            for i in range(8):
                ts = datetime(2025, 9, 1 + i, 13, 30, tzinfo=UTC)
                rows.append(_make_evidence_row(
                    analyst="technical", ticker="AAPL",
                    tick_id=f"{window_tag}-tick-{i}", recorded_at=ts, lean="bullish",
                    confidence=0.50 + 0.01 * i,
                ))
                rows.append(_make_evidence_row(
                    analyst="technical", ticker="MSFT",
                    tick_id=f"{window_tag}-tick-{i}", recorded_at=ts, lean="bearish",
                    confidence=0.50 + 0.01 * i,
                ))
            db_path = _build_fixture_db(tmp_dir, rows)

            base_dates = {date(2025, 9, 1 + i) for i in range(8)}

            def _mock_read(ticker, start, end):
                if start in base_dates:
                    return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
                close = (100.0 * (1 + fwd_aapl_pct)) if ticker == "AAPL" else 100.0
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=close, close=close)]

            mock_cache = MagicMock()
            mock_cache.read_ohlcv.side_effect = _mock_read
            return db_path, mock_cache

        win_a_dir = tmp_path / "win_a"
        win_b_dir = tmp_path / "win_b"
        win_a_dir.mkdir()
        win_b_dir.mkdir()

        # Window A: AAPL beats peers by +5 % every tick.  Window B: by +9 %.
        db_a, cache_a = _make_window(win_a_dir, "win-a", 0.05)
        db_b, cache_b = _make_window(win_b_dir, "win-b", 0.09)

        pooled = build_pooled(
            runs=[
                (db_a, cache_a, "window-a"),
                (db_b, cache_b, "window-b"),
            ],
            horizons=[1],
            inference="cluster_ticker",
        )
        cell = pooled.cell(analyst="technical", horizon=1, subset="directional")

        # Reconstruct what bare-ticker clustering would have produced, using
        # the SAME scores but labelling every AAPL observation (regardless of
        # window) with the bare ticker "AAPL", and every MSFT observation
        # "MSFT" — exactly the buggy behaviour decision 3 forbids.
        #
        # We can't reach into the pooled internals directly, so we rebuild the
        # equivalent scores/labels under both candidate cluster-label schemes.
        # Every score within a window/ticker pair is identical (deterministic
        # fixture: n=8 AAPL + 8 MSFT per window), so reconstruct the raw
        # per-observation scores directly rather than relying on cells.
        #
        # cs_mean per window = (fwd_aapl_pct + 0.0) / 2 (MSFT is flat).
        # AAPL (bullish): score = +1 × (fwd_aapl_pct − cs_mean) = fwd_aapl_pct / 2.
        # MSFT (bearish, flat): score = −1 × (0.0 − cs_mean) = cs_mean = fwd_aapl_pct / 2.
        aapl_score_a = 0.05 - (0.05 / 2)   # AAPL excess vs cs-mean of (AAPL, MSFT) in window A
        msft_score_a = -1 * (0.0 - (0.05 / 2))  # MSFT bearish, flat, vs the same cs-mean
        aapl_score_b = 0.09 - (0.09 / 2)   # ditto for window B
        msft_score_b = -1 * (0.0 - (0.09 / 2))

        scores_pooled = (
            [aapl_score_a] * 8 + [msft_score_a] * 8 +    # window A: 8 AAPL + 8 MSFT
            [aapl_score_b] * 8 + [msft_score_b] * 8       # window B: 8 AAPL + 8 MSFT
        )
        labels_bare_ticker = (
            ["AAPL"] * 8 + ["MSFT"] * 8 +
            ["AAPL"] * 8 + ["MSFT"] * 8
        )
        labels_ticker_window = (
            [("AAPL", "window-a")] * 8 + [("MSFT", "window-a")] * 8 +
            [("AAPL", "window-b")] * 8 + [("MSFT", "window-b")] * 8
        )

        t_bare, _p_bare, _df_bare = _cluster_robust_ttest(scores_pooled, labels_bare_ticker)
        t_correct, _p_correct, _df_correct = _cluster_robust_ttest(scores_pooled, labels_ticker_window)

        # The two clustering schemes must disagree (otherwise this fixture
        # fails to discriminate the bug from the fix).
        assert t_bare != pytest.approx(t_correct, rel=1e-9), (
            "Fixture must produce a different t-stat under bare-ticker vs "
            "(ticker, window) clustering for this test to be meaningful."
        )

        # The pooled scoreboard's actual t-stat must match the (ticker, window)
        # clustering, NOT the bare-ticker clustering.
        assert cell.t_stat == pytest.approx(t_correct, rel=1e-6), (
            f"Pooled t-stat ({cell.t_stat:.4f}) must match (ticker, window) "
            f"clustering ({t_correct:.4f}), not bare-ticker clustering "
            f"({t_bare:.4f})."
        )

    # ── Case 5: backwards compat — pooling a single window equals the
    #             existing build_analyst_scoreboard output for that window ────

    def test_pooling_single_window_matches_build_analyst_scoreboard(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Pooling exactly ONE ``(db_path, cache, window_id)`` triple must
        produce byte-identical cells (n, mean, hit-rate, t-stat, p-value) to
        calling ``build_analyst_scoreboard`` directly on that window.

        Single-window cluster key reduces to the bare ticker either way (one
        window means (ticker, window) and ticker are in 1:1 correspondence), so
        this also proves decision 3 doesn't change single-window behaviour.
        """
        from backtest.scoreboard import build_analyst_scoreboard

        build_pooled = self._import()

        rows = [
            _make_evidence_row(
                analyst="technical", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
            _make_evidence_row(
                analyst="technical", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bearish",
            ),
            _make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bearish",
            ),
        ]
        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            prices = {"AAPL": (100.0, 105.0), "MSFT": (100.0, 101.0)}
            base, fwd = prices.get(ticker, (100.0, 100.0))
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=base, close=base)]
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=fwd, close=fwd)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        direct = build_analyst_scoreboard(
            db_path=db_path, cache=mock_cache, horizons=[1, 5],
            primary_horizon_by_analyst={"fundamental": 5},
            neutralise_by="universe",
            inference="cluster_ticker",
        )
        pooled = build_pooled(
            runs=[(db_path, mock_cache, "only-window")],
            horizons=[1, 5],
            primary_horizon_by_analyst={"fundamental": 5},
            neutralise_by="universe",
            inference="cluster_ticker",
        )

        assert pooled.analysts == direct.analysts
        assert pooled.horizons == direct.horizons

        for analyst in direct.analysts:
            for h in direct.horizons:
                for subset in ("directional", "bullish", "bearish"):
                    d_cell = direct.cell(analyst=analyst, horizon=h, subset=subset)
                    p_cell = pooled.cell(analyst=analyst, horizon=h, subset=subset)

                    assert p_cell.n == d_cell.n, (
                        f"n mismatch for {analyst}/{h}/{subset}: "
                        f"{p_cell.n} vs {d_cell.n}"
                    )
                    if math.isfinite(d_cell.mean_excess_bps):
                        assert p_cell.mean_excess_bps == pytest.approx(
                            d_cell.mean_excess_bps, rel=1e-9, abs=1e-9,
                        )
                    else:
                        assert math.isnan(p_cell.mean_excess_bps)
                    if math.isfinite(d_cell.hit_rate):
                        assert p_cell.hit_rate == pytest.approx(d_cell.hit_rate, rel=1e-9)
                    else:
                        assert math.isnan(p_cell.hit_rate)
                    if math.isfinite(d_cell.t_stat):
                        assert p_cell.t_stat == pytest.approx(d_cell.t_stat, rel=1e-9)
                    else:
                        assert math.isnan(p_cell.t_stat)
                    if math.isfinite(d_cell.p_value):
                        assert p_cell.p_value == pytest.approx(d_cell.p_value, rel=1e-9)
                    else:
                        assert math.isnan(p_cell.p_value)


# ── Tests: neutral-coverage / abstention diagnostic (Phase 14, iter-11) ──────

class TestCoverageDiagnostic:
    """Unit tests for ``ScoreboardResult.coverage`` — the per-analyst
    neutral/abstention diagnostic introduced alongside the directional-only
    scoring change.

    Decision (documented here and in the production docstring): the
    denominator for ``neutral_rate`` is the FORWARD-BAR-SCOREABLE set — i.e.
    every deduplicated verdict that had both a base price and a forward
    close at this horizon (the same set that feeds 'directional' + neutral
    scoring), NOT the raw ``analyst_evidence`` row count.  This makes
    ``neutral_rate`` directly comparable to the 'directional' cell's n at the
    same horizon: ``n_neutral + directional.n == coverage.n_scoreable``.
    Verdicts excluded for missing price data (window edge, no base bar) are
    excluded from BOTH the directional cell and the coverage denominator —
    counting them as "neutral" would conflate two different reasons for
    absence (the analyst abstained vs. the data was unavailable).
    """

    def _import(self):
        """Lazy import of the single-window scoring entrypoint under test."""
        from backtest.scoreboard import build_analyst_scoreboard
        return build_analyst_scoreboard

    def test_coverage_counts_neutral_and_directional_correctly(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Two directional verdicts (AAPL bullish, MSFT bearish) and one
        neutral verdict (GOOG) at the same tick/horizon must report
        n_neutral=1 and neutral_rate = 1/3.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
            _make_evidence_row(
                analyst="fundamental", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bearish",
            ),
            _make_evidence_row(
                analyst="fundamental", ticker="GOOG",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
            ),
        ]
        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=101.0, close=101.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        coverage = result.coverage(analyst="fundamental", horizon=1)
        assert coverage.n_neutral == 1, f"Expected n_neutral=1; got {coverage.n_neutral}"
        assert coverage.n_scoreable == 3, f"Expected n_scoreable=3; got {coverage.n_scoreable}"
        assert coverage.neutral_rate == pytest.approx(1 / 3, abs=1e-9), (
            f"Expected neutral_rate≈0.3333; got {coverage.neutral_rate}"
        )

    def test_coverage_excludes_window_edge_verdicts_from_denominator(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A verdict with no forward bar at this horizon (window edge) must be
        excluded from BOTH the directional cell and the coverage denominator
        — it is a data-availability gap, not an abstention.
        """
        build = self._import()

        # AAPL has a forward bar; MSFT (neutral) does not (simulated window edge).
        rows = [
            _make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
            _make_evidence_row(
                analyst="fundamental", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
            ),
        ]
        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=101.0, close=101.0)]
            if ticker == "MSFT":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="MSFT", ts=OPEN_TICK, open=100.0, close=100.0)]
                return []  # no forward bar — window edge
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        coverage = result.coverage(analyst="fundamental", horizon=1)
        assert coverage.n_neutral == 0, (
            f"MSFT's neutral verdict has no forward bar — must be excluded "
            f"from n_neutral entirely; got {coverage.n_neutral}"
        )
        assert coverage.n_scoreable == 1, (
            f"Only AAPL's bullish verdict is scoreable; got {coverage.n_scoreable}"
        )
        assert coverage.neutral_rate == pytest.approx(0.0, abs=1e-9)

    def test_coverage_raises_keyerror_for_unknown_analyst(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """``coverage()`` must raise (not return a default/empty value) when
        the analyst is not present in the result — per the project's
        raise-don't-return-None policy.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
        ]
        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        with pytest.raises(KeyError):
            result.coverage(analyst="nonexistent_analyst", horizon=1)


# ── Tests: confidence-gradient view (Phase 14, iter-11) ──────────────────────

class TestConfidenceGradient:
    """Unit tests for the confidence-gradient view — buckets directional
    (non-neutral) calls by recorded ``confidence`` and reports mean excess,
    n, hit rate and t-stat per bucket, per analyst, at the analyst's primary
    horizon.

    Bucketing decision (documented here and in the production docstring):
    confidence is a CONTINUOUS float in [0, 1] whose real-world range varies
    sharply per analyst (e.g. technical ~0.14-0.9 vs fundamental ~0.6-0.85
    in production data), so a single global numeric threshold would not be
    meaningful across analysts.  Buckets are therefore TERCILES (default 3,
    configurable via ``scoreboard_confidence_buckets``) computed from that
    analyst's OWN directional-confidence distribution at its primary
    horizon — i.e. ``np.quantile`` cut points derived from the data itself,
    never a hardcoded number.
    """

    def _import(self):
        """Lazy import of the confidence-gradient entrypoint under test."""
        from backtest.scoreboard import build_analyst_scoreboard
        return build_analyst_scoreboard

    def test_high_confidence_bucket_outperforms_low_confidence_bucket(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Six bullish AAPL-style verdicts (deduplicated via distinct
        confidence) split into two natural groups: three low-confidence calls
        that LOSE to peers, three high-confidence calls that BEAT peers.
        With 3 buckets requested over 6 points, ``np.array_split`` cuts the
        sorted-by-confidence sequence into three EQUAL-SIZE (n=2) contiguous
        chunks: the lowest two confidences land in the lowest bucket, and the
        highest two in the highest bucket — both losing/winning calls
        respectively — so the high bucket's mean excess must exceed the low
        bucket's.
        """
        build = self._import()

        # Six ticks, one ticker each isolated from peers, low conf → bad call,
        # high conf → good call.  Using six distinct tickers (one per tick)
        # keeps the per-tick cross-sectional mean at the single ticker's own
        # return — i.e. one ticker is its own peer group, so we engineer the
        # PEER via a second ticker per tick that is flat, making excess equal
        # to the analyst's ticker's own fwd return.
        confidences = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
        fwd_pcts    = [-0.05, -0.04, -0.03, 0.05, 0.06, 0.07]  # low conf → losses, high conf → wins
        rows = []
        tick_dates = []
        for i, (conf, _pct) in enumerate(zip(confidences, fwd_pcts, strict=True)):
            ts = datetime(2025, 9, 1 + i, 13, 30, tzinfo=UTC)
            tick_dates.append(ts.date())
            rows.append(_make_evidence_row(
                analyst="technical", ticker=f"T{i}",
                tick_id=f"tick-{i}", recorded_at=ts, lean="bullish",
                confidence=conf,
            ))
            # Flat peer ticker at the same tick, so the cross-sectional mean
            # is exactly half of T{i}'s fwd return, giving T{i} a non-zero
            # excess equal to half its own fwd return (same sign).
            rows.append(_make_evidence_row(
                analyst="technical", ticker=f"P{i}",
                tick_id=f"tick-{i}", recorded_at=ts, lean="neutral",
                confidence=conf,
            ))
        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            if ticker.startswith("P"):
                # Peer ticker: flat throughout.
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
            idx = int(ticker[1:])
            if start == tick_dates[idx]:
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
            close = 100.0 * (1 + fwd_pcts[idx])
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=close, close=close)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(
            db_path=db_path, cache=mock_cache, horizons=[1],
            primary_horizon_by_analyst={"technical": 1},
        )

        gradient = result.confidence_gradient(analyst="technical", horizon=1, n_buckets=3)

        assert len(gradient) == 3, f"Expected 3 buckets; got {len(gradient)}"

        low_bucket  = gradient[0]
        high_bucket = gradient[-1]

        assert low_bucket.n == 2, f"Expected 2 low-confidence calls; got {low_bucket.n}"
        assert high_bucket.n == 2, f"Expected 2 high-confidence calls; got {high_bucket.n}"

        assert low_bucket.mean_excess_bps < 0, (
            f"Expected the low-confidence bucket to have negative mean excess; "
            f"got {low_bucket.mean_excess_bps:.4f} bps"
        )
        assert high_bucket.mean_excess_bps > 0, (
            f"Expected the high-confidence bucket to have positive mean excess; "
            f"got {high_bucket.mean_excess_bps:.4f} bps"
        )
        assert high_bucket.mean_excess_bps > low_bucket.mean_excess_bps, (
            f"Expected high-confidence calls to outperform low-confidence "
            f"calls: high={high_bucket.mean_excess_bps:.4f} bps, "
            f"low={low_bucket.mean_excess_bps:.4f} bps"
        )

    def test_confidence_gradient_excludes_neutral_verdicts(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """A neutral verdict must not appear in any confidence bucket — the
        gradient view operates strictly on the 'directional' subset.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
                confidence=0.9,
            ),
            _make_evidence_row(
                analyst="fundamental", ticker="MSFT",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="neutral",
                confidence=0.95,
            ),
        ]
        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=105.0, close=105.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        gradient = result.confidence_gradient(analyst="fundamental", horizon=1, n_buckets=3)
        total_n  = sum(bucket.n for bucket in gradient)

        assert total_n == 1, (
            f"Expected exactly 1 directional observation (AAPL bullish) across "
            f"all buckets; MSFT's neutral verdict must be excluded.  Got "
            f"total_n={total_n}"
        )

    def test_confidence_gradient_raises_keyerror_for_unknown_analyst(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """``confidence_gradient()`` must raise (not silently return an empty
        list) when the analyst is not present in the result.
        """
        build = self._import()

        rows = [
            _make_evidence_row(
                analyst="fundamental", ticker="AAPL",
                tick_id=TICK_A, recorded_at=OPEN_TICK, lean="bullish",
            ),
        ]
        db_path = _build_fixture_db(tmp_path, rows)

        def _mock_read(ticker, start, end):
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        with pytest.raises(KeyError):
            result.confidence_gradient(analyst="nonexistent_analyst", horizon=1, n_buckets=3)


# ── Tests: the digest aggregate scored as a pseudo-analyst (Task 14) ─────────

class TestAggregatePseudoAnalyst:
    """The cross-analyst aggregate (``ticker_evidence``) must join the
    scoreboard as its own synthetic ``"aggregate"`` analyst, scored on
    forward returns exactly like a real analyst.
    """

    def _import(self):
        """Lazy import so tests fail clearly if the symbol doesn't exist yet."""
        from backtest.scoreboard import build_analyst_scoreboard
        return build_analyst_scoreboard

    def test_aggregate_scored_as_pseudo_analyst(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """The aggregate joins the scoreboard as its own analyst row, and is
        actually SCORED (n >= 1 in the directional cell) — not merely listed
        in ``analysts_seen`` with zero scored observations.
        """
        build = self._import()

        rows: list[PersistenceBase] = [
            # A real analyst's verdict on the same tick, so the run is not
            # degenerately aggregate-only.
            _make_evidence_row(
                analyst="technical",
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="neutral",
            ),
            # A directional (non-neutral) aggregate for AAPL on the same tick.
            _make_ticker_evidence_row(
                ticker="AAPL",
                tick_id=TICK_A,
                recorded_at=OPEN_TICK,
                lean="bullish",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        # AAPL: base 100, fwd 105 (+5 %) — a real move for the aggregate to score.
        def _mock_read(ticker, start, end):
            if ticker == "AAPL":
                if start == date(2025, 9, 5):
                    return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=100.0, close=100.0)]
                return [_make_ohlcbar(ticker="AAPL", ts=OPEN_TICK, open=105.0, close=105.0)]
            return []

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1, 20])

        assert "aggregate" in result.analysts, (
            f"Expected 'aggregate' pseudo-analyst in result.analysts; got {result.analysts}"
        )

        # Proof of actual scoring, not just appearing in analysts_seen: the
        # directional cell at the primary horizon (defaults to max(horizons)
        # per ScoreboardResult.primary_horizon) must have n >= 1.
        cell = result.cell(analyst="aggregate", horizon=20, subset="directional")
        assert cell.n >= 1, (
            f"Expected the aggregate's directional cell to be scored (n >= 1); got n={cell.n}"
        )

    def test_aggregate_injection_is_backward_compatible_with_empty_ticker_evidence(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """When ``ticker_evidence`` is empty (every existing scoreboard fixture),
        zero synthetic rows are injected and real-analyst scoring is unaffected —
        ``"aggregate"`` must not appear at all.
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

        def _mock_read(ticker, start, end):
            if start == date(2025, 9, 5):
                return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=100.0, close=100.0)]
            return [_make_ohlcbar(ticker=ticker, ts=OPEN_TICK, open=105.0, close=105.0)]

        mock_cache = MagicMock()
        mock_cache.read_ohlcv.side_effect = _mock_read

        result = build(db_path=db_path, cache=mock_cache, horizons=[1])

        assert "aggregate" not in result.analysts
        assert list(result.analysts) == ["technical"]


# ── Tests: stance-vs-aggregate agreement rate (Task 14) ──────────────────────

class TestStanceVsAggregateAgreement:
    """Unit tests for ``stance_vs_aggregate_agreement`` — the pure function
    measuring how often the strategist's per-ticker stance matched the
    deterministic aggregate's lean.
    """

    def _import(self):
        """Lazy import so tests fail clearly if the symbol doesn't exist yet."""
        from backtest.scoreboard import stance_vs_aggregate_agreement
        return stance_vs_aggregate_agreement

    def test_stance_vs_aggregate_agreement_rate(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """4 directional stances (buy/sell), 3 of which agree with the
        aggregate lean on the same ``(tick_id, ticker)`` key, must yield a
        rate of 0.75.  A 5th 'update' stance with a joinable aggregate must
        NOT change the denominator (proving the non-directional exclusion).
        """
        fn = self._import()

        rows: list[PersistenceBase] = [
            # 1. buy vs bullish aggregate → agree.
            _make_stance_row(
                ticker="AAPL", tick_id="t1", recorded_at=OPEN_TICK, lifecycle_action="buy",
            ),
            _make_ticker_evidence_row(
                ticker="AAPL", tick_id="t1", recorded_at=OPEN_TICK, lean="bullish",
            ),
            # 2. sell vs bearish aggregate → agree.
            _make_stance_row(
                ticker="MSFT", tick_id="t2", recorded_at=OPEN_TICK, lifecycle_action="sell",
            ),
            _make_ticker_evidence_row(
                ticker="MSFT", tick_id="t2", recorded_at=OPEN_TICK, lean="bearish",
            ),
            # 3. buy vs bullish aggregate → agree.
            _make_stance_row(
                ticker="GOOG", tick_id="t3", recorded_at=OPEN_TICK, lifecycle_action="buy",
            ),
            _make_ticker_evidence_row(
                ticker="GOOG", tick_id="t3", recorded_at=OPEN_TICK, lean="bullish",
            ),
            # 4. sell vs bullish aggregate → DISAGREE (the one mismatch).
            _make_stance_row(
                ticker="AMZN", tick_id="t4", recorded_at=OPEN_TICK, lifecycle_action="sell",
            ),
            _make_ticker_evidence_row(
                ticker="AMZN", tick_id="t4", recorded_at=OPEN_TICK, lean="bullish",
            ),
            # 5. 'update' stance with a joinable aggregate — non-directional,
            #    must be excluded from BOTH numerator and denominator.
            _make_stance_row(
                ticker="NFLX", tick_id="t5", recorded_at=OPEN_TICK, lifecycle_action="update",
            ),
            _make_ticker_evidence_row(
                ticker="NFLX", tick_id="t5", recorded_at=OPEN_TICK, lean="bearish",
            ),
        ]

        db_path = _build_fixture_db(tmp_path, rows)

        rate = fn(db_path)

        assert abs(rate - 0.75) < 1e-9, (
            f"Expected agreement rate of exactly 0.75 (3 of 4 directional "
            f"stances agree; 'update' excluded); got {rate}"
        )

    def test_stance_vs_aggregate_agreement_raises_when_no_directional_pairs(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        """With zero directional joinable (stance, aggregate) pairs, the
        function must RAISE rather than silently return 0.0 (which would
        masquerade as a real '0 % agreement' result).
        """
        fn = self._import()

        rows: list[PersistenceBase] = [
            # Only an 'update' stance is present — non-directional, so there
            # are no directional pairs to score.
            _make_stance_row(
                ticker="AAPL", tick_id="t1", recorded_at=OPEN_TICK, lifecycle_action="update",
            ),
            _make_ticker_evidence_row(
                ticker="AAPL", tick_id="t1", recorded_at=OPEN_TICK, lean="bullish",
            ),
        ]
        db_path = _build_fixture_db(tmp_path, rows)

        with pytest.raises(ValueError):
            fn(db_path)

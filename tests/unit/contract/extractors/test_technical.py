"""Technical feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from contract.extractors.technical import _KEYS, extract_technical_features
from data.models.company_ratios import CompanyRatios

FIXTURE = Path("tests/fixtures/contract/technical_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    """The returned dict must contain exactly the keys declared in _KEYS."""
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert set(features.keys()) == set(_KEYS)


def test_all_features_are_floats(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r}"


def test_uptrend_fixture_has_positive_5d_change(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert features["pct_change_5d"] > 0


def test_uptrend_fixture_rsi_above_50(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    # Steady uptrend should put RSI in the 50–100 range
    assert features["rsi_14"] > 50.0
    assert features["rsi_14"] <= 100.0


def test_dist_from_52w_high_negative(aapl_data):
    """Latest close (193.5) is below 52w high (200) → negative percent."""
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert features["dist_from_high_52w_pct"] < 0


def test_handles_empty_data_gracefully():
    """Empty data → zeroed features (no exception), except ``vol_ratio_20d`` is NaN.

    Bug #14 changed the ``vol_ratio_20d`` sentinel from 0.0 to NaN so a
    short-/empty-history state is distinguishable from a real low-volume
    reading.  All other locked-catalogue features still default to 0.0.
    """
    import math

    features = extract_technical_features({}, ticker="AAPL")

    for k, v in features.items():
        if k == "vol_ratio_20d":
            assert math.isnan(v), f"{k} expected NaN, got {v!r}"
        else:
            assert v == 0.0, f"{k} expected 0.0, got {v!r}"


def test_handles_short_history_gracefully():
    """Too few price bars to compute RSI(14) → returns 0.0 for indicators that need history."""
    short = {
        "ticker": "AAPL",
        "price_history": [
            {"date": "2026-05-07", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"date": "2026-05-08", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
        ],
    }
    features = extract_technical_features(short, ticker="AAPL")
    # Should not raise. RSI/ATR should be 0.0 (insufficient history).
    assert features["rsi_14"] == 0.0
    assert features["atr_pct_14"] == 0.0


def test_vol_ratio_20d_is_nan_when_history_too_short():
    """Bug #14: short history (<50 bars) must emit NaN for ``vol_ratio_20d``.

    The prior default of 0.0 was a real-looking value that downstream
    consumers compared against the dry-up threshold (0.7) and spuriously
    appended ``vol_dry_up`` to the factor list — see
    docs/backtest-audits/baseline-window-2025-09-iter-2.md Bug #14.
    """
    import math

    # 30 bars — enough for RSI/ATR but well below the 50-bar volume window.
    bars = [
        {
            "timestamp": datetime(2023, 3, d + 1, tzinfo=UTC).isoformat(),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0 + d * 0.1, "volume": 1_000_000,
        }
        for d in range(30)
    ]
    raw = {"ticker": "AAPL", "bars": bars, "ratios": {}}

    features = extract_technical_features(raw, ticker="AAPL")

    # NaN sentinel — distinguishable from a real "volume is 70 % of normal".
    assert math.isnan(features["vol_ratio_20d"]), (
        f"expected NaN sentinel, got {features['vol_ratio_20d']!r}"
    )


def test_vol_ratio_20d_populated_when_enough_history():
    """With ≥50 bars present, ``vol_ratio_20d`` is a real (non-NaN) float."""
    import math

    # 60 bars — comfortably above the 50-bar requirement.
    bars = [
        {
            "timestamp": datetime(2023, 1, 1, tzinfo=UTC).isoformat(),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0 + d * 0.1, "volume": 1_000_000,
        }
        for d in range(60)
    ]
    raw = {"ticker": "AAPL", "bars": bars, "ratios": {}}

    features = extract_technical_features(raw, ticker="AAPL")

    # All-equal volumes → ratio is 1.0; the key point is that it's not NaN.
    assert not math.isnan(features["vol_ratio_20d"])
    assert features["vol_ratio_20d"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Task 2.2 — Fix A: golden/death cross + beta damping from ratios sub-key
# ---------------------------------------------------------------------------

def test_technical_emits_golden_cross_when_50d_above_200d():
    """50-day MA above 200-day MA AND price above 50-day → golden_cross == 1.0."""
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=180.0, fifty_day_average=170.0,
        two_hundred_day_average=150.0, beta=1.2,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    assert features["golden_cross"] == 1.0
    assert features["death_cross"] == 0.0


def test_technical_emits_death_cross_when_50d_below_200d():
    """50-day MA below 200-day MA AND price below 50-day → death_cross == 1.0."""
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=140.0, fifty_day_average=145.0,
        two_hundred_day_average=160.0, beta=1.2,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    assert features["death_cross"] == 1.0
    assert features["golden_cross"] == 0.0


def test_technical_emits_beta_confidence_damping():
    """beta_confidence_damping should be 1/(1+|beta-1|) and non-zero when beta is set."""
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=150.0, beta=1.5,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    # beta=1.5 → |1.5-1| = 0.5 → 1/(1+0.5) = 0.6667
    assert abs(features["beta_confidence_damping"] - (1.0 / 1.5)) < 1e-6


# ---------------------------------------------------------------------------
# Task 2.3 — Fix B: 52-week distance from bars fallback
# ---------------------------------------------------------------------------

def _bar(close: float) -> dict:
    """Construct a minimal OHLCV bar dict for testing."""
    return {
        "timestamp": datetime(2023, 3, 10, tzinfo=UTC).isoformat(),
        "open": close, "high": close, "low": close,
        "close": close, "volume": 1_000_000,
    }


def test_technical_emits_52w_distance_from_bars():
    """52-week high/low computed from bars when ratios fast-path is absent.

    Distances are expressed as signed percentages matching the verdict heuristic
    convention (e.g. -33.33 = 33.33 % below the 52-week high).
    """
    bars = [_bar(100.0) for _ in range(260)]
    # Override one bar in the middle to be the 52-week high.
    bars[100]["close"] = 180.0
    bars[100]["high"]  = 180.0
    # Current price (last bar).
    bars[-1]["close"] = 120.0
    bars[-1]["high"]  = 120.0

    raw = {"ticker": "AAPL", "bars": bars, "ratios": {}}
    features = extract_technical_features(raw, state={})

    # dist_from_high = (last / high52 - 1) × 100  →  (120/180 - 1) × 100 = -33.33…
    expected_high_dist = (120.0 / 180.0 - 1.0) * 100.0
    assert abs(features["dist_from_high_52w_pct"] - expected_high_dist) < 1e-4


def test_technical_52w_ratios_fast_path_takes_priority():
    """When ratios contain fifty_two_week_high, bars-derived value is ignored."""
    bars = [_bar(100.0) for _ in range(30)]
    bars[-1]["close"] = 95.0

    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=95.0, fifty_two_week_high=200.0, fifty_two_week_low=80.0,
    )
    raw = {"ticker": "AAPL", "bars": bars, "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})

    # (95 / 200 - 1) × 100 = -52.5 %
    expected = (95.0 / 200.0 - 1.0) * 100.0
    assert abs(features["dist_from_high_52w_pct"] - expected) < 1e-4


# ---------------------------------------------------------------------------
# Phase 5 Task 5.3 — Fix C: relative_strength_vs_spy/sector features
# ---------------------------------------------------------------------------

from data.models.price_history import PriceHistory  # noqa: E402 — after other imports


def _ph(ticker: str, prices: list[float]) -> PriceHistory:
    """Build a minimal ``PriceHistory`` from a list of closing prices.

    Timestamps are synthetic — one bar per day starting 2023-03-01.
    The ``bars`` attribute holds plain objects (not dicts) matching the
    ``OHLCBar``-like interface that ``_relative_strength`` accesses via ``.close``.
    """
    from data.models.price_history import OHLCBar

    bars = [
        OHLCBar(
            timestamp=datetime(2023, 3, d, tzinfo=UTC),
            open=p, high=p, low=p, close=p, volume=1_000_000,
        )
        for d, p in zip(range(1, len(prices) + 1), prices, strict=False)
    ]
    return PriceHistory(ticker=ticker, bars=bars)


def _make_bars(prices: list[float]) -> list[dict]:
    """Build a list of OHLCV bar dicts from closing prices — used in ``raw["bars"]``."""
    return [
        {
            "timestamp": datetime(2023, 3, d, tzinfo=UTC).isoformat(),
            "open": p, "high": p, "low": p,
            "close": p, "volume": 1_000_000,
        }
        for d, p in zip(range(1, len(prices) + 1), prices, strict=False)
    ]


def test_technical_emits_relative_strength_vs_spy_and_sector():
    """Extractor emits ``relative_strength_vs_spy_5d/20d`` and
    ``relative_strength_vs_sector_5d/20d`` when ``state["reference_prices"]``
    contains the relevant ETF series.

    AAPL rises faster than SPY and XLK over 24 days → both RS values positive.
    """
    # 24 bars: AAPL +24 %, SPY +12 %, XLK +19.2 % over the full window.
    aapl_prices = [100 + d for d in range(1, 25)]       # 101 … 124
    spy_prices  = [100 + d * 0.5 for d in range(1, 25)] # 100.5 … 112
    xlk_prices  = [100 + d * 0.8 for d in range(1, 25)] # 100.8 … 119.2

    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 24), sector="Technology",
    )
    raw = {
        "ticker": "AAPL",
        "bars": _make_bars(aapl_prices),
        "ratios": ratios.model_dump(),
    }
    state = {
        "reference_prices": {
            "SPY": _ph("SPY", spy_prices),
            "XLK": _ph("XLK", xlk_prices),
        },
    }

    features = extract_technical_features(raw, state=state)

    # AAPL outperforms SPY and XLK → both relative-strength values must be > 0.
    assert "relative_strength_vs_spy_20d" in features, (
        "Feature 'relative_strength_vs_spy_20d' missing from extractor output"
    )
    assert features["relative_strength_vs_spy_20d"] > 0, (
        f"Expected RS vs SPY > 0, got {features['relative_strength_vs_spy_20d']}"
    )
    assert "relative_strength_vs_sector_20d" in features, (
        "Feature 'relative_strength_vs_sector_20d' missing from extractor output"
    )
    assert features["relative_strength_vs_sector_20d"] > 0, (
        f"Expected RS vs sector > 0, got {features['relative_strength_vs_sector_20d']}"
    )


def test_technical_relative_strength_absent_when_no_state():
    """When ``state`` is ``None``, no relative-strength keys should appear in output."""
    bars = _make_bars([100 + d for d in range(1, 25)])
    ratios = CompanyRatios(ticker="AAPL", as_of=date(2023, 3, 24), sector="Technology")
    raw = {"ticker": "AAPL", "bars": bars, "ratios": ratios.model_dump()}

    features = extract_technical_features(raw, state=None)

    assert "relative_strength_vs_spy_20d" not in features
    assert "relative_strength_vs_sector_20d" not in features


def test_relative_strength_accepts_datetime_as_of():
    """Passing a ``datetime`` ``as_of`` clamps reference bars to that cutoff.

    Regression cover for the ``_relative_strength`` PIT clamp path: the
    extractor must accept the canonical ``datetime`` shape (the live-run
    value produced by ``resolve_as_of``) without raising.
    """
    aapl_prices = [100.0] * 4 + [100.0, 105.0, 106.0, 107.0, 108.0, 110.0]
    spy_prices  = [100.0] * 4 + [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]

    ratios = CompanyRatios(ticker="AAPL", as_of=date(2023, 3, 10), sector="Technology")
    raw = {
        "ticker": "AAPL",
        "bars": _make_bars(aapl_prices),
        "ratios": ratios.model_dump(),
    }
    state = {
        "reference_prices": {
            "SPY": _ph("SPY", spy_prices),
            "XLK": _ph("XLK", spy_prices),
        },
    }

    # ``as_of`` covers the entire ten-bar synthetic window, so the clamp is a
    # no-op and the RS values still match the unclamped expectation.
    features = extract_technical_features(
        raw, state=state, as_of=datetime(2023, 3, 10, 13, 30, tzinfo=UTC),
    )

    expected_rs_spy_5d = 0.10 - 0.05
    assert "relative_strength_vs_spy_5d" in features
    assert abs(features["relative_strength_vs_spy_5d"] - expected_rs_spy_5d) < 1e-9


def test_relative_strength_rejects_string_as_of():
    """Passing an ISO-string ``as_of`` to the extractor must raise ``TypeError``.

    The driver coerces ``state["as_of"]`` to an ISO string when seeding the
    ADK session (DatabaseSessionService cannot JSON-serialise raw
    ``datetime``).  Agents are responsible for parsing it back via
    ``resolve_as_of`` before invoking the extractor; if a raw string slips
    through, the extractor must fail loudly rather than silently producing a
    ``date <= str`` comparison crash deep inside the lookback list-comprehension.
    """
    aapl_prices = [100.0] * 4 + [100.0, 105.0, 106.0, 107.0, 108.0, 110.0]
    spy_prices  = [100.0] * 4 + [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]

    ratios = CompanyRatios(ticker="AAPL", as_of=date(2023, 3, 10), sector="Technology")
    raw = {
        "ticker": "AAPL",
        "bars": _make_bars(aapl_prices),
        "ratios": ratios.model_dump(),
    }
    state = {
        "reference_prices": {
            "SPY": _ph("SPY", spy_prices),
            "XLK": _ph("XLK", spy_prices),
        },
    }

    with pytest.raises(TypeError, match=r"as_of"):
        extract_technical_features(
            raw, state=state, as_of="2023-03-10T13:30:00+00:00",
        )


def test_technical_relative_strength_5d_values_match_expected():
    """``relative_strength_vs_spy_5d`` is AAPL 5d return minus SPY 5d return."""
    # 10 bars.  5d window uses bars[-6] to bars[-1] (6th-from-last to last).
    # AAPL: +10 % over last 5 bars; SPY: +5 % over last 5 bars → RS = +0.05
    aapl_prices = [100.0] * 4 + [100.0, 105.0, 106.0, 107.0, 108.0, 110.0]
    spy_prices  = [100.0] * 4 + [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]

    ratios = CompanyRatios(ticker="AAPL", as_of=date(2023, 3, 10), sector="Technology")
    raw = {
        "ticker": "AAPL",
        "bars": _make_bars(aapl_prices),
        "ratios": ratios.model_dump(),
    }
    state = {
        "reference_prices": {
            "SPY": _ph("SPY", spy_prices),
            "XLK": _ph("XLK", spy_prices),  # Irrelevant but must be present for sector lookup.
        },
    }

    features = extract_technical_features(raw, state=state)

    # AAPL 5d: (110/100 - 1) = 0.10; SPY 5d: (105/100 - 1) = 0.05 → RS = 0.05.
    expected_rs_spy_5d = 0.10 - 0.05
    assert "relative_strength_vs_spy_5d" in features
    assert abs(features["relative_strength_vs_spy_5d"] - expected_rs_spy_5d) < 1e-9

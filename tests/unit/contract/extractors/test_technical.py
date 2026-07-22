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
    """The returned dict must be a subset of _KEYS, all float.

    Three keys (``vol_ratio_20d``, ``pct_change_20d``, ``beta_confidence_damping``)
    are conditionally absent when data is insufficient.  The AAPL fixture has
    only 23 bars (no ratios), so ``vol_ratio_20d`` (needs ≥50 bars) and
    ``beta_confidence_damping`` (needs beta in ratios) will be absent.
    """
    # Keys conditionally absent when data is insufficient.
    nullable_keys = {
        "vol_ratio_20d",
        "pct_change_20d",
        "beta_confidence_damping",
        "vol_regime_z",
        "trend_state",
        "ma200_state",
        "ma200_flip_days",
    }

    features = extract_technical_features(aapl_data, ticker="AAPL")

    # Every key in the returned dict must belong to _KEYS.
    assert set(features.keys()).issubset(set(_KEYS)), (
        f"Unexpected keys: {set(features.keys()) - set(_KEYS)}"
    )

    # Non-nullable keys must always be present.
    mandatory_keys = set(_KEYS) - nullable_keys
    assert mandatory_keys.issubset(set(features.keys())), (
        f"Mandatory keys missing: {mandatory_keys - set(features.keys())}"
    )


def test_all_features_are_floats(aapl_data):
    """All feature values present in the dict must be plain float (never None or NaN).

    Three keys (``vol_ratio_20d``, ``pct_change_20d``, ``beta_confidence_damping``)
    may be **absent** when data is insufficient, but when present they must be float.
    The AAPL fixture has ≥50 bars and full ratios so all should be present here.
    """
    import math

    features = extract_technical_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r} (expected float, got {type(v).__name__})"
        assert not math.isnan(v), f"{k} must not be NaN — use absent key for 'no data'"


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
    """Empty data → zeroed features (no exception); three nullable keys are absent.

    Bug #20 / #23b / #23c: three keys are OMITTED rather than emitting 0.0 or NaN
    when data is absent, so ``.get()`` returns ``None`` (→ renderer shows "(no data)"):

    - ``vol_ratio_20d``          — absent (needs ≥50 bars)
    - ``pct_change_20d``         — absent (needs ≥21 bars)
    - ``beta_confidence_damping`` — absent (needs beta in ratios)

    All other locked-catalogue features default to 0.0.
    """
    nullable_keys = {"vol_ratio_20d", "pct_change_20d", "beta_confidence_damping"}

    features = extract_technical_features({}, ticker="AAPL")

    # Nullable keys must be absent, not 0.0 or NaN.
    for k in nullable_keys:
        assert k not in features, (
            f"{k} should be absent (not computable), but found value {features.get(k)!r}"
        )

    # All present keys must be 0.0 (the safe zero default).
    for k, v in features.items():
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


def test_vol_ratio_20d_absent_when_history_too_short():
    """Bug #20: short history (<50 bars) must NOT emit ``vol_ratio_20d`` at all.

    The prior sentinel of ``float('nan')`` was not handled by the strategist
    prompt renderer and produced the nonsense token "nanx" on ~98 % of rows
    during cache warm-up.  Omitting the key (so ``.get()`` returns ``None``) is
    the correct "not computable" signal — the renderer maps ``None`` → "(no data)".

    Regression cover also for Bug #14 (the earlier fix that changed 0.0 → NaN):
    the original 0.0 default was a real-looking value that compared less than
    the dry-up threshold (0.7) and spuriously appended ``vol_dry_up`` to the
    factor list.
    """
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

    # Key must be absent — not 0.0 and not NaN.
    assert "vol_ratio_20d" not in features, (
        f"vol_ratio_20d should be absent for 30 bars, but got {features.get('vol_ratio_20d')!r}"
    )


def test_vol_ratio_20d_populated_when_enough_history():
    """With ≥50 bars present, ``vol_ratio_20d`` is a real float key in the dict."""
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

    # All-equal volumes → ratio is 1.0; key must be present and correct.
    assert "vol_ratio_20d" in features, (
        "vol_ratio_20d should be present with 60 bars"
    )
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


def test_technical_emits_beta_confidence_damping(monkeypatch):
    """beta_confidence_damping is 1/(1+|beta-1|) when beta is set AND the gate is ON.

    Phase-14: the feature is gated by ``technical.beta_confidence_damping_enabled``
    (default OFF).  Force the gate ON here to exercise the formula.
    """
    import contract.extractors.technical as tech_mod
    monkeypatch.setattr(tech_mod, "_beta_damping_enabled", lambda: True)

    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=150.0, beta=1.5,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    # beta=1.5 → |1.5-1| = 0.5 → 1/(1+0.5) = 0.6667
    assert abs(features["beta_confidence_damping"] - (1.0 / 1.5)) < 1e-6


def test_beta_confidence_damping_gate_off_omits_feature(monkeypatch):
    """With the gate OFF (the default), the feature is absent even when beta is set."""
    import contract.extractors.technical as tech_mod
    monkeypatch.setattr(tech_mod, "_beta_damping_enabled", lambda: False)

    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=150.0, beta=1.5,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})

    # Gate OFF → key omitted entirely (not 0.0), so the strategist digest skips it.
    assert "beta_confidence_damping" not in features


def test_beta_confidence_damping_gate_default_is_off():
    """The shipped config default for the gate is OFF (Phase-14 decision)."""
    import os

    from agents.analysts.heuristics import load_heuristics

    # Read the real config (no override) — the committed default must be False.
    os.environ.pop("ANALYST_HEURISTICS_PATH", None)
    load_heuristics.cache_clear()
    h = load_heuristics().technical
    assert h.beta_confidence_damping_enabled is False


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


# ---------------------------------------------------------------------------
# A-016 / A-049 — Deterministic extractor must NOT fabricate AnalystReport
# ---------------------------------------------------------------------------

def test_deterministic_verdict_no_longer_fabricates_report() -> None:
    """A-016 / A-049 regression: technical extractor must leave
    report=None and let rationale carry the one-liner.  Previously the
    extractor synthesised an AnalystReport to satisfy the old
    report-required validator — that path is gone (the validator now
    enforces exactly one prose surface instead).

    Plan 3c Task 4 rewrite: the verdict is now the config-weighted trend /
    52w-anchor / relative-strength composite, so the heuristics fixture uses
    the ``trend_weight`` / ``anchor_52w_weight`` / ``rel_strength_weight`` /
    ``composite_neutral_band`` / ``horizon_days`` surface (the reversal knobs
    from Task 3 were retired in Task 2/4).
    """
    # All keys from _KEYS plus vol_ratio_20d (which is NaN when history is short,
    # but here we supply a real value so a directional verdict fires), plus the
    # composite's trend/anchor/relative-strength inputs.
    features = {
        "rsi_14": 55.0, "pct_change_20d": 0.04, "pct_change_5d": 0.05,
        "vol_ratio_20d": 1.1, "atr_pct_14": 1.5,
        "dist_from_high_52w_pct": -5.0, "dist_from_low_52w_pct": 25.0,
        "golden_cross": 0.0, "death_cross": 0.0,
        "beta_confidence_damping": 1.0, "last_close": 100.0,
        "trend_state": 0.06, "ma200_state": 1.0,
        "relative_strength_vs_spy_20d": 0.02,
    }

    # NOTE: TechnicalHeuristics lives at agents.analysts.heuristics,
    # NOT agents.heuristics.technical as the plan spec assumed.
    from agents.analysts.heuristics import TechnicalHeuristics
    from contract.extractors.technical import derive_technical_verdict

    h = TechnicalHeuristics(
        trend_weight=0.50, anchor_52w_weight=0.25, rel_strength_weight=0.25,
        composite_neutral_band=0.10, horizon_days=60,
        vol_regime_window=60, vol_regime_extreme_z=1.5,
        vol_ratio_breakout=1.5, vol_ratio_dry_up=0.7,
        near_52w_extreme_pct=5.0, magnitude_cap=1.0,
        beta_confidence_damping_enabled=False,
    )

    v = derive_technical_verdict(features, h)

    assert v.is_no_data is False
    assert v.report is None, "deterministic extractor must not fabricate report"
    assert v.rationale != "", "rationale carries the deterministic one-liner"


def test_no_data_branch_uses_canonical_builder() -> None:
    """The no-price-data fingerprint branch produces is_no_data=True with the
    canonical shape (report=None, non-empty rationale).

    The fingerprint keys are:
    - ``rsi_14 == 0``
    - ``pct_change_20d`` absent (Bug #23c: absent key = "not computable")
    - ``atr_pct_14 == 0``

    Plan 3c Task 4 rewrite: the no-data fingerprint logic is unchanged, only
    the heuristics fixture fields have moved to the new composite surface.
    """
    # Omit pct_change_20d to trigger the no-data fingerprint.
    features: dict = {k: 0.0 for k in (
        "rsi_14", "pct_change_5d",
        "atr_pct_14", "dist_from_high_52w_pct", "dist_from_low_52w_pct",
        "golden_cross", "death_cross", "last_close",
    )}
    # vol_ratio_20d and beta_confidence_damping are also absent (no bars / no ratios).
    # pct_change_20d intentionally omitted — the no-data fingerprint checks for absence.

    from agents.analysts.heuristics import TechnicalHeuristics
    from contract.extractors.technical import derive_technical_verdict

    h = TechnicalHeuristics(
        trend_weight=0.50, anchor_52w_weight=0.25, rel_strength_weight=0.25,
        composite_neutral_band=0.10, horizon_days=60,
        vol_regime_window=60, vol_regime_extreme_z=1.5,
        vol_ratio_breakout=1.5, vol_ratio_dry_up=0.7,
        near_52w_extreme_pct=5.0, magnitude_cap=1.0,
        beta_confidence_damping_enabled=False,
    )

    v = derive_technical_verdict(features, h)

    assert v.is_no_data is True
    assert v.report is None
    assert v.rationale  # non-empty


# --- Phase 3b three-reads additions: vol_regime_z (Read 2) + trend_state (Read 3) ---

import numpy as np  # noqa: E402 — after other imports


def _ramp_bars(n: int, start: float = 100.0, step: float = 0.5) -> list[dict]:
    """Build ``n`` synthetic OHLCV bars on a gentle linear ramp (oldest first)."""
    bars = []
    for i in range(n):
        close = start + i * step
        bars.append(
            {
                "timestamp": f"2025-01-{(i % 28) + 1:02d}",
                "open": close - 0.2,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1_000_000 + i,
            }
        )
    return bars


def test_trend_state_from_ma200():
    """trend_state = last_price / two_hundred_day_average - 1 when ratios carry ma200."""
    raw = {
        "bars": _ramp_bars(30),
        "ratios": {"last_price": 110.0, "two_hundred_day_average": 100.0},
    }
    feats = extract_technical_features(raw, "TEST")
    assert feats["trend_state"] == float(110.0 / 100.0 - 1.0)


def test_trend_state_absent_without_ma200():
    """No ma200 → trend_state key is omitted (nullable convention), not 0.0."""
    raw = {"bars": _ramp_bars(30), "ratios": {"last_price": 110.0}}
    feats = extract_technical_features(raw, "TEST")
    assert "trend_state" not in feats


def test_vol_regime_z_emitted_with_enough_history():
    """A long-enough bar series yields a finite vol_regime_z z-score."""
    raw = {"bars": _ramp_bars(120)}
    feats = extract_technical_features(raw, "TEST")
    assert "vol_regime_z" in feats
    assert np.isfinite(feats["vol_regime_z"])


def test_vol_regime_z_absent_when_history_too_short():
    """Fewer valid ATR% samples than the window → vol_regime_z omitted."""
    raw = {"bars": _ramp_bars(20)}
    feats = extract_technical_features(raw, "TEST")
    assert "vol_regime_z" not in feats


# --- Plan 3c Task 3: ma200_state + ma200_flip_days anchor -------------------


def test_ma200_state_positive_on_uptrend():
    """A price above its rolling 200d SMA yields ma200_state = +1.0."""
    raw = {"bars": _ramp_bars(260, start=50.0, step=0.5)}   # steady climb → last > MA200
    feats = extract_technical_features(raw, "TEST")
    assert feats["ma200_state"] == 1.0
    assert "ma200_flip_days" in feats
    assert feats["ma200_flip_days"] >= 0.0


def test_ma200_state_absent_without_enough_history():
    """Fewer than 200 bars → both MA200 anchor keys are omitted (nullable convention)."""
    raw = {"bars": _ramp_bars(120)}
    feats = extract_technical_features(raw, "TEST")
    assert "ma200_state" not in feats
    assert "ma200_flip_days" not in feats


def test_ma200_flip_days_counts_sessions_since_the_last_cross():
    """A series that dips below then recovers above MA200 reports a small flip age."""
    # 220 rising bars, then a sharp late dip that pushes the last close under MA200,
    # then a 3-session recovery back above — flip age should be the recovery length.
    # NOTE: a 220-bar linear ramp means the 200-bar SMA lags well behind the
    # current price (roughly half the ramp's slope-span behind), so the dip
    # must clear that lag margin (~-60) rather than a smaller offset (~-40)
    # to actually cross below the rolling MA200.
    bars = _ramp_bars(220, start=50.0, step=0.5)
    dip = [dict(b, close=b["close"] - 60.0, high=b["high"] - 60.0, low=b["low"] - 60.0)
           for b in _ramp_bars(6, start=155.0, step=0.0)]
    recover = [dict(b, close=200.0, high=200.5, low=199.5) for b in _ramp_bars(3)]
    feats = extract_technical_features({"bars": bars + dip + recover}, "TEST")
    assert feats["ma200_state"] == 1.0
    assert 0.0 <= feats["ma200_flip_days"] <= 5.0

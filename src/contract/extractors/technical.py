"""Technical analyst deterministic feature extractor and verdict derivation.

Two public entry points:

- ``extract_technical_features`` — converts raw OHLCV history into the locked
  feature catalogue (``_KEYS``).  Forgiving: missing keys default to 0.0.

- ``derive_technical_verdict`` — maps the feature catalogue to an
  ``AnalystVerdict`` using the Phase-5 heuristic rules.  Pure function; safe
  for table-driven unit tests (no I/O, no globals).

Input for the extractor: the dict that lives under
``state["temp:technical_data"][ticker]``.  Accepted shapes:

- Phase 7 (canonical): ``{"bars": [...], "ratios": dict}``
- Phase 5 legacy: ``{"price_history": {"bars": [...]}}`` or ``{"price_history": [...]}``
- Very old legacy: ``{"history": [...]}``

The extractor accepts ``state`` as a keyword argument (Phase 7) or ``ticker``
as a positional argument (legacy Phase 5); both are optional so existing call
sites continue to work unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from math import copysign
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import talib  # canonical TA-Lib bindings — pandas-ta was rejected in Plan A § Task A5

from contract.extractors._sector_map import SECTOR_TO_ETF

# TYPE_CHECKING guard prevents a circular import at module load time:
# contract.extractors.technical ← agents.analysts.heuristics ←
#   agents.analysts.__init__ ← technical.agent ← contract.extractors.technical.
# Both imports are done lazily inside derive_technical_verdict at runtime,
# by which point the module graph is fully initialised.
if TYPE_CHECKING:
    from agents.analysts.heuristics import TechnicalHeuristics
    from contract.evidence import AnalystVerdict

# The complete, locked set of feature keys this extractor always returns.
_KEYS = (
    "rsi_14",
    "pct_change_5d",
    "pct_change_20d",
    "vol_ratio_20d",
    "atr_pct_14",
    "dist_from_high_52w_pct",
    "dist_from_low_52w_pct",
    # Phase 7 additions: moving-average crossover + beta-aware confidence.
    "golden_cross",
    "death_cross",
    "beta_confidence_damping",
)


def _pct_change(prices: list[float], window: int) -> float | None:
    """Compute percentage change over ``window`` bars from the end of ``prices``.

    Returns ``None`` when there are not enough bars to form a complete window
    (i.e. fewer than ``window + 1`` prices).

    Parameters
    ----------
    prices:
        Ordered list of closing prices (oldest first).
    window:
        Number of bars for the lookback window.

    Returns
    -------
    float | None
        ``(prices[-1] / prices[-window-1]) - 1`` or ``None`` on insufficient data.
    """
    if len(prices) <= window:
        return None
    start = prices[-(window + 1)]
    end   = prices[-1]
    if not start:
        return None
    return (end - start) / start


def _relative_strength(
    own_bars: list[Any],
    ref_ph: Any,
    *,
    window: int,
    as_of: date | datetime | None = None,
) -> float | None:
    """Own-ticker percentage change minus reference-series percentage change.

    Positive values indicate the ticker outperformed the reference series over
    the given window; negative values indicate underperformance.

    Parameters
    ----------
    own_bars:
        List of bar dicts for the target ticker — each must have a ``"close"``
        key (Phase 7 canonical shape).
    ref_ph:
        A ``PriceHistory``-like object with a ``bars`` attribute; each bar
        must expose a ``.close`` attribute.  ``None`` or empty bars → returns
        ``None``.
    window:
        Lookback window in bars (e.g. 5 or 20).
    as_of:
        Optional PIT cutoff.  When provided, reference bars with a
        ``timestamp`` strictly after ``as_of`` are dropped before computing
        the lookback window — this prevents a backtest tick from peeking at
        future reference moves when ``state["reference_prices"]`` was seeded
        for the entire window.  ``None`` disables clamping (live behaviour).

    Returns
    -------
    float | None
        Relative-strength value, or ``None`` when data is insufficient.
    """
    if ref_ph is None or not getattr(ref_ph, "bars", None):
        return None

    own_closes = [b["close"] for b in own_bars if b.get("close") is not None]

    # PIT clamp: drop reference bars dated after ``as_of`` so the lookback
    # cannot leak post-as_of data into the percentage change.  Bars whose
    # ``timestamp`` is a ``date`` or ``datetime`` are both handled by the
    # ``_bar_date`` helper.
    #
    # The driver ISO-stringifies ``state["as_of"]`` before persisting it
    # through ADK's DatabaseSessionService (see backtest/driver.py:494–499).
    # Agents are responsible for parsing it back via ``resolve_as_of`` before
    # invoking this extractor — if a raw ``str`` reaches here it means a
    # caller skipped that step, and the comparison would otherwise crash
    # deep in the list comprehension with ``date <= str``.  Raise loudly
    # so the missing coercion is obvious in the traceback.
    ref_bars = ref_ph.bars
    if as_of is not None:
        if isinstance(as_of, datetime):
            cutoff = as_of.date()
        elif isinstance(as_of, date):
            cutoff = as_of
        else:
            raise TypeError(
                f"_relative_strength expected as_of to be date|datetime|None; "
                f"got {type(as_of).__name__!r}.  If this came from "
                f"state['as_of'], coerce it with data.timeguard.resolve_as_of "
                f"at the agent boundary first."
            )
        ref_bars = [b for b in ref_bars if _bar_date(b) <= cutoff]

    ref_closes = [b.close for b in ref_bars]

    own_chg = _pct_change(own_closes, window)
    ref_chg = _pct_change(ref_closes, window)

    if own_chg is None or ref_chg is None:
        return None

    return own_chg - ref_chg


def _bar_date(bar: Any) -> date:
    """Return the calendar date of an ``OHLCBar``-shaped object.

    Handles both ``datetime`` and ``date`` ``timestamp`` attributes so the
    PIT clamp in ``_relative_strength`` works against whichever shape the
    upstream provider emits.
    """
    ts = bar.timestamp
    return ts.date() if isinstance(ts, datetime) else ts


def _zero_features() -> dict[str, float]:
    """Return a zeroed feature dict — the safe fallback for any missing-data path."""
    return {k: 0.0 for k in _KEYS}


def _df_from_history(history: list[Mapping[str, Any]]) -> pd.DataFrame | None:
    """Convert a list of OHLCV bar dicts into a float DataFrame.

    Parameters
    ----------
    history:
        List of bar dicts, each expected to have keys
        ``open``, ``high``, ``low``, ``close``, ``volume``.

    Returns
    -------
    pd.DataFrame | None
        A numeric DataFrame restricted to the five OHLCV columns,
        or ``None`` if the input is empty or missing required columns.
    """
    if not history:
        return None

    df = pd.DataFrame(history)
    needed = {"open", "high", "low", "close", "volume"}

    if not needed.issubset(df.columns):
        return None

    df = df[list(needed)].astype(float)
    return df


def _emit_ratios_features(raw: dict) -> dict[str, float]:
    """Read ``raw['ratios']`` (already stowed by the fetch callback) and emit
    moving-average crossover + beta-aware features.

    Parameters
    ----------
    raw:
        The full per-ticker raw dict. Reads the ``"ratios"`` sub-dict.

    Returns
    -------
    dict[str, float]
        Any of: ``golden_cross``, ``death_cross``, ``beta_confidence_damping``.
        Empty dict when ratios are absent.
    """
    ratios = raw.get("ratios") or {}
    if not ratios:
        return {}

    last  = ratios.get("last_price")
    ma50  = ratios.get("fifty_day_average")
    ma200 = ratios.get("two_hundred_day_average")
    beta  = ratios.get("beta")

    out: dict[str, float] = {}

    if last is not None and ma50 is not None and ma200 is not None:
        # Golden cross: 50-day above 200-day AND price above 50-day MA.
        out["golden_cross"] = 1.0 if ma50 > ma200 and last > ma50 else 0.0
        # Death cross: 50-day below 200-day AND price below 50-day MA.
        out["death_cross"]  = 1.0 if ma50 < ma200 and last < ma50 else 0.0

    if beta is not None:
        # Damping factor applied to confidence in the verdict layer; surfaced
        # as a feature so the strategist can audit it.
        # Value is 1.0 for beta==1, falling off symmetrically for betas above/below 1.
        out["beta_confidence_damping"] = 1.0 / (1.0 + abs(beta - 1.0))

    return out


def _resolve_bars(raw: Mapping[str, Any]) -> list:
    """Resolve the OHLCV bar list from any of the supported raw dict shapes.

    Checks three locations in priority order:
    1. ``raw["bars"]`` — Phase 7 canonical.
    2. ``raw["price_history"]["bars"]`` — Phase 5 nested dict.
    3. ``raw["price_history"]`` or ``raw["history"]`` — legacy flat list.

    Parameters
    ----------
    raw:
        Per-ticker raw data dict.

    Returns
    -------
    list
        The bar list (may be empty).
    """
    # Phase 7 canonical: bars directly on the raw dict.
    if "bars" in raw:
        return raw.get("bars") or []

    # Phase 5: bars inside a price_history sub-dict.
    ph_payload = raw.get("price_history")
    if isinstance(ph_payload, dict):
        return ph_payload.get("bars") or []

    # Legacy flat list.
    return ph_payload or raw.get("history") or []


def extract_technical_features(
    raw: Mapping[str, Any],
    ticker: str = "",
    *,
    as_of: datetime | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Compute the locked technical feature catalogue from raw OHLCV history.

    Accepts either the Phase 7 canonical shape ``{"bars": [...], "ratios": dict}``
    or the Phase 5 legacy shape ``{"price_history": {"bars": [...]}}``.

    All features are returned as plain Python ``float`` values.

    Parameters
    ----------
    raw:
        Raw ticker data dict.  An empty dict returns all-zero features
        without raising.
    ticker:
        Ticker symbol — accepted for logging/tracing purposes, not used in
        computation.  Defaults to ``""`` so callers can pass ``state=`` as the
        only keyword argument.
    as_of:
        Tick clock for PIT clamping.  Forwarded to ``_relative_strength`` so
        the reference price series is truncated to bars at or before the
        tick — eliminates the backtest leak where a window-spanning
        ``state["reference_prices"]`` dict otherwise let day-1 ticks
        compute relative strength against end-of-window SPY moves.
    state:
        Phase 7 pipeline state dict — currently unused but accepted so callers
        can pass it without error (Fix C / relative-strength will wire it in
        Phase 5).

    Returns
    -------
    dict[str, float]
        Exactly the keys in ``_KEYS``, all ``float``.
        Missing or insufficient data yields ``0.0`` for the affected indicator.
    """
    out = _zero_features()

    if not raw:
        return out

    bars = _resolve_bars(raw)
    df   = _df_from_history(bars)

    if df is None or len(df) < 2:
        # No usable bar data — still run ratios-derived features.
        out.update(_emit_ratios_features(raw))
        return out

    close = df["close"]

    # --- Percentage change windows ---
    # Require at least n+1 rows so iloc[-1] and iloc[-(n+1)] are distinct bars.
    if len(close) > 5:
        out["pct_change_5d"] = float((close.iloc[-1] / close.iloc[-6]) - 1.0)

    if len(close) > 20:
        out["pct_change_20d"] = float((close.iloc[-1] / close.iloc[-21]) - 1.0)

    # --- RSI(14) ---
    # TA-Lib needs at least 15 bars (14 periods + 1 seed bar).
    # The leading 14 values in the output array are NaN — we take the last.
    if len(close) >= 15:
        rsi_arr = talib.RSI(close.to_numpy(dtype=float), timeperiod=14)
        last_rsi = rsi_arr[-1] if rsi_arr is not None and len(rsi_arr) > 0 else np.nan
        if not np.isnan(last_rsi):
            out["rsi_14"] = float(last_rsi)

    # --- ATR(14) as a percentage of last close ---
    # ATR needs high, low, close arrays and at least 15 bars.
    if len(df) >= 15:
        atr_arr = talib.ATR(
            df["high"].to_numpy(dtype=float),
            df["low"].to_numpy(dtype=float),
            df["close"].to_numpy(dtype=float),
            timeperiod=14,
        )
        last_atr = atr_arr[-1] if atr_arr is not None and len(atr_arr) > 0 else np.nan

        if not np.isnan(last_atr):
            last_close = float(close.iloc[-1])
            if last_close > 0:
                out["atr_pct_14"] = float(last_atr / last_close * 100.0)

    # --- Volume ratio: recent 20-bar average vs prior 50-bar average ---
    # Requires at least 50 bars; returns 0.0 (not 1.0) when insufficient data
    # to make the "no data" state obvious.
    if len(df) >= 50:
        vol = df["volume"]
        v20 = float(vol.iloc[-20:].mean())
        v50 = float(vol.iloc[-50:].mean())

        if v50 > 0:
            out["vol_ratio_20d"] = v20 / v50

    # --- 52-week distance (Fix B) ---
    # Priority order:
    # 1. ratios["fifty_two_week_high/low"] — populated by stats/yfinance provider.
    # 2. top-level raw["high_52w"] / raw["low_52w"] — legacy fixture shape.
    # 3. Computed from the last 252 bars — final fallback.
    ratios_dict = raw.get("ratios") or {}
    high52 = ratios_dict.get("fifty_two_week_high") or raw.get("high_52w")
    low52  = ratios_dict.get("fifty_two_week_low")  or raw.get("low_52w")

    if (high52 is None or low52 is None) and bars:
        closes = [b["close"] for b in bars[-252:] if b.get("close") is not None]
        if closes:
            if high52 is None:
                high52 = max(closes)
            if low52 is None:
                low52 = min(closes)

    last_close = float(close.iloc[-1])

    # Distances expressed as signed percentages (e.g. -3.25 = 3.25 % below high).
    # This convention matches the verdict heuristic which compares against
    # ``near_52w_extreme_pct`` (config default: 5.0).
    if last_close > 0 and high52 and high52 > 0:
        out["dist_from_high_52w_pct"] = float((last_close / high52 - 1.0) * 100.0)

    if last_close > 0 and low52 and low52 > 0:
        out["dist_from_low_52w_pct"] = float((last_close / low52 - 1.0) * 100.0)

    # --- Ratios-based features (Fix A): crossovers + beta damping ---
    out.update(_emit_ratios_features(raw))

    # --- Fix C: relative-strength vs SPY + sector ETF (Phase 5) ---
    # Only computed when ``state["reference_prices"]`` is populated (i.e. on a
    # live tick or a fully-wired backtest replay).  Emitted as *extra* keys
    # beyond ``_KEYS`` — the caller must not assume they are always present.
    ref_prices: dict[str, Any] = (state or {}).get("reference_prices") or {}

    # Reference prices arrive as PriceHistory instances on the smoke-test path
    # (in-memory session) and as JSON-dumped dicts on the persisted path (ADK
    # SqlSessionService).  Coerce dicts back so `_relative_strength`'s
    # attribute-access contract holds regardless of the upstream source.
    if ref_prices:
        from data.models import PriceHistory

        ref_prices = {
            sym: PriceHistory.model_validate(ph) if isinstance(ph, dict) else ph
            for sym, ph in ref_prices.items()
        }

        spy_ph = ref_prices.get("SPY")

        # Relative strength versus the broad market (SPY).
        # ``as_of`` (when provided by the technical agent) clamps the
        # reference series to bars at or before the tick — without this,
        # a backtest tick with a window-spanning ``reference_prices`` dict
        # would compute relative strength against post-as_of SPY bars.
        for w in (5, 20):
            rs_spy = _relative_strength(bars, spy_ph, window=w, as_of=as_of)
            if rs_spy is not None:
                out[f"relative_strength_vs_spy_{w}d"] = rs_spy

        # Relative strength versus the ticker's own SPDR sector ETF.
        ratios_dict_rs = raw.get("ratios") or {}
        sector          = ratios_dict_rs.get("sector") if isinstance(ratios_dict_rs, dict) else None
        sector_etf      = SECTOR_TO_ETF.get(sector) if sector else None
        sector_ph       = ref_prices.get(sector_etf) if sector_etf else None

        for w in (5, 20):
            rs_sec = _relative_strength(bars, sector_ph, window=w, as_of=as_of)
            if rs_sec is not None:
                out[f"relative_strength_vs_sector_{w}d"] = rs_sec

    return out


def derive_technical_verdict(
    features: dict[str, float],
    h: TechnicalHeuristics,
) -> AnalystVerdict:
    """Map the technical feature vector to an ``AnalystVerdict`` via Phase-5 heuristics.

    Pure function — no I/O, no globals.  Safe for table-driven unit tests.

    Lean logic (in order of precedence):
    1. Base lean = sign of ``pct_change_20d``.
    2. RSI exhaustion / capitulation flips override the trend lean.

    Confidence modifiers (additive, clamped to ``[0, 1]``):
    - ``+h.confidence_boost_step`` when 5d and 20d momentum agree (same sign).
    - ``+h.confidence_boost_step`` when within ``h.near_52w_extreme_pct`` of
      either the 52-week high *or* low.
    - ``-h.confidence_penalty_step`` when ``atr_pct_14 > h.atr_high_volatility_pct``.

    Note on 52-week distance keys:
    - ``dist_from_high_52w_pct`` is **negative** (e.g. -3.0 = 3 % below high).
      "Near" is tested as ``abs(value) <= h.near_52w_extreme_pct``.
    - ``dist_from_low_52w_pct`` is **positive** (e.g. 5.0 = 5 % above low).
      "Near" is tested as ``value <= h.near_52w_extreme_pct``.

    Parameters
    ----------
    features:
        Output of ``extract_technical_features`` — all keys from ``_KEYS``
        present as ``float``.
    h:
        Validated ``TechnicalHeuristics`` config section.

    Returns
    -------
    AnalystVerdict
        Fully populated verdict including ``lean``, ``magnitude``,
        ``confidence``, ``rationale``, ``key_factors``, and ``is_no_data``.
    """
    # Deferred runtime imports — avoids the circular import that arises when
    # loading this module triggers agents.analysts.__init__ (which re-imports
    # this module before it has finished initialising).
    from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver  # noqa: PLC0415

    # --- No-data fingerprint --------------------------------------------------
    # The extractor emits all-zero features when price history is missing.
    # Detect this state via the three core indicators that would otherwise be
    # non-zero for any real ticker.
    if (
        features["rsi_14"] == 0
        and features["pct_change_20d"] == 0
        and features["atr_pct_14"] == 0
    ):
        return AnalystVerdict(
            lean="neutral",
            magnitude=0.0,
            confidence=0.0,
            rationale="no price data",
            key_factors=[],
            is_no_data=True,
        )

    factors: list[str] = []

    # --- Base lean from 20-day momentum ---------------------------------------
    pct20 = features["pct_change_20d"]
    pct5  = features["pct_change_5d"]

    sign20 = copysign(1.0, pct20) if pct20 != 0 else 0.0
    sign5  = copysign(1.0, pct5)  if pct5  != 0 else 0.0

    if sign20 > 0:
        lean = "bullish"
        factors.append("trend_up_20d")
    elif sign20 < 0:
        lean = "bearish"
        factors.append("trend_down_20d")
    else:
        lean = "neutral"

    # --- 5d / 20d momentum agreement -----------------------------------------
    if sign5 == sign20 and sign20 != 0:
        factors.append("momentum_agree")
    elif sign5 != 0 and sign20 != 0:
        # Both have data but point in opposite directions.
        factors.append("momentum_disagree")

    # --- RSI overbought / oversold flips -------------------------------------
    rsi = features["rsi_14"]

    if rsi > h.rsi_overbought:
        factors.append("rsi_overbought")
        # Exhaustion: strong recent rally at extreme RSI suggests reversal.
        if pct5 > 0:
            lean = "bearish"

    if rsi < h.rsi_oversold:
        factors.append("rsi_oversold")
        # Capitulation: sharp recent sell-off at extreme RSI suggests bounce.
        if pct5 < 0:
            lean = "bullish"

    # --- Volume context -------------------------------------------------------
    vol_ratio = features["vol_ratio_20d"]

    if vol_ratio > h.vol_ratio_breakout:
        factors.append("vol_breakout")
    elif vol_ratio < h.vol_ratio_dry_up:
        factors.append("vol_dry_up")

    # --- 52-week proximity ---------------------------------------------------
    # dist_from_high_52w_pct is negative — negate to get a positive "distance".
    dist_high = features.get("dist_from_high_52w_pct", -100.0)
    dist_low  = features.get("dist_from_low_52w_pct",   100.0)

    if abs(dist_high) <= h.near_52w_extreme_pct:
        factors.append("near_52w_high")

    if dist_low <= h.near_52w_extreme_pct:
        factors.append("near_52w_low")

    # --- High volatility flag ------------------------------------------------
    if features["atr_pct_14"] > h.atr_high_volatility_pct:
        factors.append("high_volatility")

    # --- Magnitude -----------------------------------------------------------
    # Base: scale the 20d momentum, then apply volume adjustments.
    magnitude = min(abs(pct20) * h.pct_change_momentum_scale, h.magnitude_cap)

    if "vol_breakout" in factors:
        magnitude = min(magnitude + 0.15, h.magnitude_cap)

    if "vol_dry_up" in factors:
        magnitude = max(magnitude - 0.10, 0.0)

    # --- Confidence ----------------------------------------------------------
    confidence = h.confidence_base

    if "momentum_agree" in factors:
        confidence += h.confidence_boost_step

    # Either 52w extreme proximity boosts conviction.
    if "near_52w_high" in factors or "near_52w_low" in factors:
        confidence += h.confidence_boost_step

    if "high_volatility" in factors:
        confidence -= h.confidence_penalty_step

    confidence = max(0.0, min(1.0, confidence))

    # --- Rationale -----------------------------------------------------------
    rationale = (", ".join(factors) or "neutral")[:160]

    # --- Synthetic AnalystReport -----------------------------------------------
    # Structured analysts have no LLM prose, but the schema requires ``report``
    # whenever ``is_no_data=False``.  Build a minimal report from the
    # deterministic signals so the uniform contract holds.
    direction_map    = {"bullish": "bull", "bearish": "bear", "neutral": "neutral"}
    driver_direction = direction_map[lean]

    # Evenly distribute weight across factors; guard against empty list.
    driver_factors = factors if factors else [lean]
    n_factors      = len(driver_factors)
    even_weight    = round(1.0 / n_factors, 4)

    # Build one ReportDriver per key_factor, referencing the observed RSI/pct values.
    drivers = [
        ReportDriver(
            name=factor[:69],
            direction=driver_direction,
            weight=even_weight,
            body=(
                f"Technical signal: {factor} "
                f"(rsi={rsi:.1f}, pct20={pct20:.3f})"
            )[:575],
        )
        for factor in driver_factors
    ]

    # AnalystReport requires at least 2 drivers; pad when only one factor fired
    # (e.g. lean is neutral with no momentum or RSI extremes).
    if len(drivers) < 2:
        drivers.append(
            ReportDriver(
                name="overall_lean",
                direction=driver_direction,
                weight=even_weight,
                body=(
                    f"20d change {pct20:.3f}, RSI {rsi:.1f}, "
                    f"vol_ratio {vol_ratio:.2f}"
                )[:575],
            )
        )

    summary = (
        f"Technical analysis leans {lean}: {rationale}."
    )[:1150]

    report = AnalystReport(summary=summary, drivers=drivers[:4])

    return AnalystVerdict(
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=factors,
        is_no_data=False,
        report=report,
    )

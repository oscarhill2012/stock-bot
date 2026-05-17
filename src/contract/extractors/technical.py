"""Technical analyst deterministic feature extractor and verdict derivation.

Two public entry points:

- ``extract_technical_features`` — converts raw OHLCV history into the locked
  feature catalogue (``_KEYS``).  Forgiving: missing keys default to 0.0.

- ``derive_technical_verdict`` — maps the feature catalogue to an
  ``AnalystVerdict`` using the Phase-5 heuristic rules.  Pure function; safe
  for table-driven unit tests (no I/O, no globals).

Input for the extractor: the dict that lives under
``state["technical_data"][ticker]``.  Accepted shapes:

- Phase 7 (canonical): ``{"bars": [...], "ratios": dict}``
- Phase 5 legacy: ``{"price_history": {"bars": [...]}}`` or ``{"price_history": [...]}``
- Very old legacy: ``{"history": [...]}``

The extractor accepts ``state`` as a keyword argument (Phase 7) or ``ticker``
as a positional argument (legacy Phase 5); both are optional so existing call
sites continue to work unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from math import copysign
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import talib  # canonical TA-Lib bindings — pandas-ta was rejected in Plan A § Task A5

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
        Legacy historical clock parameter — reserved, currently unused.
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
    from contract.evidence import AnalystVerdict  # noqa: PLC0415

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

    return AnalystVerdict(
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=factors,
        is_no_data=False,
    )

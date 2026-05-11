"""Technical analyst deterministic feature extractor.

Input: the dict that lives under ``state["technical_data"][ticker]`` — typically
a dump of the project's ``StockStats`` model. The function is forgiving about
field shape: missing keys default to no-data (0.0 features) rather than raising.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
import talib  # canonical TA-Lib bindings — pandas-ta was rejected in Plan A § Task A5

# The complete, locked set of feature keys this extractor always returns.
_KEYS = (
    "rsi_14",
    "pct_change_5d",
    "pct_change_20d",
    "vol_ratio_20d",
    "atr_pct_14",
    "dist_from_high_52w_pct",
    "dist_from_low_52w_pct",
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


def extract_technical_features(raw: Mapping[str, Any], ticker: str) -> dict[str, float]:
    """Compute the locked technical feature catalogue from raw OHLCV history.

    Accepts the per-ticker data slice from ``state["technical_data"][ticker]``.
    All features are returned as plain Python ``float`` values.

    Parameters
    ----------
    raw:
        Raw ticker data dict. Expected to contain a ``price_history`` key
        (list of OHLCV bar dicts) plus optional ``high_52w`` / ``low_52w`` floats.
        An empty dict returns all-zero features without raising.
    ticker:
        Ticker symbol — accepted for logging/tracing purposes, not used in
        computation currently.

    Returns
    -------
    dict[str, float]
        Exactly the keys in ``_KEYS``, all ``float``.
        Missing or insufficient data yields ``0.0`` for the affected indicator.
    """
    out = _zero_features()

    if not raw:
        return out

    # Support both 'price_history' (canonical) and 'history' (legacy fallback).
    history = raw.get("price_history") or raw.get("history") or []
    df = _df_from_history(history)

    if df is None or len(df) < 2:
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

    # --- Distance from 52-week high / low (in percent) ---
    # Negative dist_from_high means the stock is trading below its annual peak.
    high_52w = raw.get("high_52w")
    low_52w = raw.get("low_52w")
    last_close = float(close.iloc[-1])

    if high_52w and high_52w > 0:
        out["dist_from_high_52w_pct"] = float((last_close / high_52w - 1.0) * 100.0)

    if low_52w and low_52w > 0:
        out["dist_from_low_52w_pct"] = float((last_close / low_52w - 1.0) * 100.0)

    return out

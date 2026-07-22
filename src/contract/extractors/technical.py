"""Technical analyst deterministic feature extractor and verdict derivation.

Two public entry points:

- ``extract_technical_features`` — converts raw OHLCV history into the locked
  feature catalogue (``_KEYS``).  Forgiving: most keys default to 0.0, but
  three indicators emit **no key at all** when not computable (insufficient
  bars, missing input) to avoid misleading values reaching downstream:

  - ``vol_ratio_20d``           — absent until ≥50 bars present.
  - ``pct_change_20d``          — absent until ≥21 bars present.
  - ``beta_confidence_damping`` — absent when beta is missing from ratios.

  Callers should use ``features.get("vol_ratio_20d")`` etc. and treat the
  ``None`` return as "(no data)".  ``0.0`` always means a genuine zero reading
  for these keys.

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

Sentinel convention
-------------------
- key absent / ``.get()`` → ``None`` — indicator not computable.  The three
  nullable features above use this convention.  Downstream renderers should
  treat ``None`` as "(no data)".
- ``0.0``     — genuine zero reading (truly flat price, truly flat volume, etc.)
- ``float('nan')`` — NOT emitted by this module.  Any NaN that reaches the
                     verdict layer is a bug, not an intended sentinel.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
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
# ``None`` may appear as the value for any key whose indicator cannot be
# computed (insufficient bars, missing input) — callers must handle ``None``.
# ``beta_confidence_damping`` is only emitted when ``beta`` is available in
# the ratios sub-dict; it is absent (not in _KEYS) when beta is unknown, to
# avoid the misleading 0.0 default that read as "extreme deviation from 1".
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
    # Most recent close — surfaced as a feature so the strategist's per-ticker
    # block can render the live price in its section header (Bug: strategist
    # was blind to current price, causing hallucinated-gains close decisions).
    # ``0.0`` is the no-data sentinel (matches the other feature defaults).
    "last_close",
    # Phase 3b three-reads additions (both nullable — omitted when not computable):
    "vol_regime_z",   # z-score of ATR% vs its own trailing window (Read 2)
    "trend_state",    # last_price / 200d MA - 1 (Read 3)
    # Plan 3c composite-anchor additions (nullable — need >=200 bars):
    "ma200_state",       # +1.0 above / -1.0 below the 200-day SMA (trend vote corroborator)
    "ma200_flip_days",   # sessions since the last ma200_state crossing (P2 anchor)
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
    """Return the baseline feature dict used as the starting point for every extraction.

    Only keys whose ``0.0`` default is a *safe* value are included.  Three
    features are intentionally **excluded** from this baseline dict — they are
    only inserted when data is sufficient to compute a meaningful value:

    - ``vol_ratio_20d``          — omitted until ≥50 bars present (Bug #20).
                                   The old NaN sentinel was not handled by the
                                   renderer, producing the token "nanx" on ~98 %
                                   of rows during cache warm-up.
    - ``pct_change_20d``         — omitted until ≥21 bars present (Bug #23c).
                                   The old 0.0 default read as "+0.0% 20d
                                   momentum" on the first (all-buys) tick.
    - ``beta_confidence_damping`` — omitted when beta is absent from ratios
                                   (Bug #23b).  The old 0.0 default read as
                                   "infinite deviation from 1" — never true.

    Callers use ``features.get("vol_ratio_20d")`` (→ ``None`` if absent) and
    treat ``None`` as "(no data)".  ``0.0`` is always a genuine zero reading.
    """
    # Nullable keys excluded; they are inserted conditionally downstream.
    _NULLABLE = {
        "vol_ratio_20d",
        "pct_change_20d",
        "beta_confidence_damping",
        "vol_regime_z",
        "trend_state",
        "ma200_state",
        "ma200_flip_days",
    }
    return {k: 0.0 for k in _KEYS if k not in _NULLABLE}


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

    # Read 3 — trend state: continuous distance of price from the 200-day MA.
    # A signed fraction (0.05 = 5 % above the 200-day MA).  Persistent regime
    # context; surfaced as a number, not blended into the technical lean.
    if last is not None and ma200 is not None and ma200 > 0:
        out["trend_state"] = last / ma200 - 1.0

    # The beta-aware confidence-damping feature is gated INDEPENDENTLY of beta
    # presence by ``technical.beta_confidence_damping_enabled``.  This decouples
    # two concerns that used to be conflated: now that ``pit_composite``
    # populates a PIT-correct beta for the Fundamental analyst, the damping
    # feature would auto-activate and silently alter the strategist's technical
    # digest.  The offline validation (Phase-14 beta commit) found the feature
    # does not move the technical *verdict* (``derive_technical_verdict`` never
    # consumes it — it is a strategist-facing context line only), so the gate
    # ships DISABLED by default; flip it in ``config/analyst_heuristics.json``
    # if a future strategist tuning wants the signal back.
    if beta is not None and _beta_damping_enabled():
        # Bug #23b: only emit this key when beta is actually known.  When beta
        # is absent the feature stays at the _zero_features default of None,
        # which the renderer maps to "(no data)".  The old 0.0 default was a
        # dead computation — it read as "beta is infinitely far from 1" which
        # is never true for real equities; the strategist silently received 0.0
        # on every row where beta was missing from the ratios sub-dict.
        #
        # Formula: 1.0 for beta == 1 (unit market sensitivity, full confidence);
        # decreases symmetrically as beta deviates from 1 in either direction.
        out["beta_confidence_damping"] = 1.0 / (1.0 + abs(beta - 1.0))

    return out


def _beta_damping_enabled() -> bool:
    """Whether the ``beta_confidence_damping`` feature should be emitted.

    Reads the ``technical.beta_confidence_damping_enabled`` flag from the
    validated heuristics config.  Imported lazily (mirroring
    ``derive_technical_verdict``) to dodge the import cycle between this module
    and ``agents.analysts.heuristics``.  Defaults to disabled if the config
    cannot be loaded — fail safe, since the feature is non-essential context.

    Returns
    -------
    bool
        ``True`` when the feature is enabled in config, else ``False``.
    """
    try:
        from agents.analysts.heuristics import load_heuristics  # noqa: PLC0415

        return load_heuristics().technical.beta_confidence_damping_enabled
    except Exception:  # noqa: BLE001 — config load is best-effort; degrade to OFF
        return False


def _vol_regime_window() -> int:
    """Trailing-window length for the volatility-regime z-score.

    Reads ``technical.vol_regime_window`` from the validated heuristics config.
    Imported lazily (mirroring ``_beta_damping_enabled``) to dodge the import
    cycle between this module and ``agents.analysts.heuristics``.  Falls back to
    60 if the config cannot be loaded — a safe default that simply widens the
    minimum history needed before the z-score is emitted.

    Returns
    -------
    int
        The configured window length, or 60 on any config-load failure.
    """
    try:
        from agents.analysts.heuristics import load_heuristics  # noqa: PLC0415

        return load_heuristics().technical.vol_regime_window
    except Exception:  # noqa: BLE001 — config load is best-effort; degrade to 60
        return 60


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
        All keys from ``_KEYS`` that are computable, all ``float``.  Three keys
        are **conditionally absent** (not just ``0.0``) when data is insufficient:

        - ``vol_ratio_20d``          — absent until ≥50 bars present (Bug #20).
        - ``pct_change_20d``         — absent until ≥21 bars present (Bug #23c).
        - ``beta_confidence_damping`` — absent when beta missing from ratios (Bug #23b).

        Use ``.get("vol_ratio_20d")`` (returns ``None``) and treat ``None`` as
        "(no data)".  ``0.0`` is always a genuine zero reading for these keys.
    """
    out = _zero_features()
    # ``vol_ratio_20d``, ``pct_change_20d``, and ``beta_confidence_damping``
    # are NOT in the baseline ``out`` dict (see _zero_features docstring).
    # They are inserted as genuine floats only when bar/data count is sufficient.

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
    # Require at least n+1 rows so iloc[-1] and iloc[-(n+1)] are distinct bars
    # (i.e. >= n+1, equivalently > n).  When the bar count is insufficient the
    # feature stays at its _zero_features default:
    #   pct_change_5d  → 0.0  (rarely an issue; 5-bar window fills quickly)
    #   pct_change_20d → None (Bug #23c: the explicit "not computable" sentinel,
    #                          NOT 0.0 which reads as "+0.0% 20d momentum")
    if len(close) > 5:
        out["pct_change_5d"] = float((close.iloc[-1] / close.iloc[-6]) - 1.0)

    # Bug #23c: boundary is >= 21 (equivalently > 20).  On the first tick of
    # a fresh backtest window the cache may hold exactly 20 bars (US Labor Day
    # 2025-09-01 shrinks the available history by one session), which with the
    # old implicit 0.0 default caused "+0.0% 20d momentum" on every name.
    # The None sentinel propagates to the renderer as "(no data)" instead.
    if len(close) >= 21:
        out["pct_change_20d"] = float((close.iloc[-1] / close.iloc[-21]) - 1.0)

    # --- 200-day MA state + flip anchor (Plan 3c) ---------------------------
    # A boolean above/below series over a rolling 200-bar simple MA; the anchor
    # is the number of sessions since that above/below state last changed.  Both
    # keys stay absent below 200 bars (nullable convention → renderer skips).
    if len(close) >= 200:
        sma200 = close.rolling(window=200).mean()

        # Elementwise sign of (close - MA200) over the valid (non-NaN) tail.
        # +1 at/above the MA, -1 below.  numpy where keeps it branch-free.
        valid_mask = sma200.notna().to_numpy()
        above = np.where(
            close.to_numpy(dtype=float)[valid_mask]
            >= sma200.to_numpy(dtype=float)[valid_mask],
            1.0, -1.0,
        )

        if len(above) > 0:
            out["ma200_state"] = float(above[-1])

            # Walk back from the last session while the state is unchanged; the
            # count of unchanged prior sessions is the flip age (0 on the flip
            # session itself).
            flip_age = 0
            for prev in reversed(above[:-1]):
                if prev == above[-1]:
                    flip_age += 1
                else:
                    break
            out["ma200_flip_days"] = float(flip_age)

    # --- RSI(14) ---
    # TA-Lib needs at least 15 bars (14 periods + 1 seed bar).
    # The leading 14 values in the output array are NaN — we take the last.
    if len(close) >= 15:
        rsi_arr = talib.RSI(close.to_numpy(dtype=float), timeperiod=14)
        last_rsi = rsi_arr[-1] if rsi_arr is not None and len(rsi_arr) > 0 else np.nan
        if not np.isnan(last_rsi):
            out["rsi_14"] = float(last_rsi)

    # --- ATR(14) as a percentage of last close (+ Read 2 volatility regime) --
    # ATR needs high, low, close arrays and at least 15 bars.  We compute the
    # ATR% as a full elementwise series (not just the last bar) so the
    # volatility-regime z-score can measure the latest reading against the
    # ticker's OWN recent ATR% history (self-relative — Moreira-Muir 2017).
    if len(df) >= 15:
        high_arr  = df["high"].to_numpy(dtype=float)
        low_arr   = df["low"].to_numpy(dtype=float)
        close_arr = df["close"].to_numpy(dtype=float)

        atr_arr = talib.ATR(high_arr, low_arr, close_arr, timeperiod=14)

        # ATR as a percentage of the contemporaneous close, elementwise.  Guard
        # divide-by-zero (a genuinely zero close) by mapping it to NaN, which is
        # then excluded from both the "last reading" and the z-score window.
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct_arr = np.where(close_arr > 0, atr_arr / close_arr * 100.0, np.nan)

        last_atr_pct = atr_pct_arr[-1] if len(atr_pct_arr) > 0 else np.nan

        if not np.isnan(last_atr_pct):
            out["atr_pct_14"] = float(last_atr_pct)

            # Read 2 — volatility regime: z-score of the latest ATR% against its
            # own trailing window.  Emitted only when enough VALID (non-NaN)
            # samples exist and the window has non-zero dispersion; otherwise the
            # key stays absent (nullable convention → renderer skips it).
            window = _vol_regime_window()
            valid  = atr_pct_arr[~np.isnan(atr_pct_arr)]

            if len(valid) >= window:
                recent = valid[-window:]
                mu     = float(recent.mean())
                sigma  = float(recent.std())

                if sigma > 0:
                    out["vol_regime_z"] = float((last_atr_pct - mu) / sigma)

    # --- Volume ratio: recent 20-bar average vs prior 50-bar average ---
    # Bug #20: requires at least 50 bars.  The feature starts as ``None`` (from
    # _zero_features) and is only replaced with a real float when enough history
    # is present.  ``None`` propagates to the renderer as "(no data)"; the old
    # ``float('nan')`` sentinel was NOT handled by the renderer and produced the
    # nonsense token "nanx" on ~98 % of rows during cache warm-up.
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

    # Surface the live close so downstream renderers (section header, thesis
    # book) can show "where the ticker is trading right now" without going back
    # to the raw bars dict.  Stored under the same feature key advertised in
    # ``_KEYS`` above.
    out["last_close"] = last_close

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
    """Map the technical feature vector to an ``AnalystVerdict`` — three reads.

    Pure function — no I/O, no globals.  Safe for table-driven unit tests.

    The verdict's lean/magnitude/confidence reflect ONLY the short-term
    reversal read (Read 1).  The volatility-regime (Read 2) and trend-state
    (Read 3) reads, plus the volume / 52-week / crossover context, are surfaced
    as ``key_factors`` tags and rendered feature numbers — they are NEVER
    blended into the lean.

    Read 1 — short-term reversal (Jegadeesh 1990; Lehmann 1990): lean CONTRARIAN
    to ``pct_change_5d`` (a recent up-move fades bearish; a recent down-move
    bounces bullish), with a neutral band.  This deliberately inverts the old
    trend-following sign.

    Parameters
    ----------
    features:
        Output of ``extract_technical_features``.
    h:
        Validated ``TechnicalHeuristics`` config section.

    Returns
    -------
    AnalystVerdict
        Lean/magnitude/confidence from the reversal read, ``horizon_days`` from
        ``h.reversal_horizon_days``, and context tags in ``key_factors``.
    """
    # Deferred runtime imports — avoids the circular import that arises when
    # loading this module triggers agents.analysts.__init__.
    from contract.evidence import AnalystVerdict  # noqa: PLC0415

    # --- No-data fingerprint (unchanged) -------------------------------------
    # rsi_14 and atr_pct_14 default to 0.0 (need >=15 bars); pct_change_20d is
    # absent (None) below 21 bars.  All three absent/zero = no usable history.
    pct20_raw = features.get("pct_change_20d")

    if (
        features["rsi_14"] == 0
        and pct20_raw is None
        and features["atr_pct_14"] == 0
    ):
        from contract.evidence import _no_data_analyst_verdict  # noqa: PLC0415

        return _no_data_analyst_verdict(reason="no price data")

    factors: list[str] = []

    # === READ 1 — short-term reversal (the ONE directional lean) =============
    # Contrarian on the 5-day return: lean AGAINST the recent short-term move.
    pct5 = features["pct_change_5d"]
    band = h.reversal_neutral_band_pct

    if pct5 >= band:
        # Recent up-move → fade DOWN.  (Boundary inclusive: pct5 == band is
        # already a directional call, with confidence == reversal_confidence_base.)
        lean = "bearish"
        factors.append("reversal_up_fade")
    elif pct5 <= -band:
        # Recent down-move → fade UP.
        lean = "bullish"
        factors.append("reversal_down_bounce")
    else:
        lean = "neutral"
        factors.append("reversal_neutral")

    # Magnitude scales with the size of the move being faded, capped.
    magnitude = min(abs(pct5) * h.reversal_magnitude_scale, h.magnitude_cap)

    # Confidence ramps from the base (at the band edge) to 1.0 (at 2x the band).
    # A neutral read carries no directional confidence (and no magnitude).
    if lean == "neutral":
        confidence = 0.0
        magnitude = 0.0
    else:
        excess     = min(max((abs(pct5) - band) / band, 0.0), 1.0) if band > 0 else 1.0
        confidence = h.reversal_confidence_base + (1.0 - h.reversal_confidence_base) * excess
        confidence = max(0.0, min(1.0, confidence))

    # === READ 2 — volatility regime (risk tag; NOT blended into the lean) ====
    # Fires on the absolute z, so either tail trips it: a stressed (high
    # positive z) regime OR an unusually calm (large negative z) one — hence
    # the tag is named "extreme", not "elevated".
    vol_z = features.get("vol_regime_z")
    if vol_z is not None and abs(vol_z) >= h.vol_regime_extreme_z:
        factors.append("vol_regime_extreme")

    # === READ 3 — trend state (regime tag; NOT blended into the lean) ========
    trend = features.get("trend_state")
    if trend is not None:
        factors.append("trend_above_ma200" if trend >= 0 else "trend_below_ma200")

    # --- Corroborating context tags (do NOT alter the reversal lean) ---------
    vol_ratio = features.get("vol_ratio_20d")
    if vol_ratio is not None and not (isinstance(vol_ratio, float) and math.isnan(vol_ratio)):
        if vol_ratio > h.vol_ratio_breakout:
            factors.append("vol_breakout")
        elif vol_ratio < h.vol_ratio_dry_up:
            factors.append("vol_dry_up")

    dist_high = features.get("dist_from_high_52w_pct", -100.0)
    dist_low  = features.get("dist_from_low_52w_pct",   100.0)

    if abs(dist_high) <= h.near_52w_extreme_pct:
        factors.append("near_52w_high")

    if dist_low <= h.near_52w_extreme_pct:
        factors.append("near_52w_low")

    if features.get("golden_cross", 0.0) >= 1.0:
        factors.append("golden_cross")

    if features.get("death_cross", 0.0) >= 1.0:
        factors.append("death_cross")

    # --- Rationale (A-016): compact ", "-joined factor list, capped 160 chars -
    rationale = (", ".join(factors) or "neutral")[:160]

    return AnalystVerdict(
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=factors,
        is_no_data=False,
        horizon_days=h.reversal_horizon_days,
    )

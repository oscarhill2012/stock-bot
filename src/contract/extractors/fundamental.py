"""Fundamental analyst deterministic feature extractor."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The locked catalogue of fundamental features this extractor emits.
# Any change here must be coordinated with the analyst contract schema.
_KEYS = (
    "pe_trailing", "pe_forward", "peg",
    "revenue_growth_yoy", "profit_margin", "debt_to_equity",
    "fcf_yield_pct", "roe", "analyst_rating_avg",
)


def _zero_features() -> dict[str, float]:
    """Return a dict with every feature key set to 0.0.

    Used as the safe default when raw data is absent or unparseable.
    """
    return {k: 0.0 for k in _KEYS}


def _f(value: Any) -> float:
    """Coerce *value* to float, returning 0.0 on None / non-numeric / NaN.

    Parameters
    ----------
    value:
        Any raw value from the incoming fundamentals dict.

    Returns
    -------
    float
        A clean, finite float — never NaN or None.
    """
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN check — NaN is the only value not equal to itself
        return 0.0
    return f


def extract_fundamental_features(raw: Mapping[str, Any], ticker: str) -> dict[str, float]:
    """Pull the locked fundamental feature catalogue from a raw fundamentals dict.

    Accepts two common field-naming conventions for each metric (e.g. both
    ``trailing_pe`` and ``pe_trailing``) so the function is tolerant of
    different upstream data-provider schemas.

    Parameters
    ----------
    raw:
        Raw key/value mapping of fundamental data for a single ticker.
    ticker:
        Ticker symbol — reserved for future logging / error context.

    Returns
    -------
    dict[str, float]
        Exactly the keys in ``_KEYS``, all floats.  Missing or unparseable
        fields default to 0.0.
    """
    out = _zero_features()
    if not raw:
        return out

    out["pe_trailing"]        = _f(raw.get("trailing_pe") or raw.get("pe_trailing"))
    out["pe_forward"]         = _f(raw.get("forward_pe") or raw.get("pe_forward"))
    out["peg"]                = _f(raw.get("peg"))
    out["revenue_growth_yoy"] = _f(raw.get("revenue_growth_yoy") or raw.get("revenue_growth"))
    out["profit_margin"]      = _f(raw.get("profit_margin"))
    out["debt_to_equity"]     = _f(raw.get("debt_to_equity"))
    out["roe"]                = _f(raw.get("return_on_equity") or raw.get("roe"))
    out["analyst_rating_avg"] = _f(raw.get("analyst_rating_avg"))

    # FCF yield = (free cash flow / market cap) × 100.
    # Guard against zero market cap to avoid ZeroDivisionError.
    fcf = _f(raw.get("free_cash_flow") or raw.get("fcf"))
    mcap = _f(raw.get("market_cap"))
    if mcap > 0:
        out["fcf_yield_pct"] = fcf / mcap * 100.0

    return out

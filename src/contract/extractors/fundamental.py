"""Fundamental analyst deterministic feature extractor.

Phase 5 extends the extractor to consume the triad payload shape (post-split):

.. code-block:: python

    raw = {
        "ratios":  dict | None,          # scalar company fundamentals (P/E, beta, …)
        "filings": list[dict],            # serialised Filing objects
        "insider": Form4Bundle | None,   # typed Form 4 bundle
    }

The ``"ratios"`` key replaces the old ``"stats"`` key from before the Phase 5
data-model split. Field names *inside* the ratios dict are unchanged
(``trailing_pe``, ``market_cap``, etc.) so downstream digest/strategist logic
is unaffected.

The function returns a ``dict[str, float]`` with exactly the keys in ``_KEYS``
(all floats, never NaN, never missing).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from data.models import Form4Bundle

# ---------------------------------------------------------------------------
# Locked feature catalogue
# ---------------------------------------------------------------------------
# Any change to _KEYS must be coordinated with the analyst contract schema.
_KEYS = (
    # Company fundamentals extracted from the "ratios" sub-dict.
    "pe_trailing",
    "pe_forward",
    "peg",
    "revenue_growth_yoy",
    "profit_margin",
    "debt_to_equity",
    "fcf_yield_pct",
    "roe",
    "analyst_rating_avg",
    # Filings-derived numerics.
    "days_since_last_filing",
    "n_filings_30d",
    # Insider trade columns (Form 4 common-stock table).
    "insider_net_dollars_30d",
    "insider_n_buys_30d",
    "insider_n_sells_30d",
    "insider_cluster_buy_flag",
    "insider_cluster_sell_flag",
    "insider_planned_sale_ratio",
    "insider_max_filer_role_rank",
    # Insider derivative columns (Form 4 derivatives table).
    "insider_derivative_exercise_count",
    "insider_derivative_grant_count",
)

# ---------------------------------------------------------------------------
# Officer role → numeric rank mapping.
# Higher rank = more informative signal (a CEO buy carries more weight than
# a Director buy).  Titles are matched case-insensitively via upper-case
# normalisation in _role_rank().
# ---------------------------------------------------------------------------
_ROLE_RANK: dict[str, int] = {
    "CEO": 5,
    "CFO": 4,
    "PRESIDENT": 4,
    "SVP": 3,
    "VP": 2,
    "DIRECTOR": 1,
}

# Number of distinct officer-level buyers/sellers required to trigger the
# cluster flag.
_CLUSTER_THRESHOLD = 3

# How many days back the window extends for "30d" metrics.
_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
    # NaN is the only value not equal to itself.
    if f != f:
        return 0.0
    return f


def _role_rank(title: str | None) -> int:
    """Map an ``insider_title`` string to a numeric rank in ``_ROLE_RANK``.

    Matching is performed on the uppercased title so that capitalisation
    differences from different data providers don't affect the result.  An
    unrecognised title, or ``None``, returns 0.

    Parameters
    ----------
    title:
        Raw insider title string (may be ``None``).

    Returns
    -------
    int
        The rank from ``_ROLE_RANK``, or 0 for unknown titles.
    """
    if title is None:
        return 0
    upper = title.upper()
    # Check each known key as a substring so "EVP & CFO" still maps to CFO.
    for keyword, rank in _ROLE_RANK.items():
        if keyword in upper:
            return rank
    return 0


def _extract_stats_features(stats: Mapping[str, Any] | None) -> dict[str, float]:
    """Pull financial ratio features from the ``ratios`` sub-dict.

    Parameters
    ----------
    stats:
        The ``raw["ratios"]`` sub-dict (may be ``None`` or empty).
        Named ``stats`` internally for historical reasons; the dict schema is
        unchanged — only the key used to retrieve it from ``raw`` changed.
        (Phase 5 data-model split renamed ``"stats"`` → ``"ratios"`` at the
        fetch-callback level; this helper receives the sub-dict directly.)

    Returns
    -------
    dict[str, float]
        Partial feature dict covering the stats columns only.
    """
    out: dict[str, float] = {}
    if not stats:
        return out

    out["pe_trailing"]        = _f(stats.get("trailing_pe") or stats.get("pe_trailing"))
    out["pe_forward"]         = _f(stats.get("forward_pe") or stats.get("pe_forward"))
    out["peg"]                = _f(stats.get("peg"))
    out["revenue_growth_yoy"] = _f(stats.get("revenue_growth_yoy") or stats.get("revenue_growth"))
    out["profit_margin"]      = _f(stats.get("profit_margin"))
    out["debt_to_equity"]     = _f(stats.get("debt_to_equity"))
    out["roe"]                = _f(stats.get("return_on_equity") or stats.get("roe"))
    out["analyst_rating_avg"] = _f(stats.get("analyst_rating_avg"))

    # FCF yield = (free cash flow / market cap) × 100.
    # Guard against zero market cap to avoid ZeroDivisionError.
    fcf  = _f(stats.get("free_cash_flow") or stats.get("fcf"))
    mcap = _f(stats.get("market_cap"))
    if mcap > 0:
        out["fcf_yield_pct"] = fcf / mcap * 100.0

    return out


def _extract_filings_features(
    filings: list[dict],
    now: datetime,
) -> dict[str, float]:
    """Derive filing-timing features from the serialised filings list.

    Parameters
    ----------
    filings:
        List of ``Filing.model_dump()`` dicts.
    now:
        Current UTC datetime used to compute staleness.

    Returns
    -------
    dict[str, float]
        ``days_since_last_filing`` and ``n_filings_30d``.
    """
    if not filings:
        return {"days_since_last_filing": 9999.0, "n_filings_30d": 0.0}

    cutoff = now.timestamp() - _WINDOW_DAYS * 86400

    filed_timestamps: list[float] = []
    within_30d = 0

    for f in filings:
        raw_filed = f.get("filed_at")
        if raw_filed is None:
            continue

        # Accept datetime objects or ISO strings.
        if isinstance(raw_filed, datetime):
            ts = raw_filed.timestamp()
        else:
            try:
                ts = datetime.fromisoformat(str(raw_filed)).timestamp()
            except (ValueError, TypeError):
                continue

        filed_timestamps.append(ts)
        if ts >= cutoff:
            within_30d += 1

    if not filed_timestamps:
        return {"days_since_last_filing": 9999.0, "n_filings_30d": 0.0}

    most_recent_ts = max(filed_timestamps)
    days_since = (now.timestamp() - most_recent_ts) / 86400.0

    return {
        "days_since_last_filing": max(0.0, days_since),
        "n_filings_30d": float(within_30d),
    }


def _extract_insider_features(
    bundle: Form4Bundle | None,
    now: datetime,
) -> dict[str, float]:
    """Compute all insider-trade feature columns from a ``Form4Bundle``.

    Only trades within the 30-day window (relative to *now*) contribute to
    count and value metrics.

    Parameters
    ----------
    bundle:
        The ``Form4Bundle`` from the fundamental data payload.  ``None`` or an
        empty bundle yields all-zero columns.
    now:
        Current UTC datetime used as the window anchor.

    Returns
    -------
    dict[str, float]
        All insider feature columns — never missing, never NaN.
    """
    zero = {
        "insider_net_dollars_30d": 0.0,
        "insider_n_buys_30d": 0.0,
        "insider_n_sells_30d": 0.0,
        "insider_cluster_buy_flag": 0.0,
        "insider_cluster_sell_flag": 0.0,
        "insider_planned_sale_ratio": 0.0,
        "insider_max_filer_role_rank": 0.0,
        "insider_derivative_exercise_count": 0.0,
        "insider_derivative_grant_count": 0.0,
    }

    if bundle is None:
        return zero

    cutoff_ts = now.timestamp() - _WINDOW_DAYS * 86400

    # Filter common-stock trades to the 30-day window.
    window_trades = [
        t for t in bundle.trades
        if t.filed_at.timestamp() >= cutoff_ts
    ]

    buys  = [t for t in window_trades if t.side == "buy"]
    sells = [t for t in window_trades if t.side == "sell"]

    # --- dollar values ---
    # Use start=0.0 to keep the type as float even when the list is empty.
    buy_value  = sum((_f(t.shares) * _f(t.price_per_share) for t in buys), 0.0)
    sell_value = sum((_f(t.shares) * _f(t.price_per_share) for t in sells), 0.0)

    # --- cluster flags ---
    # Count distinct filer names (one person making multiple transactions
    # counts as one).
    buy_officers  = {t.insider_name for t in buys}
    sell_officers = {t.insider_name for t in sells}
    cluster_buy  = 1.0 if len(buy_officers)  >= _CLUSTER_THRESHOLD else 0.0
    cluster_sell = 1.0 if len(sell_officers) >= _CLUSTER_THRESHOLD else 0.0

    # --- planned sale ratio ---
    n_sells_total = len(sells)
    if n_sells_total > 0:
        planned_ratio = sum(1 for t in sells if t.is_10b5_1) / n_sells_total
    else:
        planned_ratio = 0.0

    # --- max role rank ---
    all_window_titles = [t.insider_title for t in window_trades]
    max_rank = max((_role_rank(title) for title in all_window_titles), default=0)

    # --- derivative counts (all time, not window-filtered — these are
    #     point-in-time disclosures rather than trailing-window metrics) ---
    exercise_count = sum(
        1 for d in bundle.derivatives if d.transaction_code == "M"
    )
    grant_count = sum(
        1 for d in bundle.derivatives if d.transaction_code == "A"
    )

    return {
        "insider_net_dollars_30d": buy_value - sell_value,
        "insider_n_buys_30d": float(len(buys)),
        "insider_n_sells_30d": float(len(sells)),
        "insider_cluster_buy_flag": cluster_buy,
        "insider_cluster_sell_flag": cluster_sell,
        "insider_planned_sale_ratio": planned_ratio,
        "insider_max_filer_role_rank": float(max_rank),
        "insider_derivative_exercise_count": float(exercise_count),
        "insider_derivative_grant_count": float(grant_count),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_fundamental_features(
    raw: Mapping[str, Any],
    ticker: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, float]:
    """Pull the locked fundamental feature catalogue from a raw payload dict.

    Accepts the Phase 5 triad payload shape (post data-model split)::

        {
            "ratios":  dict | None,
            "filings": list[dict],
            "insider": Form4Bundle | None,
        }

    The ``"ratios"`` key replaces the old ``"stats"`` key. Field names inside
    the dict (``trailing_pe``, ``market_cap``, etc.) are unchanged.

    All ratios field aliases from different data providers are normalised
    (e.g. both ``trailing_pe`` and ``pe_trailing`` are accepted).

    Parameters
    ----------
    raw:
        Phase 5 triad payload for a single ticker.
    ticker:
        Ticker symbol — used for future logging / error context.

    Returns
    -------
    dict[str, float]
        Exactly the keys in ``_KEYS``, all floats.  Missing or unparseable
        fields default to 0.0.
    """
    out = _zero_features()

    if not raw:
        return out

    # Use the caller-supplied historical clock so backtest replays produce the
    # same time-delta features as the original run.  Live callers that omit
    # ``as_of`` fall back to wall-clock now — identical behaviour to before.
    now = as_of if as_of is not None else datetime.now(tz=UTC)

    # --- ratios (Phase 5: renamed from "stats" key at the fetch-callback level) ---
    stats_sub = raw.get("ratios") or {}
    out.update(_extract_stats_features(stats_sub))

    # --- filings ---
    filings_sub = raw.get("filings") or []
    out.update(_extract_filings_features(filings_sub, now))

    # --- insider ---
    insider_sub = raw.get("insider")
    # Accept either a Form4Bundle instance or None.
    if isinstance(insider_sub, Form4Bundle):
        out.update(_extract_insider_features(insider_sub, now))
    else:
        out.update(_extract_insider_features(None, now))

    return out

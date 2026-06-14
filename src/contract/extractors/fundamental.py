"""Fundamental analyst deterministic feature extractor.

Phase 5 extends the extractor to consume the triad payload shape (post-split).

Phase 7 (providers-and-silent-gaps-v1) migrated all producers to the flat-list
shape.  Plan 13 (A-054) retired the legacy ``Form4Bundle`` path entirely.

The sole accepted payload shape is now:

.. code-block:: python

    raw = {
        "ratios":                  dict | None,
        "filings":                 list[dict],
        "insider_trades":          list[dict],   # InsiderTrade.model_dump() rows
        "insider_derivative_trades": list[dict], # InsiderDerivativeTrade.model_dump() rows
    }

A missing ``insider_trades`` key raises ``KeyError`` rather than silently
degrading — every producer must emit the Phase 7 flat-list shape.

The ``"ratios"`` key replaces the old ``"stats"`` key from before the Phase 5
data-model split.  Field names *inside* the ratios dict are unchanged so
downstream digest/strategist logic is unaffected.

The function returns a ``dict[str, float]`` with exactly the keys in ``_KEYS``
(all floats, never NaN, never missing).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Locked feature catalogue
# ---------------------------------------------------------------------------
# Any change to _KEYS must be coordinated with the analyst contract schema.
_KEYS = (
    # Company fundamentals extracted from the "ratios" sub-dict.
    #
    # NB: forward-looking and broker-consensus fields — ``pe_forward``,
    # ``peg``, ``analyst_rating_avg``, ``number_of_analyst_opinions`` — were
    # retired from this catalogue (audit: full-backtest-iter-1).  Their sole
    # source is yfinance ``.info``, a wall-clock value that leaks future
    # information into PIT backtests, so the ``pit_composite`` provider always
    # surfaces them as ``None``.  Emitting them coerced-to-0.0 told the
    # strategist "forward P/E = 0" / "analyst rating = 0" — actively
    # misleading.  They are now simply absent (0/19 ticker coverage confirmed).
    "pe_trailing",
    "revenue_growth_yoy",
    "profit_margin",
    "debt_to_equity",
    "fcf_yield_pct",
    "roe",
    "free_cash_flow",
    # Filings-derived numerics.
    "days_since_last_filing",
    "n_filings_30d",
    # 8-K item counters (Phase 7, Fix H).
    "n_item_502_30d",
    "n_item_202_30d",
    "n_item_101_30d",
    # Insider trade columns — per-transaction-code aggregates (Phase 7, Fix E).
    "insider_net_dollars_30d",        # kept for back-compat: P buys − P/S sells
    "insider_n_buys_30d",
    "insider_n_sells_30d",
    "insider_cluster_buy_flag",
    "insider_cluster_sell_flag",
    "insider_planned_sale_ratio",
    # Phase 7 per-code breakdown (Fix E).
    "insider_open_market_buy_dollars_30d",
    "insider_open_market_sell_dollars_30d",
    "insider_tax_withholding_dollars_30d",
    "insider_gift_count_30d",
    # Phase 7 senior-officer aggregate (Fix F — replaces _role_rank).
    "senior_officer_buy_dollars_30d",
    # Insider derivative columns (Phase 7, Fix G).
    "insider_option_exercise_value_30d",
    "insider_derivative_planned_ratio_30d",
    "senior_officer_derivative_grant_shares_30d",
    # Legacy derivative counts (kept for back-compat).
    "insider_derivative_exercise_count",
    "insider_derivative_grant_count",
)

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


def _parse_dt(raw_filed: Any) -> datetime | None:
    """Parse a filed_at value (datetime object or ISO string) into a UTC datetime.

    Parameters
    ----------
    raw_filed:
        A ``datetime`` object or ISO 8601 string.

    Returns
    -------
    datetime | None
        UTC-aware datetime, or ``None`` if parsing fails.
    """
    if raw_filed is None:
        return None
    if isinstance(raw_filed, datetime):
        # Ensure timezone-aware; assume UTC if naive.
        return raw_filed if raw_filed.tzinfo else raw_filed.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(raw_filed))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


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
        Partial feature dict covering the stats columns.
    """
    out: dict[str, float] = {}
    if not stats:
        return out

    # forward-looking / broker-consensus fields (pe_forward, peg,
    # analyst_rating_avg, number_of_analyst_opinions) are intentionally NOT
    # extracted — see the note on _KEYS.  They have no PIT-correct source.
    out["pe_trailing"]               = _f(stats.get("trailing_pe") or stats.get("pe_trailing"))
    out["revenue_growth_yoy"]        = _f(stats.get("revenue_growth_yoy") or stats.get("revenue_growth"))
    out["profit_margin"]             = _f(stats.get("profit_margin"))
    out["debt_to_equity"]            = _f(stats.get("debt_to_equity"))
    out["roe"]                       = _f(stats.get("return_on_equity") or stats.get("roe"))
    out["free_cash_flow"]            = _f(stats.get("free_cash_flow") or stats.get("fcf"))

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


def _item_counters_30d(filings: list[dict], as_of: date) -> dict[str, float]:
    """Count 8-K item appearances in the trailing 30 days.

    Maps the three items most relevant to fundamental signals:
    - 5.02 executive departure
    - 2.02 earnings release
    - 1.01 material agreement

    Parameters
    ----------
    filings:
        List of ``Filing.model_dump()`` dicts.
    as_of:
        Reference date for the 30-day cutoff.

    Returns
    -------
    dict[str, float]
        ``n_item_502_30d``, ``n_item_202_30d``, ``n_item_101_30d``.
    """
    cutoff = as_of - timedelta(days=_WINDOW_DAYS)
    counters = {"n_item_502_30d": 0, "n_item_202_30d": 0, "n_item_101_30d": 0}

    for f in filings:
        if f.get("form_type") != "8-K":
            continue
        filed_dt = _parse_dt(f.get("filed_at"))
        if filed_dt is None or filed_dt.date() < cutoff:
            continue
        items = f.get("items_8k") or []
        if "5.02" in items:
            counters["n_item_502_30d"] += 1
        if "2.02" in items:
            counters["n_item_202_30d"] += 1
        if "1.01" in items:
            counters["n_item_101_30d"] += 1

    return {k: float(v) for k, v in counters.items()}


def _insider_per_code_aggregates(trades: list[dict]) -> dict[str, float]:
    """Split insider trades by transaction_code so the strategist can
    distinguish open-market activity (P/S) from administrative codes (F/G).

    Also computes the ``senior_officer_buy_dollars_30d`` feature using the
    reporter flag ``is_officer`` rather than the old ``_role_rank()`` regex
    heuristic.

    Parameters
    ----------
    trades:
        List of ``InsiderTrade.model_dump()`` dicts (already window-filtered
        to the 30-day look-back by the caller).

    Returns
    -------
    dict[str, float]
        Per-code aggregate features.
    """
    out = {
        "insider_open_market_buy_dollars_30d":  0.0,
        "insider_open_market_sell_dollars_30d": 0.0,
        "insider_tax_withholding_dollars_30d":  0.0,
        "insider_gift_count_30d":               0.0,
        "senior_officer_buy_dollars_30d":       0.0,
    }

    for t in trades:
        code    = t.get("transaction_code") or ""
        shares  = float(t.get("shares") or 0.0)
        price   = float(t.get("price_per_share") or 0.0)
        dollars = shares * price

        if code == "P":
            out["insider_open_market_buy_dollars_30d"] += dollars
            # Open-market buy by a named officer → senior-officer aggregate.
            if t.get("is_officer"):
                out["senior_officer_buy_dollars_30d"] += dollars
        elif code == "S":
            out["insider_open_market_sell_dollars_30d"] += dollars
        elif code == "F":
            out["insider_tax_withholding_dollars_30d"] += dollars
        elif code == "G":
            out["insider_gift_count_30d"] += 1

    return out


def _insider_aggregates_from_flat(
    trades: list[dict],
    as_of_date: date,
) -> dict[str, float]:
    """Compute all insider-trade feature columns from a flat list of trade dicts.

    Parameters
    ----------
    trades:
        List of ``InsiderTrade.model_dump()`` dicts.
    as_of_date:
        Reference date for the 30-day window cutoff.

    Returns
    -------
    dict[str, float]
        All insider feature columns.
    """
    cutoff = as_of_date - timedelta(days=_WINDOW_DAYS)

    # Filter to window.
    window_trades: list[dict] = []
    for t in trades:
        filed_dt = _parse_dt(t.get("filed_at"))
        if filed_dt is not None and filed_dt.date() >= cutoff:
            window_trades.append(t)

    # Count open-market buys/sells by side (all codes, for back-compat counts).
    buys  = [t for t in window_trades if (t.get("side") or "").lower() == "buy"]
    sells = [t for t in window_trades if (t.get("side") or "").lower() == "sell"]

    # Seed both sums with 0.0 so an empty window yields a float (not int 0);
    # the all-floats column contract requires every value be a plain float.
    buy_value  = sum((float(t.get("shares") or 0) * float(t.get("price_per_share") or 0) for t in buys), 0.0)
    sell_value = sum((float(t.get("shares") or 0) * float(t.get("price_per_share") or 0) for t in sells), 0.0)

    # Cluster flags — count distinct insider names on each side.
    buy_names  = {t.get("insider_name") for t in buys if t.get("insider_name")}
    sell_names = {t.get("insider_name") for t in sells if t.get("insider_name")}
    cluster_buy  = 1.0 if len(buy_names)  >= _CLUSTER_THRESHOLD else 0.0
    cluster_sell = 1.0 if len(sell_names) >= _CLUSTER_THRESHOLD else 0.0

    # Planned sale ratio — proportion of sells that carry the 10b5-1 flag.
    n_sells_total = len(sells)
    planned_ratio = (
        sum(1 for t in sells if t.get("is_10b5_1")) / n_sells_total
        if n_sells_total > 0
        else 0.0
    )

    # Per-code breakdown (Fix E) and senior-officer aggregate (Fix F).
    per_code = _insider_per_code_aggregates(window_trades)

    result = {
        "insider_net_dollars_30d": buy_value - sell_value,
        "insider_n_buys_30d":  float(len(buys)),
        "insider_n_sells_30d": float(len(sells)),
        "insider_cluster_buy_flag":  cluster_buy,
        "insider_cluster_sell_flag": cluster_sell,
        "insider_planned_sale_ratio": planned_ratio,
    }
    result.update(per_code)
    return result


def _derivative_aggregates(
    derivs: list[dict],
    last_price: float,
    as_of_date: date,
) -> dict[str, float]:
    """Aggregate Phase 7 derivative-trade features from a flat list of dicts.

    Emits three features:
    - ``insider_option_exercise_value_30d`` — intrinsic value of option exercises
      (code ``M``) in the window: ``underlying_shares × (last_price − strike_price)``.
    - ``insider_derivative_planned_ratio_30d`` — fraction of derivative shares
      covered by a 10b5-1 plan.
    - ``senior_officer_derivative_grant_shares_30d`` — grant shares (code ``A``)
      from officer-level insiders.

    Parameters
    ----------
    derivs:
        List of ``InsiderDerivativeTrade.model_dump()`` dicts.
    last_price:
        Most recent share price from the ``ratios`` sub-dict; used to compute
        intrinsic exercise value.
    as_of_date:
        Reference date for the 30-day window cutoff.

    Returns
    -------
    dict[str, float]
        Three derivative aggregate features.
    """
    cutoff = as_of_date - timedelta(days=_WINDOW_DAYS)

    exercise_value = 0.0
    total_deriv_shares = 0.0
    planned_deriv_shares = 0.0
    officer_grant_shares = 0.0

    for d in derivs:
        filed_dt = _parse_dt(d.get("filed_at"))
        if filed_dt is not None and filed_dt.date() < cutoff:
            continue

        code    = d.get("transaction_code") or ""
        shares  = float(d.get("underlying_shares") or 0.0)
        strike  = float(d.get("strike_price") or 0.0)

        total_deriv_shares += shares

        if d.get("is_10b5_1"):
            planned_deriv_shares += shares

        if code == "M":
            # Exercise value: intrinsic value × number of shares.
            intrinsic = last_price - strike
            if intrinsic > 0:
                exercise_value += shares * intrinsic

        if code == "A" and d.get("is_officer"):
            # Grant to a senior officer — potential alignment signal.
            officer_grant_shares += shares

    planned_ratio = (
        planned_deriv_shares / total_deriv_shares
        if total_deriv_shares > 0
        else 0.0
    )

    return {
        "insider_option_exercise_value_30d":        exercise_value,
        "insider_derivative_planned_ratio_30d":      planned_ratio,
        "senior_officer_derivative_grant_shares_30d": officer_grant_shares,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_fundamental_features(
    raw: Mapping[str, Any],
    ticker: str = "",
    *,
    as_of: datetime | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Pull the locked fundamental feature catalogue from a raw payload dict.

    The sole accepted payload shape is the Phase 7 flat-list form:

    .. code-block:: python

        {
            "ratios":                  dict | None,
            "filings":                 list[dict],
            "insider_trades":          list[dict],   # InsiderTrade.model_dump()
            "insider_derivative_trades": list[dict], # InsiderDerivativeTrade.model_dump()
        }

    A missing ``insider_trades`` key raises ``KeyError`` — the legacy
    ``"insider": Form4Bundle`` path was retired in Plan 13 (A-054).

    Parameters
    ----------
    raw:
        Payload for a single ticker.
    ticker:
        Ticker symbol — used for future logging / error context.  Defaults to
        ``""`` so callers can pass ``state=`` as the only keyword argument.
    as_of:
        Legacy historical clock parameter.  Prefer ``state={"as_of": "..."}``
        for Phase 7 callers.
    state:
        Phase 7 pipeline state dict.  If ``state["as_of"]`` is present, it is
        used as the reference time for window computations.  Overrides ``as_of``
        when both are provided.

    Returns
    -------
    dict[str, float]
        Exactly the keys in ``_KEYS``, all floats.  Missing or unparseable
        fields default to 0.0.
    """
    out = _zero_features()

    if not raw:
        return out

    # Resolve the historical clock.  State takes priority over the legacy kwarg.
    if state is not None and state.get("as_of"):
        raw_as_of = state["as_of"]
        # Accept ISO string or datetime.
        if isinstance(raw_as_of, str):
            now = datetime.fromisoformat(raw_as_of)
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
        elif isinstance(raw_as_of, datetime):
            now = raw_as_of if raw_as_of.tzinfo else raw_as_of.replace(tzinfo=UTC)
        else:
            now = datetime.now(tz=UTC)
    elif as_of is not None:
        now = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    else:
        now = datetime.now(tz=UTC)

    as_of_date = now.date()

    # --- ratios (Phase 5: renamed from "stats" key at the fetch-callback level) ---
    stats_sub = raw.get("ratios") or {}
    out.update(_extract_stats_features(stats_sub))

    # --- filings ---
    filings_sub = raw.get("filings") or []
    out.update(_extract_filings_features(filings_sub, now))

    # --- 8-K item counters (Fix H) ---
    out.update(_item_counters_30d(filings_sub, as_of_date))

    # --- insider trades (Phase 7 flat-list path — sole supported shape) ---
    # The legacy 'insider: Form4Bundle' key was retired in Plan 13 (A-054).
    # Every producer must now emit insider_trades + insider_derivative_trades.
    if "insider_trades" not in raw:
        raise KeyError(
            "insider_trades missing from fundamental payload — every producer "
            "must emit the Phase 7 flat-list shape "
            "(insider_trades + insider_derivative_trades); the legacy "
            "'insider: Form4Bundle' key was retired in Plan 13 (A-054)."
        )

    trades_flat   = raw.get("insider_trades") or []
    derivs_flat   = raw.get("insider_derivative_trades") or []
    last_price_for_derivs = _f((stats_sub or {}).get("last_price"))

    out.update(_insider_aggregates_from_flat(trades_flat, as_of_date))
    out.update(_derivative_aggregates(derivs_flat, last_price_for_derivs, as_of_date))

    # Legacy derivative counts from the flat deriv list (for _KEYS back-compat).
    out["insider_derivative_exercise_count"] = float(
        sum(1 for d in derivs_flat if (d.get("transaction_code") or "") == "M")
    )
    out["insider_derivative_grant_count"] = float(
        sum(1 for d in derivs_flat if (d.get("transaction_code") or "") == "A")
    )

    return out

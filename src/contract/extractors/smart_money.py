"""Smart-money analyst deterministic feature extractor.

Phase 5 scope: smart_money consumes **external-observer flows only** —
congressional / public-figure trades (Quiver) and notable 13F holders.
Insider trades (Form 4) are now part of the Fundamental analyst's domain.

Phase 7 (providers-and-silent-gaps-v1, Task 2.12): adds notable-holder
aggregates from SC 13D / 13G filings.  The politician features remain
unchanged — Quiver is the sole feed and continues to soft-fail to an empty
list for most tickers.

Sparseness is the rule, not the exception — most tickers will have zero filings.
The ``is_no_data`` feature is the signal to the aggregator that this analyst's
verdict should be ignored for this ticker (``fill_missing`` semantics in
``contract.digest``).

Closed vocabulary
-----------------
The ``key_factors`` tags emitted by the downstream verdict function
(``derive_smart_money_verdict``, Task 9) are drawn exclusively from this set:

``net_buying``
    Net dollar flow across politicians + 13F holders is positive.
``net_selling``
    Net dollar flow is negative.
``multi_filer_consensus``
    Three or more distinct filers on the same side.
``lone_filer``
    Only one filer present across all sources.
``high_volume_flow``
    Total trades (buys + sells) meet or exceed the high-activity threshold.
``mixed_activity``
    Buys and sells are both present with no dominant side.

No insider-derived tags (e.g. ``cluster_buying``, ``planned_sale_dominant``)
appear here; those live in the Fundamental analyst's vocabulary.
"""
from __future__ import annotations

import contextlib
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

# TYPE_CHECKING guard prevents a circular import at module load time:
# contract.extractors.smart_money ← agents.analysts.heuristics ←
#   agents.analysts.__init__ ← smart_money.agent ← contract.extractors.smart_money.
# Both imports are done lazily inside derive_smart_money_verdict at runtime,
# by which point the module graph is fully initialised.
if TYPE_CHECKING:
    from agents.analysts.heuristics import SmartMoneyHeuristics
    from contract.evidence import AnalystVerdict

# How many days back the notable-holder window extends.
#
# Set to 90 days to match the ``notable_holders`` provider's lookback semantics.
# SC 13D / 13G filings describe ongoing ownership positions that remain
# meaningful well past their filing date; a 30-day cutoff was over-aggressive
# and caused false ``is_no_data`` on tickers whose most recent filing was
# 31–89 days old, even though the position itself was still current.
_HOLDER_WINDOW_DAYS = 90

# The complete, locked set of feature keys this extractor always returns.
_KEYS = (
    "n_politicians",
    "n_buys_30d",
    "n_sells_30d",
    "total_dollar_value_buys",
    "total_dollar_value_sells",
    "net_flow_dollar",
    # Phase 7 notable-holder aggregates (Fix: Task 2.12).
    "n_active_13d_30d",
    "n_passive_13g_30d",
    "n_amendments_30d",
    "notable_holder_present",
    "max_percent_of_class_30d",
    "total_shares_held_30d",
    "is_no_data",
)


def _zero_features() -> dict[str, float]:
    """Return a zeroed feature dict with `is_no_data` defaulting to 1.0 (no data)."""
    out = {k: 0.0 for k in _KEYS}
    out["is_no_data"] = 1.0  # default to no-data; caller must explicitly clear it
    return out


def _amount(filing: Mapping[str, Any]) -> float:
    """Extract a dollar amount from a filing dict, tolerating missing or malformed values.

    Parameters
    ----------
    filing:
        A single congressional-filing dict. Checks ``amount`` first,
        then ``dollar_value`` as a legacy alias.

    Returns
    -------
    float
        The parsed amount, or ``0.0`` if absent or unparseable.
    """
    val = filing.get("amount") or filing.get("dollar_value") or 0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(raw_filed: Any) -> datetime | None:
    """Parse a filed_at value (datetime object or ISO string) to a UTC datetime.

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
        return raw_filed if raw_filed.tzinfo else raw_filed.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(raw_filed))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _notable_holder_aggregates(
    holders: list[dict],
    as_of_date: date,
) -> dict[str, float]:
    """Aggregate SC 13D / 13G notable-holder features within the 90-day window.

    The window width is controlled by ``_HOLDER_WINDOW_DAYS`` (90 days).
    Feature keys use the ``_30d`` suffix for backwards compatibility with the
    pipeline's feature-key contract; the suffix reflects the original design
    intent, not the current window width.

    Emits six features:
    - ``n_active_13d_30d`` — count of SC 13D filings with ``intent="active"``
    - ``n_passive_13g_30d`` — count of SC 13G filings (intent ``"passive"``)
    - ``n_amendments_30d`` — count of ``is_amendment=True``
    - ``notable_holder_present`` — ``1.0`` if any holder rows in window else ``0.0``
    - ``max_percent_of_class_30d`` — max non-null ``percent_of_class``
    - ``total_shares_held_30d`` — sum of non-null ``shares_held``

    Parameters
    ----------
    holders:
        List of ``NotableHolder.model_dump()`` dicts.
    as_of_date:
        Reference date for the 90-day cutoff (``_HOLDER_WINDOW_DAYS``).

    Returns
    -------
    dict[str, float]
        Six notable-holder aggregate features.
    """
    cutoff = as_of_date - timedelta(days=_HOLDER_WINDOW_DAYS)

    n_active     = 0
    n_passive    = 0
    n_amendments = 0
    max_pct      = 0.0
    total_shares = 0.0
    any_in_window = False

    for h in holders:
        filed_dt = _parse_dt(h.get("filed_at"))
        if filed_dt is None or filed_dt.date() < cutoff:
            continue

        any_in_window = True

        form_type = (h.get("form_type") or "").upper()
        intent    = (h.get("intent") or "").lower()

        # Count 13D (active) vs 13G (passive) filings.
        if "13D" in form_type and intent == "active":
            n_active += 1
        elif "13G" in form_type:
            n_passive += 1

        # Amendment counter — covers both 13D/A and 13G/A.
        if h.get("is_amendment"):
            n_amendments += 1

        # Accumulate percent_of_class and shares_held where present.
        pct = h.get("percent_of_class")
        if pct is not None:
            with contextlib.suppress(TypeError, ValueError):
                max_pct = max(max_pct, float(pct))

        shares = h.get("shares_held")
        if shares is not None:
            with contextlib.suppress(TypeError, ValueError):
                total_shares += float(shares)

    return {
        "n_active_13d_30d":       float(n_active),
        "n_passive_13g_30d":      float(n_passive),
        "n_amendments_30d":       float(n_amendments),
        "notable_holder_present": 1.0 if any_in_window else 0.0,
        "max_percent_of_class_30d": max_pct,
        "total_shares_held_30d":    total_shares,
    }


def _resolve_as_of(state: Mapping[str, Any] | None) -> date:
    """Resolve the reference date from the pipeline state dict.

    Parameters
    ----------
    state:
        Pipeline state dict (may be ``None``).  Reads ``state["as_of"]``
        as an ISO date or datetime string.

    Returns
    -------
    date
        Resolved reference date, defaulting to today (UTC) when absent.
    """
    if state is None:
        return datetime.now(tz=UTC).date()
    raw = state.get("as_of")
    if raw is None:
        return datetime.now(tz=UTC).date()
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    try:
        dt = datetime.fromisoformat(str(raw))
        return dt.date()
    except (ValueError, TypeError):
        return datetime.now(tz=UTC).date()


def extract_smart_money_features(
    raw: Mapping[str, Any],
    ticker: str = "",
    *,
    as_of: datetime | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Aggregate congressional filings and notable-holder records into features.

    Phase 7 adds ``notable_holders`` aggregation (SC 13D / 13G) to the
    existing congressional-trade features.

    Parameters
    ----------
    raw:
        Raw ticker data dict.  May contain:
        - ``"filings"`` or ``"transactions"`` — congressional filing dicts.
        - ``"politician_trades"`` — alias for congressional filings.
        - ``"notable_holders"`` — list of ``NotableHolder.model_dump()`` dicts.
    ticker:
        Ticker symbol — accepted for logging/tracing; not used in computation.
        Defaults to ``""`` so callers can pass ``state=`` as the only keyword.
    as_of:
        Legacy historical clock parameter.
    state:
        Phase 7 pipeline state dict.  ``state["as_of"]`` is used for the
        notable-holder window (90 days; see ``_HOLDER_WINDOW_DAYS``).

    Returns
    -------
    dict[str, float]
        Exactly the keys in ``_KEYS``, all ``float``.
        ``is_no_data == 1.0`` signals that the digest aggregator should
        ignore this analyst's verdict for this ticker.
    """
    out = _zero_features()

    if not raw:
        return out

    # Resolve the reference date for the notable-holder window.
    as_of_date = _resolve_as_of(state) if state is not None else (
        as_of.date() if as_of is not None else datetime.now(tz=UTC).date()
    )

    # --- Congressional / politician trades ---
    # Support 'filings' (canonical), 'transactions', or 'politician_trades' alias.
    filings = (
        raw.get("filings")
        or raw.get("transactions")
        or raw.get("politician_trades")
        or []
    )

    if filings:
        # At least one filing present — clear the no-data flag.
        out["is_no_data"] = 0.0

        filers: set[str] = set()
        n_buys = 0
        n_sells = 0
        total_buys = 0.0
        total_sells = 0.0

        for f in filings:
            filer = f.get("filer_id") or f.get("filer") or ""
            if filer:
                filers.add(str(filer))

            side = (f.get("side") or "").upper()
            amt = _amount(f)

            if side == "BUY":
                n_buys += 1
                total_buys += amt
            elif side == "SELL":
                n_sells += 1
                total_sells += amt

        out["n_politicians"]            = float(len(filers))
        out["n_buys_30d"]               = float(n_buys)
        out["n_sells_30d"]              = float(n_sells)
        out["total_dollar_value_buys"]  = total_buys
        out["total_dollar_value_sells"] = total_sells
        out["net_flow_dollar"]          = total_buys - total_sells

    # --- Notable holders (Phase 7, Fix: Task 2.12) ---
    holders = raw.get("notable_holders") or []
    if holders:
        holder_aggs = _notable_holder_aggregates(holders, as_of_date)
        out.update(holder_aggs)
        # Clear the no-data flag if we have notable-holder data even without
        # politician filings — they're independent signals.
        if holder_aggs["notable_holder_present"] > 0:
            out["is_no_data"] = 0.0

    return out


def derive_smart_money_verdict(
    features: dict[str, float],
    h: SmartMoneyHeuristics,
) -> AnalystVerdict:
    """Map the smart-money feature vector to an ``AnalystVerdict`` via Phase-5 heuristics.

    External-observer flows only (politicians + 13F holders).  Insider trades
    (Form 4) are the Fundamental analyst's domain.  See spec
    §"derive_smart_money_verdict".

    Pure function — no I/O, no globals.  Safe for table-driven unit tests.

    Lean logic:
    - Positive ``net_flow_dollar`` → ``"bullish"`` (net buying observed).
    - Negative ``net_flow_dollar`` → ``"bearish"`` (net selling observed).
    - Zero net flow → ``"neutral"`` (mixed or no directional signal).

    Confidence interpolation (clamped to ``[0, 1]``):
    - *Many filers + high activity*: confidence set to
      ``h.consensus_confidence_ceiling`` and ``multi_filer_consensus`` /
      ``high_volume_flow`` tags added.
    - *Lone filer / single trade*: confidence set to
      ``h.lone_filer_confidence_floor`` and ``lone_filer`` tag added.
    - Otherwise: linearly interpolated between floor and ceiling based on a
      combined weight of filer count and trade activity.

    Magnitude:
    - Computed as ``|net_flow_dollar| / (buys + sells + 1)``, representing
      the flow asymmetry ratio rather than raw dollar size.  Capped at
      ``h.magnitude_cap``.

    Parameters
    ----------
    features:
        Output of ``extract_smart_money_features`` — all keys from ``_KEYS``
        present as ``float``.
    h:
        Validated ``SmartMoneyHeuristics`` config section.

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

    # --- No-data short-circuit -----------------------------------------------
    # The extractor sets is_no_data=1.0 when no filings were found.
    # Propagate this as a zero-confidence neutral verdict so downstream
    # consumers can apply fill_missing semantics.
    if features.get("is_no_data", 0.0) >= 1.0:
        return AnalystVerdict(
            lean="neutral",
            magnitude=0.0,
            confidence=0.0,
            rationale="no smart-money activity",
            key_factors=[],
            is_no_data=True,
        )

    net    = features["net_flow_dollar"]
    buys   = features["total_dollar_value_buys"]
    sells  = features["total_dollar_value_sells"]
    nf     = features["n_politicians"]
    trades = features["n_buys_30d"] + features["n_sells_30d"]

    factors: list[str] = []

    # --- Lean: sign of net dollar flow ----------------------------------------
    if net > 0:
        lean = "bullish"
        factors.append("net_buying")
    elif net < 0:
        lean = "bearish"
        factors.append("net_selling")
    else:
        lean = "neutral"
        factors.append("mixed_activity")

    # --- Magnitude: flow asymmetry ratio, capped --------------------------------
    # Add 1.0 to the denominator to guard against division-by-zero when both
    # buys and sells are zero (edge case with net_flow_dollar also zero).
    denom     = buys + sells + 1.0
    magnitude = min(abs(net) / denom, h.magnitude_cap)

    # --- Confidence: interpolated by filer count and trade activity ------------
    if nf >= h.multi_filer_min_count and trades >= h.high_activity_trade_count:
        # Strong consensus signal: multiple filers + high activity.
        confidence = h.consensus_confidence_ceiling
        factors.append("multi_filer_consensus")
        factors.append("high_volume_flow")

    elif nf <= 1 and trades <= 1:
        # Weak signal: only one filer with at most one trade.
        confidence = h.lone_filer_confidence_floor
        factors.append("lone_filer")

    else:
        # Intermediate: linearly interpolate using equal weight for filer count
        # and trade activity, each normalised to their respective thresholds.
        span_f = max(0.0, (nf - 1) / max(1, h.multi_filer_min_count - 1))
        span_t = max(0.0, (trades - 1) / max(1, h.high_activity_trade_count - 1))
        weight = min(1.0, (span_f + span_t) / 2.0)
        confidence = h.lone_filer_confidence_floor + weight * (
            h.consensus_confidence_ceiling - h.lone_filer_confidence_floor
        )

    # Clamp confidence to the valid [0, 1] range before returning.
    confidence = max(0.0, min(1.0, confidence))

    # Build rationale from the collected factor tags, truncated for safety.
    rationale = (", ".join(factors) or "neutral")[:160]

    return AnalystVerdict(
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=factors,
        is_no_data=False,
    )

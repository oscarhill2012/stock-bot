"""Smart-money analyst deterministic feature extractor.

Phase 5 scope: smart_money consumes **external-observer flows only** —
congressional / public-figure trades (Quiver) and notable 13F holders.
Insider trades (Form 4) are now part of the Fundamental analyst's domain.

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

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

# TYPE_CHECKING guard prevents a circular import at module load time:
# contract.extractors.smart_money ← agents.analysts.heuristics ←
#   agents.analysts.__init__ ← smart_money.agent ← contract.extractors.smart_money.
# Both imports are done lazily inside derive_smart_money_verdict at runtime,
# by which point the module graph is fully initialised.
if TYPE_CHECKING:
    from agents.analysts.heuristics import SmartMoneyHeuristics
    from contract.evidence import AnalystVerdict

# The complete, locked set of feature keys this extractor always returns.
_KEYS = (
    "n_politicians",
    "n_buys_30d",
    "n_sells_30d",
    "total_dollar_value_buys",
    "total_dollar_value_sells",
    "net_flow_dollar",
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


def extract_smart_money_features(raw: Mapping[str, Any], ticker: str) -> dict[str, float]:
    """Aggregate congressional filings into counts, dollar totals, and a no-data flag.

    Caller is expected to have already filtered to the last 30 days; this
    function just summarises whatever it is given.

    Parameters
    ----------
    raw:
        Raw ticker data dict. Expected to contain a ``filings`` key
        (list of congressional-filing dicts with ``filer_id``, ``side``,
        and ``amount`` fields). An empty dict or empty filings list sets
        ``is_no_data = 1.0`` and returns zeroed counts.
    ticker:
        Ticker symbol — accepted for logging/tracing purposes, not used in
        computation currently.

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

    # Support both 'filings' (canonical) and 'transactions' (alternative alias).
    filings = raw.get("filings") or raw.get("transactions") or []

    if not filings:
        return out

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

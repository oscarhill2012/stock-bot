"""Smart-money analyst deterministic feature extractor.

Sparseness is the rule, not the exception — most tickers will have zero filings.
The `is_no_data` feature is the signal to the aggregator that this analyst's
verdict should be ignored for this ticker (`fill_missing` semantics in
`contract.digest`).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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

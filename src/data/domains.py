"""Canonical set of data-provider domain names.

Single source of truth — previously duplicated between ``data.config``
(as the private ``_DOMAINS``) and ``data.registry`` (as the public
``DOMAINS``).  The duplication existed to avoid an import cycle: this
leaf module has no project imports and breaks the cycle cleanly.

A domain is a category of data (price history, news, filings, …) for
which exactly one provider must be configured in ``config/data.json``.
"""
from __future__ import annotations

# Eight domains.  The four Phase-3 additions (earnings, analyst_consensus,
# short_interest, options) were culled in the 2026-05-26 data-provider audit
# (plan 08) because no analyst consumed them.  Any addition here must also
# gain a ``DOMAIN_SHAPES`` entry in ``data.registry`` and a ``providers``
# entry in ``config/data.json``.
DOMAINS: frozenset[str] = frozenset({
    # Phase 5: "stats" retired — split into "price_history" + "company_ratios".
    "price_history",
    "company_ratios",
    "news",
    "social_sentiment",
    "insider_trades",
    "politician_trades",
    "notable_holders",
    "filings",
})

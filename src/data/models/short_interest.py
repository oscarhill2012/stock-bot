"""Short-interest snapshot model — populated by the FINRA provider.

v1 PROXY CAVEAT (Phase -1 verification 2026-05-17): the only freely available
FINRA dataset on the OAuth tier is ``regShoDaily`` (per-ticker, per-venue,
daily short *sale* volume).  The classical NYSE/Nasdaq biweekly *open*
short-interest snapshot is not accessible on this tier and has no sibling
endpoint.

v1 therefore ships a synthesised proxy:

- ``short_interest`` = 30-day cumulative short sale volume (a stock-vs-flow
  approximation, not the classical open short-interest figure).
- ``days_to_cover`` = 30d cumulative short sale volume / 30d mean daily total
  volume.

This is correlated with classical open short interest but is not the real
thing.  The ``source`` field records the synthesis origin so downstream
extractors can disambiguate if (when) a true snapshot provider lands.

The PIT gate is ``report_publish_date``.  For the synthesis path it equals
``settlement_date`` because regShoDaily publishes T+1 with no biweekly lag.
A future true-snapshot provider would diverge here (~8 business-day lag), so
backtest queries must filter on ``report_publish_date <= as_of``, not on
``settlement_date`` directly.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class ShortInterestSnapshot(BaseModel):
    """One short-interest (or proxy) observation for a single ticker.

    Parameters
    ----------
    ticker:
        Upper-cased symbol.
    settlement_date:
        The date the short positions were as-of (settlement, not publish).
    report_publish_date:
        The date the data became publicly available — used as the PIT gate in
        backtest cache lookups.  Collapses to ``settlement_date`` for the v1
        synthesis path; diverges for a true biweekly snapshot provider.
    short_interest:
        Share count.  For ``source="finra_regsho_synthesised"`` this is the
        30-day cumulative short sale volume (proxy); for
        ``source="finra_official_snapshot"`` it would be the classical open
        short-interest figure.
    average_daily_volume:
        30-day mean daily total traded volume (shares), used to derive
        ``days_to_cover``.
    days_to_cover:
        ``short_interest / average_daily_volume``.  ``None`` when
        ``average_daily_volume`` is unavailable.
    source:
        Distinguishes the synthesis proxy from any future official snapshot
        source, so downstream code can apply appropriate caveats.
    """

    ticker: str
    settlement_date: date
    report_publish_date: date              # PIT gate

    # For the synthesis path: 30-day cumulative short sale volume (proxy).
    # For a future true-snapshot provider: classical open short interest.
    short_interest: float

    average_daily_volume: float | None = None
    days_to_cover: float | None = None

    source: Literal[
        "finra_regsho_synthesised",    # v1 proxy — regShoDaily cumulative volume
        "finra_official_snapshot",     # future: official biweekly open SI snapshot
    ] = "finra_regsho_synthesised"

"""PIT-pin probe — does ``pit_composite.fetch`` enforce filing-date PIT?

The whole backtest cache for the ``company_ratios`` domain rests on edgartools'
``EntityFacts.query().as_of(date).latest()`` filtering by **filing date**, not
fiscal-period-end.  If it accidentally filters by period-end (or doesn't
filter at all), then a snapshot taken ``as_of=2025-09-02`` could silently
return Q-results that weren't actually publicly filed until weeks later —
invalidating every backtest that touches fundamentals.

Method
------
We pick a known 10-Q filing date and call ``pit_composite.fetch`` one trading
day either side of it.  If the provider is honouring filing-date PIT, then
**XBRL-derived facts** (profit_margin, debt_to_equity, roe,
revenue_growth_yoy, free_cash_flow) should **change** across this boundary —
because BEFORE sees the prior 10-Q while AFTER sees the newly-filed one.

This script also runs a low-level diagnostic that calls ``edgartools``
directly (bypassing pit_composite's exception swallowing) so we can see
whether ``.as_of()`` is returning anything at all, and what the row's
filing date actually is.

Usage
-----
Network + ``EDGAR_IDENTITY`` required:

    PYTHONPATH=src .venv/bin/python -m scripts.debug_pit_composite_pin

Exit code is 0 on PASS (XBRL facts differ across the boundary) and 1 on
FAIL.  A FAIL means the backtest cache cannot be trusted for fundamentals
without an upstream fix.
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import datetime, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Module path setup — this script is invoked as ``-m scripts.X`` from the
# project root with ``PYTHONPATH=src`` already set.
from data.providers.company_ratios.pit_composite import fetch as pit_fetch


# AAPL's most recent 10-Q (per backtests/cache/store.sqlite filings table)
# was filed 2025-08-01 — clean calendar fiscal year, large universe of XBRL
# concepts.  Use it as the boundary instead of AVGO (which has an Oct/Nov
# fiscal year and returned all-None XBRL on the first probe).
_NY            = ZoneInfo("America/New_York")
_TICKER        = "AAPL"
_FILING_DATE   = datetime(2025, 8, 1).date()

# Straddle the filing date by one trading day either side.  Markets close
# at 16:00 New York; that's the natural as_of time for an end-of-day
# fundamentals snapshot.
_BEFORE        = datetime.combine(datetime(2025, 7, 31).date(), time(16, 0), tzinfo=_NY)
_AFTER         = datetime.combine(datetime(2025, 8, 4).date(),  time(16, 0), tzinfo=_NY)

# XBRL-derived fields — these decide the PIT verdict.  If they change
# across the boundary, edgartools' .as_of() is filtering by filing date.
_XBRL_FIELDS = (
    "profit_margin",
    "debt_to_equity",
    "roe",
    "revenue_growth_yoy",
    "free_cash_flow",
)

# Price-derived fields — expected to differ daily; informational only.
_PRICE_FIELDS = (
    "last_price",
    "market_cap",
    "trailing_pe",
    "dividend_yield",
    "fifty_day_average",
    "two_hundred_day_average",
)

# Identity fields — expected to match; informational only.
_IDENTITY_FIELDS = (
    "long_name",
    "sector",
)


def _format_value(value: object) -> str:
    """Render a model field for the diff table in a fixed-width cell.

    Floats are rounded to four decimal places; ``None`` is rendered
    literally so the table is easy to scan visually.
    """
    if value is None:
        return "None"
    if isinstance(value, float):
        return f"{value:,.4f}"
    return str(value)


def _row(field: str, before: object, after: object) -> tuple[str, bool]:
    """Build one diff row + a boolean indicating whether the values differ.

    The boolean is used by the caller to compute the overall PIT verdict —
    any single XBRL-derived field changing across the boundary is enough
    to declare PASS.
    """
    differs = before != after
    mark    = "✓ DIFFER" if differs else "  (same)"
    return (
        f"  {field:25s} {_format_value(before)[:22]:22s}  {_format_value(after)[:22]:22s}  {mark}",
        differs,
    )


def _diagnose_edgartools(ticker: str, before: datetime, after: datetime) -> None:
    """Low-level probe of edgartools directly — bypasses pit_composite's exception swallow.

    Prints whatever ``EntityFacts.query().by_concept(...).as_of(...).latest()``
    actually returns for a couple of well-known concepts at the two boundary
    dates.  If this prints empty / None on both sides, the bug is upstream
    of pit_composite (in edgartools or our EDGAR_IDENTITY setup).
    """
    print("\n" + "=" * 84)
    print(f"Low-level edgartools diagnostic — {ticker}")
    print("=" * 84)

    try:
        from edgar import Company, set_identity

        set_identity(os.environ["EDGAR_IDENTITY"])
        company = Company(ticker)

        # Identity attributes — exposed by the Company entity, not by facts.
        print(f"  Company.name             = {getattr(company, 'name', None)!r}")
        print(f"  Company.sic_description  = {getattr(company, 'sic_description', None)!r}")
        print(f"  Company.cik              = {getattr(company, 'cik', None)!r}")

        facts = company.get_facts()
        print(f"  Company.get_facts()      = {type(facts).__name__}")

        # Probe two well-known concepts at both as_ofs and print the row's
        # filing date — this is what tells us whether .as_of() is filtering
        # by filing date or by period-end.
        for concept in ("EarningsPerShareBasic", "Revenues", "NetIncomeLoss"):
            print(f"\n  -- concept: {concept} --")
            for label, as_of in (("BEFORE", before), ("AFTER", after)):
                as_of_d = as_of.date()
                try:
                    q   = facts.query().by_concept(concept).as_of(as_of_d)
                    row = q.latest() if hasattr(q, "latest") else None
                    if row is None:
                        print(f"    {label} as_of={as_of_d}: row = None")
                        continue
                    # Try every plausible attribute name for value / filing date.
                    val   = getattr(row, "value", None)
                    filed = (
                        getattr(row, "filed", None)
                        or getattr(row, "filed_date", None)
                        or getattr(row, "accn_filed", None)
                    )
                    period = (
                        getattr(row, "period", None)
                        or getattr(row, "period_end", None)
                        or getattr(row, "end", None)
                    )
                    print(f"    {label} as_of={as_of_d}: value={val!r}  filed={filed!r}  period={period!r}")
                except Exception as exc:
                    print(f"    {label} as_of={as_of_d}: EXCEPTION {type(exc).__name__}: {exc}")

    except Exception:
        print("Diagnostic crashed:")
        traceback.print_exc()


async def _main() -> int:
    """Run the boundary probe and report a PASS/FAIL verdict.

    Returns
    -------
    int
        Process exit code — 0 on PASS (XBRL fields change across the
        filing boundary), 1 on FAIL.  2 indicates missing EDGAR_IDENTITY.
    """
    load_dotenv()
    if not os.getenv("EDGAR_IDENTITY"):
        print("EDGAR_IDENTITY missing — set it in .env before running.")
        return 2

    print("=" * 84)
    print("PIT-pin probe — pit_composite XBRL .as_of() filing-date enforcement")
    print("=" * 84)
    print(f"  ticker          : {_TICKER}")
    print(f"  10-Q filed      : {_FILING_DATE}")
    print(f"  BEFORE as_of    : {_BEFORE}")
    print(f"  AFTER  as_of    : {_AFTER}")
    print()

    print("Fetching BEFORE snapshot ...")
    before_snap = await pit_fetch(_TICKER, as_of=_BEFORE)

    print("Fetching AFTER  snapshot ...")
    after_snap  = await pit_fetch(_TICKER, as_of=_AFTER)

    print()
    print("Field-by-field diff:")
    print(f"  {'field':25s} {'BEFORE ' + str(_BEFORE.date()):22s}  {'AFTER ' + str(_AFTER.date()):22s}  verdict")
    print("  " + "-" * 80)

    print("\n  -- XBRL-derived (these decide the verdict) --")
    xbrl_differs_any = False
    for f in _XBRL_FIELDS:
        line, differs = _row(f, getattr(before_snap, f, None), getattr(after_snap, f, None))
        print(line)
        xbrl_differs_any = xbrl_differs_any or differs

    print("\n  -- Price-derived (informational; expected to differ) --")
    for f in _PRICE_FIELDS:
        line, _ = _row(f, getattr(before_snap, f, None), getattr(after_snap, f, None))
        print(line)

    print("\n  -- Identity (informational; expected to match) --")
    for f in _IDENTITY_FIELDS:
        line, _ = _row(f, getattr(before_snap, f, None), getattr(after_snap, f, None))
        print(line)

    # Run the edgartools-level diagnostic regardless of the verdict so we
    # can see whether None XBRL means "edgartools returned nothing" vs
    # "edgartools returned the same thing".
    _diagnose_edgartools(_TICKER, _BEFORE, _AFTER)

    print()
    print("=" * 84)
    if xbrl_differs_any:
        print("VERDICT: PASS — at least one XBRL field changed across the filing boundary.")
        print("         edgartools' .as_of() is honouring filing-date PIT as documented.")
        print("         'One snapshot at window-start' is safe.")
        return 0
    else:
        print("VERDICT: INCONCLUSIVE or FAIL — XBRL fields did not change across boundary.")
        print("         Inspect the diagnostic above: if edgartools returned None on both")
        print("         sides, the issue is upstream (no XBRL data, wrong concept names,")
        print("         identity not set).  If it returned the SAME non-None value, then")
        print("         .as_of() is NOT filtering by filing date — that's a real leak.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

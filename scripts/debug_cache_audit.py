"""Audit a fetched backtest cache window — sanity check before LLM replay.

Read-only diagnostic.  Connects to the cache SQLite directly (no project
imports) and reports per-domain coverage so a silent emptiness — like the
2026-05-18 insider_trades MISSING_TIMESTAMP drop — does not survive
unnoticed into a backtest run.

Usage:
    PYTHONPATH=src .venv/bin/python -m scripts.debug_cache_audit \\
        --window svb-stress-2023-03

Optional overrides:
    --cache-path <path>       use a non-default cache file
    --watchlist  <path>       use a non-default watchlist file
    --config-dir <path>       use a non-default config directory
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stdlib only.  We intentionally avoid importing project modules so the
# audit cannot be fooled by a regression that breaks our own readers.
# ---------------------------------------------------------------------------
import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Domain registry — covers every domain the fetcher knows about.  The PIT
# expression is what we filter on for date-range checks (matches the
# correctness rule documented at src/backtest/cache/schema.py:6).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Domain:
    """One row in the audit's domain registry."""

    name:     str        # registry key — also the value stored in cache_runs.domain
    table:    str        # SQL table name in the cache
    pit_expr: str        # SQL expression returning the PIT date (point-in-time)
    label:    str        # human row label, e.g. "trades", "bars"
    enabled:  bool = True


DOMAINS: list[Domain] = [
    Domain("ohlcv",             "ohlcv_bars",       "date(ts)",                                          "bars"),
    Domain("company_ratios",    "company_ratios",   "date(as_of_date)",                                  "snapshots"),
    Domain("filings",           "filings",          "date(filed_at)",                                    "filings"),
    Domain("news",              "news_articles",    "date(published_at)",                                "articles"),
    Domain("insider_trades",    "insider_trades",   "date(filed_at)",                                    "trades"),
    Domain(
        "politician_trades",
        "politician_trades",
        "date(COALESCE(disclosure_date, transaction_date))",
        "trades",
        enabled=False,      # disabled in fetcher 2026-05-18 — no free historical source
    ),
    Domain("notable_holders",   "notable_holders",  "date(filed_at)",                                    "filings"),
]


# ---------------------------------------------------------------------------
# Per-domain findings container — populated by `audit_domain` and consumed
# by the report formatter and the final verdict.
# ---------------------------------------------------------------------------
@dataclass
class DomainFindings:
    """Structured result of one domain's audit; everything the report needs."""

    domain:               Domain
    table_present:        bool                = True
    total_rows:           int                 = 0
    min_pit:              str | None          = None
    max_pit:              str | None          = None
    per_ticker_in_window: dict[str, int]      = field(default_factory=dict)
    cache_runs:           dict[str, dict]     = field(default_factory=dict)   # status → {count, rows_written}
    silent_empty_tickers: list[str]           = field(default_factory=list)   # status=ok AND rows_written=0
    errored_tickers:      list[str]           = field(default_factory=list)   # status=error
    skipped_tickers:      list[str]           = field(default_factory=list)   # no cache_runs row at all
    error_examples:       list[str]           = field(default_factory=list)

    # ── Future-bleed (PIT > window_end) — rows that a backtest analyst at
    # any tick within the window must never see.  A non-zero count almost
    # certainly indicates a provider that's silently over-fetching past the
    # simulation clock (e.g. ignoring a ``to_date`` kwarg the way AV news
    # was doing pre-2026-05-18 fix).  These bleeds are interpretation-
    # falsifying — the strategist would see post-event information and
    # produce results that *look* good but rely on hindsight.
    future_bleed_rows:        int                  = 0
    future_bleed_max_pit:     str | None           = None
    future_bleed_per_ticker:  dict[str, int]       = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> dict:
    """Read and parse `path` as JSON, raising a helpful FileNotFoundError."""

    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    return json.loads(path.read_text())


def _section(title: str) -> None:
    """Print a section divider so the report scans nicely."""

    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


# ---------------------------------------------------------------------------
# Per-domain audit — gathers every signal we'd want before letting LLMs
# loose on the cache.
# ---------------------------------------------------------------------------
def audit_domain(
    con:        sqlite3.Connection,
    domain:     Domain,
    window_key: str,
    start_iso:  str,
    end_iso:    str,
    tickers:    list[str],
) -> DomainFindings:
    """Audit one domain table for the given window and return findings.

    Parameters
    ----------
    con:
        Open read-only-ish sqlite3 connection to the cache file.
    domain:
        Domain registry entry naming the table + PIT expression.
    window_key:
        The ``cache_runs.window_key`` value to filter by (e.g.
        ``"svb-stress-2023-03"``).
    start_iso, end_iso:
        ISO date strings bounding the analyst-visible window.
    tickers:
        Watchlist symbols to check coverage against.
    """
    findings = DomainFindings(domain=domain)
    cur = con.cursor()

    # ── 0.  Does the table exist?  Skip everything else if not. ────────────
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (domain.table,),
    )
    if cur.fetchone() is None:
        findings.table_present = False
        return findings

    # ── 1.  Total rows currently stored (any date) for this domain.  Use
    #        this to spot "we didn't store anything at all". ────────────────
    cur.execute(f"SELECT COUNT(*) FROM {domain.table}")                       # noqa: S608
    findings.total_rows = cur.fetchone()[0]

    # Min/max PIT date across the whole table — gives a feel for the
    # date span the cache covers (warm-up should land earlier than start).
    if findings.total_rows > 0:
        cur.execute(f"SELECT MIN({domain.pit_expr}), MAX({domain.pit_expr}) FROM {domain.table}")  # noqa: S608
        lo, hi = cur.fetchone()
        findings.min_pit = lo
        findings.max_pit = hi

    # ── 2.  Per-ticker row count within the window (analyst-visible). ──────
    counts: dict[str, int] = {}
    for t in tickers:
        cur.execute(
            f"SELECT COUNT(*) FROM {domain.table} "                            # noqa: S608
            f"WHERE ticker=? AND {domain.pit_expr} BETWEEN ? AND ?",
            (t, start_iso, end_iso),
        )
        counts[t] = cur.fetchone()[0]
    findings.per_ticker_in_window = counts

    # ── 2b.  Future-bleed — rows whose PIT date is past window_end. ───────
    # Any non-zero result means a provider over-fetched past the simulation
    # clock, which would leak future info into a backtest tick at any time
    # within the window.  Reported per-ticker so the offending source is
    # obvious rather than just a domain-level number.
    cur.execute(
        f"SELECT COUNT(*), MAX({domain.pit_expr}) "                            # noqa: S608
        f"FROM {domain.table} WHERE {domain.pit_expr} > ?",
        (end_iso,),
    )
    bleed_count, bleed_max = cur.fetchone()
    findings.future_bleed_rows    = bleed_count or 0
    findings.future_bleed_max_pit = bleed_max

    if findings.future_bleed_rows > 0:
        cur.execute(
            f"SELECT ticker, COUNT(*) FROM {domain.table} "                    # noqa: S608
            f"WHERE {domain.pit_expr} > ? GROUP BY ticker "
            f"ORDER BY COUNT(*) DESC",
            (end_iso,),
        )
        findings.future_bleed_per_ticker = {t: n for t, n in cur.fetchall()}

    # ── 3.  cache_runs audit — what did the fetcher record? ────────────────
    cur.execute(
        "SELECT status, COUNT(*), COALESCE(SUM(rows_written), 0) "
        "FROM cache_runs WHERE window_key=? AND domain=? "
        "GROUP BY status",
        (window_key, domain.name),
    )
    for status, n, rows in cur.fetchall():
        findings.cache_runs[status] = {"count": n, "rows_written": rows}

    # Silent-empty trap (status='ok' AND rows_written=0) — this is exactly
    # the pattern that masked the EDGAR insider_trades bug pre-fix.
    cur.execute(
        "SELECT ticker FROM cache_runs WHERE window_key=? AND domain=? "
        "AND status='ok' AND rows_written=0 ORDER BY ticker",
        (window_key, domain.name),
    )
    findings.silent_empty_tickers = [r[0] for r in cur.fetchall()]

    # Errored tickers + a couple of example error strings (truncated).
    cur.execute(
        "SELECT ticker, error FROM cache_runs WHERE window_key=? AND domain=? "
        "AND status='error' ORDER BY ticker",
        (window_key, domain.name),
    )
    err_rows = cur.fetchall()
    findings.errored_tickers  = [r[0] for r in err_rows]
    findings.error_examples   = [f"{t}: {e[:100]}" for t, e in err_rows[:3]]

    # Skipped tickers — watchlist members with no cache_runs row at all.
    cur.execute(
        "SELECT DISTINCT ticker FROM cache_runs WHERE window_key=? AND domain=?",
        (window_key, domain.name),
    )
    recorded = {r[0] for r in cur.fetchall()}
    findings.skipped_tickers = sorted(set(tickers) - recorded)

    return findings


# ---------------------------------------------------------------------------
# Domain-specific deep dive: insider_trades.  Verifies the 2026-05-18 EDGAR
# Form 4 fixes have actually taken effect in the cache.
# ---------------------------------------------------------------------------
def deep_check_insider_trades(con: sqlite3.Connection) -> dict:
    """Return counts of rows that would indicate the EDGAR bugs are back.

    - ``missing_filed_at`` — rows whose ``filed_at`` coerced to the
      ``MISSING_TIMESTAMP`` sentinel (``0001-01-01 00:00:00+00:00``).
      Should be 0 once the filed_at fallback fix is live.
    - ``numeric_insider_name`` — rows whose ``insider_name`` consists only
      of digits (the Series.name → row-index leak).  Should be 0 once the
      ``"name"`` key is out of ``_row_get``'s fallback list.
    """
    cur = con.cursor()
    out: dict[str, int] = {}

    # SQLite stores TZ-aware datetimes as text starting with "0001-01-01".
    cur.execute(
        "SELECT COUNT(*) FROM insider_trades WHERE filed_at LIKE '0001-01-01%'",
    )
    out["missing_filed_at"] = cur.fetchone()[0]

    # SQLite has no REGEXP by default; fetch names and test in Python.
    cur.execute("SELECT insider_name FROM insider_trades")
    out["numeric_insider_name"] = sum(
        1 for (n,) in cur.fetchall() if n and n.isdigit()
    )

    return out


# ---------------------------------------------------------------------------
# Domain-specific deep dive: company_ratios.  Verifies the 2026-05-19 fixes:
#
# - list-unwrap fix in pit_composite (XBRL fields no longer silently None);
# - per-tick fill rewrite in scripts/backtest_fetch (one snapshot per NYSE
#   trading day, not one per quarter-end inside the window);
# - peg field intentionally surfaced as None in both live and backtest.
#
# We cross-check the per-ticker company_ratios row count against the
# in-window ohlcv bar count for the same ticker — they share the same
# trading-day cardinality, so any mismatch flags a fill regression.  We
# also report per-column null counts (in-window) and per-ticker × XBRL
# field coverage so that a *partial* regression (e.g. NetIncomeLoss
# missing for one ticker but not another) is visible at a glance.
# ---------------------------------------------------------------------------
# XBRL-derived ratio columns — should be populated on the majority of
# rows for calendar-fiscal-year US-domiciled tickers.  Sparse / all-null
# for ADRs and non-calendar fiscal years (e.g. AVGO Oct year-end) is
# expected — flag, but don't FAIL.
_XBRL_RATIO_FIELDS: tuple[str, ...] = (
    "profit_margin",
    "debt_to_equity",
    "roe",
    "revenue_growth_yoy",
    "free_cash_flow",
)

# Price-derived columns — should be populated on every in-window row
# (yfinance always has a close).  100% null on any of these means the
# yfinance leg of pit_composite failed silently.
_PRICE_FIELDS: tuple[str, ...] = (
    "last_price",
    "market_cap",
    "trailing_pe",
    "dividend_yield",
    "fifty_day_average",
    "two_hundred_day_average",
)


def deep_check_company_ratios(
    con:       sqlite3.Connection,
    tickers:   list[str],
    start_iso: str,
    end_iso:   str,
) -> dict:
    """Per-ticker fill density + per-column XBRL null distribution.

    Schema-resilient: introspects ``company_ratios`` columns at runtime and
    only queries fields that actually exist in the cache.  Any
    model-expected XBRL/price column that is *missing from the table* is
    captured in ``missing_columns`` so the verdict can flag the drift
    (silent-drop on write path).

    Returns
    -------
    dict
        Keys: ``rows_per_ticker`` (in-window rowcount),
        ``distinct_dates_per_ticker`` (distinct ``as_of_date``s),
        ``null_counts`` (per-column null count over in-window rows — only
        for columns that exist),
        ``peg_non_null`` (sanity for the leak fix — must be 0; ``None`` if
        the column does not exist in the schema),
        ``xbrl_coverage`` (per-ticker × XBRL field non-null count — only
        for XBRL fields that exist as columns),
        ``missing_columns`` (model-expected columns absent from the
        company_ratios table — drift signal).
    """
    cur = con.cursor()
    out: dict = {}

    # ── 0. Introspect the schema so we only query columns that exist. ────────
    # The 2026-05-19 pit_composite fix added several XBRL-derived ratio
    # fields to the ``CompanyRatios`` Pydantic model, but the SQLite cache
    # schema and the store's ``write_company_ratios`` were never updated to
    # persist them.  This block discovers the gap rather than crashing.
    cur.execute("PRAGMA table_info(company_ratios)")
    table_cols  = {row[1] for row in cur.fetchall()}

    expected    = set(_XBRL_RATIO_FIELDS) | set(_PRICE_FIELDS) | {"peg"}
    present     = sorted(expected & table_cols)
    missing     = sorted(expected - table_cols)
    out["missing_columns"] = missing

    # ── 1. Per-ticker row & distinct-date counts ──────────────────────────────
    # If the per-tick fill is healthy these two numbers match and equal the
    # number of NYSE trading days in [start, end] (cross-checked against
    # ohlcv in the printer).
    rows_per_ticker:           dict[str, int] = {}
    distinct_dates_per_ticker: dict[str, int] = {}
    for t in tickers:
        cur.execute(
            "SELECT COUNT(*), COUNT(DISTINCT date(as_of_date)) "
            "FROM company_ratios "
            "WHERE ticker=? AND date(as_of_date) BETWEEN ? AND ?",
            (t, start_iso, end_iso),
        )
        n, n_dates = cur.fetchone()
        rows_per_ticker[t]           = n
        distinct_dates_per_ticker[t] = n_dates
    out["rows_per_ticker"]           = rows_per_ticker
    out["distinct_dates_per_ticker"] = distinct_dates_per_ticker

    # ── 2. Per-column null counts (in-window only) ───────────────────────────
    # Iterate over the intersection of expected and present columns so a
    # cache schema older than the model doesn't blow up the audit.  Columns
    # that exist on the model but not the table are surfaced via
    # ``missing_columns`` instead (much more informative than a crash).
    null_counts: dict[str, int] = {}
    for col in present:
        cur.execute(
            f"SELECT COUNT(*) FROM company_ratios "                            # noqa: S608
            f"WHERE date(as_of_date) BETWEEN ? AND ? AND {col} IS NULL",
            (start_iso, end_iso),
        )
        null_counts[col] = cur.fetchone()[0]
    out["null_counts"] = null_counts

    # ── 3. PEG-specific verification — must be 100% null post-fix ────────────
    # ``peg`` had a wall-clock leak from yf.Ticker.info["pegRatio"] before
    # the 2026-05-19 fix.  Any non-null row means the fix didn't land for
    # that path (or someone re-introduced the yfinance fallback).  If peg
    # isn't a column yet, the field is structurally absent — record None so
    # the verdict can distinguish "no column" from "column with leaks".
    if "peg" in table_cols:
        cur.execute("SELECT COUNT(*) FROM company_ratios WHERE peg IS NOT NULL")
        out["peg_non_null"] = cur.fetchone()[0]
    else:
        out["peg_non_null"] = None

    # ── 4. Per-ticker × XBRL field non-null count ────────────────────────────
    # A row of zeros for one ticker × all five fields strongly suggests the
    # XBRL pipeline failed for that ticker (foreign filer, no XBRL data,
    # weird taxonomy mapping).  Per-field sparsity is normal for ratios
    # that depend on a missing concept (e.g. ShortTermBorrowings absent →
    # debt_to_equity null while profit_margin / roe stay populated).  Only
    # iterate over XBRL fields that actually exist in the schema.
    present_xbrl = [f for f in _XBRL_RATIO_FIELDS if f in table_cols]
    xbrl_coverage: dict[str, dict[str, int]] = {}
    for t in tickers:
        per_field: dict[str, int] = {}
        for fld in present_xbrl:
            cur.execute(
                f"SELECT COUNT(*) FROM company_ratios "                        # noqa: S608
                f"WHERE ticker=? AND date(as_of_date) BETWEEN ? AND ? "
                f"AND {fld} IS NOT NULL",
                (t, start_iso, end_iso),
            )
            per_field[fld] = cur.fetchone()[0]
        xbrl_coverage[t] = per_field
    out["xbrl_coverage"]   = xbrl_coverage
    out["present_xbrl"]    = present_xbrl

    return out


# ---------------------------------------------------------------------------
# Domain-specific deep dive: ohlcv.  Per-ticker date span confirms the
# warm-up window actually landed (analysts request lookback from tick T).
# ---------------------------------------------------------------------------
def deep_check_ohlcv(
    con:     sqlite3.Connection,
    tickers: list[str],
) -> dict[str, dict[str, object]]:
    """Per-ticker min/max bar date and bar count."""
    cur = con.cursor()
    out: dict[str, dict[str, object]] = {}
    for t in tickers:
        cur.execute(
            "SELECT MIN(date(ts)), MAX(date(ts)), COUNT(*) "
            "FROM ohlcv_bars WHERE ticker=?",
            (t,),
        )
        lo, hi, n = cur.fetchone()
        out[t] = {"min": lo, "max": hi, "count": n}
    return out


# ---------------------------------------------------------------------------
# Report formatter.  Pure presentation — no SQL, no failure logic.
# ---------------------------------------------------------------------------
def print_domain_table(all_findings: list[DomainFindings]) -> None:
    """Print the per-domain headline table."""

    _section("Per-domain summary")
    print(
        f"  {'domain':<20} {'total':>9} {'min PIT':>11} {'max PIT':>11} "
        f"{'missing tkrs':>13} {'silent-empty':>13} {'errored':>8}"
    )
    for f in all_findings:
        if not f.table_present:
            print(f"  {f.domain.name:<20}  <table not present in cache>")
            continue

        missing = sum(1 for v in f.per_ticker_in_window.values() if v == 0)
        print(
            f"  {f.domain.name:<20} {f.total_rows:>9d} "
            f"{(f.min_pit or '-'):>11} {(f.max_pit or '-'):>11} "
            f"{missing:>13d} {len(f.silent_empty_tickers):>13d} "
            f"{len(f.errored_tickers):>8d}"
        )


def print_ticker_matrix(
    all_findings: list[DomainFindings],
    tickers:      list[str],
) -> None:
    """Print the per-ticker × domain in-window count matrix."""

    _section("Per-ticker × domain in-window row counts")

    present = [f for f in all_findings if f.table_present]
    header  = " ".join(f"{f.domain.name[:11]:>11}" for f in present)
    print(f"  {'ticker':<8} {header}")
    for t in tickers:
        cells = " ".join(
            f"{f.per_ticker_in_window.get(t, 0):>11d}" for f in present
        )
        print(f"  {t:<8} {cells}")


def print_cache_runs(con: sqlite3.Connection, window_key: str) -> None:
    """Print the cache_runs status breakdown across every domain."""

    _section("cache_runs status by domain")
    cur = con.cursor()
    cur.execute(
        "SELECT domain, status, COUNT(*), COALESCE(SUM(rows_written), 0) "
        "FROM cache_runs WHERE window_key=? "
        "GROUP BY domain, status ORDER BY domain, status",
        (window_key,),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"  (no cache_runs rows for window_key={window_key!r})")
        return

    print(f"  {'domain':<20} {'status':<10} {'runs':>5}  {'rows_written':>12}")
    for dom, status, n, rows_written in rows:
        print(f"  {dom:<20} {status:<10} {n:>5d}  {rows_written:>12d}")


def print_insider_deep(deep: dict[str, int]) -> None:
    """Print the insider_trades EDGAR-fix verification."""

    _section("insider_trades fix verification (2026-05-18 EDGAR fix)")
    print(f"  rows with MISSING_TIMESTAMP filed_at:  {deep['missing_filed_at']}")
    print(f"  rows with numeric insider_name (Series.name leak):  {deep['numeric_insider_name']}")


def print_ohlcv_spans(ohl: dict[str, dict[str, object]]) -> None:
    """Print the per-ticker OHLCV date span."""

    _section("ohlcv per-ticker date span")
    for t, info in ohl.items():
        print(f"  {t:<8} {str(info['min']):>11} → {str(info['max']):>11}  ({info['count']} bars)")


def print_company_ratios_deep(
    deep:           dict,
    ohlcv_findings: DomainFindings,
    tickers:        list[str],
) -> None:
    """Print the company_ratios deep-dive — per-tick fill density + XBRL coverage.

    Cross-checks the per-ticker company_ratios in-window row count against
    the ohlcv in-window bar count.  Both share the NYSE trading-day
    cardinality, so any mismatch indicates the per-tick fill regressed
    relative to the price feed.
    """
    _section("company_ratios deep check (2026-05-19 pit_composite + per-tick fixes)")

    # ── Schema drift — model columns absent from the cache table ───────────
    # Flag prominently because a column missing from the table means the
    # write path silently drops that field on every snapshot.
    missing = deep.get("missing_columns") or []
    if missing:
        print(
            f"  SCHEMA DRIFT — {len(missing)} model column(s) absent from "
            f"company_ratios table:"
        )
        for col in missing:
            print(f"      {col}")
        print(
            "  (write_company_ratios silently drops these — backtest "
            "replays will never see them.)\n"
        )
    else:
        print("  Schema in sync with model — no missing columns.\n")

    # ── Per-ticker fill density vs ohlcv in-window cardinality ──────────────
    rows_pt    = deep["rows_per_ticker"]
    dates_pt   = deep["distinct_dates_per_ticker"]
    ohlcv_pt   = ohlcv_findings.per_ticker_in_window

    print("  Per-ticker fill density (rows / distinct dates) vs ohlcv in-window bars:")
    print(f"    {'ticker':<8} {'rows':>6} {'dates':>6} {'ohlcv':>6}  status")
    for t in tickers:
        n        = rows_pt.get(t, 0)
        n_dates  = dates_pt.get(t, 0)
        n_ohlcv  = ohlcv_pt.get(t, 0)

        # Three checks: rows == dates (no duplicate PIT-pinned snapshots),
        # rows == ohlcv bar count (one snapshot per trading day), and
        # rows > 0 (something landed at all).
        if n == 0:
            flag = "EMPTY"
        elif n != n_dates:
            flag = f"DUPLICATES ({n - n_dates} extra)"
        elif n != n_ohlcv:
            flag = f"MISMATCH (ohlcv has {n_ohlcv})"
        else:
            flag = "ok"
        print(f"    {t:<8} {n:>6d} {n_dates:>6d} {n_ohlcv:>6d}  {flag}")

    # ── Per-column null counts (in-window only) ──────────────────────────────
    # Render fields the schema actually has; absent ones show "—" so the
    # block stays readable next to the schema-drift section above.
    null_counts = deep["null_counts"]

    def _fmt_nulls(fld: str) -> str:
        """Format the null-count cell, marking columns absent from the table."""
        return f"{null_counts[fld]:>6d}" if fld in null_counts else "   —  "

    print("\n  Per-column null counts (in-window rows):")
    print("    XBRL-derived fields (expected sparse for ADRs / non-Dec FY):")
    for fld in _XBRL_RATIO_FIELDS:
        print(f"      {fld:<22} nulls: {_fmt_nulls(fld)}")

    print("    Price-derived fields (expected ~0 nulls):")
    for fld in _PRICE_FIELDS:
        print(f"      {fld:<22} nulls: {_fmt_nulls(fld)}")

    print(f"\n    peg                    nulls: {_fmt_nulls('peg')}  "
          f"(peg is intentionally always None — non-null = leak)")

    peg_nn = deep["peg_non_null"]
    if peg_nn is None:
        print("    peg non-null rowcount (whole table): n/a (column not in schema)")
    else:
        print(f"    peg non-null rowcount (whole table): {peg_nn}")

    # ── Per-ticker × XBRL field coverage matrix ──────────────────────────────
    cov          = deep["xbrl_coverage"]
    present_xbrl = deep.get("present_xbrl", [])

    print("\n  Per-ticker × XBRL-field non-null row counts:")
    if not present_xbrl:
        print("    (no XBRL columns present in schema — matrix omitted)")
    else:
        header_fields = " ".join(f"{fld[:11]:>11}" for fld in present_xbrl)
        print(f"    {'ticker':<8} {header_fields}")
        for t in tickers:
            cells = " ".join(
                f"{cov[t].get(fld, 0):>11d}" for fld in present_xbrl
            )
            print(f"    {t:<8} {cells}")


def print_future_bleed_check(
    all_findings: list[DomainFindings],
    end_iso:      str,
) -> None:
    """Print per-domain future-bleed counts (rows with PIT > window_end).

    Any non-zero count is interpretation-falsifying: at every tick inside
    the backtest window the analyst would gain visibility on information
    dated after the window even ends, which leaks into every signal.
    """
    _section(f"Future-bleed check (rows with PIT > window_end {end_iso})")

    any_bleed = False
    for f in all_findings:
        if not f.table_present:
            continue

        if f.future_bleed_rows == 0:
            print(f"  {f.domain.name:<20}  clean")
            continue

        any_bleed = True

        # Compact summary line + per-ticker breakdown indented under it.
        print(
            f"  {f.domain.name:<20}  BLEED — "
            f"{f.future_bleed_rows} row(s), latest PIT = {f.future_bleed_max_pit}"
        )
        for t, n in f.future_bleed_per_ticker.items():
            print(f"      {t:<8} +{n}")

    if not any_bleed:
        print("\n  All domains clean — no rows dated past window_end.")


def print_per_domain_details(all_findings: list[DomainFindings]) -> None:
    """Print the longer per-domain breakdown — skip details when the
    headline shows the domain is healthy and there is nothing to elaborate."""

    _section("Per-domain detail (skipped, errored, silent-empty)")
    for f in all_findings:
        if not f.table_present:
            continue

        missing  = [t for t, n in f.per_ticker_in_window.items() if n == 0]
        sub_msgs: list[str] = []

        if f.skipped_tickers:
            sub_msgs.append(f"skipped (no cache_runs row): {f.skipped_tickers}")
        if f.errored_tickers:
            sub_msgs.append(f"errored: {f.errored_tickers}")
        if f.error_examples:
            sub_msgs.append("first errors:")
            sub_msgs.extend(f"      {e}" for e in f.error_examples)
        if f.silent_empty_tickers:
            sub_msgs.append(
                f"silent-empty (ok+0 rows): {f.silent_empty_tickers}"
            )
        if missing:
            sub_msgs.append(f"tickers with 0 in-window rows: {missing}")

        if not sub_msgs:
            print(f"  {f.domain.name:<20}  OK")
            continue

        print(f"  {f.domain.name}:")
        for m in sub_msgs:
            if m.startswith("      "):
                print(m)
            else:
                print(f"    - {m}")


# ---------------------------------------------------------------------------
# Verdict — promotes findings into pass / warn lines.  Politician_trades is
# expected to be empty (the domain is disabled in the fetcher) so we treat
# it as informational only.
# ---------------------------------------------------------------------------
def render_verdict(
    all_findings:    list[DomainFindings],
    deep:            dict[str, int],
    ratios_deep:     dict,
    ohlcv_findings:  DomainFindings,
    tickers:         list[str],
) -> list[str]:
    """Return a list of WARN strings; empty list means PASS."""

    warns: list[str] = []

    if deep["missing_filed_at"] > 0:
        warns.append(
            f"{deep['missing_filed_at']} insider_trades rows still carry "
            f"MISSING_TIMESTAMP — refetch may not have re-pulled this domain"
        )
    if deep["numeric_insider_name"] > 0:
        warns.append(
            f"{deep['numeric_insider_name']} insider_trades rows still have "
            f"a numeric insider_name (Series.name leak)"
        )

    # ── company_ratios regressions ──────────────────────────────────────────
    # Schema drift is the highest-priority signal here: a column absent from
    # the cache means the store's write path silently drops the field on
    # every snapshot, so backtest replays will never see it regardless of
    # whether the live provider computes it correctly.
    missing_cols = ratios_deep.get("missing_columns") or []
    if missing_cols:
        warns.append(
            f"company_ratios: schema drift — {len(missing_cols)} model "
            f"column(s) absent from cache table: {missing_cols}.  "
            f"write_company_ratios silently drops these on every write."
        )

    # PEG must be 100% null after the 2026-05-19 fix — any non-null row means
    # someone re-introduced a wall-clock fallback (yf.Ticker.info["pegRatio"]
    # or similar).  Whole-table count, not just in-window — we want to catch
    # a regression even if it leaked outside the immediate window.  Skip if
    # the column is structurally absent (schema-drift covers that).
    peg_nn = ratios_deep["peg_non_null"]
    if peg_nn is not None and peg_nn > 0:
        warns.append(
            f"company_ratios: PEG leak regression — {peg_nn} "
            f"row(s) have non-null peg (post-fix expectation is 0)"
        )

    # Cross-check per-tick fill density against ohlcv bar counts — a healthy
    # per-tick fill produces exactly one ratios row per ohlcv bar per ticker.
    # Mismatches indicate the fill loop short-circuited (missing days) or
    # double-wrote (duplicate as_of_date values).
    rows_pt  = ratios_deep["rows_per_ticker"]
    ohlcv_pt = ohlcv_findings.per_ticker_in_window if ohlcv_findings.table_present else {}
    fill_mismatches = [
        t for t in tickers
        if rows_pt.get(t, 0) != ohlcv_pt.get(t, 0)
    ]
    if fill_mismatches:
        warns.append(
            f"company_ratios: per-tick fill mismatch with ohlcv for "
            f"{len(fill_mismatches)} ticker(s): {fill_mismatches}"
        )

    # Whole-pipeline regression — if every ticker is null for an XBRL field
    # the upstream concept selector or list-unwrap fix has regressed.  Per-
    # ticker sparsity is normal and is *not* flagged here (the printer's
    # coverage matrix shows that case clearly).  Only check XBRL columns
    # that actually exist; absent ones are already covered by schema-drift.
    cov          = ratios_deep["xbrl_coverage"]
    present_xbrl = ratios_deep.get("present_xbrl", [])
    for fld in present_xbrl:
        if all(cov[t].get(fld, 0) == 0 for t in tickers):
            warns.append(
                f"company_ratios: XBRL field {fld!r} is 100% null across all "
                f"tickers — pipeline regression?"
            )

    for f in all_findings:
        # Skip disabled domains — they're expected to be empty.
        if not f.domain.enabled:
            continue

        if not f.table_present:
            warns.append(f"{f.domain.name}: table not present in cache")
            continue

        if f.total_rows == 0:
            warns.append(f"{f.domain.name}: 0 rows stored — fetcher never wrote")
            continue

        if f.errored_tickers:
            warns.append(
                f"{f.domain.name}: {len(f.errored_tickers)} ticker(s) errored "
                f"in cache_runs: {f.errored_tickers}"
            )
        if f.silent_empty_tickers:
            warns.append(
                f"{f.domain.name}: {len(f.silent_empty_tickers)} silent-empty "
                f"ticker(s) (ok+0 rows_written): {f.silent_empty_tickers}"
            )
        if f.skipped_tickers:
            warns.append(
                f"{f.domain.name}: {len(f.skipped_tickers)} ticker(s) have no "
                f"cache_runs row at all: {f.skipped_tickers}"
            )

        # Future-bleed is the single highest-severity check — flag it
        # prominently so it isn't lost in the silent-empty noise.
        if f.future_bleed_rows > 0:
            warns.append(
                f"{f.domain.name}: FUTURE-BLEED — {f.future_bleed_rows} "
                f"row(s) dated past window_end (latest "
                f"{f.future_bleed_max_pit}); per-ticker "
                f"{f.future_bleed_per_ticker}"
            )

    return warns


# ---------------------------------------------------------------------------
# CLI entrypoint.
# ---------------------------------------------------------------------------
def main() -> int:
    """Parse args, run all audits, render the report, return exit code 0."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", required=True, help="Window key, e.g. svb-stress-2023-03")
    parser.add_argument("--cache-path", default=None, help="Override cache SQLite path")
    parser.add_argument("--watchlist",  default=None, help="Override watchlist file")
    parser.add_argument("--config-dir", default=None, help="Override config directory")
    args = parser.parse_args()

    repo_root  = Path(__file__).resolve().parents[1]
    config_dir = Path(args.config_dir) if args.config_dir else (repo_root / "config")

    from backtest.settings import load_backtest_settings_from
    settings  = load_backtest_settings_from(config_dir / "backtest_settings.json")
    windows   = _load_json(config_dir / "backtest_windows.json")
    watch_p   = Path(args.watchlist) if args.watchlist else (config_dir / "watchlist.json")
    watchlist = _load_json(watch_p)

    if args.window not in windows:
        print(
            f"window {args.window!r} not in backtest_windows.json "
            f"(have: {list(windows.keys())})",
            file=sys.stderr,
        )
        return 1

    start_iso = windows[args.window]["start"]
    end_iso   = windows[args.window]["end"]
    tickers   = watchlist["tickers"]

    cache_path = (
        Path(args.cache_path) if args.cache_path
        else (repo_root / settings.cache_path)
    )
    if not cache_path.exists():
        print(f"cache file not found: {cache_path}", file=sys.stderr)
        return 1

    print(f"\nCache audit — window {args.window} ({start_iso} → {end_iso})")
    print(f"  cache_path:  {cache_path}")
    print(f"  tickers:     {len(tickers)} ({', '.join(tickers)})")
    print("  disabled:    " + ", ".join(d.name for d in DOMAINS if not d.enabled))

    con = sqlite3.connect(str(cache_path))
    try:
        all_findings = [
            audit_domain(con, d, args.window, start_iso, end_iso, tickers)
            for d in DOMAINS
        ]

        print_domain_table(all_findings)
        print_ticker_matrix(all_findings, tickers)
        print_cache_runs(con, args.window)
        print_per_domain_details(all_findings)

        deep = deep_check_insider_trades(con)
        print_insider_deep(deep)

        ohl = deep_check_ohlcv(con, tickers)
        print_ohlcv_spans(ohl)

        # company_ratios deep check needs the ohlcv DomainFindings so it can
        # cross-check the per-tick fill density against the trading-day bar
        # count — find the entry by name rather than relying on list index.
        ohlcv_findings = next(f for f in all_findings if f.domain.name == "ohlcv")
        ratios_deep    = deep_check_company_ratios(con, tickers, start_iso, end_iso)
        print_company_ratios_deep(ratios_deep, ohlcv_findings, tickers)

        print_future_bleed_check(all_findings, end_iso)

        _section("Verdict")
        warns = render_verdict(all_findings, deep, ratios_deep, ohlcv_findings, tickers)
        if not warns:
            print("  PASS — cache looks ready for backtest replay.\n")
            return 0
        print("  WARNINGS:")
        for w in warns:
            print(f"    - {w}")
        print()
        return 0     # diagnostic, not a CI gate
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

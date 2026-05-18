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
    all_findings: list[DomainFindings],
    deep:         dict[str, int],
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

        print_future_bleed_check(all_findings, end_iso)

        _section("Verdict")
        warns = render_verdict(all_findings, deep)
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

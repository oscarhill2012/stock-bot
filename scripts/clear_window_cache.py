"""Delete every cache row that belongs to one backtest window.

Domain tables in the cache (``ohlcv_bars``, ``company_ratios``, ``filings``,
``news_articles``, ``insider_trades``, ``notable_holders``) do **not**
carry a ``window_key`` column — they store rows by ticker + PIT date and
those rows are shared across whichever windows want them.  This script
therefore identifies a window's rows by their PIT date falling inside the
window's *scope*: ``[window_start - lookback_floor, window_end +
bleed_ceiling]``.

- ``lookback_floor`` (default 90 days) is sized to cover the longest
  per-domain lookback the fetcher uses (insider window-span + 30 day
  analyst lookback, news 7 days, ohlcv warmup ~30 days).
- ``bleed_ceiling`` (default 365 days) catches any future-bleed rows that
  a buggy provider may have written past ``window_end``.

A pre-flight check warns if any *other* configured window's range
overlaps the deletion scope, so the operator can decide whether the
collateral damage is acceptable before running ``--apply``.

The ``cache_runs`` ledger is also pruned for the named window so a
follow-up refetch starts with a clean run history (and the audit script
won't see duplicate run entries).

Read-only by default — call with ``--apply`` to actually delete.

Usage
-----
    # Dry-run: just report counts that would be deleted.
    PYTHONPATH=src .venv/bin/python -m scripts.clear_window_cache \\
        --window baseline-2025-09

    # Execute the delete.
    PYTHONPATH=src .venv/bin/python -m scripts.clear_window_cache \\
        --window baseline-2025-09 --apply

    # Override the date buffers if the defaults don't fit the window.
    PYTHONPATH=src .venv/bin/python -m scripts.clear_window_cache \\
        --window baseline-2025-09 --lookback-days 120 --bleed-days 180
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Stdlib only — same posture as ``scripts/debug_cache_audit.py``.  The
# script must not depend on project modules so a regression elsewhere in
# the codebase can't break our recovery tool.
# ---------------------------------------------------------------------------
import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Domain registry — mirrors ``scripts/debug_cache_audit.py:DOMAINS`` so the
# two stay in lock-step.  Each entry names the table + the SQL expression
# that yields the PIT date for that table (this is what we filter on).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Domain:
    """One row in the delete script's domain registry."""

    name:     str        # cache_runs.domain value
    table:    str        # physical SQL table name
    pit_expr: str        # SQL expression returning the PIT date


DOMAINS: list[Domain] = [
    Domain("ohlcv",          "ohlcv_bars",      "date(ts)"),
    Domain("company_ratios", "company_ratios",  "date(as_of_date)"),
    Domain("filings",        "filings",         "date(filed_at)"),
    Domain("news",           "news_articles",   "date(published_at)"),
    Domain("insider_trades", "insider_trades",  "date(filed_at)"),
    Domain(
        "politician_trades",
        "politician_trades",
        "date(COALESCE(disclosure_date, transaction_date))",
    ),
    Domain("notable_holders", "notable_holders", "date(filed_at)"),
]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> dict:
    """Read and parse ``path`` as JSON; raise a helpful FileNotFoundError."""

    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    return json.loads(path.read_text())


def _parse_iso(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` string into a ``date``."""

    return datetime.strptime(value, "%Y-%m-%d").date()


def _section(title: str) -> None:
    """Print a section divider so the report scans nicely."""

    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


# ---------------------------------------------------------------------------
# Pre-flight: report any other configured windows whose ``[start, end]``
# overlaps our delete scope.  We never refuse — the operator decides.
# ---------------------------------------------------------------------------
def report_overlapping_windows(
    windows:        dict[str, dict],
    target_window:  str,
    scope_start:    date,
    scope_end:      date,
) -> None:
    """Print other windows whose configured range overlaps the delete scope."""

    overlaps: list[tuple[str, date, date]] = []
    for key, cfg in windows.items():
        if key == target_window:
            continue

        other_start = _parse_iso(cfg["start"])
        other_end   = _parse_iso(cfg["end"])

        # Standard interval-overlap test: two intervals [a,b] and [c,d]
        # overlap iff a <= d and c <= b.
        if other_start <= scope_end and scope_start <= other_end:
            overlaps.append((key, other_start, other_end))

    if not overlaps:
        print("  no other windows overlap the delete scope — safe to apply")
        return

    print("  WARNING: the following windows overlap the delete scope —")
    print("           rows shared with them will also be removed:")
    for key, s, e in overlaps:
        print(f"           - {key:<28} {s} → {e}")


# ---------------------------------------------------------------------------
# Per-domain dry-run / apply core.
# ---------------------------------------------------------------------------
def domain_row_count(
    cur:         sqlite3.Cursor,
    domain:      Domain,
    scope_start: date,
    scope_end:   date,
) -> int | None:
    """Return the row count in scope for one domain, or ``None`` if absent.

    ``None`` means the table does not exist (the domain has no cache
    schema in this DB) — we treat that as "nothing to do" rather than an
    error, since not every install fills every domain.
    """
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (domain.table,),
    )
    if cur.fetchone() is None:
        return None

    sql = (
        f"SELECT COUNT(*) FROM {domain.table} "                                # noqa: S608
        f"WHERE {domain.pit_expr} BETWEEN ? AND ?"
    )
    cur.execute(sql, (scope_start.isoformat(), scope_end.isoformat()))
    return cur.fetchone()[0]


def domain_delete(
    cur:         sqlite3.Cursor,
    domain:      Domain,
    scope_start: date,
    scope_end:   date,
) -> int | None:
    """Delete in-scope rows for one domain; return rows deleted, or ``None``."""

    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (domain.table,),
    )
    if cur.fetchone() is None:
        return None

    sql = (
        f"DELETE FROM {domain.table} "                                         # noqa: S608
        f"WHERE {domain.pit_expr} BETWEEN ? AND ?"
    )
    cur.execute(sql, (scope_start.isoformat(), scope_end.isoformat()))
    return cur.rowcount


# ---------------------------------------------------------------------------
# CLI entrypoint.
# ---------------------------------------------------------------------------
def main() -> int:
    """Parse args, report the delete scope, optionally apply the delete."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", required=True,
                        help="Window key from backtest_windows.json (e.g. baseline-2025-09)")
    parser.add_argument("--lookback-days", type=int, default=90,
                        help="Floor on PIT date = window_start - this (default 90)")
    parser.add_argument("--bleed-days", type=int, default=365,
                        help="Ceiling on PIT date = window_end + this (default 365)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually execute the deletes (default is dry-run)")
    parser.add_argument("--cache-path", default=None,
                        help="Override cache SQLite path")
    parser.add_argument("--config-dir", default=None,
                        help="Override config directory")
    args = parser.parse_args()

    # ── Resolve config + cache paths ───────────────────────────────────────
    repo_root  = Path(__file__).resolve().parents[1]
    config_dir = Path(args.config_dir) if args.config_dir else (repo_root / "config")

    # We import the settings loader lazily inside main so the module's
    # top-level remains stdlib-only (matching debug_cache_audit.py).
    from backtest.settings import load_backtest_settings_from
    settings = load_backtest_settings_from(config_dir / "backtest_settings.json")
    windows  = _load_json(config_dir / "backtest_windows.json")

    if args.window not in windows:
        print(
            f"window {args.window!r} not in backtest_windows.json "
            f"(have: {list(windows.keys())})",
            file=sys.stderr,
        )
        return 1

    window_start = _parse_iso(windows[args.window]["start"])
    window_end   = _parse_iso(windows[args.window]["end"])

    # Delete scope = [window_start - lookback, window_end + bleed].
    scope_start = window_start - timedelta(days=args.lookback_days)
    scope_end   = window_end   + timedelta(days=args.bleed_days)

    cache_path = (
        Path(args.cache_path) if args.cache_path
        else (repo_root / settings.cache_path)
    )
    if not cache_path.exists():
        print(f"cache file not found: {cache_path}", file=sys.stderr)
        return 1

    # ── Header ─────────────────────────────────────────────────────────────
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\nClear-window-cache — window {args.window} [{mode}]")
    print(f"  window range:   {window_start} → {window_end}")
    print(f"  delete scope:   {scope_start} → {scope_end}")
    print(f"    (window_start - {args.lookback_days}d   →   window_end + {args.bleed_days}d)")
    print(f"  cache_path:     {cache_path}")

    # ── Pre-flight: warn about overlapping windows ────────────────────────
    _section("Pre-flight: other configured windows in scope")
    report_overlapping_windows(windows, args.window, scope_start, scope_end)

    # ── Pass one: per-domain counts so the operator can sanity-check ──────
    _section("Rows in scope (per domain)")
    con = sqlite3.connect(str(cache_path))
    try:
        cur = con.cursor()

        # cache_runs is keyed by window_key directly — no date math needed.
        cur.execute(
            "SELECT COUNT(*) FROM cache_runs WHERE window_key=?",
            (args.window,),
        )
        cache_runs_count = cur.fetchone()[0]

        print(f"  {'cache_runs':<20} {cache_runs_count:>10d}  (window_key = {args.window!r})")

        domain_counts: dict[str, int | None] = {}
        for d in DOMAINS:
            n = domain_row_count(cur, d, scope_start, scope_end)
            domain_counts[d.name] = n
            label = "<no table>" if n is None else f"{n:>10d}"
            print(f"  {d.name:<20} {label}")

        # Sum across domains for headline.
        total_rows = sum(v for v in domain_counts.values() if v is not None)
        total_rows += cache_runs_count
        print(f"\n  TOTAL rows to delete:        {total_rows}")

        # ── Pass two: if --apply, run the deletes inside a single txn ─────
        if not args.apply:
            _section("Dry-run — no changes made")
            print("  Re-run with --apply to execute the deletes above.\n")
            return 0

        _section("Applying deletes")
        cur.execute("BEGIN")

        # cache_runs first so the run ledger is gone even if a later
        # domain delete fails midway and the txn rolls back.
        cur.execute("DELETE FROM cache_runs WHERE window_key=?", (args.window,))
        print(f"  cache_runs:           deleted {cur.rowcount} row(s)")

        for d in DOMAINS:
            deleted = domain_delete(cur, d, scope_start, scope_end)
            if deleted is None:
                print(f"  {d.name:<20}  (no table — skipped)")
            else:
                print(f"  {d.name:<20}  deleted {deleted} row(s)")

        con.commit()
        print("\n  COMMITTED — cache delete complete.\n")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

"""One-shot EDGAR Form 4 inspector.

Stands alone (no project imports) — same pattern as `scripts/api_smoke.py`.
Calls `Company(ticker).get_filings(form="4", filing_date=...)` directly and
dumps everything `filing.obj()` exposes so we can see WHY some rows arrive
without a parseable `filed_at` (the 253 dropped insider rows on the first
SVB backfill, 2026-05-18).

Usage:
    PYTHONPATH=src .venv/bin/python -m scripts.debug_edgar_form4 \\
        --ticker AVGO --as-of 2023-04-07 --lookback 62

Output is purely diagnostic — printed, never written to the cache.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stdlib + thin deps only.  We deliberately do NOT import the project's
# data layer — we want to see the raw upstream values before any coercion.
# ---------------------------------------------------------------------------
import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Minimal .env loader — copy of the helper in api_smoke.py so the script is
# self-contained.
# ---------------------------------------------------------------------------
def _load_dotenv(path: Path) -> dict[str, str]:
    """Return env vars parsed from `path`; no os.environ side effects."""

    if not path.exists():
        return {}

    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split("#", 1)[0].strip()
        out[key.strip()] = value
    return out


# ---------------------------------------------------------------------------
# Pretty-printers for the various edgartools shapes (pandas DataFrame, plain
# list, SimpleNamespace, edgartools Footnotes …).  All callers tolerate None.
# ---------------------------------------------------------------------------
def _describe_table(table: Any) -> str:
    """Return a short human description of a transaction-table object."""

    if table is None:
        return "None"

    # pandas DataFrame — guard against the fact that pandas Index objects
    # raise ValueError on ``bool(idx)`` so the usual ``or []`` idiom blows up.
    if hasattr(table, "columns") and hasattr(table, "iterrows"):
        try:
            n = len(table)
        except Exception:
            n = "?"
        try:
            cols = list(table.columns)
        except Exception:
            cols = []
        return f"DataFrame(rows={n}, cols={cols})"

    # plain iterable
    try:
        rows = list(table)
        return f"list(rows={len(rows)})"
    except TypeError:
        return f"{type(table).__name__}(?)"


def _dump_table_rows(label: str, table: Any) -> None:
    """Print every row of a transaction table verbosely."""

    print(f"    {label}: {_describe_table(table)}")
    if table is None:
        return

    # pandas path — iterate with iterrows so we see the row index (which is
    # the suspect source of the numbered "insider=1, 2, 3..." pattern).
    if hasattr(table, "iterrows"):
        for idx, row in table.iterrows():
            try:
                items = {k: row[k] for k in row.index}
            except Exception:
                items = {"<repr>": repr(row)[:200]}
            print(f"      [idx={idx!r}] {items}")
        return

    # generic iterable path
    try:
        for i, row in enumerate(table):
            print(f"      [{i}] {row!r}"[:300])
    except TypeError:
        print(f"      <non-iterable: {type(table).__name__}>")


def _safe_attrs(obj: Any, names: list[str]) -> dict[str, Any]:
    """Pull the named attrs off `obj`, swallowing any access errors."""

    out: dict[str, Any] = {}
    for name in names:
        try:
            out[name] = getattr(obj, name, None)
        except Exception as exc:                       # noqa: BLE001
            out[name] = f"<error: {type(exc).__name__}: {exc}>"
    return out


# ---------------------------------------------------------------------------
# Main probe — replicates the live provider's `_list_form4_filings` call and
# then walks every filing the same way `_fetch_and_parse_one` does, but
# dumps the raw values instead of building a Form4Bundle.
# ---------------------------------------------------------------------------
def probe(ticker: str, as_of: date, lookback: int, env: dict[str, str]) -> int:
    """Fetch every Form 4 for ``ticker`` in the window and pretty-print
    each parsed form's attributes + table rows.

    Returns a process exit code (0 on completion regardless of findings —
    this is a diagnostic, not a gate).
    """

    try:
        from edgar import Company, set_identity
    except ImportError:
        print("edgar package not installed", file=sys.stderr)
        return 1

    identity = env.get("EDGAR_IDENTITY") or "StockBot smoke-test stockbot@example.com"
    set_identity(identity)

    upper = as_of
    lower = as_of - timedelta(days=lookback)

    print(f"\nEDGAR Form 4 probe for {ticker}")
    print(f"  window: {lower.isoformat()} → {upper.isoformat()}  ({lookback} days)\n")

    company = Company(ticker)
    raw_filings = company.get_filings(
        form="4", filing_date=f"{lower.isoformat()}:{upper.isoformat()}",
    )
    filings = list(raw_filings.head(50))

    print(f"  {len(filings)} filings returned (cap=50)\n")

    # Per-filing summary counters so we can see which filings produced
    # missing filed_at vs numbered insider rows.
    missing_filed_at = 0
    numbered_insider = 0
    total_rows = 0

    for i, filing in enumerate(filings, 1):
        # Outer shell attrs — the bits exposed by the filings list, BEFORE
        # the heavy `.obj()` parse.  These are usually well-behaved and
        # serve as a sanity baseline.
        shell = _safe_attrs(
            filing,
            ["accession_no", "form", "filing_date", "filer", "company", "cik"],
        )
        print(f"[{i:>2}] shell: {shell}")

        try:
            form4 = filing.obj()
        except Exception as exc:                       # noqa: BLE001
            print(f"      filing.obj() raised: {type(exc).__name__}: {exc}")
            continue

        # The attributes our provider reads from the parsed form4 object.
        form_attrs = _safe_attrs(
            form4,
            [
                "filed_at",
                "ticker",
                "form_type",
                "insider_name",
                "position",
                "equity_swap_or_planned_sale",
            ],
        )
        print(f"      form4 attrs: {form_attrs}")

        if form_attrs.get("filed_at") in (None, "", "None"):
            missing_filed_at += 1

        # Dump every table the parser reads.  This is where the row-level
        # truth lives — and where the suspect numbered-insider rows must
        # come from if they exist at all.
        for label in ("common_stock_purchases", "common_stock_sales", "derivative_securities"):
            tbl = getattr(form4, label, None)
            _dump_table_rows(label, tbl)

            # Heuristic counter — if the row index is an int and we then
            # later fall back to it as the insider name, count it.
            if tbl is not None and hasattr(tbl, "iterrows"):
                for idx, row in tbl.iterrows():
                    total_rows += 1
                    # The parser falls back to row.name when "insider_name"
                    # is missing; row.name on a pandas Series equals the
                    # index, which for default RangeIndex is an int.
                    has_named_insider = False
                    for key in ("insider_name", "InsiderName", "name"):
                        try:
                            val = row[key] if key in row.index else None
                        except Exception:
                            val = None
                        if val:
                            has_named_insider = True
                            break
                    if not has_named_insider and isinstance(idx, int):
                        numbered_insider += 1

        # Show what the footnote map looks like so we can correlate row
        # footnote IDs against text resolution later if needed.
        fmap = getattr(form4, "footnotes", None)
        if fmap:
            try:
                fmap_repr = dict(fmap)
            except Exception:
                fmap_repr = repr(fmap)[:200]
            print(f"    footnotes: {fmap_repr}")

        print()

    print("=" * 78)
    print(f"  filings probed:           {len(filings)}")
    print(f"  filings missing filed_at: {missing_filed_at}")
    print(f"  total rows seen:          {total_rows}")
    print(f"  rows likely to fall back")
    print(f"    to numeric insider idx: {numbered_insider}")
    print("=" * 78)
    print()

    # --------------------------------------------------------------------
    # Round-trip through the live provider to confirm both fixes hold —
    # `filed_at` populated, `insider_name` carries the real reporter name
    # for every row (no numeric fallbacks).  This imports the project, so
    # we do it after the raw probe to keep the diagnostic above clean.
    # --------------------------------------------------------------------
    import asyncio
    from datetime import time as _time
    from datetime import timezone as _tz

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from data.providers.insider_trades.edgar import fetch as _fetch  # noqa: E402

    as_of_dt = datetime.combine(as_of, _time(16, 0), tzinfo=_tz.utc)

    bundle = asyncio.run(_fetch(ticker, as_of=as_of_dt, lookback_days=lookback))
    print(f"Provider round-trip ({ticker} via fetch):")
    print(f"  trades:      {len(bundle.trades)}")
    print(f"  derivatives: {len(bundle.derivatives)}")

    bad_filed = sum(1 for t in bundle.trades if t.filed_at.year == 1)
    bad_name  = sum(1 for t in bundle.trades if t.insider_name.isdigit())
    print(f"  trades with MISSING_TIMESTAMP filed_at: {bad_filed}")
    print(f"  trades with numeric insider_name:       {bad_name}")
    for t in bundle.trades[:10]:
        print(
            f"    {t.filed_at.date()} {t.insider_name:<30s} "
            f"{t.side:<5s} {t.shares:>8.0f} @ {t.price_per_share}"
        )

    return 0


def main() -> int:
    """CLI entrypoint — see module docstring for usage."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True, help="Issuer symbol, e.g. AVGO")
    parser.add_argument(
        "--as-of",
        required=True,
        help="Upper bound for the filing window, ISO date (e.g. 2023-04-07)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=62,
        help="Days to look back from --as-of (default: 62 = SVB window + 30)",
    )
    args = parser.parse_args()

    as_of = datetime.fromisoformat(args.as_of).date()
    root = Path(__file__).resolve().parents[1]
    env = _load_dotenv(root / ".env")

    return probe(args.ticker, as_of, args.lookback, env)


if __name__ == "__main__":
    sys.exit(main())

"""Smoke-test every upstream the providers-and-silent-gaps-v1 plan depends on.

One-shot script.  Probes each upstream with a representative call and prints
`[OK]`, `[SKIP]` (missing credential), or `[FAIL]` with a short reason.
Exits with code 0 if every probe is OK or SKIP, non-zero if any probe
hard-fails.

Usage:
    PYTHONPATH=src .venv/bin/python -m scripts.api_smoke

Credentials are read from `.env` at the project root.  No project modules
are imported — the goal is to verify upstream availability in isolation
from the provider/registry layer.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stdlib + thin deps only — keep this script standalone so it works even if
# the project's data layer is mid-refactor.
# ---------------------------------------------------------------------------
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx


# ---------------------------------------------------------------------------
# Minimal .env loader — we do not want a python-dotenv hard dependency for
# a smoke script.  Parses `KEY=value` lines; comments and blank lines are
# ignored; anything after `#` is treated as an inline comment and dropped
# (so values must not contain literal `#` characters — regenerate any
# secret that includes one).
# ---------------------------------------------------------------------------
def _load_dotenv(path: Path) -> dict[str, str]:
    """Return a dict of env vars parsed from `path` (no os.environ side
    effects).  Returns {} if the file is missing."""

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
        # Strip inline comments and surrounding whitespace.  Users add
        # trailing comments like `KEY=abc# https://example.com` to .env
        # and we must not let the URL leak into the value.
        value = value.split("#", 1)[0].strip()
        out[key.strip()] = value
    return out


# ---------------------------------------------------------------------------
# Per-probe result type + small helpers for the printed summary.
# ---------------------------------------------------------------------------
@dataclass
class ProbeResult:
    """Outcome of one upstream probe.

    `status` is one of {"OK", "SKIP", "FAIL"}.  `note` is a short
    free-text explanation (≤120 chars).  `payload` carries any sampled
    response fragment that's useful for the printed summary.
    """

    name: str
    status: str
    note: str = ""
    payload: dict = field(default_factory=dict)


def _line(result: ProbeResult) -> str:
    """One-line summary suitable for terminal output."""

    icon = {"OK": "[ OK ]", "SKIP": "[SKIP]", "FAIL": "[FAIL]"}.get(
        result.status, "[????]"
    )
    return f"{icon}  {result.name:<28} {result.note}"


def _safe(fn: Callable[[], ProbeResult]) -> ProbeResult:
    """Run a probe and convert any uncaught exception into a FAIL result
    so one broken probe never crashes the whole script."""

    try:
        return fn()
    except Exception as exc:                       # noqa: BLE001
        name = getattr(fn, "__name__", "probe")
        tb_line = traceback.format_exc().splitlines()[-1][:160]
        return ProbeResult(
            name=name,
            status="FAIL",
            note=f"{type(exc).__name__}: {tb_line}",
        )


# Defaults applied to every HTTP probe — keep short so the script can't hang.
_TIMEOUT = httpx.Timeout(15.0)


# ===========================================================================
# Probe implementations — one per upstream.  Each returns a ProbeResult.
# ===========================================================================


def probe_finnhub_earnings(env: dict[str, str]) -> ProbeResult:
    """GET /calendar/earnings — Row #6 Finnhub earnings provider.

    Two-pass probe:
      1. Recent-history window (past 120 days) — confirms historical data is
         available and inspects the observed field names.
      2. Future window (today → today+90d) — asserts the API does NOT
         auto-filter unannounced events (epsActual=null rows must exist),
         which validates the PIT dual-filter requirement documented in the
         Phase -1 verification pass (2026-05-17).
    """

    name = "finnhub /calendar/earnings"
    token = env.get("FINNHUB_API_KEY", "").strip()
    if not token or "your_" in token:
        return ProbeResult(name, "SKIP", "FINNHUB_API_KEY not set")

    today = date.today()

    # --- Pass 1: recent-history check (past 120 days) ---
    start = today - timedelta(days=120)
    params = {
        "symbol": "AAPL",
        "from": start.isoformat(),
        "to": today.isoformat(),
        "token": token,
    }
    r = httpx.get(
        "https://finnhub.io/api/v1/calendar/earnings",
        params=params,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    rows = (r.json() or {}).get("earningsCalendar") or []
    if not rows:
        return ProbeResult(name, "FAIL", "empty earningsCalendar array")
    sample = rows[0]

    # --- Pass 2: future window — verify PIT behaviour ---
    # The API must include rows with epsActual=null for future (unannounced)
    # dates.  If it filters them out, the Task 3.1 PIT-dual-filter assumption
    # breaks and must be re-verified before that code lands.
    future_params = {
        "symbol": "AAPL",
        "from": today.isoformat(),
        "to": (today + timedelta(days=90)).isoformat(),
        "token": token,
    }
    future_r = httpx.get(
        "https://finnhub.io/api/v1/calendar/earnings",
        params=future_params,
        timeout=_TIMEOUT,
    )
    future_r.raise_for_status()
    future_cal = (future_r.json() or {}).get("earningsCalendar") or []

    # At least one row should have epsActual=None or "" (unannounced quarter).
    unannounced = [row for row in future_cal if row.get("epsActual") in (None, "")]
    if not unannounced:
        return ProbeResult(
            name, "FAIL",
            "no unannounced future rows in 90d window — "
            "API behaviour may have changed; re-verify Task 3.1 PIT filter",
        )

    return ProbeResult(
        name, "OK",
        f"{len(rows)} rows; latest {sample.get('date')} EPS={sample.get('epsActual')}; "
        f"{len(unannounced)} unannounced in next-90d (PIT check OK)",
        payload={"sample": sample},
    )


def probe_stocktwits(env: dict[str, str]) -> ProbeResult:
    """GET /streams/symbol/AAPL.json — Row #13.

    StockTwits sits behind Cloudflare and 403s on httpx's default
    User-Agent (`python-httpx/<ver>`).  A browser-shaped UA gets through,
    which is what the eventual provider implementation will also need.
    """

    name = "stocktwits streams"
    r = httpx.get(
        "https://api.stocktwits.com/api/2/streams/symbol/AAPL.json",
        timeout=_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
        # StockTwits returns JSON unauthenticated for public streams; no key.
    )
    if r.status_code != 200:
        return ProbeResult(name, "FAIL", f"HTTP {r.status_code}")

    payload = r.json() or {}
    msgs = payload.get("messages") or []
    if not msgs:
        return ProbeResult(name, "FAIL", "empty messages array")

    rl = r.headers.get("X-RateLimit-Remaining", "?")
    return ProbeResult(
        name, "OK",
        f"{len(msgs)} messages; X-RateLimit-Remaining={rl}",
    )


def probe_yfinance_analyst(env: dict[str, str]) -> ProbeResult:
    """yfinance .analyst_price_targets — Row #10."""

    name = "yfinance analyst targets"
    try:
        import yfinance as yf
    except ImportError:
        return ProbeResult(name, "FAIL", "yfinance not installed")

    targets = yf.Ticker("AAPL").analyst_price_targets
    if not isinstance(targets, dict) or not targets:
        return ProbeResult(name, "FAIL", "no targets returned")

    keys = ", ".join(sorted(targets.keys())[:6])
    return ProbeResult(name, "OK", f"keys: {keys}")


def probe_yfinance_bulk(env: dict[str, str]) -> ProbeResult:
    """Bulk Ticker download — feeds the reference_prices populator (Phase 5)."""

    name = "yfinance bulk download"
    try:
        import yfinance as yf
    except ImportError:
        return ProbeResult(name, "FAIL", "yfinance not installed")

    df = yf.download(
        ["SPY", "XLK"], period="5d", interval="1d",
        auto_adjust=False, progress=False, threads=True,
    )
    if df is None or df.empty:
        return ProbeResult(name, "FAIL", "empty dataframe")

    return ProbeResult(
        name, "OK",
        f"{len(df)} rows; cols={len(df.columns)}; index={df.index[0].date()}->{df.index[-1].date()}",
    )


def probe_edgartools_8k(env: dict[str, str]) -> ProbeResult:
    """edgartools 8-K body fetch — Phase 4 filings/edgar extension."""

    name = "edgartools 8-K body"
    try:
        from edgar import Company, set_identity
    except ImportError:
        return ProbeResult(name, "FAIL", "edgar package not installed")

    # SEC asks for a User-Agent style identity; reuse whatever the project
    # already uses if set, else a generic placeholder so the request goes
    # through.
    identity = env.get("EDGAR_IDENTITY") or "StockBot smoke-test stockbot@example.com"
    set_identity(identity)

    company = Company("AAPL")
    filings = company.get_filings(form="8-K").head(1)
    if len(filings) == 0:
        return ProbeResult(name, "FAIL", "no 8-K filings returned")

    filing = filings[0]
    body = (filing.text() or "")[:200]
    items = list(getattr(filing, "items", []) or [])
    if not body:
        return ProbeResult(name, "FAIL", "8-K body text empty")

    return ProbeResult(
        name, "OK",
        f"body chars={len(body)}; items={items or 'none'}",
    )


def probe_stock_watcher(env: dict[str, str]) -> ProbeResult:
    """Probe both Senate and House Stock Watcher aggregate JSONs.

    Findings (May 2026):
    - The S3 buckets the gist referenced
      (`senate-stock-watcher-data.s3-us-west-2.amazonaws.com`,
      `house-stock-watcher-data.s3-us-west-2.amazonaws.com`) now return
      403 AccessDenied — the bucket policies were removed.
    - `senatestockwatcher.com` and `housestockwatcher.com` no longer
      resolve.
    - The GitHub repo `timothycarambat/senate-stock-watcher-data` still
      serves senate data via raw.githubusercontent.com but the repo was
      last pushed in 2021 (stale, no longer updated).
    - There is no live House equivalent.

    This probe records the state honestly: senate-raw is live-but-stale,
    house has no working source.  The plan must re-scope Row #14 onto a
    different provider (Quiver already exists in
    src/data/providers/politician_trades/quiver.py).
    """

    name = "stock-watcher"
    senate_raw = (
        "https://raw.githubusercontent.com/timothycarambat/"
        "senate-stock-watcher-data/master/aggregate/all_transactions.json"
    )
    house_s3 = (
        "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com"
        "/data/all_transactions.json"
    )

    senate_r = httpx.head(senate_raw, timeout=_TIMEOUT, follow_redirects=True)
    house_r  = httpx.head(house_s3,  timeout=_TIMEOUT, follow_redirects=True)

    senate_ok = senate_r.status_code == 200
    house_ok  = house_r.status_code  == 200

    if senate_ok and house_ok:
        return ProbeResult(name, "OK", "both senate and house URLs HEAD 200")

    # Any non-200 means the spec's politician_trades free-tier story is
    # broken — surface it as FAIL so we re-scope onto Quiver.
    return ProbeResult(
        name, "FAIL",
        f"senate-raw={senate_r.status_code} (last pushed 2021, stale); "
        f"house-S3={house_r.status_code} (bucket private). "
        f"Re-scope politician_trades onto Quiver (already implemented).",
        payload={"senate_url": senate_raw, "house_url": house_s3},
    )


# ===========================================================================
# Main — run every probe, print a summary, exit non-zero on any FAIL.
# ===========================================================================


def main() -> int:
    """Run all probes and print a summary table.  Returns the process
    exit code (0 = all OK or SKIP; 1 = at least one FAIL)."""

    root = Path(__file__).resolve().parents[1]
    env = _load_dotenv(root / ".env")

    probes: list[Callable[[dict[str, str]], ProbeResult]] = [
        probe_finnhub_earnings,
        probe_stocktwits,
        probe_yfinance_analyst,
        probe_yfinance_bulk,
        probe_edgartools_8k,
        probe_stock_watcher,
    ]

    print("\nUpstream smoke probes — providers-and-silent-gaps-v1\n")
    started = time.time()
    results: list[ProbeResult] = []
    for probe in probes:
        # Bind the env dict into a no-arg callable so _safe can name the
        # probe via __name__ when it builds a FAIL fallback.
        def bound(p=probe):
            return p(env)

        bound.__name__ = probe.__name__
        result = _safe(bound)
        results.append(result)
        print(_line(result), flush=True)

    elapsed = time.time() - started
    ok = sum(1 for r in results if r.status == "OK")
    skip = sum(1 for r in results if r.status == "SKIP")
    fail = sum(1 for r in results if r.status == "FAIL")

    print(f"\n  {ok} OK | {skip} SKIP | {fail} FAIL | {elapsed:.1f}s elapsed\n")

    if fail:
        print(
            "FAILs above must be resolved before the matching Phase 3 "
            "provider work can begin.  SKIPs indicate missing credentials "
            "— add to .env and re-run.\n"
        )
        return 1

    if skip:
        print(
            "All reachable upstreams OK; SKIPs are credential-gated and "
            "don't block other phases.\n"
        )
    else:
        print("All upstreams reachable — Phase 3 work is unblocked.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

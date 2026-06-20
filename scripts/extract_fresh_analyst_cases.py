"""Extract fresh (non-cache-hit) LLM-analyst cases from a backtest run for review.

Purpose
-------
The analyst predictive-power scoreboard in ``report/metrics.md`` scores *every*
verdict against realised forward returns — but it cannot tell a freshly-computed
LLM verdict from a cache hit, so a single confidently-wrong verdict that the
report cache replays across many ticks is counted once per tick and its error is
amplified.  This tool re-derives, from the run's own artefacts, the subset of
verdicts that were genuine fresh LLM calls (a ``report_cache_miss`` in the
observability log) and bundles each with:

- the full context the analyst saw that tick (filings / ratios / insider data
  for ``fundamental``; the news articles for ``news``), and
- the full structured verdict *and* the LLM's prose ``report`` (summary +
  drivers), and
- realised forward returns at +1d / +5d / +20d plus the per-tick
  cross-sectional excess (same base-price / forward-bar methodology as
  ``backtest.scoreboard``), and
- a signed mismatch score = lean-direction × forward excess, so the worst
  "confident and wrong" calls sort to the top.

Output layout (under ``--out``, default ``<run>/analysis``)::

    <analyst>/manifest.json          ranked summary of every fresh case
    <analyst>/cases/<tick>__<tkr>.json   full context + report for selected cases

Only ``fundamental`` and ``news`` are emitted — ``technical`` is a deterministic
heuristic with no LLM call and no cache, so the fresh/cache distinction and the
"context the agent reasoned over" framing do not apply to it.

Invocation::

    PYTHONPATH=src .venv/bin/python -m scripts.extract_fresh_analyst_cases \
        --run-dir backtests/baseline-2025-09/runs/analysts-eval-iter-6 \
        --window  baseline-2025-09
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from backtest.cache.store import CachedDataStore
from backtest.reporting import _forward_close
from backtest.settings import cache_path_for_window, get_backtest_settings

# The two LLM-driven, cache-backed analysts.  ``technical`` is excluded by
# design (deterministic heuristic — see module docstring).
LLM_ANALYSTS = ("fundamental", "news")

# Forward horizons to score against, in calendar days.
HORIZONS = (1, 5, 20)

# How many cases to dump full context for, per analyst, at each tail.
TOP_WORST = 30
TOP_BEST  = 12


def _lean_position(lean: str) -> int:
    """Map a verdict lean string to a signed position for the mismatch score.

    Parameters
    ----------
    lean:
        The verdict ``lean`` field — one of ``bullish`` / ``bearish`` /
        ``neutral`` (other strings are treated as neutral).

    Returns
    -------
    int
        ``+1`` for bullish, ``-1`` for bearish, ``0`` otherwise.
    """
    return {"bullish": 1, "bearish": -1}.get(lean, 0)


def _build_fresh_set(logs_dir: Path) -> set[tuple[str, str, str]]:
    """Scan the observability logs for genuine fresh LLM calls.

    A fresh call is recorded as a ``report_cache_miss`` event whose ``extra``
    carries the ``analyst`` and ``ticker``; the surrounding log file is named
    per tick and embeds the ``tick_id``.

    Parameters
    ----------
    logs_dir:
        The run's ``obs/logs`` directory.

    Returns
    -------
    set[tuple[str, str, str]]
        Set of ``(analyst, ticker, tick_id)`` triples that were cache misses.
    """
    fresh: set[tuple[str, str, str]] = set()

    for log_file in sorted(logs_dir.glob("*.json")):
        doc      = json.loads(log_file.read_text())
        tick_id  = doc.get("tick_id", "")

        for event in doc.get("events", []):
            if event.get("message") != "report_cache_miss":
                continue

            extra = event.get("extra", {})
            fresh.add((extra.get("analyst"), extra.get("ticker"), tick_id))

    return fresh


def _tick_id_for_trace(trace_path: Path, run_name: str) -> tuple[str, datetime, str]:
    """Reconstruct ``(tick_id, as_of, phase)`` from a trace filename.

    Trace files are named like ``2025-09-02T13-30-00p00-00.json`` (no phase
    suffix); the observability ``tick_id`` is
    ``<run_name>-<as_of_iso>-<phase>``.  Phase is inferred from the wall-clock
    hour — the 13:30Z tick is the NYSE *open* tick, the 20:00Z tick is *close*.

    Parameters
    ----------
    trace_path:
        Path to the per-tick trace JSON.
    run_name:
        The run identifier (the run directory's name), which prefixes tick_id.

    Returns
    -------
    tuple[str, datetime, str]
        The reconstructed ``tick_id``, the ``as_of`` datetime, and the phase.
    """
    stem = trace_path.stem  # e.g. "2025-09-02T13-30-00p00-00"

    # Reverse the filesystem-safe encoding back into an ISO-8601 timestamp:
    #   "T13-30-00p00-00"  ->  "T13:30:00+00:00"
    iso = stem.replace("p", "+")
    head, sep, tz = iso.partition("+")
    date_part, _, time_part = head.partition("T")
    time_part = time_part.replace("-", ":")
    tz        = tz.replace("-", ":")
    iso_full  = f"{date_part}T{time_part}+{tz}"

    as_of = datetime.fromisoformat(iso_full)
    phase = "open" if as_of.hour < 17 else "close"
    tick_id = f"{run_name}-{iso_full}-{phase}"

    return tick_id, as_of, phase


def _forward_returns(
    cache: CachedDataStore,
    ticker: str,
    as_of_date: date,
) -> dict[int, float | None]:
    """Compute realised forward returns for one ticker at the standard horizons.

    Uses the same base-price (the as-of-date close) and forward-bar lookup as
    ``backtest.scoreboard`` so the numbers reconcile with the run's own
    scoreboard.

    Parameters
    ----------
    cache:
        Golden-cache store for the window.
    ticker:
        Ticker symbol.
    as_of_date:
        The verdict's calendar date — the base for the return.

    Returns
    -------
    dict[int, float | None]
        Horizon (days) -> forward return, or ``None`` where no bar is available
        (e.g. window-edge ticks whose +20d bar is past the cache).
    """
    base_bars = cache.read_ohlcv(ticker, as_of_date, as_of_date)
    if not base_bars:
        return {h: None for h in HORIZONS}

    base_close = base_bars[-1].close
    out: dict[int, float | None] = {}

    for h in HORIZONS:
        fwd = _forward_close(cache, ticker, as_of_date, h)
        out[h] = None if fwd is None else (fwd - base_close) / base_close

    return out


def main() -> None:
    """Entry point — parse args, extract fresh cases, write manifests + bundles."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--window",  required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output root (default: <run-dir>/analysis).")
    args = parser.parse_args()

    run_dir  = args.run_dir
    run_name = run_dir.name
    out_root = args.out or (run_dir / "analysis")

    settings = get_backtest_settings()
    cache    = CachedDataStore(cache_path_for_window(settings, args.window))

    fresh_set = _build_fresh_set(run_dir / "obs" / "logs")

    # Pass 1 — collect every fresh verdict with its context, verdict, report and
    # raw (absolute) forward returns.  We need the full cross-section per tick
    # before we can compute excess returns, so accumulate first.
    #
    # records[analyst] -> list of per-case dicts
    # tick_returns[(tick_id, h)] -> list of all tickers' fwd returns that tick
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tick_returns: dict[tuple[str, int], list[float]] = defaultdict(list)

    for trace_path in sorted((run_dir / "traces").glob("*.json")):
        trace = json.loads(trace_path.read_text())
        tick_id, as_of, _phase = _tick_id_for_trace(trace_path, run_name)
        as_of_date = as_of.date()

        for analyst in LLM_ANALYSTS:
            context_by_ticker = trace.get(f"01_fetch_{analyst}", {}).get("data", {})
            verdicts          = trace.get(f"02_{analyst}_verdict", {}).get("data", [])

            for verdict in verdicts:
                ticker = verdict.get("ticker")

                # Fresh-only filter — skip cache hits entirely.
                if (analyst, ticker, tick_id) not in fresh_set:
                    continue

                fwd = _forward_returns(cache, ticker, as_of_date)

                # Feed the cross-sectional pools (used for excess in pass 2).
                for h in HORIZONS:
                    if fwd[h] is not None:
                        tick_returns[(tick_id, h)].append(fwd[h])

                records[analyst].append({
                    "ticker":          ticker,
                    "tick_id":         tick_id,
                    "as_of":           as_of.isoformat(),
                    "phase":           _phase,
                    "lean":            verdict.get("lean"),
                    "position":        _lean_position(verdict.get("lean", "")),
                    "magnitude":       verdict.get("magnitude"),
                    "confidence":      verdict.get("confidence"),
                    "key_factors":     verdict.get("key_factors"),
                    "is_no_data":      verdict.get("is_no_data"),
                    "report":          verdict.get("report"),
                    "fwd_return":      fwd,
                    "context":         context_by_ticker.get(ticker, {}),
                })

    # Pass 2 — attach cross-sectional excess + signed mismatch, rank, and write.
    for analyst, cases in records.items():
        for case in cases:
            tick_id = case["tick_id"]
            excess: dict[int, float | None] = {}
            signed: dict[int, float | None] = {}

            for h in HORIZONS:
                r = case["fwd_return"][h]
                pool = tick_returns[(tick_id, h)]

                if r is None or not pool:
                    excess[h] = None
                    signed[h] = None
                    continue

                # Excess vs the per-tick cross-sectional mean (relative call
                # quality — matches the scoreboard's framing).
                ex        = r - mean(pool)
                excess[h] = ex
                signed[h] = case["position"] * ex

            case["fwd_excess"]    = excess
            case["signed_excess"] = signed

        # Primary ranking key: the +20d signed excess (most negative = the most
        # confident *wrong-direction* call).  Cases without a +20d bar sort last.
        def _sort_key(c: dict[str, Any]) -> tuple[int, float]:
            s20 = c["signed_excess"].get(20)
            has = 0 if s20 is None else 1
            return (-has, s20 if s20 is not None else 0.0)

        cases.sort(key=_sort_key)

        analyst_dir = out_root / analyst
        cases_dir   = analyst_dir / "cases"
        cases_dir.mkdir(parents=True, exist_ok=True)

        # Manifest — compact, every fresh case, no bulky context/report bodies.
        manifest = []
        for c in cases:
            manifest.append({
                "ticker":        c["ticker"],
                "as_of":         c["as_of"],
                "phase":         c["phase"],
                "lean":          c["lean"],
                "confidence":    c["confidence"],
                "magnitude":     c["magnitude"],
                "key_factors":   c["key_factors"],
                "fwd_return":    c["fwd_return"],
                "fwd_excess":    c["fwd_excess"],
                "signed_excess": c["signed_excess"],
                "report_summary": (c.get("report") or {}).get("summary")
                                   if isinstance(c.get("report"), dict) else None,
            })

        (analyst_dir / "manifest.json").write_text(
            json.dumps({
                "analyst":      analyst,
                "run":          run_name,
                "n_fresh":      len(cases),
                "horizons":     list(HORIZONS),
                "ranking":      "ascending signed_excess[+20d] — worst (confident & wrong) first",
                "cases":        manifest,
            }, indent=1)
        )

        # Full-context bundles for the worst tail (and a best-tail control set).
        selected = cases[:TOP_WORST] + cases[-TOP_BEST:]
        seen: set[str] = set()

        for c in selected:
            stem = f"{c['as_of'].replace(':', '-')}__{c['ticker']}"
            if stem in seen:
                continue
            seen.add(stem)
            (cases_dir / f"{stem}.json").write_text(json.dumps(c, indent=1))

        print(f"{analyst}: {len(cases)} fresh cases, "
              f"{len(seen)} full bundles -> {analyst_dir}")


if __name__ == "__main__":
    main()

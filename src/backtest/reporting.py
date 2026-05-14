"""End-of-window reporting: equity curve, metrics, forward-return backfill.

Reads ``PortfolioSnapshotRow`` from the run's ``db.sqlite`` and produces
``report/equity_curve.png`` and ``report/metrics.md``.

The forward-return backfill walks ``decisions/*.json`` and patches each file
in place with +1d / +5d / +20d returns from the cache — the supervision signal
a future RAG retriever or self-improvement loop will want.

SPY baseline approach
---------------------
``PortfolioSnapshotRow`` already carries ``spy_price`` and ``spy_value_if_held``
(written by ``PortfolioSnapshotWriter`` from the live pipeline).  We use those
columns directly rather than fetching SPY data from ``yfinance`` or the cache
store at report-generation time.  This means:

- No network call at report time (the pipeline may run in a sandboxed environment).
- Perfect alignment: the SPY series is anchored to exactly the same ticks as
  the bot series.
- If ``spy_price`` is uniformly zero (e.g. early runs before the pipeline
  populated the field), the SPY overlay is silently omitted and a note is
  written to ``metrics.md``.

Adaptation note vs. plan spec
------------------------------
The plan referenced ``total_value`` and ``taken_at`` on ``PortfolioSnapshotRow``.
The actual model (``src/orchestrator/persistence.py``) uses ``bot_total_value``
and ``recorded_at`` instead.  This file uses the real field names.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import matplotlib

# Use a non-interactive backend so rendering works in CI / headless environments.
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sqlalchemy import select
from sqlalchemy.orm import Session

from backtest.cache.store import CachedDataStore
from orchestrator.persistence import PortfolioSnapshotRow, make_engine

logger = logging.getLogger(__name__)


# ── Public entry point ─────────────────────────────────────────────────────────

def report(run_dir: Path, settings: dict) -> None:
    """Generate the full end-of-window report for a completed run.

    Writes ``report/equity_curve.png`` and ``report/metrics.md``, then
    backfills ``forward_returns`` into every decision snapshot under
    ``decisions/``.

    Parameters
    ----------
    run_dir:
        Root directory for the run (contains ``db.sqlite``, ``decisions/``,
        ``manifest.json``).
    settings:
        Parsed contents of ``config/backtest_settings.json``.  Expected keys:
        ``cache_path`` and ``forward_return_horizons_days``.
    """
    run_dir    = Path(run_dir)
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── Load portfolio snapshots from the run's DB ─────────────────────────
    db_url = f"sqlite:///{run_dir / 'db.sqlite'}"
    engine = make_engine(db_url)

    with Session(engine) as session:
        rows = session.execute(
            select(PortfolioSnapshotRow)
            .order_by(PortfolioSnapshotRow.recorded_at)
        ).scalars().all()

        # Build lightweight series to avoid holding ORM objects after session closes.
        # Each element: (recorded_at, bot_total_value, spy_price, spy_value_if_held)
        snapshots: list[tuple[datetime, float, float, float]] = [
            (
                r.recorded_at,
                float(r.bot_total_value),
                float(r.spy_price),
                float(r.spy_value_if_held),
            )
            for r in rows
        ]

    engine.dispose()

    if not snapshots:
        logger.warning(
            "no portfolio snapshots in %s — report generation skipped", run_dir
        )
        return

    # ── Render equity curve PNG ────────────────────────────────────────────
    compute_equity_curve(
        snapshots=snapshots,
        outpath=report_dir / "equity_curve.png",
    )

    # ── Write metrics Markdown ─────────────────────────────────────────────
    compute_metrics(
        snapshots=snapshots,
        outpath=report_dir / "metrics.md",
    )

    # ── Backfill forward returns into decision snapshots ───────────────────
    cache          = CachedDataStore(Path(settings["cache_path"]))
    horizons: list[int] = settings["forward_return_horizons_days"]
    backfill_forward_returns(
        decisions_dir=run_dir / "decisions",
        cache=cache,
        horizons_days=horizons,
    )


# ── Equity curve ───────────────────────────────────────────────────────────────

def compute_equity_curve(
    snapshots: list[tuple[datetime, float, float, float]],
    outpath: Path,
    *,
    baseline_ticker: str = "SPY",
) -> None:
    """Render a bot-vs-SPY equity curve PNG and save it to ``outpath``.

    The curve is normalised to 100 at the first tick so both series start at
    the same anchor and the chart shows relative performance rather than
    absolute dollar values.

    Parameters
    ----------
    snapshots:
        Ordered list of ``(recorded_at, bot_total_value, spy_price,
        spy_value_if_held)`` tuples, ascending in time.
    outpath:
        Destination path for the PNG file.  Parent directories must exist.
    baseline_ticker:
        Label used in the chart legend for the SPY series (default ``"SPY"``).
    """
    timestamps    = [t for t, *_ in snapshots]
    bot_values    = [v for _, v, _, _ in snapshots]
    spy_prices    = [p for _, _, p, _ in snapshots]

    # Normalise to 100 at the anchor tick.
    anchor_bot = bot_values[0]
    anchor_spy = spy_prices[0]

    if anchor_bot > 0:
        bot_norm = [v / anchor_bot * 100.0 for v in bot_values]
    else:
        # Degenerate: no starting capital; fall back to raw values.
        bot_norm = bot_values

    has_spy = anchor_spy > 0
    if has_spy:
        spy_norm = [p / anchor_spy * 100.0 for p in spy_prices]

    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(timestamps, bot_norm, label="Bot", linewidth=1.5)

    if has_spy:
        ax.plot(timestamps, spy_norm, label=baseline_ticker, linewidth=1.5, linestyle="--")

    ax.set_title("Backtest equity curve (normalised to 100)")
    ax.set_xlabel("Tick timestamp")
    ax.set_ylabel("Normalised value")
    ax.legend()
    fig.tight_layout()

    fig.savefig(outpath, dpi=150)
    plt.close(fig)

    logger.info("equity curve saved to %s", outpath)


# ── Metrics ────────────────────────────────────────────────────────────────────

def compute_metrics(
    snapshots: list[tuple[datetime, float, float, float]],
    outpath: Path,
) -> None:
    """Compute performance metrics and write a Markdown file to ``outpath``.

    Metrics computed
    ----------------
    - **Total return**: ``(end_value - start_value) / start_value``
    - **Annualised Sharpe ratio** (252-day basis, using the per-tick return
      series; note: if ticks are twice-daily, this is not a strict daily Sharpe
      but still informative as a relative measure).
    - **Max drawdown**: largest peak-to-trough percentage decline.
    - **vs-SPY delta**: bot total-return minus SPY total-return over the window.
    - **Win rate**: fraction of ticks where bot outperformed SPY.
    - **Fill count**: not available from snapshots alone; written as N/A here
      (the decision log count is available separately via the decisions/ dir).

    Parameters
    ----------
    snapshots:
        Ordered list of ``(recorded_at, bot_total_value, spy_price,
        spy_value_if_held)`` tuples.
    outpath:
        Destination Markdown file path.
    """
    bot_values = [v for _, v, _, _ in snapshots]
    spy_prices = [p for _, _, p, _ in snapshots]

    # ── Total return ───────────────────────────────────────────────────────
    start_v = bot_values[0]
    end_v   = bot_values[-1]
    total_return = (end_v - start_v) / start_v if start_v else float("nan")

    # ── Per-tick returns for Sharpe and win-rate ───────────────────────────
    bot_rets: list[float] = []
    spy_rets: list[float] = []
    win_ticks = 0

    for (_, v0, p0, _), (_, v1, p1, _) in zip(snapshots, snapshots[1:]):
        br = (v1 - v0) / v0 if v0 else 0.0
        sr = (p1 - p0) / p0 if p0 else 0.0
        bot_rets.append(br)
        spy_rets.append(sr)
        if br > sr:
            win_ticks += 1

    win_rate = win_ticks / len(bot_rets) if bot_rets else float("nan")

    # ── Annualised Sharpe (252-day basis) ──────────────────────────────────
    # Returns here are per-tick.  The bot runs twice per trading day (morning
    # and afternoon ticks), so there are 252 * 2 = 504 ticks per year.
    # Annualisation factor = sqrt(ticks_per_year) = sqrt(504).
    # Using sqrt(252) would overstate Sharpe by a factor of sqrt(2) ≈ 1.41.
    # Using population std (ddof=0) to match the baselines/spy.py approach.
    _TICKS_PER_YEAR = 252 * 2  # two ticks per trading day
    if len(bot_rets) >= 2:
        mean_r = statistics.mean(bot_rets)
        std_r  = statistics.pstdev(bot_rets)
        sharpe = (mean_r / std_r * (_TICKS_PER_YEAR ** 0.5)) if std_r > 0 else float("nan")
    else:
        sharpe = float("nan")

    # ── Max drawdown ───────────────────────────────────────────────────────
    peak    = bot_values[0]
    max_dd  = 0.0
    for v in bot_values:
        if v > peak:
            peak = v
        if peak > 0:
            drawdown = (v - peak) / peak
            if drawdown < max_dd:
                max_dd = drawdown

    # ── vs-SPY delta ───────────────────────────────────────────────────────
    spy_start = spy_prices[0]
    spy_end   = spy_prices[-1]
    has_spy   = spy_start > 0 and spy_end > 0

    if has_spy:
        spy_return     = (spy_end - spy_start) / spy_start
        vs_spy_delta   = total_return - spy_return
        spy_return_str = f"{spy_return:+.2%}"
        vs_spy_str     = f"{vs_spy_delta:+.2%}"
    else:
        spy_return_str = "N/A (SPY data unavailable in snapshots)"
        vs_spy_str     = "N/A"

    # ── Format total_return / Sharpe / max_dd safely ──────────────────────
    def _fmt_pct(v: float, fallback: str = "N/A") -> str:
        """Format a float as a percentage, or return ``fallback`` if NaN."""
        import math
        return fallback if math.isnan(v) else f"{v:+.2%}"

    def _fmt_float(v: float, fallback: str = "N/A") -> str:
        """Format a float to 2 d.p., or return ``fallback`` if NaN."""
        import math
        return fallback if math.isnan(v) else f"{v:.2f}"

    # ── Write Markdown ─────────────────────────────────────────────────────
    lines = [
        "# Backtest metrics",
        "",
        f"- **Total return (bot):** {_fmt_pct(total_return)}",
        f"- **SPY return (window):** {spy_return_str}",
        f"- **vs-SPY delta:** {vs_spy_str}",
        f"- **Annualised Sharpe (504-tick/yr basis):** {_fmt_float(sharpe)}",
        f"- **Max drawdown:** {_fmt_pct(max_dd)}",
        f"- **Win rate (ticks beating SPY):** {_fmt_pct(win_rate)}",
        f"- **Ticks recorded:** {len(snapshots)}",
        "",
        "_Win rate = fraction of ticks where bot return > SPY return._",
        "_Sharpe is computed on per-tick returns (2 ticks/day), annualised with sqrt(252)._",
        "_Fill count is available via `decisions/` directory file count._",
    ]
    outpath.write_text("\n".join(lines) + "\n")
    logger.info("metrics written to %s", outpath)


# ── Forward-return backfill ────────────────────────────────────────────────────

def backfill_forward_returns(
    decisions_dir: Path,
    cache: CachedDataStore,
    horizons_days: list[int],
) -> None:
    """Patch ``forward_returns`` into every decision snapshot JSON file.

    For each horizon ``h`` in ``horizons_days`` (+1d, +5d, +20d), the function
    looks up the first available OHLCV close on or after ``entry_date + h`` days
    using the cache store.  It scans up to 4 calendar days forward to skip
    weekends and market holidays where no bar is recorded.

    The ``forward_returns`` field is patched in-place:

    .. code-block:: json

        "forward_returns": {"+1d": 0.023, "+5d": null, "+20d": -0.011}

    A ``null`` value means no bar was found in the cache at that horizon (e.g.
    the window end was too recent, or data was not fetched).

    Files that fail to parse, or whose required fields are missing, are skipped
    with a warning rather than raising.

    Parameters
    ----------
    decisions_dir:
        Directory containing ``*.json`` decision snapshot files.
    cache:
        Open ``CachedDataStore`` instance for OHLCV lookups.
    horizons_days:
        List of integer day offsets, e.g. ``[1, 5, 20]``.
    """
    if not decisions_dir.exists():
        logger.debug("decisions dir %s does not exist — skipping backfill", decisions_dir)
        return

    decision_files = sorted(decisions_dir.glob("*.json"))
    if not decision_files:
        logger.debug("no decision files in %s — nothing to backfill", decisions_dir)
        return

    logger.info(
        "backfilling forward returns for %d decision files (horizons: %s)",
        len(decision_files),
        horizons_days,
    )

    for path in decision_files:
        try:
            _backfill_one(path, cache, horizons_days)
        except Exception:
            logger.exception("forward-return backfill failed for %s", path)


def _backfill_one(
    path: Path,
    cache: CachedDataStore,
    horizons_days: list[int],
) -> None:
    """Patch one decision snapshot file with forward returns.

    Parameters
    ----------
    path:
        Filesystem path to the decision JSON file.
    cache:
        Open ``CachedDataStore`` for OHLCV lookups.
    horizons_days:
        Integer day offsets to compute (e.g. ``[1, 5, 20]``).
    """
    raw      = path.read_text()
    snapshot = json.loads(raw)

    ticker      = snapshot.get("ticker")
    entry_price = snapshot.get("execution", {}).get("fill_price")
    tick_as_of  = snapshot.get("tick", {}).get("as_of")

    # Skip files that are missing the fields we need for the calculation.
    if not ticker or entry_price is None or tick_as_of is None:
        logger.debug("skipping %s — missing ticker/fill_price/as_of", path.name)
        return

    entry_price = float(entry_price)
    entry_date  = _parse_date(tick_as_of)

    forwards: dict[str, float | None] = {}

    for h in horizons_days:
        target_date = entry_date + timedelta(days=h)

        # Scan up to 4 extra calendar days to cover weekends and holidays where
        # no bar was recorded.  Return the first bar found.
        scan_end = target_date + timedelta(days=4)
        bars = cache.read_ohlcv(ticker, target_date, scan_end)

        if bars:
            exit_close      = bars[0].close
            fwd_return      = (exit_close - entry_price) / entry_price
            forwards[f"+{h}d"] = fwd_return
        else:
            # No bar available at this horizon — data may not have been fetched
            # far enough past the window end.
            forwards[f"+{h}d"] = None

    snapshot["forward_returns"] = forwards

    # Write atomically via a sibling temp file then os.replace().
    # This guards against partial writes corrupting the decision JSON if the
    # process is interrupted mid-write (e.g. SIGKILL, disk full).
    new_text = json.dumps(snapshot, indent=2, default=str)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(new_text)
    import os
    os.replace(tmp_path, path)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _parse_date(as_of: str) -> date:
    """Parse an ISO datetime string (with or without 'Z' suffix) to a ``date``.

    Parameters
    ----------
    as_of:
        ISO 8601 datetime string, e.g. ``"2023-03-13T13:30:00Z"``.

    Returns
    -------
    date
        The calendar date component of the parsed datetime.
    """
    # Python 3.10 fromisoformat does not accept the 'Z' suffix — replace it.
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()

"""End-of-window reporting: equity curve, metrics, forward-return backfill.

Reads ``PortfolioSnapshotRow`` from the run's ``db.sqlite`` and produces
``report/equity_curve.png`` and ``report/metrics.md``.  The forward-return
backfill walks ``decisions/*.json`` and patches each file in place with +1d /
+5d / +20d returns from the cache — the supervision signal a future RAG
retriever wants.

Adaptation notes vs plan:
- ``PortfolioSnapshotRow`` uses ``recorded_at`` (not ``taken_at``) and
  ``bot_total_value`` (not ``total_value``).  Both names are adapted here.
- ``spy.py`` calls the live yfinance API — excluded to satisfy the
  "no external API calls" constraint.  The equity curve is portfolio-only.
  A SPY overlay can be added in Phase I once a cached SPY series is available.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering for CI / nightly cron
import matplotlib.pyplot as plt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backtest.cache.store import CachedDataStore
from orchestrator.persistence import PortfolioSnapshotRow

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def report(run_dir: Path, settings: dict) -> None:
    """Generate ``report/equity_curve.png`` and ``report/metrics.md``; backfill forwards.

    Reads portfolio snapshots from the run's ``db.sqlite``, writes an equity
    curve PNG and a Markdown metrics file, then walks ``decisions/`` to
    backfill forward returns from the golden cache.

    Parameters
    ----------
    run_dir:
        Root directory for the run (contains ``db.sqlite``, ``decisions/``, etc.).
    settings:
        Parsed contents of ``config/backtest_settings.json``.  Required keys:
        ``cache_path`` and ``forward_return_horizons_days``.
    """
    run_dir = Path(run_dir)
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── load portfolio snapshots from the run DB ──────────────────────────────
    engine = create_engine(f"sqlite:///{run_dir / 'db.sqlite'}", future=True)
    with Session(engine) as s:
        rows = s.execute(
            select(PortfolioSnapshotRow)
            .order_by(PortfolioSnapshotRow.recorded_at),
        ).scalars().all()

        # Build (timestamp, total_value) pairs using actual column names.
        equity = [(r.recorded_at, float(r.bot_total_value)) for r in rows]

    if not equity:
        logger.warning("no portfolio snapshots in %s — skipping report", run_dir)
        return

    _write_equity_curve(equity, report_dir / "equity_curve.png")
    _write_metrics(equity, report_dir / "metrics.md")

    # ── forward-return backfill ───────────────────────────────────────────────
    cache = CachedDataStore(Path(settings["cache_path"]))
    horizons = settings["forward_return_horizons_days"]
    _backfill_forward_returns(run_dir / "decisions", cache, horizons)


# ── Private helpers ───────────────────────────────────────────────────────────

def _write_equity_curve(
    series: list[tuple[datetime, float]], outpath: Path,
) -> None:
    """Render a portfolio equity curve PNG to ``outpath``.

    Parameters
    ----------
    series:
        Ordered list of (timestamp, portfolio_value) pairs.
    outpath:
        Destination file path (e.g. ``report/equity_curve.png``).
    """
    xs = [t for t, _ in series]
    ys = [v for _, v in series]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(xs, ys, label="Portfolio")
    ax.set_xlabel("Time")
    ax.set_ylabel("Portfolio value ($)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outpath)
    plt.close(fig)


def _write_metrics(
    series: list[tuple[datetime, float]], outpath: Path,
) -> None:
    """Compute performance metrics and write a Markdown report to ``outpath``.

    Metrics computed:
    - Total return as a percentage of starting value.
    - Annualised Sharpe ratio (assumes 252 trading days per year).
    - Max drawdown (largest peak-to-trough decline as a fraction).
    - Tick count.

    Parameters
    ----------
    series:
        Ordered list of (timestamp, portfolio_value) pairs.
    outpath:
        Destination file path (e.g. ``report/metrics.md``).
    """
    start_v = series[0][1]
    end_v   = series[-1][1]
    total_return = (end_v - start_v) / start_v

    # ── Sharpe ───────────────────────────────────────────────────────────────
    # Per-tick returns: assumes ticks are evenly spaced.
    rets = []
    for (_, v0), (_, v1) in zip(series, series[1:]):
        if v0 != 0:
            rets.append((v1 - v0) / v0)

    if len(rets) >= 2 and statistics.pstdev(rets) > 0:
        sharpe = (statistics.mean(rets) / statistics.pstdev(rets)) * (252 ** 0.5)
    else:
        sharpe = float("nan")

    # ── Max drawdown ──────────────────────────────────────────────────────────
    peak   = series[0][1]
    max_dd = 0.0
    for _, v in series:
        peak   = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak)

    outpath.write_text(
        "# Backtest metrics\n\n"
        f"- Total return: **{total_return:+.2%}**\n"
        f"- Sharpe (annualised, 252d): **{sharpe:.2f}**\n"
        f"- Max drawdown: **{max_dd:+.2%}**\n"
        f"- Ticks recorded: **{len(series)}**\n",
        encoding="utf-8",
    )


def _backfill_forward_returns(
    decisions_dir: Path,
    cache: CachedDataStore,
    horizons_days: list[int],
) -> None:
    """Patch ``forward_returns`` into every decision JSON in ``decisions_dir``.

    For each ``*.json`` file in ``decisions_dir``, looks up the closing price
    at each horizon offset from the decision's entry date using the golden
    cache.  Writes the return fractions (or ``None`` if no bar available) back
    into the file in place.

    Parameters
    ----------
    decisions_dir:
        Directory containing decision snapshot JSON files.
    cache:
        The golden ``CachedDataStore`` to query for OHLCV data.
    horizons_days:
        List of forward-horizon offsets in calendar days (e.g. ``[1, 5, 20]``).
    """
    if not decisions_dir.exists():
        return

    for path in decisions_dir.glob("*.json"):
        try:
            snapshot    = json.loads(path.read_text(encoding="utf-8"))
            ticker      = snapshot["ticker"]
            entry_price = snapshot["execution"].get("fill_price")
            tick_as_of  = snapshot["tick"].get("as_of")

            # Skip decisions without a fill price or timestamp.
            if entry_price is None or tick_as_of is None:
                continue

            entry_date = _parse_date(tick_as_of)

            forwards: dict[str, float | None] = {}
            for h in horizons_days:
                target = entry_date + timedelta(days=h)
                # Look up to 4 calendar days forward to skip weekends / holidays.
                bars = cache.read_ohlcv(ticker, target, target + timedelta(days=4))
                if not bars:
                    forwards[f"+{h}d"] = None
                    continue
                # Use the first available bar's close as the horizon price.
                forwards[f"+{h}d"] = (bars[0].close - entry_price) / entry_price

            snapshot["forward_returns"] = forwards
            path.write_text(
                json.dumps(snapshot, indent=2, default=str),
                encoding="utf-8",
            )

        except Exception:
            logger.exception("forward-return backfill failed for %s", path)


def _parse_date(as_of: str) -> date:
    """Parse an ISO-8601 datetime string into a ``date``.

    Handles the 'Z' UTC suffix (replaced with '+00:00' for Python < 3.11
    compatibility), offset-aware strings, and naive ISO strings.

    Parameters
    ----------
    as_of:
        ISO datetime string (e.g. ``"2023-03-06T09:30:00Z"``).

    Returns
    -------
    date
        The calendar date portion of the parsed datetime.
    """
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()

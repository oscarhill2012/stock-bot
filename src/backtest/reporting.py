"""End-of-window reporting: equity curve, metrics, forward-return backfill.

Reads ``PortfolioSnapshotRow`` from the run's ``db.sqlite`` and produces
``report/equity_curve.png`` and ``report/metrics.md``.  The forward-return
backfill walks ``decisions/*.json`` and patches each file in place with +1d /
+5d / +20d returns from the cache — the supervision signal a future RAG
retriever wants.

Spec §end-of-window: metrics.md must include:
  - total return
  - annualised Sharpe ratio (252-day basis)
  - max drawdown
  - vs-SPY delta (bot_total_return − spy_total_return)
  - win rate (winning closed trades / total closed trades)
  - total Fill count

Adaptation notes vs plan:
- ``PortfolioSnapshotRow`` uses ``recorded_at`` (not ``taken_at``) and
  ``bot_total_value`` (not ``total_value``).  Both names are adapted here.
- vs-SPY delta: reads SPY OHLCV from the golden cache via
  ``CachedDataStore.read_ohlcv("SPY", start, end)``.  If SPY is not in the
  cache (user did not fetch it), the metric is written as ``N/A`` with a
  hint to re-run ``backtest_fetch`` with SPY in the watchlist.
- win rate + fill count: queried from ``TradeLogRow`` in the run DB.
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
from orchestrator.persistence import PortfolioSnapshotRow, TradeLogRow

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

    # ── load portfolio snapshots and trade log from the run DB ───────────────
    engine = create_engine(f"sqlite:///{run_dir / 'db.sqlite'}", future=True)
    with Session(engine) as s:
        rows = s.execute(
            select(PortfolioSnapshotRow)
            .order_by(PortfolioSnapshotRow.recorded_at),
        ).scalars().all()

        # Build (timestamp, total_value) pairs using actual column names.
        equity = [(r.recorded_at, float(r.bot_total_value)) for r in rows]

        # Load closed trades for win rate and fill count.
        trade_rows = s.execute(select(TradeLogRow)).scalars().all()

    if not equity:
        logger.warning("no portfolio snapshots in %s — skipping report", run_dir)
        return

    # ── win rate and fill count from closed trades ────────────────────────────
    fill_count = len(trade_rows)
    winning    = sum(1 for t in trade_rows if t.pnl_dollar > 0)
    win_rate   = (winning / fill_count) if fill_count > 0 else float("nan")

    # ── vs-SPY delta from the golden cache ────────────────────────────────────
    # Attempt to compute SPY buy-and-hold return over the same window as the
    # portfolio snapshots.  Falls back to a descriptive N/A string if SPY is not
    # in the cache, so the run does not crash when the user hasn't fetched SPY.
    cache          = CachedDataStore(Path(settings["cache_path"]))
    vs_spy_delta   = _compute_vs_spy_delta(equity, cache)

    _write_equity_curve(equity, report_dir / "equity_curve.png")
    _write_metrics(
        equity,
        report_dir / "metrics.md",
        fill_count=fill_count,
        win_rate=win_rate,
        vs_spy_delta=vs_spy_delta,
    )

    # ── forward-return backfill ───────────────────────────────────────────────
    # ``cache`` was already opened above for the SPY delta calculation; reuse it.
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
    series: list[tuple[datetime, float]],
    outpath: Path,
    *,
    fill_count: int = 0,
    win_rate: float = float("nan"),
    vs_spy_delta: float | str = "N/A — SPY not in cache (run backtest_fetch with SPY)",
) -> None:
    """Compute performance metrics and write a Markdown report to ``outpath``.

    Metrics written (spec §end-of-window):
    - Total return as a percentage of starting value.
    - Annualised Sharpe ratio (assumes 252 trading days per year).
    - Max drawdown (largest peak-to-trough decline as a fraction).
    - vs-SPY delta (bot total return − SPY total return over the same window).
    - Win rate (winning closed trades / total closed trades).
    - Total Fill count (number of closed trade-log entries).
    - Tick count (number of portfolio snapshots).

    Parameters
    ----------
    series:
        Ordered list of (timestamp, portfolio_value) pairs.
    outpath:
        Destination file path (e.g. ``report/metrics.md``).
    fill_count:
        Total number of closed trades (Fills) in the run's trade log.
    win_rate:
        Fraction of closed trades that were profitable (pnl_dollar > 0).
        ``float("nan")`` when no trades were closed.
    vs_spy_delta:
        Bot total return minus SPY total return, expressed as a fraction
        (e.g. ``0.05`` = 5 pp outperformance).  Pass a descriptive string
        when SPY data is unavailable rather than crashing.
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

    # ── Format optional fields ────────────────────────────────────────────────
    # vs-SPY delta may be a float (computed from cache) or a descriptive string
    # (when SPY is absent from the cache).
    if isinstance(vs_spy_delta, float):
        vs_spy_str = f"**{vs_spy_delta:+.2%}**"
    else:
        vs_spy_str = f"_{vs_spy_delta}_"

    win_rate_str = f"**{win_rate:.1%}**" if not (isinstance(win_rate, float) and win_rate != win_rate) else "**N/A** (no closed trades)"

    outpath.write_text(
        "# Backtest metrics\n\n"
        f"- Total return: **{total_return:+.2%}**\n"
        f"- Sharpe (annualised, 252d): **{sharpe:.2f}**\n"
        f"- Max drawdown: **{max_dd:+.2%}**\n"
        f"- vs-SPY delta: {vs_spy_str}\n"
        f"- Win rate: {win_rate_str}\n"
        f"- Total fills: **{fill_count}**\n"
        f"- Ticks recorded: **{len(series)}**\n",
        encoding="utf-8",
    )


def _compute_vs_spy_delta(
    equity: list[tuple[datetime, float]],
    cache: CachedDataStore,
) -> float | str:
    """Compute the bot's outperformance vs SPY buy-and-hold over the same window.

    Reads SPY OHLCV from the golden cache and computes SPY total return over the
    window spanned by ``equity``.  Returns ``bot_total_return − spy_total_return``
    as a fraction (e.g. ``0.05`` = 5 pp outperformance).

    If SPY is not in the cache, returns a descriptive string rather than crashing
    so the metrics file is still written and the run is not aborted.  The string
    instructs the user to re-run the fetcher with SPY in the watchlist.

    Parameters
    ----------
    equity:
        Ordered list of (timestamp, portfolio_value) pairs covering the run window.
    cache:
        The golden ``CachedDataStore`` to query for SPY OHLCV data.

    Returns
    -------
    float | str
        A float delta when SPY data is available; a descriptive string otherwise.
    """
    if not equity:
        return "N/A — no portfolio snapshots"

    # Derive the window from the equity series timestamps.
    start_dt, start_v = equity[0]
    end_dt,   end_v   = equity[-1]
    start_date = start_dt.date() if hasattr(start_dt, "date") else date.fromisoformat(str(start_dt)[:10])
    end_date   = end_dt.date()   if hasattr(end_dt,   "date") else date.fromisoformat(str(end_dt)[:10])

    try:
        spy_bars = cache.read_ohlcv("SPY", start_date, end_date)
    except Exception:
        logger.exception("Failed to read SPY OHLCV from cache")
        return "N/A — error reading SPY from cache"

    if not spy_bars:
        return "N/A — SPY not in cache (run backtest_fetch with SPY)"

    # SPY buy-and-hold return: use first bar's open and last bar's close.
    spy_start_price = spy_bars[0].open
    spy_end_price   = spy_bars[-1].close

    if spy_start_price <= 0:
        return "N/A — SPY start price is zero"

    spy_total_return = (spy_end_price - spy_start_price) / spy_start_price
    bot_total_return = (end_v - start_v) / start_v if start_v else 0.0

    return bot_total_return - spy_total_return


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

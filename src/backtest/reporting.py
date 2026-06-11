"""Reporting: equity curve, metrics, forward-return backfill.

Reads ``PortfolioSnapshotRow`` from the run's ``db.sqlite`` and produces
``report/equity_curve.png`` and ``report/metrics.md``.  The forward-return
backfill walks ``decisions/*.json`` and patches each file in place with +1d /
+5d / +20d returns from the cache — the supervision signal a future RAG
retriever wants.

Two public entry points exist:

- ``report_progress`` — equity curve + metrics.md (with the pipeline-efficiency
  section appended).  Cheap enough to call at the end of every tick from the
  driver, so an operator watching the run gets a live, on-disk dashboard
  rather than a single artefact at the end.
- ``report`` — calls ``report_progress`` then runs the forward-return backfill.
  Backfill walks every decision JSON and only gains useful data as time
  passes within the cache window, so it stays end-of-run.

Spec §end-of-window: metrics.md must include:
  - total return
  - annualised Sharpe ratio (252-day basis)
  - max drawdown
  - vs-SPY delta (bot_total_return − spy_total_return)
  - SPY Sharpe — apples-to-apples risk-adjusted comparison
  - vs matched-exposure SPY — bot return minus SPY weighted each tick by
    the bot's actual equity exposure %, so cash drag is stripped out
  - matched-exposure SPY Sharpe (annualised, same basis as bot Sharpe)
  - average bot equity exposure (1 − cash/total) across the run
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
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering for CI / nightly cron
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backtest.cache.store import CachedDataStore
from backtest.settings import BacktestSettings
from orchestrator.persistence import PortfolioSnapshotRow, TradeLogRow

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def report_progress(run_dir: Path, settings: BacktestSettings, *, window: str) -> None:
    """Refresh ``report/equity_curve.png`` and ``report/metrics.md`` only.

    The cheap, per-tick slice of :func:`report`: load snapshots, render the
    equity curve, write the financial metrics file, and append the
    pipeline-efficiency section.  The forward-return backfill is **not**
    performed here — it walks every decision JSON and only gains useful data
    as time advances within the cache window, so it stays end-of-run.

    Safe to call after every tick.  Returns silently if no portfolio
    snapshots have been written yet (e.g. the snapshotter has not run on the
    very first tick of a brand-new run).

    Parameters
    ----------
    run_dir:
        Root directory for the run (contains ``db.sqlite``, ``decisions/``, etc.).
    settings:
        Validated ``BacktestSettings`` instance.  Used to locate the per-window
        golden cache (for the SPY benchmark) and to scale the Sharpe annualisation.
    window:
        Window key — required so the per-window cache path can be derived.
    """
    run_dir    = Path(run_dir)
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── load window config to extract the risk-free rate ─────────────────────
    # The rf rate is stored per-window in config/backtest_windows.json so
    # reports are reproducible even if the prevailing rate changes after a run.
    from backtest.windows import load_windows

    _windows_path = Path(__file__).parents[2] / "config" / "backtest_windows.json"
    windows = load_windows(_windows_path)
    if window not in windows:
        raise KeyError(
            f"Window key {window!r} not found in {_windows_path}. "
            f"Available: {sorted(windows)}"
        )
    window_cfg            = windows[window]
    risk_free_rate_annual = window_cfg.risk_free_rate_annual

    # ── load portfolio snapshots and trade log from the run DB ───────────────
    engine = create_engine(f"sqlite:///{run_dir / 'db.sqlite'}", future=True)
    with Session(engine) as s:
        rows = s.execute(
            select(PortfolioSnapshotRow)
            .order_by(PortfolioSnapshotRow.recorded_at),
        ).scalars().all()

        # Build (timestamp, total_value) pairs using actual column names.
        equity = [(r.recorded_at, float(r.bot_total_value)) for r in rows]
        # Parallel cash series — same order, same length as ``equity`` — so the
        # matched-exposure benchmark can ask "what was the bot's invested
        # fraction at this tick?" without re-querying the DB.
        cash   = [float(r.bot_cash) for r in rows]

        # Load closed trades for win rate and fill count.
        trade_rows = s.execute(select(TradeLogRow)).scalars().all()

    if not equity:
        # First-tick / pre-snapshotter call — nothing to render yet.  Logged at
        # debug because per-tick callers will hit this path until the first
        # snapshot lands; warning-level would spam the console.
        logger.debug("no portfolio snapshots in %s — skipping progress report", run_dir)
        return

    # ── win rate and fill count from closed trades ────────────────────────────
    fill_count = len(trade_rows)
    winning    = sum(1 for t in trade_rows if t.pnl_dollar > 0)
    win_rate   = (winning / fill_count) if fill_count > 0 else float("nan")

    # ── benchmark plumbing (SPY + matched-exposure) ──────────────────────────
    # Per-window golden cache — derived from ``backtests_root`` + window.
    from backtest.settings import cache_path_for_window
    cache         = CachedDataStore(cache_path_for_window(settings, window))
    starting_cash = equity[0][1]
    ticks_per_day = len(settings.ticks_per_day)

    # Build the SPY series once and share it: chart overlay, matched-exposure
    # benchmark, vs-SPY delta, and SPY Sharpe all derive from the same
    # tick-aligned source so they cannot drift apart.
    spy_series = _spy_benchmark_series(equity, cache, starting_cash)

    # Matched-exposure series: SPY weighted each tick by the bot's equity
    # exposure %.  Cash fraction earns rf_tick (not zero) when rf is non-zero.
    # Returns a descriptive N/A string when SPY is unavailable.
    matched_series = _matched_exposure_series(
        equity, cash, spy_series, starting_cash,
        risk_free_rate_annual=risk_free_rate_annual,
        ticks_per_day=ticks_per_day,
    )

    # vs-SPY delta uses the same SPY series — call ``_compute_vs_spy_delta``
    # rather than re-deriving here so any future refactor stays consolidated.
    vs_spy_delta = _compute_vs_spy_delta(equity, cache, starting_cash=starting_cash)

    # SPY Sharpe and matched Sharpe.  Both fall back to the matching N/A
    # string when their series is unavailable, so the metrics file always
    # writes successfully.
    if isinstance(spy_series, list) and len(spy_series) >= 2:
        spy_sharpe: float | str = _annualised_sharpe(
            spy_series, risk_free_rate_annual=risk_free_rate_annual,
        )
    else:
        spy_sharpe = spy_series if isinstance(spy_series, str) else "N/A — SPY series too short"

    if isinstance(matched_series, list) and len(matched_series) >= 2:
        matched_sharpe: float | str = _annualised_sharpe(
            matched_series, risk_free_rate_annual=risk_free_rate_annual,
        )
        # vs-matched delta: bot total return minus matched-exposure total
        # return — positive = bot's stock-picks beat passive SPY on the same
        # invested capital.
        matched_start = matched_series[0][1]
        matched_end   = matched_series[-1][1]
        if matched_start > 0:
            matched_return  = (matched_end - matched_start) / matched_start
            bot_return      = (equity[-1][1] - equity[0][1]) / equity[0][1] if equity[0][1] else 0.0
            vs_matched_delta: float | str = bot_return - matched_return
        else:
            vs_matched_delta = "N/A — matched series start is zero"
    else:
        matched_sharpe   = matched_series if isinstance(matched_series, str) else "N/A — matched series too short"
        vs_matched_delta = matched_series if isinstance(matched_series, str) else "N/A — matched series too short"

    # Information Ratio: annualised active return / tracking error vs SPY.
    if isinstance(spy_series, list) and len(spy_series) >= 2:
        ir: float | str = _information_ratio(equity, spy_series)
    elif isinstance(spy_series, str):
        ir = spy_series
    else:
        ir = "N/A — SPY series too short"

    avg_exposure_pct = _avg_exposure_pct(equity, cash)

    _write_equity_curve(
        equity,
        report_dir / "equity_curve.png",
        cache=cache,
        starting_cash=starting_cash,
        # Pass only when it materialised as a real series — string N/A means
        # SPY isn't in the cache and the SPY line itself is already absent,
        # so a matched line would be misleading.
        matched_series=matched_series if isinstance(matched_series, list) else None,
    )
    _write_metrics(
        equity,
        report_dir / "metrics.md",
        fill_count=fill_count,
        win_rate=win_rate,
        vs_spy_delta=vs_spy_delta,
        spy_sharpe=spy_sharpe,
        matched_sharpe=matched_sharpe,
        vs_matched_delta=vs_matched_delta,
        avg_exposure_pct=avg_exposure_pct,
        risk_free_rate_annual=risk_free_rate_annual,
        information_ratio=ir,
    )

    # ── pipeline-efficiency section (tokens, latency, cache, retries) ────────
    # Driven by the per-tick observability artefacts written under ``obs/`` by
    # ``observability.drain.drain_tick``.  Older runs (or runs that skipped the
    # observability install) won't have the directory — skip silently.  Because
    # ``_write_metrics`` rewrites ``metrics.md`` whole on every call, the
    # append below is fresh each time and never accumulates stale sections.
    obs_dir = run_dir / "obs"
    if obs_dir.exists():
        aggregates = _aggregate_obs_artefacts(obs_dir)
        if aggregates is not None:
            section = _format_obs_section(aggregates)
            with (report_dir / "metrics.md").open("a", encoding="utf-8") as f:
                f.write(section)


def report(run_dir: Path, settings: BacktestSettings, *, window: str) -> None:
    """Generate the full end-of-window report.

    Calls :func:`report_progress` to refresh the equity curve and metrics
    file, then walks ``decisions/`` to backfill +1d / +5d / +20d forward
    returns from the golden cache into each decision JSON in place.

    Parameters
    ----------
    run_dir:
        Root directory for the run (contains ``db.sqlite``, ``decisions/``, etc.).
    settings:
        Validated ``BacktestSettings`` instance.  Used to locate the
        per-window golden cache and to read ``forward_return_horizons_days``.
    window:
        Window key — required so the per-window cache path can be derived.
        Callers running a fresh backtest pass the same ``window_key`` used
        for the run; ad-hoc replays parse it from the run-id via
        ``window_from_run_id``.
    """
    report_progress(run_dir, settings, window=window)

    # ── forward-return backfill ───────────────────────────────────────────────
    # Re-open the per-window cache here (rather than threading it through from
    # ``report_progress``) so the two entry points stay independently callable.
    # The cost is one extra ``CachedDataStore`` open per end-of-run report —
    # negligible compared to the backfill walk itself.
    from backtest.settings import cache_path_for_window
    cache    = CachedDataStore(cache_path_for_window(settings, window))
    horizons = settings.forward_return_horizons_days
    _backfill_forward_returns(Path(run_dir) / "decisions", cache, horizons)


# ── Private helpers ───────────────────────────────────────────────────────────

def _write_equity_curve(
    series: list[tuple[datetime, float]],
    outpath: Path,
    *,
    cache: CachedDataStore,
    starting_cash: float,
    matched_series: list[tuple[datetime, float]] | None = None,
) -> None:
    """Render a portfolio equity curve PNG (with SPY overlay) to ``outpath``.

    Thin wrapper around ``_build_equity_figure`` that handles file I/O and
    figure teardown.  All layout decisions live in the helper so unit tests
    can inspect the figure directly without going through ``savefig``.

    Parameters
    ----------
    series:
        Ordered list of (timestamp, portfolio_value) pairs.
    outpath:
        Destination file path (e.g. ``report/equity_curve.png``).
    cache:
        The golden ``CachedDataStore`` to query for SPY OHLCV data.  Passed
        through to the helper; missing or unreadable SPY data is handled
        silently there.
    starting_cash:
        Starting equity used as the anchor for the dashed initial-funds
        line and as the rebasing target for the SPY overlay.  Pass
        ``series[0][1]`` so both reference lines sit exactly at the
        portfolio's day-one value.
    matched_series:
        Optional output of ``_matched_exposure_series`` — when provided,
        a third "Matched-exposure (rebased)" line is overlaid on the chart.
        ``None`` (the default) preserves the original three-line layout
        for callers that don't compute it.
    """
    fig = _build_equity_figure(
        series, cache=cache, starting_cash=starting_cash,
        matched_series=matched_series,
    )
    fig.tight_layout()
    fig.savefig(outpath)
    plt.close(fig)


def _build_equity_figure(
    series: list[tuple[datetime, float]],
    *,
    cache: CachedDataStore,
    starting_cash: float,
    matched_series: list[tuple[datetime, float]] | None = None,
) -> Figure:
    """Build the equity-curve ``Figure`` with portfolio + benchmark lines.

    Renders up to four lines on a single ``$`` axis:

    - **Portfolio** — solid blue, the raw ``series`` values.
    - **SPY (rebased)** — solid orange, the tick-aligned buy-and-hold
      benchmark produced by ``_spy_benchmark_series``.  Skipped silently if
      SPY is absent from the cache or the read raises (a descriptive
      ``N/A`` row is already written into ``metrics.md`` by
      ``_compute_vs_spy_delta``).  Critically, the chart and the metric
      now consume the **same** benchmark series, so they cannot drift
      apart on methodology (anchor price, intraday phase, tick cadence).
    - **Matched-exposure (rebased)** — solid green, the SPY benchmark
      weighted tick-by-tick to the bot's actual equity exposure %.  Drawn
      only when ``matched_series`` is supplied (caller computed it from
      cash + spy_series); omitted on backwards-compatible call sites.
    - **Initial funds** — grey dashed horizontal at ``starting_cash``, so
      the "above the line / below the line" signal is immediate.

    The time axis uses weekly major ticks (Mondays) with a concise date
    formatter and daily minor ticks, plus a faint grid on both levels.

    Parameters
    ----------
    series:
        Ordered list of (timestamp, portfolio_value) pairs.  May contain
        multiple intra-day ticks per trading day.
    cache:
        Golden ``CachedDataStore`` to read SPY OHLCV bars from.
    starting_cash:
        Anchor value for the dashed initial-funds line and the SPY
        rebasing target.
    matched_series:
        Optional tick-aligned matched-exposure benchmark.  When provided,
        an extra green line is drawn between the SPY line and the
        initial-funds reference.  ``None`` preserves the original
        three-line layout.

    Returns
    -------
    Figure
        The composed matplotlib ``Figure``.  Caller is responsible for
        saving and closing it.
    """
    # ── Portfolio line ───────────────────────────────────────────────────────
    xs = [t for t, _ in series]
    ys = [v for _, v in series]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(xs, ys, label="Portfolio", color="tab:blue")

    # ── SPY overlay — shares its series with the vs-SPY metric ──────────────
    # Single source of truth: ``_spy_benchmark_series`` produces the same
    # tick-aligned, ``starting_cash``-anchored series that
    # ``_compute_vs_spy_delta`` consumes, so the chart's last orange point
    # always equals the metric's SPY ending value.  The helper returns a
    # descriptive string when SPY is missing — that branch is dropped
    # silently here since the metrics file already surfaces the reason in
    # human-readable form.
    spy_series = _spy_benchmark_series(series, cache, starting_cash)
    if isinstance(spy_series, list) and spy_series:
        spy_xs = [t for t, _ in spy_series]
        spy_ys = [v for _, v in spy_series]
        ax.plot(spy_xs, spy_ys, label="SPY (rebased)", color="tab:orange")

    # ── Matched-exposure overlay (optional) ──────────────────────────────────
    # When the caller has computed a matched-exposure series, draw it as a
    # third line sandwiched between the SPY line and the initial-funds
    # reference.  This is the "apples-to-apples" comparison: SPY weighted
    # to the bot's actual equity exposure %, so cash drag is stripped out.
    # Drawn in green so it visually separates from SPY's orange.
    if matched_series:
        matched_xs = [t for t, _ in matched_series]
        matched_ys = [v for _, v in matched_series]
        ax.plot(
            matched_xs, matched_ys,
            label="Matched-exposure (rebased)",
            color="tab:green",
        )

    # ── Initial-funds reference line ─────────────────────────────────────────
    ax.axhline(
        starting_cash,
        linestyle="--",
        color="grey",
        linewidth=1,
        alpha=0.6,
        label="Initial funds",
    )

    # ── Axis cosmetics ───────────────────────────────────────────────────────
    ax.set_xlabel("Time")
    ax.set_ylabel("Portfolio value ($)")
    ax.legend()

    # Weekly major ticks (Monday) with concise labels; daily minor ticks.
    major_locator = mdates.WeekdayLocator(byweekday=mdates.MO)
    ax.xaxis.set_major_locator(major_locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(major_locator))
    ax.xaxis.set_minor_locator(mdates.DayLocator())

    # Faint two-level grid: stronger on weekly majors, almost-invisible on days.
    ax.grid(True, which="major", alpha=0.3)
    ax.grid(True, which="minor", alpha=0.1)

    # Rotate labels automatically if they would overlap at this figure width.
    fig.autofmt_xdate()

    return fig


def _write_metrics(
    series: list[tuple[datetime, float]],
    outpath: Path,
    *,
    fill_count: int = 0,
    win_rate: float = float("nan"),
    vs_spy_delta: float | str = "N/A — SPY not in cache (run backtest_fetch with SPY)",
    spy_sharpe: float | str = "N/A — SPY not in cache",
    matched_sharpe: float | str = "N/A — SPY not in cache",
    vs_matched_delta: float | str = "N/A — SPY not in cache",
    avg_exposure_pct: float = float("nan"),
    risk_free_rate_annual: float = 0.0,
    information_ratio: float | str = "N/A — SPY not in cache",
) -> None:
    """Compute performance metrics and write a Markdown report to ``outpath``.

    Metrics written (spec §end-of-window):
    - Total return as a percentage of starting value.
    - Annualised Sharpe ratio (252 trading days, daily basis, rf-subtracted).
    - Max drawdown (largest peak-to-trough decline as a fraction).
    - vs-SPY delta (bot total return − SPY total return over the same window).
    - SPY Sharpe (annualised, daily basis) — apples-to-apples risk-adjusted
      comparison against a 100% SPY buy-and-hold.
    - Matched-exposure Sharpe and vs-matched-exposure delta — same SPY
      benchmark dynamically re-weighted to the bot's per-tick equity
      exposure %, so cash-drag is stripped from the comparison.
    - Information Ratio vs SPY — annualised active return / tracking error.
    - Average equity exposure — the bot's mean invested fraction across
      the run (1 − cash/total), so the matched-exposure number is
      interpretable on its own.
    - Risk-free rate transparency line (FRED DTB3 window average).
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
    spy_sharpe:
        Annualised Sharpe of the SPY buy-and-hold benchmark over the
        same window.  Pass a string when SPY is absent from the cache.
    matched_sharpe:
        Annualised Sharpe of the matched-exposure benchmark (SPY weighted
        each tick by the bot's equity exposure %).  Pass a string when
        SPY is absent.
    vs_matched_delta:
        Bot total return minus matched-exposure total return.  Positive =
        bot's stock-picks beat passive SPY on the same invested capital.
    avg_exposure_pct:
        Mean of ``(total - cash) / total`` across all snapshots — the
        bot's average invested fraction over the run.  ``NaN`` if no
        snapshots have positive total value.
    risk_free_rate_annual:
        Annualised risk-free rate used for the Sharpe calculations.
        Written verbatim in a transparency row so the reader knows which
        rf was applied.  Defaults to ``0.0`` so callers without a window
        context (ad-hoc replays) still produce a valid file.
    information_ratio:
        Pre-computed Information Ratio vs SPY, or a descriptive string
        when SPY is unavailable.  Defaults to the standard N/A string so
        existing callers that don't pass it continue to work.
    """
    start_v = series[0][1]
    end_v   = series[-1][1]
    total_return = (end_v - start_v) / start_v

    # ── Sharpe ───────────────────────────────────────────────────────────────
    # Delegated to the shared helper so bot / SPY / matched-exposure Sharpes
    # all use the same daily-resampled, rf-subtracted, sqrt(252) convention.
    # The three numbers in ``metrics.md`` are directly comparable.
    sharpe = _annualised_sharpe(series, risk_free_rate_annual=risk_free_rate_annual)

    # ── Max drawdown ──────────────────────────────────────────────────────────
    peak   = series[0][1]
    max_dd = 0.0
    for _, v in series:
        peak   = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak)

    # ── Format optional fields ────────────────────────────────────────────────
    # Each delta / Sharpe may be a float (computed) or a descriptive string
    # (when SPY is absent from the cache).  ``_fmt_pct`` / ``_fmt_sharpe``
    # below centralise the two layouts so every row in the file is
    # consistent (bold for numbers, italic for N/A explanations).

    def _fmt_pct(value: float | str) -> str:
        """Format a fraction as a signed percentage, or an italic N/A string."""
        if isinstance(value, float):
            return f"**{value:+.2%}**"
        return f"_{value}_"

    def _fmt_sharpe(value: float | str) -> str:
        """Format a Sharpe ratio as a 2dp float, or an italic N/A string."""
        if isinstance(value, float):
            return f"**{value:.2f}**"
        return f"_{value}_"

    vs_spy_str         = _fmt_pct(vs_spy_delta)
    vs_matched_str     = _fmt_pct(vs_matched_delta)
    spy_sharpe_str     = _fmt_sharpe(spy_sharpe)
    matched_sharpe_str = _fmt_sharpe(matched_sharpe)
    ir_str             = _fmt_sharpe(information_ratio)

    # Average exposure: a NaN means no snapshots had positive total value;
    # surface the gap rather than silently writing "nan%".
    if isinstance(avg_exposure_pct, float) and avg_exposure_pct == avg_exposure_pct:
        avg_exposure_str = f"**{avg_exposure_pct:.1%}**"
    else:
        avg_exposure_str = "_N/A_"

    # NaN check on win_rate uses ``x != x`` — robust to any NaN flavour.
    win_rate_str = f"**{win_rate:.1%}**" if not (isinstance(win_rate, float) and win_rate != win_rate) else "**N/A** (no closed trades)"

    outpath.write_text(
        "# Backtest metrics\n\n"
        f"- Total return: **{total_return:+.2%}**\n"
        f"- Sharpe (annualised, daily basis): **{sharpe:.2f}**\n"
        f"- Max drawdown: **{max_dd:+.2%}**\n"
        f"- vs-SPY delta (100% buy-and-hold): {vs_spy_str}\n"
        f"- SPY Sharpe (annualised, daily basis): {spy_sharpe_str}\n"
        f"- vs matched-exposure SPY: {vs_matched_str}\n"
        f"- Matched-exposure SPY Sharpe (annualised, daily basis): {matched_sharpe_str}\n"
        f"- Information Ratio vs SPY (annualised): {ir_str}\n"
        f"- Avg bot equity exposure: {avg_exposure_str}\n"
        f"- Risk-free rate (annualised): **{risk_free_rate_annual:.3%}** (FRED DTB3 window average)\n"
        f"- Win rate: {win_rate_str}\n"
        f"- Closed round-trips: **{fill_count}**\n"
        f"- Ticks recorded: **{len(series)}**\n",
        encoding="utf-8",
    )


def _daily_series(
    series: list[tuple[datetime, float]],
) -> list[tuple[date, float]]:
    """Resample a tick-level series to one point per calendar date.

    Groups ticks by their calendar date (in whatever timezone the datetimes
    carry) and retains only the **last** value for each date, so intra-day
    volatility does not inflate the daily-return count.  Output is sorted
    in ascending date order.

    Parameters
    ----------
    series:
        Ordered list of (timestamp, value) pairs at any intra-day frequency.

    Returns
    -------
    list[(date, float)]
        One (date, last_value) pair per calendar date, in chronological order.
    """

    if not series:
        return []

    # Walk chronologically, keeping the most recent value seen per date.
    # A plain dict preserves insertion order (Python 3.7+), so we get a
    # naturally sorted result as long as the input is chronological.
    last_by_date: dict[date, float] = {}
    for ts, value in series:
        last_by_date[ts.date()] = value

    return list(last_by_date.items())


def _annualised_sharpe(
    series: list[tuple[datetime, float]] | None,
    *,
    risk_free_rate_annual: float,
) -> float:
    """Compute the annualised Sharpe ratio on a daily-resampled basis.

    Resamples the tick-level series to one value per calendar date (last
    tick of each date), computes daily simple returns, subtracts the daily
    risk-free rate (converted from the annualised figure via
    ``(1 + rf)^(1/252) − 1``), then annualises by ``sqrt(252)``.

    All three Sharpe ratios in ``metrics.md`` (bot, SPY, matched-exposure)
    use this helper, so they are directly comparable on a daily basis.

    Parameters
    ----------
    series:
        Ordered list of (timestamp, value) pairs.  ``None`` or fewer than
        two daily points after resampling yields ``NaN``.
    risk_free_rate_annual:
        Annualised risk-free rate (e.g. ``0.048`` for 4.8% pa).  Sourced
        from the window's ``risk_free_rate_annual`` field (FRED DTB3
        window average).

    Returns
    -------
    float
        Annualised Sharpe (daily basis), or ``NaN`` when fewer than two
        daily returns are available or the excess-return series has zero
        variance.
    """

    if not series or len(series) < 2:
        return float("nan")

    # Resample to daily (last tick per date) before computing returns so that
    # intra-day tick frequency does not bias the annualisation factor.
    daily = _daily_series(series)

    if len(daily) < 2:
        return float("nan")

    # Daily simple returns from consecutive daily close values.
    daily_rets: list[float] = []
    for (_, v0), (_, v1) in zip(daily, daily[1:], strict=False):
        if v0 != 0:
            daily_rets.append((v1 - v0) / v0)

    if len(daily_rets) < 2:
        return float("nan")

    # Convert annualised rf to a per-day rate using the standard compound
    # formula: rf_daily = (1 + rf_annual)^(1/252) − 1.
    rf_daily = (1 + risk_free_rate_annual) ** (1 / 252) - 1

    excess = [r - rf_daily for r in daily_rets]

    std = statistics.pstdev(excess)
    if std == 0:
        return float("nan")

    # Annualise on a 252-trading-day basis.  Because the return series is
    # already daily, the factor is simply sqrt(252) — no tick-frequency
    # scaling required.
    return (statistics.mean(excess) / std) * (252 ** 0.5)


def _information_ratio(
    bot_series: list[tuple[datetime, float]],
    spy_series: list[tuple[datetime, float]],
) -> float:
    """Compute the annualised Information Ratio of the bot vs SPY.

    IR = annualised active return / tracking error, where:

    - Active return per day = bot daily return − SPY daily return.
    - Tracking error = population std of daily active returns.
    - Annualisation factor = ``sqrt(252)``.

    Both series are resampled to daily (last tick per date) and
    inner-joined on common dates before computing returns, so a difference
    in tick cadence between bot and SPY does not pollute the result.

    Parameters
    ----------
    bot_series:
        Ordered list of (timestamp, portfolio_value) pairs for the bot.
    spy_series:
        Ordered list of (timestamp, spy_benchmark_value) pairs.

    Returns
    -------
    float
        Annualised Information Ratio, or ``NaN`` when:
        - fewer than two common calendar dates exist after the inner join,
        - fewer than two daily return diffs can be computed, or
        - tracking error is zero (bot tracks SPY exactly).
    """

    if not bot_series or not spy_series:
        return float("nan")

    # Resample both series to daily (last tick per date).
    bot_daily = dict(_daily_series(bot_series))
    spy_daily = dict(_daily_series(spy_series))

    # Inner join on common dates, sorted chronologically.
    common_dates = sorted(set(bot_daily) & set(spy_daily))

    if len(common_dates) < 2:
        return float("nan")

    # Compute per-day active return: bot − SPY simple return.
    diffs: list[float] = []
    for d_prev, d_curr in zip(common_dates, common_dates[1:], strict=False):
        bot_v0, bot_v1 = bot_daily[d_prev], bot_daily[d_curr]
        spy_v0, spy_v1 = spy_daily[d_prev], spy_daily[d_curr]

        if bot_v0 == 0 or spy_v0 == 0:
            # Skip degenerate periods — zero anchor would produce a
            # misleading infinite return.
            continue

        bot_ret = (bot_v1 - bot_v0) / bot_v0
        spy_ret = (spy_v1 - spy_v0) / spy_v0
        diffs.append(bot_ret - spy_ret)

    if len(diffs) < 2:
        return float("nan")

    tracking_error = statistics.pstdev(diffs)
    if tracking_error == 0:
        # Zero tracking error means the bot is a perfect SPY replica —
        # the ratio is undefined.
        return float("nan")

    return (statistics.mean(diffs) / tracking_error) * (252 ** 0.5)


def _avg_exposure_pct(
    equity: list[tuple[datetime, float]],
    cash: list[float],
) -> float:
    """Return the mean fraction of portfolio invested across all snapshots.

    Defined as ``mean((total - cash) / total)`` over every snapshot with
    ``total > 0``.  Clamped to ``[0, 1]`` per-tick so a transient negative
    position value (e.g. an intra-tick mark glitch) cannot push the mean
    below zero or above one.

    Parameters
    ----------
    equity:
        Ordered list of (timestamp, total_portfolio_value) pairs.
    cash:
        Parallel list of cash balances — same length and order as
        ``equity``.

    Returns
    -------
    float
        Mean exposure as a fraction in ``[0, 1]``.  ``NaN`` when no
        snapshot has a positive total value (degenerate / empty run).
    """

    if not equity or not cash:
        return float("nan")

    fractions: list[float] = []
    for (_, total), c in zip(equity, cash, strict=False):
        if total <= 0:
            continue
        # Clamp protects against negative positions value (mark glitch) and
        # cash exceeding total (rounding or reconciliation edge cases).
        invested = max(0.0, min(1.0, (total - c) / total))
        fractions.append(invested)

    if not fractions:
        return float("nan")

    return statistics.mean(fractions)


def _matched_exposure_series(
    equity: list[tuple[datetime, float]],
    cash: list[float],
    spy_series: list[tuple[datetime, float]] | str,
    starting_cash: float,
    *,
    risk_free_rate_annual: float,
    ticks_per_day: int,
) -> list[tuple[datetime, float]] | str:
    """Build a tick-aligned "matched-exposure" benchmark series.

    Conceptually: at every tick a synthetic portfolio holds
    ``bot_equity_pct`` of SPY and ``bot_cash_pct`` in cash.  Over each
    tick it earns ``bot_equity_pct × spy_return`` on the equity fraction
    and ``rf_tick`` on the cash fraction, where ``rf_tick`` is the per-tick
    risk-free rate derived from the window's annualised T-bill rate.

    Used to strip cash-drag out of the vs-SPY comparison: if the bot is
    only 40% invested, comparing it to a 100% SPY benchmark is unfair.
    The matched-exposure series asks "did the bot's stock-picks beat
    *passive* SPY on the same invested capital?".  Pair with the raw 100%
    SPY benchmark to also see whether holding cash was the correct decision
    (e.g. avoiding a drawdown).

    Returns the same descriptive ``N/A`` string as ``_spy_benchmark_series``
    when SPY is unavailable, so the metrics file still writes.

    Parameters
    ----------
    equity:
        Ordered list of (timestamp, total_portfolio_value) pairs.
    cash:
        Parallel list of cash balances — same length as ``equity``.
    spy_series:
        Output of ``_spy_benchmark_series`` — either a list of
        (timestamp, $-value) pairs or a descriptive ``N/A`` string.
    starting_cash:
        Anchor $-value for the matched series.  Must equal the
        portfolio's day-one value so the chart and metric share a $-zero.
    risk_free_rate_annual:
        Annualised risk-free rate (e.g. ``0.048``).  Used to credit the
        cash fraction of the synthetic portfolio each tick.
    ticks_per_day:
        Number of ticks per trading day.  Used alongside
        ``risk_free_rate_annual`` to convert the annual rate to a per-tick
        rate via ``(1 + rf)^(1 / (252 × tpd)) − 1``.

    Returns
    -------
    list[(datetime, float)] | str
        A tick-aligned matched-exposure series, or a descriptive ``N/A``
        string when SPY is unusable / the equity series is empty.
    """

    # Forward any upstream N/A so the metrics file carries the exact reason.
    if isinstance(spy_series, str):
        return spy_series

    if not equity or not spy_series:
        return "N/A — empty series"

    if len(equity) != len(cash):
        # Defensive: callers always build these parallel from the same DB
        # query, but a mismatch would silently produce wrong exposures.
        return "N/A — equity/cash length mismatch"

    # Build per-timestamp lookups so we can pair SPY ticks (which may have
    # skipped some equity ticks when SPY had no bar that calendar date)
    # with the bot's exposure at the *start* of each compounding period.
    total_by_ts: dict[datetime, float] = {ts: v for ts, v in equity}
    cash_by_ts:  dict[datetime, float] = {ts: c for (ts, _), c in zip(equity, cash, strict=False)}

    matched: list[tuple[datetime, float]] = []
    value = starting_cash
    matched.append((spy_series[0][0], value))

    # Lagged exposure: the bot's exposure at the *start* of each return
    # period drives the matched return over that period.  End-of-period
    # exposure would smuggle in information that wasn't yet available.
    for (ts_prev, spy_prev), (ts_curr, spy_curr) in zip(spy_series, spy_series[1:], strict=False):
        if spy_prev <= 0:
            # Anchor pathology — skip this period's compounding, value unchanged.
            matched.append((ts_curr, value))
            continue

        total_prev = total_by_ts.get(ts_prev, 0.0)
        cash_prev  = cash_by_ts.get(ts_prev,  0.0)
        if total_prev <= 0:
            exposure_pct = 0.0
        else:
            exposure_pct = max(0.0, min(1.0, (total_prev - cash_prev) / total_prev))

        spy_return = (spy_curr - spy_prev) / spy_prev

        # Cash fraction earns rf_tick (uniform tick-interval approximation).
        # Assumes each tick represents an equal fraction of the 252-day trading
        # calendar — reasonable for a fixed daily schedule but not perfectly
        # accurate over irregular holiday-adjacent gaps.
        rf_tick        = (1 + risk_free_rate_annual) ** (1 / (252 * ticks_per_day)) - 1
        matched_return = exposure_pct * spy_return + (1.0 - exposure_pct) * rf_tick

        value = value * (1.0 + matched_return)
        matched.append((ts_curr, value))

    return matched


def _spy_benchmark_series(
    equity: list[tuple[datetime, float]],
    cache: CachedDataStore,
    starting_cash: float,
) -> list[tuple[datetime, float]] | str:
    """Build a tick-aligned SPY buy-and-hold benchmark series.

    Models SPY as a single position the bot would have opened at the very
    first equity tick: ``spy_shares = starting_cash / spy_price_at_first_tick``.
    Every subsequent tick is then valued at ``spy_shares × spy_price_at_tick``,
    where the price is sampled at the **same intraday phase as the portfolio
    snapshot** — open-phase ticks read ``bar.open``, close-phase ticks read
    ``bar.close``.

    This is the single source of truth for both the equity-curve chart
    overlay (``_build_equity_figure``) and the vs-SPY metric
    (``_compute_vs_spy_delta``).  Both call this helper directly so they
    cannot drift apart on anchor price, intraday phase, or tick cadence —
    eliminating the apples-to-oranges bug where the chart was anchored on
    bar-close while the metric used bar-open.

    Parameters
    ----------
    equity:
        Ordered list of (timestamp, portfolio_value) pairs.  Used both for
        the window boundaries and for per-tick alignment.
    cache:
        Golden ``CachedDataStore`` to read SPY OHLCV bars from.
    starting_cash:
        Anchor value used to size the SPY "position".  Must equal the
        portfolio's starting equity so the two series share a $-zero.

    Returns
    -------
    list[(datetime, float)] | str
        A tick-aligned list of (timestamp, benchmark_value) pairs when
        SPY data overlaps the equity window, or a descriptive ``N/A``
        string when it does not.  Callers must ``isinstance``-check the
        result before consuming it as a series.
    """

    if not equity:
        return "N/A — no portfolio snapshots"

    # Derive the SPY-read window from the equity series timestamps.
    start_date = equity[0][0].date()
    end_date   = equity[-1][0].date()

    try:
        spy_bars = cache.read_ohlcv("SPY", start_date, end_date)
    except Exception:
        logger.exception("Failed to read SPY OHLCV from cache")
        return "N/A — error reading SPY from cache"

    if not spy_bars:
        return "N/A — SPY not in cache (run backtest_fetch with SPY)"

    # Index bars by calendar date for O(1) per-tick lookup.  OHLCV bars
    # carry a single per-day timestamp; we pick open vs close at read time.
    bars_by_date = {b.timestamp.date(): b for b in spy_bars}

    def _spy_price_for_tick(tick_ts: datetime) -> float | None:
        """Return SPY price at ``tick_ts`` matching its intraday phase.

        Ticks scheduled at or before 17:00 UTC are treated as open-phase
        (standard NYSE open is 13:30 UTC; DST shifts move it earlier).
        Later ticks are close-phase (standard close 20:00 UTC; early-close
        half-days at 17:00 / 18:00 UTC also fall on this side).  The
        17:00 UTC threshold sits comfortably between the two cohorts so
        the classifier is robust to DST shifts and half-day schedules.

        Returns ``None`` when no SPY bar exists for this calendar date —
        weekend, holiday, or a tick that fell outside cached coverage.
        """

        bar = bars_by_date.get(tick_ts.date())
        if bar is None:
            return None
        return bar.open if tick_ts.hour < 17 else bar.close

    # Anchor at the first tick that has a matching SPY price.  Leading
    # mismatches are rare (the runner usually aligns the window) but
    # skipping them keeps the series usable rather than failing the whole
    # report on a single bad timestamp.
    anchor_price: float | None = None
    for tick_ts, _ in equity:
        anchor_price = _spy_price_for_tick(tick_ts)
        if anchor_price is not None and anchor_price > 0:
            break

    if anchor_price is None or anchor_price <= 0:
        return "N/A — no SPY bar overlaps the equity series"

    spy_shares = starting_cash / anchor_price

    # Walk every tick; emit (timestamp, $-value) for ticks with a bar.
    series: list[tuple[datetime, float]] = []
    for tick_ts, _ in equity:
        price = _spy_price_for_tick(tick_ts)
        if price is None:
            continue
        series.append((tick_ts, spy_shares * price))

    return series


def _compute_vs_spy_delta(
    equity: list[tuple[datetime, float]],
    cache: CachedDataStore,
    starting_cash: float,
) -> float | str:
    """Compute the bot's outperformance vs a tick-aligned SPY buy-and-hold.

    Delegates SPY valuation to ``_spy_benchmark_series`` — the same helper
    the equity-curve chart consumes — so the metric and the chart cannot
    disagree on methodology.  Returns
    ``bot_total_return − spy_total_return`` as a fraction (e.g. ``0.05`` =
    5 pp outperformance).

    Falls back to a descriptive string if SPY is missing from the cache,
    so the metrics file still writes and the run does not abort.

    Parameters
    ----------
    equity:
        Ordered list of (timestamp, portfolio_value) pairs covering the
        run window.
    cache:
        The golden ``CachedDataStore`` to query for SPY OHLCV data.
    starting_cash:
        Starting equity — forwarded to ``_spy_benchmark_series`` so the
        SPY "position" is sized against the same $-zero as the portfolio.

    Returns
    -------
    float | str
        A float delta when SPY data is available; a descriptive string
        otherwise.
    """

    if not equity:
        return "N/A — no portfolio snapshots"

    spy_series = _spy_benchmark_series(equity, cache, starting_cash)

    # _spy_benchmark_series already produced a descriptive N/A string when
    # SPY data was unusable — surface it verbatim so the metrics file
    # carries the exact reason rather than a generic fallback.
    if isinstance(spy_series, str):
        return spy_series

    if not spy_series:
        return "N/A — empty SPY series"

    # Apples-to-apples: identical starting cash, identical tick cadence,
    # identical intraday phase at each tick.
    spy_start = spy_series[0][1]
    spy_end   = spy_series[-1][1]
    bot_start = equity[0][1]
    bot_end   = equity[-1][1]

    if spy_start <= 0:
        return "N/A — SPY start value is zero"

    spy_total_return = (spy_end - spy_start) / spy_start
    bot_total_return = (bot_end - bot_start) / bot_start if bot_start else 0.0

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

            forwards:      dict[str, float | None] = {}
            actual_dates:  dict[str, str | None]   = {}

            for h in horizons_days:
                target = entry_date + timedelta(days=h)
                # Look up to 4 calendar days forward to skip weekends / holidays.
                bars = cache.read_ohlcv(ticker, target, target + timedelta(days=4))
                if not bars:
                    forwards[f"+{h}d"]     = None
                    # Record None so the two dicts always have identical key sets.
                    actual_dates[f"+{h}d"] = None
                    continue

                # Use the first available bar's close as the horizon price.
                bar = bars[0]
                forwards[f"+{h}d"]     = (bar.close - entry_price) / entry_price
                # Record the actual calendar date of the bar used.  When a target
                # date is a holiday, the bar lands on a later date; supervision
                # tooling can detect the gap by comparing this against the target.
                actual_dates[f"+{h}d"] = bar.timestamp.date().isoformat()

            snapshot["forward_returns"]             = forwards
            snapshot["forward_returns_actual_date"] = actual_dates
            path.write_text(
                json.dumps(snapshot, indent=2, default=str),
                encoding="utf-8",
            )

        except Exception:
            logger.exception("forward-return backfill failed for %s", path)


def _aggregate_obs_artefacts(obs_dir: Path) -> dict | None:
    """Walk ``obs_dir`` and aggregate per-tick artefacts into run-level totals.

    Reads three sibling directories:

    - ``traces/<tick>.json`` — for ``generate_content`` spans carrying the
      OTEL GenAI ``gen_ai.usage.*_tokens`` attributes (token totals) and
      ``invoke_agent`` spans carrying ``gen_ai.agent.name`` plus
      ``duration_ms`` (per-agent latency envelope).
    - ``metrics/<tick>.json`` — for ADK's native
      ``gen_ai.agent.invocation.duration`` histogram (per-agent latency,
      cross-checked against the span-derived numbers).
    - ``logs/<tick>.json`` — for the structured ``report_cache_hit`` /
      ``report_cache_miss`` events emitted by
      ``agents.analysts.cache_callbacks`` and any records from the
      ``agents.llm_retry`` logger.

    Designed to be lenient: a missing sibling directory or a malformed
    file is logged and skipped rather than aborting the report.  Returns
    ``None`` only when no ticks contributed any data at all — that lets
    the caller suppress the markdown section entirely on empty runs.

    Parameters
    ----------
    obs_dir:
        ``<run_dir>/obs`` — the parent of ``traces/``, ``metrics/``,
        ``logs/``.

    Returns
    -------
    dict | None
        Aggregated counters, or ``None`` when nothing was found.  Shape:
        ``{"tokens": {"input": int, "output": int, "total": int,
                       "generate_content_spans": int},
           "agent_latency_ms": {"<agent>": {"count": int, "sum": float,
                                            "min": float, "max": float}},
           "cache": {"hits": int, "misses": int},
           "retries": int,
           "ticks_observed": int}``.
    """

    # ── Token totals (from ``generate_content`` spans) ───────────────────────
    # Per the OTEL GenAI semantic conventions, ADK writes token usage as span
    # attributes on the ``generate_content`` span. Sum across every span
    # across every tick.
    input_tokens     = 0
    output_tokens    = 0
    generate_spans   = 0

    # ── Per-agent latency (from ``invoke_agent`` spans + native histogram) ───
    # Two independent sources for the same number — kept separate so the
    # final markdown can present whichever is non-empty.  The histogram is
    # the canonical source (it's what ADK emits natively for metrics
    # dashboards); the span-derived numbers are a cross-check.
    agent_latency_ms: dict[str, dict[str, float]] = {}

    # ── Cache hits / misses + retry counts (from structured log events) ──────
    cache_hits   = 0
    cache_misses = 0
    retry_count  = 0

    # ── Strategist hallucinations (sell on non-held, etc.) ───────────────────
    # Emitted by ``agents.executor._verb_dispatch`` with stable message
    # ``hallucinated_stance``.  One log event per hallucinated stance.
    hallucinated_stances = 0

    ticks_observed = 0

    traces_dir = obs_dir / "traces"
    if traces_dir.is_dir():

        for path in sorted(traces_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("failed to parse trace file %s", path)
                continue

            ticks_observed += 1

            for span in payload.get("spans", []):
                attrs = span.get("attributes", {}) or {}
                name  = span.get("name", "")

                # Token usage lives on ``generate_content`` spans only.  ADK
                # emits them as ``generate_content <model_id>`` (e.g.
                # ``generate_content gemini-2.5-flash-lite``); use a prefix
                # match so the model-id suffix doesn't reject the span.
                if name.startswith("generate_content"):
                    generate_spans += 1
                    input_tokens   += int(attrs.get("gen_ai.usage.input_tokens",  0) or 0)
                    output_tokens  += int(attrs.get("gen_ai.usage.output_tokens", 0) or 0)

                # ``invoke_agent`` spans carry the agent name in
                # ``gen_ai.agent.name`` and the wall-clock duration on
                # the span itself.  ADK suffixes the span name with the
                # agent name (e.g. ``invoke_agent FundamentalAnalyst_AAPL``);
                # prefix-match so the suffix doesn't reject it.
                if name.startswith("invoke_agent"):
                    agent       = attrs.get("gen_ai.agent.name", "<unknown>")
                    duration_ms = float(span.get("duration_ms", 0.0) or 0.0)

                    bucket = agent_latency_ms.setdefault(
                        agent,
                        {"count": 0, "sum": 0.0, "min": float("inf"), "max": 0.0},
                    )
                    bucket["count"] += 1
                    bucket["sum"]   += duration_ms
                    bucket["min"]    = min(bucket["min"], duration_ms)
                    bucket["max"]    = max(bucket["max"], duration_ms)

    logs_dir = obs_dir / "logs"
    if logs_dir.is_dir():

        for path in sorted(logs_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("failed to parse log file %s", path)
                continue

            for event in payload.get("events", []):
                msg    = event.get("message", "") or ""
                lgr    = event.get("logger",  "") or ""

                # Structured cache events — emitted by
                # ``agents.analysts.cache_callbacks`` with stable message keys.
                if msg == "report_cache_hit":
                    cache_hits += 1
                elif msg == "report_cache_miss":
                    cache_misses += 1

                # Strategist hallucinations — emitted by
                # ``agents.executor._verb_dispatch`` with stable message
                # ``hallucinated_stance`` (one event per occurrence).
                elif msg == "hallucinated_stance":
                    hallucinated_stances += 1

                # Retries — anything coming out of the LLM retry helper logger.
                # ``before_sleep_log`` writes one record per retry attempt, so
                # this is a faithful count of retry events.
                if lgr.startswith("agents.llm_retry"):
                    retry_count += 1

    # Tidy infinities so the markdown formatter doesn't have to special-case
    # them: an agent we never observed shouldn't appear at all.
    for stats in agent_latency_ms.values():
        if stats["count"] == 0:
            stats["min"] = 0.0

    # Suppress the section entirely on a totally empty obs/ tree (no ticks,
    # no spans, no logs) — the caller treats ``None`` as "skip section".
    nothing_found = (
        ticks_observed == 0
        and not agent_latency_ms
        and cache_hits == 0
        and cache_misses == 0
        and retry_count == 0
        and generate_spans == 0
        and hallucinated_stances == 0
    )
    if nothing_found:
        return None

    return {
        "tokens": {
            "input":                  input_tokens,
            "output":                 output_tokens,
            "total":                  input_tokens + output_tokens,
            "generate_content_spans": generate_spans,
        },
        "agent_latency_ms":     agent_latency_ms,
        "cache": {
            "hits":   cache_hits,
            "misses": cache_misses,
        },
        "retries":              retry_count,
        "hallucinated_stances": hallucinated_stances,
        "ticks_observed":       ticks_observed,
    }


def _format_obs_section(aggs: dict) -> str:
    """Render the aggregated observability totals as a Markdown section.

    Section is appended to ``metrics.md`` after the headline financial
    metrics.  Layout mirrors the existing bullet style so the file reads
    as one document.  Per-agent latency is rendered as a compact table
    sorted by descending total time spent (mean × count) — the most
    expensive agent appears first, which is what you want when chasing
    token / latency savings.

    Parameters
    ----------
    aggs:
        Dict produced by ``_aggregate_obs_artefacts`` — see that function's
        docstring for the shape contract.

    Returns
    -------
    str
        Markdown text starting with a newline so it can be appended
        cleanly to an existing file.
    """

    tokens         = aggs["tokens"]
    cache          = aggs["cache"]
    latency        = aggs["agent_latency_ms"]
    retries        = aggs["retries"]
    hallucinations = aggs.get("hallucinated_stances", 0)
    ticks          = aggs["ticks_observed"]

    # ── Cache hit rate (defensive against zero-denominator runs) ─────────────
    cache_total = cache["hits"] + cache["misses"]
    if cache_total > 0:
        hit_rate_pct = 100.0 * cache["hits"] / cache_total
        cache_line   = (
            f"- Report cache: **{cache['hits']} hits / {cache_total} lookups** "
            f"({hit_rate_pct:.1f}% hit rate)"
        )
    else:
        cache_line = "- Report cache: _no cache lookups recorded_"

    # ── Per-agent latency table ──────────────────────────────────────────────
    # Sorted by total time descending so the heaviest agent surfaces first —
    # that's the lever for shaving wall-clock per tick.
    if latency:
        rows = []

        # Pre-compute (agent, mean, total, min, max, count) tuples once so
        # the sort key and the row formatting share the same values.
        prepared = []
        for agent, stats in latency.items():
            count = int(stats["count"])
            total = float(stats["sum"])
            mean  = total / count if count > 0 else 0.0
            prepared.append((
                agent, mean, total, float(stats["min"]), float(stats["max"]), count,
            ))

        prepared.sort(key=lambda row: row[2], reverse=True)

        for agent, mean, total, lo, hi, count in prepared:
            rows.append(
                f"| `{agent}` | {count} | {mean:,.0f} | {lo:,.0f} | {hi:,.0f} | {total:,.0f} |"
            )

        latency_block = (
            "\n"
            "| Agent | Invocations | Mean (ms) | Min (ms) | Max (ms) | Total (ms) |\n"
            "|---|---:|---:|---:|---:|---:|\n"
            + "\n".join(rows)
            + "\n"
        )
    else:
        latency_block = "\n_no per-agent latency recorded_\n"

    return (
        "\n"
        "## Pipeline efficiency\n\n"
        f"- LLM tokens — **input {tokens['input']:,}**, "
        f"**output {tokens['output']:,}**, "
        f"**total {tokens['total']:,}** "
        f"across {tokens['generate_content_spans']:,} model calls\n"
        f"{cache_line}\n"
        f"- LLM retries: **{retries}**\n"
        f"- Hallucinated stances (sell-on-non-held etc., dropped silently): "
        f"**{hallucinations}**\n"
        f"- Ticks observed: **{ticks}**\n"
        "\n"
        "### Per-agent latency\n"
        f"{latency_block}"
    )


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

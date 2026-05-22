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

def report(run_dir: Path, settings: BacktestSettings, *, window: str) -> None:
    """Generate ``report/equity_curve.png`` and ``report/metrics.md``; backfill forwards.

    Reads portfolio snapshots from the run's ``db.sqlite``, writes an equity
    curve PNG and a Markdown metrics file, then walks ``decisions/`` to
    backfill forward returns from the golden cache.

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
    # Per-window golden cache — derived from ``backtests_root`` + window.
    from backtest.settings import cache_path_for_window
    cache          = CachedDataStore(cache_path_for_window(settings, window))
    vs_spy_delta   = _compute_vs_spy_delta(equity, cache)

    _write_equity_curve(
        equity,
        report_dir / "equity_curve.png",
        cache=cache,
        starting_cash=equity[0][1],
    )
    _write_metrics(
        equity,
        report_dir / "metrics.md",
        fill_count=fill_count,
        win_rate=win_rate,
        vs_spy_delta=vs_spy_delta,
        ticks_per_day=len(settings.ticks_per_day),
    )

    # ── pipeline-efficiency section (tokens, latency, cache, retries) ────────
    # Driven by the per-tick observability artefacts written under ``obs/`` by
    # ``observability.drain.drain_tick``.  Older runs (or runs that skipped the
    # observability install) won't have the directory — skip silently.
    obs_dir = run_dir / "obs"
    if obs_dir.exists():
        aggregates = _aggregate_obs_artefacts(obs_dir)
        if aggregates is not None:
            section = _format_obs_section(aggregates)
            with (report_dir / "metrics.md").open("a", encoding="utf-8") as f:
                f.write(section)

    # ── forward-return backfill ───────────────────────────────────────────────
    # ``cache`` was already opened above for the SPY delta calculation; reuse it.
    horizons = settings.forward_return_horizons_days
    _backfill_forward_returns(run_dir / "decisions", cache, horizons)


# ── Private helpers ───────────────────────────────────────────────────────────

def _write_equity_curve(
    series: list[tuple[datetime, float]],
    outpath: Path,
    *,
    cache: CachedDataStore,
    starting_cash: float,
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
    """
    fig = _build_equity_figure(
        series, cache=cache, starting_cash=starting_cash,
    )
    fig.tight_layout()
    fig.savefig(outpath)
    plt.close(fig)


def _build_equity_figure(
    series: list[tuple[datetime, float]],
    *,
    cache: CachedDataStore,
    starting_cash: float,
) -> Figure:
    """Build the equity-curve ``Figure`` with portfolio, SPY, and initial-funds lines.

    Renders three lines on a single ``$`` axis:

    - **Portfolio** — solid blue, the raw ``series`` values.
    - **SPY (rebased)** — solid orange, SPY buy-and-hold rebased so the
      first point equals ``starting_cash``.  Skipped silently if SPY is
      absent from the cache or the read raises (a descriptive ``N/A`` row
      is already written into ``metrics.md`` by ``_compute_vs_spy_delta``).
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

    # ── SPY overlay (rebased to starting_cash) ───────────────────────────────
    # Best-effort: missing or broken SPY data must not abort the report — the
    # metrics file already surfaces "SPY not in cache" so a noisy chart
    # annotation would add clutter without conveying anything new.
    start_date = xs[0].date()
    end_date   = xs[-1].date()
    try:
        spy_bars = cache.read_ohlcv("SPY", start_date, end_date)
    except Exception:
        logger.exception("Failed to read SPY OHLCV for equity-curve overlay")
        spy_bars = []

    if spy_bars:
        # Rebase so the first plotted SPY point sits exactly at starting_cash.
        # Using close-of-every-bar (including the first) anchors both lines
        # visually at (t0, starting_cash) — intentionally different from
        # _compute_vs_spy_delta which uses open-of-first-bar for its metric.
        spy_anchor = spy_bars[0].close
        if spy_anchor > 0:
            spy_xs = [b.timestamp for b in spy_bars]
            spy_ys = [starting_cash * b.close / spy_anchor for b in spy_bars]
            ax.plot(spy_xs, spy_ys, label="SPY (rebased)", color="tab:orange")

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
    ticks_per_day: int = 1,
) -> None:
    """Compute performance metrics and write a Markdown report to ``outpath``.

    Metrics written (spec §end-of-window):
    - Total return as a percentage of starting value.
    - Annualised Sharpe ratio (252 trading days × ``ticks_per_day``).
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
    ticks_per_day:
        Number of ticks per trading day in the schedule (e.g. 2 for the
        default open + close policy).  Used to scale the Sharpe
        annualisation factor — the ``series`` contains one return per
        tick, so the annualisation must compound across both the trading
        calendar (252) and the per-day tick count.  Defaults to 1 so
        callers with no schedule context (ad-hoc replays) still get a
        sensible figure.
    """
    start_v = series[0][1]
    end_v   = series[-1][1]
    total_return = (end_v - start_v) / start_v

    # ── Sharpe ───────────────────────────────────────────────────────────────
    # Per-tick returns: assumes ticks are evenly spaced.  Annualisation
    # scales the per-tick Sharpe by ``sqrt(252 * ticks_per_day)`` — a
    # two-ticks-per-day schedule produces twice as many returns per year
    # as a daily-close-only schedule, so the naïve ``sqrt(252)`` factor
    # under-reports Sharpe by ``sqrt(ticks_per_day)``.
    rets = []
    for (_, v0), (_, v1) in zip(series, series[1:], strict=False):
        if v0 != 0:
            rets.append((v1 - v0) / v0)

    if len(rets) >= 2 and statistics.pstdev(rets) > 0:
        annualisation = (252 * ticks_per_day) ** 0.5
        sharpe = (statistics.mean(rets) / statistics.pstdev(rets)) * annualisation
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
        f"- Closed round-trips: **{fill_count}**\n"
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
        "agent_latency_ms": agent_latency_ms,
        "cache": {
            "hits":   cache_hits,
            "misses": cache_misses,
        },
        "retries":         retry_count,
        "ticks_observed":  ticks_observed,
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

    tokens   = aggs["tokens"]
    cache    = aggs["cache"]
    latency  = aggs["agent_latency_ms"]
    retries  = aggs["retries"]
    ticks    = aggs["ticks_observed"]

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

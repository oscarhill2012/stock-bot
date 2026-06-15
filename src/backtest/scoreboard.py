"""Analyst predictive-power scoreboard (Phase 12).

Computes a baseline-corrected signal-quality metric for every analyst in a
completed backtest run, comparing their directional lean (bullish / bearish /
neutral) against realised forward returns from the per-window golden cache.

The metric isolates SELECTION skill by removing the market-wide move via a
per-tick cross-sectional demean:

    base_price   = phase-matched ticker price at the verdict's tick
                   (bar.open for open-phase ticks, bar.close for close-phase)
    fwd_return_h = (forward_close_h − base_price) / base_price
    excess_h     = fwd_return_h − mean(fwd_return_h over all tickers in that tick)
    position     = +1 / −1 / 0  for  bullish / bearish / neutral
    score_h      = position × excess_h

Aggregated per analyst × horizon × lean-subset {all, bullish, bearish}:
  - mean excess in basis points  (headline, cross-window comparable)
  - hit rate                     (fraction of non-neutral verdicts with score > 0)
  - n                            (scored verdicts; window-edge excluded rows reduce n)
  - t-stat / p-value             (scipy.stats.ttest_1samp to separate signal from noise)

Public surface
--------------
``build_analyst_scoreboard(db_path, cache, horizons) → ScoreboardResult``
    Pure function.  Reads ``analyst_evidence`` from the run's ``db.sqlite``
    and prices from the passed ``CachedDataStore``; no LLM, no network.

``render_scoreboard_md(result) → str``
    Formats a ``ScoreboardResult`` as a Markdown section, suitable for
    appending to ``report/metrics.md``.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import NamedTuple

import numpy as np
import scipy.stats
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backtest.cache.store import CachedDataStore
from backtest.reporting import _forward_close
from orchestrator.persistence import AnalystEvidenceRow

logger = logging.getLogger(__name__)


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoreboardCell:
    """Aggregated statistics for one (analyst, horizon, subset) combination.

    Attributes
    ----------
    analyst:
        Name of the analyst (e.g. ``"technical"``, ``"fundamental"``).
    horizon:
        Forward horizon in calendar days (e.g. 1, 5, 20).
    subset:
        Lean subset — ``"all"``, ``"bullish"``, or ``"bearish"``.
    n:
        Number of verdicts actually scored (coverage; reduced at window edge).
    mean_excess_bps:
        Mean of ``score_h`` in basis points.  Positive means the analyst's
        directional calls outperformed the tick's cross-sectional mean.
    hit_rate:
        Fraction of non-neutral verdicts with ``score_h > 0``.  ``math.nan``
        when n == 0 (no data to compute).
    t_stat:
        t-statistic from ``scipy.stats.ttest_1samp(scores, 0.0)``.
        ``math.nan`` when n < 2.
    p_value:
        Two-sided p-value from the same test.  ``math.nan`` when n < 2.
    """
    analyst:         str
    horizon:         int
    subset:          str
    n:               int
    mean_excess_bps: float
    hit_rate:        float
    t_stat:          float
    p_value:         float


@dataclass
class ScoreboardResult:
    """Complete scoreboard output for one backtest run.

    Stores cells in a dict keyed by ``(analyst, horizon, subset)`` for O(1)
    access.  Use ``cell()`` for convenient lookup with a clear error on miss.

    Attributes
    ----------
    cells:
        Mapping from ``(analyst, horizon, subset)`` to ``ScoreboardCell``.
    analysts:
        Ordered list of analyst names present in the data.
    horizons:
        Forward horizons (calendar days) that were scored.
    """
    cells:     dict[tuple[str, int, str], ScoreboardCell] = field(default_factory=dict)
    analysts:  list[str]  = field(default_factory=list)
    horizons:  list[int]  = field(default_factory=list)

    def cell(self, *, analyst: str, horizon: int, subset: str) -> ScoreboardCell:
        """Return the cell for ``(analyst, horizon, subset)``.

        Raises ``KeyError`` with a clear message if the combination is not
        present — per the project's raise-don't-return-None policy.

        Parameters
        ----------
        analyst:
            Analyst name (e.g. ``"technical"``).
        horizon:
            Horizon in calendar days.
        subset:
            One of ``"all"``, ``"bullish"``, ``"bearish"``.
        """
        key = (analyst, horizon, subset)
        if key not in self.cells:
            raise KeyError(
                f"No scoreboard cell for (analyst={analyst!r}, "
                f"horizon={horizon}, subset={subset!r}).  "
                f"Available analysts: {self.analysts}, "
                f"horizons: {self.horizons}."
            )
        return self.cells[key]


# ── Internal types ────────────────────────────────────────────────────────────

class _VerdictKey(NamedTuple):
    """Key for grouping verdicts by analyst + ticker + tick."""
    analyst:  str
    ticker:   str
    tick_id:  str


# ── Public API ────────────────────────────────────────────────────────────────

def build_analyst_scoreboard(
    *,
    db_path: Path,
    cache: CachedDataStore,
    horizons: list[int],
) -> ScoreboardResult:
    """Build the analyst predictive-power scoreboard from a completed run.

    Pure function: reads ``analyst_evidence`` from the run's ``db.sqlite``
    and prices from ``cache``; produces no side-effects.  Suitable for unit
    testing on a small fixture DB.

    Algorithm
    ---------
    1. Load all ``AnalystEvidenceRow`` records.
    2. For each record, look up the phase-matched ``base_price`` from the
       cache (``bar.open`` for open-phase ticks, ``bar.close`` for close-phase;
       the 17:00 UTC threshold matches ``_spy_benchmark_series`` in reporting.py).
    3. For each horizon ``h``, look up ``forward_close_h`` via the shared
       ``_forward_close`` helper (same first-available-bar logic as the
       backfill).
    4. Compute per-tick cross-sectional means and demean each verdict's
       ``fwd_return_h`` within its ``(tick_id, h)`` group.
    5. Aggregate per ``(analyst, horizon, subset)`` with mean, hit-rate, n,
       t-stat and p-value.

    Parameters
    ----------
    db_path:
        Path to the run's ``db.sqlite`` file containing ``analyst_evidence``.
    cache:
        ``CachedDataStore`` backed by the per-window golden-cache SQLite.
    horizons:
        List of forward horizons in calendar days (e.g. ``[1, 5, 20]``).

    Returns
    -------
    ScoreboardResult
        Fully populated result with one ``ScoreboardCell`` per
        ``(analyst, horizon, subset)`` combination.
    """
    # ── 1. Load verdict rows ─────────────────────────────────────────────────
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with Session(engine) as s:
        rows: list[AnalystEvidenceRow] = (
            s.execute(select(AnalystEvidenceRow)).scalars().all()
        )

    if not rows:
        logger.warning("scoreboard: no analyst_evidence rows found in %s", db_path)
        return ScoreboardResult()

    # ── 2. Resolve base prices and forward closes per verdict ────────────────
    # Structure: row_index → {h: (base_price, fwd_close)}
    # We store both so forward returns can be computed after cross-sectional
    # grouping.  Rows missing a base price are excluded entirely (n=0 at all
    # horizons — no sentinel values injected).

    row_base:     dict[int, float]                    = {}   # row idx → base_price
    row_fwd_close: dict[int, dict[int, float | None]] = {}  # row idx → {h: fwd_close|None}

    for idx, row in enumerate(rows):
        recorded_at = _ensure_aware(row.recorded_at)
        as_of_date  = recorded_at.date()

        # Phase-matched base price: bar.open for open-phase (hour < 17 UTC),
        # bar.close for close-phase.  Matches the _spy_benchmark_series rule.
        base_bars = cache.read_ohlcv(row.ticker, as_of_date, as_of_date)
        if not base_bars:
            # No bar for this ticker on this date → exclude verdict entirely.
            logger.debug(
                "scoreboard: no base bar for %s on %s — verdict excluded",
                row.ticker, as_of_date,
            )
            continue

        base_bar   = base_bars[0]
        base_price = base_bar.open if recorded_at.hour < 17 else base_bar.close

        if base_price <= 0:
            logger.debug(
                "scoreboard: base_price ≤ 0 for %s on %s — verdict excluded",
                row.ticker, as_of_date,
            )
            continue

        row_base[idx] = base_price

        # Forward closes per horizon.
        fwd_by_h: dict[int, float | None] = {}
        for h in horizons:
            fwd_by_h[h] = _forward_close(cache, row.ticker, as_of_date, h)
        row_fwd_close[idx] = fwd_by_h

    # ── 3. Compute fwd_returns per DISTINCT (tick_id, ticker, h) ────────────────
    # The cross-sectional mean is a market fact — one return per ticker per tick,
    # regardless of how many analysts covered that ticker.  We record the
    # canonical fwd_return for each (tick_id, ticker) pair (using the first
    # analysed row's prices, which are deterministic from the cache).

    # ticker_fwd_by_tick[(tick_id, ticker, h)] = fwd_return (a single market fact).
    ticker_fwd_by_tick: dict[tuple[str, str, int], float] = {}

    for idx, row in enumerate(rows):
        if idx not in row_base:
            continue   # excluded above

        base_price = row_base[idx]
        for h, fwd_close_val in row_fwd_close[idx].items():
            if fwd_close_val is None:
                continue  # window edge — excluded from this horizon

            key = (row.tick_id, row.ticker, h)
            if key not in ticker_fwd_by_tick:
                # First row for this (tick, ticker, h) — record its fwd_return.
                # Subsequent analyst rows for the same ticker in the same tick
                # are skipped for the cross-sectional universe (they would
                # double-count the same market event).
                fwd_return = (fwd_close_val - base_price) / base_price
                ticker_fwd_by_tick[key] = fwd_return

    # Build per-(tick_id, h) cross-sectional means over distinct tickers.
    # cs_mean_by_tick[(tick_id, h)] = mean(fwd_return) over distinct tickers
    #   that have a forward return in this group.
    cs_mean_by_tick: dict[tuple[str, int], float] = {}
    # Collect the set of (tick_id, h) pairs from the distinct-ticker universe.
    tick_h_tickers: dict[tuple[str, int], list[float]] = defaultdict(list)
    for (tick_id, _ticker, h), fwd_return in ticker_fwd_by_tick.items():
        tick_h_tickers[(tick_id, h)].append(fwd_return)

    for (tick_id, h), fwd_returns in tick_h_tickers.items():
        cs_mean_by_tick[(tick_id, h)] = float(np.mean(fwd_returns))

    # ── 4. Compute per-verdict excess and accumulate scores ──────────────────
    # For each analyst row, look up the cross-sectional mean for its (tick_id, h)
    # and compute excess = ticker_fwd_return − cs_mean.  Multiply by the
    # lean position to get the score.

    # Scores: (analyst, h) → subset → list[score]
    score_store: dict[
        tuple[str, int],
        dict[str, list[float]],
    ] = defaultdict(lambda: {"all": [], "bullish": [], "bearish": []})

    for idx, row in enumerate(rows):
        if idx not in row_base:
            continue  # no base bar — excluded entirely

        for h in horizons:
            fwd_close_val = row_fwd_close[idx].get(h)
            if fwd_close_val is None:
                continue  # window edge — excluded from this horizon

            ticker_key = (row.tick_id, row.ticker, h)
            if ticker_key not in ticker_fwd_by_tick:
                continue  # should not happen given the earlier loop

            ticker_fwd = ticker_fwd_by_tick[ticker_key]
            cs_mean    = cs_mean_by_tick.get((row.tick_id, h), 0.0)
            excess     = ticker_fwd - cs_mean
            position   = _lean_to_position(row.lean)
            score      = float(position * excess)

            key = (row.analyst, h)
            score_store[key]["all"].append(score)
            if row.lean == "bullish":
                score_store[key]["bullish"].append(score)
            elif row.lean == "bearish":
                score_store[key]["bearish"].append(score)

    # ── 5. Aggregate into ScoreboardResult ───────────────────────────────────
    analysts_seen: list[str] = sorted({r.analyst for r in rows})
    result = ScoreboardResult(analysts=analysts_seen, horizons=sorted(horizons))

    for analyst in analysts_seen:
        for h in sorted(horizons):
            store = score_store.get(
                (analyst, h),
                {"all": [], "bullish": [], "bearish": []},
            )
            for subset in ("all", "bullish", "bearish"):
                scores = store.get(subset, [])

                # Hit-rate is defined over NON-NEUTRAL verdicts only (spec).  For
                # the ``all`` subset the directional calls are exactly
                # bullish ∪ bearish; for the single-lean subsets every score is
                # already directional.  This keeps a mostly-neutral analyst
                # (e.g. fundamental, bug #21) from reading as "almost always
                # wrong" when it has simply declined to bet.
                if subset == "all":
                    directional = store.get("bullish", []) + store.get("bearish", [])
                else:
                    directional = scores

                cell = _aggregate(
                    analyst=analyst,
                    horizon=h,
                    subset=subset,
                    scores=scores,
                    directional_scores=directional,
                )
                result.cells[(analyst, h, subset)] = cell

    return result


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_scoreboard_md(result: ScoreboardResult) -> str:
    """Render a ``ScoreboardResult`` as a Markdown section.

    Produces a section suitable for appending to ``report/metrics.md``.
    Layout: one sub-table per analyst, with rows for each
    (horizon × subset) combination.

    Parameters
    ----------
    result:
        Output of ``build_analyst_scoreboard``.

    Returns
    -------
    str
        Markdown string beginning with a ``##`` heading.
    """
    lines: list[str] = [
        "\n## Analyst predictive-power scoreboard\n",
        (
            "Metric: mean excess return (bps) = analyst lean × "
            "(ticker fwd-return − per-tick cross-sectional mean).  "
            "Positive = lean predicted the relative outperformer.  "
            "Coverage (n) excludes window-edge verdicts where no forward bar exists.\n"
        ),
    ]

    if not result.analysts:
        lines.append("_No analyst evidence rows found._\n")
        return "\n".join(lines)

    for analyst in result.analysts:
        lines.append(f"\n### {analyst}\n")
        lines.append(
            "| horizon | subset   | n | mean excess (bps) | hit rate | t-stat | p-value |"
        )
        lines.append(
            "|---------|----------|---|------------------:|----------|-------:|---------|"
        )

        for h in result.horizons:
            for subset in ("all", "bullish", "bearish"):
                try:
                    c = result.cell(analyst=analyst, horizon=h, subset=subset)
                except KeyError:
                    continue

                mean_bps_str = f"{c.mean_excess_bps:+.1f}" if math.isfinite(c.mean_excess_bps) else "N/A"
                hit_str      = f"{c.hit_rate:.1%}"  if math.isfinite(c.hit_rate) else "N/A"
                t_str        = f"{c.t_stat:+.2f}"   if math.isfinite(c.t_stat)   else "N/A"
                p_str        = f"{c.p_value:.3f}"   if math.isfinite(c.p_value)  else "N/A"

                lines.append(
                    f"| +{h}d     | {subset:<8} | {c.n} | {mean_bps_str:>17} | {hit_str:<8} | {t_str:>6} | {p_str:>7} |"
                )

    return "\n".join(lines) + "\n"


# ── Private helpers ───────────────────────────────────────────────────────────

def _lean_to_position(lean: str) -> int:
    """Convert a lean string to its directional position (+1 / −1 / 0).

    Parameters
    ----------
    lean:
        One of ``"bullish"``, ``"bearish"``, ``"neutral"``.

    Returns
    -------
    int
        +1 for bullish, −1 for bearish, 0 for neutral (or any unknown value).
    """
    if lean == "bullish":
        return 1
    if lean == "bearish":
        return -1
    return 0


def _aggregate(
    *,
    analyst: str,
    horizon: int,
    subset: str,
    scores: list[float],
    directional_scores: list[float],
) -> ScoreboardCell:
    """Aggregate a list of scores into a ``ScoreboardCell``.

    Parameters
    ----------
    analyst:
        Analyst name.
    horizon:
        Forward horizon in calendar days.
    subset:
        Lean subset (``"all"``, ``"bullish"``, ``"bearish"``).
    scores:
        List of ``score_h = position × excess`` values for the whole subset,
        including neutral verdicts (which contribute an exact ``0``).  Drives
        ``n``, the mean excess, and the t-stat.  May be empty.
    directional_scores:
        The non-neutral subset of ``scores`` — i.e. only bullish/bearish
        verdicts.  Drives the hit-rate denominator so a mostly-neutral analyst
        is not scored as "almost always wrong" merely for declining to bet.
        For the single-lean subsets this is identical to ``scores``.

    Returns
    -------
    ScoreboardCell
        Populated cell.  All statistics are ``math.nan`` when ``scores`` is
        empty; t-stat and p-value are ``math.nan`` when fewer than 2 scores;
        hit-rate is ``math.nan`` when there are no directional verdicts.
    """
    n = len(scores)

    if n == 0:
        return ScoreboardCell(
            analyst=analyst, horizon=horizon, subset=subset,
            n=0,
            mean_excess_bps=math.nan,
            hit_rate=math.nan,
            t_stat=math.nan,
            p_value=math.nan,
        )

    arr             = np.array(scores, dtype=float)
    mean_excess     = float(np.mean(arr))
    mean_excess_bps = mean_excess * 10_000

    # Hit rate: fraction of NON-NEUTRAL verdicts strictly greater than 0.
    # Neutral verdicts (score == 0) are excluded from the denominator so that
    # an analyst which abstains rather than bets is not penalised.
    n_directional = len(directional_scores)
    if n_directional == 0:
        hit_rate = math.nan
    else:
        d_arr    = np.array(directional_scores, dtype=float)
        hit_rate = float(np.sum(d_arr > 0) / n_directional)

    # t-stat / p-value via scipy.  Requires at least 2 observations.
    if n < 2:
        t_stat  = math.nan
        p_value = math.nan
    else:
        t_result = scipy.stats.ttest_1samp(arr, 0.0)
        t_stat   = float(t_result.statistic)
        p_value  = float(t_result.pvalue)

    return ScoreboardCell(
        analyst=analyst, horizon=horizon, subset=subset,
        n=n,
        mean_excess_bps=mean_excess_bps,
        hit_rate=hit_rate,
        t_stat=t_stat,
        p_value=p_value,
    )


def _ensure_aware(dt: object) -> object:
    """Ensure a datetime is timezone-aware (UTC), handling naive datetimes.

    SQLite stores datetimes without timezone info.  Treat naive datetimes as
    UTC so the intraday-phase classifier (``dt.hour < 17``) is consistent.

    Parameters
    ----------
    dt:
        A ``datetime`` object, possibly naive.

    Returns
    -------
    datetime
        A timezone-aware ``datetime`` in UTC.
    """
    from datetime import datetime as _dt
    if isinstance(dt, _dt) and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt

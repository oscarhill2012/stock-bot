"""Analyst predictive-power scoreboard (Phase 12, revised Phase 14).

Computes a baseline-corrected signal-quality metric for every analyst in a
completed backtest run, comparing their directional lean (bullish / bearish /
neutral) against realised forward returns from the per-window golden cache.

The metric isolates SELECTION skill by removing the market-wide (or sector-wide)
move via a per-tick cross-sectional demean:

    base_price   = phase-matched ticker price at the verdict's tick
                   (bar.open for open-phase ticks, bar.close for close-phase)
    fwd_return_h = (forward_close_h − base_price) / base_price
    excess_h     = fwd_return_h − mean(fwd_return_h over peer group in that tick)
    position     = +1 / −1 / 0  for  bullish / bearish / neutral
    score_h      = position × excess_h

Peer group depends on ``neutralise_by``:
  ``"sector"``   — mean over tickers in the same GICS sector (from
                   ``CachedDataStore.read_company_ratios``).  Falls back to
                   universe when sector data is absent.
  ``"universe"`` — mean over all tickers present in the tick (original behaviour).

Aggregated per analyst × horizon × lean-subset {directional, bullish, bearish}:
  - mean excess in basis points  (headline, cross-window comparable)
  - hit rate                     (fraction of non-neutral verdicts with score > 0)
  - n                            (scored verdicts; window-edge excluded rows reduce n)
  - t-stat / p-value             (scipy.stats.ttest_1samp to separate signal from noise)

Phase 14 (iter-11) — directional-only scoring + confidence gradient
---------------------------------------------------------------------
1. **Neutral verdicts excluded from "directional"** — previously the "all"
   subset included EVERY verdict, with neutral contributing an exact score
   of 0.  This diluted the mean excess and inflated n for analysts that
   abstain often (e.g. fundamental).  "directional" is now redefined as
   bullish ∪ bearish ONLY; neutral verdicts contribute nothing to mean, n,
   t-stat, or hit rate.  Their abstention rate is reported separately via
   ``ScoreboardResult.coverage()`` (see ``CoverageDiagnostic``).
2. **Confidence-gradient view** — ``ScoreboardResult.confidence_gradient()``
   buckets an analyst's directional calls by recorded confidence (terciles
   by default, data-derived per analyst — see ``ConfidenceBucket``) and
   reports mean excess / n / hit rate / t-stat per bucket.  This is the
   decision-relevant output: if high-confidence calls outperform
   low-confidence calls, the analyst has extractable edge but is
   miscalibrated (fix: calibration / abstain-when-unsure); if not, it is a
   capability problem.

Phase 14 defect fixes
---------------------
1. **Cache-replay dedup** — the report cache replays a cached LLM verdict
   across many subsequent ticks (fundamental verdicts change slowly → ~83 %
   cache-hit rate).  Counting each replayed tick as a separate observation
   amplifies one confidently-wrong fresh call 6× into the score.

   Fix: the ``analyst_evidence`` table carries the report cache's own
   ``input_hash`` (the blake2b digest the cache was keyed on — populated only
   for the LLM analysts that consult it, fundamental and news).  Dedup is
   EXACT: every row sharing ``(analyst, ticker, input_hash)`` within a window
   is the SAME cached verdict replayed, and only the FIRST occurrence (tick
   order) is kept as a SINGLE observation, anchored at that first tick.  The
   forward return is measured from that anchor tick.

   Rows with no ``input_hash`` (deterministic analysts — technical,
   smart_money, social — which recompute every tick and have no cache to
   replay) are never deduped; each is scored as its own fresh observation.
   An analyst with a MIX of hashed and unhashed rows within one window is a
   plumbing bug, not a legitimate state, and raises loudly naming the analyst
   and both counts rather than guessing which rule applies.

2. **Per-analyst primary horizon** — news signals decay in ~1 day; scoring
   them at +20d measures noise.  The ``primary_horizon_by_analyst`` config key
   (``dict[str, int]``) sets per-analyst primary horizons.  All horizons are
   still scored and reported; the primary horizon drives the headline rank
   column.  Unconfigured analysts default to ``max(horizons)``.

3. **Sector neutralisation** — the ``neutralise_by`` parameter (default
   ``"sector"``) subtracts the sector-peer mean rather than the whole-universe
   mean so that a correct single-name call is not swamped by a sector-wide move.
   Falls back to universe when ``read_company_ratios`` returns ``None``.

Public surface
--------------
``build_analyst_scoreboard(db_path, cache, horizons, *, primary_horizon_by_analyst,
                           neutralise_by) → ScoreboardResult``
    Pure function.  Reads ``analyst_evidence`` from the run's ``db.sqlite``
    and prices from the passed ``CachedDataStore``; no LLM, no network.

``build_pooled_analyst_scoreboard(runs, horizons, *, primary_horizon_by_analyst,
                                  neutralise_by, inference) → ScoreboardResult``
    Pools observations from MULTIPLE ``(db_path, cache, window_id)`` triples
    into a single aggregated scoreboard.  Each window's own dedup and peer-mean
    computation runs in isolation (see ``_score_window_observations``); only the
    final aggregation step combines the windows.  Calling this with exactly one
    run is byte-identical to ``build_analyst_scoreboard`` on that run.

``render_scoreboard_md(result) → str``
    Formats a ``ScoreboardResult`` as a Markdown section, suitable for
    appending to ``report/metrics.md``.

Phase 14 (iter-2) — multi-window pooling
-----------------------------------------
``build_analyst_scoreboard`` measures one run against one window's cache.
To get a more reliable read on analyst skill we often want to pool several
backtest windows (different market regimes) together.  Three correctness
traps had to be designed around explicitly:

1. **Peer groups must stay strictly within-window.**  The per-tick
   cross-sectional mean is keyed on ``tick_id``, which is already unique per
   window (window run-ids are baked into tick generation) — so a correct
   implementation that keys cs-means on ``tick_id`` cannot blend two windows'
   peer groups by construction.  ``_score_window_observations`` computes
   every peer mean using ONLY that window's own cache and ticks, before any
   pooling step sees the data.
2. **Dedup must run per-window, before pooling.**  If the exact-``input_hash``
   dedup ran on the concatenated multi-window record list, a ticker's last
   verdict in window A could be wrongly collapsed with its first verdict in
   window B if a report-cache collision ever produced the same hash in both
   (extremely unlikely, but not a risk worth taking).  ``_score_window_observations``
   runs dedup internally, scoped to its own window's rows only.
3. **Cluster-robust SE clusters by ``(ticker, window_id)`` when pooling.**
   Two windows months apart are not autocorrelated with each other, so
   clustering pooled data by bare ticker would over-cluster (collapse two
   independent regimes into one cluster, inflating the SE and shrinking
   apparent significance).  The cluster label threaded into ``_aggregate`` /
   ``_cluster_robust_ttest`` is ``ticker`` for the single-window path
   (unchanged behaviour) and ``(ticker, window_id)`` for the pooled path.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Literal, NamedTuple

import numpy as np
import scipy.stats
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backtest.cache.store import CachedDataStore
from backtest.reporting import _forward_close
from orchestrator.persistence import AnalystEvidenceRow, TickerEvidenceRow, TickerStanceRow

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
        Lean subset — ``"directional"`` (bullish ∪ bearish; neutral
        EXCLUDED entirely — see module docstring "Phase 14 (iter-11)"),
        ``"bullish"``, or ``"bearish"``.
    n:
        Number of verdicts actually scored (coverage; reduced at window edge).
        After Phase 14 dedup, this counts UNIQUE fresh-verdict observations,
        not total replayed rows.  Neutral verdicts contribute 0 to this count
        for the ``"directional"`` subset (they are excluded, not zero-scored).
    mean_excess_bps:
        Mean of ``score_h`` in basis points.  Positive means the analyst's
        directional calls outperformed the tick's peer-group mean.  ``nan``
        when n == 0 (no directional data to score — e.g. an all-neutral
        analyst at this horizon).
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


@dataclass(frozen=True)
class CoverageDiagnostic:
    """Per-analyst neutral/abstention coverage diagnostic (Phase 14, iter-11).

    Reported ALONGSIDE the scoreboard cells, never folded into them — this is
    a coverage/abstention measure, not a predictive-power measure.  Computing
    it as a defined ratio requires a denominator decision; see the docstring
    on ``ScoreboardResult.coverage`` for the chosen convention.

    Attributes
    ----------
    analyst:
        Analyst name.
    horizon:
        Forward horizon in calendar days this diagnostic was computed for.
    n_neutral:
        Count of deduplicated, forward-bar-scoreable verdicts with lean
        ``"neutral"`` at this horizon.
    n_scoreable:
        Total deduplicated, forward-bar-scoreable verdicts at this horizon
        (neutral + bullish + bearish).  Verdicts excluded for missing price
        data (window edge / no base bar) are excluded from this denominator
        too — they are a data-availability gap, not an abstention.
    neutral_rate:
        ``n_neutral / n_scoreable``.  ``math.nan`` when ``n_scoreable == 0``.
    """
    analyst:      str
    horizon:      int
    n_neutral:    int
    n_scoreable:  int
    neutral_rate: float


@dataclass(frozen=True)
class ConfidenceBucket:
    """One bucket of the confidence-gradient view (Phase 14, iter-11).

    Attributes
    ----------
    bucket_index:
        0-based index of this bucket, ordered from LOWEST to HIGHEST
        confidence (bucket 0 = lowest tercile, last = highest).
    confidence_range:
        ``(low, high)`` bounds of the confidence values that fell into this
        bucket (inclusive), purely for display/diagnostic purposes.
    n:
        Number of directional (non-neutral) observations in this bucket.
    mean_excess_bps:
        Mean ``score_h`` in basis points for this bucket.  ``nan`` if n == 0.
    hit_rate:
        Fraction of this bucket's observations with ``score_h > 0``.
        ``nan`` if n == 0.
    t_stat:
        t-statistic for this bucket (cluster-robust or naive, matching
        whatever inference mode produced the underlying scoreboard).
        ``nan`` if n < 2.
    p_value:
        Two-sided p-value paired with ``t_stat``.  ``nan`` if n < 2.
    """
    bucket_index:     int
    confidence_range: tuple[float, float]
    n:                int
    mean_excess_bps:  float
    hit_rate:         float
    t_stat:           float
    p_value:          float


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
    _primary_horizons:
        Internal mapping from analyst name to its primary scoring horizon.
        Accessed via ``primary_horizon(analyst)``.
    """
    cells:              dict[tuple[str, int, str], ScoreboardCell] = field(default_factory=dict)
    analysts:           list[str]                                  = field(default_factory=list)
    horizons:           list[int]                                  = field(default_factory=list)
    _primary_horizons:  dict[str, int]                             = field(default_factory=dict)

    # Internal: the flat observation list feeding this result, retained so
    # ``coverage()`` and ``confidence_gradient()`` can recompute their own
    # bespoke aggregations (different subsetting / bucketing rules from the
    # main cells) without re-reading the database.  Never part of the public
    # contract — callers use the methods below, not this field directly.
    _records:           list[_ObservationRecord]                   = field(default_factory=list, repr=False)
    _inference:         str                                        = field(default="cluster_ticker", repr=False)
    _cluster_key_fn:    object                                      = field(default=None, repr=False)

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
            One of ``"directional"``, ``"bullish"``, ``"bearish"``.
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

    def primary_horizon(self, analyst: str) -> int:
        """Return the primary scoring horizon for ``analyst``.

        The primary horizon is the one used for headline ranking.  It comes
        from ``primary_horizon_by_analyst`` config (set during
        ``build_analyst_scoreboard``).  Analysts not in the config map default
        to ``max(horizons)``.

        Parameters
        ----------
        analyst:
            Analyst name (e.g. ``"news"``, ``"fundamental"``).

        Returns
        -------
        int
            Primary horizon in calendar days.

        Raises
        ------
        KeyError
            If ``analyst`` is not present in this result at all
            (i.e. not in ``self.analysts``).
        """
        if analyst not in self.analysts:
            raise KeyError(
                f"Analyst {analyst!r} not found in scoreboard result.  "
                f"Available: {self.analysts}"
            )
        return self._primary_horizons.get(analyst, max(self.horizons))

    def coverage(self, *, analyst: str, horizon: int) -> CoverageDiagnostic:
        """Return the neutral/abstention coverage diagnostic for ``analyst``.

        Denominator decision (Phase 14, iter-11): ``n_scoreable`` is the
        FORWARD-BAR-SCOREABLE set at this horizon — every deduplicated
        verdict that had both a base price and a forward close — NOT the raw
        ``analyst_evidence`` row count.  Verdicts excluded for missing price
        data (window edge) are excluded from this denominator too, since
        that absence reflects data availability, not the analyst abstaining.
        This makes ``neutral_rate`` directly comparable to the
        ``"directional"`` cell's ``n`` at the same horizon:
        ``n_neutral + directional.n == n_scoreable``.

        Parameters
        ----------
        analyst:
            Analyst name.
        horizon:
            Forward horizon in calendar days.

        Returns
        -------
        CoverageDiagnostic
            ``n_neutral`` / ``n_scoreable`` / ``neutral_rate`` for this
            (analyst, horizon).  ``neutral_rate`` is ``math.nan`` when
            ``n_scoreable == 0``.

        Raises
        ------
        KeyError
            If ``analyst`` is not present in this result.
        """
        if analyst not in self.analysts:
            raise KeyError(
                f"Analyst {analyst!r} not found in scoreboard result.  "
                f"Available: {self.analysts}"
            )

        relevant = [
            rec for rec in self._records
            if rec.analyst == analyst and rec.horizon == horizon
        ]
        n_scoreable = len(relevant)
        n_neutral   = sum(1 for rec in relevant if rec.lean == "neutral")
        neutral_rate = (n_neutral / n_scoreable) if n_scoreable > 0 else math.nan

        return CoverageDiagnostic(
            analyst=analyst,
            horizon=horizon,
            n_neutral=n_neutral,
            n_scoreable=n_scoreable,
            neutral_rate=neutral_rate,
        )

    def confidence_gradient(
        self,
        *,
        analyst: str,
        horizon: int,
        n_buckets: int = 3,
    ) -> list[ConfidenceBucket]:
        """Bucket ``analyst``'s directional calls by confidence and report
        mean excess / n / hit rate / t-stat per bucket.

        Bucketing scheme (Phase 14, iter-11): confidence is a CONTINUOUS
        float in ``[0, 1]`` whose real-world range varies sharply per
        analyst, so buckets are TERCILES (or ``n_buckets`` quantile cuts)
        computed from THIS analyst's own directional-confidence distribution
        at this horizon — never a hardcoded global threshold.  Neutral
        verdicts are excluded entirely before bucketing (this view operates
        strictly on the ``"directional"`` subset).

        Parameters
        ----------
        analyst:
            Analyst name.
        horizon:
            Forward horizon in calendar days (typically the analyst's
            primary horizon — see ``primary_horizon``).
        n_buckets:
            Number of quantile buckets to cut into.  Defaults to 3
            (terciles).  Comes from ``scoreboard_confidence_buckets`` config
            at the call site — never hardcoded by a caller.

        Returns
        -------
        list[ConfidenceBucket]
            ``n_buckets`` buckets ordered from lowest to highest confidence.
            A bucket may have ``n == 0`` if too few directional observations
            exist to fill every quantile slice (e.g. fewer observations than
            buckets) — reported as such rather than silently dropped.

        Raises
        ------
        KeyError
            If ``analyst`` is not present in this result.
        """
        if analyst not in self.analysts:
            raise KeyError(
                f"Analyst {analyst!r} not found in scoreboard result.  "
                f"Available: {self.analysts}"
            )

        directional = [
            rec for rec in self._records
            if rec.analyst == analyst
            and rec.horizon == horizon
            and rec.lean in ("bullish", "bearish")
        ]

        if not directional:
            # No directional data at all — return n_buckets empty buckets so
            # the caller gets a consistently-shaped (if empty) gradient.
            return [
                ConfidenceBucket(
                    bucket_index=i,
                    confidence_range=(math.nan, math.nan),
                    n=0,
                    mean_excess_bps=math.nan,
                    hit_rate=math.nan,
                    t_stat=math.nan,
                    p_value=math.nan,
                )
                for i in range(n_buckets)
            ]

        # Sort by confidence, then cut into n_buckets CONTIGUOUS, near-equal-
        # size chunks (np.array_split) — i.e. quantile buckets derived
        # directly from this analyst's own directional-confidence
        # distribution, never a hardcoded numeric threshold.  Using
        # array_split on the sorted, ranked sequence (rather than cutting on
        # the raw confidence VALUES via np.quantile edges) avoids uneven
        # bucket sizes when the distribution has ties or is lumpy — every
        # bucket gets floor(n/n_buckets) or ceil(n/n_buckets) members.
        directional_sorted = sorted(directional, key=lambda rec: rec.confidence)
        chunks = np.array_split(np.arange(len(directional_sorted)), n_buckets)

        buckets: list[ConfidenceBucket] = []

        for i, chunk_indices in enumerate(chunks):
            members = [directional_sorted[idx] for idx in chunk_indices]
            bucket_n = len(members)

            if bucket_n == 0:
                # Only possible when there are fewer directional observations
                # than requested buckets — no confidence data fell in this slot.
                buckets.append(ConfidenceBucket(
                    bucket_index=i,
                    confidence_range=(math.nan, math.nan),
                    n=0,
                    mean_excess_bps=math.nan,
                    hit_rate=math.nan,
                    t_stat=math.nan,
                    p_value=math.nan,
                ))
                continue

            scores = [rec.score for rec in members]
            cluster_labels = [
                self._cluster_key_fn(rec) if self._cluster_key_fn is not None else rec.ticker
                for rec in members
            ]

            cell = _aggregate(
                analyst=analyst,
                horizon=horizon,
                subset="confidence_bucket",
                scores=scores,
                directional_scores=scores,
                cluster_labels=cluster_labels,
                inference=self._inference,
            )

            confs = [rec.confidence for rec in members]
            buckets.append(ConfidenceBucket(
                bucket_index=i,
                confidence_range=(min(confs), max(confs)),
                n=cell.n,
                mean_excess_bps=cell.mean_excess_bps,
                hit_rate=cell.hit_rate,
                t_stat=cell.t_stat,
                p_value=cell.p_value,
            ))

        return buckets


# ── Internal types ────────────────────────────────────────────────────────────

class _VerdictKey(NamedTuple):
    """Key for grouping verdicts by analyst + ticker + tick."""
    analyst:  str
    ticker:   str
    tick_id:  str


@dataclass(frozen=True)
class _ObservationRecord:
    """One scored verdict, ready for aggregation.

    This is the unit of output from ``_score_window_observations`` — the
    per-window load → dedup → excess-return computation.  A pooling caller
    concatenates these across windows before calling ``_aggregate`` ONCE over
    the union, so the cluster label (``window_id`` baked into ``ticker``-pair
    grouping at the aggregation step) is the only thing that differs between
    the single-window and pooled code paths.

    Attributes
    ----------
    analyst:
        Analyst name (e.g. ``"technical"``).
    ticker:
        Ticker symbol this observation scores.
    window_id:
        Identifier for the window this observation was scored against (e.g.
        a window key such as ``"baseline-2025-09"``).  Used ONLY as a cluster-
        label component when pooling; never affects the score itself, which is
        already fully determined by the within-window peer mean.
    horizon:
        Forward horizon in calendar days.
    lean:
        Verdict lean (``"bullish"`` / ``"bearish"`` / ``"neutral"``) — drives
        subset membership at aggregation time.
    score:
        ``position × excess`` — the signed contribution to the mean-excess
        metric.  Exactly ``0.0`` for neutral verdicts.
    confidence:
        Recorded confidence (``[0, 1]``) for this verdict.  Carried through
        purely for the confidence-gradient view (Phase 14, iter-11); never
        affects the score itself.
    """
    analyst:    str
    ticker:     str
    window_id:  str
    horizon:    int
    lean:       str
    score:      float
    confidence: float


@dataclass(frozen=True)
class _WindowScoringResult:
    """Output of ``_score_window_observations`` for one window.

    Carries the analyst roster SEPARATELY from ``records`` because an analyst
    can have rows in ``analyst_evidence`` that are entirely excluded from
    scoring (e.g. every verdict missing a base price — see
    ``test_verdict_excluded_when_no_base_bar``).  Such an analyst must still
    get n=0 cells rather than vanishing from the report, so the aggregation
    step needs the roster even when ``records`` contributed nothing for them.

    Attributes
    ----------
    records:
        Flat list of scored observations (see ``_ObservationRecord``).  May be
        empty even when ``analysts_seen`` is non-empty.
    analysts_seen:
        Every analyst name present in ``analyst_evidence`` for this window,
        AFTER dedup, regardless of whether any of their verdicts survived
        the base-price / forward-close exclusion.
    """
    records:        list[_ObservationRecord]
    analysts_seen:  set[str]


# ── Public API ────────────────────────────────────────────────────────────────

def build_analyst_scoreboard(
    *,
    db_path: Path,
    cache: CachedDataStore,
    horizons: list[int],
    primary_horizon_by_analyst: dict[str, int] | None = None,
    # Default is "universe": the ``company_ratios`` cache does not currently
    # populate ``sector`` (NULL for every row in every window), so "sector"
    # mode degrades to universe for every ticker anyway while emitting a
    # per-ticker warning.  Re-default to "sector" once sector data is filled.
    neutralise_by: Literal["sector", "universe"] = "universe",
    # Inference mode for the t-stat / p-value.  "cluster_ticker" uses a
    # cluster-robust SE clustered by ticker (corrects for the strong
    # within-ticker autocorrelation induced by verdict persistence + overlapping
    # forward windows); "naive" restores the original independence-assuming
    # ``ttest_1samp``.  See ``_aggregate`` / ``_cluster_robust_ttest``.
    inference: Literal["cluster_ticker", "naive"] = "cluster_ticker",
) -> ScoreboardResult:
    """Build the analyst predictive-power scoreboard from a completed run.

    Pure function: reads ``analyst_evidence`` from the run's ``db.sqlite``
    and prices from ``cache``; produces no side-effects.  Suitable for unit
    testing on a small fixture DB.

    Thin wrapper around ``_score_window_observations`` (load → dedup →
    excess-return computation for ONE window) followed by ``_aggregate_records``
    (group into cells, cluster-robust inference).  The single-window cluster
    label is the bare ticker — unchanged from the pre-pooling behaviour.

    Parameters
    ----------
    db_path:
        Path to the run's ``db.sqlite`` file containing ``analyst_evidence``.
    cache:
        ``CachedDataStore`` backed by the per-window golden-cache SQLite.
    horizons:
        List of forward horizons in calendar days (e.g. ``[1, 5, 20]``).
    primary_horizon_by_analyst:
        Optional mapping from analyst name to its primary scoring horizon.
        Analysts not in the map fall back to ``max(horizons)``.
        If ``None``, all analysts default to ``max(horizons)``.
    neutralise_by:
        Cross-sectional neutralisation mode.
        ``"sector"``   — subtract per-tick sector-peer mean (preferred).
        ``"universe"`` — subtract per-tick whole-universe mean (fallback).
    inference:
        Inference mode for the per-cell ``t_stat`` / ``p_value``.
        ``"cluster_ticker"`` — cluster-robust standard error clustered by
                               ticker (corrects for autocorrelated, non-independent
                               observations; degrees of freedom = #tickers − 1).
        ``"naive"``          — original ``scipy.stats.ttest_1samp`` assuming
                               independent observations (over-confident; opt-in).
        Only the SE / t-stat / p-value depend on this — ``n`` and
        ``mean_excess_bps`` are identical across modes.

    Returns
    -------
    ScoreboardResult
        Fully populated result with one ``ScoreboardCell`` per
        ``(analyst, horizon, subset)`` combination.
    """
    # A single-window call has no real window identity to thread through — the
    # sentinel value below is only ever consumed by the cluster-key function,
    # and the single-window cluster key (bare ticker) ignores it entirely.
    window_result = _score_window_observations(
        db_path=db_path,
        cache=cache,
        horizons=horizons,
        neutralise_by=neutralise_by,
        window_id="_single_window",
    )

    if not window_result.analysts_seen:
        return ScoreboardResult()

    return _aggregate_records(
        window_result.records,
        analysts_seen=window_result.analysts_seen,
        horizons=horizons,
        primary_horizon_by_analyst=primary_horizon_by_analyst,
        inference=inference,
        # Single-window cluster key = bare ticker (pre-pooling behaviour).
        cluster_key_fn=lambda rec: rec.ticker,
    )


def stance_vs_aggregate_agreement(db_path: Path) -> float:
    """Fraction of the strategist's directional stances that matched the aggregate's lean.

    Joins ``ticker_stances`` to ``ticker_evidence`` on ``(tick_id, ticker)`` and
    measures agreement between the strategist's per-ticker decision and the
    deterministic digest aggregate that was available to it at that same tick.
    A rate near 1.0 means the LLM strategist layer is largely redundant above
    the deterministic combine; a lower rate means the strategist is exercising
    independent judgement (overriding the aggregate) at least some of the time.

    Direction mapping (``TickerStanceRow.lifecycle_action`` → direction):
      - ``"buy"``    → ``"bullish"``
      - ``"sell"``   → ``"bearish"``
      - ``"update"`` → excluded — a position adjustment carries no unambiguous
        directional sign (it may be a trim, an add, or a rebalance), so it
        cannot meaningfully agree or disagree with a bullish/bearish aggregate
        lean.  ``"update"`` rows are dropped from BOTH the numerator and the
        denominator.

    Parameters
    ----------
    db_path:
        Path to the run's ``db.sqlite`` (contains ``ticker_stances`` and
        ``ticker_evidence``).

    Returns
    -------
    float
        Fraction (0.0–1.0) of directional joined (stance, aggregate) pairs
        whose mapped direction equals the aggregate's ``lean``.

    Raises
    ------
    ValueError
        If there are zero directional joinable (stance, aggregate) pairs.
        Silently returning ``0.0`` here would be indistinguishable from a
        genuine "the strategist never agreed with the aggregate" result —
        exactly the silent-degradation trap this project forbids — so the
        no-data case is surfaced loudly instead.
    """
    # Only these two lifecycle verbs carry an unambiguous directional sign;
    # "update" is intentionally absent from this mapping (see docstring).
    _DIRECTION_BY_ACTION = {"buy": "bullish", "sell": "bearish"}

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with Session(engine) as s:
        stance_rows: list[TickerStanceRow] = (
            s.execute(select(TickerStanceRow)).scalars().all()
        )
        aggregate_rows: list[TickerEvidenceRow] = (
            s.execute(select(TickerEvidenceRow)).scalars().all()
        )

    # Index the aggregate rows by (tick_id, ticker) for the join.
    aggregate_by_key: dict[tuple[str, str], TickerEvidenceRow] = {
        (row.tick_id, row.ticker): row for row in aggregate_rows
    }

    agreements = 0
    directional_pairs = 0

    for stance in stance_rows:
        direction = _DIRECTION_BY_ACTION.get(stance.lifecycle_action)
        if direction is None:
            # Non-directional ("update") — excluded from numerator and denominator.
            continue

        aggregate = aggregate_by_key.get((stance.tick_id, stance.ticker))
        if aggregate is None:
            # No joinable aggregate for this (tick_id, ticker) — cannot score.
            continue

        directional_pairs += 1
        if direction == aggregate.lean:
            agreements += 1

    if directional_pairs == 0:
        raise ValueError(
            "stance_vs_aggregate_agreement: zero directional joinable "
            f"(stance, aggregate) pairs found in {db_path} — cannot compute "
            "an agreement rate (see docstring: returning 0.0 here would "
            "misleadingly read as '0 % agreement')."
        )

    return agreements / directional_pairs


def build_pooled_analyst_scoreboard(
    *,
    runs: list[tuple[Path, CachedDataStore, str]],
    horizons: list[int],
    primary_horizon_by_analyst: dict[str, int] | None = None,
    neutralise_by: Literal["sector", "universe"] = "universe",
    inference: Literal["cluster_ticker", "naive"] = "cluster_ticker",
) -> ScoreboardResult:
    """Build a scoreboard pooling observations from MULTIPLE windows.

    Each ``(db_path, cache, window_id)`` triple is scored independently by
    ``_score_window_observations`` — its own dedup pass, its own per-tick
    peer means computed solely from its own cache — and the resulting
    per-observation records are concatenated before a SINGLE aggregation
    pass.  This guarantees:

      - peer means never blend across windows (each window's cs-means are
        already final by the time pooling sees the records);
      - dedup never collapses a verdict in window A with a verdict in
        window B (dedup is internal to ``_score_window_observations``);
      - the cluster-robust SE clusters by ``(ticker, window_id)``, NOT bare
        ticker, since windows months apart are not autocorrelated with each
        other and bare-ticker clustering would over-cluster the pooled set.

    Calling this with exactly one run reduces to ``build_analyst_scoreboard``
    on that run (the (ticker, window_id) cluster key partitions identically
    to the bare-ticker key when there is only one window).

    Parameters
    ----------
    runs:
        List of ``(db_path, cache, window_id)`` triples.  ``window_id`` is an
        arbitrary, caller-chosen label used only for cluster-key disambiguation
        (e.g. the window key from ``config/backtest_windows.json``).  Must be
        unique per element — reusing a ``window_id`` for two different runs
        would silently merge their clusters.
    horizons:
        List of forward horizons in calendar days (e.g. ``[1, 5, 20]``).
    primary_horizon_by_analyst:
        See ``build_analyst_scoreboard``.
    neutralise_by:
        See ``build_analyst_scoreboard``.  Applied identically and
        independently within every window.
    inference:
        See ``build_analyst_scoreboard``.

    Returns
    -------
    ScoreboardResult
        Fully populated POOLED result with one ``ScoreboardCell`` per
        ``(analyst, horizon, subset)`` combination, aggregated across every
        supplied window.

    Raises
    ------
    ValueError
        If ``runs`` is empty (pooling zero windows is a caller error, not a
        valid "empty" result) or if any ``window_id`` is duplicated.
    """
    if not runs:
        raise ValueError(
            "build_pooled_analyst_scoreboard: 'runs' must contain at least "
            "one (db_path, cache, window_id) triple."
        )

    window_ids = [window_id for _db, _cache, window_id in runs]
    if len(window_ids) != len(set(window_ids)):
        raise ValueError(
            f"build_pooled_analyst_scoreboard: duplicate window_id in "
            f"'runs' — every window must have a unique id. Got: {window_ids}"
        )

    # Score every window in isolation and concatenate the resulting records.
    # Each call to _score_window_observations sees ONLY its own db_path/cache,
    # so dedup and peer means cannot leak across the window boundary.
    pooled_records:      list[_ObservationRecord] = []
    pooled_analysts_seen: set[str]                 = set()

    for db_path, cache, window_id in runs:
        window_result = _score_window_observations(
            db_path=db_path,
            cache=cache,
            horizons=horizons,
            neutralise_by=neutralise_by,
            window_id=window_id,
        )
        pooled_records.extend(window_result.records)
        pooled_analysts_seen |= window_result.analysts_seen

    if not pooled_analysts_seen:
        return ScoreboardResult()

    return _aggregate_records(
        pooled_records,
        analysts_seen=pooled_analysts_seen,
        horizons=horizons,
        primary_horizon_by_analyst=primary_horizon_by_analyst,
        inference=inference,
        # Pooled cluster key = (ticker, window_id) — see module docstring
        # decision 3.  Two windows months apart are not autocorrelated, so
        # clustering by bare ticker would over-cluster the pooled set.
        cluster_key_fn=lambda rec: (rec.ticker, rec.window_id),
    )


def _score_window_observations(
    *,
    db_path: Path,
    cache: CachedDataStore,
    horizons: list[int],
    neutralise_by: Literal["sector", "universe"],
    window_id: str,
) -> _WindowScoringResult:
    """Score ONE window's ``analyst_evidence`` into a flat observation list.

    This is steps 1–6 of the original ``build_analyst_scoreboard`` algorithm,
    extracted so a pooling caller can run it independently per window before
    a single combined aggregation pass.  Running dedup and peer-mean
    computation HERE (scoped to one ``db_path``/``cache`` pair) is what
    guarantees pooling cannot contaminate either step across windows.

    Algorithm
    ---------
    1. Load all ``AnalystEvidenceRow`` records, ordered by (analyst, ticker,
       recorded_at) so consecutive ticks are adjacent.
    2. **Dedup** (cache-replay fix): rows carrying the report cache's
       ``input_hash`` collapse EXACTLY on ``(analyst, ticker, input_hash)`` —
       every row sharing that triple is the same cached verdict replayed, and
       only the FIRST occurrence (tick order) is kept as the anchor.  Rows
       with no ``input_hash`` (deterministic analysts, which never populate
       it) are never deduped.  A mix of hashed and unhashed rows for one
       analyst within this window is a plumbing bug and raises.  Scoped to
       THIS window's rows only.
    3. For each deduplicated verdict, look up ``base_price`` (phase-matched)
       and forward closes per horizon from ``cache``.
    4. Compute per-tick cross-sectional means using the **peer group** for each
       ticker, where the peer group is keyed on ``tick_id`` — which is unique
       to this window — so the mean can never include another window's tickers.
    5. Compute each verdict's signed score (``position × excess``).
    6. Emit one ``_ObservationRecord`` per scored (verdict, horizon) pair.

    Parameters
    ----------
    db_path:
        Path to this window's run's ``db.sqlite``.
    cache:
        ``CachedDataStore`` backed by THIS window's golden-cache SQLite.
    horizons:
        Forward horizons in calendar days.
    neutralise_by:
        Cross-sectional neutralisation mode (``"sector"`` | ``"universe"``).
    window_id:
        Caller-chosen label stamped onto every emitted record's
        ``window_id`` field.  Used only as a cluster-key component by the
        pooled aggregation path — never affects the score itself.

    Returns
    -------
    _WindowScoringResult
        ``records`` — one entry per (deduplicated verdict, horizon) pair that
        had both a base price and a forward close.
        ``analysts_seen`` — every analyst present after dedup, even those
        whose verdicts were entirely excluded (so they still get n=0 cells
        rather than vanishing from the report).  Both empty if no rows exist
        in ``db_path``.
    """
    # ── 1. Load verdict rows, sorted for dedup ───────────────────────────────
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with Session(engine) as s:
        rows: list[AnalystEvidenceRow] = (
            s.execute(select(AnalystEvidenceRow)).scalars().all()
        )

        # ── Pseudo-analyst injection: score the digest aggregate ────────────
        # ``ticker_evidence`` holds one row per ticker per tick — the
        # cross-analyst aggregate lean/magnitude/confidence produced by the
        # digest (Phase 14 Plan 3c, Task 1).  To answer "is the aggregator
        # adding value?" it must be scored on forward returns EXACTLY like a
        # real analyst — so we map each ``TickerEvidenceRow`` into an
        # in-memory (never persisted) synthetic ``AnalystEvidenceRow`` with
        # ``analyst="aggregate"``.  This is safe rather than a hack because
        # ``TickerEvidenceRow`` and ``AnalystEvidenceRow`` share the exact
        # columns the scorer below reads (``analyst``/``ticker``/
        # ``recorded_at``/``lean``/``magnitude``/``confidence``) — the two
        # tables were deliberately given a compatible shape for this reason.
        # The synthetic rows are extended into ``rows`` BEFORE the emptiness
        # check and the dedup pass, so they flow through the identical
        # dedup → base-price/forward-close → scoring path as any other
        # analyst, and the aggregate's primary horizon needs no special
        # handling — ``ScoreboardResult.primary_horizon`` already defaults
        # unconfigured analysts to ``max(horizons)``.
        ticker_evidence_rows: list[TickerEvidenceRow] = (
            s.execute(select(TickerEvidenceRow)).scalars().all()
        )

    aggregate_rows: list[AnalystEvidenceRow] = [
        AnalystEvidenceRow(
            tick_id=tev.tick_id,
            recorded_at=tev.recorded_at,
            analyst="aggregate",
            ticker=tev.ticker,
            lean=tev.lean,
            magnitude=tev.magnitude,
            confidence=tev.confidence,
        )
        for tev in ticker_evidence_rows
    ]
    rows = rows + aggregate_rows

    if not rows:
        logger.warning(
            "scoreboard: no analyst_evidence or ticker_evidence rows found in %s", db_path,
        )
        return _WindowScoringResult(records=[], analysts_seen=set())

    # Sort by (analyst, ticker, recorded_at) so consecutive ticks are adjacent
    # and the dedup pass can work in a single linear scan per (analyst, ticker) group.
    rows_sorted = sorted(
        rows,
        key=lambda r: (r.analyst, r.ticker, _ensure_aware(r.recorded_at)),
    )

    # ── 2. Dedup: collapse exact cache-replay observations ───────────────────
    # Rows carrying an ``input_hash`` (cached LLM analysts — fundamental,
    # news) dedup EXACTLY on ``(analyst, ticker, input_hash)``: every row
    # sharing that triple within this window is the same underlying cached
    # verdict replayed across ticks, and only the FIRST occurrence (tick
    # order — guaranteed by ``rows_sorted`` above) is kept as the scoring
    # anchor.  The forward return is measured from that anchor tick (see
    # step 3 below, which resolves prices from each surviving row's own
    # ``recorded_at``).
    #
    # Rows without an ``input_hash`` (deterministic analysts — technical,
    # smart_money, social — plus the synthetic "aggregate" pseudo-analyst,
    # which has no cache concept at all) are NEVER deduped: each is scored as
    # its own fresh observation, because there is no cache-replay mechanism to
    # collapse for them in the first place.
    #
    # Consistency check: within THIS window, an analyst must have EITHER all
    # rows hashed OR all rows unhashed.  A mix means input_hash plumbing is
    # broken for that analyst (e.g. it fires for some tickers/ticks but not
    # others) — raise loudly rather than silently under- or over-deduping.
    # The check is derived from the data itself, not a hardcoded list of
    # "cached analysts", so it fires however the inconsistency arises.
    #
    # Scoped to THIS window's rows: dedup never sees another window's data,
    # which is the property that makes per-window scoring safe to pool later.

    hashed_count:   dict[str, int] = defaultdict(int)
    unhashed_count: dict[str, int] = defaultdict(int)

    for row in rows_sorted:
        if row.input_hash is not None:
            hashed_count[row.analyst] += 1
        else:
            unhashed_count[row.analyst] += 1

    for analyst in sorted(set(hashed_count) & set(unhashed_count)):
        raise ValueError(
            f"scoreboard: window={window_id!r} analyst={analyst!r} has a mix "
            f"of hashed and unhashed analyst_evidence rows "
            f"({hashed_count[analyst]} with input_hash, "
            f"{unhashed_count[analyst]} without) — this indicates broken "
            f"input_hash plumbing for this analyst, not a legitimate mixed "
            f"state.  Fix the producer that writes this analyst's evidence."
        )

    seen_triples: set[tuple[str, str, str]] = set()
    deduplicated_rows: list[AnalystEvidenceRow] = []

    for row in rows_sorted:
        if row.input_hash is None:
            # No cache concept for this analyst (or the aggregate
            # pseudo-analyst) — every row is its own fresh observation.
            deduplicated_rows.append(row)
            continue

        triple = (row.analyst, row.ticker, row.input_hash)
        if triple in seen_triples:
            # Exact cache replay: same analyst, same ticker, same cached
            # input — discard; the anchor observation was already kept.
            logger.debug(
                "scoreboard: dedup — discarding replayed verdict "
                "(analyst=%s, ticker=%s, input_hash=%s, window=%s)",
                row.analyst, row.ticker, row.input_hash, window_id,
            )
            continue

        # First occurrence of this triple → this is the scoring anchor.
        seen_triples.add(triple)
        deduplicated_rows.append(row)

    logger.info(
        "scoreboard: window=%s dedup reduced %d rows → %d unique fresh-call "
        "observations (%.1f %% removed as cache replays)",
        window_id,
        len(rows_sorted),
        len(deduplicated_rows),
        100.0 * (1 - len(deduplicated_rows) / len(rows_sorted)) if rows_sorted else 0.0,
    )

    # ── 3. Resolve base prices and forward closes per deduplicated verdict ───
    # Structure: row_index → base_price  and  row_index → {h: fwd_close|None}
    # Rows missing a base price are excluded entirely (no sentinel values).

    row_base:      dict[int, float]                    = {}
    row_fwd_close: dict[int, dict[int, float | None]]  = {}

    for idx, row in enumerate(deduplicated_rows):
        recorded_at = _ensure_aware(row.recorded_at)
        as_of_date  = recorded_at.date()

        # Phase-matched base price: open for open-phase (hour < 17 UTC),
        # close for close-phase.  Matches _spy_benchmark_series rule in reporting.py.
        base_bars = cache.read_ohlcv(row.ticker, as_of_date, as_of_date)
        if not base_bars:
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

    # ── 4. Compute sector assignments and fwd returns for the cs-mean ────────
    # ticker_sector[(tick_id, ticker)] = sector string or None.
    # fwd return is a market fact; deduplicated so each (tick_id, ticker) pair
    # contributes at most one return to the cross-sectional mean.
    #
    # tick_id is unique to THIS window (window run-ids are baked into tick
    # generation upstream), so keying on it is what makes the peer group
    # provably within-window even after records from many windows are later
    # concatenated for pooled aggregation.

    # ticker_fwd_by_tick[(tick_id, ticker, h)] = fwd_return (a single market fact).
    ticker_fwd_by_tick: dict[tuple[str, str, int], float] = {}

    # ticker_sector_by_tick[(tick_id, ticker)] = sector | None
    ticker_sector_by_tick: dict[tuple[str, str], str | None] = {}

    for idx, row in enumerate(deduplicated_rows):
        if idx not in row_base:
            continue

        recorded_at = _ensure_aware(row.recorded_at)
        base_price  = row_base[idx]

        tick_ticker_key = (row.tick_id, row.ticker)

        # Look up sector for this ticker at this tick's date (PIT-correct).
        if tick_ticker_key not in ticker_sector_by_tick:
            sector: str | None = None

            if neutralise_by == "sector":
                try:
                    ratios = cache.read_company_ratios(row.ticker, recorded_at)
                    sector = ratios.sector if ratios is not None else None
                except Exception:
                    logger.warning(
                        "scoreboard: read_company_ratios failed for %s — "
                        "falling back to universe neutralisation",
                        row.ticker,
                    )
                    sector = None

                if sector is None:
                    logger.warning(
                        "scoreboard: no sector data for %s at %s — "
                        "falling back to universe mean for this ticker",
                        row.ticker, recorded_at.date(),
                    )

            ticker_sector_by_tick[tick_ticker_key] = sector

        for h, fwd_close_val in row_fwd_close[idx].items():
            if fwd_close_val is None:
                continue

            key = (row.tick_id, row.ticker, h)
            if key not in ticker_fwd_by_tick:
                fwd_return = (fwd_close_val - base_price) / base_price
                ticker_fwd_by_tick[key] = fwd_return

    # ── 5. Build cross-sectional means ───────────────────────────────────────
    # For "sector" mode: cs_mean[(tick_id, h, sector)] = mean fwd_return for that sector
    # For "universe" mode: cs_mean[(tick_id, h, None)] = whole-universe mean
    # Both cases are handled by keying on (tick_id, h, group_key) where
    # group_key = sector string or None (universe).  Because tick_id is unique
    # to this window, every group computed here is, by construction, a
    # within-window peer group.

    # Accumulate fwd returns per group.
    group_fwds: dict[tuple[str, int, str | None], list[float]] = defaultdict(list)

    for (tick_id, ticker, h), fwd_return in ticker_fwd_by_tick.items():
        tick_ticker_key = (tick_id, ticker)
        sector          = ticker_sector_by_tick.get(tick_ticker_key)

        if neutralise_by == "sector" and sector is not None:
            # Sector group — ticker belongs to its own sector bucket.
            group_fwds[(tick_id, h, sector)].append(fwd_return)
        else:
            # Universe group — always accumulate into the None bucket.
            group_fwds[(tick_id, h, None)].append(fwd_return)

    # When using sector mode, also accumulate a full-universe bucket as the
    # fallback for tickers whose sector is unknown.
    if neutralise_by == "sector":
        for (tick_id, _ticker, h), fwd_return in ticker_fwd_by_tick.items():
            group_fwds[(tick_id, h, None)].append(fwd_return)

    # cs_mean_by_group[(tick_id, h, group_key)] = mean fwd_return
    cs_mean_by_group: dict[tuple[str, int, str | None], float] = {
        key: float(np.mean(fwds))
        for key, fwds in group_fwds.items()
    }

    # ── 6. Compute per-verdict excess and emit observation records ───────────
    records: list[_ObservationRecord] = []

    for idx, row in enumerate(deduplicated_rows):
        if idx not in row_base:
            continue

        tick_ticker_key = (row.tick_id, row.ticker)
        sector          = ticker_sector_by_tick.get(tick_ticker_key)

        for h in horizons:
            fwd_close_val = row_fwd_close[idx].get(h)
            if fwd_close_val is None:
                continue  # window edge — excluded from this horizon

            ticker_key = (row.tick_id, row.ticker, h)
            if ticker_key not in ticker_fwd_by_tick:
                continue  # should not happen given the earlier loop

            ticker_fwd = ticker_fwd_by_tick[ticker_key]

            # Determine which cs-mean group to use for this ticker.
            # Sector mode with known sector → use sector bucket.
            # Otherwise → use universe bucket (None key).
            group_key = sector if neutralise_by == "sector" and sector is not None else None

            cs_mean = cs_mean_by_group.get((row.tick_id, h, group_key), 0.0)
            excess  = ticker_fwd - cs_mean
            position = _lean_to_position(row.lean)
            score    = float(position * excess)

            records.append(_ObservationRecord(
                analyst=row.analyst,
                ticker=row.ticker,
                window_id=window_id,
                horizon=h,
                lean=row.lean,
                score=score,
                confidence=float(row.confidence),
            ))

    # analysts_seen tracks the FULL post-dedup roster, independent of whether
    # any of an analyst's verdicts survived the base-price/forward-close
    # exclusion — an analyst with zero scored records must still get n=0
    # cells rather than vanishing from the report.
    analysts_seen = {row.analyst for row in deduplicated_rows}

    return _WindowScoringResult(records=records, analysts_seen=analysts_seen)


def _aggregate_records(
    records: list[_ObservationRecord],
    *,
    analysts_seen: set[str],
    horizons: list[int],
    primary_horizon_by_analyst: dict[str, int] | None,
    inference: Literal["cluster_ticker", "naive"],
    cluster_key_fn,
) -> ScoreboardResult:
    """Group a flat observation list into a populated ``ScoreboardResult``.

    Shared aggregation step (formerly step 7 of ``build_analyst_scoreboard``)
    used by BOTH the single-window and pooled entrypoints.  The only thing
    that differs between them is ``cluster_key_fn`` — the function used to
    derive each record's cluster-robust-inference label.

    Parameters
    ----------
    records:
        Flat list of ``_ObservationRecord``, possibly drawn from multiple
        windows (already deduplicated and peer-mean-corrected upstream).
    analysts_seen:
        The FULL analyst roster (union across all source windows), supplied
        explicitly rather than re-derived from ``records`` — an analyst whose
        every verdict was excluded (e.g. no base price anywhere) still has
        zero records but must still get n=0 cells rather than vanishing.
    horizons:
        Forward horizons to report (drives ``ScoreboardResult.horizons``).
    primary_horizon_by_analyst:
        Optional per-analyst primary horizon mapping; unconfigured analysts
        default to ``max(horizons)``.
    inference:
        ``"cluster_ticker"`` or ``"naive"`` — see ``_aggregate``.
    cluster_key_fn:
        Callable ``(_ObservationRecord) -> Hashable`` returning the cluster
        label for that record.  Single-window callers pass ``rec.ticker``;
        the pooled caller passes ``(rec.ticker, rec.window_id)``.

    Returns
    -------
    ScoreboardResult
        Fully populated result with one ``ScoreboardCell`` per
        ``(analyst, horizon, subset)`` combination.
    """
    primary_map: dict[str, int] = primary_horizon_by_analyst or {}
    default_horizon = max(horizons) if horizons else 1

    # Scores: (analyst, h) → subset → list[score]
    #
    # Phase 14 (iter-11): "directional" = bullish ∪ bearish ONLY.  Neutral
    # verdicts are EXCLUDED entirely from this subset — they no longer
    # contribute a score of 0 (which previously diluted the mean and
    # inflated n).  Neutral verdicts are tracked separately via
    # ScoreboardResult.coverage(), never folded into "directional".
    score_store: dict[
        tuple[str, int],
        dict[str, list[float]],
    ] = defaultdict(lambda: {"directional": [], "bullish": [], "bearish": []})

    # Cluster labels aligned element-for-element with ``score_store``.  Kept
    # as a parallel store (rather than zipping into tuples) so the existing
    # score-only code paths are untouched.
    cluster_store: dict[
        tuple[str, int],
        dict[str, list],
    ] = defaultdict(lambda: {"directional": [], "bullish": [], "bearish": []})

    for rec in records:
        key          = (rec.analyst, rec.horizon)
        cluster_label = cluster_key_fn(rec)

        # Neutral verdicts are excluded from "directional" (and never
        # contribute to "bullish" / "bearish" either) — they carry no
        # directional score to measure selection skill against.
        if rec.lean == "bullish":
            score_store[key]["directional"].append(rec.score)
            cluster_store[key]["directional"].append(cluster_label)
            score_store[key]["bullish"].append(rec.score)
            cluster_store[key]["bullish"].append(cluster_label)
        elif rec.lean == "bearish":
            score_store[key]["directional"].append(rec.score)
            cluster_store[key]["directional"].append(cluster_label)
            score_store[key]["bearish"].append(rec.score)
            cluster_store[key]["bearish"].append(cluster_label)
        # lean == "neutral": deliberately not appended anywhere here.

    analysts_sorted: list[str] = sorted(analysts_seen)
    result = ScoreboardResult(
        analysts=analysts_sorted,
        horizons=sorted(horizons),
        _records=records,
        _inference=inference,
        _cluster_key_fn=cluster_key_fn,
    )

    # Populate the per-analyst primary horizon mapping.  Analysts not in the
    # supplied primary_map default to max(horizons).
    result._primary_horizons = {
        analyst: primary_map.get(analyst, default_horizon)
        for analyst in analysts_seen
    }

    for analyst in analysts_seen:
        for h in sorted(horizons):
            store = score_store.get(
                (analyst, h),
                {"directional": [], "bullish": [], "bearish": []},
            )
            clusters = cluster_store.get(
                (analyst, h),
                {"directional": [], "bullish": [], "bearish": []},
            )
            for subset in ("directional", "bullish", "bearish"):
                scores         = store.get(subset, [])
                cluster_labels = clusters.get(subset, [])

                # Hit-rate is defined over NON-NEUTRAL verdicts only (spec).
                # Since neutral verdicts are now excluded from "directional"
                # entirely (Phase 14, iter-11), every subset here is already
                # fully directional — the hit-rate denominator and the n/mean
                # denominator are now the SAME set for every subset.
                directional = scores

                cell = _aggregate(
                    analyst=analyst,
                    horizon=h,
                    subset=subset,
                    scores=scores,
                    directional_scores=directional,
                    cluster_labels=cluster_labels,
                    inference=inference,
                )
                result.cells[(analyst, h, subset)] = cell

    return result


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_scoreboard_md(result: ScoreboardResult) -> str:
    """Render a ``ScoreboardResult`` as a Markdown section.

    Produces a section suitable for appending to ``report/metrics.md``.

    Layout (Phase 14 — horizon-across-columns grid).  One compact grid per
    analyst.  Rows are the directional lean subsets — ``directional``
    (bullish ∪ bearish) first as the headline, then its ``bullish`` and
    ``bearish`` decomposition.  Columns are the forward horizons, so a
    lean's signal reads left-to-right as the horizon lengthens — the drift
    shape the analysts are aimed at.  Each grid cell packs three figures,
    ``mean excess (bps) / hit rate / t-stat``; a leading ``n`` column gives
    the observation count at fullest coverage, and each analyst's primary
    scoring horizon column is marked with ``★``.

    Neutral verdicts are abstentions with no directional score, so they
    cannot occupy a grid row.  They are reported instead as a per-horizon
    abstention rate on a line beneath each analyst's grid, honouring the
    ``directional`` cell's coverage identity
    (``n_neutral + directional.n == n_scoreable``).

    A cell reads ``—`` when the combination has no scoreable observation
    (no directional verdict, or a window-edge horizon with no forward bar);
    the ``t-stat`` slot within a cell reads ``—`` when ``n < 2`` (a
    t-statistic is undefined on a single observation).

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
            "(ticker fwd-return − per-tick peer-group mean).  "
            "Positive = lean predicted the relative outperformer.  "
            "Each cell: **mean excess bps / hit rate / t-stat** across the "
            "horizon columns; **n** is the directional count at fullest "
            "coverage (window-edge verdicts with no forward bar are dropped "
            "at longer horizons, shown as ``—``).  ★ marks each analyst's "
            "primary scoring horizon.\n"
        ),
    ]

    if not result.analysts:
        lines.append("_No analyst evidence rows found._\n")
        return "\n".join(lines)

    def _cell_str(analyst: str, horizon: int, subset: str) -> str:
        """Render one grid cell as ``mean / hit% / t``, or ``—`` when unscored.

        Parameters
        ----------
        analyst, horizon, subset:
            Identify the ``ScoreboardCell`` to render.

        Returns
        -------
        str
            The packed three-figure cell, or a single ``—`` when there is no
            scoreable observation for the combination.
        """
        try:
            c = result.cell(analyst=analyst, horizon=horizon, subset=subset)
        except KeyError:
            return "—"

        # No directional observation → nothing to score in this cell.
        if c.n == 0 or not math.isfinite(c.mean_excess_bps):
            return "—"

        mean_s = f"{c.mean_excess_bps:+.1f}"
        hit_s  = f"{c.hit_rate:.0%}" if math.isfinite(c.hit_rate) else "—"
        t_s    = f"{c.t_stat:+.2f}"  if math.isfinite(c.t_stat)   else "—"
        return f"{mean_s} / {hit_s} / {t_s}"

    def _row_n(analyst: str, subset: str) -> int:
        """Return the observation count for ``subset`` at fullest coverage.

        The count is the maximum ``n`` across the scored horizons, i.e. the
        sample size before window-edge attrition thins the longer horizons.
        This is the number a reader needs to judge how much to trust the
        starred (primary-horizon) cell — e.g. a −444 bps bearish mean means
        something very different on n=78 than on n=500.

        Parameters
        ----------
        analyst, subset:
            Identify the lean row.

        Returns
        -------
        int
            Maximum ``n`` across horizons, or ``0`` when the row is empty.
        """
        counts: list[int] = []
        for h in result.horizons:
            try:
                counts.append(result.cell(analyst=analyst, horizon=h, subset=subset).n)
            except KeyError:
                continue
        return max(counts) if counts else 0

    for analyst in result.analysts:
        ph = result.primary_horizon(analyst)

        # Column headers: one per horizon, with ★ on the primary.
        horizon_headers = [
            f"+{h}d★" if h == ph else f"+{h}d" for h in result.horizons
        ]

        lines.append(f"\n### {analyst}\n")
        lines.append("| lean        | n | " + " | ".join(horizon_headers) + " |")
        lines.append(
            "|-------------|---|" + "|".join(["-------------"] * len(result.horizons)) + "|"
        )

        # Directional headline first, then its bullish / bearish split.
        for subset in ("directional", "bullish", "bearish"):
            cells = [_cell_str(analyst, h, subset) for h in result.horizons]
            lines.append(
                f"| {subset:<11} | {_row_n(analyst, subset)} | " + " | ".join(cells) + " |"
            )

        # Neutral verdicts have no directional score — report their
        # per-horizon abstention rate on a separate line beneath the grid.
        neutral_parts: list[str] = []
        for h in result.horizons:
            cov  = result.coverage(analyst=analyst, horizon=h)
            rate = f"{cov.neutral_rate:.0%}" if math.isfinite(cov.neutral_rate) else "—"
            neutral_parts.append(f"+{h}d {rate}")
        lines.append(
            "\n_neutral (abstention rate): " + " · ".join(neutral_parts) + "_\n"
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
    cluster_labels: list,
    inference: str = "cluster_ticker",
) -> ScoreboardCell:
    """Aggregate a list of scores into a ``ScoreboardCell``.

    Parameters
    ----------
    analyst:
        Analyst name.
    horizon:
        Forward horizon in calendar days.
    subset:
        Lean subset (``"directional"``, ``"bullish"``, ``"bearish"``).
    scores:
        List of ``score_h = position × excess`` values for the whole subset,
        including neutral verdicts (which contribute an exact ``0``).  Drives
        ``n``, the mean excess, and the t-stat.  May be empty.
    directional_scores:
        The non-neutral subset of ``scores`` — i.e. only bullish/bearish
        verdicts.  Drives the hit-rate denominator so a mostly-neutral analyst
        is not scored as "almost always wrong" merely for declining to bet.
        For the single-lean subsets this is identical to ``scores``.
    cluster_labels:
        Cluster label for each element of ``scores`` (aligned element-for-
        element).  Used by the ``"cluster_ticker"`` inference mode to group
        autocorrelated observations into one cluster.  For a single-window
        scoreboard this is the bare ticker (``str``); for a pooled scoreboard
        it is ``(ticker, window_id)`` so windows months apart are never
        merged into one cluster.  Must be the same length as ``scores``.
    inference:
        ``"cluster_ticker"`` (default) → cluster-robust SE clustered by
        ``cluster_labels``.
        ``"naive"`` → original independence-assuming ``ttest_1samp``.

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

    # Defensive: clusters must align with scores.  A mismatch is a programming
    # error in the caller, not a data condition — raise loudly rather than
    # silently dropping or padding (silent-failure is the recurring bug class).
    if len(cluster_labels) != n:
        raise ValueError(
            f"_aggregate: cluster_labels length {len(cluster_labels)} does not "
            f"match scores length {n} for (analyst={analyst!r}, horizon={horizon}, "
            f"subset={subset!r})."
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

    # t-stat / p-value.  Requires at least 2 observations.  ``n`` and the mean
    # are IDENTICAL regardless of inference mode — only the SE changes.
    if n < 2:
        t_stat  = math.nan
        p_value = math.nan
    elif inference == "naive":
        # Original behaviour: assumes independent observations (over-confident).
        t_result = scipy.stats.ttest_1samp(arr, 0.0)
        t_stat   = float(t_result.statistic)
        p_value  = float(t_result.pvalue)
    else:
        # Cluster-robust SE clustered by ticker — the honest inference.
        t_stat, p_value, _df = _cluster_robust_ttest(scores, cluster_labels)

    return ScoreboardCell(
        analyst=analyst, horizon=horizon, subset=subset,
        n=n,
        mean_excess_bps=mean_excess_bps,
        hit_rate=hit_rate,
        t_stat=t_stat,
        p_value=p_value,
    )


def _cluster_robust_ttest(
    scores: list[float],
    cluster_labels: list,
) -> tuple[float, float, int]:
    """One-sample test of H0: mean(scores) = 0 with a cluster-robust SE.

    Why
    ---
    The scored observations are NOT independent.  For a given ticker the
    analyst's verdict persists across ticks and the +5d / +20d forward-return
    windows heavily overlap, so observations on the same ticker are strongly
    serially autocorrelated.  A naive one-sample t-test assumes independence and
    therefore understates the standard error, inflating ``|t|`` and shrinking
    the p-value (the audit estimated ~4.5× inflation).

    Method
    ------
    We treat the sample mean as the OLS estimator from regressing ``scores`` on
    a constant, then apply the standard cluster-robust ("sandwich" / CR1)
    variance estimator clustering by ticker:

        beta_hat = mean(x)
        u_i      = x_i − beta_hat                          (residuals)
        meat     = Σ_g ( Σ_{i∈g} u_i )²                    (sum over clusters g)
        bread    = 1 / N                                   (since X'X = N)
        Var(beta_hat) = c · bread² · meat
                      = c · meat / N²

    with the CR1 finite-sample correction ``c = G / (G − 1)``, where ``G`` is the
    number of clusters.  The test statistic is ``beta_hat / sqrt(Var(beta_hat))``
    referred to a Student-t distribution with ``G − 1`` degrees of freedom.

    Consistency with the naive test on i.i.d. data
    ----------------------------------------------
    When every observation is its own singleton cluster (genuine independence),
    there are no within-cluster cross terms, so ``meat = Σ_i u_i²`` and
    ``G = N``.  Then ``Var = (N/(N−1)) · Σ u_i² / N² = Σ u_i² / (N(N−1))``, which
    is exactly the variance of the sample mean used by ``ttest_1samp`` — so the
    t-stat, p-value and df all coincide.  This guarantees the correction never
    spuriously moves a result that had no autocorrelation to correct.

    Parameters
    ----------
    scores:
        Per-verdict scores.  Must contain at least 2 elements.
    cluster_labels:
        Cluster label for each score (aligned element-for-element).  Any
        hashable value — a bare ticker ``str`` for single-window callers, or
        a ``(ticker, window_id)`` tuple for pooled callers.  Must be the same
        length as ``scores``.

    Returns
    -------
    tuple[float, float, int]
        ``(t_stat, p_value, degrees_of_freedom)``.  ``degrees_of_freedom`` is
        ``G − 1`` (number of clusters minus one).

    Raises
    ------
    ValueError
        If ``scores`` and ``cluster_labels`` differ in length, or if there are
        fewer than 2 scores (the caller is responsible for the n < 2 guard, but
        we re-check to avoid a silent divide-by-zero).
    """
    n = len(scores)

    if len(cluster_labels) != n:
        raise ValueError(
            f"_cluster_robust_ttest: scores ({n}) and cluster_labels "
            f"({len(cluster_labels)}) must be the same length."
        )

    if n < 2:
        raise ValueError(
            f"_cluster_robust_ttest: need at least 2 observations; got {n}."
        )

    arr  = np.array(scores, dtype=float)
    mean = float(np.mean(arr))

    # Residuals from the constant-only regression.
    residuals = arr - mean

    # Sum residuals WITHIN each cluster, then sum the squared cluster totals
    # (the "meat" of the sandwich).  ``defaultdict`` keeps this O(N).  Keys
    # are any hashable cluster label (bare ticker, or (ticker, window_id)).
    cluster_sums: dict = defaultdict(float)
    for label, u in zip(cluster_labels, residuals, strict=True):
        cluster_sums[label] += float(u)

    n_clusters = len(cluster_sums)

    # A single cluster gives 0 degrees of freedom — inference is undefined.
    # Surface this as NaN rather than crashing, mirroring the n < 2 sentinel.
    if n_clusters < 2:
        return math.nan, math.nan, n_clusters - 1

    meat = float(sum(s * s for s in cluster_sums.values()))

    # CR1 finite-sample correction.
    correction = n_clusters / (n_clusters - 1)

    # Var(mean) = correction · meat / N².
    var_mean = correction * meat / (n * n)

    # Degenerate zero-variance case (e.g. every score identical): the mean is
    # either exactly 0 (no signal) or a perfectly-estimated non-zero constant.
    # Return NaN to avoid a divide-by-zero rather than an infinite t-stat.
    if var_mean <= 0.0:
        return math.nan, math.nan, n_clusters - 1

    se     = math.sqrt(var_mean)
    t_stat = mean / se
    df     = n_clusters - 1

    # Two-sided p-value from the Student-t with (#clusters − 1) df.
    p_value = float(2.0 * scipy.stats.t.sf(abs(t_stat), df))

    return float(t_stat), p_value, df


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

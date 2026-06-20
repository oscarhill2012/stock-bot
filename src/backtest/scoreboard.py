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

Aggregated per analyst × horizon × lean-subset {all, bullish, bearish}:
  - mean excess in basis points  (headline, cross-window comparable)
  - hit rate                     (fraction of non-neutral verdicts with score > 0)
  - n                            (scored verdicts; window-edge excluded rows reduce n)
  - t-stat / p-value             (scipy.stats.ttest_1samp to separate signal from noise)

Phase 14 defect fixes
---------------------
1. **Cache-replay dedup** — the report cache replays a cached LLM verdict
   across many subsequent ticks (fundamental verdicts change slowly → ~83 %
   cache-hit rate).  Counting each replayed tick as a separate observation
   amplifies one confidently-wrong fresh call 6× into the score.

   Fix: collapse consecutive identical (lean, magnitude, confidence) tuples for
   the same (analyst, ticker) pair into a SINGLE observation anchored at the
   FIRST tick in the run.  The forward return is measured from that anchor tick.

   Caveat: the ``analyst_evidence`` table carries NO hash or identity column
   that directly tags a cache replay (it records only lean/magnitude/confidence/
   rationale).  The consecutive-identical proxy is a best-effort heuristic:
   it correctly handles the common replay pattern (many ticks with the same
   verdict) but would merge two genuinely independent verdicts that happen to
   be numerically identical.  This limitation is documented here and in the
   config README.

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
from typing import Literal, NamedTuple

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
        After Phase 14 dedup, this counts UNIQUE fresh-verdict observations,
        not total replayed rows.
    mean_excess_bps:
        Mean of ``score_h`` in basis points.  Positive means the analyst's
        directional calls outperformed the tick's peer-group mean.
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
    _primary_horizons:
        Internal mapping from analyst name to its primary scoring horizon.
        Accessed via ``primary_horizon(analyst)``.
    """
    cells:              dict[tuple[str, int, str], ScoreboardCell] = field(default_factory=dict)
    analysts:           list[str]                                  = field(default_factory=list)
    horizons:           list[int]                                  = field(default_factory=list)
    _primary_horizons:  dict[str, int]                             = field(default_factory=dict)

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


# ── Internal types ────────────────────────────────────────────────────────────

class _VerdictKey(NamedTuple):
    """Key for grouping verdicts by analyst + ticker + tick."""
    analyst:  str
    ticker:   str
    tick_id:  str


# Proxy identity for a cached verdict: (lean, magnitude, confidence) tuple.
# Two rows with the same identity are considered a cache-replay continuation;
# only the first occurrence in tick order is used as the anchor observation.
_VerdictIdentity = tuple[str, float, float]


# ── Public API ────────────────────────────────────────────────────────────────

def build_analyst_scoreboard(
    *,
    db_path: Path,
    cache: CachedDataStore,
    horizons: list[int],
    primary_horizon_by_analyst: dict[str, int] | None = None,
    neutralise_by: Literal["sector", "universe"] = "sector",
) -> ScoreboardResult:
    """Build the analyst predictive-power scoreboard from a completed run.

    Pure function: reads ``analyst_evidence`` from the run's ``db.sqlite``
    and prices from ``cache``; produces no side-effects.  Suitable for unit
    testing on a small fixture DB.

    Algorithm (Phase 14 revised)
    ----------------------------
    1. Load all ``AnalystEvidenceRow`` records, ordered by (analyst, ticker,
       recorded_at) so consecutive ticks are adjacent.
    2. **Dedup** (cache-replay fix): for each (analyst, ticker) pair, collapse
       consecutive identical (lean, magnitude, confidence) tuples into a single
       observation anchored at the FIRST occurrence.  Subsequent ticks with an
       identical tuple are discarded from the scoring universe.  A change in
       the tuple marks a new fresh call and starts a new run.
       Caveat: this is a proxy heuristic — there is no replay-identity column
       in ``analyst_evidence``.  See module docstring for limitations.
    3. For each deduplicated verdict, look up ``base_price`` (phase-matched)
       and forward closes per horizon from ``cache``.
    4. Compute per-tick cross-sectional means using the **peer group** for each
       ticker.  Peer group = same GICS sector (``read_company_ratios().sector``)
       when ``neutralise_by="sector"``; whole universe when ``"universe"`` or
       when sector data is absent.
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
    primary_horizon_by_analyst:
        Optional mapping from analyst name to its primary scoring horizon.
        Analysts not in the map fall back to ``max(horizons)``.
        If ``None``, all analysts default to ``max(horizons)``.
    neutralise_by:
        Cross-sectional neutralisation mode.
        ``"sector"``   — subtract per-tick sector-peer mean (preferred).
        ``"universe"`` — subtract per-tick whole-universe mean (fallback).

    Returns
    -------
    ScoreboardResult
        Fully populated result with one ``ScoreboardCell`` per
        ``(analyst, horizon, subset)`` combination.
    """
    primary_map: dict[str, int] = primary_horizon_by_analyst or {}
    default_horizon = max(horizons) if horizons else 1

    # ── 1. Load verdict rows, sorted for dedup ───────────────────────────────
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with Session(engine) as s:
        rows: list[AnalystEvidenceRow] = (
            s.execute(select(AnalystEvidenceRow)).scalars().all()
        )

    if not rows:
        logger.warning("scoreboard: no analyst_evidence rows found in %s", db_path)
        return ScoreboardResult()

    # Sort by (analyst, ticker, recorded_at) so consecutive ticks are adjacent
    # and the dedup pass can work in a single linear scan per (analyst, ticker) group.
    rows_sorted = sorted(
        rows,
        key=lambda r: (r.analyst, r.ticker, _ensure_aware(r.recorded_at)),
    )

    # ── 2. Dedup: collapse consecutive identical verdicts ────────────────────
    # For each (analyst, ticker) pair, the 'last seen' identity tuple is tracked.
    # When the tuple changes, the new row is a fresh call (anchor).
    # When the tuple matches the last anchor, the row is a cache replay (discarded).
    #
    # Proxy identity: (lean, magnitude, confidence).  Rationale/key_factors are
    # excluded because they are narrative and may vary trivially even for a cached
    # LLM output that was served from a content-addressed cache.
    #
    # LIMITATION: two genuinely independent identical verdicts on adjacent ticks
    # would be incorrectly merged.  This is unlikely in practice (independent
    # verdicts vary in confidence if not in lean), and the alternative (counting
    # every row) is far more misleading.  See module docstring.

    # last_identity[(analyst, ticker)] = (lean, magnitude, confidence) of the
    # last ANCHOR row for this pair.  Missing = no prior anchor.
    last_identity: dict[tuple[str, str], _VerdictIdentity] = {}
    deduplicated_rows: list[AnalystEvidenceRow] = []

    for row in rows_sorted:
        pair     = (row.analyst, row.ticker)
        identity = (row.lean, float(row.magnitude), float(row.confidence))

        if last_identity.get(pair) == identity:
            # Same identity as prior anchor → cache replay; discard.
            logger.debug(
                "scoreboard: dedup — discarding replayed verdict "
                "(analyst=%s, ticker=%s, lean=%s, mag=%.2f, conf=%.2f)",
                row.analyst, row.ticker, row.lean, row.magnitude, row.confidence,
            )
            continue

        # New identity or first occurrence → this is a fresh call (anchor).
        last_identity[pair] = identity
        deduplicated_rows.append(row)

    logger.info(
        "scoreboard: dedup reduced %d rows → %d unique fresh-call observations "
        "(%.1f %% removed as cache replays)",
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
    # group_key = sector string or None (universe).

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

    # ── 6. Compute per-verdict excess and accumulate scores ──────────────────
    # For each analyst row, find the appropriate cs mean and score the verdict.

    # Scores: (analyst, h) → subset → list[score]
    score_store: dict[
        tuple[str, int],
        dict[str, list[float]],
    ] = defaultdict(lambda: {"all": [], "bullish": [], "bearish": []})

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

            key = (row.analyst, h)
            score_store[key]["all"].append(score)
            if row.lean == "bullish":
                score_store[key]["bullish"].append(score)
            elif row.lean == "bearish":
                score_store[key]["bearish"].append(score)

    # ── 7. Aggregate into ScoreboardResult ───────────────────────────────────
    analysts_seen: list[str] = sorted({r.analyst for r in deduplicated_rows})
    result = ScoreboardResult(
        analysts=analysts_seen,
        horizons=sorted(horizons),
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
    (horizon × subset) combination.  The primary horizon for each analyst
    is marked with a ``★`` in the horizon label.

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
            "Coverage (n) excludes window-edge verdicts where no forward bar exists.  "
            "★ marks each analyst's primary scoring horizon.\n"
        ),
    ]

    if not result.analysts:
        lines.append("_No analyst evidence rows found._\n")
        return "\n".join(lines)

    for analyst in result.analysts:
        ph = result.primary_horizon(analyst)
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

                # Mark the primary horizon with ★ for easy identification.
                horizon_label = f"+{h}d★  " if h == ph else f"+{h}d   "

                lines.append(
                    f"| {horizon_label} | {subset:<8} | {c.n} | {mean_bps_str:>17} | {hit_str:<8} | {t_str:>6} | {p_str:>7} |"
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

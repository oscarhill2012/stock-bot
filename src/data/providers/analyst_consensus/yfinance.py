"""yfinance analyst-consensus provider.

Returns a consensus price-target + rating snapshot (``AnalystRating``) and a
list of recent upgrade/downgrade events (``list[AnalystRevision]``) for a
given ticker symbol.

Snapshot-only caveat
--------------------
yfinance does not expose a historical ``as_of`` date for the analyst tables —
``analyst_price_targets``, ``upgrades_downgrades``, and
``recommendations_summary`` always reflect the current state of the data as of
the moment the call is made.  The returned ``AnalystRating.as_of`` is set to
the requested ``as_of`` argument, but **the underlying values may be more
recent than that date**.

Consequence for backtesting: cache a fresh fetch on the relevant date (via
``backtest_fetch``) rather than re-fetching inside a replay.  A warning is
emitted when ``as_of < today − 7 days`` to flag calls that are likely using
stale proxy data.

Data sources used (all via ``yfinance.Ticker``)
------------------------------------------------
* ``.analyst_price_targets``    — dict with keys ``current, high, low, mean, median``
* ``.upgrades_downgrades``      — DataFrame of per-firm rating changes
* ``.recommendations_summary``  — DataFrame with ``period`` and ``strongBuy /
                                   buy / hold / sell / strongSell`` columns;
                                   period ``"0m"`` is the current-month row
* ``Ticker.info["numberOfAnalystOpinions"]`` — integer analyst count

Action mapping
--------------
Raw ``GradeChange`` / ``Action`` strings from yfinance are normalised to the
seven-value ``AnalystRevision.action`` Literal via ``_ACTION_MAP``.  Any
string not present in the map falls back to ``"unknown"``.
"""
from __future__ import annotations

import asyncio
import logging
import math
import warnings
from datetime import date, timedelta
from typing import Any

import yfinance as yf

from data.models.analyst_consensus import AnalystConsensusBundle, AnalystRating, AnalystRevision
from data.registry import register

log = logging.getLogger(__name__)

# ── Action normalisation ──────────────────────────────────────────────────────

# Maps the raw yfinance ``GradeChange`` / ``Action`` column strings to the
# controlled ``AnalystRevision.action`` Literal.  Keys are lower-cased for a
# case-insensitive lookup.
_ACTION_MAP: dict[str, str] = {
    # Upgrades
    "up":                "upgrade",
    "upgrade":           "upgrade",
    "upgraded to":       "upgrade",
    "raised to":         "upgrade",

    # Downgrades
    "down":              "downgrade",
    "downgrade":         "downgrade",
    "downgraded to":     "downgrade",
    "lowered to":        "downgrade",

    # Initiations / new coverage
    "init":              "initiate",
    "initiated":         "initiate",
    "initiates":         "initiate",
    "initiates coverage on": "initiate",
    "initiated coverage on": "initiate",
    "new":               "initiate",

    # Reiterations / maintained ratings
    "main":              "reiterate",
    "maintains":         "reiterate",
    "maintained":        "reiterate",
    "reiterate":         "reiterate",
    "reiterates":        "reiterate",
    "resumed":           "reiterate",

    # Price-target raises (grade unchanged)
    "target raised":     "target_raise",
    "raised":            "target_raise",
    "price target raised": "target_raise",

    # Price-target cuts (grade unchanged)
    "target lowered":    "target_cut",
    "lowered":           "target_cut",
    "price target lowered": "target_cut",
    "cut":               "target_cut",
}


def _normalise_action(raw: str | None) -> str:
    """Normalise a raw yfinance action/grade-change string to a controlled literal.

    Parameters
    ----------
    raw:
        The raw string from the ``GradeChange`` or ``Action`` column, or
        ``None`` when the field is absent.

    Returns
    -------
    str
        One of the seven ``AnalystRevision.action`` literals:
        ``"upgrade"``, ``"downgrade"``, ``"initiate"``, ``"reiterate"``,
        ``"target_raise"``, ``"target_cut"``, or ``"unknown"``.
    """
    if not raw:
        return "unknown"

    return _ACTION_MAP.get(raw.strip().lower(), "unknown")


# ── Rate limit parameters ─────────────────────────────────────────────────────

# Must match the existing yfinance limiter declared in stats/yfinance.py.
# The registry raises ``ValueError`` on conflicting declarations, so any
# mismatch is caught at import time.
_RATE_PER_MINUTE = 60
_BURST            = 30


# ── Provider ──────────────────────────────────────────────────────────────────

@register(
    domain="analyst_consensus",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=_RATE_PER_MINUTE,
    burst=_BURST,
)
async def fetch(
    ticker: str,
    *,
    as_of: date,
    max_revisions: int = 20,
    **_: Any,
) -> AnalystConsensusBundle:
    """Fetch the analyst consensus snapshot and recent revisions for ``ticker``.

    All data is sourced from ``yfinance.Ticker`` synchronously then wrapped in
    ``asyncio.to_thread`` so the coroutine does not block the event loop.

    Parameters
    ----------
    ticker:
        Upper-cased stock symbol (e.g. ``"AAPL"``).
    as_of:
        The simulation/backtest date.  Stored in the returned ``AnalystRating``
        but does **not** filter the underlying yfinance data (see module
        docstring for the snapshot-only caveat).
    max_revisions:
        Maximum number of ``AnalystRevision`` records to return, newest-first.
        Defaults to 20.
    **_:
        Absorbs extra keyword arguments passed by ``dispatch`` (e.g.
        ``as_of_dt``, ``limit``) so callers do not need to filter kwargs.

    Returns
    -------
    AnalystConsensusBundle
        Bundle containing ``rating`` (consensus snapshot) and ``revisions``
        (list of recent upgrades/downgrades), ordered newest-first and capped
        at ``max_revisions``.

    Warns
    -----
    UserWarning
        When ``as_of < today − 7 days`` — the returned values reflect "now",
        not the requested historical date.
    """
    symbol = ticker.upper()

    # Warn when the caller is using this provider for a historical as_of — the
    # data will reflect today's snapshot, not the historical state.
    today = date.today()
    if as_of < today - timedelta(days=7):
        warnings.warn(
            f"analyst_consensus/yfinance: as_of={as_of} is more than 7 days in the "
            f"past but yfinance returns current (live) data only.  Values will "
            f"reflect today ({today}), not {as_of}.  Use the backtest cache for "
            f"historical replay.",
            UserWarning,
            stacklevel=2,
        )

    # Run the blocking yfinance calls off the event loop.
    rating, revisions = await asyncio.to_thread(
        _fetch_sync, symbol, as_of, max_revisions
    )

    # Wrap the tuple into the canonical bundle shape (DOMAIN_SHAPES[analyst_consensus]).
    return AnalystConsensusBundle(rating=rating, revisions=revisions)


def _fetch_sync(
    symbol: str,
    as_of: date,
    max_revisions: int,
) -> tuple[AnalystRating, list[AnalystRevision]]:
    """Synchronous yfinance fetch — run inside ``asyncio.to_thread``.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    as_of:
        The requested as_of date; stored on the returned ``AnalystRating``.
    max_revisions:
        Maximum number of revision records to return.

    Returns
    -------
    tuple[AnalystRating, list[AnalystRevision]]
    """
    t = yf.Ticker(symbol)

    # ── Price targets ──────────────────────────────────────────────────────────
    # ``analyst_price_targets`` is a plain dict with keys:
    #   current, high, low, mean, median
    # Any key may be absent or None on tickers with thin coverage.
    targets: dict[str, Any] = {}
    try:
        raw_targets = t.analyst_price_targets
        if isinstance(raw_targets, dict):
            targets = raw_targets
    except Exception as exc:  # noqa: BLE001
        log.warning("analyst_consensus/yfinance: analyst_price_targets failed for %s: %s", symbol, exc)

    # ── Analyst count ──────────────────────────────────────────────────────────
    # ``recommendations_summary`` is a DataFrame with columns:
    #   period | strongBuy | buy | hold | sell | strongSell
    # Period "0m" is the current month's aggregated counts.
    n_analysts: int | None = None
    try:
        info = t.info or {}
        raw_count = info.get("numberOfAnalystOpinions")
        if raw_count is not None:
            n_analysts = int(raw_count)
    except Exception as exc:  # noqa: BLE001
        log.warning("analyst_consensus/yfinance: info fetch failed for %s: %s", symbol, exc)

    # ── Recommendation mean ────────────────────────────────────────────────────
    # Prefer ``recommendations_summary`` "0m" row; fall back to ``info`` key.
    rec_mean: float | None = None
    try:
        rec_summary = t.recommendations_summary
        if rec_summary is not None and not rec_summary.empty:
            # Filter to the current-period row ("0m").
            period_mask = rec_summary.get("period") == "0m"
            row_0m = rec_summary[period_mask]
            if not row_0m.empty:
                row = row_0m.iloc[0]
                strong_buy  = _to_int(row.get("strongBuy",  0))
                buy         = _to_int(row.get("buy",        0))
                hold        = _to_int(row.get("hold",       0))
                sell        = _to_int(row.get("sell",       0))
                strong_sell = _to_int(row.get("strongSell", 0))

                # Weighted mean: strongBuy=1, buy=2, hold=3, sell=4, strongSell=5
                total = strong_buy + buy + hold + sell + strong_sell
                if total > 0:
                    weighted = (
                        1 * strong_buy
                        + 2 * buy
                        + 3 * hold
                        + 4 * sell
                        + 5 * strong_sell
                    )
                    rec_mean = weighted / total
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "analyst_consensus/yfinance: recommendations_summary failed for %s: %s",
            symbol, exc,
        )

    # Fall back to ``info["recommendationMean"]`` when summary is unavailable.
    if rec_mean is None:
        try:
            info = t.info or {}
            raw_mean = info.get("recommendationMean")
            if raw_mean is not None:
                rec_mean = float(raw_mean)
        except Exception as exc:  # noqa: BLE001
            log.debug("analyst_consensus/yfinance: recommendationMean fallback failed for %s: %s", symbol, exc)

    rating = AnalystRating(
        ticker=symbol,
        as_of=as_of,
        target_high=_to_float(targets.get("high")),
        target_low=_to_float(targets.get("low")),
        target_mean=_to_float(targets.get("mean")),
        target_median=_to_float(targets.get("median")),
        recommendation_mean=rec_mean,
        number_of_analysts=n_analysts,
    )

    # ── Upgrades / downgrades ──────────────────────────────────────────────────
    revisions: list[AnalystRevision] = []
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            # The DataFrame index is the event date; reset to make it a column.
            ud = ud.reset_index()
            date_col = _detect_date_col(ud)

            for _, row in ud.iterrows():
                event_date = _parse_event_date(row.get(date_col))
                if event_date is None:
                    continue

                firm       = str(row.get("Firm", "") or "").strip() or "Unknown"
                grade_from = _str_or_none(row.get("FromGrade"))
                grade_to   = _str_or_none(row.get("ToGrade"))
                action_raw = _str_or_none(row.get("Action") or row.get("GradeChange"))
                action     = _normalise_action(action_raw)

                revisions.append(AnalystRevision(
                    ticker=symbol,
                    firm=firm,
                    action=action,
                    from_grade=grade_from,
                    to_grade=grade_to,
                    event_date=event_date,
                ))

            # Newest-first so callers can slice [:N] for recency.
            revisions.sort(key=lambda r: r.event_date, reverse=True)

    except Exception as exc:  # noqa: BLE001
        log.warning(
            "analyst_consensus/yfinance: upgrades_downgrades failed for %s: %s",
            symbol, exc,
        )

    return rating, revisions[:max_revisions]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(value: Any) -> float | None:
    """Coerce ``value`` to float, returning ``None`` on failure.

    Parameters
    ----------
    value:
        Any raw value from a yfinance dict or DataFrame cell.

    Returns
    -------
    float | None
    """
    try:
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    """Coerce ``value`` to int, defaulting to 0 on failure.

    Parameters
    ----------
    value:
        Raw value from a yfinance DataFrame cell.

    Returns
    -------
    int
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _str_or_none(value: Any) -> str | None:
    """Return ``str(value).strip()`` or ``None`` for falsy / empty values.

    Parameters
    ----------
    value:
        Raw value from a yfinance DataFrame cell.

    Returns
    -------
    str | None
    """
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _detect_date_col(df: Any) -> str:
    """Return the name of the date column in an upgrades_downgrades DataFrame.

    yfinance uses ``"GradeDate"`` in some versions and ``"Date"`` in others;
    after ``reset_index`` the original index lands as ``"Date"`` or
    ``"GradeDate"`` depending on the version.

    Parameters
    ----------
    df:
        The DataFrame returned by ``yf.Ticker.upgrades_downgrades`` after
        ``reset_index()``.

    Returns
    -------
    str
        Column name to use for the event date.
    """
    for candidate in ("GradeDate", "Date", "date", "gradeDate"):
        if candidate in df.columns:
            return candidate
    # Fallback — first column is typically the date after reset_index.
    return df.columns[0]


def _parse_event_date(raw: Any) -> date | None:
    """Parse a yfinance date cell into a ``datetime.date``.

    Handles ``pandas.Timestamp``, ``datetime.datetime``, ``datetime.date``,
    and ISO-format strings.

    Parameters
    ----------
    raw:
        Raw value from the date column of the DataFrame.

    Returns
    -------
    date | None
        Parsed date, or ``None`` if the value cannot be interpreted.
    """
    if raw is None:
        return None

    # pandas.Timestamp / datetime.datetime — both have a .date() method.
    if hasattr(raw, "date") and callable(raw.date):
        try:
            return raw.date()
        except Exception:  # noqa: BLE001
            pass

    # datetime.date already.
    if isinstance(raw, date):
        return raw

    # ISO string fallback.
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None

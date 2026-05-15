"""PIT-correct ``company_ratios`` — edgartools XBRL fundamentals + yfinance OHLCV.

The ``CompanyRatios`` model carries three classes of field:

- **Identity** (``long_name``, ``sector``) — from XBRL submission metadata.
- **Raw fundamentals** (shares_out, eps_ttm, dps_ttm — implicit via
  ``trailing_pe``, ``dividend_yield``) — from XBRL ``EntityFacts.query().as_of``.
- **Price-dependent / technical** (``last_price``, ``market_cap``,
  ``trailing_pe``, ``dividend_yield``, ``fifty_day_average``,
  ``two_hundred_day_average``) — derived from yfinance OHLCV history sliced
  to ``as_of``.

Live behaviour: when ``as_of`` is "now" (the wrapper default), this reduces to
"use today's OHLCV close + latest XBRL facts" — identical signal to the old
yfinance provider, just with authoritative SEC fundamentals.
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import yfinance as yf
from edgar import Company, set_identity

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import CompanyRatios, OHLCBar, PriceHistory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Facts:
    """Subset of XBRL facts the composite provider needs.

    All fields are optional because EDGAR submissions are sparse — not every
    company files every concept, and not every concept existed at every date.
    """

    long_name:   str | None
    sector:      str | None
    shares_out:  float | None
    eps_ttm:     float | None
    dps_ttm:     float | None


def _ensure_identity() -> None:
    """Set the EDGAR User-Agent identity required by the SEC fair-use policy.

    Reads ``EDGAR_IDENTITY`` from the environment via ``require_key`` so the
    call only fails at fetch time, not at import time.
    """
    set_identity(require_key("EDGAR_IDENTITY"))


def _safe_float(v: Any) -> float | None:
    """Coerce ``v`` to a finite float; return ``None`` on failure or non-finite.

    Parameters
    ----------
    v:
        Any value returned by edgartools — may be int, float, Decimal, str,
        or ``None``.

    Returns
    -------
    float | None
        A finite float, or ``None`` when conversion fails or the value is
        infinite / NaN.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@with_retry
def _fetch_xbrl_facts(symbol: str, as_of_date: date) -> _Facts:
    """Pull the snapshot of SEC fundamentals known at ``as_of_date`` for ``symbol``.

    Uses edgartools ``EntityFacts.query().by_concept().as_of()`` to retrieve
    point-in-time XBRL data.  Missing facts are represented as ``None``; callers
    must handle sparse returns gracefully.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    as_of_date:
        The historical date for which to retrieve the XBRL snapshot.

    Returns
    -------
    _Facts
        Dataclass with fundamental fields; any unavailable field is ``None``.
    """
    _ensure_identity()
    company = Company(symbol)
    facts   = company.get_facts()

    def _scalar(concept: str) -> float | None:
        """Return the most recent value of ``concept`` as of ``as_of_date``.

        Parameters
        ----------
        concept:
            us-gaap concept name (e.g. ``"EarningsPerShareBasic"``).

        Returns
        -------
        float | None
            Parsed float, or ``None`` if the concept is absent / unparseable.
        """
        try:
            q   = facts.query().by_concept(concept).as_of(as_of_date)
            row = q.latest() if hasattr(q, "latest") else None
            return _safe_float(getattr(row, "value", None)) if row else None
        except Exception:
            return None

    # Try diluted EPS first; fall back to basic.
    eps  = _scalar("EarningsPerShareBasic") or _scalar("EarningsPerShareDiluted")
    dps  = _scalar("CommonStockDividendsPerShareDeclared")

    # Two XBRL concepts cover shares outstanding across different filing eras.
    shrs = (
        _scalar("CommonStockSharesOutstanding")
        or _scalar("EntityCommonStockSharesOutstanding")
    )

    # Identity comes from the company entity object, not a fact table.
    long_name = getattr(company, "name", None) or getattr(company, "company_name", None)
    sector    = getattr(company, "sic_description", None) or getattr(company, "sector", None)

    return _Facts(
        long_name  = str(long_name) if long_name else None,
        sector     = str(sector)    if sector    else None,
        shares_out = shrs,
        eps_ttm    = eps,
        dps_ttm    = dps,
    )


@with_retry
def _fetch_price_series(symbol: str, as_of: datetime) -> PriceHistory:
    """Pull yfinance ``period="max"`` daily history and slice to ``as_of``.

    Fetching ``period="max"`` and slicing client-side gives a PIT-correct view:
    no prices after ``as_of.date()`` can leak into the result.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    as_of:
        Bars with a date strictly after ``as_of.date()`` are excluded.

    Returns
    -------
    PriceHistory
        Bars ordered oldest → newest, truncated at ``as_of``.  Empty when
        yfinance returns no data for the ticker.
    """
    ticker = yf.Ticker(symbol)
    df     = ticker.history(period="max", interval="1d", auto_adjust=True)

    bars: list[OHLCBar] = []
    if df is not None and not df.empty:
        cutoff = as_of.date()
        for ts, row in df.iterrows():
            bar_date = ts.date() if hasattr(ts, "date") else ts
            if bar_date > cutoff:
                continue
            bars.append(OHLCBar(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0) or 0),
            ))

    return PriceHistory(ticker=symbol, bars=bars)


def _moving_average(closes: list[float], window: int) -> float | None:
    """Mean of the last ``window`` closes; ``None`` when fewer bars are available.

    Parameters
    ----------
    closes:
        Ordered list of close prices (oldest first).
    window:
        Number of most-recent bars to average.

    Returns
    -------
    float | None
        Arithmetic mean of the trailing window, or ``None`` if there are fewer
        bars than ``window``.
    """
    if len(closes) < window:
        return None
    return statistics.fmean(closes[-window:])


def _ratios_from_components(
    symbol:  str,
    facts:   _Facts,
    history: PriceHistory,
) -> CompanyRatios:
    """Combine XBRL ``_Facts`` + sliced ``PriceHistory`` into a ``CompanyRatios``.

    Price-derived fields (market cap, trailing P/E, dividend yield, moving
    averages) are all computed from the sliced OHLCV series so they are
    point-in-time correct.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    facts:
        XBRL fundamentals snapshot from ``_fetch_xbrl_facts``.
    history:
        PIT-sliced OHLCV bars from ``_fetch_price_series``.

    Returns
    -------
    CompanyRatios
        Fully populated model; unavailable fields are ``None``.
    """
    closes = [b.close for b in history.bars]
    last   = closes[-1] if closes else None

    market_cap = (
        facts.shares_out * last
        if facts.shares_out is not None and last is not None
        else None
    )

    trailing_pe = (
        last / facts.eps_ttm
        if last is not None and facts.eps_ttm not in (None, 0)
        else None
    )

    dividend_yield = (
        facts.dps_ttm / last
        if last is not None and facts.dps_ttm is not None and last != 0
        else None
    )

    fifty_day    = _moving_average(closes,  50)
    two_hundred  = _moving_average(closes, 200)

    return CompanyRatios(
        ticker                  = symbol,
        long_name               = facts.long_name,
        sector                  = facts.sector,
        market_cap              = market_cap,
        trailing_pe             = trailing_pe,
        forward_pe              = None,   # Requires analyst estimates — not in XBRL.
        beta                    = None,   # Deferred: 1-year SPY correlation (future work).
        dividend_yield          = dividend_yield,
        fifty_day_average       = fifty_day,
        two_hundred_day_average = two_hundred,
        last_price              = last,
    )


# Upstream is "yfinance" because price fetching is the dominant rate-limited
# call here — the EDGAR call uses the edgar limiter via _ensure_identity inside
# _fetch_xbrl_facts (no token acquisition needed for Company.get_facts itself).
@register(
    domain="company_ratios",
    name="pit_composite",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    period: str = "1y",
    interval: str = "1d",
    **_unused: Any,
) -> CompanyRatios:
    """PIT-correct ``CompanyRatios`` snapshot for ``ticker`` at ``as_of``.

    Runs the XBRL fetch and the OHLCV fetch concurrently via
    ``asyncio.gather``, then combines the results.

    ``period`` / ``interval`` are accepted for signature parity with the
    existing yfinance provider but this composite always fetches
    ``period="max"`` daily bars internally and slices to ``as_of``.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    as_of:
        Required keyword-only.  Historical date for the PIT snapshot.
    period:
        Accepted for dispatch parity; ignored internally.
    interval:
        Accepted for dispatch parity; ignored internally.
    **_unused:
        Absorbs any extra kwargs passed by the dispatch layer.

    Returns
    -------
    CompanyRatios
        Fundamentals + price-derived fields, all PIT-correct as of ``as_of``.
    """
    symbol = ticker.upper()

    facts, history = await asyncio.gather(
        asyncio.to_thread(_fetch_xbrl_facts,   symbol, as_of.date()),
        asyncio.to_thread(_fetch_price_series, symbol, as_of),
    )

    return _ratios_from_components(symbol, facts, history)

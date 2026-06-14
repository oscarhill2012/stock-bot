"""PIT-correct ``company_ratios`` — edgartools XBRL fundamentals + yfinance OHLCV.

The ``CompanyRatios`` model carries three classes of field:

- **Identity** (``long_name``, ``sector``) — from XBRL submission metadata.
- **Raw fundamentals** (shares_out, eps_ttm, dps_ttm — implicit via
  ``trailing_pe``, ``dividend_yield``) — from XBRL ``EntityFacts.query().as_of``.
- **Price-dependent / technical** (``last_price``, ``market_cap``,
  ``trailing_pe``, ``dividend_yield``, ``fifty_day_average``,
  ``two_hundred_day_average``) — derived from yfinance OHLCV history sliced
  to ``as_of``.
- **Derived XBRL ratios** (``profit_margin``, ``debt_to_equity``, ``roe``,
  ``revenue_growth_yoy``, ``free_cash_flow``) — computed from SEC-filed
  concepts via ``_load_xbrl_summary``.  All default to ``None`` when the
  required concepts are absent or the company has no XBRL data.
- **PEG ratio** — intentionally always ``None``.  Forward EPS growth (the "G"
  in PEG) is broker / analyst consensus and is not in XBRL; the only
  available source (``yf.Ticker.info["pegRatio"]``) returns a wall-clock
  value that leaks future information into backtest replays.  Until a
  PIT-correct source exists the field is surfaced as ``None`` in both live
  and backtest so the two modes match.

Live behaviour: when ``as_of`` is "now" (the wrapper default), this reduces to
"use today's OHLCV close + latest XBRL facts" — identical signal to the old
yfinance provider, just with authoritative SEC fundamentals.
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
from collections.abc import Callable
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


# Module-level flag — ``set_identity`` only needs to run once per process,
# but ``_ensure_identity`` is called inside every XBRL fetch path.  Without
# this flag the underlying ``edgar.core`` logger emits an INFO line on every
# call (twice per snapshot × every ticker × every trading day in a backtest
# fill), drowning out genuinely useful progress output.
_IDENTITY_SET: bool = False


def _ensure_identity() -> None:
    """Set the EDGAR User-Agent identity required by the SEC fair-use policy.

    Idempotent — the underlying ``set_identity`` call is fired at most once
    per process.  Reads ``EDGAR_IDENTITY`` from the environment via
    ``require_key`` so the call only fails at fetch time, not at import time.
    """
    global _IDENTITY_SET

    if _IDENTITY_SET:
        return

    set_identity(require_key("EDGAR_IDENTITY"))
    _IDENTITY_SET = True


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
            q    = facts.query().by_concept(concept).as_of(as_of_date)
            # ``q.latest()`` returns a *list* of FinancialFact rows (one per
            # period of the most recent filing).  We take the first row — it
            # carries the scalar ``value`` we want.
            rows = q.latest() if hasattr(q, "latest") else None
            row  = rows[0] if rows else None
            return _safe_float(getattr(row, "value", None)) if row else None
        except Exception:  # noqa: BLE001 — edgartools internals raise unpredictably; log and degrade
            logger.debug("XBRL scalar fetch failed for concept=%r symbol=%r", concept, symbol)
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


# Candidate us-gaap revenue concepts, in the order we prefer them.  No single
# concept is the top line for every filer: ASC-606 adopters (most tech) report
# under ``RevenueFromContractWithCustomerExcludingAssessedTax`` and leave a
# stale fragment under ``Revenues``; many energy / industrial filers are the
# reverse, and some older filers only ever filed ``SalesRevenueNet``.  Priority
# is a tie-breaker only — period-distinctness (below) decides first, so a
# higher-priority concept that is stale for a given filer is skipped in favour
# of a lower-priority concept that genuinely moves year-on-year.
_REVENUE_CONCEPTS: tuple[str, ...] = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)


def _select_revenue_series(
    ttm_at:     Callable[[str, date], float | None],
    as_of_date: date,
    prior_date: date,
) -> tuple[float | None, float | None, str | None]:
    """Pick the XBRL revenue concept that actually reflects the filer's TTM top line.

    No single us-gaap revenue concept is correct across all filers (see
    ``_REVENUE_CONCEPTS``).  The reliable discriminator is *period-distinctness*:
    the genuinely-filed concept yields different TTM values at ``as_of_date``
    versus a year earlier, whereas a stale single-fact fragment returns the
    identical value for any date.  A real operating company's TTM revenue never
    lands on the same figure to the dollar two years running, so an exact
    equality is a dependable "stale fragment" signal — and exact equality is
    safer here than a tolerance, which could wrongly reject a genuinely
    near-flat live series.

    Parameters
    ----------
    ttm_at:
        Callable returning the TTM value of a concept as of a given date, or
        ``None`` when the filer never reported it.  Injected so the selection
        logic is unit-testable without an EDGAR round-trip.
    as_of_date:
        The point-in-time date for the current TTM figure.
    prior_date:
        One year before ``as_of_date`` — the comparison point for growth.

    Returns
    -------
    tuple[float | None, float | None, str | None]
        ``(rev_now, rev_prior, concept)`` for the first concept (in priority
        order) whose current and prior values are both present and distinct.
        Falls back to ``(rev_now, None, concept)`` for the first concept with a
        present current value but no usable prior (e.g. a recent IPO, or a
        filer whose every concept is stale) — so the margin denominator still
        resolves while revenue_growth_yoy is left unset rather than computed as
        a false 0.  Returns ``(None, None, None)`` when no concept has data.
    """
    # First pass — prefer a concept that genuinely moves year-on-year.
    for concept in _REVENUE_CONCEPTS:
        now   = ttm_at(concept, as_of_date)
        prior = ttm_at(concept, prior_date)
        if now is not None and prior is not None and now != prior:
            return now, prior, concept

    # Second pass — no period-distinct concept found.  Use the first concept
    # with a present current value so profit_margin still has a denominator;
    # the ``None`` prior signals "do not compute revenue_growth_yoy".
    for concept in _REVENUE_CONCEPTS:
        now = ttm_at(concept, as_of_date)
        if now is not None:
            return now, None, concept

    return None, None, None


def _load_xbrl_summary(symbol: str, as_of_date: date) -> dict[str, float | None]:
    """Derive five ratio fields from XBRL ``EntityFacts`` filed at or before ``as_of_date``.

    Each ratio is computed from US-GAAP-taxonomy concepts using trailing
    twelve-month (TTM) figures where the spec requires it, and a single
    point-in-time balance-sheet figure where the spec requires that.  If a
    required concept is missing the field silently defaults to ``None``.

    ``peg`` is also present in the returned dict but is intentionally left
    as ``None`` — there is no PIT-correct source for the forward growth
    term, so the field is surfaced as ``None`` in both live and backtest.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    as_of_date:
        Point-in-time gate — only facts filed on or before this date are used.

    Returns
    -------
    dict[str, float | None]
        Keys: ``profit_margin``, ``debt_to_equity``, ``roe``,
        ``revenue_growth_yoy``, ``free_cash_flow``, ``peg``.  All values are
        ``float | None``; ``peg`` is always ``None``.
    """
    _ensure_identity()

    # Initialise all output fields to None; we populate what we can.  ``peg``
    # is kept in the dict shape (rather than dropped) so callers — and the
    # ``_ratios_from_components`` reader — don't need to special-case the
    # absence of the key.  It is never populated to a non-``None`` value.
    result: dict[str, float | None] = {
        "profit_margin":      None,
        "debt_to_equity":     None,
        "roe":                None,
        "revenue_growth_yoy": None,
        "free_cash_flow":     None,
        "peg":                None,
    }

    # --- Attempt to pull XBRL facts; ADRs / recent IPOs may have no data ---
    try:
        company = Company(symbol)
        facts   = company.get_facts()
    except Exception:  # noqa: BLE001 — edgartools raises RuntimeError/httpx errors/internal types; log and return empty
        logger.warning("XBRL facts unavailable for %s; returning empty ratio summary.", symbol, exc_info=True)
        return result  # type: ignore[return-value]

    def _ttm_at(concept: str, at: date) -> float | None:
        """Return the trailing-twelve-month value for ``concept`` as of ``at``.

        Uses ``EntityFacts.query().by_concept().as_of()`` and the ``.latest()``
        result.  Returns ``None`` for any error, including missing concept.

        Parameters
        ----------
        concept:
            US-GAAP concept name without namespace prefix (e.g.
            ``"NetIncomeLoss"``).
        at:
            Point-in-time gate — only facts filed on or before this date are
            considered.  Parameterised (rather than closing over ``as_of_date``)
            so the revenue selector can probe the prior-year value too.

        Returns
        -------
        float | None
            Parsed finite float, or ``None`` on any failure.
        """
        try:
            q    = facts.query().by_concept(concept).as_of(at)
            # ``q.latest()`` returns a *list* of FinancialFact rows from the
            # most recent filing — take the first (the canonical TTM/period
            # value for the concept).
            rows = q.latest() if hasattr(q, "latest") else None
            row  = rows[0] if rows else None
            return _safe_float(getattr(row, "value", None)) if row else None
        except Exception:  # noqa: BLE001 — edgartools internals raise unpredictably; log and degrade
            logger.debug("XBRL TTM fetch failed for concept=%r symbol=%r at=%r", concept, symbol, at)
            return None

    def _ttm(concept: str) -> float | None:
        """Trailing-twelve-month value for ``concept`` at the snapshot ``as_of_date``."""
        return _ttm_at(concept, as_of_date)

    # --- Revenue: pick the genuinely-filed concept for this filer (see
    #     ``_select_revenue_series``) and reuse it for both the margin
    #     denominator and the YoY growth calculation, so the two never disagree
    #     about which concept is the top line. ---
    prior_date = as_of_date.replace(year=as_of_date.year - 1)
    rev_now, rev_prior, _rev_concept = _select_revenue_series(_ttm_at, as_of_date, prior_date)

    # --- profit_margin: NetIncomeLoss / selected revenue (both TTM) ---
    net_income = _ttm("NetIncomeLoss")
    if net_income is not None and rev_now is not None and rev_now != 0:
        result["profit_margin"] = net_income / rev_now

    # --- debt_to_equity: total_debt / StockholdersEquity ---
    # Missing debt addends default to 0; negative equity → None (meaningless).
    equity           = _ttm("StockholdersEquity")
    long_term_nc     = _ttm("LongTermDebtNoncurrent")  or 0.0
    long_term_curr   = _ttm("LongTermDebtCurrent")     or 0.0
    short_term       = _ttm("ShortTermBorrowings")     or 0.0
    total_debt       = long_term_nc + long_term_curr + short_term
    if equity is not None and equity > 0:
        result["debt_to_equity"] = total_debt / equity

    # --- roe: NetIncomeLoss (TTM) / StockholdersEquity (balance sheet) ---
    if net_income is not None and equity is not None and equity > 0:
        result["roe"] = net_income / equity

    # --- revenue_growth_yoy: (rev_now - rev_1y_ago) / rev_1y_ago ---
    # Both legs come from the *same* selected concept (above), so a filer whose
    # ``Revenues`` fragment is stale no longer collapses to a false 0% growth.
    # ``rev_prior`` is None when no period-distinct series exists, in which case
    # growth is intentionally left unset rather than fabricated.
    if rev_now is not None and rev_prior is not None and rev_prior != 0:
        result["revenue_growth_yoy"] = (rev_now - rev_prior) / rev_prior

    # --- free_cash_flow: OperatingCashFlow - CapEx (both TTM) ---
    operating_cf = _ttm("NetCashProvidedByUsedInOperatingActivities")
    capex        = _ttm("PaymentsToAcquirePropertyPlantAndEquipment")
    if operating_cf is not None and capex is not None:
        result["free_cash_flow"] = operating_cf - capex

    # --- peg: intentionally left None ---
    # PEG = trailing P/E ÷ forward EPS growth.  The "G" term is broker /
    # analyst consensus and is not filed in XBRL.  The only available source
    # (``yf.Ticker(symbol).info["pegRatio"]``) returns whatever yfinance has
    # cached *today* — a wall-clock value that would be identical for every
    # historical ``as_of`` and so would leak future information into
    # backtest replays.  Until we find a PIT-correct source we surface
    # ``None`` everywhere, in both live and backtest, so the strategist's
    # "PEG:" bullet renders the same in both modes (no live/backtest skew).

    return result  # type: ignore[return-value]


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
    symbol:      str,
    facts:       _Facts,
    history:     PriceHistory,
    xbrl_ratios: dict[str, float | None],
    as_of:       date,
) -> CompanyRatios:
    """Combine XBRL ``_Facts`` + sliced ``PriceHistory`` + derived ratios into a ``CompanyRatios``.

    Price-derived fields (market cap, trailing P/E, dividend yield, moving
    averages) are all computed from the sliced OHLCV series so they are
    point-in-time correct.  The six XBRL-derived ratios come from
    ``xbrl_ratios`` (output of ``_load_xbrl_summary``).

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    facts:
        XBRL fundamentals snapshot from ``_fetch_xbrl_facts``.
    history:
        PIT-sliced OHLCV bars from ``_fetch_price_series``.
    xbrl_ratios:
        Dict from ``_load_xbrl_summary`` with the five derived ratio fields
        and a ``peg`` key that is always ``None`` (kept in the dict shape
        for forward compatibility — see ``_load_xbrl_summary``).
    as_of:
        The point-in-time date for this snapshot; stored on the model so
        the backtest cache can key lookups correctly.

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

    fifty_day   = _moving_average(closes,  50)
    two_hundred = _moving_average(closes, 200)

    return CompanyRatios(
        ticker                  = symbol,
        as_of                   = as_of,
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
        # Six XBRL-derived ratios (None when concepts are absent or company
        # has no XBRL data — e.g. ADRs, recent IPOs, foreign filers).
        profit_margin           = xbrl_ratios.get("profit_margin"),
        debt_to_equity          = xbrl_ratios.get("debt_to_equity"),
        roe                     = xbrl_ratios.get("roe"),
        revenue_growth_yoy      = xbrl_ratios.get("revenue_growth_yoy"),
        free_cash_flow          = xbrl_ratios.get("free_cash_flow"),
        peg                     = xbrl_ratios.get("peg"),
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
    symbol   = ticker.upper()
    as_of_d  = as_of.date() if isinstance(as_of, datetime) else as_of

    # Run all three IO-bound fetches concurrently.  ``_load_xbrl_summary`` makes
    # its own edgartools + yfinance calls internally; keep it on a thread too.
    facts, history, xbrl_ratios = await asyncio.gather(
        asyncio.to_thread(_fetch_xbrl_facts,    symbol, as_of_d),
        asyncio.to_thread(_fetch_price_series,  symbol, as_of),
        asyncio.to_thread(_load_xbrl_summary,   symbol, as_of_d),
    )

    return _ratios_from_components(symbol, facts, history, xbrl_ratios, as_of_d)

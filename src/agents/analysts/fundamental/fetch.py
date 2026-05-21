"""Fundamental analyst fetch helpers.

Provides the per-ticker context-building helpers used by ``FundamentalFetchAgent``
(``agents.analysts.fundamental.fetch_agent``).

The per-ticker triad payload layout is::

    {
        "ratios":  <dict from CompanyRatios.model_dump() | None on failure>,
        "filings": [<Filing.model_dump()>, ...],
        "insider": <Form4Bundle instance>,
    }

The context block written into state combines:

- Filing excerpts (MD&A + risk factors) for each ticker.
- A structured insider activity section (numeric flows + footnote prose).

Separating the LLM-readable context from the machine-readable data dict
keeps the prompt compact and avoids serialising the entire ``Form4Bundle``
object graph into the instruction.

The legacy ``fundamental_fetch_callback`` (an ADK ``before_agent_callback``)
was retired in Phase 9 when the per-ticker fan-out design replaced the batched
``FundamentalAnalyst`` LlmAgent.  Only the formatting helpers and the
``_build_ticker_fundamental_context`` adapter shim remain here so that
``FundamentalFetchAgent`` can reuse the logic without duplication.
"""
from __future__ import annotations

import logging

from config.analysts import FundamentalCaps, get_analysts_config
from data.config import get_config
from data.models import Form4Bundle, InsiderTrade

_logger = logging.getLogger(__name__)


def _caps() -> FundamentalCaps:
    """Return the ``FundamentalCaps`` section from the analysts config.

    Reads caps lazily on first call — avoids running the config loader at
    module import time, which simplifies test isolation.

    Returns
    -------
    FundamentalCaps
        Validated caps object containing all four truncation settings for the
        Fundamental analyst's LLM context block.
    """
    return get_analysts_config().fundamental


def _build_ticker_context(
    ticker: str,
    filings_payload: list[dict],
    insider_bundle: Form4Bundle,
    insider_lookback_days: int,
) -> str:
    """Build the LLM-readable context block for a single ticker.

    Combines filing excerpts (MD&A + risk factors) and a structured insider
    activity section (numeric metrics + footnote prose) into one formatted
    text block.  This text is concatenated across all tickers and written to
    ``state["fundamental_context"]`` by the fetch callback.

    Parameters
    ----------
    ticker:
        Ticker symbol label.
    filings_payload:
        List of ``Filing.model_dump()`` dicts for the ticker.
    insider_bundle:
        Typed ``Form4Bundle`` containing common-stock trades and derivatives.

    Returns
    -------
    str
        A formatted text block suitable for direct inclusion in an LLM prompt.
    """
    lines: list[str] = [f"=== {ticker} ==="]

    # Read all four caps from config once per call.
    caps = _caps()

    # --- Filing excerpts ---
    if filings_payload:
        lines.append("-- COMPANY FILINGS (PROSE) --")
        for filing in filings_payload:
            form_type = filing.get("form_type", "?")
            filed_at  = filing.get("filed_at", "?")

            mda      = (filing.get("mda_excerpt") or "").strip()
            risk_fac = (filing.get("risk_factors_excerpt") or "").strip()

            if mda or risk_fac:
                lines.append(f"  [{form_type}, filed {filed_at}]")
                if mda:
                    lines.append(f"  MD&A: {mda[:caps.max_filing_mda_chars]}")
                if risk_fac:
                    lines.append(f"  Risk factors: {risk_fac[:caps.max_filing_risk_chars]}")
    else:
        lines.append("-- COMPANY FILINGS (PROSE) --")
        lines.append("  (no filings available)")

    # --- Insider activity (structured numerics) ---
    # Window label reflects the *configured* lookback (defaults.insider_lookback_days),
    # not a hardcoded "30d" — those days flow from config/data.json and can drift.
    lines.append(f"-- INSIDER ACTIVITY ({insider_lookback_days}d, structured) --")

    trades = insider_bundle.trades if insider_bundle else []
    buys   = [t for t in trades if t.side == "buy"]
    sells  = [t for t in trades if t.side == "sell"]

    # Net dollar value of open-market transactions.
    buy_val  = sum(
        (t.shares or 0.0) * (t.price_per_share or 0.0) for t in buys
    )
    sell_val = sum(
        (t.shares or 0.0) * (t.price_per_share or 0.0) for t in sells
    )
    net_dollars = buy_val - sell_val

    # Planned-sale (10b5-1) ratio among sells.
    planned_ratio = (
        sum(1 for t in sells if t.is_10b5_1) / len(sells)
        if sells
        else 0.0
    )

    # Cluster flags — multiple distinct filers on the same side.
    buy_names  = {t.insider_name for t in buys}
    sell_names = {t.insider_name for t in sells}
    cluster_buy  = len(buy_names)  >= 3
    cluster_sell = len(sell_names) >= 3

    # Max role rank — proxy for seniority of the most active insider.
    _ROLE_RANK = {"CEO": 5, "CFO": 4, "PRESIDENT": 4, "SVP": 3, "VP": 2, "DIRECTOR": 1}

    def _role_rank_name(trade: InsiderTrade) -> tuple[int, str]:
        """Return (rank, normalised_title) for an ``InsiderTrade``."""
        title = (trade.insider_title or "").upper()
        for keyword, rank in _ROLE_RANK.items():
            if keyword in title:
                return rank, trade.insider_title or ""
        return 0, trade.insider_title or "unknown"

    top_rank, top_role = max(
        (_role_rank_name(t) for t in trades),
        key=lambda x: x[0],
        default=(0, "none"),
    )

    # Derivative counts from the derivatives table.
    derivatives = insider_bundle.derivatives if insider_bundle else []
    exercise_count = sum(1 for d in derivatives if d.transaction_code == "M")
    grant_count    = sum(1 for d in derivatives if d.transaction_code == "A")

    lines.extend([
        f"  net Form-4 dollars:           {net_dollars:,.0f}",
        f"  buys / sells (count):         {len(buys)} / {len(sells)}",
        f"  cluster_buying:               {cluster_buy}",
        f"  cluster_selling:              {cluster_sell}",
        f"  planned-sale ratio (10b5-1):  {planned_ratio:.2f}",
        f"  top filer role:               {top_role}",
        f"  derivative exercises:         {exercise_count}",
        f"  derivative grants:            {grant_count}",
    ])

    # --- Insider footnotes (prose, capped) ---
    lines.append(f"-- INSIDER FOOTNOTES (≤{caps.max_insider_footnotes}, prose) --")
    footnotes: list[str] = []

    for trade in trades:
        note = (getattr(trade, "footnote", None) or "").strip()
        if note:
            footnotes.append(note[:caps.max_insider_footnote_chars])

    for deriv in derivatives:
        note = (getattr(deriv, "footnote", None) or "").strip()
        if note:
            footnotes.append(note[:caps.max_insider_footnote_chars])

    if footnotes:
        for i, note in enumerate(footnotes[:caps.max_insider_footnotes]):
            lines.append(f"  [{i + 1}] {note}")
    else:
        lines.append("  (no footnotes)")

    return "\n".join(lines)


def _build_ticker_fundamental_context(ticker: str, data: dict) -> str:
    """Adapter shim for ``FundamentalFetchAgent`` — wraps ``_build_ticker_context``.

    The per-ticker agent stores each ticker's payload in a flat dict
    ``{"ratios": ..., "filings": [...], "insider": Form4Bundle}``.  This
    adapter unpacks that dict and forwards to ``_build_ticker_context`` with
    the correct positional arguments, so the agent's call site stays tidy.

    Parameters
    ----------
    ticker:
        Ticker symbol label.
    data:
        Per-ticker payload dict with keys ``"ratios"``, ``"filings"``, and
        ``"insider"``.  ``"filings"`` must be a list of serialised Filing dicts;
        ``"insider"`` must be a ``Form4Bundle`` (or ``None``, in which case an
        empty bundle is substituted).

    Returns
    -------
    str
        A formatted text block identical to what ``_build_ticker_context``
        produces — suitable for direct inclusion in an LLM prompt.
    """
    filings_payload: list[dict] = data.get("filings") or []
    insider_bundle: Form4Bundle = data.get("insider") or Form4Bundle(trades=[], derivatives=[])

    # Read the lookback window from config so the window label in the block
    # always matches the actual fetch window used by the agent.
    insider_lookback_days: int = get_config().defaults.insider_lookback_days

    return _build_ticker_context(
        ticker,
        filings_payload,
        insider_bundle,
        insider_lookback_days=insider_lookback_days,
    )

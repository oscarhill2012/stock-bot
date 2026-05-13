"""Fundamental analyst data fetch callback.

Phase 5 introduces a triad of data domains for the Fundamental analyst:

- **stats** — company fundamentals (P/E, ROE, FCF, etc.) via the active stats provider.
- **filings** — recent SEC filings (10-K, 10-Q, 8-K) with MD&A / risk-factor excerpts.
- **insider** — Form 4 insider trades and derivative transactions as a ``Form4Bundle``.

Each domain is fetched independently.  A failure in one domain is logged and
falls back to a safe empty value so that the other two domains are still
available to the downstream extractor and LLM.

The resulting ``state["fundamental_data"]`` layout per ticker is::

    {
        "stats":   <dict from StockStats.model_dump() | None on failure>,
        "filings": [<Filing.model_dump()>, ...],
        "insider": <Form4Bundle instance>,
    }

In addition to ``state["fundamental_data"]``, the callback writes
``state["fundamental_context"]`` — a human-readable multi-ticker text block
that the Fundamental LLM instruction template references as the runtime
``{fundamental_context}`` placeholder.  This block contains:

- Filing excerpts (MD&A + risk factors) for each ticker.
- A structured insider activity block (numeric flows + footnote prose).

Separating the LLM-readable context from the machine-readable data dict
keeps the prompt compact and avoids serialising the entire ``Form4Bundle``
object graph into the instruction.
"""
from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_company_filings, get_insider_trades, get_stock_stats
from data.models import Form4Bundle, InsiderTrade

logger = logging.getLogger(__name__)

# Lookback window for Form 4 insider trades passed to the provider.
_INSIDER_LOOKBACK_DAYS = 30

# Maximum number of insider footnote snippets to include in the LLM prompt
# per ticker.  Footnotes can be verbose; cap them to control token usage.
_MAX_FOOTNOTES = 5

# Maximum character length per footnote excerpt.  Truncated beyond this.
_MAX_FOOTNOTE_CHARS = 200


def _build_ticker_context(
    ticker: str,
    filings_payload: list[dict],
    insider_bundle: Form4Bundle,
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
                    lines.append(f"  MD&A: {mda[:500]}")
                if risk_fac:
                    lines.append(f"  Risk factors: {risk_fac[:500]}")
    else:
        lines.append("-- COMPANY FILINGS (PROSE) --")
        lines.append("  (no filings available)")

    # --- Insider activity (structured numerics) ---
    lines.append("-- INSIDER ACTIVITY (30d, structured) --")

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
    lines.append("-- INSIDER FOOTNOTES (≤5, prose) --")
    footnotes: list[str] = []

    for trade in trades:
        note = (getattr(trade, "footnote", None) or "").strip()
        if note:
            footnotes.append(note[:_MAX_FOOTNOTE_CHARS])

    for deriv in derivatives:
        note = (getattr(deriv, "footnote", None) or "").strip()
        if note:
            footnotes.append(note[:_MAX_FOOTNOTE_CHARS])

    if footnotes:
        for i, note in enumerate(footnotes[:_MAX_FOOTNOTES]):
            lines.append(f"  [{i + 1}] {note}")
    else:
        lines.append("  (no footnotes)")

    return "\n".join(lines)


async def fundamental_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch stats, SEC filings, and insider trades for every watchlist ticker.

    Iterates ``state["tickers"]`` and, for each ticker, dispatches three
    independent provider calls.  Partial failures are tolerated — each domain
    catches its own exception, logs a warning, and falls back to an empty
    payload rather than aborting the entire ticker's fetch.

    Writes two state keys:

    - ``state["fundamental_data"]`` — machine-readable triad dict consumed by
      the feature extractor and the after-callback.
    - ``state["fundamental_context"]`` — human-readable multi-ticker text block
      that ADK's instruction template fills into the LLM prompt via the
      ``{fundamental_context}`` placeholder.

    Parameters
    ----------
    callback_context:
        ADK callback context.  ``callback_context.state["tickers"]`` must be a
        list of ticker strings.

    Returns
    -------
    google.genai.types.Content | None
        Always ``None`` — this callback never short-circuits the LLM call.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    fundamental_data: dict[str, dict] = {}
    context_blocks: list[str] = []

    for ticker in tickers:
        # --- stats ---
        try:
            stats_obj = await get_stock_stats(ticker)
            stats_payload = (
                stats_obj.model_dump() if hasattr(stats_obj, "model_dump") else stats_obj
            )
        except Exception as exc:
            logger.warning("stats fetch failed for %s: %s", ticker, exc)
            stats_payload = None

        # --- filings ---
        try:
            filings = await get_company_filings(ticker)
            filings_payload = [
                f.model_dump() if hasattr(f, "model_dump") else f for f in filings
            ]
        except Exception as exc:
            logger.warning("filings fetch failed for %s: %s", ticker, exc)
            filings_payload = []

        # --- insider trades (Form 4) ---
        try:
            insider_bundle = await get_insider_trades(
                ticker, lookback_days=_INSIDER_LOOKBACK_DAYS
            )
            # Store the raw Form4Bundle so the extractor can access typed fields
            # directly without re-parsing a dict.
            if not isinstance(insider_bundle, Form4Bundle):
                # Guard: if the provider returned something unexpected, wrap it.
                logger.warning(
                    "insider_trades for %s returned %s, expected Form4Bundle — using empty bundle",
                    ticker,
                    type(insider_bundle).__name__,
                )
                insider_bundle = Form4Bundle(trades=[], derivatives=[])
        except Exception as exc:
            logger.warning("insider_trades fetch failed for %s: %s", ticker, exc)
            insider_bundle = Form4Bundle(trades=[], derivatives=[])

        fundamental_data[ticker] = {
            "stats": stats_payload,
            "filings": filings_payload,
            "insider": insider_bundle,
        }

        # Build the LLM-readable context block for this ticker and accumulate.
        context_blocks.append(
            _build_ticker_context(ticker, filings_payload, insider_bundle)
        )

    state["fundamental_data"] = fundamental_data

    # Join all per-ticker blocks into one string for the {fundamental_context}
    # ADK instruction placeholder.
    state["fundamental_context"] = "\n\n".join(context_blocks) if context_blocks else "(no data)"

    return None

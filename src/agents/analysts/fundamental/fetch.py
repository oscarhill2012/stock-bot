"""Fundamental analyst fetch helpers.

Provides the per-ticker context-building helpers used by ``FundamentalFetchAgent``
(``agents.analysts.fundamental.fetch_agent``).

The per-ticker triad payload layout is::

    {
        "ratios":                    <dict from CompanyRatios.model_dump() | None on failure>,
        "filings":                   [<Filing.model_dump()>, ...],
        "baseline_filings":          [<Filing.model_dump()>, ...],  # prior-year periodic filings
        "insider_trades":            [<InsiderTrade.model_dump()>, ...],
        "insider_derivative_trades": [<InsiderDerivativeTrade.model_dump()>, ...],
    }

The context block written into state combines:

- Filing excerpts (MD&A + risk factors) for each ticker, with MD&A paragraphs
  de-boilerplated against the prior-year filing where a fiscal-period pair
  can be established (Phase 13).
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

import contextlib
import logging
from datetime import date, datetime

from pydantic import ValidationError

from agents.analysts.fundamental.deboilerplate import (
    MDA_DEBOILERPLATE_ALGO_VERSION,
    deboilerplate_mda,
)
from config.analysts import FundamentalCaps, get_analysts_config
from data.config import get_config
from data.models import Form4Bundle, InsiderTrade
from data.models.trades import InsiderDerivativeTrade

_logger = logging.getLogger(__name__)

# Minimum number of characters an MD&A excerpt must have before we attempt
# de-boilerplate diffing.  Stubs shorter than this (e.g. a single placeholder
# sentence) are rendered verbatim with a marker — no pairing attempted.
# Configurable via ``config/analysts.json`` → ``fundamental.mda_stub_char_threshold``.
_DEFAULT_MDA_STUB_THRESHOLD = 400

# Acceptable delta (in calendar days) between a current filing's period_of_report
# and the prior-year baseline.  A 10-Q with period "20240930" pairs with a
# baseline period between 335 and 395 days earlier — this range covers late
# fiscal-quarter filers and non-calendar fiscal years.
_PAIRING_WINDOW_DAYS_MIN = 335
_PAIRING_WINDOW_DAYS_MAX = 395


# ---------------------------------------------------------------------------
# Shared insider-reconstruction helper
# ---------------------------------------------------------------------------

def _bundle_from_flat_lists(
    raw_trades: list[dict],
    raw_derivatives: list[dict],
) -> Form4Bundle:
    """Rebuild a ``Form4Bundle`` from the Phase 7 flat-list insider dicts.

    Each row is re-validated into its typed model; a row that fails Pydantic
    validation is dropped (best-effort context reconstruction) rather than
    aborting the whole bundle.  Only ``ValidationError`` is suppressed —
    anything else (e.g. a non-dict row causing an ``AttributeError``) is a
    genuine bug and must surface loudly.

    This helper is imported by ``agents.analysts.report_cache`` so the
    suppression logic lives in exactly one place.

    Parameters
    ----------
    raw_trades:
        List of ``InsiderTrade.model_dump()`` dicts (may be empty).
    raw_derivatives:
        List of ``InsiderDerivativeTrade.model_dump()`` dicts (may be empty).

    Returns
    -------
    Form4Bundle
        Typed bundle; any invalid rows are silently omitted.
    """
    trades: list[InsiderTrade] = []
    for row in (raw_trades or []):
        with contextlib.suppress(ValidationError):
            trades.append(InsiderTrade.model_validate(row))

    derivatives: list[InsiderDerivativeTrade] = []
    for row in (raw_derivatives or []):
        with contextlib.suppress(ValidationError):
            derivatives.append(InsiderDerivativeTrade.model_validate(row))

    return Form4Bundle(trades=trades, derivatives=derivatives)


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


def _render_ratios_block(lines: list[str], ratios: dict) -> None:
    """Append a ``-- COMPANY RATIOS (SCALAR) --`` block to *lines* in-place.

    Iterates over the known ``CompanyRatios`` scalar fields and appends a
    labelled line for every non-null value.  Fields are grouped loosely:
    valuation multiples, then growth/profitability, then price/market
    reference data, then analyst consensus.

    Fraction fields (dividend yield, revenue growth, profit margin, ROE) are
    multiplied by 100 and formatted as percentages so the LLM receives a
    human-readable number rather than a decimal fraction.

    The section is omitted entirely when *ratios* is falsy — the caller
    already guards with ``if ratios``.

    Parameters
    ----------
    lines:
        Mutable list of output lines being assembled by ``_build_ticker_context``.
        Modified in-place; nothing is returned.
    ratios:
        ``CompanyRatios.model_dump()`` dict.  Unknown keys are silently
        ignored so future model extensions do not break existing renders.
    """
    # Fields that are stored as decimal fractions but should be shown as %.
    _PERCENT_FIELDS = {
        "dividend_yield",
        "revenue_growth_yoy",
        "profit_margin",
        "roe",
    }

    # Fields stored in raw USD that we display in millions for readability.
    _MILLIONS_FIELDS = {"free_cash_flow"}

    # Ordered list of (dict_key, human_label) pairs to render.
    # Grouped: valuation → growth/profitability → price reference → analyst.
    _FIELD_ORDER: list[tuple[str, str]] = [
        ("market_cap",                   "Market cap (B USD)"),
        ("trailing_pe",                  "Trailing P/E"),
        ("forward_pe",                   "Forward P/E"),
        ("peg",                          "PEG ratio"),
        ("beta",                         "Beta"),
        ("debt_to_equity",               "Debt/equity"),
        ("revenue_growth_yoy",           "Revenue growth YoY"),
        ("profit_margin",                "Profit margin"),
        ("roe",                          "Return on equity (ROE)"),
        ("free_cash_flow",               "Free cash flow TTM (M USD)"),
        ("dividend_yield",               "Dividend yield"),
        ("fifty_day_average",            "50-day avg price"),
        ("two_hundred_day_average",      "200-day avg price"),
        ("fifty_two_week_high",          "52-week high"),
        ("fifty_two_week_low",           "52-week low"),
        ("analyst_rating_avg",           "Analyst rating avg (1=Strong Buy, 5=Sell)"),
        ("number_of_analyst_opinions",   "Analyst opinion count"),
    ]

    rendered_rows: list[str] = []

    for key, label in _FIELD_ORDER:
        value = ratios.get(key)
        if value is None:
            continue

        if key == "market_cap":
            # Convert raw USD → billions for readability.
            formatted = f"{value / 1e9:.3f} B"
        elif key in _MILLIONS_FIELDS:
            formatted = f"{value / 1e6:.2f} M"
        elif key in _PERCENT_FIELDS:
            formatted = f"{value * 100:.2f}%"
        elif isinstance(value, int):
            formatted = str(value)
        else:
            formatted = f"{value:.2f}"

        rendered_rows.append(f"  {label}: {formatted}")

    if rendered_rows:
        lines.append("-- COMPANY RATIOS (SCALAR) --")
        lines.extend(rendered_rows)


def _render_mda(
    filing: dict,
    mda: str,
    baselines: list[dict],
    caps: FundamentalCaps,
    mda_stub_threshold: int,
) -> str:
    """Return the MD&A text to include in the LLM context for one filing.

    Attempts de-boilerplate diffing against the prior-year baseline filing when:
    - The current filing has a ``period_of_report`` field (Phase 13 cache).
    - A matching baseline filing can be found (same form type, ~365 days earlier).
    - Both the current and prior MD&A texts exceed ``mda_stub_threshold`` chars
      (stubs are too short to diff meaningfully).

    Falls back to full text (capped at ``max_filing_mda_chars``) with a
    descriptive marker in these cases:
    - No ``period_of_report`` on the current filing (pre-Phase 13 cache row).
    - No matching baseline found.
    - Either text is shorter than ``mda_stub_threshold``.
    - An unexpected exception inside the diff call.

    Parameters
    ----------
    filing:
        Current filing dict (``Filing.model_dump()`` shape).
    mda:
        Stripped MD&A text from the current filing.
    baselines:
        Prior-year baseline filing dicts from the provider call.
    caps:
        ``FundamentalCaps`` for this tick (read from config).
    mda_stub_threshold:
        Minimum character count for both current and prior MD&A before
        de-boilerplate diffing is attempted.

    Returns
    -------
    str
        Either the de-boilerplated (diffed) MD&A text or the full text with a
        fallback marker, capped at ``caps.max_filing_mda_chars``.
    """
    form_type = filing.get("form_type", "?")
    current_period = filing.get("period_of_report") or ""

    # --- Attempt pairing only when period_of_report is available ---
    if not current_period:
        _logger.debug(
            "_render_mda: no period_of_report for %s %s — rendering full text",
            form_type, filing.get("accession_no", "?"),
        )
        return "[no prior-year pair: period_of_report absent — full text]\n\n" + mda[:caps.max_filing_mda_chars]

    # --- Find prior-year baseline ---
    baseline = _find_prior_year_baseline(current_period, form_type, baselines)

    if baseline is None:
        _logger.debug(
            "_render_mda: no baseline found for %s period=%s — rendering full text",
            form_type, current_period,
        )
        return "[no prior-year pair: baseline not in cache — full text]\n\n" + mda[:caps.max_filing_mda_chars]

    prior_mda = (baseline.get("mda_excerpt") or "").strip()
    prior_period = baseline.get("period_of_report") or "prior year"

    # --- Stub guard: skip diffing if either text is too short ---
    if len(mda) < mda_stub_threshold or len(prior_mda) < mda_stub_threshold:
        _logger.debug(
            "_render_mda: stub text for %s (current=%d, prior=%d chars, threshold=%d) "
            "— rendering full text",
            form_type, len(mda), len(prior_mda), mda_stub_threshold,
        )
        return "[prior-year pair found but text too short to diff — full text]\n\n" + mda[:caps.max_filing_mda_chars]

    # --- De-boilerplate diff ---
    try:
        filtered_text, stats = deboilerplate_mda(
            current_text=mda,
            prior_text=prior_mda,
            algo_version=MDA_DEBOILERPLATE_ALGO_VERSION,
            prior_period_label=prior_period,
        )
        _logger.info(
            "_render_mda: %s pair=%s→%s dropped=%d/%d (%.1f%% retained) "
            "chars %d→%d",
            form_type,
            prior_period,
            current_period,
            stats["paragraphs_dropped"],
            stats["paragraphs_total"],
            stats["coverage_pct"],
            stats["chars_in"],
            stats["chars_out"],
        )
        return filtered_text[:caps.max_filing_mda_chars]

    except Exception as exc:
        _logger.warning(
            "_render_mda: deboilerplate_mda failed for %s %s: %s — rendering full text",
            form_type, current_period, exc,
        )
        return "[de-boilerplate error — full text]\n\n" + mda[:caps.max_filing_mda_chars]


# Known on-disk formats for ``period_of_report``.  The live EDGAR provider
# emits compact ``YYYYMMDD``; the golden cache that backs the backtest stores
# ISO ``YYYY-MM-DD``.  Both must parse so de-boilerplate pairing behaves
# identically live and in replay.  ISO is tried first because it is the shape
# the backtest (the overwhelming caller) actually serves.
_PERIOD_FORMATS = ("%Y-%m-%d", "%Y%m%d")


def _parse_period_of_report(period: str) -> date | None:
    """Parse a filing ``period_of_report`` into a date, tolerant of format.

    Accepts both the ISO ``YYYY-MM-DD`` shape stored by the golden cache and
    the compact ``YYYYMMDD`` shape emitted by the live EDGAR provider, so the
    same pairing logic works on both data paths.

    Parameters
    ----------
    period:
        The ``period_of_report`` string from a filing dict.

    Returns
    -------
    date | None
        The parsed calendar date, or ``None`` if *period* is empty or matches
        no known format.  Distinguishing "empty" from "malformed" is left to
        the caller — a non-empty value that parses nowhere is a contract
        violation worth logging, not a silent drop.
    """
    for fmt in _PERIOD_FORMATS:
        try:
            return datetime.strptime(period, fmt).date()
        except (ValueError, TypeError):
            continue

    return None


def _find_prior_year_baseline(
    current_period: str,
    current_form_type: str,
    baseline_filings: list[dict],
) -> dict | None:
    """Find the prior-year periodic filing that matches ``current_period``.

    Pairs a current filing's ``period_of_report`` (e.g. ``"2024-09-30"`` from
    the cache or ``"20240930"`` from live EDGAR) with a baseline filing of the
    same form type whose period falls 335–395 days earlier.  This window covers
    non-calendar fiscal years and late-filer extensions (Form 12b-25).

    Parameters
    ----------
    current_period:
        ``period_of_report`` string from the current filing.  Either the ISO
        ``YYYY-MM-DD`` (golden cache) or compact ``YYYYMMDD`` (live EDGAR)
        shape is accepted.
    current_form_type:
        Form type of the current filing (e.g. ``"10-K"`` or ``"10-Q"``).
    baseline_filings:
        List of ``Filing.model_dump()`` dicts from the prior-year provider call.

    Returns
    -------
    dict | None
        The best-matching baseline filing dict, or ``None`` if no match found.
    """
    current_date = _parse_period_of_report(current_period)

    if current_date is None:
        # A non-empty period that parses in no known format is a contract
        # violation — historically this was silently swallowed, which disabled
        # de-boilerplate for an entire run undetected.  Surface it loudly.
        if current_period:
            _logger.warning(
                "_find_prior_year_baseline: unparseable current period_of_report "
                "%r (form=%s) — no prior-year pairing attempted",
                current_period, current_form_type,
            )
        return None

    best: dict | None = None
    best_date: date | None = None

    for baseline in baseline_filings:
        # Only consider the same form type — a 10-K must pair with a 10-K.
        if baseline.get("form_type") != current_form_type:
            continue

        prior_period = baseline.get("period_of_report")
        if not prior_period:
            continue

        prior_date = _parse_period_of_report(str(prior_period))
        if prior_date is None:
            continue

        # Check that the prior period is between 335 and 395 days before
        # the current period — this is the "one year earlier" window.
        # Also take the most-recent match if multiple baselines qualify
        # (shouldn't happen, but be defensive).
        delta = (current_date - prior_date).days
        in_window = _PAIRING_WINDOW_DAYS_MIN <= delta <= _PAIRING_WINDOW_DAYS_MAX
        is_newer_match = best_date is None or prior_date > best_date

        if in_window and is_newer_match:
            best = baseline
            best_date = prior_date

    return best


def _build_ticker_context(
    ticker: str,
    filings_payload: list[dict],
    insider_bundle: Form4Bundle,
    insider_lookback_days: int,
    ratios: dict | None = None,
    baseline_filings_payload: list[dict] | None = None,
) -> str:
    """Build the LLM-readable context block for a single ticker.

    Combines a scalar ratios block (revenue growth, margins, valuation
    multiples, etc.), filing excerpts (MD&A + risk factors + 8-K body), and a
    structured insider activity section (numeric metrics + footnote prose) into
    one formatted text block.  This text is concatenated across all tickers and
    written to ``state["fundamental_context"]`` by the fetch callback.

    For periodic filings (10-K, 10-Q) with available MD&A text, this function
    attempts to de-boilerplate the MD&A by diffing it against the prior-year
    filing's MD&A from ``baseline_filings_payload`` (Phase 13).  If a prior-year
    baseline is found and both texts are long enough, unchanged paragraphs are
    filtered out so the LLM sees only what actually changed.

    Parameters
    ----------
    ticker:
        Ticker symbol label.
    filings_payload:
        List of ``Filing.model_dump()`` dicts for the ticker.
    insider_bundle:
        Typed ``Form4Bundle`` containing common-stock trades and derivatives.
    insider_lookback_days:
        Number of days the insider fetch window covers — used as the window
        label in the insider activity section header.
    ratios:
        ``CompanyRatios.model_dump()`` dict for the ticker, or ``None`` / an
        empty dict if unavailable.  Only non-null scalar fields are rendered.
    baseline_filings_payload:
        List of ``Filing.model_dump()`` dicts from the prior-year periodic
        *pool* provider call (range mode, reaching ~800 days back).  The pairing
        layer picks the same-period-prior-year filing out of this pool by
        ``period_of_report``.  Used for MD&A de-boilerplate pairing.  ``None``
        or an empty list disables pairing for all filings in this call.

    Returns
    -------
    str
        A formatted text block suitable for direct inclusion in an LLM prompt.
    """
    lines: list[str] = [f"=== {ticker} ==="]

    # Read all caps from config once per call.
    caps = _caps()

    # --- Company ratios (scalar fundamentals) ---
    # Render non-null CompanyRatios fields so the LLM can reason about
    # valuation, margins, and growth — these were previously fetched + cached
    # but never forwarded to the LLM context (silent data drop, audit fix).
    #
    # Fields rendered and their formatting:
    #   - market_cap: billions (3 dp) to keep the figure readable
    #   - trailing_pe, forward_pe, peg, beta, debt_to_equity: plain 2 dp
    #   - dividend_yield, revenue_growth_yoy, profit_margin, roe: percentage (2 dp)
    #   - free_cash_flow: millions (2 dp) — raw figure is in USD
    #   - fifty_day_average, two_hundred_day_average,
    #     fifty_two_week_high, fifty_two_week_low: price (2 dp)
    #   - analyst_rating_avg: plain 2 dp (scale: 1.0 = Strong Buy, 5.0 = Sell)
    #   - number_of_analyst_opinions: integer
    #   - long_name, sector: string — omitted; not decision-relevant scalars
    if ratios:
        _render_ratios_block(lines, ratios)

    # Read the stub threshold from config (falls back to module default if
    # the config object pre-dates the Phase 13 field addition).
    mda_stub_threshold: int = getattr(
        caps, "mda_stub_char_threshold", _DEFAULT_MDA_STUB_THRESHOLD,
    )

    # Normalise baseline list once — empty list is fine (disables pairing).
    baselines: list[dict] = baseline_filings_payload or []

    # --- Filing excerpts ---
    # For annual / quarterly filings (10-K, 10-Q) we render MD&A and risk
    # factors.  MD&A is de-boilerplated against the prior-year filing when a
    # fiscal-period pair can be established (Phase 13).
    # For event-driven filings (8-K) those sections are absent; instead we
    # render the body_excerpt which captures the catalyst, guidance update, or
    # earnings announcement (Phase 7 audit 2.7 addition).
    if filings_payload:
        lines.append("-- COMPANY FILINGS (PROSE) --")
        for filing in filings_payload:
            form_type = filing.get("form_type", "?")
            filed_at  = filing.get("filed_at", "?")

            mda       = (filing.get("mda_excerpt") or "").strip()
            risk_fac  = (filing.get("risk_factors_excerpt") or "").strip()
            body_expt = (filing.get("body_excerpt") or "").strip()

            if mda or risk_fac:
                # 10-K / 10-Q style: MD&A + risk factors sections available.
                lines.append(f"  [{form_type}, filed {filed_at}]")

                if mda:
                    # Attempt de-boilerplate pairing for periodic forms with
                    # sufficient MD&A text and a prior-year baseline.
                    mda_to_render = _render_mda(
                        filing=filing,
                        mda=mda,
                        baselines=baselines,
                        caps=caps,
                        mda_stub_threshold=mda_stub_threshold,
                    )
                    lines.append(f"  MD&A: {mda_to_render}")

                if risk_fac:
                    lines.append(f"  Risk factors: {risk_fac[:caps.max_filing_risk_chars]}")

            elif body_expt:
                # 8-K style: no MD&A/risk sections, but there is a body excerpt
                # capturing the event (earnings, guidance, material disclosure).
                lines.append(f"  [{form_type}, filed {filed_at}]")
                lines.append(f"  Body: {body_expt[:caps.max_filing_8k_body_chars]}")
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

    The per-ticker agent stores each ticker's payload in a flat dict::

        {
            "ratios":                    dict | None,
            "filings":                   [dict, ...],
            "baseline_filings":          [dict, ...],  # prior-year, Phase 13
            "insider_trades":            [dict, ...],
            "insider_derivative_trades": [dict, ...],
        }

    This adapter unpacks that dict, reconstructs a ``Form4Bundle`` from the
    two flat insider lists (Phase 7 unified emission shape), and forwards to
    ``_build_ticker_context`` with the correct positional arguments so the
    agent's call site stays tidy.

    Parameters
    ----------
    ticker:
        Ticker symbol label.
    data:
        Per-ticker payload dict.  ``"filings"`` must be a list of serialised
        Filing dicts; ``"baseline_filings"`` is an optional list of prior-year
        Filing dicts used for MD&A de-boilerplate (Phase 13).
        ``"insider_trades"`` and ``"insider_derivative_trades"`` must be lists
        of ``InsiderTrade.model_dump()`` / ``InsiderDerivativeTrade.model_dump()``
        dicts respectively.  All three default to empty on absence.

    Returns
    -------
    str
        A formatted text block identical to what ``_build_ticker_context``
        produces — suitable for direct inclusion in an LLM prompt.
    """
    filings_payload: list[dict] = data.get("filings") or []

    # Prior-year baseline filings — populated by the Phase 13 baseline fetch.
    # Empty list when absent (pre-Phase 13 callers); disables de-boilerplate.
    baseline_filings_payload: list[dict] = data.get("baseline_filings") or []

    # Reconstruct the typed Form4Bundle from the two flat lists emitted by
    # the producer (Phase 7 unified shape).  Invalid rows are silently dropped;
    # see _bundle_from_flat_lists for the suppression policy.
    insider_bundle: Form4Bundle = _bundle_from_flat_lists(
        raw_trades=data.get("insider_trades") or [],
        raw_derivatives=data.get("insider_derivative_trades") or [],
    )

    # Read the lookback window from config so the window label in the block
    # always matches the actual fetch window used by the agent.
    insider_lookback_days: int = get_config().defaults.insider_lookback_days

    # Forward the ratios dict — previously fetched and cached but silently
    # dropped before this call (audit fix: render dropped ratios into LLM ctx).
    ratios: dict | None = data.get("ratios") or None

    return _build_ticker_context(
        ticker,
        filings_payload,
        insider_bundle,
        insider_lookback_days=insider_lookback_days,
        ratios=ratios,
        baseline_filings_payload=baseline_filings_payload,
    )

"""Strategist prompt renderer — per-ticker block builder.

Takes a ``TickerEvidence`` object and produces the human- and LLM-readable
per-ticker block that the strategist sees in its system prompt. The rendered
format is documented in the Phase 5 analyst-surface-redesign spec (§ 3).

Design principles
-----------------
- **Feature-bullet registries** keep the "what the strategist sees" surface
  auditable and version-controlled. Each entry is a 4-tuple:
  ``(feature_key, label, formatter, optional_interpreter)``. Adding a new
  feature is a one-line change in the appropriate registry.
- **Separation of concerns.** This module is purely presentational — no
  business logic, no state mutation. It is called once per ticker, just before
  the strategist LLM prompt is assembled.
- **Graceful degradation.** A missing feature key silently produces
  ``"(no data)"``. A missing analyst slot is noted as ``(missing)``.

Formatters
----------
- ``_pct_signed(v)``        — multiplies by 100, appends %, prefixes sign.
                              Use for fractional features (e.g. pct_change_20d
                              stored as 0.123 meaning 12.3 %).
- ``_pct_unscaled_signed(v)`` — appends %, prefixes sign.
                              Use for features already stored in percentage
                              units (e.g. dist_from_high_52w_pct stored as -3.0
                              meaning 3 % below the high).
- ``_plain(v)``             — round to one decimal, no sign or unit suffix.
- ``_ratio(v)``             — one-decimal float followed by "x" (for ratios).
- ``_dollars_m(v)``         — signed million-dollar display (e.g. -$72.0M).
"""
from __future__ import annotations

from collections.abc import Callable

from contract.evidence import AnalystEvidence, AnalystReport
from contract.ticker_evidence import TickerEvidence

# ---------------------------------------------------------------------------
# Formatters — all accept a single float, return a str
# ---------------------------------------------------------------------------

def _pct_signed(v: float) -> str:
    """Format a fractional value as a signed percentage string.

    Multiplies by 100 before formatting — use for features stored as fractions
    (e.g. ``pct_change_20d = 0.123`` renders as ``+12.3%``).

    Parameters
    ----------
    v:
        Feature value as a fraction (e.g. 0.123 for 12.3 %).

    Returns
    -------
    str
        Signed percentage string, e.g. ``"+12.3%"`` or ``"-4.1%"``.
    """
    scaled = v * 100.0
    sign = "+" if scaled >= 0 else ""
    return f"{sign}{scaled:.1f}%"


def _pct_unscaled_signed(v: float) -> str:
    """Format an already-scaled percentage value with a sign prefix.

    Use for features that are already stored in percentage units (e.g.
    ``dist_from_high_52w_pct = -3.0`` renders as ``-3.0%``).

    Parameters
    ----------
    v:
        Feature value already in percentage units.

    Returns
    -------
    str
        Signed percentage string, e.g. ``"-3.0%"`` or ``"+84.2%"``.
    """
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _plain(v: float) -> str:
    """Format a float to one decimal place with no unit suffix.

    Parameters
    ----------
    v:
        Numeric feature value.

    Returns
    -------
    str
        One-decimal string, e.g. ``"76.0"`` or ``"2.07"``
        (rounded to one decimal for compactness).
    """
    return f"{v:.1f}"


def _ratio(v: float) -> str:
    """Format a ratio value as a one-decimal ``x``-suffixed string.

    Parameters
    ----------
    v:
        Ratio value (e.g. ``vol_ratio_20d = 1.10``).

    Returns
    -------
    str
        Formatted ratio, e.g. ``"1.1x"``.
    """
    return f"{v:.1f}x"


def _dollars_m(v: float) -> str:
    """Format a dollar value in millions, with sign and currency symbol.

    Parameters
    ----------
    v:
        Dollar value (e.g. ``-72_000_000.0``).

    Returns
    -------
    str
        Signed million-dollar string, e.g. ``"-$72.0M"`` or ``"+$5.3M"``.
    """
    millions = v / 1_000_000.0
    sign = "+" if millions >= 0 else ""
    return f"{sign}${millions:.1f}M"


# ---------------------------------------------------------------------------
# Interpreter helpers — return a short inline annotation, or "" for nothing
# ---------------------------------------------------------------------------

def _rsi_band(v: float) -> str:
    """Return an RSI band annotation for values outside the neutral zone.

    Parameters
    ----------
    v:
        RSI value.

    Returns
    -------
    str
        ``"(overbought)"`` above 70, ``"(oversold)"`` below 30, ``""`` otherwise.
    """
    if v > 70:
        return "(overbought)"
    if v < 30:
        return "(oversold)"
    return ""


def _position_band(v: float) -> str:
    """Return a proximity annotation for 52-week distance values.

    Parameters
    ----------
    v:
        Distance value already in percentage units (negative = below high,
        positive = above low).

    Returns
    -------
    str
        ``"(at high)"`` or ``"(at low)"`` when within 1 % of the extreme,
        otherwise ``""``.
    """
    if abs(v) <= 1.0:
        return "(at high)" if v <= 0 else "(at low)"
    return ""


def _cluster_sell_band(v: float) -> str:
    """Return a flag annotation when the cluster_sell flag is set.

    Parameters
    ----------
    v:
        ``insider_cluster_sell_flag`` feature value (1.0 = flag set).

    Returns
    -------
    str
        ``"(cluster sell)"`` if flag is set, otherwise ``""``.
    """
    return "(cluster sell)" if v >= 1.0 else ""


def _cluster_buy_band(v: float) -> str:
    """Return a flag annotation when the cluster_buy flag is set.

    Parameters
    ----------
    v:
        ``insider_cluster_buy_flag`` feature value (1.0 = flag set).

    Returns
    -------
    str
        ``"(cluster buy)"`` if flag is set, otherwise ``""``.
    """
    return "(cluster buy)" if v >= 1.0 else ""


def _planned_sale_band(v: float) -> str:
    """Return a neutralisation annotation for 10b5-1 planned-sale ratios.

    Rule 10b5-1 sales are pre-scheduled and disclosed in advance — they are a
    neutral signal, not a bearish one. The fundamental analyst's prompt is
    explicit about this, but the strategist downstream was reading the raw
    ``Planned sale ratio: 1.0`` number with no neutralisation hint and
    pattern-completing on it as bearish (Bug #16 in the 2025-09 baseline
    audit). Surfacing an inline annotation here gives the strategist the same
    cue the analyst prompt provides.

    Parameters
    ----------
    v:
        ``insider_planned_sale_ratio`` feature value — fraction in the range
        ``0.0`` to ``1.0`` representing the share of recent insider sales that
        were 10b5-1 planned.

    Returns
    -------
    str
        ``"(all 10b5-1 — neutral)"`` at or above 0.9,
        ``"(mostly 10b5-1 — neutral)"`` at or above 0.7,
        otherwise ``""`` (no annotation).
    """
    # Check the stricter "all" threshold first; the 0.9 boundary is inclusive
    # so an exact 0.9 ratio reads as "all", not "mostly".
    if v >= 0.9:
        return "(all 10b5-1 — neutral)"

    if v >= 0.7:
        return "(mostly 10b5-1 — neutral)"

    return ""


# ---------------------------------------------------------------------------
# Feature-bullet registries
# ---------------------------------------------------------------------------
#
# Each entry is a 4-tuple:
#   (feature_key, display_label, formatter, interpreter | None)
#
# The renderer calls ``formatter(value)`` to get the display string, then
# appends ``interpreter(value)`` (if not empty) as an inline annotation.
# A missing feature key renders as ``"(no data)"``.
#
# Ordering is intentional — most informative signals appear first.
# ---------------------------------------------------------------------------

_BulletEntry = tuple[str, str, Callable[[float], str], Callable[[float], str] | None]

TECHNICAL_BULLETS: list[_BulletEntry] = [
    # RSI — range 0-100; overbought > 70, oversold < 30.
    ("rsi_14",                 "RSI(14):",                 _plain,              _rsi_band),
    # 20-day and 5-day momentum — stored as fractions, rendered as %.
    ("pct_change_20d",         "20d momentum:",            _pct_signed,         None),
    ("pct_change_5d",          "5d momentum:",             _pct_signed,         None),
    # 52-week distances — already stored as scaled %, e.g. -3.0 = 3% below high.
    ("dist_from_high_52w_pct", "Distance from 52w high:",  _pct_unscaled_signed, _position_band),
    ("dist_from_low_52w_pct",  "Distance from 52w low:",   _pct_unscaled_signed, None),
    # Volume relative to 20-day average.
    ("vol_ratio_20d",          "Volume vs 20d avg:",       _ratio,              None),
    # ATR as % of close — volatility gauge.
    ("atr_pct_14",             "ATR%(14):",                _plain,              None),
]

FUNDAMENTAL_BULLETS: list[_BulletEntry] = [
    # Valuation ratios.
    ("pe_trailing",                    "P/E (trailing):",        _plain,        None),
    ("pe_forward",                     "P/E (forward):",         _plain,        None),
    ("peg",                            "PEG:",                   _plain,        None),
    # Growth and quality metrics.
    ("revenue_growth_yoy",             "Revenue growth YoY:",    _pct_signed,   None),
    ("profit_margin",                  "Profit margin:",         _pct_signed,   None),
    ("debt_to_equity",                 "Debt/equity:",           _plain,        None),
    ("fcf_yield_pct",                  "FCF yield:",             _plain,        None),
    ("roe",                            "RoE:",                   _pct_signed,   None),
    ("analyst_rating_avg",             "Analyst rating avg:",    _plain,        None),
    # Filing recency.
    ("days_since_last_filing",         "Days since filing:",     _plain,        None),
    ("n_filings_30d",                  "Filings last 30d:",      _plain,        None),
    # Insider activity — the single most actionable fundamental signal.
    ("insider_net_dollars_30d",        "Insider net 30d:",       _dollars_m,    None),
    ("insider_n_buys_30d",             "Insider buys 30d:",      _plain,        None),
    ("insider_n_sells_30d",            "Insider sells 30d:",     _plain,        None),
    ("insider_cluster_sell_flag",      "Cluster sell flag:",     _plain,        _cluster_sell_band),
    ("insider_cluster_buy_flag",       "Cluster buy flag:",      _plain,        _cluster_buy_band),
    ("insider_planned_sale_ratio",     "Planned sale ratio:",    _plain,        _planned_sale_band),
    ("insider_max_filer_role_rank",    "Top filer role rank:",   _plain,        None),
    # Derivative-security disclosures.
    ("insider_derivative_exercise_count", "Derivative exercises:", _plain,      None),
    ("insider_derivative_grant_count",    "Derivative grants:",    _plain,      None),
]

NEWS_BULLETS: list[_BulletEntry] = [
    # Article count is the primary volume signal.
    ("news_count_7d",             "Article count 7d:",       _plain,    None),
    # Sentiment breakdown.
    ("pct_news_positive_7d",      "% positive:",             _plain,    None),
    ("pct_news_negative_7d",      "% negative:",             _plain,    None),
    ("headline_polarity_mean_7d", "Mean polarity:",          _plain,    None),
    # Social volume (legacy / optional).
    ("social_volume_z",           "Social volume z:",        _plain,    None),
]

SMART_MONEY_BULLETS: list[_BulletEntry] = [
    ("n_politicians",              "Politicians tracked:",    _plain,    None),
    ("n_buys_30d",                 "Buys 30d:",              _plain,    None),
    ("n_sells_30d",                "Sells 30d:",             _plain,    None),
    ("net_flow_dollar",            "Net flow:",              _dollars_m, None),
    ("total_dollar_value_buys",    "Buy value:",             _dollars_m, None),
    ("total_dollar_value_sells",   "Sell value:",            _dollars_m, None),
]

SOCIAL_BULLETS: list[_BulletEntry] = [
    ("mention_count_total",            "Mentions (total):",       _plain,  None),
    ("mention_count_reddit",           "Mentions (Reddit):",      _plain,  None),
    ("mention_count_twitter",          "Mentions (Twitter):",     _plain,  None),
    ("aggregate_score",                "Aggregate score:",        _plain,  None),
    ("score_velocity_24h",             "Score velocity 24h:",     _plain,  None),
    ("platform_score_disagreement",    "Platform disagreement:",  _plain,  None),
]

# Map analyst name → its bullet registry.
_ANALYST_BULLETS: dict[str, list[_BulletEntry]] = {
    "technical":   TECHNICAL_BULLETS,
    "fundamental": FUNDAMENTAL_BULLETS,
    "news":        NEWS_BULLETS,
    "smart_money": SMART_MONEY_BULLETS,
    "social":      SOCIAL_BULLETS,
}

# Map analyst name → the tag-line label (for the rationale/closed-vocab line).
_TAG_LINE_LABEL: dict[str, str] = {
    "technical":   "Rationale tags",
    "fundamental": "Closed-vocab tags",
    "news":        "Closed-vocab tags",
    "smart_money": "Rationale tags",
    "social":      "Rationale tags",
}

# Canonical analyst display order for consistent output.
_ANALYST_ORDER = ("technical", "fundamental", "news", "smart_money", "social")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _render_features(
    features: dict[str, float],
    bullets: list[_BulletEntry],
) -> list[str]:
    """Render a list of labelled feature-bullet lines from a feature dict.

    For each entry in ``bullets``:

    - If the feature key is **absent** from ``features``, the bullet is skipped
      entirely (no line emitted). This indicates the extractor did not produce
      that feature for this tick.
    - If the key is **present** but its value is ``None``, the bullet renders
      as ``"(no data)"``. This covers extractors that report missing data by
      setting the key to ``None`` rather than dropping it.
    - Otherwise the formatter is called on the value and the result is indented
      as a normal bullet. Present values of ``0.0`` always format normally
      (e.g. ``+0.0%``) — they are never treated as absent.

    Parameters
    ----------
    features:
        Dict of feature key → float (or None) value from the analyst extractor.
    bullets:
        List of ``(key, label, formatter, interpreter)`` entries to render.

    Returns
    -------
    list[str]
        One indented string per bullet entry whose key is present in
        ``features``.
    """
    lines: list[str] = []

    for key, label, formatter, interpreter in bullets:
        if key not in features:
            # Key absent — extractor didn't emit it for this tick; skip bullet.
            continue

        value = features[key]

        # Present-but-None occurs when an extractor reports missing data without
        # dropping the key (e.g. a provider returned null for this field).
        if value is None:
            lines.append(f"  {label:<30} (no data)")
            continue

        display = formatter(value)

        # Append interpreter annotation when present and non-empty.
        if interpreter is not None:
            annotation = interpreter(value)
            if annotation:
                display = f"{display}   {annotation}"

        lines.append(f"  {label:<30} {display}")

    return lines


def _render_report(report: AnalystReport) -> list[str]:
    """Render an AnalystReport as indented prompt lines.

    Produces a ``"-> Report summary:"`` line containing the report summary,
    followed by a ``"-> Drivers:"`` block listing each driver with its
    direction, weight, and body.

    Parameters
    ----------
    report:
        The ``AnalystReport`` from an LLM analyst's verdict.

    Returns
    -------
    list[str]
        Prompt-ready lines to be appended to an analyst block.
    """
    lines: list[str] = []

    # Summary block — a brief paragraph of connective tissue.
    lines.append(f'  -> Report summary: "{report.summary}"')

    # Driver block — one bullet per driver with direction and weight.
    lines.append("  -> Drivers:")
    for driver in report.drivers:
        lines.append(
            f"       * {driver.name}  ({driver.direction}, w={driver.weight:.2f}):"
        )
        # Indent the body text under the driver header.
        lines.append(f"         {driver.body}")

    return lines


def _render_analyst(
    name: str,
    ev: AnalystEvidence | None,
) -> str:
    """Render one analyst slot as a multi-line block string.

    Covers three cases:
    - ``ev is None`` — slot entirely absent (not just no-data): marked as
      ``(missing)``.
    - ``ev.verdict.is_no_data`` — analyst had no data for this tick: compact
      one-liner.
    - Normal verdict — full header + feature bullets + tags + optional report.

    Parameters
    ----------
    name:
        Canonical analyst name (e.g. ``"technical"``).
    ev:
        The ``AnalystEvidence`` for this slot, or ``None`` if absent entirely.

    Returns
    -------
    str
        A multi-line string for one analyst slot.
    """
    # ── Slot absent ──────────────────────────────────────────────────────────
    if ev is None:
        return f"[{name.title().replace('_', '')}]  (missing)"

    # ── No-data verdict ───────────────────────────────────────────────────────
    if ev.verdict.is_no_data:
        return f"[{_analyst_display_name(name)}]  is_no_data: true"

    v = ev.verdict
    header = (
        f"[{_analyst_display_name(name)}]  "
        f"lean: {v.lean}  "
        f"magnitude: {v.magnitude:.2f}  "
        f"confidence: {v.confidence:.2f}"
    )

    lines: list[str] = [header]

    # ── Feature bullets ───────────────────────────────────────────────────────
    bullets = _ANALYST_BULLETS.get(name, [])
    if bullets and ev.features:
        lines.extend(_render_features(ev.features, bullets))
    elif bullets:
        # Extractor returned an empty feature dict — flag explicitly.
        lines.append("  (no features extracted)")

    # ── Key factors / rationale tags ─────────────────────────────────────────
    if v.key_factors:
        tag_label = _TAG_LINE_LABEL.get(name, "Tags")
        lines.append(f"  -> {tag_label}: {', '.join(v.key_factors)}")

    # ── Report (LLM analysts only) ────────────────────────────────────────────
    if v.report is not None:
        lines.extend(_render_report(v.report))

    return "\n".join(lines)


def _analyst_display_name(name: str) -> str:
    """Convert an internal analyst name to its display form for the prompt header.

    Maps snake_case analyst names to the header label the spec shows
    (e.g. ``"smart_money"`` → ``"SmartMoney"``).

    Parameters
    ----------
    name:
        Internal analyst name, e.g. ``"smart_money"``.

    Returns
    -------
    str
        Display name for use in the ``[Name]`` header, e.g. ``"SmartMoney"``.
    """
    _DISPLAY = {
        "technical":   "Technical",
        "fundamental": "Fundamental",
        "news":        "News",
        "smart_money": "SmartMoney",
        "social":      "Social",
    }
    return _DISPLAY.get(name, name.title())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_ticker_block(te: TickerEvidence) -> str:
    """Render one ``TickerEvidence`` as a complete per-ticker prompt block.

    The block follows the format documented in spec §3:

    .. code-block:: text

        === <TICKER> ===

        [Technical]  lean: bearish  magnitude: 0.49  confidence: 0.90
          RSI(14):                  76.0   (overbought)
          20d momentum:             +12.3%
          ...
          -> Rationale tags: trend_up_20d, rsi_overbought

        [Fundamental]  lean: bearish  magnitude: 0.60  confidence: 0.70
          ...
          -> Report summary: "..."
          -> Drivers:
               * ...

        [News]  lean: neutral  magnitude: 0.30  confidence: 0.70
          Article count 7d:          50.0
          -> Closed-vocab tags: catalyst:legal
          -> Report summary: "..."
          -> Drivers: ...

        [SmartMoney]  is_no_data: true
        [Social]      is_no_data: true

    Parameters
    ----------
    te:
        The ``TickerEvidence`` for one ticker on the current tick.

    Returns
    -------
    str
        A human- and LLM-readable multi-line string for this ticker.
    """
    parts: list[str] = []

    # ── Section header ────────────────────────────────────────────────────────
    parts.append(f"=== {te.ticker} ===")
    parts.append("")

    # ── Per-analyst blocks ────────────────────────────────────────────────────
    for analyst_name in _ANALYST_ORDER:
        ev = te.per_analyst.get(analyst_name)
        block = _render_analyst(analyst_name, ev)
        parts.append(block)
        parts.append("")

    # Remove trailing blank line for a clean join.
    while parts and parts[-1] == "":
        parts.pop()

    return "\n".join(parts)


def render_all_ticker_blocks(items: list[TickerEvidence]) -> str:
    """Render all ``TickerEvidence`` objects as a combined prompt section.

    Concatenates ``render_ticker_block`` outputs, separated by a blank line
    and a horizontal divider, so each ticker's block is visually distinct in
    the prompt.

    Parameters
    ----------
    items:
        List of ``TickerEvidence`` objects for the current tick, one per
        watchlist ticker.

    Returns
    -------
    str
        Combined prompt-ready string covering all tickers.
        Returns ``"(no evidence this tick)"`` when the list is empty.
    """
    if not items:
        return "(no evidence this tick)"

    divider = "\n" + "-" * 60 + "\n"
    return divider.join(render_ticker_block(te) for te in items)

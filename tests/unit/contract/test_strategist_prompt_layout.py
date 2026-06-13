"""Strategist prompt layout tests — contract/strategist_prompt.py.

Tier 1 (no LLM). Feeds a fully-populated ``TickerEvidence`` object through
``render_ticker_block`` and asserts that every structural element the spec
demands is present in the rendered string.

Layout under test (see spec §3):

    === <TICKER> ===

    [Technical]  lean: <lean>  magnitude: <mag>  confidence: <conf>
      <labelled feature bullets>
      -> Rationale tags: <key_factors>

    [Fundamental]  lean: ...
      <labelled feature bullets>
      -> Closed-vocab tags: <key_factors>
      -> Report summary: ...  # only when report is present
      -> Drivers: ...          # only when report is present

    [News]  lean: ...
      <article count bullet>
      -> Closed-vocab tags: <key_factors>
      -> Report summary: ...  # only when report is present
      -> Drivers: ...          # only when report is present

    [SmartMoney]  is_no_data: true
    [Social]      is_no_data: true
"""
from __future__ import annotations

from datetime import UTC, datetime

from contract.evidence import (
    AnalystEvidence,
    AnalystReport,
    AnalystVerdict,
    ReportDriver,
)
from contract.strategist_prompt import (
    _death_cross_band,
    _golden_cross_band,
    _planned_sale_band,
    render_ticker_block,
)
from contract.ticker_evidence import AggregateVerdict, TickerEvidence

# ---------------------------------------------------------------------------
# Fixtures — shared TickerEvidence builders
# ---------------------------------------------------------------------------

def _make_verdict(
    lean: str = "bullish",
    magnitude: float = 0.5,
    confidence: float = 0.7,
    rationale: str = "test rationale",
    key_factors: list[str] | None = None,
    is_no_data: bool = False,
    report: AnalystReport | None = None,
) -> AnalystVerdict:
    """Build an AnalystVerdict respecting the exactly-one-prose-surface invariant.

    Exactly one of ``report`` (LLM analysts) or ``rationale`` (deterministic
    extractors) may be set on a non-no-data verdict.  This helper enforces the
    three valid configurations:

    - ``is_no_data=True``       → no-data short-circuit; report forced to None,
                                   rationale may carry the no-data reason.
    - ``report is not None``    → LLM-style verdict; rationale forced to ``""``
                                   so the validator's exactly-one check passes.
    - otherwise                 → deterministic verdict; report stays None and
                                   rationale keeps its non-empty default.

    Parameters
    ----------
    lean:
        Directional call: ``"bullish"``, ``"bearish"``, or ``"neutral"``.
    magnitude, confidence:
        Numeric fields in ``[0, 1]``.
    rationale:
        Short rationale string.  Blanked automatically for LLM-style verdicts.
    key_factors:
        List of closed-vocab factor tags.
    is_no_data:
        When True the verdict represents an absent/empty analyst.
    report:
        Optional ``AnalystReport`` (LLM analysts only).

    Returns
    -------
    AnalystVerdict
        Fully-formed verdict object satisfying the exactly-one-prose-surface
        invariant.
    """
    if is_no_data:
        # No-data short-circuit — report is invalid here; rationale holds the reason.
        effective_report = None
        effective_rationale = rationale
    elif report is not None:
        # LLM-style: report is the prose surface; blank the rationale field.
        effective_report = report
        effective_rationale = ""
    else:
        # Deterministic extractor: rationale is the prose surface; no report.
        effective_report = None
        effective_rationale = rationale

    return AnalystVerdict(
        lean=lean,            # type: ignore[arg-type]
        magnitude=magnitude,
        confidence=confidence,
        rationale=effective_rationale,
        key_factors=key_factors or [],
        is_no_data=is_no_data,
        report=effective_report,
    )


def _make_evidence(
    analyst: str,
    lean: str = "bullish",
    features: dict[str, float] | None = None,
    key_factors: list[str] | None = None,
    is_no_data: bool = False,
    report: AnalystReport | None = None,
    ticker: str = "AAPL",
) -> AnalystEvidence:
    """Build an AnalystEvidence object for test fixtures.

    Parameters
    ----------
    analyst:
        One of the five canonical analyst names.
    lean:
        Directional call.
    features:
        Feature dict — defaults to an empty dict.
    key_factors:
        Closed-vocab factor tags surfaced on the verdict.
    is_no_data:
        When True the verdict is a no-data placeholder.
    report:
        Optional AnalystReport populated by LLM analysts.
    ticker:
        Stock ticker symbol.

    Returns
    -------
    AnalystEvidence
        Fully-formed evidence object.
    """
    return AnalystEvidence(
        ticker=ticker,
        analyst=analyst,  # type: ignore[arg-type]
        tick_id="tick_TEST",
        recorded_at=datetime(2026, 5, 14, 9, 45, tzinfo=UTC),
        features=features or {},
        verdict=_make_verdict(
            lean=lean,
            key_factors=key_factors,
            is_no_data=is_no_data,
            report=report,
        ),
    )


def _make_report(summary: str = "Test summary prose.") -> AnalystReport:
    """Build a minimal valid AnalystReport with two drivers.

    Parameters
    ----------
    summary:
        Report summary text.

    Returns
    -------
    AnalystReport
        Minimal valid report with two contrasting drivers.
    """
    return AnalystReport(
        summary=summary,
        drivers=[
            ReportDriver(
                name="Primary bull driver",
                direction="bull",
                weight=0.6,
                body="First driver body text explaining the bullish case.",
            ),
            ReportDriver(
                name="Secondary risk",
                direction="bear",
                weight=0.3,
                body="Second driver body text explaining the risk.",
            ),
        ],
    )


def _make_ticker_evidence(
    ticker: str = "AAPL",
    news_report: AnalystReport | None = None,
    news_no_data: bool = False,
) -> TickerEvidence:
    """Build a fully-populated TickerEvidence for AAPL.

    All five analyst slots are populated. Technical and Fundamental carry
    realistic feature dicts. SmartMoney and Social are marked as no-data.
    News optionally carries an ``AnalystReport``.

    Parameters
    ----------
    ticker:
        Stock ticker symbol.
    news_report:
        Optional ``AnalystReport`` to attach to the news analyst verdict.
        When ``None`` and ``news_no_data=False``, the news analyst is built as
        a deterministic-style (rationale-only) verdict — no report is injected.
    news_no_data:
        When ``True`` the news analyst is marked as no-data (report=None is
        then valid).  Used by tests that assert no Drivers block is rendered.

    Returns
    -------
    TickerEvidence
        Fully-formed evidence object covering all five analyst slots.
    """
    tech_features = {
        "rsi_14": 76.0,
        "pct_change_20d": 0.123,    # fraction: 0.123 = +12.3%
        "pct_change_5d": 0.041,     # fraction: 0.041 = +4.1%
        "dist_from_high_52w_pct": 0.0,    # already-scaled pct, 0 = at high
        "dist_from_low_52w_pct": 84.2,    # already-scaled pct, +84.2% above low
        "vol_ratio_20d": 1.10,
        "atr_pct_14": 2.07,
    }

    fund_features = {
        "pe_trailing": 36.2,
        "pe_forward": 31.3,
        "profit_margin": 0.0,       # 0.0 = no data
        "insider_net_dollars_30d": -72_000_000.0,
        "insider_n_buys_30d": 0.0,
        "insider_n_sells_30d": 4.0,
        "insider_cluster_sell_flag": 1.0,
        "insider_max_filer_role_rank": 4.0,
        "days_since_last_filing": 12.7,
    }

    news_features = {
        "news_count_7d": 50.0,
    }

    return TickerEvidence(
        ticker=ticker,
        tick_id="tick_TEST",
        recorded_at=datetime(2026, 5, 14, 9, 45, tzinfo=UTC),
        per_analyst={
            "technical": _make_evidence(
                "technical",
                lean="bearish",
                features=tech_features,
                key_factors=["trend_up_20d", "rsi_overbought", "near_52w_high"],
            ),
            "fundamental": _make_evidence(
                "fundamental",
                lean="bearish",
                features=fund_features,
                key_factors=["insider:discretionary_sale_dominant"],
            ),
            "news": _make_evidence(
                "news",
                lean="neutral",
                features=news_features,
                key_factors=["catalyst:legal", "direction:mixed"],
                is_no_data=news_no_data,
                report=news_report,
            ),
            "smart_money": _make_evidence(
                "smart_money",
                lean="neutral",
                features={"is_no_data": 1.0},
                is_no_data=True,
            ),
            "social": _make_evidence(
                "social",
                lean="neutral",
                features={"is_no_data": 1.0},
                is_no_data=True,
            ),
        },
        aggregate=AggregateVerdict(
            lean="bearish",
            magnitude=0.55,
            confidence=0.80,
            disagreement=0.10,
            summary="3 bearish / 2 no_data",
        ),
        weights={
            "technical": 1.0,
            "fundamental": 1.0,
            "news": 1.0,
            "smart_money": 1.0,
            "social": 1.0,
        },
    )


# ---------------------------------------------------------------------------
# Tests — structural presence
# ---------------------------------------------------------------------------

def test_header_contains_ticker():
    """The rendered block must open with the ticker symbol as a section header."""
    te = _make_ticker_evidence()
    out = render_ticker_block(te)
    assert "AAPL" in out


def test_technical_block_present():
    """The [Technical] header must appear in the rendered block."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "[Technical]" in out


def test_fundamental_block_present():
    """The [Fundamental] header must appear in the rendered block."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "[Fundamental]" in out


def test_news_block_present():
    """The [News] header must appear in the rendered block."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "[News]" in out


def test_smart_money_no_data():
    """SmartMoney marked no-data must render as a compact no-data line."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "[SmartMoney]" in out
    # The renderer always emits the literal "is_no_data: true" for no-data slots.
    assert "is_no_data: true" in out


def test_social_no_data():
    """Social marked no-data must render as a compact no-data line."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "[Social]" in out
    # The renderer always emits the literal "is_no_data: true" for no-data slots.
    assert "is_no_data: true" in out


# ---------------------------------------------------------------------------
# Tests — Technical features (pct_change_20d fraction → scaled %)
# ---------------------------------------------------------------------------

def test_technical_rsi_value_rendered():
    """RSI(14) value must appear in the rendered block."""
    out = render_ticker_block(_make_ticker_evidence())
    # The fixture has rsi_14=76.0, so "76" must be present.
    assert "76" in out


def test_technical_pct_change_20d_rendered_as_percentage():
    """pct_change_20d stored as fraction (0.123) must render as +12.3%.

    The extractor stores this as a fraction; the renderer must multiply by 100
    before formatting so the strategist sees the human-readable percentage.
    """
    out = render_ticker_block(_make_ticker_evidence())
    # 0.123 * 100 = 12.3 — either "12.3" or "+12.3" must be present.
    assert "12.3" in out


def test_technical_dist_from_high_rendered_as_unscaled_pct():
    """dist_from_high_52w_pct stored already scaled must not be multiplied again.

    The fixture value is 0.0 (at the high), so "0.0" must appear and the block
    must not show an erroneous "0.0%" multiplied to "0.0" (harmless) vs
    e.g. 84.2 × 100 = 8420 for dist_from_low (which would be wrong).
    """
    out = render_ticker_block(_make_ticker_evidence())
    # dist_from_low_52w_pct = 84.2 (already scaled) — must appear as ~84.2, not 8420.
    assert "84.2" in out
    assert "8420" not in out


def test_technical_key_factors_rendered():
    """Technical key_factors must appear in the rationale/tags line."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "rsi_overbought" in out or "trend_up_20d" in out or "near_52w_high" in out


# ---------------------------------------------------------------------------
# Tests — Fundamental features
# ---------------------------------------------------------------------------

def test_fundamental_pe_rendered():
    """P/E trailing and/or forward values must appear in the fundamental block."""
    out = render_ticker_block(_make_ticker_evidence())
    # pe_trailing=36.2
    assert "36.2" in out or "36" in out


def test_fundamental_insider_net_rendered():
    """Insider net 30d dollar value must appear in the fundamental block."""
    out = render_ticker_block(_make_ticker_evidence())
    # insider_net_dollars_30d = -72_000_000 — should show as -$72M or -72000000 or similar
    # At minimum the negative sign and magnitude must be present.
    assert "-" in out  # negative sign
    assert "72" in out  # magnitude (M or full)


def test_fundamental_key_factors_rendered():
    """Fundamental key_factors must appear in the closed-vocab tags line."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "insider:discretionary_sale_dominant" in out or "discretionary" in out


# ---------------------------------------------------------------------------
# Tests — News features
# ---------------------------------------------------------------------------

def test_news_article_count_rendered():
    """News article count (news_count_7d) must appear in the news block."""
    out = render_ticker_block(_make_ticker_evidence())
    # news_count_7d = 50
    assert "50" in out


def test_news_key_factors_rendered():
    """News key_factors must appear in the closed-vocab tags line."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "catalyst:legal" in out or "direction:mixed" in out


# ---------------------------------------------------------------------------
# Tests — AnalystReport integration (News with report)
# ---------------------------------------------------------------------------

def test_report_summary_rendered_when_present():
    """When a news verdict carries an AnalystReport, its summary must appear."""
    report = _make_report("Two converging negatives this tick — test summary.")
    te = _make_ticker_evidence(news_report=report)
    out = render_ticker_block(te)
    assert "Two converging negatives" in out


def test_report_driver_name_rendered_when_present():
    """Report driver names must appear in the Drivers block when a report is present."""
    report = _make_report()
    te = _make_ticker_evidence(news_report=report)
    out = render_ticker_block(te)
    assert "Primary bull driver" in out


def test_report_driver_direction_rendered():
    """Each driver's direction label must appear in the Drivers block."""
    report = _make_report()
    te = _make_ticker_evidence(news_report=report)
    out = render_ticker_block(te)
    # The two drivers are "bull" and "bear".
    assert "bull" in out or "bear" in out


def test_no_report_omits_drivers_block():
    """When the News analyst has no AnalystReport, no Drivers block appears in
    the News section of the rendered output.

    The "no report" case is represented by news_no_data=True (a no-data verdict
    naturally carries no report).  Other analysts (Technical, Fundamental) are
    deterministic-style with rationale only — no Drivers lines from them either;
    this test isolates the News section for clarity.
    """
    # Build a te where the news analyst is no-data (and therefore report-less).
    te = _make_ticker_evidence(news_report=None, news_no_data=True)
    out = render_ticker_block(te)

    # Isolate the [News] section by finding its start and the next section marker.
    news_start = out.find("[News]")
    assert news_start != -1, "[News] section header must be present"

    # The next section after News is [SmartMoney].
    next_section = out.find("[SmartMoney]", news_start)
    news_section = out[news_start:next_section] if next_section != -1 else out[news_start:]

    # No Drivers block must appear within the News section itself.
    assert "-> Drivers:" not in news_section


# ---------------------------------------------------------------------------
# Tests — edge cases
# ---------------------------------------------------------------------------

def test_no_data_analyst_renders_compactly():
    """A no-data analyst slot must produce a short line, not a full feature block.

    The no-data branch skips all feature bullets and just marks the slot.
    """
    te = _make_ticker_evidence()
    out = render_ticker_block(te)
    # SmartMoney is no_data — its block should be short (no bullet lines).
    # A rough proxy: the rendered line should not contain "RSI" or "P/E" under SmartMoney.
    sm_idx = out.find("[SmartMoney]")
    social_idx = out.find("[Social]")
    assert sm_idx != -1
    if social_idx != -1 and social_idx > sm_idx:
        sm_section = out[sm_idx:social_idx]
    else:
        sm_section = out[sm_idx:sm_idx + 200]
    # SmartMoney section should not contain technical or fundamental bullet labels.
    assert "RSI" not in sm_section
    assert "P/E" not in sm_section


def test_lean_and_confidence_in_header():
    """Each analyst header line must contain the lean and confidence values."""
    out = render_ticker_block(_make_ticker_evidence())
    # Technical is bearish with conf=0.7 (default in _make_evidence).
    assert "bearish" in out
    # Confidence value from the default fixture.
    assert "0.7" in out or "0.70" in out


# ---------------------------------------------------------------------------
# Tests — _planned_sale_band helper (Bug #16b)
# ---------------------------------------------------------------------------
#
# Annotates ``insider_planned_sale_ratio`` so the strategist sees an explicit
# neutralisation hint next to a 10b5-1-dominated raw number, instead of just
# the bare ratio (which the strategist had been mis-reading as bearish).
# ---------------------------------------------------------------------------

def test_planned_sale_band_all_threshold():
    """A ratio at or above 0.9 must annotate as the 'all 10b5-1' neutral band."""
    assert _planned_sale_band(0.95) == "(all 10b5-1 — neutral)"


def test_planned_sale_band_mostly_threshold():
    """A ratio in [0.7, 0.9) must annotate as the 'mostly 10b5-1' neutral band."""
    assert _planned_sale_band(0.75) == "(mostly 10b5-1 — neutral)"


def test_planned_sale_band_below_mostly_returns_empty():
    """A ratio below 0.7 must emit no annotation (empty string)."""
    assert _planned_sale_band(0.5) == ""


def test_planned_sale_band_zero_returns_empty():
    """A ratio of exactly zero must emit no annotation."""
    assert _planned_sale_band(0.0) == ""


def test_planned_sale_band_lower_bound_inclusive_at_0_9():
    """The 0.9 boundary itself must qualify for the 'all' band (>= comparison)."""
    # The audit fix specifies a >= threshold, so 0.9 exactly is in the 'all' band,
    # not the 'mostly' band.
    assert _planned_sale_band(0.9) == "(all 10b5-1 — neutral)"


def test_planned_sale_band_lower_bound_inclusive_at_0_7():
    """The 0.7 boundary itself must qualify for the 'mostly' band (>= comparison)."""
    assert _planned_sale_band(0.7) == "(mostly 10b5-1 — neutral)"


# ---------------------------------------------------------------------------
# Tests — _golden_cross_band / _death_cross_band helpers (Bug #13)
# ---------------------------------------------------------------------------
#
# Surface the trend-regime flags emitted by the technical extractor so the
# strategist can weigh medium-term context alongside the short-term RSI /
# momentum reads. The helpers annotate the inline bullet only when the
# corresponding flag is set — otherwise they collapse to an empty annotation
# and the bullet renders as a plain ``0.0`` (which the strategist can ignore).
# ---------------------------------------------------------------------------

def test_golden_cross_band_set():
    """A ``golden_cross`` value of 1.0 must annotate as ``(golden cross)``."""
    assert _golden_cross_band(1.0) == "(golden cross)"


def test_golden_cross_band_unset():
    """A ``golden_cross`` value of 0.0 must produce no annotation."""
    assert _golden_cross_band(0.0) == ""


def test_death_cross_band_set():
    """A ``death_cross`` value of 1.0 must annotate as ``(death cross)``."""
    assert _death_cross_band(1.0) == "(death cross)"


def test_death_cross_band_unset():
    """A ``death_cross`` value of 0.0 must produce no annotation."""
    assert _death_cross_band(0.0) == ""


def test_golden_cross_bullet_renders_annotation():
    """A technical feature dict with ``golden_cross=1.0`` must surface the annotation.

    Integration cover for Bug #13 Layer 2 — the strategist must see a regime
    annotation next to the raw flag value so it can fold the medium-term
    trend regime into its scoring.
    """
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["golden_cross"] = 1.0
    te.per_analyst["technical"].features["death_cross"] = 0.0

    out = render_ticker_block(te)

    assert "(golden cross)" in out


def test_death_cross_bullet_renders_annotation():
    """A technical feature dict with ``death_cross=1.0`` must surface the annotation."""
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["golden_cross"] = 0.0
    te.per_analyst["technical"].features["death_cross"] = 1.0

    out = render_ticker_block(te)

    assert "(death cross)" in out


def test_no_cross_bullet_omits_annotation():
    """When both flags are 0.0 neither cross annotation must appear."""
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["golden_cross"] = 0.0
    te.per_analyst["technical"].features["death_cross"] = 0.0

    out = render_ticker_block(te)

    assert "(golden cross)" not in out
    assert "(death cross)" not in out


# ---------------------------------------------------------------------------
# Tests — relative-strength + beta-damping bullets (Bug #15a)
# ---------------------------------------------------------------------------
#
# These feature keys already lived in the technical extractor's catalogue
# (``relative_strength_vs_spy_*``, ``relative_strength_vs_sector_*``,
# ``beta_confidence_damping``) but never surfaced to the strategist — the
# bullet registry simply hadn't been extended when the extractor learned
# them. Wiring them in gives the strategist the "is this ticker beating
# its index over the lookback?" read a discretionary manager would expect.
# ---------------------------------------------------------------------------

def test_relative_strength_spy_5d_bullet_rendered():
    """``relative_strength_vs_spy_5d`` must render as a signed percentage.

    The extractor stores the value as a fraction (e.g. 0.04 = ticker beat SPY
    by 4 points), so the renderer must multiply by 100 to produce ``+4.0%``.
    """
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["relative_strength_vs_spy_5d"] = 0.04

    out = render_ticker_block(te)

    assert "Rel str vs SPY 5d:" in out
    assert "+4.0%" in out


def test_relative_strength_spy_20d_bullet_rendered():
    """``relative_strength_vs_spy_20d`` must render with the SPY 20d label."""
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["relative_strength_vs_spy_20d"] = -0.07

    out = render_ticker_block(te)

    assert "Rel str vs SPY 20d:" in out
    assert "-7.0%" in out


def test_relative_strength_sector_5d_bullet_rendered():
    """``relative_strength_vs_sector_5d`` must render with the sector 5d label."""
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["relative_strength_vs_sector_5d"] = 0.025

    out = render_ticker_block(te)

    assert "Rel str vs sector 5d:" in out
    assert "+2.5%" in out


def test_relative_strength_sector_20d_bullet_rendered():
    """``relative_strength_vs_sector_20d`` must render with the sector 20d label."""
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["relative_strength_vs_sector_20d"] = 0.012

    out = render_ticker_block(te)

    assert "Rel str vs sector 20d:" in out
    assert "+1.2%" in out


def test_beta_confidence_damping_bullet_rendered():
    """``beta_confidence_damping`` must render via the plain one-decimal formatter."""
    te = _make_ticker_evidence()
    te.per_analyst["technical"].features["beta_confidence_damping"] = 0.83

    out = render_ticker_block(te)

    assert "Beta confidence damping:" in out
    # _plain renders to one decimal place.
    assert "0.8" in out


def test_relative_strength_keys_omitted_when_absent():
    """Bullets for absent relative-strength keys must be skipped, not stubbed.

    Mirrors the renderer's documented behaviour (line 395-397 of
    ``strategist_prompt.py``): keys not in the feature dict produce no
    line at all. ``_make_ticker_evidence`` does not populate the
    relative-strength keys by default, so the bullets must be absent
    entirely from the rendered output.
    """
    te = _make_ticker_evidence()
    # Belt-and-braces: ensure the keys are not present.
    for key in (
        "relative_strength_vs_spy_5d",
        "relative_strength_vs_spy_20d",
        "relative_strength_vs_sector_5d",
        "relative_strength_vs_sector_20d",
        "beta_confidence_damping",
    ):
        te.per_analyst["technical"].features.pop(key, None)

    out = render_ticker_block(te)

    assert "Rel str vs SPY 5d:" not in out
    assert "Rel str vs SPY 20d:" not in out
    assert "Rel str vs sector 5d:" not in out
    assert "Rel str vs sector 20d:" not in out
    assert "Beta confidence damping:" not in out


def test_planned_sale_ratio_bullet_renders_annotation():
    """An ``insider_planned_sale_ratio`` of 1.0 must show the 'all 10b5-1' annotation.

    Integration test: feeds a fundamental feature dict containing the
    ``insider_planned_sale_ratio`` key through the full ticker-block renderer
    and asserts that the strategist-facing string carries the neutralisation
    annotation.  This guards the wiring in FUNDAMENTAL_BULLETS (4th tuple
    element) so a future refactor cannot quietly drop the helper.
    """
    # Build a ticker-evidence object where the fundamental analyst's feature
    # dict explicitly carries insider_planned_sale_ratio = 1.0 — the CVX-style
    # scenario from Bug #16: a 100 %-10b5-1 sale that the strategist was
    # mis-weighing as bearish.
    te = _make_ticker_evidence()
    te.per_analyst["fundamental"].features["insider_planned_sale_ratio"] = 1.0

    out = render_ticker_block(te)

    # The literal annotation string — with em-dash (U+2014) — must appear.
    assert "(all 10b5-1 — neutral)" in out


# ---------------------------------------------------------------------------
# Tests — rationale fallback for deterministic analysts (Task 9)
# ---------------------------------------------------------------------------
#
# Deterministic analysts (technical, social, smart_money) no longer produce an
# AnalystReport.  The renderer must therefore surface their ``rationale`` string
# as a one-line prose fallback so the strategist is not left with a header-only
# block.  LLM analysts (news with a report) must still render via the report
# path; the rationale fallback must not fire for them.
# ---------------------------------------------------------------------------

def test_deterministic_analyst_block_renders_rationale_line() -> None:
    """A deterministic analyst (report=None) must surface its rationale as a
    one-line prose fallback, otherwise the strategist loses all per-analyst
    prose for technical/social/smart_money.
    """
    # Build a TickerEvidence whose technical analyst carries a distinctive
    # rationale string.  _make_ticker_evidence builds technical as a
    # deterministic verdict by default (no report), so we just need to ensure
    # the rationale string is recognisable and won't collide with other output.
    te = _make_ticker_evidence()
    te.per_analyst["technical"].verdict.rationale = (
        "distinctive-rationale-xyz: momentum divergence above upper band"
    )

    block = render_ticker_block(te)

    # The exact prose line the renderer must emit for a deterministic analyst.
    assert '-> Rationale: "distinctive-rationale-xyz: momentum divergence above upper band"' in block

    # Belt-and-braces: no synthetic Report summary line must have been invented.
    # Isolate the [Technical] section to avoid false-positives from other analysts.
    tech_start = block.find("[Technical]")
    fund_start = block.find("[Fundamental]")
    tech_section = block[tech_start:fund_start] if fund_start > tech_start else block[tech_start:]
    assert "-> Report summary:" not in tech_section


def test_llm_analyst_block_renders_report_summary() -> None:
    """An LLM analyst (report populated, rationale=='') renders the report
    summary and drivers as before — the rationale fallback must not fire.
    """
    te = _make_ticker_evidence(news_report=_make_report(summary="LLM prose here."))
    block = render_ticker_block(te)

    # The report summary must appear in the rendered block.
    assert "LLM prose here." in block

    # The rationale fallback line must not appear in the News section — the
    # news verdict was built with rationale="" (LLM path) so there is nothing
    # to fall back to.
    news_start = block.find("[News]")
    sm_start = block.find("[SmartMoney]", news_start)
    news_section = block[news_start:sm_start] if sm_start != -1 else block[news_start:]
    assert '-> Rationale:' not in news_section

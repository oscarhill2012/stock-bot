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
from contract.strategist_prompt import render_ticker_block
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
    """Build an AnalystVerdict for use in fixtures.

    Parameters
    ----------
    lean:
        Directional call: ``"bullish"``, ``"bearish"``, or ``"neutral"``.
    magnitude, confidence:
        Numeric fields in ``[0, 1]``.
    rationale:
        Short rationale string.
    key_factors:
        List of closed-vocab factor tags.
    is_no_data:
        When True the verdict represents an absent/empty analyst.
    report:
        Optional ``AnalystReport`` (LLM analysts only).

    Returns
    -------
    AnalystVerdict
        Fully-formed verdict object.
    """
    return AnalystVerdict(
        lean=lean,            # type: ignore[arg-type]
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=key_factors or [],
        is_no_data=is_no_data,
        report=report,
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
        feature_warnings=[],
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
    # The no-data branch should be short and visible (not an empty gap).
    assert "no_data" in out or "is_no_data" in out


def test_social_no_data():
    """Social marked no-data must render as a compact no-data line."""
    out = render_ticker_block(_make_ticker_evidence())
    assert "[Social]" in out
    assert "no_data" in out or "is_no_data" in out


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
    """When no AnalystReport is present, the Drivers block must not appear."""
    # Build a te with no news_report.
    te = _make_ticker_evidence(news_report=None)
    out = render_ticker_block(te)
    # The "-> Drivers:" line must be absent when there is no report.
    assert "-> Drivers:" not in out


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

"""Unit tests for the LLM context rendering helpers in ``fetch.py``.

Verifies that:

1. The ``-- COMPANY RATIOS (SCALAR) --`` block is rendered when a ratios dict
   is supplied — and that specific field values appear verbatim.
2. None / empty ratios input is handled gracefully (no block emitted).
3. Only non-null ratio fields are rendered (null fields are silently omitted).
4. Fraction fields (margins, yield, growth, ROE) are displayed as percentages.
5. ``body_excerpt`` is rendered for 8-K filings that have no MD&A/risk text.
6. ``body_excerpt`` is NOT rendered when MD&A text is already present (10-K
   behaviour — the existing sections take precedence).
7. ``body_excerpt`` respects the ``max_filing_8k_body_chars`` cap.
"""
from __future__ import annotations

from unittest.mock import patch

from agents.analysts.fundamental.fetch import (
    _build_ticker_context,
    _build_ticker_fundamental_context,
)
from config.analysts import FundamentalCaps, LlmCaps
from data.models import Form4Bundle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_bundle() -> Form4Bundle:
    """Return a Form4Bundle with no trades or derivatives."""
    return Form4Bundle(trades=[], derivatives=[])


def _minimal_caps() -> FundamentalCaps:
    """Return a FundamentalCaps with conservative but non-zero limits."""
    llm = LlmCaps(
        timeout_seconds=30,
        max_output_tokens=512,
        temperature=0.3,
        timeout_retries=1,
        schema_retries=1,
    )
    return FundamentalCaps(
        max_filing_mda_chars=500,
        max_filing_risk_chars=500,
        max_filing_8k_body_chars=200,
        max_insider_footnotes=2,
        max_insider_footnote_chars=100,
        llm=llm,
    )


def _make_ratios_dict() -> dict:
    """Return a populated CompanyRatios.model_dump() dict for AAPL."""
    return {
        "ticker":               "AAPL",
        "as_of":                "2026-01-15",
        "long_name":            "Apple Inc.",
        "sector":               "Technology",
        "market_cap":           3_100_000_000_000,   # 3.1 T USD
        "trailing_pe":          28.5,
        "forward_pe":           24.1,
        "peg":                  1.42,
        "beta":                 1.2,
        "dividend_yield":       0.0052,              # 0.52 %
        "revenue_growth_yoy":   0.071,               # 7.10 %
        "profit_margin":        0.262,               # 26.20 %
        "debt_to_equity":       1.45,
        "roe":                  0.175,               # 17.50 %
        "free_cash_flow":       107_000_000_000,     # 107,000 M USD
        "analyst_rating_avg":   1.8,
        "number_of_analyst_opinions": 42,
        "fifty_day_average":    185.30,
        "two_hundred_day_average": 175.10,
        "fifty_two_week_high":  199.62,
        "fifty_two_week_low":   164.08,
    }


# ---------------------------------------------------------------------------
# Tests — ratios block
# ---------------------------------------------------------------------------

class TestRatiosBlockRendering:
    """Ratios block appears in context and contains expected values."""

    def test_ratios_block_header_present(self):
        """The ``-- COMPANY RATIOS (SCALAR) --`` header appears when ratios supplied."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        assert "-- COMPANY RATIOS (SCALAR) --" in result

    def test_trailing_pe_in_ratios_block(self):
        """Trailing P/E value appears formatted to 2 decimal places."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        # 28.5 → "28.50"
        assert "28.50" in result

    def test_forward_pe_in_ratios_block(self):
        """Forward P/E value appears formatted to 2 decimal places."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        # 24.1 → "24.10"
        assert "24.10" in result

    def test_fraction_fields_rendered_as_percentages(self):
        """Fractional fields (margins, yield, growth, ROE) are shown as percentages."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        # 0.071 → "7.10%"
        assert "7.10%" in result, "Revenue growth YoY not formatted as percentage"
        # 0.262 → "26.20%"
        assert "26.20%" in result, "Profit margin not formatted as percentage"
        # 0.175 → "17.50%"
        assert "17.50%" in result, "ROE not formatted as percentage"
        # 0.0052 → "0.52%"
        assert "0.52%" in result, "Dividend yield not formatted as percentage"

    def test_market_cap_rendered_in_billions(self):
        """Market cap is expressed in billions (B USD) for readability."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        # 3_100_000_000_000 → "3100.000 B"
        assert "3100.000 B" in result, "Market cap not rendered in billions"

    def test_free_cash_flow_rendered_in_millions(self):
        """Free cash flow is expressed in millions (M USD) for readability."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        # 107_000_000_000 → "107000.00 M"
        assert "107000.00 M" in result, "Free cash flow not rendered in millions"

    def test_beta_in_ratios_block(self):
        """Beta is rendered (risk/volatility lens the prompt now relies on)."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        # 1.2 → "1.20", labelled "Beta".
        assert "Beta" in result
        assert "1.20" in result

    def test_sector_in_ratios_block(self):
        """Sector renders verbatim as a string so the model can judge the
        trailing multiple sector-relative (Phase 14 — sector now populated)."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        assert "Sector" in result
        assert "Technology" in result

    def test_analyst_opinion_count_rendered_as_integer(self):
        """Integer fields (analyst count) appear without decimal places."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=_make_ratios_dict(),
            )

        assert "42" in result, "Analyst opinion count missing from context"


class TestRatiosBlockGracefulDegradation:
    """Ratios block is omitted cleanly when ratios are absent or empty."""

    def test_none_ratios_produces_no_block(self):
        """Passing ``ratios=None`` must NOT produce a ratios section header."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
            )

        assert "-- COMPANY RATIOS (SCALAR) --" not in result

    def test_empty_dict_ratios_produces_no_block(self):
        """Passing ``ratios={}`` must NOT produce a ratios section header."""
        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios={},
            )

        assert "-- COMPANY RATIOS (SCALAR) --" not in result

    def test_all_null_ratio_fields_produces_no_block(self):
        """A ratios dict where all renderable fields are None emits no section."""
        all_null = {
            "ticker": "AAPL",
            "trailing_pe": None,
            "forward_pe": None,
            "peg": None,
            "beta": None,
            "revenue_growth_yoy": None,
            "profit_margin": None,
            "roe": None,
            "market_cap": None,
            "free_cash_flow": None,
            "dividend_yield": None,
            "debt_to_equity": None,
            "fifty_day_average": None,
            "two_hundred_day_average": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
            "analyst_rating_avg": None,
            "number_of_analyst_opinions": None,
        }

        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=all_null,
            )

        assert "-- COMPANY RATIOS (SCALAR) --" not in result

    def test_partial_nulls_renders_only_non_null_fields(self):
        """Only non-null ratio fields appear in the rendered block."""
        partial = {
            "ticker": "AAPL",
            "trailing_pe": 25.0,   # present
            "forward_pe": None,    # absent
            "beta": 1.1,           # present
        }

        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=partial,
            )

        assert "-- COMPANY RATIOS (SCALAR) --" in result
        assert "25.00" in result      # trailing_pe rendered
        assert "1.10" in result       # beta rendered
        assert "Forward P/E" not in result  # forward_pe omitted (null)


# ---------------------------------------------------------------------------
# Tests — 8-K body_excerpt rendering
# ---------------------------------------------------------------------------

class TestEightKBodyExcerpt:
    """body_excerpt is rendered for 8-K filings that lack MD&A/risk sections."""

    def test_8k_body_excerpt_rendered_when_no_mda_or_risk(self):
        """8-K filing with body_excerpt but no MD&A renders the Body: line."""
        eight_k = {
            "ticker": "AAPL",
            "form_type": "8-K",
            "filed_at": "2026-01-20",
            "mda_excerpt": None,
            "risk_factors_excerpt": None,
            "body_excerpt": "Apple reported record quarterly revenue of $130 billion.",
        }

        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[eight_k],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
            )

        assert "Body:" in result
        assert "record quarterly revenue" in result

    def test_8k_body_not_rendered_when_mda_present(self):
        """When MD&A is present, body_excerpt is NOT rendered (10-K behaviour)."""
        ten_k_with_body = {
            "ticker": "AAPL",
            "form_type": "10-K",
            "filed_at": "2026-01-20",
            "mda_excerpt": "Management discussion of annual performance.",
            "risk_factors_excerpt": None,
            "body_excerpt": "This body should not appear.",
        }

        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[ten_k_with_body],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
            )

        assert "This body should not appear." not in result
        assert "MD&A" in result  # MD&A section rendered instead

    def test_8k_body_excerpt_truncated_to_cap(self):
        """body_excerpt is truncated to ``max_filing_8k_body_chars`` (200 in test caps)."""
        long_body = "X" * 500  # well over the 200-char cap in _minimal_caps

        eight_k = {
            "ticker": "AAPL",
            "form_type": "8-K",
            "filed_at": "2026-01-20",
            "mda_excerpt": None,
            "risk_factors_excerpt": None,
            "body_excerpt": long_body,
        }

        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[eight_k],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
            )

        # The body line should contain at most 200 X's, not 500.
        body_line = next(
            (line for line in result.splitlines() if line.strip().startswith("Body:")),
            None,
        )
        assert body_line is not None, "Body: line not found in rendered context"
        assert body_line.count("X") == 200, (
            f"body_excerpt not truncated: found {body_line.count('X')} X chars, expected 200"
        )

    def test_8k_with_empty_body_and_no_mda_not_rendered(self):
        """An 8-K with an empty body_excerpt and no MD&A produces no filing entry."""
        eight_k = {
            "ticker": "AAPL",
            "form_type": "8-K",
            "filed_at": "2026-01-20",
            "mda_excerpt": "",
            "risk_factors_excerpt": "",
            "body_excerpt": "",
        }

        with patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[eight_k],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
            )

        # Filing header should not appear for a completely empty filing.
        assert "[8-K, filed" not in result


# ---------------------------------------------------------------------------
# Tests — adapter shim (_build_ticker_fundamental_context) passes ratios through
# ---------------------------------------------------------------------------

class TestAdapterShimForwardsRatios:
    """``_build_ticker_fundamental_context`` forwards ratios into the rendered block."""

    def test_adapter_renders_ratios_from_data_dict(self):
        """Ratios stored in the per-ticker ``data`` dict reach the rendered context."""
        data = {
            "ratios": {
                "ticker": "MSFT",
                "trailing_pe": 33.0,
                "profit_margin": 0.365,
            },
            "filings": [],
            "insider_trades": [],
            "insider_derivative_trades": [],
        }

        with (
            patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()),
            patch("agents.analysts.fundamental.fetch.get_config") as mock_cfg,
        ):
            mock_cfg.return_value.defaults.insider_lookback_days = 30
            result = _build_ticker_fundamental_context("MSFT", data)

        assert "-- COMPANY RATIOS (SCALAR) --" in result
        assert "33.00" in result         # trailing_pe
        assert "36.50%" in result        # profit_margin as %

    def test_adapter_none_ratios_produces_no_block(self):
        """``data["ratios"] = None`` must not produce a ratios section."""
        data = {
            "ratios": None,
            "filings": [],
            "insider_trades": [],
            "insider_derivative_trades": [],
        }

        with (
            patch("agents.analysts.fundamental.fetch._caps", return_value=_minimal_caps()),
            patch("agents.analysts.fundamental.fetch.get_config") as mock_cfg,
        ):
            mock_cfg.return_value.defaults.insider_lookback_days = 30
            result = _build_ticker_fundamental_context("MSFT", data)

        assert "-- COMPANY RATIOS (SCALAR) --" not in result


# ---------------------------------------------------------------------------
# Tests — MD&A de-boilerplate firing from a prior-year pool (Phase 13)
# ---------------------------------------------------------------------------

# Caps with a generous MD&A char budget so the de-boilerplate header and the
# surviving paragraph both fit (the class-default _minimal_caps clips at 500).
def _deboilerplate_caps() -> FundamentalCaps:
    """Return caps with a 12k MD&A budget and a 50-char stub threshold."""
    llm = LlmCaps(
        timeout_seconds=30,
        max_output_tokens=512,
        temperature=0.3,
        timeout_retries=1,
        schema_retries=1,
    )
    return FundamentalCaps(
        max_filing_mda_chars=12000,
        max_filing_risk_chars=12000,
        max_filing_8k_body_chars=200,
        max_insider_footnotes=2,
        max_insider_footnote_chars=100,
        mda_stub_char_threshold=50,
        llm=llm,
    )


# A boilerplate preamble repeated verbatim across both filings, plus a unique
# closing paragraph that differs year-over-year.  Each paragraph clears the
# 50-char stub threshold so diffing is actually attempted.
_BOILERPLATE_PARA = (
    "This discussion contains forward-looking statements within the meaning of "
    "the Private Securities Litigation Reform Act of 1995 and should be read "
    "alongside the audited consolidated financial statements."
)

_CURRENT_UNIQUE_PARA = (
    "Revenue rose 11 percent in the quarter driven by Services and a record "
    "March quarter for iPhone, with gross margin expanding to 46.6 percent."
)

_PRIOR_UNIQUE_PARA = (
    "Revenue declined 3 percent in the prior-year quarter as foreign exchange "
    "headwinds weighed on results, with gross margin of 44.3 percent."
)


class TestMdaDeboilerplateFiresFromPool:
    """An AAPL-shaped 10-Q de-boilerplates against the correct prior-year 10-Q."""

    def _current_filing(self) -> dict:
        """Return the current Q2 10-Q dict (period 20260328)."""
        return {
            "ticker": "AAPL",
            "form_type": "10-Q",
            "filed_at": "2026-05-01",
            "period_of_report": "20260328",
            "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _CURRENT_UNIQUE_PARA,
            "risk_factors_excerpt": None,
            "body_excerpt": None,
        }

    def _baseline_pool(self) -> list[dict]:
        """Return an AAPL-shaped prior-year pool with intervening quarters.

        Only the Q2-prior-year filing (period 20250329, ~364 days before the
        current period) sits inside the 335–395 pairing window.  The adjacent
        quarters (Q1 FY26 and Q3 FY25) are deliberately included to prove the
        pairing layer selects by fiscal period, not by recency.
        """
        return [
            {   # Q1 FY26 — only ~90 days before current period (out of window).
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2026-02-01",
                "period_of_report": "20251228",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\nUnrelated Q1 narrative.",
                "risk_factors_excerpt": None, "body_excerpt": None,
            },
            {   # Q3 FY25 — ~273 days before current period (out of window).
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2025-08-01",
                "period_of_report": "20250628",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\nUnrelated Q3 narrative.",
                "risk_factors_excerpt": None, "body_excerpt": None,
            },
            {   # Q2 FY25 — ~364 days before current period: the true baseline.
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2025-05-02",
                "period_of_report": "20250329",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _PRIOR_UNIQUE_PARA,
                "risk_factors_excerpt": None, "body_excerpt": None,
            },
        ]

    def test_deboilerplate_header_and_survivors(self):
        """The diff fires: boilerplate is dropped, the unique current para kept."""
        with patch(
            "agents.analysts.fundamental.fetch._caps",
            return_value=_deboilerplate_caps(),
        ):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[self._current_filing()],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

        # De-boilerplate header names the matched prior period (20250329).
        assert "[de-boilerplate vs 20250329:" in result

        # The shared boilerplate preamble was dropped...
        assert "forward-looking statements within the meaning" not in result
        # ...while the unique current-quarter narrative survived (a paragraph
        # is emitted contiguously, so the phrase appears verbatim).
        assert "record March quarter for iPhone" in result

        # No fallback marker — pairing succeeded.
        assert "no prior-year pair" not in result
        assert "too short to diff" not in result

    def test_adjacent_quarter_not_selected_as_baseline(self):
        """Pairing must NOT pick an out-of-window adjacent quarter's prose.

        If the pairing wrongly matched Q1 FY26 or Q3 FY25, the unrelated
        narrative from those filings would not de-boilerplate the current
        unique paragraph — but more tellingly, the header would name the wrong
        period.  Pin the period to guard against a recency-based mismatch.
        """
        with patch(
            "agents.analysts.fundamental.fetch._caps",
            return_value=_deboilerplate_caps(),
        ):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=[self._current_filing()],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

        assert "20251228" not in result, "adjacent Q1 wrongly chosen as baseline"
        assert "20250628" not in result, "adjacent Q3 wrongly chosen as baseline"

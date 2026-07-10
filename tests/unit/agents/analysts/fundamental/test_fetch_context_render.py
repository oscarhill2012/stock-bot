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


# ---------------------------------------------------------------------------
# Tests — Phase 14: risk-factor + litigation diffing, XOM stub fallback
# ---------------------------------------------------------------------------

# Year-over-year risk-factor prose: one boilerplate bullet shared verbatim,
# one genuinely new bullet in the current filing.  Each clears the 50-char
# stub threshold used by _deboilerplate_caps.
_RISK_BOILERPLATE = (
    "Our business is subject to intense competition across all markets in "
    "which we operate, which may adversely affect our results of operations."
)

_RISK_NEW_BULLET = (
    "We are subject to new export-control restrictions announced in March "
    "that materially limit shipments of our highest-margin products to Asia."
)

_LITIGATION_BOILERPLATE = (
    "The company is subject to various legal proceedings arising in the "
    "ordinary course of business, none of which is expected to be material."
)

_LITIGATION_NEW = (
    "In February the Department of Justice filed a civil antitrust complaint "
    "against the company seeking structural remedies in the services segment."
)


class TestRiskAndLitigationDiffing:
    """Risk factors and litigation de-boilerplate against the prior-year pair."""

    def _current_filing(self) -> dict:
        """Return a current 10-Q dict with all three prose sections."""
        return {
            "ticker": "AAPL",
            "form_type": "10-Q",
            "filed_at": "2026-05-01",
            "period_of_report": "20260328",
            "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _CURRENT_UNIQUE_PARA,
            "risk_factors_excerpt": _RISK_BOILERPLATE + "\n\n" + _RISK_NEW_BULLET,
            "litigation_excerpt": _LITIGATION_BOILERPLATE + "\n\n" + _LITIGATION_NEW,
            "body_excerpt": None,
        }

    def _baseline_pool(self) -> list[dict]:
        """Return the prior-year 10-Q carrying the shared boilerplate only."""
        return [
            {
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2025-05-02",
                "period_of_report": "20250329",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _PRIOR_UNIQUE_PARA,
                "risk_factors_excerpt": _RISK_BOILERPLATE,
                "litigation_excerpt": _LITIGATION_BOILERPLATE,
                "body_excerpt": None,
            },
        ]

    def _render(self) -> str:
        """Run the context builder with generous diff-friendly caps."""
        with patch(
            "agents.analysts.fundamental.fetch._caps",
            return_value=_deboilerplate_caps(),
        ):
            return _build_ticker_context(
                ticker="AAPL",
                filings_payload=[self._current_filing()],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

    def test_risk_factors_are_diffed_against_prior_year(self):
        """The shared risk bullet is stripped; the new bullet survived."""
        result = self._render()

        # Positive signal: the diff fired on the risk section (header names
        # the matched prior period) and the genuinely new bullet survived.
        assert "Risk factors:" in result
        assert "new export-control restrictions" in result

        # The verbatim boilerplate bullet was removed as unchanged.
        assert "intense competition across all markets" not in result

    def test_litigation_is_rendered_and_diffed(self):
        """The litigation line appears, boilerplate stripped, new matter kept."""
        result = self._render()

        assert "Litigation:" in result
        assert "civil antitrust complaint" in result
        assert "ordinary course of business" not in result

    def test_all_three_sections_name_the_matched_prior_period(self):
        """Every diffed section header names 20250329 — one pairing, three diffs."""
        result = self._render()

        # One de-boilerplate header per section (MD&A + risk + litigation).
        assert result.count("[de-boilerplate vs 20250329:") == 3


# An XOM-shaped 10-K MD&A: a cross-reference stub incorporating Exhibit 13 by
# reference.  ~115 chars — under the 200-char stub threshold below.
_XOM_MDA_STUB = (
    "Reference is made to the Financial Section of the 2025 Annual Report, "
    "Exhibit 13, incorporated herein by reference."
)


def _stub_guard_caps() -> FundamentalCaps:
    """Caps with a 200-char stub threshold: the XOM stub (~115 chars) is
    guarded while the ~380-char 10-Q prose fixtures still clear it."""
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
        max_filing_litigation_chars=12000,
        max_filing_8k_body_chars=200,
        max_insider_footnotes=2,
        max_insider_footnote_chars=100,
        mda_stub_char_threshold=200,
        llm=llm,
    )


class TestIncorporatedByReferenceStubFallback:
    """XOM-style 10-K stub: no diff attempted; the 10-Q pair still diffs."""

    def _payload(self) -> list[dict]:
        """Return a 10-K with a stub MD&A plus a 10-Q with real prose."""
        return [
            {   # The stub 10-K — MD&A incorporated by reference (Exhibit 13).
                "ticker": "XOM", "form_type": "10-K", "filed_at": "2026-02-25",
                "period_of_report": "20251231",
                "mda_excerpt": _XOM_MDA_STUB,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
            {   # The 10-Q — genuine prose that must still diff normally.
                "ticker": "XOM", "form_type": "10-Q", "filed_at": "2026-05-01",
                "period_of_report": "20260331",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _CURRENT_UNIQUE_PARA,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
        ]

    def _baseline_pool(self) -> list[dict]:
        """Prior-year pool: a stub 10-K (same shape) and a real-prose 10-Q."""
        return [
            {
                "ticker": "XOM", "form_type": "10-K", "filed_at": "2025-02-26",
                "period_of_report": "20241231",
                "mda_excerpt": _XOM_MDA_STUB,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
            {
                "ticker": "XOM", "form_type": "10-Q", "filed_at": "2025-05-02",
                "period_of_report": "20250331",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _PRIOR_UNIQUE_PARA,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
        ]

    def test_stub_10k_gets_marker_and_10q_still_diffs(self):
        """The stub is marked (no fake diff); the 10-Q carries the delta signal.

        This pins the established XOM fallback: an incorporated-by-reference
        MD&A must surface as a NO-COMPARISON marker — never as a de-boilerplate
        header that the prompt would read as 'nothing changed' (quiet-bullish).
        """
        with patch(
            "agents.analysts.fundamental.fetch._caps",
            return_value=_stub_guard_caps(),
        ):
            result = _build_ticker_context(
                ticker="XOM",
                filings_payload=self._payload(),
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

        # The stub 10-K: pairing succeeded but the stub guard blocked the diff.
        assert "too short to diff" in result
        # The stub text itself is still shown (the LLM sees WHY there is no diff).
        assert "incorporated herein by reference" in result

        # Positive signal: the 10-Q pair diffed normally in the same context.
        assert "[de-boilerplate vs 20250331:" in result
        assert "record March quarter for iPhone" in result


# ---------------------------------------------------------------------------
# Tests — C1 fix: zero-survivor case must render as documented near-verbatim
# marker (quiet-bullish), never as a full-text dump under a de-boilerplate
# header (which the prompt's volume heuristic reads as heavily bearish).
# ---------------------------------------------------------------------------

# A litigation section that is byte-identical year-over-year — every
# paragraph in the current filing matches a paragraph in the prior filing, so
# ``deboilerplate_mda`` hits its zero-survivor fallback (all paragraphs
# dropped).  Two paragraphs so the "N of N" count is unambiguous.
_LITIGATION_VERBATIM_PARA_1 = (
    "The company is subject to various legal proceedings arising in the "
    "ordinary course of business, none of which is expected to be material."
)

_LITIGATION_VERBATIM_PARA_2 = (
    "Management believes the ultimate resolution of these matters will not "
    "have a material adverse effect on the company's financial condition."
)

_LITIGATION_VERBATIM_TEXT = (
    _LITIGATION_VERBATIM_PARA_1 + "\n\n" + _LITIGATION_VERBATIM_PARA_2
)


class TestZeroSurvivorNearVerbatimMarker:
    """A byte-identical YoY litigation section renders as near-verbatim, not
    a full-text dump (Plan 1 review finding C1 — sign-inversion fix)."""

    def _payload(self) -> list[dict]:
        """Return a current 10-Q whose litigation text exactly matches its
        prior-year pair — every paragraph is dropped as unchanged."""
        return [
            {
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2026-05-01",
                "period_of_report": "20260328",
                "mda_excerpt": None,
                "risk_factors_excerpt": None,
                "litigation_excerpt": _LITIGATION_VERBATIM_TEXT,
                "body_excerpt": None,
            },
        ]

    def _baseline_pool(self) -> list[dict]:
        """Return the prior-year 10-Q carrying the identical litigation text."""
        return [
            {
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2025-05-02",
                "period_of_report": "20250329",
                "mda_excerpt": None,
                "risk_factors_excerpt": None,
                "litigation_excerpt": _LITIGATION_VERBATIM_TEXT,
                "body_excerpt": None,
            },
        ]

    def test_zero_survivor_renders_near_verbatim_marker_without_full_text(self):
        """Byte-identical filing renders the documented all-removed marker and
        omits the full section body — the quiet-bullish, not bearish, shape."""
        with patch(
            "agents.analysts.fundamental.fetch._caps",
            return_value=_deboilerplate_caps(),
        ):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=self._payload(),
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

        # Positive signal 1: the documented near-verbatim marker fires, naming
        # the matched prior period and the all-removed "2 of 2" count.
        assert "[de-boilerplate vs 20250329: 2 of 2 paragraphs removed as unchanged" in result

        # Positive signal 2: the full section body is ABSENT — this is the
        # sign-inversion regression guard.  Before the fix, both verbatim
        # paragraphs would appear in full under the header, which the
        # prompt's volume heuristic reads as a large bearish delta.
        assert _LITIGATION_VERBATIM_PARA_1 not in result
        assert _LITIGATION_VERBATIM_PARA_2 not in result

        # The undocumented deboilerplate.py fallback header must never reach
        # the prompt — the render layer intercepts it via the stats dict.
        assert "no unique paragraphs found" not in result


class TestDiffExceptionDegradesToSignalAbsent:
    """A diff crash must degrade to signal-absent (neutral), never bearish
    (Plan 1 review finding I1)."""

    def _payload(self) -> list[dict]:
        """Return a current 10-Q with a valid prior-year pair present."""
        return [
            {
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2026-05-01",
                "period_of_report": "20260328",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _CURRENT_UNIQUE_PARA,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
        ]

    def _baseline_pool(self) -> list[dict]:
        """Return the prior-year 10-Q so pairing succeeds (only the diff call
        itself is forced to raise)."""
        return [
            {
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2025-05-02",
                "period_of_report": "20250329",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _PRIOR_UNIQUE_PARA,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
        ]

    def test_diff_exception_renders_no_prior_year_pair_marker(self):
        """A forced ``deboilerplate_mda`` crash renders the no-comparison
        marker family, never the (bearish-reading) de-boilerplate-header shape."""
        with (
            patch(
                "agents.analysts.fundamental.fetch._caps",
                return_value=_deboilerplate_caps(),
            ),
            patch(
                "agents.analysts.fundamental.fetch.deboilerplate_mda",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = _build_ticker_context(
                ticker="AAPL",
                filings_payload=self._payload(),
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

        # Extract the rendered MD&A line so we can pin its exact start —
        # the surrounding context (ratios block, insider section) is noise.
        mda_line = next(
            (line for line in result.splitlines() if line.strip().startswith("MD&A:")),
            None,
        )
        assert mda_line is not None, "MD&A: line not found in rendered context"

        rendered_section = mda_line.split("MD&A: ", 1)[1]

        # Positive signal: starts with the documented no-comparison marker...
        assert rendered_section.startswith("[no prior-year pair:")
        # ...and NEVER with the de-boilerplate-header shape (bearish reading).
        assert not rendered_section.startswith("[de-boilerplate")

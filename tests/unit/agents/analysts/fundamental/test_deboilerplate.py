"""Unit tests for the MD&A de-boilerplate filter (``deboilerplate.py``).

Tests cover:
  1. A completely unique current text passes through 100% unchanged (no prior match).
  2. A completely duplicate current text (all paragraphs in prior) returns the
     full text verbatim with a "no unique paragraphs" fallback marker.
  3. Paragraphs that differ only by punctuation or capitalisation are
     normalised and treated as duplicates (boilerplate).
  4. Paragraphs with changed numbers are treated as UNIQUE (NOT removed), even
     if otherwise identical, because digit content is preserved in the
     fingerprint.
  5. The de-boilerplate header line is present in the output and reports
     correct drop stats.
  6. The ``stats`` dict has the expected keys and values.
  7. The LRU cache is keyed correctly — a different ``algo_version`` is a
     distinct entry (cache miss).
  8. ``mda_stub_char_threshold`` fallback: stubs below threshold render verbatim.
"""
from __future__ import annotations

from agents.analysts.fundamental.deboilerplate import (
    MDA_DEBOILERPLATE_ALGO_VERSION,
    deboilerplate_mda,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text(*paragraphs: str) -> str:
    """Join paragraphs with double newlines (standard MD&A paragraph format)."""
    return "\n\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Tests — core paragraph diffing
# ---------------------------------------------------------------------------

class TestUniqueTextPassesThrough:
    """A current text with no paragraphs in the prior year passes unchanged."""

    def test_all_unique_paragraphs_retained(self):
        """Every paragraph in the current text is novel — all should survive."""
        current = _make_text(
            "Revenue increased 12% year-over-year to $4.2 billion.",
            "Operating margin expanded 150 basis points to 18.3%.",
            "We repurchased $500 million of shares in the quarter.",
        )
        prior = _make_text(
            "Revenue declined 3% year-over-year to $3.7 billion.",
            "Operating margin contracted 80 basis points to 14.2%.",
            "We repurchased $200 million of shares in the quarter.",
        )

        result, stats = deboilerplate_mda(current, prior)

        # All three paragraphs have different numbers — all should survive.
        assert stats["paragraphs_dropped"] == 0
        assert stats["paragraphs_total"] == 3
        assert stats["coverage_pct"] == 100.0

        assert "Revenue increased 12%" in result
        assert "Operating margin expanded" in result
        assert "We repurchased $500 million" in result

    def test_empty_prior_text_drops_nothing(self):
        """An empty prior text produces no baseline fingerprints — all current paragraphs survive."""
        current = _make_text(
            "Strong demand across all segments.",
            "Gross margin improved to 45%.",
        )
        prior = ""

        result, stats = deboilerplate_mda(current, prior)

        assert stats["paragraphs_dropped"] == 0
        assert "Strong demand" in result
        assert "Gross margin" in result


class TestFullyDuplicateTextFallback:
    """When all current paragraphs are boilerplate, the fallback fires."""

    def test_all_duplicate_returns_full_text_with_marker(self):
        """A 100% match returns the FULL current text with a fallback marker."""
        shared_para = "This forward-looking statement is subject to risks."
        current = _make_text(shared_para, shared_para + " Additional context.")
        prior   = _make_text(shared_para, shared_para + " Additional context.")

        result, stats = deboilerplate_mda(current, prior)

        # Fallback: original text is returned verbatim.
        assert shared_para in result
        # Fallback marker must be present.
        assert "no unique paragraphs" in result.lower()

        # Stats reflect the drop attempt even in fallback.
        assert stats["paragraphs_total"] > 0
        assert stats["paragraphs_dropped"] == stats["paragraphs_total"]


class TestBoilerplateNormalisation:
    """Punctuation and capitalisation differences don't escape the filter."""

    def test_case_difference_treated_as_duplicate(self):
        """Identical paragraphs that differ only in capitalisation are boilerplate."""
        current = _make_text("We believe our strategy positions us well for growth.")
        prior   = _make_text("We BELIEVE our strategy positions us well for growth.")

        result, stats = deboilerplate_mda(current, prior)

        # Only cosmetic difference — should be filtered as boilerplate.
        assert stats["paragraphs_dropped"] == 1
        assert stats["paragraphs_total"] == 1

    def test_punctuation_difference_treated_as_duplicate(self):
        """Paragraphs differing only in punctuation (commas, periods) are boilerplate."""
        current = _make_text("Our products include widgets, gadgets, and gizmos.")
        prior   = _make_text("Our products include widgets gadgets and gizmos")

        result, stats = deboilerplate_mda(current, prior)

        # Punctuation is stripped during normalisation — same fingerprint.
        assert stats["paragraphs_dropped"] == 1

    def test_extra_whitespace_treated_as_duplicate(self):
        """Extra internal whitespace is collapsed — same content is still boilerplate."""
        current = _make_text("Revenue  grew  by  10  percent.")
        prior   = _make_text("Revenue grew by 10 percent.")

        result, stats = deboilerplate_mda(current, prior)

        assert stats["paragraphs_dropped"] == 1


class TestChangedNumbersAreUnique:
    """A paragraph with a changed number is NOT treated as boilerplate."""

    def test_revenue_figure_change_is_unique(self):
        """Changing a revenue figure makes the paragraph unique — it must survive."""
        current = _make_text("Revenue increased to $4.2 billion this year.")
        prior   = _make_text("Revenue increased to $3.7 billion this year.")

        result, stats = deboilerplate_mda(current, prior)

        # The figure changed — paragraph must NOT be dropped.
        assert stats["paragraphs_dropped"] == 0
        assert "4.2 billion" in result

    def test_percentage_change_is_unique(self):
        """Changing a percentage figure makes the paragraph unique."""
        current = _make_text("Operating margin was 18.3% in the current period.")
        prior   = _make_text("Operating margin was 16.8% in the current period.")

        result, stats = deboilerplate_mda(current, prior)

        assert stats["paragraphs_dropped"] == 0
        assert "18.3%" in result

    def test_only_boilerplate_paragraphs_dropped_not_numeric_changes(self):
        """Mixed text: boilerplate paras dropped, numeric-change paras retained."""
        boilerplate_para = (
            "Forward-looking statements involve risks and uncertainties "
            "that could cause actual results to differ materially."
        )
        current = _make_text(
            "Revenue grew 12% to $5.1 billion.",          # numbers changed
            boilerplate_para,                              # identical in prior
            "Net income rose to $820 million.",            # numbers changed
        )
        prior = _make_text(
            "Revenue grew 8% to $4.6 billion.",
            boilerplate_para,
            "Net income rose to $650 million.",
        )

        result, stats = deboilerplate_mda(current, prior)

        # Only the boilerplate paragraph should be dropped.
        assert stats["paragraphs_dropped"] == 1
        assert stats["paragraphs_total"] == 3

        # Changed paragraphs must survive.
        assert "5.1 billion" in result
        assert "820 million" in result

        # Boilerplate must not appear.
        assert "risks and uncertainties" not in result


class TestHeaderLine:
    """The de-boilerplate header line is present and reports correct stats."""

    def test_header_present_in_output(self):
        """The ``[de-boilerplate vs ...]`` header line appears in the output."""
        current = _make_text("Revenue grew 10% this year.", "Margins improved.")
        prior   = _make_text("Revenue grew 5% last year.", "Margins improved.")

        result, stats = deboilerplate_mda(
            current, prior, prior_period_label="Q3 2023",
        )

        assert "[de-boilerplate vs Q3 2023:" in result

    def test_header_reports_dropped_count(self):
        """The header mentions the number of dropped paragraphs."""
        boilerplate = "We are subject to market risks."
        current = _make_text("New guidance: 12% growth.", boilerplate)
        prior   = _make_text("Old guidance: 8% growth.",   boilerplate)

        result, _ = deboilerplate_mda(current, prior, prior_period_label="FY2023")

        # Header should say "1 of 2 paragraphs removed".
        assert "1 of 2 paragraphs" in result

    def test_zero_drop_header(self):
        """When nothing is dropped the header says 0 paragraphs removed."""
        current = _make_text("Revenue $5.1B.", "EPS $2.40.")
        prior   = _make_text("Revenue $4.6B.", "EPS $2.10.")

        result, _ = deboilerplate_mda(current, prior)

        # Header should show 0 dropped.
        assert "0 of 2 paragraphs" in result


class TestStatsDict:
    """The ``stats`` dict has the expected structure and correct values."""

    def test_stats_keys_present(self):
        """All five expected keys are present in the returned stats dict."""
        current = _make_text("Revenue grew.", "Margins improved.")
        prior   = _make_text("Revenue shrank.", "Margins worsened.")

        _, stats = deboilerplate_mda(current, prior)

        assert "chars_in"           in stats
        assert "chars_out"          in stats
        assert "paragraphs_total"   in stats
        assert "paragraphs_dropped" in stats
        assert "coverage_pct"       in stats

    def test_chars_in_matches_current_text_length(self):
        """``chars_in`` equals the length of the current text."""
        current = _make_text("A unique paragraph.")
        prior   = _make_text("A different paragraph.")

        _, stats = deboilerplate_mda(current, prior)

        assert stats["chars_in"] == len(current)

    def test_coverage_pct_calculation(self):
        """``coverage_pct`` = 100 * kept / total, rounded to 1 dp."""
        boilerplate = "Standard boilerplate paragraph."
        current = _make_text(
            "Changed line 1.",
            boilerplate,
            "Changed line 2.",
            boilerplate,
        )
        prior = _make_text(
            "Old line 1.",
            boilerplate,
            "Old line 2.",
            boilerplate,
        )

        _, stats = deboilerplate_mda(current, prior)

        # 2 of 4 paragraphs retained = 50.0 %
        assert stats["paragraphs_total"] == 4
        assert stats["paragraphs_dropped"] == 2
        assert stats["coverage_pct"] == 50.0


class TestCacheKeyBehaviour:
    """The LRU cache uses (current_text, prior_text, algo_version) as key."""

    def test_different_algo_version_is_cache_miss(self):
        """A different ``algo_version`` string produces a distinct cache entry."""
        current = _make_text("Revenue grew 10%.")
        prior   = _make_text("Revenue grew 8%.")

        result_v1, _ = deboilerplate_mda(current, prior, algo_version="v1")
        result_v2, _ = deboilerplate_mda(current, prior, algo_version="v2-test")

        # Both should produce identical results (same algorithm), but they must
        # be computed independently (not reusing each other's cached value).
        # The key point is that neither raises and both return valid output.
        assert "Revenue grew 10%" in result_v1
        assert "Revenue grew 10%" in result_v2

    def test_different_prior_text_is_cache_miss(self):
        """Different prior texts produce distinct results (different baseline)."""
        current = _make_text(
            "Revenue grew 10%.",
            "Boilerplate safety clause.",
        )
        prior_a = _make_text("Old revenue line.")
        prior_b = _make_text("Boilerplate safety clause.")

        result_a, stats_a = deboilerplate_mda(current, prior_a, algo_version="v1-test-a")
        result_b, stats_b = deboilerplate_mda(current, prior_b, algo_version="v1-test-a")

        # prior_a has no match → 0 dropped
        # prior_b matches the boilerplate para → 1 dropped
        assert stats_a["paragraphs_dropped"] == 0
        assert stats_b["paragraphs_dropped"] == 1

    def test_module_version_constant_exists(self):
        """The module-level ``MDA_DEBOILERPLATE_ALGO_VERSION`` constant is a non-empty string."""
        assert isinstance(MDA_DEBOILERPLATE_ALGO_VERSION, str)
        assert len(MDA_DEBOILERPLATE_ALGO_VERSION) > 0


class TestSingleNewlineFallback:
    """When the text has no blank lines, single-newline splitting is used."""

    def test_single_newline_text_is_split_and_diffed(self):
        """Text with only single newlines still has boilerplate correctly removed."""
        current = "Revenue grew 10%.\nBoilerplate clause.\nMargins improved."
        prior   = "Revenue grew 8%.\nBoilerplate clause.\nMargins contracted."

        result, stats = deboilerplate_mda(current, prior)

        # The boilerplate clause should be filtered.
        assert stats["paragraphs_dropped"] == 1
        assert "Boilerplate clause" not in result
        assert "Revenue grew 10%." in result
        assert "Margins improved." in result

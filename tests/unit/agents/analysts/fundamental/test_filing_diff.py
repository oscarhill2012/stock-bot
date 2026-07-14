"""Tests for filing_diff — similarity-threshold dedup + numeric-delta surfacing."""
from __future__ import annotations

from agents.analysts.fundamental.filing_diff import (
    FILING_DIFF_ALGO_VERSION,
    filing_diff,
)
from agents.analysts.fundamental.filing_similarity import (
    _cosine_vectors,
    _vectorise,
    compute_similarity,
)

_DEDUP = 0.92
_NUM_PCT = 0.10


def _run(current: str, prior: str):
    return filing_diff(
        current, prior,
        dedup_cosine=_DEDUP,
        numeric_delta_pct=_NUM_PCT,
        algo_version=FILING_DIFF_ALGO_VERSION,
        prior_period_label="FY2023",
    )


def test_number_only_rollforward_dedups_to_near_verbatim() -> None:
    """A filing that only rolls numbers forward must read as near-verbatim.

    This is the quiet-bullish leg Plan 1 could never reach.  Every paragraph is
    a numeric roll-forward of the prior year, so all should dedup and the
    near-verbatim marker must fire with NO body prose.
    """
    prior = (
        "Revenue was 12.1 billion, up 4% year over year.\n\n"
        "Operating margin improved to 21.0% from 19.5%."
    )
    current = (
        "Revenue was 13.4 billion, up 9% year over year.\n\n"
        "Operating margin improved to 23.0% from 21.0%."
    )
    text, stats = _run(current, prior)
    assert "near-verbatim" in text
    assert stats["paragraphs_dropped"] == stats["paragraphs_total"]


def test_numeric_delta_is_surfaced_even_when_paragraph_deduped() -> None:
    """A large figure change inside a deduped paragraph must be surfaced.

    Number normalisation hides it from the similarity view; the numeric-delta
    detector must bring it back so the LLM can weigh it.
    """
    prior   = "Total contractual obligations were 1.0 billion at year end."
    current = "Total contractual obligations were 3.0 billion at year end."
    text, stats = _run(current, prior)
    assert stats["numeric_deltas"], "a >=10% figure change must be recorded"
    assert "NUMERIC DELTAS" in text


def test_genuine_rewrite_survives_the_diff() -> None:
    """Substantively new prose must NOT be deduped — it is the bearish signal."""
    prior   = "We expect continued strong demand across our core markets."
    current = "A newly disclosed regulatory investigation may materially harm results."
    text, stats = _run(current, prior)
    assert stats["paragraphs_dropped"] == 0
    assert "regulatory investigation" in text


def test_near_verbatim_render_is_byte_exact() -> None:
    """Pins the exact rendered near-verbatim + numeric-deltas string.

    Captured from the pre-Task-11 (O(M×N) re-tokenising) implementation so the
    Task 11 pre-vectorise fix can be checked against it verbatim — a diff here
    is a failed task, not an acceptable trade.
    """
    prior = (
        "Revenue was 12.1 billion, up 4% year over year.\n\n"
        "Operating margin improved to 21.0% from 19.5%."
    )
    current = (
        "Revenue was 13.4 billion, up 9% year over year.\n\n"
        "Operating margin improved to 23.0% from 21.0%."
    )
    text, stats = _run(current, prior)
    assert text == (
        "[filing-diff vs FY2023: 2 of 2 paragraphs removed as unchanged — "
        "filing is near-verbatim]\n\n"
        "NUMERIC DELTAS (figures changed inside unchanged prose):\n"
        "  - 12.1 -> 13.4 (+10.7%)\n"
        "  - 4 -> 9 (+125.0%)"
    )
    assert stats == {
        "paragraphs_total":   2,
        "paragraphs_dropped": 2,
        "coverage_pct":       0.0,
        "numeric_deltas":     ["12.1 -> 13.4 (+10.7%)", "4 -> 9 (+125.0%)"],
        "chars_in":           95,
        "chars_out":          194,
    }


def test_numeric_delta_render_is_byte_exact() -> None:
    """Pins the exact rendered numeric-delta string for a single-paragraph diff.

    Captured from the pre-Task-11 implementation for the same reason as
    ``test_near_verbatim_render_is_byte_exact`` above.
    """
    prior   = "Total contractual obligations were 1.0 billion at year end."
    current = "Total contractual obligations were 3.0 billion at year end."
    text, stats = _run(current, prior)
    assert text == (
        "[filing-diff vs FY2023: 1 of 1 paragraphs removed as unchanged — "
        "filing is near-verbatim]\n\n"
        "NUMERIC DELTAS (figures changed inside unchanged prose):\n"
        "  - 1 -> 3 (+200.0%)"
    )
    assert stats == {
        "paragraphs_total":   1,
        "paragraphs_dropped": 1,
        "coverage_pct":       0.0,
        "numeric_deltas":     ["1 -> 3 (+200.0%)"],
        "chars_in":           59,
        "chars_out":          168,
    }


def test_single_newline_document_is_chunked_via_fallback() -> None:
    """A document with only single-newline breaks must still split into paragraphs.

    ``_split_paragraphs`` falls back to single-newline splitting when the text
    has no blank-line boundaries (common in some EDGAR extracts).  Using
    genuinely different, non-numeric prose per line so nothing dedups, this pins
    that the fallback chunks the document into multiple paragraphs rather than
    treating the whole thing as one blob.
    """
    prior = (
        "We expect continued strong demand across our core markets.\n"
        "Our supply chain remained resilient throughout the period."
    )
    current = (
        "A newly disclosed regulatory investigation may materially harm results.\n"
        "An unforeseen factory closure disrupted deliveries to key customers."
    )
    text, stats = _run(current, prior)

    # The fallback must have produced more than one paragraph from the
    # single-newline document — not collapsed it into a single blob.
    assert stats["paragraphs_total"] > 1

    # None of the substantively-new prose dedups, so both survivors render.
    assert stats["paragraphs_dropped"] == 0
    assert "regulatory investigation" in text
    assert "factory closure" in text


# --- Pre-vectorise equivalence (Task 11: O(M×N) re-tokenisation perf fix) ---
#
# The fix caches each side's (Counter, squared-norm) once and scores the inner
# loop from those cached vectors via ``_cosine_vectors`` instead of re-tokenising
# both paragraphs on every one of the M×N comparisons via ``compute_similarity``.
# These tests assert the two paths agree EXACTLY — same winning cosine value AND
# same winning prior-paragraph identity — for every current paragraph in a pool,
# for several representative pools including one with a token-less paragraph.


def _reference_best_matches(current_pool: list[str], prior_pool: list[str]):
    """The reference O(M×N) best-match search via ``compute_similarity``.

    Mirrors the tie-break in ``filing_diff`` exactly: strict ``>`` so the FIRST
    prior paragraph achieving the maximum wins over a later equal one.

    Returns
    -------
    list[tuple[float, int, str]]
        Per current paragraph: ``(best_cosine, best_prior_index, best_prior_text)``.
    """
    results = []
    for para in current_pool:
        best_cos, best_idx, best_prior = 0.0, -1, ""
        for idx, prior_para in enumerate(prior_pool):
            cos = compute_similarity(para, prior_para).cosine
            if cos > best_cos:
                best_cos, best_idx, best_prior = cos, idx, prior_para
        results.append((best_cos, best_idx, best_prior))
    return results


def _vectorised_best_matches(current_pool: list[str], prior_pool: list[str]):
    """The pre-vectorised best-match search via ``_vectorise`` + ``_cosine_vectors``.

    This is the algorithm the Task 11 fix installs inside ``filing_diff`` —
    exercised directly here (rather than only through the public ``filing_diff``
    entrypoint) so the equivalence property is pinned at the primitive level.
    """
    current_vectors = [_vectorise(p) for p in current_pool]
    prior_vectors   = [_vectorise(p) for p in prior_pool]

    results = []
    for (a_counts, sq_a) in current_vectors:
        best_cos, best_idx, best_prior = 0.0, -1, ""
        for idx, (b_counts, sq_b) in enumerate(prior_vectors):
            cos = _cosine_vectors(a_counts, sq_a, b_counts, sq_b)
            if cos > best_cos:
                best_cos, best_idx, best_prior = cos, idx, prior_pool[idx]
        results.append((best_cos, best_idx, best_prior))
    return results


def test_prevectorised_matching_equals_reference_for_near_duplicate_pool() -> None:
    """A pool with several near-duplicate paragraphs (dedup-boundary territory)."""
    prior_pool = [
        "Revenue was 12.1 billion, up 4% year over year.",
        "Operating margin improved to 21.0% from 19.5%.",
        "We expect continued strong demand across our core markets.",
        "Total contractual obligations were 1.0 billion at year end.",
    ]
    current_pool = [
        "Revenue was 13.4 billion, up 9% year over year.",
        "Operating margin improved to 23.0% from 21.0%.",
        "Revenue was 13.9 billion, up 11% year over year.",
        "Total contractual obligations were 3.0 billion at year end.",
    ]
    reference  = _reference_best_matches(current_pool, prior_pool)
    vectorised = _vectorised_best_matches(current_pool, prior_pool)
    assert vectorised == reference


def test_prevectorised_matching_equals_reference_for_pool_with_one_clear_survivor() -> None:
    """A pool where exactly one current paragraph is a genuine rewrite."""
    prior_pool = [
        "We expect continued strong demand across our core markets.",
        "Our supply chain remained resilient throughout the period.",
        "Revenue was 12.1 billion, up 4% year over year.",
    ]
    current_pool = [
        "We expect continued strong demand across our core markets.",
        "Our supply chain remained resilient throughout the period.",
        "A newly disclosed regulatory investigation may materially harm results.",
    ]
    reference  = _reference_best_matches(current_pool, prior_pool)
    vectorised = _vectorised_best_matches(current_pool, prior_pool)
    assert vectorised == reference


def test_prevectorised_matching_equals_reference_with_token_less_paragraph() -> None:
    """A pool containing a paragraph that tokenises to NO tokens (e.g. bullets/dashes).

    Exercises the empty-vector guard shared by ``_cosine`` and ``_cosine_vectors``
    at the pre-vectorise call site — the token-less paragraph's cached vector must
    still compare as 0.0 against every prior paragraph, never NaN or a crash.
    """
    prior_pool = [
        "Revenue was 12.1 billion, up 4% year over year.",
        "•••",
        "We expect continued strong demand across our core markets.",
    ]
    current_pool = [
        "— —",
        "Revenue was 13.4 billion, up 9% year over year.",
        "A newly disclosed regulatory investigation may materially harm results.",
    ]
    reference  = _reference_best_matches(current_pool, prior_pool)
    vectorised = _vectorised_best_matches(current_pool, prior_pool)
    assert vectorised == reference

    # The token-less current paragraph must score 0.0 against everything, and
    # (per the strict tie-break) resolve to "no match" — best_idx stays -1
    # because 0.0 is never STRICTLY greater than the running best_cos of 0.0.
    assert reference[0] == (0.0, -1, "")

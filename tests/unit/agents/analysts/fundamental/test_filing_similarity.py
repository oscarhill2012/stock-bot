"""Tests for the lexical filing-similarity primitive."""
from __future__ import annotations

from collections import Counter

from agents.analysts.fundamental.filing_similarity import (
    SimilarityScores,
    _cosine,
    _cosine_vectors,
    _vectorise,
    compute_similarity,
)


def test_identical_text_scores_one() -> None:
    """Identical prose is a perfect match on both measures."""
    text = "The company grew revenue and expanded margins in all regions."
    scores = compute_similarity(text, text)
    assert scores.cosine == 1.0
    assert scores.jaccard == 1.0


def test_number_only_rollforward_is_near_verbatim() -> None:
    """A pure figure roll-forward must read as near-identical.

    This is the exact case the old digit-preserving hash treated as fully
    changed — the defect behind the degenerate all-bearish run.  Under number
    normalisation the two paragraphs are the same language.
    """
    prior   = "Revenue was 12.1 billion, up 4% year over year."
    current = "Revenue was 13.4 billion, up 9% year over year."
    scores = compute_similarity(current, prior)
    assert scores.cosine > 0.95


def test_substantial_rewrite_scores_low() -> None:
    """Genuinely different prose scores low on cosine."""
    prior   = "We expect continued strong demand across our core markets."
    current = "A newly disclosed regulatory investigation may materially harm results."
    scores = compute_similarity(current, prior)
    assert scores.cosine < 0.4


def test_empty_versus_nonempty_is_zero_not_nan() -> None:
    """One-sided emptiness is a clean 0.0, never NaN."""
    scores = compute_similarity("", "some real content here")
    assert scores.cosine == 0.0
    assert scores.jaccard == 0.0


# --- Shared-primitive congruence (Task 11: pre-vectorise perf fix) ---
#
# ``compute_similarity`` must remain numerically unchanged after ``_cosine`` is
# refactored to delegate to ``_cosine_vectors``.  These tests pin that both the
# public API and the new internal primitives agree exactly with the pre-refactor
# ``_cosine`` behaviour, so a borderline dedup decision downstream in
# ``filing_diff`` can never flip.


def test_compute_similarity_identical_texts_still_exactly_one() -> None:
    """Identical prose must still score cosine exactly 1.0 post-refactor.

    This is the case the module's own comment calls out as fragile: a naive
    ``sqrt(a) * sqrt(b)`` denominator can land one ulp below 1.0.  Pinning the
    exact value (not ``> 0.99``) guards the single-sqrt formula surviving the
    refactor into ``_cosine_vectors``.
    """
    text = "The company grew revenue and expanded margins in all regions."
    assert compute_similarity(text, text).cosine == 1.0


def test_compute_similarity_disjoint_texts_score_zero() -> None:
    """Texts sharing no tokens must score exactly 0.0 (no shared vocabulary)."""
    scores = compute_similarity("alpha beta gamma", "delta epsilon zeta")
    assert scores.cosine == 0.0


def test_compute_similarity_one_empty_side_scores_zero() -> None:
    """The empty-vector guard must still fire through the delegated path."""
    assert compute_similarity("", "some real content here").cosine == 0.0
    assert compute_similarity("some real content here", "").cosine == 0.0


def test_compute_similarity_known_mixed_pair_matches_expected_value() -> None:
    """A hand-computed mixed pair pins the exact cosine value post-refactor.

    Tokens (after number normalisation and punctuation stripping):
        a = ["the", "cat", "sat"]              -> counts all 1, sq_a = 3
        b = ["the", "cat", "sat", "the", "mat"] -> counts: the=2, cat=1, sat=1,
                                                    mat=1, sq_b = 4+1+1+1 = 7
    Shared vocabulary dot product: the(1*2) + cat(1*1) + sat(1*1) = 4.
    cosine = 4 / sqrt(3 * 7) = 4 / sqrt(21).
    """
    a = "the cat sat"
    b = "the cat sat the mat"
    expected = 4 / (21 ** 0.5)
    assert compute_similarity(a, b).cosine == expected


def test_vectorise_returns_counter_and_squared_norm() -> None:
    """``_vectorise`` must tokenise once and return (Counter, squared-norm)."""
    counts, sq = _vectorise("the cat sat on the mat")
    assert counts == Counter(["the", "cat", "sat", "on", "the", "mat"])
    assert sq == sum(v * v for v in counts.values())


def test_vectorise_of_empty_text_yields_empty_counter_and_zero_norm() -> None:
    """An empty (or all-punctuation) string tokenises to nothing."""
    counts, sq = _vectorise("")
    assert counts == Counter()
    assert sq == 0


def test_cosine_vectors_congruent_with_cosine_for_a_battery_of_pairs() -> None:
    """``_cosine_vectors(a, sq_a, b, sq_b)`` must equal ``_cosine(a, b)`` exactly.

    This is the load-bearing equivalence for the Task 11 perf fix: the
    pre-vectorised path in ``filing_diff`` calls ``_cosine_vectors`` on cached
    vectors instead of ``_cosine`` on freshly tokenised Counters, and the two
    must never disagree — including at the empty-vector and identical-vector
    edges where floating-point subtleties bite hardest.
    """
    battery: list[tuple[Counter[str], Counter[str]]] = [
        (Counter(), Counter()),                                   # both empty
        (Counter(), Counter({"a": 1})),                            # one empty
        (Counter({"a": 1}), Counter()),                            # other empty
        (Counter({"a": 1, "b": 2}), Counter({"a": 1, "b": 2})),    # identical
        (Counter({"a": 1, "b": 2}), Counter({"c": 1, "d": 2})),    # disjoint
        (Counter({"a": 3, "b": 1, "c": 2}), Counter({"a": 1, "b": 1, "d": 5})),
        (Counter({"the": 10, "cat": 1}), Counter({"the": 1, "mat": 1})),
    ]
    for a_counts, b_counts in battery:
        sq_a = sum(v * v for v in a_counts.values())
        sq_b = sum(v * v for v in b_counts.values())
        assert _cosine_vectors(a_counts, sq_a, b_counts, sq_b) == _cosine(a_counts, b_counts)

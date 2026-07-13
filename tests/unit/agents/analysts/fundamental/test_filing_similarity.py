"""Tests for the lexical filing-similarity primitive."""
from __future__ import annotations

from agents.analysts.fundamental.filing_similarity import (
    SimilarityScores,
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

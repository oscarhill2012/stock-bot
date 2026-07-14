"""Lexical filing-similarity primitive — number-normalised bag-of-words.

Implements the measurement behind Cohen, Malloy & Nguyen (2020, "Lazy Prices"):
document (or paragraph) similarity via lexical measures, NOT neural embeddings.
Two measures are returned — cosine (term-frequency vector) and Jaccard (token
set) — because CMN used several and a second view is cheap.

The critical departure from the Phase 13 SHA-256 diff is NUMBER NORMALISATION:
every numeral run is collapsed to a single placeholder token before vectorising,
so a routine figure roll-forward (revenue 12.1 -> 13.4) no longer makes an
otherwise-identical paragraph look "fully changed".  Figure changes are surfaced
separately by ``filing_diff`` (Task 3), not through this similarity view.

Pure and memoised on the ``(current, prior)`` string pair — safe because both
arguments are immutable.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from math import sqrt

# Bump when the tokenisation / normalisation / scoring changes.  Feeds the
# fundamental report-cache digest AND gates the persisted cosine columns so a
# change forces recompute on refetch (never a silent stale read).
# v1.1 (2026-07): added the stub guard in ``compute_filing_similarities`` —
# a section shorter than ``mda_stub_char_threshold`` on either side of the
# pair is no longer scored (was poisoning the self-relative history baseline
# with cosines computed from incorporation-by-reference stubs). A targeted
# guard, not a scoring-algorithm rework, hence a minor bump rather than v2.
FILING_SIMILARITY_ALGO_VERSION = "v1.1"

# A maximal run of digits with embedded separators / percent signs — collapsed
# to one placeholder token so numeric drift does not dominate the vector.
_RE_NUMBER = re.compile(r"\d[\d,.%]*")

# Non-word, non-space characters stripped after number normalisation.
_RE_PUNCT = re.compile(r"[^\w\s]")

# Whitespace collapse.
_RE_SPACE = re.compile(r"\s+")

# The single token every numeral collapses to.  Deliberately not a real word.
_NUM_TOKEN = " qnum "


@dataclass(frozen=True)
class SimilarityScores:
    """A pair of lexical similarity measures in [0.0, 1.0].

    Attributes
    ----------
    cosine:
        Term-frequency cosine similarity — the primary measure (drives the
        self-relative scale).
    jaccard:
        Token-set Jaccard similarity — a secondary view shown to the LLM.
    """

    cosine:  float
    jaccard: float


def _tokenise(text: str) -> list[str]:
    """Normalise and tokenise filing prose into comparable tokens.

    Lowercases, collapses every numeral run to a single placeholder token,
    strips punctuation, and splits on whitespace.  Number normalisation is the
    whole point: it makes a figure roll-forward lexically identical to its prior
    year so it stops masquerading as substantive change.

    Parameters
    ----------
    text:
        Raw section or paragraph prose.

    Returns
    -------
    list[str]
        Lower-cased tokens in document order (may be empty).
    """
    lowered   = text.lower()
    numbered  = _RE_NUMBER.sub(_NUM_TOKEN, lowered)
    no_punct  = _RE_PUNCT.sub(" ", numbered)
    collapsed = _RE_SPACE.sub(" ", no_punct).strip()

    return collapsed.split(" ") if collapsed else []


def _vectorise(text: str) -> tuple[Counter[str], int]:
    """Tokenise ``text`` once into a reusable term-frequency vector.

    Extracted so a caller comparing ONE side against MANY paragraphs (as
    ``filing_diff``'s year-over-year matcher does) tokenises each side exactly
    once and reuses the result across every comparison, instead of re-tokenising
    on every one of the O(M×N) pair evaluations.

    Parameters
    ----------
    text:
        Raw section or paragraph prose.

    Returns
    -------
    tuple[Counter[str], int]
        The term-frequency ``Counter`` over normalised tokens, and its squared
        magnitude (``sum(v*v for v in counts.values())``) — precomputed here so
        ``_cosine_vectors`` never has to recompute it per comparison.
    """
    counts = Counter(_tokenise(text))
    sq     = sum(v * v for v in counts.values())

    return counts, sq


def _cosine_vectors(
    a_counts: Counter[str],
    sq_a:     int,
    b_counts: Counter[str],
    sq_b:     int,
) -> float:
    """Cosine similarity of two PRE-BUILT term-frequency vectors.

    This holds the exact scoring formula (empty-guard, shared-vocabulary dot
    product, single-sqrt-of-product denominator, upper clamp) — ``_cosine``
    delegates to this so there is only one copy of the formula to keep correct.
    Callers that already hold a vector (e.g. from ``_vectorise``) should call
    this directly to skip re-tokenising and re-summing the squared magnitude.

    Returns 0.0 when either vector is empty (one-sided emptiness), never NaN.

    Parameters
    ----------
    a_counts, b_counts:
        Term-frequency Counters over normalised tokens.
    sq_a, sq_b:
        Each vector's precomputed squared magnitude
        (``sum(v*v for v in counts.values())``).

    Returns
    -------
    float
        Cosine similarity in [0.0, 1.0].
    """
    if not a_counts or not b_counts:
        return 0.0

    # Dot product over the shared vocabulary.
    shared = set(a_counts) & set(b_counts)
    dot    = sum(a_counts[t] * b_counts[t] for t in shared)

    # Denominator computed as a single sqrt of the product, NOT sqrt(a)*sqrt(b).
    # The naive product suffers an IEEE-754 roundtrip — sqrt(x) * sqrt(x) can
    # exceed x by one ulp — so identical vectors score 0.9999999999999998 (the
    # epsilon lands in the denominator, dragging the quotient BELOW 1.0, where a
    # min() clamp cannot lift it).  sqrt(sq_a * sq_b) keeps the exact endpoint:
    # for identical vectors sqrt(100) is exactly 10.0, giving cosine 1.0.
    norm = sqrt(sq_a * sq_b)

    # Belt-and-braces upper clamp: cosine is mathematically bounded by 1.0, so
    # cap any residual above-1 float artefact and guarantee callers cosine in
    # [0.0, 1.0] at both endpoints.
    return min(1.0, dot / norm)


def _cosine(a_counts: Counter[str], b_counts: Counter[str]) -> float:
    """Cosine similarity of two term-frequency Counters.

    Thin wrapper over ``_cosine_vectors`` that computes each vector's squared
    magnitude on the fly — kept for callers (``compute_similarity``) that only
    ever compare ONE pair and have no reason to pre-build/cache a vector.

    Returns 0.0 when either vector is empty (one-sided emptiness), never NaN.

    Parameters
    ----------
    a_counts, b_counts:
        Term-frequency Counters over normalised tokens.

    Returns
    -------
    float
        Cosine similarity in [0.0, 1.0].
    """
    sq_a = sum(v * v for v in a_counts.values())
    sq_b = sum(v * v for v in b_counts.values())

    return _cosine_vectors(a_counts, sq_a, b_counts, sq_b)


def _jaccard(a_tokens: set[str], b_tokens: set[str]) -> float:
    """Jaccard similarity of two token sets.

    Returns 0.0 when either set is empty.

    Parameters
    ----------
    a_tokens, b_tokens:
        Sets of normalised tokens.

    Returns
    -------
    float
        Jaccard similarity in [0.0, 1.0].
    """
    if not a_tokens or not b_tokens:
        return 0.0

    union = a_tokens | b_tokens
    return len(a_tokens & b_tokens) / len(union)


@lru_cache(maxsize=4096)
def compute_similarity(current: str, prior: str) -> SimilarityScores:
    """Return the lexical similarity of two pieces of filing prose.

    Pure and memoised on ``(current, prior)`` — both immutable strings.  Number
    normalisation is applied inside ``_tokenise`` so figure roll-forwards do not
    register as change.

    Parameters
    ----------
    current:
        The current filing's section (or paragraph) text.
    prior:
        The prior comparable filing's counterpart text.

    Returns
    -------
    SimilarityScores
        ``cosine`` (primary) and ``jaccard`` (secondary), both in [0.0, 1.0].
    """
    a_tokens = _tokenise(current)
    b_tokens = _tokenise(prior)

    cosine  = _cosine(Counter(a_tokens), Counter(b_tokens))
    jaccard = _jaccard(set(a_tokens), set(b_tokens))

    return SimilarityScores(cosine=cosine, jaccard=jaccard)
